"""Process-local frozen research policy; no network clients or credential reads.

Set TRADING_AGENT_FROZEN=1 before importing data_provider. Frozen databases
must be completed, quiescent snapshots: immutable SQLite readers deliberately
do not participate in locking and must never run against an active writer.
"""
from __future__ import annotations

import os
from pathlib import Path
import sqlite3


class FrozenDataError(RuntimeError):
    """An operation would violate the frozen, local-only research policy."""


def frozen_mode() -> bool:
    """Fail closed on misspelled configuration instead of silently networking."""
    value = os.environ.get("TRADING_AGENT_FROZEN", "0").strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off", ""}:
        return False
    raise FrozenDataError("Invalid TRADING_AGENT_FROZEN; use 1 or 0")


def require_online_allowed() -> None:
    if frozen_mode():
        raise FrozenDataError("Frozen research mode prohibits online data access")


def require_writes_allowed() -> None:
    if frozen_mode():
        raise FrozenDataError("Frozen research mode prohibits database initialization or writes")


def connect_sqlite(db_path: str | Path) -> sqlite3.Connection:
    """Open a local DB; frozen mode cannot create files, journals, or tables.

    mode=ro is the write protection; immutable=1 avoids lock/sidecar writes;
    query_only is an additional guard. Paths with spaces/#/? are URI-escaped.
    Refuse nonempty WAL/journal sidecars instead of silently ignoring them.
    """
    path = Path(db_path).expanduser().resolve()
    if not frozen_mode():
        return sqlite3.connect(str(path))
    if not path.is_file():
        raise FileNotFoundError(f"Frozen database not found: {path}")
    for suffix in ("-wal", "-journal"):
        sidecar = Path(str(path) + suffix)
        if sidecar.exists() and sidecar.stat().st_size:
            raise FrozenDataError(
                f"Frozen snapshot has a nonempty {suffix} sidecar: {path}; "
                "obtain a completed snapshot separately without modifying this source"
            )
    conn = sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)
    try:
        conn.execute("PRAGMA query_only=ON")
    except BaseException:
        conn.close()
        raise
    return conn
