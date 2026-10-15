from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from typing import Any

from .config import ChannelConfig


@dataclass
class PairingResult:
    open_id: str = ""
    chat_id: str = ""


def discover_open_id(config: ChannelConfig, timeout_seconds: float = 180.0) -> PairingResult:
    """Wait for one direct text message and return its app-scoped sender ID."""
    import lark_oapi as lark
    from lark_oapi import ws
    from lark_oapi.event.dispatcher_handler import EventDispatcherHandler

    found = threading.Event()
    result = PairingResult()

    def on_message(data: Any) -> None:
        event = getattr(data, "event", None)
        message = getattr(event, "message", None)
        sender = getattr(event, "sender", None)
        sender_id = getattr(sender, "sender_id", None)
        if message is None or sender_id is None:
            return
        if str(getattr(message, "chat_type", "")) != "p2p":
            return
        if str(getattr(message, "message_type", "")) != "text":
            return
        try:
            text = json.loads(getattr(message, "content", "{}") or "{}").get("text", "")
        except json.JSONDecodeError:
            return
        if not str(text).strip():
            return
        result.open_id = str(getattr(sender_id, "open_id", ""))
        result.chat_id = str(getattr(message, "chat_id", ""))
        if result.open_id:
            found.set()

    domain = "https://open.feishu.cn" if config.feishu_domain == "feishu" else "https://open.larksuite.com"
    event_handler = (
        EventDispatcherHandler.builder("", "")
        .register_p2_im_message_receive_v1(on_message)
        .build()
    )
    client = ws.Client(
        config.feishu_app_id,
        config.feishu_app_secret,
        event_handler=event_handler,
        domain=domain,
        auto_reconnect=True,
        log_level=lark.LogLevel.ERROR,
    )
    thread = threading.Thread(target=client.start, name="feishu-codex-pairing", daemon=True)
    thread.start()
    if not found.wait(timeout_seconds):
        raise TimeoutError("No direct text message arrived before the pairing timeout")
    return result
