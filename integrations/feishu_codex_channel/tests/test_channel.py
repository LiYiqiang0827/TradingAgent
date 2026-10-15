from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from feishu_codex_channel.agent_backend import CodexAgentBackend
from feishu_codex_channel.config import ChannelConfig
from feishu_codex_channel.feishu_transport import parse_message_event
from feishu_codex_channel.session_store import SessionRecord, SessionStore


def test_config_requires_keys(tmp_path: Path) -> None:
    config = ChannelConfig.from_env(
        {
            "TRADING_AGENT_WORKSPACE": str(tmp_path),
            "CODEX_CLI": str(tmp_path / "missing-codex"),
        }
    )
    errors = config.validation_errors()
    assert "FEISHU_CODEX_APP_ID is not configured" in errors
    assert any("Codex CLI does not exist" in error for error in errors)
    assert config.pairing_errors() == [
        "FEISHU_CODEX_APP_ID is not configured",
        "FEISHU_CODEX_APP_SECRET is not configured",
    ]


def test_session_store_round_trip_and_dedup(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "state.sqlite3")
    record = SessionRecord("chat", "thread")
    store.put(record)
    assert store.get("chat") == record
    assert store.claim_message("message") is True
    assert store.claim_message("message") is False
    store.delete("chat")
    assert store.get("chat") is None


def test_final_text_prefers_final_answer() -> None:
    messages = [
        {"phase": "commentary", "text": "正在处理"},
        {"phase": "final_answer", "text": "完整答案"},
    ]
    assert CodexAgentBackend._final_text(messages) == "完整答案"


def test_parse_feishu_text_message() -> None:
    message = SimpleNamespace(
        message_id="om_1",
        chat_id="oc_1",
        chat_type="p2p",
        message_type="text",
        content=json.dumps({"text": "你好"}),
    )
    sender = SimpleNamespace(sender_id=SimpleNamespace(open_id="ou_1"))
    data = SimpleNamespace(event=SimpleNamespace(message=message, sender=sender))
    parsed = parse_message_event(data)
    assert parsed is not None
    assert parsed.text == "你好"
    assert parsed.sender_open_id == "ou_1"
