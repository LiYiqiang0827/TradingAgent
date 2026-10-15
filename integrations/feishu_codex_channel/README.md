# TradingAgent Codex Feishu Channel

This service connects a dedicated Feishu enterprise bot directly to the local
Codex app-server and the `/Users/nickzhang/TradingAgent` workspace. It neither
uses nor changes the existing Hermes Feishu channel.

## Architecture

```text
Feishu WebSocket -> allowlist/dedup -> chat/thread mapping
                 -> local `codex app-server` (existing ChatGPT login)
                 -> TradingAgent files, tools and skills
                 -> Feishu reply
```

One Feishu chat maps to one durable Codex thread. Runtime state is kept outside
the repository in
`~/.local/share/tradingagent-feishu-codex/state.sqlite3`. The channel reuses the
current local Codex login, so it does not require an OpenAI API key or an Agents
API executor key.

`codex app-server` is currently marked experimental by OpenAI. It is appropriate
for this personal local integration, but the generated protocol can change with
future Codex CLI upgrades. Run the tests and `--doctor` after upgrading Codex.

## Prerequisites

1. Create a **separate** Feishu enterprise application and enable its Bot
   feature. Do not reuse the Hermes application because two WebSocket clients
   would compete for the same events.
2. Subscribe to `im.message.receive_v1` using long connection/WebSocket mode.
3. Grant these Feishu permissions and publish the application:
   - `im:message`
   - `im:message:send_as_bot`
   - `im:chat:readonly`
4. Keep the Mac awake and logged in while the channel is expected to answer.

Do not paste the App Secret into chat or commit it to Git.

## Local setup

```bash
cd /Users/nickzhang/TradingAgent/integrations/feishu_codex_channel
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

mkdir -p ~/.config/tradingagent-feishu-codex
cp .env.example ~/.config/tradingagent-feishu-codex/.env
chmod 600 ~/.config/tradingagent-feishu-codex/.env
```

Fill only the dedicated Feishu application's credentials first:

```bash
FEISHU_CODEX_APP_ID=cli_xxx
FEISHU_CODEX_APP_SECRET=xxx
```

Discover the app-scoped Open ID without invoking Codex:

```bash
PYTHONPATH=src .venv/bin/python -m feishu_codex_channel.main --pair
```

Send one direct text message to the new bot. The command prints the sender Open
ID locally and exits. Add it to the local `.env`:

```bash
FEISHU_CODEX_ALLOWED_OPEN_IDS=ou_xxx
```

Then run the checks and start the channel in the foreground:

```bash
PYTHONPATH=src .venv/bin/python -m feishu_codex_channel.main --doctor
PYTHONPATH=src .venv/bin/python -m feishu_codex_channel.main
```

After the foreground smoke test succeeds, install it as a macOS login service:

```bash
./scripts/install_launchd.sh
```

## Security defaults

- Direct messages only.
- Explicit Feishu Open ID allowlist; an empty allowlist denies everyone.
- `workspace-write` sandbox rooted at TradingAgent.
- Approval policy `never`, because Feishu approval cards are not implemented;
  operations requiring an escalation fail instead of silently escaping.
- Credentials and SQLite state live outside Git.

The model, effort, sandbox, approval policy, and turn timeout can be changed in
the local `.env`. Leaving `CODEX_FEISHU_MODEL` empty inherits the current Codex
CLI default.

## Commands in Feishu

- `/new` clears the local chat-to-thread mapping. The next message creates a new
  Codex thread.
- `/status` reports whether the current chat has a mapped Codex thread.
- `/stop` interrupts the active Codex turn.

The first version accepts text messages in direct chats only. Attachments,
approval cards, progress-message editing, and group routing can be added after
the end-to-end text path is verified.

## Tests

```bash
PYTHONPATH=src .venv/bin/python -m pytest -q tests
```

Official references:

- <https://developers.openai.com/zh-Hans/blog/codex-as-a-platform>
- <https://developers.openai.com/docs/mcp-server>
