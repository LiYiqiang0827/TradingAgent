from __future__ import annotations

import argparse
import asyncio
import logging
import subprocess
import sys
from pathlib import Path

from .agent_backend import CodexAgentBackend
from .config import ChannelConfig, load_env_file
from .feishu_transport import FeishuTransport, InboundMessage
from .pairing import discover_open_id
from .session_store import SessionStore


def doctor(config: ChannelConfig) -> list[str]:
    errors = config.validation_errors()
    try:
        import lark_oapi  # noqa: F401
    except ImportError:
        errors.append("lark-oapi is not installed")
    if config.codex_cli.is_file():
        try:
            status = subprocess.run(
                [str(config.codex_cli), "login", "status"],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
            if status.returncode != 0:
                errors.append("Codex CLI is not logged in")
        except (OSError, subprocess.TimeoutExpired) as exc:
            errors.append(f"could not check Codex login: {exc}")
    return errors


async def run(config: ChannelConfig) -> None:
    store = SessionStore(config.state_dir / "state.sqlite3")
    backend = CodexAgentBackend(config, store)

    async def handle(message: InboundMessage) -> str:
        if message.text == "/new":
            store.delete(message.chat_id)
            return "已清除当前映射。下一条消息会创建新的 Codex 会话。"
        if message.text == "/status":
            record = store.get(message.chat_id)
            return "当前聊天已连接独立 Codex 线程。" if record else "当前聊天尚未创建 Codex 线程。"
        if message.text == "/stop":
            cancelled = await backend.cancel(message.chat_id)
            return "已发送停止请求。" if cancelled else "当前聊天没有可停止的 Codex 会话。"
        return await backend.ask(message.chat_id, message.text)

    transport = FeishuTransport(config, store, handle)
    try:
        await transport.run()
    finally:
        await backend.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="TradingAgent Codex Feishu Channel")
    parser.add_argument("--doctor", action="store_true", help="validate local configuration without connecting")
    parser.add_argument("--pair", action="store_true", help="discover one Feishu sender Open ID without invoking Codex")
    parser.add_argument("--pair-timeout", type=float, default=180.0)
    parser.add_argument("--env-file", help="path to the local env file")
    args = parser.parse_args(argv)

    load_env_file() if not args.env_file else load_env_file(Path(args.env_file))
    config = ChannelConfig.from_env()
    logging.basicConfig(
        level=getattr(logging, config.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if args.pair:
        errors = config.pairing_errors()
        try:
            import lark_oapi  # noqa: F401
        except ImportError:
            errors.append("lark-oapi is not installed")
        if errors:
            print("Pairing configuration is incomplete:", file=sys.stderr)
            for error in errors:
                print(f"- {error}", file=sys.stderr)
            return 2
        print("Waiting for one direct text message to the dedicated Feishu bot...")
        result = discover_open_id(config, timeout_seconds=args.pair_timeout)
        print(f"FEISHU_CODEX_ALLOWED_OPEN_IDS={result.open_id}")
        return 0
    errors = doctor(config)
    if errors:
        print("Configuration is incomplete:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 2
    if args.doctor:
        print("Configuration and dependencies look ready.")
        return 0
    try:
        asyncio.run(run(config))
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
