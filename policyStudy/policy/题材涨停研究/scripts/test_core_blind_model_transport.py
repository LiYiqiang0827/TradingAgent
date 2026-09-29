"""Offline tests: capture exact outbound body, isolation, and secret-safe failures."""
import io
import json
from pathlib import Path
from urllib.error import HTTPError, URLError

import pytest
import yaml

import core_blind_model_transport as transport


@pytest.fixture
def local_config(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(transport, "_HERMES_HOME", tmp_path)
    monkeypatch.delenv("MINIMAX_CN_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "foreign-secret-must-not-be-used")
    config = {"model": {"provider": "minimax-cn", "default": "MiniMax-M3"},
              "memory": {"enabled": True, "text": "PRIVATE_OLD_MEMORY"},
              "system_prompt": "PRIVATE_EXTRA_SYSTEM", "mcp_servers": {"search": {}},
              "history": [{"role": "user", "content": "PRIVATE_PREVIOUS_HISTORY"}]}
    auth = {"credential_pool": {"minimax-cn": [
        {"auth_type": "api_key", "source": "env:MINIMAX_CN_API_KEY",
         "base_url": "https://api.minimaxi.com/anthropic"}]}}
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(config))
    (tmp_path / "auth.json").write_text(json.dumps(auth))
    (tmp_path / ".env").write_text("UNRELATED_KEY='do-not-load-this'\nexport MINIMAX_CN_API_KEY='fake-selected-secret'\n")
    return tmp_path


def response_bytes(text='{"ok":true}'):
    return json.dumps({"type": "message", "model": "MiniMax-M3", "role": "assistant",
                       "content": [{"type": "text", "text": text}], "stop_reason": "end_turn",
                       "usage": {"input_tokens": 8, "output_tokens": 5}}).encode()


def test_public_metadata_and_payload_never_resolve_or_include_credentials(local_config, monkeypatch):
    monkeypatch.setattr(transport, "_credential", lambda *_: pytest.fail("secret must not be read"))
    public = transport.configuration_public()
    assert public["model"] == "MiniMax-M3"
    assert public["endpoint"] == "https://api.minimaxi.com/anthropic/v1/messages"
    body = transport.request_payload("唯一隔离数据包", 4096)
    assert body == {"model": "MiniMax-M3", "messages": [{"role": "user", "content": "唯一隔离数据包"}],
                    "max_tokens": 4096, "stream": False}
    serialized = json.dumps({"metadata": public, "body": body})
    assert "secret" not in serialized and "PRIVATE_" not in serialized


def test_exact_one_packet_body_no_tools_history_or_foreign_auth(local_config, monkeypatch, capsys):
    outbound = []
    raw = response_bytes()
    def post(request, timeout):
        assert request.full_url == "https://api.minimaxi.com/anthropic/v1/messages"
        assert request.get_method() == "POST" and timeout == 37
        assert request.get_header("Authorization") == "Bearer fake-selected-secret"
        assert request.get_header("X-api-key") is None
        body = json.loads(request.data)
        assert set(body) == {"model", "messages", "max_tokens", "stream"}
        assert request.data == json.dumps(body, ensure_ascii=False, sort_keys=True,
                                          separators=(",", ":"), allow_nan=False).encode("utf-8")
        outbound.append(body)
        return raw
    monkeypatch.setattr(transport, "_post", post)
    expected = transport.request_payload("包A", 4096)
    first = transport.call_model("包A", timeout=37, max_tokens=4096)
    second = transport.call_model("包B", timeout=37, max_tokens=4096)
    assert first["request_payload"] == outbound[0] == expected
    assert first["request_body_bytes"] == json.dumps(expected, ensure_ascii=False, sort_keys=True,
                                                     separators=(",", ":"), allow_nan=False).encode("utf-8")
    assert second["request_payload"] == outbound[1] == transport.request_payload("包B", 4096)
    assert outbound[1]["messages"] == [{"role": "user", "content": "包B"}]
    assert first["raw_response_bytes"] == raw
    assert first["raw_text"] == '{"ok":true}'
    assert first["usage"] == {"input_tokens": 8, "output_tokens": 5}
    assert first["response_model"] == "MiniMax-M3" and first["elapsed"] >= 0
    assert first["stop_reason"] == "end_turn"
    assert "fake-selected-secret" not in repr(first)
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize("failure", ["http", "network", "bad_json", "credential_echo", "tool"])
def test_no_implicit_retries_and_sanitized_errors(local_config, monkeypatch, capsys, failure):
    calls = []
    def post(request, timeout):
        calls.append(1)
        if failure == "http":
            raise HTTPError(request.full_url, 429, "fake-selected-secret", {}, io.BytesIO(b"fake-selected-secret"))
        if failure == "network":
            raise URLError("fake-selected-secret")
        if failure == "bad_json":
            return b"bad private server error"
        if failure == "credential_echo":
            return response_bytes("fake-selected-secret")
        return json.dumps({"content": [{"type": "tool_use", "name": "search", "input": {}}]}).encode()
    monkeypatch.setattr(transport, "_post", post)
    with pytest.raises(transport.ModelTransportError) as error:
        transport.call_model("CURRENT_PACKET")
    assert len(calls) == 1
    assert "fake-selected-secret" not in str(error.value)
    assert "CURRENT_PACKET" not in str(error.value)
    assert capsys.readouterr() == ("", "")


def test_redirect_is_refused_without_forwarding_headers():
    with pytest.raises(transport.ModelTransportError, match="redirect refused"):
        transport._NoRedirect().redirect_request(None, None, 302, "", {}, "https://other.invalid")


@pytest.mark.parametrize("base", ["https://user:credential@api.minimaxi.com/anthropic",
                                   "https://api.minimaxi.com/anthropic?key=private",
                                   "https://wrong.invalid/anthropic", "http://api.minimaxi.com/anthropic"])
def test_untrusted_or_credential_bearing_endpoint_is_rejected(local_config, base):
    path = local_config / "auth.json"
    auth = json.loads(path.read_text())
    auth["credential_pool"]["minimax-cn"][0]["base_url"] = base
    path.write_text(json.dumps(auth))
    with pytest.raises(transport.ModelConfigurationError) as error:
        transport.configuration_public()
    assert base not in str(error.value)


def test_model_is_read_fresh_and_other_provider_cannot_fall_back(local_config):
    path = local_config / "config.yaml"
    config = yaml.safe_load(path.read_text())
    config["model"]["default"] = "MiniMax-M4"
    path.write_text(yaml.safe_dump(config))
    assert transport.request_payload("packet", 64)["model"] == "MiniMax-M4"
    config["model"]["provider"] = "custom:llm_server_vllm"
    path.write_text(yaml.safe_dump(config))
    with pytest.raises(transport.ModelConfigurationError):
        transport.request_payload("packet", 64)
