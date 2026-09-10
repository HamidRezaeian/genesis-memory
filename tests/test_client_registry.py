"""Unit & Integration Tests for Client Registry, JSONC Merger, and Auto-Wiring."""

import json
from pathlib import Path
import tempfile
from unittest.mock import patch
import pytest

from genesis_memory.cli.client_registry import (
    ClientCapability,
    ClientRegistry,
    DiscoveredClient,
    get_default_daemon_path,
    get_default_hook_path,
    get_default_memory_db,
)
from genesis_memory.cli.jsonc_merger import (
    deep_merge,
    merge_jsonc_file_content,
    parse_jsonc,
    strip_jsonc_comments,
)


class TestJSONCMerger:
    def test_strip_single_line_comments(self):
        text = '{\n  // This is a comment\n  "key": "value" // inline comment\n}'
        cleaned = strip_jsonc_comments(text)
        assert "//" not in cleaned
        data = parse_jsonc(text)
        assert data == {"key": "value"}

    def test_strip_multi_line_comments(self):
        text = '{\n  /* Header comment\n     spanning lines */\n  "foo": /* inline */ "bar"\n}'
        data = parse_jsonc(text)
        assert data == {"foo": "bar"}

    def test_preserve_urls_inside_strings(self):
        text = '{\n  "$schema": "https://opencode.ai/config.json",\n  "comment": "http://foo.bar//baz"\n}'
        data = parse_jsonc(text)
        assert data["$schema"] == "https://opencode.ai/config.json"
        assert data["comment"] == "http://foo.bar//baz"

    def test_trailing_comma_cleanup(self):
        text = '{\n  "a": 1,\n  "b": [1, 2, ],\n}'
        data = parse_jsonc(text)
        assert data == {"a": 1, "b": [1, 2]}

    def test_deep_merge_preserves_existing_keys(self):
        base = {
            "mcp": {
                "arena_assistant": {
                    "type": "local",
                    "command": ["node", "server.js"],
                    "enabled": True,
                }
            },
            "experimental": {"mcp_timeout": 900000},
        }
        patch_dict = {
            "mcp": {
                "genesis-memory": {
                    "type": "local",
                    "command": ["python", "daemon.py"],
                    "enabled": True,
                }
            }
        }
        merged = deep_merge(base, patch_dict)
        assert "arena_assistant" in merged["mcp"]
        assert "genesis-memory" in merged["mcp"]
        assert merged["experimental"]["mcp_timeout"] == 900000

    def test_merge_jsonc_file_content(self):
        original = """
        {
          // Existing tool
          "mcp": {
            "custom": {"enabled": true}
          }
        }
        """
        patch_dict = {
            "mcp": {
                "genesis-memory": {"enabled": True}
            }
        }
        res = merge_jsonc_file_content(original, patch_dict)
        data = json.loads(res)
        assert data["mcp"]["custom"]["enabled"] is True
        assert data["mcp"]["genesis-memory"]["enabled"] is True


class TestClientRegistry:
    def test_discovery_returns_known_clients(self):
        clients = ClientRegistry.discover_all()
        client_ids = [c.id for c in clients]
        assert "opencode" in client_ids
        assert "cursor" in client_ids
        assert "claude_code" in client_ids
        assert "claude_desktop" in client_ids
        assert "antigravity" in client_ids

    def test_client_capability_matrices(self):
        clients = ClientRegistry.discover_all()
        by_id = {c.id: c for c in clients}

        # OpenCode: Tri-Modal
        assert ClientCapability.MCP in by_id["opencode"].capabilities
        assert ClientCapability.PROXY in by_id["opencode"].capabilities
        assert ClientCapability.HOOK in by_id["opencode"].capabilities

        # Cursor: Tri-Modal (MCP, Hook, Proxy)
        assert ClientCapability.MCP in by_id["cursor"].capabilities
        assert ClientCapability.HOOK in by_id["cursor"].capabilities
        assert ClientCapability.PROXY in by_id["cursor"].capabilities

        # Claude Code: Tri-Modal (Hook, Proxy, MCP)
        assert ClientCapability.HOOK in by_id["claude_code"].capabilities
        assert ClientCapability.PROXY in by_id["claude_code"].capabilities
        assert ClientCapability.MCP in by_id["claude_code"].capabilities

        # Claude Desktop: Stdio MCP only
        assert by_id["claude_desktop"].capabilities == [ClientCapability.MCP]

        # Antigravity: Native MCP & Hook
        assert ClientCapability.MCP in by_id["antigravity"].capabilities
        assert ClientCapability.HOOK in by_id["antigravity"].capabilities

    def test_generate_patch_opencode(self):
        daemon = Path("/path/to/daemon.py")
        db = Path("/path/to/memory.db")
        patch_data = ClientRegistry.generate_patch("opencode", daemon_path=daemon, db_path=db)
        assert "mcp" in patch_data
        assert "genesis-memory" in patch_data["mcp"]
        server_cfg = patch_data["mcp"]["genesis-memory"]
        assert server_cfg["type"] == "local"
        assert server_cfg["environment"]["GENESIS_DAEMON_DB"] == "/path/to/memory.db"

    def test_generate_patch_claude_desktop(self):
        daemon = Path("/path/to/daemon.py")
        db = Path("/path/to/memory.db")
        patch_data = ClientRegistry.generate_patch("claude_desktop", daemon_path=daemon, db_path=db)
        assert "mcpServers" in patch_data
        assert "genesis-memory" in patch_data["mcpServers"]
        server_cfg = patch_data["mcpServers"]["genesis-memory"]
        assert server_cfg["env"]["GENESIS_DAEMON_DB"] == "/path/to/memory.db"

    def test_generate_patch_claude_code(self):
        daemon = Path("/path/to/daemon.py")
        db = Path("/path/to/memory.db")
        hook = Path("/path/to/hook.py")
        patch_data = ClientRegistry.generate_patch("claude_code", daemon_path=daemon, db_path=db, hook_path=hook)
        assert "env" in patch_data
        assert "hooks" in patch_data
        assert "PrePrompt" in patch_data["hooks"]

    def test_wire_client_mock_file(self, tmp_path):
        cfg_file = tmp_path / "opencode.jsonc"
        cfg_file.write_text("""
        {
          "$schema": "https://opencode.ai/config.json",
          "mcp": {
            "arena_assistant": {
              "type": "local",
              "command": ["node", "index.js"],
              "enabled": true
            }
          }
        }
        """, encoding="utf-8")

        client = DiscoveredClient(
            id="opencode",
            name="OpenCode",
            capabilities=[ClientCapability.MCP, ClientCapability.PROXY, ClientCapability.HOOK],
            config_path=cfg_file,
            detected=True,
            configured=False,
        )

        ok, msg = ClientRegistry.wire_client(client)
        assert ok is True
        updated_data = json.loads(cfg_file.read_text(encoding="utf-8"))
        assert "arena_assistant" in updated_data["mcp"]
        assert "genesis-memory" in updated_data["mcp"]

    def test_opencode_plugin_content_invariants(self):
        plugin_path = Path.home() / ".config" / "opencode" / "plugins" / "genesis-memory.js"
        assert plugin_path.exists(), "genesis-memory.js must exist in OpenCode plugins directory"
        content = plugin_path.read_text(encoding="utf-8")
        assert "experimental.chat.system.transform" in content
        assert "experimental.session.compacting" in content
        assert "permission.ask" in content
        assert "chat.message" in content
        assert "spawnSync" in content
        assert "CACHE_TTL_MS" in content
        assert "GENESIS_PROXY_ACTIVE" in content

