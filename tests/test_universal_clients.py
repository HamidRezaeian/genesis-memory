"""Universal client matrix, export-config emitters, and the expanded headless spooler."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest

from genesis_memory.cli import config_formats as cf
from genesis_memory.cli import export_config
from genesis_memory.cli.client_registry import (
    CLIENT_SPECS, ClientCapability, ClientRegistry, ConfigFormat, DiscoveredClient, RenderContext, SERVER_KEY,
    opencode_plugin_source,
)
from genesis_memory.cli.run import (
    ALLOWLIST_COMMANDS, ALLOWLIST_SUBCOMMANDS, HEADLESS_ENV, build_headless_env, is_spoolable_command, main,
)

REQUIRED_CLIENTS = {
    "cursor", "claude_code", "opencode", "windsurf", "zed", "antigravity", "vscode", "vscode_cline",
    "roo_code", "continue", "jetbrains_junie", "neovim", "emacs", "aider", "codex_cli", "gemini_cli",
    "amazon_q", "goose", "claude_desktop", "sdk_gateway",
}

DAEMON = Path("/opt/genesis/daemon/server.py")
DB = Path("/home/dev/.genesis/memory.db")
HOOK = Path("/opt/genesis/hooks/subconscious_hook.py")


# --------------------------------------------------------------------------- registry
def test_registry_covers_every_ai_environment():
    ids = set(ClientRegistry.client_ids())
    missing = REQUIRED_CLIENTS - ids
    assert not missing, f"missing clients: {missing}"
    assert len(ids) == len(CLIENT_SPECS)  # no duplicate ids


def test_aliases_resolve_to_specs():
    for alias, expected in {"nvim": "neovim", "cline": "vscode_cline", "roo": "roo_code", "codex": "codex_cli",
                            "gemini": "gemini_cli", "langchain": "sdk_gateway", "junie": "jetbrains_junie",
                            "gptel": "emacs", "Claude": "claude_code", "jetbrains-junie": "jetbrains_junie"}.items():
        spec = ClientRegistry.spec(alias)
        assert spec is not None and spec.id == expected, alias
    assert ClientRegistry.spec("not-a-client") is None


def test_discover_all_is_read_only_and_complete(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".config"))
    before = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*"))
    clients = ClientRegistry.discover_all()
    after = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*"))
    assert before == after  # nothing written
    assert {c.id for c in clients} == set(ClientRegistry.client_ids())
    for c in clients:
        d = c.to_dict()
        assert set(d) >= {"id", "name", "family", "capabilities", "config_path", "format", "detected", "configured"}


def test_every_spec_renders_and_matches_its_format():
    ctx = RenderContext.build(DAEMON, DB, HOOK)
    for spec in CLIENT_SPECS:
        rendered = spec.render(ctx)
        if spec.format in (ConfigFormat.JSON, ConfigFormat.JSONC, ConfigFormat.NATIVE):
            assert isinstance(rendered, dict), spec.id
        else:
            assert isinstance(rendered, str) and rendered.strip(), spec.id
        text = ClientRegistry.render_config(spec.id, DAEMON, DB, HOOK)
        if spec.format != ConfigFormat.NATIVE:
            assert "genesis" in text.lower(), spec.id
        if spec.format == ConfigFormat.TOML:
            parsed = tomllib.loads(text)
            assert parsed["mcp_servers"][SERVER_KEY]["args"] == [DAEMON.as_posix()]
        if spec.format in (ConfigFormat.JSON, ConfigFormat.JSONC) and spec.format != ConfigFormat.NATIVE:
            json.loads(text)
        if ClientCapability.MCP in spec.capabilities and spec.format != ConfigFormat.NATIVE:
            # Claude Code's MCP entry lives in the auxiliary ~/.claude.json; its primary file carries the hook.
            assert (DAEMON.as_posix() in text) or (spec.id == "claude_code" and HOOK.as_posix() in text), spec.id
        if ClientCapability.PROXY in spec.capabilities and ClientCapability.MCP not in spec.capabilities:
            assert "127.0.0.1:8000" in text, spec.id


def test_generate_patch_canonical_shapes():
    vscode = ClientRegistry.generate_patch("vscode", DAEMON, DB)
    assert vscode["servers"][SERVER_KEY]["type"] == "stdio"
    zed = ClientRegistry.generate_patch("zed", DAEMON, DB)
    assert zed["context_servers"][SERVER_KEY]["command"]["args"] == [DAEMON.as_posix()]
    # Text-format clients still expose a canonical mcpServers dict for JSON export
    codex = ClientRegistry.generate_patch("codex_cli", DAEMON, DB)
    assert codex["mcpServers"][SERVER_KEY]["env"]["GENESIS_DAEMON_DB"] == DB.as_posix()
    aider = ClientRegistry.generate_patch("aider", DAEMON, DB)
    assert aider["env"]["OPENAI_BASE_URL"] == "http://127.0.0.1:8000/v1"
    assert ClientRegistry.generate_patch("nope") == {}


def _client(spec_id: str, path: Path) -> DiscoveredClient:
    spec = ClientRegistry.spec(spec_id)
    return DiscoveredClient(id=spec.id, name=spec.name, capabilities=list(spec.capabilities),
                            config_path=path, detected=True, configured=False)


def test_wire_toml_client_uses_idempotent_marker_block(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text('model = "o3"\n[mcp_servers.other]\ncommand = "node"\n', encoding="utf-8")
    ok, _ = ClientRegistry.wire_client(_client("codex_cli", cfg), DAEMON, DB, HOOK)
    assert ok
    first = cfg.read_text(encoding="utf-8")
    assert first.startswith('model = "o3"')
    assert '[mcp_servers.other]' in first and cf.MARKER_BEGIN in first
    ok, _ = ClientRegistry.wire_client(_client("codex_cli", cfg), DAEMON, DB, HOOK)
    assert cfg.read_text(encoding="utf-8") == first  # idempotent
    parsed = tomllib.loads(cfg.read_text(encoding="utf-8"))
    assert parsed["mcp_servers"][SERVER_KEY]["command"]
    assert parsed["mcp_servers"]["other"]["command"] == "node"
    ok, _ = ClientRegistry.unwire_client(_client("codex_cli", cfg))
    assert ok and cf.MARKER_BEGIN not in cfg.read_text(encoding="utf-8")
    assert '[mcp_servers.other]' in cfg.read_text(encoding="utf-8")


def test_wire_yaml_client_preserves_user_content(tmp_path):
    cfg = tmp_path / ".aider.conf.yml"
    cfg.write_text("model: gpt-4o\ndark-mode: true\n", encoding="utf-8")
    ok, _ = ClientRegistry.wire_client(_client("aider", cfg), DAEMON, DB, HOOK)
    text = cfg.read_text(encoding="utf-8")
    assert ok and text.startswith("model: gpt-4o\ndark-mode: true\n")
    assert "openai-api-base: http://127.0.0.1:8000/v1" in text


def test_wire_owned_files_are_fully_replaced(tmp_path):
    cfg = tmp_path / "genesis-memory.el"
    cfg.write_text("stale", encoding="utf-8")
    ok, _ = ClientRegistry.wire_client(_client("emacs", cfg), DAEMON, DB, HOOK)
    text = cfg.read_text(encoding="utf-8")
    assert ok and "stale" not in text and "mcp-hub-servers" in text and "gptel-make-openai" in text
    env = tmp_path / "genesis.env"
    ok, _ = ClientRegistry.wire_client(_client("sdk_gateway", env), DAEMON, DB, HOOK)
    assert ok and "OPENAI_BASE_URL=http://127.0.0.1:8000/v1" in env.read_text(encoding="utf-8")
    assert "ANTHROPIC_BASE_URL=http://127.0.0.1:8000" in env.read_text(encoding="utf-8")
    ok, _ = ClientRegistry.unwire_client(_client("sdk_gateway", env))
    assert ok and not env.exists()


def test_wire_json_client_deep_merges(tmp_path):
    cfg = tmp_path / "mcp.json"
    cfg.write_text(json.dumps({"servers": {"other": {"type": "stdio", "command": "x"}}, "inputs": []}), encoding="utf-8")
    ok, _ = ClientRegistry.wire_client(_client("vscode", cfg), DAEMON, DB, HOOK)
    data = json.loads(cfg.read_text(encoding="utf-8"))
    assert ok and "other" in data["servers"] and SERVER_KEY in data["servers"] and data["inputs"] == []


def test_native_client_writes_nothing(tmp_path):
    cfg = tmp_path / "ag"
    ok, msg = ClientRegistry.wire_client(_client("antigravity", cfg), DAEMON, DB, HOOK)
    assert ok and "native" in msg.lower() and not cfg.exists()


def test_opencode_plugin_template_invariants():
    src = opencode_plugin_source()
    assert "export const GenesisMemoryPlugin" in src
    assert "content never logged" in src
    assert "query.substring(0, 60)" not in src
    assert "${new Date().toISOString()}" in src  # braces un-escaped from the old f-string
    assert "{{" not in src


# --------------------------------------------------------------------------- config formats
def test_toml_yaml_env_emitters_roundtrip():
    data = {"mcp_servers": {"genesis-memory": {"command": "/usr/bin/python3", "args": ["/x/server.py"],
                                               "env": {"GENESIS_DAEMON_DB": "/h/.genesis/memory.db"},
                                               "enabled": True, "timeout": 900000}}, "model": "genesis"}
    assert tomllib.loads(cf.to_toml(data)) == data
    y = cf.to_yaml(data)
    assert "genesis-memory:" in y and "      - /x/server.py" in y and "enabled: true" in y
    env = cf.to_env({"A": "x y", "B": 3, "C": True, "D": "plain"})
    assert env == 'A="x y"\nB=3\nC=1\nD=plain\n'
    with pytest.raises(ValueError):
        cf.to_env({"bad-name": 1})
    with pytest.raises(ValueError):
        cf.render({}, "xml")


def test_yaml_quotes_reserved_scalars():
    y = cf.to_yaml({"a": "yes", "b": "null", "c": "007", "d": "x: y", "e": "", "f": None})
    assert 'a: "yes"' in y and 'b: "null"' in y and 'c: "007"' in y and 'd: "x: y"' in y
    assert 'e: ""' in y and "f: null" in y


def test_marker_block_merge_strip_idempotent():
    base = "# user config\nfoo = 1\n"
    once = cf.merge_marker_block(base, 'bar = 2', "#")
    twice = cf.merge_marker_block(once, 'bar = 3', "#")
    assert once.startswith(base) and twice.count(cf.MARKER_BEGIN) == 1
    assert "bar = 3" in twice and "bar = 2" not in twice
    assert cf.strip_marker_block(twice, "#") == base
    assert cf.has_marker_block(twice) and not cf.has_marker_block(base)
    lisp = cf.merge_marker_block("", "(setq x 1)", ";;")
    assert lisp.startswith(";; " + cf.MARKER_BEGIN)


# --------------------------------------------------------------------------- export-config
def test_export_config_formats(capsys):
    for fmt, needle in (("json", '"mcpServers"'), ("yaml", "mcpServers:"), ("toml", "[mcpServers.genesis-memory]"),
                        ("env", "OPENAI_BASE_URL="), ("lua", "mcpServers = {"), ("elisp", "mcp-hub-servers"),
                        ("native", "from openai import OpenAI")):
        assert main(["export-config", "--format", fmt]) == 0
        out = capsys.readouterr().out
        assert needle in out, (fmt, out[:200])


def test_export_config_client_native_and_errors(capsys, tmp_path):
    assert main(["export-config", "--client", "zed", "--format", "native"]) == 0
    assert "context_servers" in capsys.readouterr().out
    assert main(["export-config", "--client", "codex", "--format", "native"]) == 0
    assert "[mcp_servers.genesis-memory]" in capsys.readouterr().out
    assert main(["export-config", "--client", "nvim", "--format", "lua"]) == 0
    assert 'command = ' in capsys.readouterr().out
    assert main(["export-config", "--client", "unknown-thing"]) == 2
    assert "unknown client" in capsys.readouterr().err
    out = tmp_path / "snip.json"
    assert main(["export-config", "--client", "cursor", "--out", str(out)]) == 0
    assert json.loads(out.read_text())["mcpServers"][SERVER_KEY]


def test_export_all_writes_one_snippet_per_client(tmp_path):
    ctx = RenderContext.build(DAEMON, DB, HOOK)
    files = export_config.export_all(tmp_path, ctx)
    names = {f.name for f in files}
    assert {"cursor.json", "codex_cli.toml", "emacs.el", "aider.yaml", "gateway.env", "neovim.lua", "antigravity.md"} <= names
    assert len(files) == len(CLIENT_SPECS) + 2


def test_clients_matrix_cli(capsys):
    assert main(["clients"]) == 0
    out = capsys.readouterr().out
    assert "Universal Client Matrix" in out and "neovim" in out and "jetbrains_junie" in out
    assert main(["clients", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert {d["id"] for d in data} == set(ClientRegistry.client_ids())


# --------------------------------------------------------------------------- spooler
@pytest.mark.parametrize("argv", [
    ["pytest", "tests/"], ["python", "-m", "pytest"], ["python3", "-m", "ruff", "check", "."],
    ["git", "status"], ["git", "log", "--oneline"], ["npm", "test"], ["pnpm", "build"], ["yarn", "lint"],
    ["bun", "test"], ["deno", "test"], ["tsc", "--noEmit"], ["cargo", "test"], ["cargo", "clippy"],
    ["go", "test", "./..."], ["go", "vet"], ["dotnet", "test"], ["dotnet", "build"], ["gradlew", "test"],
    ["mvn", "test"], ["docker", "build", "."], ["docker", "compose", "config"], ["kubectl", "get", "pods"],
    ["terraform", "plan"], ["ruff", "check", "."], ["mypy", "."], ["black", "--check", "."],
    ["pip", "install", "-e", "."], ["uv", "sync"], ["poetry", "install"], ["make", "test"],
    ["gh", "pr", "list"], ["/usr/local/bin/Pytest.EXE", "-q"], ["./gradlew", "build"], ["pytest", "-p", "no:cacheprovider"],
])
def test_expanded_allowlist_accepts_toolchains(argv):
    assert is_spoolable_command(argv) is True, argv


@pytest.mark.parametrize("argv", [
    [], ["vim", "file.py"], ["python", "interactive.py"], ["bash"], ["git"], ["git", "rebase", "-i"],
    ["git", "commit"], ["git", "add", "-p"], ["git", "push"], ["docker", "run", "-it", "ubuntu"],
    ["docker", "exec", "-it", "c", "sh"], ["kubectl", "exec", "-it", "pod", "--", "sh"], ["kubectl", "logs", "-f", "x"],
    ["npm", "start"], ["cargo", "watch"], ["vitest", "--ui"], ["jest", "--watch"], ["python", "-m", "http.server"],
    ["ssh", "host"], ["psql"], ["docker", "compose", "up", "--watch"],
])
def test_allowlist_rejects_interactive_or_unknown(argv):
    assert is_spoolable_command(argv) is False, argv


def test_allowlist_has_every_required_toolchain():
    for tool in ("pytest", "cargo", "git", "npm", "tsc", "go", "dotnet", "docker", "ruff", "mypy",
                 "pnpm", "yarn", "bun", "deno", "gradle", "mvn", "make", "kubectl", "terraform", "pip", "uv"):
        assert tool in ALLOWLIST_COMMANDS, tool
    for tool in ("git", "npm", "cargo", "go", "dotnet", "docker", "kubectl"):
        assert ALLOWLIST_SUBCOMMANDS[tool], tool


def test_headless_env_is_strict_and_non_destructive():
    env = build_headless_env({"PATH": "/bin", "HOME": "/h", "CI": "0"})
    assert env["PATH"] == "/bin" and env["HOME"] == "/h"
    for k, v in (("CI", "1"), ("TERM", "dumb"), ("NO_COLOR", "1"), ("PAGER", "cat"), ("GIT_TERMINAL_PROMPT", "0"),
                 ("PIP_NO_INPUT", "1"), ("CARGO_TERM_COLOR", "never"), ("DOTNET_CLI_TELEMETRY_OPTOUT", "1"),
                 ("TF_INPUT", "0"), ("BUILDKIT_PROGRESS", "plain")):
        assert env[k] == v, k
    assert HEADLESS_ENV["npm_config_yes"] == "true"


def test_genesis_run_expanded_toolchain_end_to_end(tmp_path, monkeypatch, capsys):
    """`genesis run -- git log` spools + summarizes + preserves exit code with the new allowlist."""
    import subprocess
    monkeypatch.setenv("GENESIS_SPOOL_DIR", str(tmp_path / "spool"))
    if subprocess.run(["git", "rev-parse", "--is-inside-work-tree"], capture_output=True).returncode != 0:
        pytest.skip("not in a git checkout")
    code = main(["run", "--", "git", "log", "--oneline", "-n", "3"])
    out = capsys.readouterr().out
    assert code == 0
    assert "ctx:log/" in out
    assert list((tmp_path / "spool").glob("*.log"))
