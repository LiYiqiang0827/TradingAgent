from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class SessionRecord:
    chat_id: str
    thread_id: str


class SessionStore:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS chat_threads (
                    chat_id TEXT PRIMARY KEY,
                    thread_id TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS processed_messages (
                    message_id TEXT PRIMARY KEY,
                    processed_at TEXT NOT NULL
                );
                """
            )

    def get(self, chat_id: str) -> SessionRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT chat_id, thread_id FROM chat_threads WHERE chat_id = ?",
                (chat_id,),
            ).fetchone()
        return SessionRecord(**dict(row)) if row else None

    def put(self, record: SessionRecord) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO chat_threads(chat_id, thread_id, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(chat_id) DO UPDATE SET
                    thread_id = excluded.thread_id,
                    updated_at = excluded.updated_at
                """,
                (record.chat_id, record.thread_id, now),
            )

    def delete(self, chat_id: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM chat_threads WHERE chat_id = ?", (chat_id,))

    def claim_message(self, message_id: str) -> bool:
        """Return True once for a message ID and False for Feishu redeliveries."""
        now = datetime.now(timezone.utc).isoformat()
        try:
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO processed_messages(message_id, processed_at) VALUES (?, ?)",
                    (message_id, now),
                )
            return True
        except sqlite3.IntegrityError:
            return False
