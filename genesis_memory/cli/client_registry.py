"""Universal AI Client Capability Matrix & Auto-Discovery Registry.

Every AI coding environment speaks one (or more) of three GENESIS primitives:

- **MCP**   — stdio Model Context Protocol server (``remember``/``recall``/...).
- **Hook**  — lifecycle priming: the subconscious capsule is injected before each prompt.
- **Proxy** — stateless OpenAI/Anthropic-compatible gateway on ``127.0.0.1:8000``.

This module is a *data-driven* registry: each :class:`ClientSpec` declares where a
client keeps its config, how to detect the client, which primitives it supports,
and how to render the patch in the client's own dialect (JSON / JSONC / YAML /
TOML / Emacs Lisp / dotenv). Structured formats are deep-merged; text formats
use idempotent ``>>> genesis-memory >>>`` marker blocks. Nothing outside GENESIS'
own keys or block is ever modified (Non-Hostile Invariant). Plugin/hook payloads
log metadata only — content never logged.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from genesis_memory.cli import config_formats as cf
from genesis_memory.cli.jsonc_merger import merge_jsonc_file_content, parse_jsonc

SERVER_KEY = "genesis-memory"
PROXY_URL = "http://127.0.0.1:8000/v1"
PROXY_ANTHROPIC_URL = "http://127.0.0.1:8000"
TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"


class ClientCapability(str, Enum):
    MCP = "MCP"
    HOOK = "Hook"
    PROXY = "Proxy"


class ConfigFormat(str, Enum):
    JSON = "json"
    JSONC = "jsonc"
    YAML = "yaml"
    TOML = "toml"
    ELISP = "elisp"
    ENV = "env"
    NATIVE = "native"  # client already ships GENESIS integration; nothing to write


class ClientFamily(str, Enum):
    IDE = "IDE / Editor"
    CLI = "Terminal Agent"
    DESKTOP = "Desktop App"
    EDITOR_PLUGIN = "Editor Plugin"
    FRAMEWORK = "Agent Framework / SDK"


@dataclass
class DiscoveredClient:
    id: str
    name: str
    capabilities: List[ClientCapability]
    config_path: Path
    detected: bool
    configured: bool = False
    details: str = ""
    family: str = ClientFamily.IDE.value
    format: str = ConfigFormat.JSON.value
    docs_url: str = ""

    @property
    def capability_tags(self) -> str:
        return ", ".join(cap.value for cap in self.capabilities)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id, "name": self.name, "family": self.family,
            "capabilities": [c.value for c in self.capabilities],
            "config_path": str(self.config_path), "format": self.format,
            "detected": self.detected, "configured": self.configured,
            "details": self.details, "docs_url": self.docs_url,
        }


# --------------------------------------------------------------------------- paths
def get_default_daemon_path() -> Path:
    return (Path(__file__).resolve().parent.parent / "daemon" / "server.py").resolve()


def get_default_hook_path() -> Path:
    return (Path(__file__).resolve().parent.parent / "hooks" / "subconscious_hook.py").resolve()


def get_default_memory_db() -> Path:
    return Path.home() / ".genesis" / "memory.db"


def _home() -> Path:
    return Path.home()


def _appdata() -> Path:
    return Path(os.environ.get("APPDATA") or (_home() / "AppData" / "Roaming"))


def _vscode_user_dir(product: str = "Code") -> Path:
    if sys.platform == "win32":
        return _appdata() / product / "User"
    if sys.platform == "darwin":
        return _home() / "Library" / "Application Support" / product / "User"
    return _home() / ".config" / product / "User"


def _xdg_config() -> Path:
    if sys.platform == "win32":
        return _appdata()
    return Path(os.environ.get("XDG_CONFIG_HOME") or (_home() / ".config"))


def _module_available(*names: str) -> bool:
    for n in names:
        try:
            if importlib.util.find_spec(n) is not None:
                return True
        except (ImportError, ValueError):
            continue
    return False


def _on_path(*binaries: str) -> bool:
    return any(shutil.which(b) for b in binaries)


# --------------------------------------------------------------------------- render context
@dataclass
class RenderContext:
    python_bin: str
    daemon: str
    hook: str
    db: str

    @classmethod
    def build(cls, daemon_path: Optional[Path] = None, db_path: Optional[Path] = None,
              hook_path: Optional[Path] = None) -> "RenderContext":
        norm = ClientRegistry._normalize_path_str
        return cls(
            python_bin=norm(Path(sys.executable)),
            daemon=norm(daemon_path or get_default_daemon_path()),
            hook=norm(hook_path or get_default_hook_path()),
            db=norm(db_path or get_default_memory_db()),
        )

    def mcp_server(self) -> Dict[str, Any]:
        return {"command": self.python_bin, "args": [self.daemon],
                "env": {"GENESIS_DAEMON_DB": self.db}}

    def mcp_servers(self) -> Dict[str, Any]:
        return {"mcpServers": {SERVER_KEY: self.mcp_server()}}

    def proxy_env(self) -> Dict[str, str]:
        return {
            "OPENAI_BASE_URL": PROXY_URL,
            "OPENAI_API_BASE": PROXY_URL,
            "ANTHROPIC_BASE_URL": PROXY_ANTHROPIC_URL,
            "GENESIS_DAEMON_DB": self.db,
        }


# --------------------------------------------------------------------------- spec
@dataclass
class ClientSpec:
    id: str
    name: str
    family: ClientFamily
    capabilities: List[ClientCapability]
    format: ConfigFormat
    config_path: Callable[[], Path]
    detect: Callable[[Path], bool]
    render: Callable[[RenderContext], Any]  # dict for json/jsonc, str body for text formats
    is_configured: Callable[[str], bool]
    details: str = ""
    docs_url: str = ""
    comment: str = "#"  # comment token for marker blocks in text formats
    owns_file: bool = False  # dedicated drop-in file fully owned by GENESIS
    aux_wire: Optional[Callable[["ClientSpec", RenderContext], None]] = None
    aliases: List[str] = field(default_factory=list)


def _json_has(path_keys: List[str]) -> Callable[[str], bool]:
    def check(text: str) -> bool:
        try:
            data = parse_jsonc(text)
        except Exception:
            return False
        node: Any = data
        for k in path_keys:
            if not isinstance(node, dict) or k not in node:
                return False
            node = node[k]
        return True
    return check


def _marker_present(text: str) -> bool:
    return cf.has_marker_block(text)


def _always(_text: str) -> bool:
    return True


def _parent_exists(p: Path) -> bool:
    return p.parent.exists()


# --------------------------------------------------------------------------- renderers
def _r_mcp_servers(ctx: RenderContext) -> Dict[str, Any]:
    return ctx.mcp_servers()


def _r_opencode(ctx: RenderContext) -> Dict[str, Any]:
    return {
        "mcp": {SERVER_KEY: {"type": "local", "command": [ctx.python_bin, ctx.daemon],
                             "environment": {"GENESIS_DAEMON_DB": ctx.db},
                             "enabled": True, "timeout": 900000}},
        "provider": {"genesis-proxy": {
            "npm": "@ai-sdk/openai-compatible", "name": "GENESIS Proxy",
            "options": {"baseURL": PROXY_URL, "apiKey": "not-needed"},
            "models": {"genesis-stateless": {"name": "GENESIS Stateless (Single-Prompt)"}}}},
    }


def _r_claude_code(ctx: RenderContext) -> Dict[str, Any]:
    return {
        "env": {"GENESIS_DAEMON_DB": ctx.db},
        "hooks": {"PrePrompt": [{"matcher": ".*", "hooks": [
            {"type": "command", "command": f'"{ctx.python_bin}" "{ctx.hook}" --claude'}]}]},
    }


def _r_zed(ctx: RenderContext) -> Dict[str, Any]:
    return {"context_servers": {SERVER_KEY: {"command": {
        "path": ctx.python_bin, "args": [ctx.daemon], "env": {"GENESIS_DAEMON_DB": ctx.db}}}}}


def _r_vscode(ctx: RenderContext) -> Dict[str, Any]:
    # VS Code native MCP (Copilot agent mode) uses a top-level "servers" map.
    return {"servers": {SERVER_KEY: {"type": "stdio", **ctx.mcp_server()}}}


def _r_continue(ctx: RenderContext) -> str:
    return cf.to_yaml({"name": "GENESIS Memory", "version": "0.5.0", "schema": "v1",
                       "mcpServers": [{"name": SERVER_KEY, **ctx.mcp_server()}]})


def _r_codex_toml(ctx: RenderContext) -> str:
    return cf.to_toml({"mcp_servers": {SERVER_KEY: ctx.mcp_server()}})


def _r_goose_yaml(ctx: RenderContext) -> str:
    return cf.to_yaml({"extensions": {SERVER_KEY: {
        "enabled": True, "type": "stdio", "name": SERVER_KEY, "cmd": ctx.python_bin,
        "args": [ctx.daemon], "envs": {"GENESIS_DAEMON_DB": ctx.db}, "timeout": 300}}})


def _r_aider_yaml(ctx: RenderContext) -> str:
    return cf.to_yaml({"openai-api-base": PROXY_URL, "set-env": [f"GENESIS_DAEMON_DB={ctx.db}"]})


def _r_emacs_elisp(ctx: RenderContext) -> str:
    return (
        ";; GENESIS Memory for gptel / aidermacs / mcp.el — (load-file \"~/.emacs.d/genesis-memory.el\")\n"
        "(with-eval-after-load 'mcp\n"
        "  (add-to-list 'mcp-hub-servers\n"
        f"               '(\"{SERVER_KEY}\" . (:command \"{ctx.python_bin}\"\n"
        f"                                    :args (\"{ctx.daemon}\")\n"
        f"                                    :env (:GENESIS_DAEMON_DB \"{ctx.db}\")))))\n"
        "(with-eval-after-load 'gptel\n"
        "  (gptel-make-openai \"GENESIS Proxy\"\n"
        "    :host \"127.0.0.1:8000\" :protocol \"http\" :endpoint \"/v1/chat/completions\"\n"
        "    :stream t :key \"not-needed\" :models '(genesis-stateless)))\n"
        f"(setenv \"GENESIS_DAEMON_DB\" \"{ctx.db}\")\n"
    )


def _r_env(ctx: RenderContext) -> str:
    return cf.to_env(ctx.proxy_env())


def _r_native(_ctx: RenderContext) -> Dict[str, Any]:
    return {}


# --------------------------------------------------------------------------- aux wiring
def _aux_cursor(_spec: ClientSpec, ctx: RenderContext) -> None:
    hooks_file = _home() / ".cursor" / "hooks.json"
    patch = {"version": 1, "hooks": {"beforeSubmitPrompt": [
        {"command": f'"{ctx.python_bin}" "{ctx.hook}" --cursor'}]}}
    existing = hooks_file.read_text(encoding="utf-8") if hooks_file.exists() else ""
    hooks_file.parent.mkdir(parents=True, exist_ok=True)
    hooks_file.write_text(merge_jsonc_file_content(existing, patch), encoding="utf-8")


def _aux_claude_code(_spec: ClientSpec, ctx: RenderContext) -> None:
    claude_json = _home() / ".claude.json"
    existing = claude_json.read_text(encoding="utf-8") if claude_json.exists() else ""
    claude_json.write_text(merge_jsonc_file_content(existing, ctx.mcp_servers()), encoding="utf-8")


def opencode_plugin_source() -> str:
    """OpenCode plugin (TIER-1 hardened; logs lengths only — content never logged)."""
    return (TEMPLATES_DIR / "opencode_plugin.js").read_text(encoding="utf-8")


def _aux_opencode(_spec: ClientSpec, ctx: RenderContext) -> None:
    genesis_home = _home() / ".genesis"
    genesis_home.mkdir(parents=True, exist_ok=True)
    source_hook = Path(ctx.hook)
    if source_hook.exists():
        try:
            (genesis_home / "subconscious_memory_hook.py").write_bytes(source_hook.read_bytes())
        except Exception:
            pass
    plugins_dir = _xdg_config() / "opencode" / "plugins"
    plugins_dir.mkdir(parents=True, exist_ok=True)
    deprecated_ts = plugins_dir / "genesis_memory.ts"
    if deprecated_ts.exists():
        try:
            deprecated_ts.unlink()
        except Exception:
            pass
    (plugins_dir / "genesis-memory.js").write_text(opencode_plugin_source(), encoding="utf-8")


# --------------------------------------------------------------------------- detection helpers
def _claude_desktop_path() -> Path:
    if sys.platform == "win32":
        return _appdata() / "Claude" / "claude_desktop_config.json"
    if sys.platform == "darwin":
        return _home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    return _home() / ".config" / "Claude" / "claude_desktop_config.json"


def _zed_path() -> Path:
    if sys.platform == "win32":
        return _appdata() / "Zed" / "settings.json"
    return _home() / ".config" / "zed" / "settings.json"


def _cursor_detect(_p: Path) -> bool:
    return (_home() / ".cursor").exists() or (_appdata() / "Cursor").exists()


def _cursor_configured(text: str) -> bool:
    if not _json_has(["mcpServers", SERVER_KEY])(text):
        return False
    hooks_file = _home() / ".cursor" / "hooks.json"
    return hooks_file.exists() and "subconscious" in hooks_file.read_text(encoding="utf-8")


def _claude_code_configured(text: str) -> bool:
    try:
        hook_ok = "subconscious" in json.dumps(parse_jsonc(text).get("hooks", {}))
    except Exception:
        return False
    claude_json = _home() / ".claude.json"
    if not claude_json.exists():
        return False
    try:
        return hook_ok and SERVER_KEY in parse_jsonc(claude_json.read_text(encoding="utf-8")).get("mcpServers", {})
    except Exception:
        return False


def _antigravity_detect(p: Path) -> bool:
    return p.parent.exists() or (Path.cwd() / ".agents").exists()


def _frameworks_detect(_p: Path) -> bool:
    return _module_available("langchain", "langchain_core", "llama_index", "crewai", "autogen",
                             "autogen_agentchat", "openai", "anthropic")


def _jetbrains_detect(p: Path) -> bool:
    return (p.parent.parent.exists() or (_home() / ".config" / "JetBrains").exists()
            or (_home() / "Library" / "Application Support" / "JetBrains").exists()
            or (_appdata() / "JetBrains").exists())


CLIENT_SPECS: List[ClientSpec] = [
    ClientSpec(
        id="opencode", name="OpenCode", family=ClientFamily.CLI,
        capabilities=[ClientCapability.MCP, ClientCapability.PROXY, ClientCapability.HOOK],
        format=ConfigFormat.JSONC,
        config_path=lambda: _xdg_config() / "opencode" / "opencode.jsonc",
        detect=_parent_exists, render=_r_opencode,
        is_configured=_json_has(["mcp", SERVER_KEY]),
        details="Tri-Modal: local MCP server, stateless gateway provider & plugin hooks (genesis-memory.js)",
        docs_url="https://opencode.ai/docs/mcp-servers", aux_wire=_aux_opencode,
    ),
    ClientSpec(
        id="cursor", name="Cursor", family=ClientFamily.IDE,
        capabilities=[ClientCapability.MCP, ClientCapability.PROXY, ClientCapability.HOOK],
        format=ConfigFormat.JSON,
        config_path=lambda: _home() / ".cursor" / "mcp.json",
        detect=_cursor_detect, render=_r_mcp_servers, is_configured=_cursor_configured,
        details="Tri-Modal: mcp.json + Cursor 1.7 lifecycle hooks (hooks.json) + OpenAI-compatible proxy",
        docs_url="https://docs.cursor.com/context/model-context-protocol", aux_wire=_aux_cursor,
    ),
    ClientSpec(
        id="claude_code", name="Claude Code", family=ClientFamily.CLI,
        capabilities=[ClientCapability.HOOK, ClientCapability.PROXY, ClientCapability.MCP],
        format=ConfigFormat.JSON,
        config_path=lambda: _home() / ".claude" / "settings.json",
        detect=_parent_exists, render=_r_claude_code, is_configured=_claude_code_configured,
        details="Tri-Modal: PrePrompt lifecycle hook (settings.json), stdio MCP (~/.claude.json) & headless runner flags",
        docs_url="https://docs.anthropic.com/en/docs/claude-code/mcp", aux_wire=_aux_claude_code,
        aliases=["claude"],
    ),
    ClientSpec(
        id="claude_desktop", name="Claude Desktop", family=ClientFamily.DESKTOP,
        capabilities=[ClientCapability.MCP], format=ConfigFormat.JSON,
        config_path=_claude_desktop_path, detect=_parent_exists, render=_r_mcp_servers,
        is_configured=_json_has(["mcpServers", SERVER_KEY]),
        details="Native stdio MCP via claude_desktop_config.json",
        docs_url="https://modelcontextprotocol.io/quickstart/user",
    ),
    ClientSpec(
        id="antigravity", name="Antigravity IDE", family=ClientFamily.IDE,
        capabilities=[ClientCapability.MCP, ClientCapability.HOOK], format=ConfigFormat.NATIVE,
        config_path=lambda: _home() / ".gemini" / "antigravity-ide" / "mcp",
        detect=_antigravity_detect, render=_r_native, is_configured=_always,
        details="Native built-in integration (.agents/mcp_config.json + .agents/hooks.json)",
    ),
    ClientSpec(
        id="windsurf", name="Windsurf (Codeium)", family=ClientFamily.IDE,
        capabilities=[ClientCapability.MCP, ClientCapability.PROXY], format=ConfigFormat.JSON,
        config_path=lambda: _home() / ".codeium" / "windsurf" / "mcp_config.json",
        detect=_parent_exists, render=_r_mcp_servers,
        is_configured=_json_has(["mcpServers", SERVER_KEY]),
        details="Cascade MCP integration via mcp_config.json",
        docs_url="https://docs.windsurf.com/windsurf/cascade/mcp", aliases=["codeium"],
    ),
    ClientSpec(
        id="zed", name="Zed", family=ClientFamily.IDE,
        capabilities=[ClientCapability.MCP, ClientCapability.PROXY], format=ConfigFormat.JSONC,
        config_path=_zed_path, detect=_parent_exists, render=_r_zed,
        is_configured=_json_has(["context_servers", SERVER_KEY]),
        details="context_servers in settings.json + OpenAI-compatible provider endpoint",
        docs_url="https://zed.dev/docs/ai/mcp",
    ),
    ClientSpec(
        id="vscode", name="VS Code (Copilot agent mode)", family=ClientFamily.IDE,
        capabilities=[ClientCapability.MCP, ClientCapability.PROXY], format=ConfigFormat.JSONC,
        config_path=lambda: _vscode_user_dir("Code") / "mcp.json",
        detect=_parent_exists, render=_r_vscode,
        is_configured=_json_has(["servers", SERVER_KEY]),
        details="Native MCP servers (user-level mcp.json) — shared by Copilot Chat & Copilot CLI",
        docs_url="https://code.visualstudio.com/docs/copilot/chat/mcp-servers",
        aliases=["vscode_native", "copilot"],
    ),
    ClientSpec(
        id="vscode_cline", name="Cline (VS Code)", family=ClientFamily.EDITOR_PLUGIN,
        capabilities=[ClientCapability.MCP, ClientCapability.PROXY], format=ConfigFormat.JSON,
        config_path=lambda: _vscode_user_dir("Code") / "globalStorage" / "saoudrizwan.claude-dev" / "settings" / "cline_mcp_settings.json",
        detect=_parent_exists, render=_r_mcp_servers,
        is_configured=_json_has(["mcpServers", SERVER_KEY]),
        details="Extension MCP settings + OpenAI-compatible provider (base URL)",
        docs_url="https://docs.cline.bot/mcp", aliases=["cline"],
    ),
    ClientSpec(
        id="roo_code", name="Roo Code (VS Code)", family=ClientFamily.EDITOR_PLUGIN,
        capabilities=[ClientCapability.MCP, ClientCapability.PROXY], format=ConfigFormat.JSON,
        config_path=lambda: _vscode_user_dir("Code") / "globalStorage" / "rooveterinaryinc.roo-cline" / "settings" / "mcp_settings.json",
        detect=_parent_exists, render=_r_mcp_servers,
        is_configured=_json_has(["mcpServers", SERVER_KEY]),
        details="Global MCP settings + OpenAI-compatible provider",
        docs_url="https://docs.roocode.com/features/mcp/using-mcp-in-roo", aliases=["roo"],
    ),
    ClientSpec(
        id="continue", name="Continue.dev", family=ClientFamily.EDITOR_PLUGIN,
        capabilities=[ClientCapability.MCP, ClientCapability.PROXY], format=ConfigFormat.YAML,
        config_path=lambda: _home() / ".continue" / "mcpServers" / "genesis-memory.yaml",
        detect=lambda p: p.parent.parent.exists(), render=_r_continue, is_configured=_always,
        owns_file=True,
        details="Drop-in MCP block (~/.continue/mcpServers/*.yaml) for VS Code & JetBrains",
        docs_url="https://docs.continue.dev/customize/deep-dives/mcp",
    ),
    ClientSpec(
        id="jetbrains_junie", name="JetBrains Junie / AI Assistant", family=ClientFamily.IDE,
        capabilities=[ClientCapability.MCP, ClientCapability.PROXY], format=ConfigFormat.JSON,
        config_path=lambda: _home() / ".junie" / "mcp" / "mcp.json",
        detect=_jetbrains_detect, render=_r_mcp_servers,
        is_configured=_json_has(["mcpServers", SERVER_KEY]),
        details="Junie global MCP (~/.junie/mcp/mcp.json); AI Assistant imports the same mcpServers JSON",
        docs_url="https://www.jetbrains.com/help/junie/mcp-settings.html",
        aliases=["jetbrains", "junie", "intellij", "pycharm", "webstorm"],
    ),
    ClientSpec(
        id="neovim", name="Neovim (mcphub · Avante · CodeCompanion · Copilot.lua)", family=ClientFamily.EDITOR_PLUGIN,
        capabilities=[ClientCapability.MCP, ClientCapability.PROXY], format=ConfigFormat.JSON,
        config_path=lambda: _xdg_config() / "mcphub" / "servers.json",
        detect=lambda _p: (_xdg_config() / "nvim").exists() or _on_path("nvim"),
        render=_r_mcp_servers, is_configured=_json_has(["mcpServers", SERVER_KEY]),
        details="mcphub.nvim servers.json (consumed by Avante & CodeCompanion); Lua snippet via export-config",
        docs_url="https://github.com/ravitemer/mcphub.nvim",
        aliases=["nvim", "avante", "codecompanion", "mcphub"],
    ),
    ClientSpec(
        id="emacs", name="Emacs (gptel · aidermacs · mcp.el)", family=ClientFamily.EDITOR_PLUGIN,
        capabilities=[ClientCapability.MCP, ClientCapability.PROXY], format=ConfigFormat.ELISP,
        config_path=lambda: _home() / ".emacs.d" / "genesis-memory.el",
        detect=lambda p: p.parent.exists() or (_xdg_config() / "emacs").exists() or _on_path("emacs"),
        render=_r_emacs_elisp, is_configured=_always, comment=";;", owns_file=True,
        details="Self-contained .el: mcp-hub server + gptel OpenAI-compatible backend (load-file it from init.el)",
        docs_url="https://github.com/lizqwerscott/mcp.el", aliases=["gptel", "aidermacs"],
    ),
    ClientSpec(
        id="aider", name="Aider", family=ClientFamily.CLI,
        capabilities=[ClientCapability.PROXY], format=ConfigFormat.YAML,
        config_path=lambda: _home() / ".aider.conf.yml",
        detect=lambda p: p.exists() or _on_path("aider"),
        render=_r_aider_yaml, is_configured=_marker_present,
        details="openai-api-base -> GENESIS proxy (marker block in ~/.aider.conf.yml)",
        docs_url="https://aider.chat/docs/config/aider_conf.html",
    ),
    ClientSpec(
        id="codex_cli", name="OpenAI Codex CLI", family=ClientFamily.CLI,
        capabilities=[ClientCapability.MCP, ClientCapability.PROXY], format=ConfigFormat.TOML,
        config_path=lambda: _home() / ".codex" / "config.toml",
        detect=lambda p: p.parent.exists() or _on_path("codex"),
        render=_r_codex_toml, is_configured=_marker_present,
        details="[mcp_servers.genesis-memory] TOML table (marker block)",
        docs_url="https://github.com/openai/codex", aliases=["codex"],
    ),
    ClientSpec(
        id="gemini_cli", name="Gemini CLI", family=ClientFamily.CLI,
        capabilities=[ClientCapability.MCP], format=ConfigFormat.JSON,
        config_path=lambda: _home() / ".gemini" / "settings.json",
        detect=lambda p: p.parent.exists() or _on_path("gemini"),
        render=_r_mcp_servers, is_configured=_json_has(["mcpServers", SERVER_KEY]),
        details="mcpServers in ~/.gemini/settings.json",
        docs_url="https://github.com/google-gemini/gemini-cli/blob/main/docs/tools/mcp-server.md",
        aliases=["gemini"],
    ),
    ClientSpec(
        id="amazon_q", name="Amazon Q Developer CLI", family=ClientFamily.CLI,
        capabilities=[ClientCapability.MCP], format=ConfigFormat.JSON,
        config_path=lambda: _home() / ".aws" / "amazonq" / "mcp.json",
        detect=lambda p: p.parent.exists() or _on_path("q"),
        render=_r_mcp_servers, is_configured=_json_has(["mcpServers", SERVER_KEY]),
        details="Global MCP config (~/.aws/amazonq/mcp.json)",
        docs_url="https://docs.aws.amazon.com/amazonq/latest/qdeveloper-ug/command-line-mcp.html",
        aliases=["amazonq", "q"],
    ),
    ClientSpec(
        id="goose", name="Goose (Block)", family=ClientFamily.CLI,
        capabilities=[ClientCapability.MCP, ClientCapability.PROXY], format=ConfigFormat.YAML,
        config_path=lambda: _xdg_config() / "goose" / "config.yaml",
        detect=lambda p: p.parent.exists() or _on_path("goose"),
        render=_r_goose_yaml, is_configured=_marker_present,
        details="extensions.genesis-memory stdio block (marker block in config.yaml)",
        docs_url="https://block.github.io/goose/docs/getting-started/using-extensions",
    ),
    ClientSpec(
        id="sdk_gateway",
        name="Any SDK / Agent Framework (LangChain · LlamaIndex · CrewAI · AutoGen · OpenAI · Anthropic)",
        family=ClientFamily.FRAMEWORK,
        capabilities=[ClientCapability.PROXY], format=ConfigFormat.ENV,
        config_path=lambda: _home() / ".genesis" / "genesis.env",
        detect=_frameworks_detect, render=_r_env, is_configured=_always, owns_file=True,
        details="Drop-in gateway: OPENAI_BASE_URL / ANTHROPIC_BASE_URL -> 127.0.0.1:8000. Zero code changes.",
        docs_url="https://platform.openai.com/docs/libraries",
        aliases=["langchain", "llamaindex", "crewai", "autogen", "openai", "anthropic", "sdk", "frameworks"],
    ),
]

_SPEC_BY_ID: Dict[str, ClientSpec] = {}
for _spec in CLIENT_SPECS:
    _SPEC_BY_ID[_spec.id] = _spec
    for _alias in _spec.aliases:
        _SPEC_BY_ID.setdefault(_alias, _spec)


class ClientRegistry:
    """Registry managing external AI client definitions, discovery, and multi-file patching."""

    @staticmethod
    def _normalize_path_str(p: Path) -> str:
        return str(p).replace("\\", "/")

    # Legacy path getters (kept for callers/tests) ----------------------------
    @classmethod
    def get_opencode_config_path(cls) -> Path:
        return _SPEC_BY_ID["opencode"].config_path()

    @classmethod
    def get_claude_desktop_config_path(cls) -> Path:
        return _claude_desktop_path()

    @classmethod
    def get_claude_code_config_path(cls) -> Path:
        return _SPEC_BY_ID["claude_code"].config_path()

    @classmethod
    def get_cursor_config_path(cls) -> Path:
        return _SPEC_BY_ID["cursor"].config_path()

    @classmethod
    def get_cline_config_path(cls) -> Path:
        return _SPEC_BY_ID["vscode_cline"].config_path()

    @classmethod
    def get_antigravity_config_path(cls) -> Path:
        return _SPEC_BY_ID["antigravity"].config_path()

    @classmethod
    def get_zed_config_path(cls) -> Path:
        return _zed_path()

    @classmethod
    def get_windsurf_config_path(cls) -> Path:
        return _SPEC_BY_ID["windsurf"].config_path()

    # Registry API -----------------------------------------------------------
    @classmethod
    def specs(cls) -> List[ClientSpec]:
        return list(CLIENT_SPECS)

    @classmethod
    def spec(cls, client_id: str) -> Optional[ClientSpec]:
        return _SPEC_BY_ID.get((client_id or "").strip().lower().replace("-", "_"))

    @classmethod
    def client_ids(cls) -> List[str]:
        return [s.id for s in CLIENT_SPECS]

    @classmethod
    def discover_all(cls) -> List[DiscoveredClient]:
        """Scans host system for installed AI clients in a strictly read-only manner."""
        clients: List[DiscoveredClient] = []
        for spec in CLIENT_SPECS:
            try:
                cfg = spec.config_path()
            except Exception:
                continue
            try:
                detected = bool(spec.detect(cfg))
            except Exception:
                detected = False
            configured = False
            if spec.format == ConfigFormat.NATIVE:
                configured = True
            elif cfg.exists():
                try:
                    configured = bool(spec.is_configured(cfg.read_text(encoding="utf-8")))
                except Exception:
                    configured = False
            clients.append(DiscoveredClient(
                id=spec.id, name=spec.name, capabilities=list(spec.capabilities),
                config_path=cfg, detected=detected, configured=configured,
                details=spec.details, family=spec.family.value, format=spec.format.value,
                docs_url=spec.docs_url,
            ))
        return clients

    @classmethod
    def generate_patch(cls, client_id: str, daemon_path: Optional[Path] = None,
                       db_path: Optional[Path] = None, hook_path: Optional[Path] = None) -> Dict[str, Any]:
        """Structured patch for JSON-family clients; canonical ``mcpServers`` shape for others."""
        spec = cls.spec(client_id)
        if spec is None:
            return {}
        ctx = RenderContext.build(daemon_path, db_path, hook_path)
        rendered = spec.render(ctx)
        if isinstance(rendered, dict):
            return rendered
        if ClientCapability.MCP in spec.capabilities:
            return ctx.mcp_servers()
        return {"env": ctx.proxy_env()}

    @classmethod
    def render_config(cls, client_id: str, daemon_path: Optional[Path] = None,
                      db_path: Optional[Path] = None, hook_path: Optional[Path] = None) -> str:
        """The exact text GENESIS writes for the client, in the client's native dialect."""
        spec = cls.spec(client_id)
        if spec is None:
            raise KeyError(f"unknown client: {client_id}")
        ctx = RenderContext.build(daemon_path, db_path, hook_path)
        rendered = spec.render(ctx)
        if isinstance(rendered, dict):
            return cf.to_json(rendered)
        return rendered if rendered.endswith("\n") else rendered + "\n"

    @classmethod
    def wire_client(cls, client: DiscoveredClient, daemon_path: Optional[Path] = None,
                    db_path: Optional[Path] = None, hook_path: Optional[Path] = None) -> Tuple[bool, str]:
        """Safely applies the patch to the client's config (structural merge or marker block)."""
        spec = cls.spec(client.id)
        if spec is None:
            return False, f"No patch definition found for {client.id}"
        if spec.format == ConfigFormat.NATIVE:
            return True, f"{spec.name} has native built-in integration (nothing to write)"

        ctx = RenderContext.build(daemon_path, db_path, hook_path)
        rendered = spec.render(ctx)
        target = client.config_path
        target.parent.mkdir(parents=True, exist_ok=True)
        existing = target.read_text(encoding="utf-8") if target.exists() else ""

        if isinstance(rendered, dict):
            new_content = merge_jsonc_file_content(existing, rendered)
        elif spec.owns_file:
            new_content = rendered
        else:
            new_content = cf.merge_marker_block(existing, rendered, spec.comment)
        target.write_text(new_content, encoding="utf-8")

        if spec.aux_wire is not None:
            spec.aux_wire(spec, ctx)
        return True, f"Successfully wired {spec.name} ({client.capability_tags})"

    @classmethod
    def unwire_client(cls, client: DiscoveredClient) -> Tuple[bool, str]:
        """Removes GENESIS' marker block from text configs; JSON clients revert via the
        timestamped backup manifest (``genesis setup --revert``)."""
        spec = cls.spec(client.id)
        if spec is None or not client.config_path.exists():
            return False, "nothing to remove"
        if spec.owns_file:
            client.config_path.unlink()
            return True, f"Removed {client.config_path}"
        if spec.format in (ConfigFormat.JSON, ConfigFormat.JSONC, ConfigFormat.NATIVE):
            return False, "structured config: use `genesis setup --revert`"
        text = client.config_path.read_text(encoding="utf-8")
        client.config_path.write_text(cf.strip_marker_block(text, spec.comment), encoding="utf-8")
        return True, f"Removed GENESIS block from {client.config_path}"
