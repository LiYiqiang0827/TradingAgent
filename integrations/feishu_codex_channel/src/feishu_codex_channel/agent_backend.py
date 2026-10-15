from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from .config import ChannelConfig
from .session_store import SessionRecord, SessionStore


logger = logging.getLogger(__name__)


class AppServerError(RuntimeError):
    pass


class CodexAppServerClient:
    """Small async client for the newline-delimited Codex app-server protocol."""

    def __init__(self, config: ChannelConfig):
        self.config = config
        self._process: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._start_lock = asyncio.Lock()
        self._write_lock = asyncio.Lock()
        self._next_id = 1
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._subscribers: dict[str, set[asyncio.Queue[dict[str, Any]]]] = defaultdict(set)

    async def start(self) -> None:
        async with self._start_lock:
            if self._process is not None and self._process.returncode is None:
                return
            self._process = await asyncio.create_subprocess_exec(
                str(self.config.codex_cli),
                "app-server",
                "--listen",
                "stdio://",
                cwd=str(self.config.workspace),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            self._reader_task = asyncio.create_task(self._read_stdout(), name="codex-app-server-stdout")
            self._stderr_task = asyncio.create_task(self._read_stderr(), name="codex-app-server-stderr")
            try:
                await self._request_started(
                    "initialize",
                    {
                        "clientInfo": {
                            "name": "tradingagent-feishu-codex",
                            "title": "TradingAgent Feishu Channel",
                            "version": "0.2.0",
                        },
                        "capabilities": {},
                    },
                    timeout=30,
                )
                await self._notify_started("initialized")
            except Exception:
                await self.close()
                raise
            logger.info("Codex app-server started with the existing local Codex login")

    async def _write(self, message: dict[str, Any]) -> None:
        process = self._process
        if process is None or process.returncode is not None or process.stdin is None:
            raise AppServerError("Codex app-server is not running")
        payload = json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n"
        async with self._write_lock:
            process.stdin.write(payload.encode("utf-8"))
            await process.stdin.drain()

    async def _request_started(
        self,
        method: str,
        params: dict[str, Any],
        *,
        timeout: float | None = 60,
    ) -> dict[str, Any]:
        request_id = self._next_id
        self._next_id += 1
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        try:
            await self._write({"id": request_id, "method": method, "params": params})
            if timeout is None:
                return await future
            return await asyncio.wait_for(future, timeout=timeout)
        finally:
            self._pending.pop(request_id, None)

    async def request(
        self,
        method: str,
        params: dict[str, Any],
        *,
        timeout: float | None = 60,
    ) -> dict[str, Any]:
        await self.start()
        return await self._request_started(method, params, timeout=timeout)

    async def _notify_started(self, method: str, params: dict[str, Any] | None = None) -> None:
        message: dict[str, Any] = {"method": method}
        if params is not None:
            message["params"] = params
        await self._write(message)

    async def _read_stdout(self) -> None:
        assert self._process is not None and self._process.stdout is not None
        try:
            while line := await self._process.stdout.readline():
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    logger.warning("Ignored malformed app-server output")
                    continue
                if "method" in message and "id" in message:
                    asyncio.create_task(self._handle_server_request(message))
                elif "id" in message:
                    self._handle_response(message)
                elif "method" in message:
                    self._publish_notification(message)
        finally:
            error = AppServerError("Codex app-server connection closed")
            for future in list(self._pending.values()):
                if not future.done():
                    future.set_exception(error)
            self._publish_notification({"method": "_transport/closed", "params": {}})

    async def _read_stderr(self) -> None:
        assert self._process is not None and self._process.stderr is not None
        while line := await self._process.stderr.readline():
            # Upstream diagnostic records may contain local context, so only
            # record their size at DEBUG level.
            logger.debug("codex app-server diagnostic received (%d bytes)", len(line))

    def _handle_response(self, message: dict[str, Any]) -> None:
        future = self._pending.get(message.get("id"))
        if future is None or future.done():
            return
        if "error" in message:
            error = message.get("error") or {}
            future.set_exception(AppServerError(str(error.get("message", "app-server request failed"))))
        else:
            future.set_result(message.get("result") or {})

    def _publish_notification(self, message: dict[str, Any]) -> None:
        params = message.get("params") or {}
        thread_id = params.get("threadId")
        queues: set[asyncio.Queue[dict[str, Any]]] = set()
        if thread_id:
            queues.update(self._subscribers.get(str(thread_id), set()))
        if message.get("method") == "_transport/closed":
            for subscribers in self._subscribers.values():
                queues.update(subscribers)
        for queue in queues:
            queue.put_nowait(message)

    async def _handle_server_request(self, message: dict[str, Any]) -> None:
        request_id = message.get("id")
        method = str(message.get("method", ""))
        if method == "currentTime/read":
            response: dict[str, Any] = {
                "id": request_id,
                "result": {"currentTimeAt": int(time.time())},
            }
        elif method in {
            "item/commandExecution/requestApproval",
            "item/fileChange/requestApproval",
        }:
            response = {"id": request_id, "result": {"decision": "decline"}}
        elif method in {"execCommandApproval", "applyPatchApproval"}:
            response = {
                "id": request_id,
                "result": {"decision": {"denied": {"rejection": "No interactive approval UI"}}},
            }
        elif method == "mcpServer/elicitation/request":
            response = {
                "id": request_id,
                "result": {"action": "decline", "content": None, "_meta": None},
            }
        elif method == "item/tool/requestUserInput":
            response = {"id": request_id, "result": {"answers": {}}}
        else:
            response = {
                "id": request_id,
                "error": {"code": -32601, "message": f"Unsupported client request: {method}"},
            }
        try:
            await self._write(response)
        except AppServerError:
            logger.warning("Could not answer app-server request %s", method)

    @asynccontextmanager
    async def subscribe(self, thread_id: str) -> AsyncIterator[asyncio.Queue[dict[str, Any]]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._subscribers[thread_id].add(queue)
        try:
            yield queue
        finally:
            subscribers = self._subscribers.get(thread_id)
            if subscribers is not None:
                subscribers.discard(queue)
                if not subscribers:
                    self._subscribers.pop(thread_id, None)

    async def close(self) -> None:
        process = self._process
        if process is not None and process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=5)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
        tasks = [task for task in (self._reader_task, self._stderr_task) if task is not None]
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._process = None
        self._reader_task = None
        self._stderr_task = None


class CodexAgentBackend:
    def __init__(self, config: ChannelConfig, store: SessionStore):
        self.config = config
        self.store = store
        self.client = CodexAppServerClient(config)
        self._chat_locks: dict[str, asyncio.Lock] = {}
        self._loaded_threads: set[str] = set()
        self._active_turns: dict[str, tuple[str, str]] = {}

    async def _create_thread(self, chat_id: str) -> SessionRecord:
        params: dict[str, Any] = {
            "cwd": str(self.config.workspace),
            "runtimeWorkspaceRoots": [str(self.config.workspace)],
            "approvalPolicy": self.config.approval_policy,
            "sandbox": self.config.sandbox,
            "ephemeral": False,
            "developerInstructions": (
                "You are the dedicated TradingAgent Codex assistant reached through Feishu. "
                "Work in the configured TradingAgent repository, follow repository instructions "
                "and installed skills, verify work with actual commands, and answer in Chinese "
                "unless the user requests otherwise. Feishu has no interactive approval or form "
                "UI, so ask any necessary question as ordinary response text."
            ),
        }
        if self.config.model:
            params["model"] = self.config.model
        result = await self.client.request("thread/start", params, timeout=60)
        thread_id = str(result["thread"]["id"])
        record = SessionRecord(chat_id=chat_id, thread_id=thread_id)
        self.store.put(record)
        self._loaded_threads.add(thread_id)
        return record

    async def _resume_thread(self, record: SessionRecord) -> None:
        if record.thread_id in self._loaded_threads:
            return
        params: dict[str, Any] = {
            "threadId": record.thread_id,
            "cwd": str(self.config.workspace),
            "runtimeWorkspaceRoots": [str(self.config.workspace)],
            "approvalPolicy": self.config.approval_policy,
            "sandbox": self.config.sandbox,
            "excludeTurns": True,
        }
        if self.config.model:
            params["model"] = self.config.model
        await self.client.request("thread/resume", params, timeout=60)
        self._loaded_threads.add(record.thread_id)

    async def ask(self, chat_id: str, text: str) -> str:
        lock = self._chat_locks.setdefault(chat_id, asyncio.Lock())
        async with lock:
            await self.client.start()
            record = self.store.get(chat_id)
            if record is None:
                record = await self._create_thread(chat_id)
            else:
                await self._resume_thread(record)

            turn_params: dict[str, Any] = {
                "threadId": record.thread_id,
                "input": [{"type": "text", "text": text, "text_elements": []}],
            }
            if self.config.reasoning_effort:
                turn_params["effort"] = self.config.reasoning_effort

            completed_messages: list[dict[str, Any]] = []
            async with self.client.subscribe(record.thread_id) as notifications:
                result = await self.client.request("turn/start", turn_params, timeout=60)
                turn_id = str(result["turn"]["id"])
                self._active_turns[chat_id] = (record.thread_id, turn_id)
                try:
                    while True:
                        event = await asyncio.wait_for(
                            notifications.get(), timeout=self.config.turn_timeout_seconds
                        )
                        method = event.get("method")
                        params = event.get("params") or {}
                        event_turn_id = params.get("turnId", (params.get("turn") or {}).get("id", ""))
                        if method == "_transport/closed":
                            self._loaded_threads.clear()
                            raise AppServerError("Codex app-server connection closed during the turn")
                        if str(event_turn_id) != turn_id:
                            continue
                        if method == "item/completed":
                            item = params.get("item") or {}
                            if item.get("type") == "agentMessage" and item.get("text"):
                                completed_messages.append(item)
                        if method == "turn/completed":
                            turn = params.get("turn") or {}
                            status = turn.get("status")
                            if status == "failed":
                                error = turn.get("error") or {}
                                raise AppServerError(str(error.get("message", "Codex turn failed")))
                            if status == "interrupted":
                                return "任务已停止。"
                            return self._final_text(completed_messages)
                finally:
                    self._active_turns.pop(chat_id, None)

    @staticmethod
    def _final_text(messages: list[dict[str, Any]]) -> str:
        finals = [str(item["text"]) for item in messages if item.get("phase") == "final_answer"]
        if finals:
            return "\n".join(finals).strip()
        unknown = [str(item["text"]) for item in messages if item.get("phase") != "commentary"]
        if unknown:
            return "\n".join(unknown).strip()
        if messages:
            return str(messages[-1]["text"]).strip()
        return "任务已完成，但本轮没有返回文本。"

    async def cancel(self, chat_id: str) -> bool:
        active = self._active_turns.get(chat_id)
        if active is None:
            return False
        thread_id, turn_id = active
        await self.client.request(
            "turn/interrupt",
            {"threadId": thread_id, "turnId": turn_id},
            timeout=30,
        )
        return True

    async def close(self) -> None:
        await self.client.close()
