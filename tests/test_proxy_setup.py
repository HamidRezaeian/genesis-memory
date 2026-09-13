"""Tests for GENESIS Proxy Setup Wizard, Credential Gatekeeping & Client Auto-Wiring."""

import json
import os
from pathlib import Path
import pytest

from genesis_memory.cli.client_registry import ClientRegistry, DiscoveredClient, ClientCapability
from genesis_memory.cli.proxy_setup import _validate_upstream_url, run_proxy_setup
from genesis_memory.proxy import supervisor


def test_validate_upstream_url_rejects_empty():
    ok, err = _validate_upstream_url("")
    assert not ok
    assert "empty" in err


def test_validate_upstream_url_rejects_missing_scheme():
    ok, err = _validate_upstream_url("openrouter.ai/api/v1")
    assert not ok
    assert "http://" in err or "https://" in err


def test_validate_upstream_url_rejects_local_proxy_address():
    # User must not pass the local proxy 127.0.0.1:8000 as the upstream provider
    ok, err = _validate_upstream_url("http://127.0.0.1:8000/v1")
    assert not ok
    assert "local GENESIS proxy gateway" in err

    ok, err = _validate_upstream_url("http://localhost:8000")
    assert not ok
    assert "local GENESIS proxy gateway" in err


def test_validate_upstream_url_accepts_valid_providers():
    ok, url = _validate_upstream_url("https://openrouter.ai/api/v1/")
    assert ok
    assert url == "https://openrouter.ai/api/v1"

    ok, url = _validate_upstream_url("https://api.openai.com/v1")
    assert ok
    assert url == "https://api.openai.com/v1"


