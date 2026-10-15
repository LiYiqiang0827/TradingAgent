from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


DEFAULT_ENV_FILE = Path.home() / ".config/tradingagent-feishu-codex/.env"


def load_env_file(path: Path = DEFAULT_ENV_FILE) -> None:
    """Load simple KEY=VALUE entries without overriding exported variables."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


def _csv_set(value: str) -> frozenset[str]:
    return frozenset(item.strip() for item in value.split(",") if item.strip())


@dataclass(frozen=True)
class ChannelConfig:
    feishu_app_id: str
    feishu_app_secret: str
    feishu_domain: str
    allowed_open_ids: frozenset[str]
    model: str
    reasoning_effort: str
    sandbox: str
    approval_policy: str
    turn_timeout_seconds: float
    workspace: Path
    codex_cli: Path
    state_dir: Path
    log_level: str

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "ChannelConfig":
        source = os.environ if env is None else env
        return cls(
            feishu_app_id=source.get("FEISHU_CODEX_APP_ID", "").strip(),
            feishu_app_secret=source.get("FEISHU_CODEX_APP_SECRET", "").strip(),
            feishu_domain=source.get("FEISHU_CODEX_DOMAIN", "feishu").strip().lower(),
            allowed_open_ids=_csv_set(source.get("FEISHU_CODEX_ALLOWED_OPEN_IDS", "")),
            model=source.get("CODEX_FEISHU_MODEL", "").strip(),
            reasoning_effort=source.get("CODEX_FEISHU_REASONING_EFFORT", "high").strip(),
            sandbox=source.get("CODEX_FEISHU_SANDBOX", "workspace-write").strip(),
            approval_policy=source.get("CODEX_FEISHU_APPROVAL_POLICY", "never").strip(),
            turn_timeout_seconds=float(source.get("CODEX_FEISHU_TURN_TIMEOUT_SECONDS", "7200")),
            workspace=Path(source.get("TRADING_AGENT_WORKSPACE", "/Users/nickzhang/TradingAgent")).expanduser(),
            codex_cli=Path(source.get("CODEX_CLI", "/opt/homebrew/bin/codex")).expanduser(),
            state_dir=Path(
                source.get(
                    "FEISHU_CODEX_STATE_DIR",
                    "~/.local/share/tradingagent-feishu-codex",
                )
            ).expanduser(),
            log_level=source.get("FEISHU_CODEX_LOG_LEVEL", "INFO").strip().upper(),
        )

    def validation_errors(self) -> list[str]:
        errors: list[str] = []
        required = {
            "FEISHU_CODEX_APP_ID": self.feishu_app_id,
            "FEISHU_CODEX_APP_SECRET": self.feishu_app_secret,
            "FEISHU_CODEX_ALLOWED_OPEN_IDS": self.allowed_open_ids,
        }
        errors.extend(f"{key} is not configured" for key, value in required.items() if not value)
        if self.feishu_domain not in {"feishu", "lark"}:
            errors.append("FEISHU_CODEX_DOMAIN must be feishu or lark")
        if not self.workspace.is_dir():
            errors.append(f"workspace does not exist: {self.workspace}")
        if not self.codex_cli.is_file():
            errors.append(f"Codex CLI does not exist: {self.codex_cli}")
        if self.sandbox not in {"read-only", "workspace-write", "danger-full-access"}:
            errors.append("CODEX_FEISHU_SANDBOX must be read-only, workspace-write, or danger-full-access")
        if self.approval_policy not in {"untrusted", "on-request", "never"}:
            errors.append("CODEX_FEISHU_APPROVAL_POLICY must be untrusted, on-request, or never")
        if self.turn_timeout_seconds <= 0:
            errors.append("CODEX_FEISHU_TURN_TIMEOUT_SECONDS must be positive")
        return errors

    def pairing_errors(self) -> list[str]:
        errors: list[str] = []
        if not self.feishu_app_id:
            errors.append("FEISHU_CODEX_APP_ID is not configured")
        if not self.feishu_app_secret:
            errors.append("FEISHU_CODEX_APP_SECRET is not configured")
        if self.feishu_domain not in {"feishu", "lark"}:
            errors.append("FEISHU_CODEX_DOMAIN must be feishu or lark")
        return errors
