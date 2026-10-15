from __future__ import annotations

import asyncio
import json
import logging
import threading
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from .config import ChannelConfig
from .session_store import SessionStore


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class InboundMessage:
    message_id: str
    chat_id: str
    sender_open_id: str
    chat_type: str
    text: str


def parse_message_event(data: Any) -> InboundMessage | None:
    event = getattr(data, "event", None)
    message = getattr(event, "message", None)
    sender = getattr(event, "sender", None)
    sender_id = getattr(sender, "sender_id", None)
    if message is None or sender_id is None:
        return None
    if str(getattr(message, "message_type", "")) != "text":
        return None
    try:
        content = json.loads(getattr(message, "content", "{}") or "{}")
    except json.JSONDecodeError:
        return None
    text = str(content.get("text", "")).strip()
    if not text:
        return None
    return InboundMessage(
        message_id=str(getattr(message, "message_id", "")),
        chat_id=str(getattr(message, "chat_id", "")),
        sender_open_id=str(getattr(sender_id, "open_id", "")),
        chat_type=str(getattr(message, "chat_type", "")),
        text=text,
    )


class FeishuTransport:
    def __init__(
        self,
        config: ChannelConfig,
        store: SessionStore,
        handler: Callable[[InboundMessage], Awaitable[str]],
    ):
        self.config = config
        self.store = store
        self.handler = handler
        self._loop: asyncio.AbstractEventLoop | None = None
        self._client: Any = None
        self._ws_client: Any = None

    def _build_clients(self) -> None:
        import lark_oapi as lark
        from lark_oapi import ws
        from lark_oapi.event.dispatcher_handler import EventDispatcherHandler

        domain = "https://open.feishu.cn" if self.config.feishu_domain == "feishu" else "https://open.larksuite.com"
        self._client = (
            lark.Client.builder()
            .app_id(self.config.feishu_app_id)
            .app_secret(self.config.feishu_app_secret)
            .domain(domain)
            .log_level(lark.LogLevel.ERROR)
            .build()
        )
        event_handler = (
            EventDispatcherHandler.builder("", "")
            .register_p2_im_message_receive_v1(self._on_message_event)
            .build()
        )
        self._ws_client = ws.Client(
            self.config.feishu_app_id,
            self.config.feishu_app_secret,
            event_handler=event_handler,
            domain=domain,
            auto_reconnect=True,
            log_level=lark.LogLevel.ERROR,
        )

    def _on_message_event(self, data: Any) -> None:
        message = parse_message_event(data)
        if message is None or self._loop is None:
            return
        asyncio.run_coroutine_threadsafe(self._handle_message(message), self._loop)

    async def _handle_message(self, message: InboundMessage) -> None:
        if message.chat_type != "p2p":
            return
        if message.sender_open_id not in self.config.allowed_open_ids:
            logger.warning("Rejected Feishu sender not in allowlist")
            return
        if not self.store.claim_message(message.message_id):
            return
        try:
            response = await self.handler(message)
        except Exception:
            logger.exception("Failed to process Feishu message")
            response = "Codex 处理失败，请查看本机 Channel 日志。"
        await asyncio.to_thread(self.send_text, message.chat_id, response)

    def send_text(self, chat_id: str, text: str) -> None:
        from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody

        if self._client is None:
            raise RuntimeError("Feishu client is not connected")
        chunks = [text[i : i + 3500] for i in range(0, len(text), 3500)] or [""]
        for chunk in chunks:
            request = (
                CreateMessageRequest.builder()
                .receive_id_type("chat_id")
                .request_body(
                    CreateMessageRequestBody.builder()
                    .receive_id(chat_id)
                    .msg_type("text")
                    .content(json.dumps({"text": chunk}, ensure_ascii=False))
                    .build()
                )
                .build()
            )
            response = self._client.im.v1.message.create(request)
            if not response.success():
                raise RuntimeError(f"Feishu send failed: code={response.code}, msg={response.msg}")

    async def run(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._build_clients()
        thread = threading.Thread(target=self._ws_client.start, name="feishu-codex-ws", daemon=True)
        thread.start()
        logger.info("TradingAgent Codex Feishu Channel started")
        await asyncio.Event().wait()