def test_gatekeeping_refuses_when_unconfigured(monkeypatch, tmp_path):
    # Ensure no config file or env vars
    fake_config = tmp_path / "fake_proxy_config.json"
    monkeypatch.setattr(supervisor, "PROXY_CONFIG_PATH", fake_config)
    monkeypatch.delenv("GENESIS_UPSTREAM_URL", raising=False)
    monkeypatch.delenv("GENESIS_UPSTREAM_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    assert not supervisor.is_proxy_configured()
    # ensure_proxy_running must reject and return False without starting
    assert not supervisor.ensure_proxy_running(quiet=True, force_config_check=True)


def test_save_and_load_proxy_config(monkeypatch, tmp_path):
    fake_dir = tmp_path / ".genesis"
    fake_config = fake_dir / "proxy_config.json"
    monkeypatch.setattr(supervisor, "GENESIS_DIR", fake_dir)
    monkeypatch.setattr(supervisor, "PROXY_CONFIG_PATH", fake_config)

    supervisor.save_proxy_config(
        upstream_url="https://openrouter.ai/api/v1",
        api_key="sk-or-v1-testkey12345",
        default_model="gpt 6 astra",
        wired_clients=["opencode", "aider"],
    )

    assert fake_config.exists()
    loaded = supervisor.load_proxy_config()
    assert loaded is not None
    assert loaded["upstream_url"] == "https://openrouter.ai/api/v1"
    assert loaded["api_key"] == "sk-or-v1-testkey12345"
    assert loaded["default_model"] == "gpt 6 astra"
    assert "opencode" in loaded["wired_clients"]
    assert supervisor.is_proxy_configured()


def test_wire_proxy_client_opencode(tmp_path):
    opencode_cfg = tmp_path / "opencode.jsonc"
    initial = '{\n  "$schema": "https://opencode.ai/config.json",\n  "mcp": {}\n}\n'
    opencode_cfg.write_text(initial, encoding="utf-8")

    client = DiscoveredClient(
        id="opencode",
        name="OpenCode",
        capabilities=[ClientCapability.MCP, ClientCapability.PROXY],
        config_path=opencode_cfg,
        detected=True,
    )

    ok, msg = ClientRegistry.wire_proxy_client(
        client,
        model_name="gpt 6 astra",
        proxy_url="http://127.0.0.1:8000/v1",
    )
    assert ok
    data = json.loads(opencode_cfg.read_text(encoding="utf-8"))
    assert "provider" in data
    assert "genesis-proxy" in data["provider"]
    provider = data["provider"]["genesis-proxy"]
    assert provider["options"]["baseURL"] == "http://127.0.0.1:8000/v1"
    assert "gpt-6-astra" in provider["models"]
    assert provider["models"]["gpt-6-astra"]["name"] == "gpt 6 astra"


def test_wire_proxy_client_aider(tmp_path):
    aider_cfg = tmp_path / ".aider.conf.yml"
    client = DiscoveredClient(
        id="aider",
        name="Aider",
        capabilities=[ClientCapability.PROXY],
        config_path=aider_cfg,
        detected=True,
    )

    ok, msg = ClientRegistry.wire_proxy_client(
        client,
        model_name="gpt 6 astra",
        proxy_url="http://127.0.0.1:8000/v1",
    )
    assert ok
    content = aider_cfg.read_text(encoding="utf-8")
    assert "openai-api-base: http://127.0.0.1:8000/v1" in content
    assert "openai/gpt 6 astra" in content


def test_wire_proxy_client_zed(tmp_path):
    zed_cfg = tmp_path / "settings.json"
    client = DiscoveredClient(
        id="zed",
        name="Zed",
        capabilities=[ClientCapability.MCP, ClientCapability.PROXY],
        config_path=zed_cfg,
        detected=True,
    )

    ok, msg = ClientRegistry.wire_proxy_client(
        client,
        proxy_url="http://127.0.0.1:8000/v1",
    )
    assert ok
    data = json.loads(zed_cfg.read_text(encoding="utf-8"))
    assert data["language_models"]["openai"]["api_url"] == "http://127.0.0.1:8000/v1"


def test_wire_proxy_client_sdk_gateway(tmp_path):
    env_cfg = tmp_path / "genesis.env"
    client = DiscoveredClient(
        id="sdk_gateway",
        name="SDK Gateway",
        capabilities=[ClientCapability.PROXY],
        config_path=env_cfg,
        detected=True,
    )

    ok, msg = ClientRegistry.wire_proxy_client(
        client,
        model_name="gpt 6 astra",
        proxy_url="http://127.0.0.1:8000/v1",
    )
    assert ok
    content = env_cfg.read_text(encoding="utf-8")
    assert "OPENAI_BASE_URL=http://127.0.0.1:8000/v1" in content
    assert "GENESIS_TARGET_MODEL=gpt 6 astra" in content


def test_run_proxy_setup_cli_flow(monkeypatch, tmp_path):
    fake_dir = tmp_path / ".genesis"
    fake_config = fake_dir / "proxy_config.json"
    monkeypatch.setattr(supervisor, "GENESIS_DIR", fake_dir)
    monkeypatch.setattr(supervisor, "PROXY_CONFIG_PATH", fake_config)

    # Run proxy setup non-interactively with --yes and --no-start
    rc = run_proxy_setup([
        "--upstream-url", "https://openrouter.ai/api/v1",
        "--api-key", "sk-or-v1-supersecret123",
        "--model", "gpt 6 astra",
        "--yes",
        "--no-start",
    ])

    assert rc == 0
    assert fake_config.exists()
    cfg = json.loads(fake_config.read_text(encoding="utf-8"))
    assert cfg["upstream_url"] == "https://openrouter.ai/api/v1"
    assert cfg["api_key"] == "sk-or-v1-supersecret123"
    assert cfg["default_model"] == "gpt 6 astra"


def test_run_proxy_setup_without_model(monkeypatch, tmp_path):
    fake_dir = tmp_path / ".genesis"
    fake_config = fake_dir / "proxy_config.json"
    monkeypatch.setattr(supervisor, "GENESIS_DIR", fake_dir)
    monkeypatch.setattr(supervisor, "PROXY_CONFIG_PATH", fake_config)

    # Setup does NOT require a model upfront
    rc = run_proxy_setup([
        "--upstream-url", "https://openrouter.ai/api/v1",
        "--api-key", "sk-or-v1-testkey999",
        "--yes",
        "--no-start",
    ])

    assert rc == 0
    assert fake_config.exists()
    cfg = json.loads(fake_config.read_text(encoding="utf-8"))
    assert cfg["upstream_url"] == "https://openrouter.ai/api/v1"
    assert cfg["api_key"] == "sk-or-v1-testkey999"

