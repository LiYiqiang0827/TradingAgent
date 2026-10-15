"""Stateless MiniMax HTTP transport for isolated research packets.

Reads only Hermes model/provider metadata and the selected credential reference.
Does not import or run Hermes, create sessions, load memory, execute tools, retry,
or log prompts/credentials. Caller owns packet/output persistence and retries.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import re
import shlex
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

import yaml


_HERMES_HOME = Path.home() / ".hermes"
_PROVIDERS = {
    "minimax-cn": ("api.minimaxi.com", "MINIMAX_CN_API_KEY"),
    "minimax": ("api.minimax.io", "MINIMAX_API_KEY"),
}


class ModelConfigurationError(RuntimeError):
    """Configuration error with no secret/config contents in the message."""


class ModelTransportError(RuntimeError):
    """Sanitized network/protocol failure; no server body or headers attached."""


@dataclass(frozen=True)
class _Configuration:
    provider: str
    model: str
    endpoint: str
    credential_env: str


def _configuration() -> _Configuration:
    try:
        config = yaml.safe_load((_HERMES_HOME / "config.yaml").read_text(encoding="utf-8"))
        auth = json.loads((_HERMES_HOME / "auth.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, yaml.YAMLError):
        raise ModelConfigurationError("Cannot read Hermes model/auth metadata") from None
    model_config = config.get("model") if isinstance(config, dict) else None
    if not isinstance(model_config, dict):
        raise ModelConfigurationError("Hermes must explicitly configure model.default and model.provider")
    provider = model_config.get("provider")
    model = model_config.get("default")
    if provider not in _PROVIDERS:
        raise ModelConfigurationError("Configured provider is not an allowed MiniMax provider")
    if not isinstance(model, str) or not re.fullmatch(r"MiniMax-[A-Za-z0-9_.-]+", model):
        raise ModelConfigurationError("Configured model is not an explicit MiniMax model")
    host, credential_env = _PROVIDERS[provider]
    pool = auth.get("credential_pool", {}).get(provider) if isinstance(auth, dict) else None
    if not isinstance(pool, list):
        raise ModelConfigurationError("No explicit MiniMax credential pool metadata")
    entries = [entry for entry in pool if isinstance(entry, dict)
               and entry.get("auth_type") == "api_key"
               and entry.get("source") == f"env:{credential_env}"]
    if len(entries) != 1:
        raise ModelConfigurationError("Expected exactly one matching MiniMax environment credential reference")
    base = entries[0].get("base_url")
    if not isinstance(base, str):
        raise ModelConfigurationError("MiniMax credential metadata must explicitly configure base_url")
    try:
        url = urlsplit(base)
        valid = (url.scheme == "https" and url.hostname == host and url.port in (None, 443)
                 and url.username is None and url.password is None and not url.query and not url.fragment
                 and url.path.rstrip("/") in ("/anthropic", "/anthropic/v1", "/anthropic/v1/messages"))
    except ValueError:
        valid = False
    if not valid:
        raise ModelConfigurationError("MiniMax endpoint must be the configured provider's HTTPS Messages endpoint")
    endpoint = f"https://{host}/anthropic/v1/messages"
    # Reject conflicting model/provider endpoint overrides instead of silently selecting one.
    provider_configs = config.get("providers", {})
    provider_config = provider_configs.get(provider, {}) if isinstance(provider_configs, dict) else {}
    for extra in (model_config.get("base_url"),
                  provider_config.get("base_url") if isinstance(provider_config, dict) else None):
        if extra and str(extra).rstrip("/") != base.rstrip("/"):
            raise ModelConfigurationError("Conflicting explicit MiniMax endpoint settings")
    return _Configuration(provider, model, endpoint, credential_env)


def configuration_public() -> dict:
    """Return allowlisted public metadata only; does not read the secret value."""
    config = _configuration()
    return {"provider": config.provider, "model": config.model, "endpoint": config.endpoint,
            "protocol": "anthropic_messages", "transport": "direct_http",
            "authentication": "Hermes selected environment reference; Bearer header only",
            "tools_enabled": False, "history_message_count": 0,
            "implicit_retries": 0, "redirects_allowed": False}


def _payload(prompt: str, max_tokens: int, model: str) -> dict:
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("prompt must be a non-empty string")
    if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or not 1 <= max_tokens <= 32768:
        raise ValueError("max_tokens must be an integer from 1 to 32768")
    # Exactly one packet, no system message, tools, history, retrieval, or session ID.
    return {"model": model, "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens, "stream": False}


def request_payload(prompt: str, max_tokens: int = 4096) -> dict:
    """Build the entire outbound JSON body without reading credentials."""
    return _payload(prompt, max_tokens, _configuration().model)


def _credential(name: str) -> str:
    secret = os.environ.get(name)
    if not secret:
        try:
            with (_HERMES_HOME / ".env").open(encoding="utf-8") as handle:
                for line in handle:
                    match = re.match(rf"^\s*(?:export\s+)?{re.escape(name)}\s*=\s*(.*?)\s*$", line)
                    if match:
                        tokens = shlex.split(match.group(1), comments=True, posix=True)
                        if len(tokens) != 1:
                            raise ModelConfigurationError("Selected MiniMax credential assignment is invalid")
                        secret = tokens[0]
                        break
        except (OSError, ValueError):
            raise ModelConfigurationError("Cannot resolve selected MiniMax credential reference") from None
    if not secret or any(c in secret for c in ("\r", "\n", "\x00")) or "${" in secret:
        raise ModelConfigurationError("Selected MiniMax credential is absent or invalid")
    return secret


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ModelTransportError("HTTP redirect refused")


def _post(request: Request, timeout: float) -> bytes:
    # Fresh opener, no cookies/session state. A redirect cannot forward the credential.
    with build_opener(_NoRedirect()).open(request, timeout=timeout) as response:
        return response.read()


def call_model(prompt: str, timeout: float = 180, max_tokens: int = 4096) -> dict:
    """One HTTP attempt; raw bytes/text plus the actual secret-free request body.

    Exceptions expose only a status/error class, never credentials, headers,
    upstream error bodies, or the prompt. No retries or tool execution occur.
    """
    if isinstance(timeout, bool) or not isinstance(timeout, (float, int)) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout must be a positive finite number")
    config = _configuration()
    payload = _payload(prompt, max_tokens, config.model)
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")
    secret = _credential(config.credential_env)
    request = Request(config.endpoint, data=body,
                      headers={"Content-Type": "application/json", "Authorization": f"Bearer {secret}",
                               "anthropic-version": "2023-06-01", "Connection": "close"}, method="POST")
    started = time.monotonic()
    try:
        raw = _post(request, timeout)
    except HTTPError as exc:
        raise ModelTransportError(f"MiniMax HTTP status {int(exc.code)}") from None
    except ModelTransportError:
        raise
    except (URLError, OSError, TimeoutError, ValueError):
        raise ModelTransportError("MiniMax transport failed") from None
    elapsed = time.monotonic() - started
    if secret.encode("utf-8") in raw:
        raise ModelTransportError("MiniMax response rejected because it contains credential material")
    try:
        response = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise ModelTransportError("MiniMax returned invalid JSON") from None
    if not isinstance(response, dict) or response.get("type") == "error" or "error" in response:
        raise ModelTransportError("MiniMax returned an error envelope")
    content = response.get("content")
    if not isinstance(content, list) or any(not isinstance(block, dict) for block in content):
        raise ModelTransportError("MiniMax returned invalid message content")
    if any(block.get("type") not in {"text", "thinking", "redacted_thinking"} for block in content):
        raise ModelTransportError("MiniMax returned prohibited tool content")
    text = "\n".join(block["text"] for block in content
                     if block.get("type") == "text" and isinstance(block.get("text"), str))
    if not text:
        raise ModelTransportError("MiniMax returned no text content")
    return {"raw_response_bytes": raw, "raw_text": text,
            "response_model": response.get("model"), "usage": response.get("usage"),
            "elapsed": elapsed, "request_payload": payload,
            "request_body_bytes": body,
            "stop_reason": response.get("stop_reason")}
