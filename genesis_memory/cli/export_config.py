"""``genesis export-config`` / ``genesis clients`` — instant snippets for any tool.

    genesis export-config                       # canonical mcpServers JSON
    genesis export-config --format yaml         # same, as YAML
    genesis export-config --format toml         # [mcpServers.genesis-memory] tables
    genesis export-config --format env          # OPENAI_BASE_URL / ANTHROPIC_BASE_URL gateway env
    genesis export-config --client zed          # Zed's native context_servers shape
    genesis export-config --client neovim --format lua
    genesis export-config --all --out ./snippets/  # one file per client

Snippets are generated from the same :class:`ClientRegistry` specs that power
``genesis setup``, so what you copy-paste is byte-identical to what auto-wiring
would write.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from genesis_memory.cli import config_formats as cf
from genesis_memory.cli.client_registry import (
    PROXY_ANTHROPIC_URL, PROXY_URL, SERVER_KEY, ClientRegistry, ConfigFormat, RenderContext,
)

EXPORT_FORMATS = ("json", "yaml", "toml", "env", "lua", "elisp", "native")


def _lua(ctx: RenderContext) -> str:
    return (
        "-- GENESIS Memory: stdio MCP (mcphub.nvim / Avante / CodeCompanion) + proxy endpoint\n"
        "return {\n"
        "  mcpServers = {\n"
        f'    ["{SERVER_KEY}"] = {{\n'
        f'      command = "{ctx.python_bin}",\n'
        f'      args = {{ "{ctx.daemon}" }},\n'
        f'      env = {{ GENESIS_DAEMON_DB = "{ctx.db}" }},\n'
        "    },\n"
        "  },\n"
        f'  proxy = {{ endpoint = "{PROXY_URL}", api_key = "not-needed" }},\n'
        "}\n"
    )


def _python_snippet(ctx: RenderContext) -> str:
    return (
        "# Any OpenAI / Anthropic SDK or agent framework — zero code changes needed.\n"
        "# Just point the base URL at the GENESIS gateway:\n"
        f"#   export OPENAI_BASE_URL={PROXY_URL}\n"
        f"#   export ANTHROPIC_BASE_URL={PROXY_ANTHROPIC_URL}\n"
        "from openai import OpenAI\n"
        f'client = OpenAI(base_url="{PROXY_URL}", api_key="not-needed")\n'
        "# LangChain:  ChatOpenAI(base_url=..., api_key='not-needed')\n"
        "# LlamaIndex: OpenAI(api_base=...)\n"
        "# CrewAI / AutoGen: set OPENAI_BASE_URL in the environment before import\n"
    )


def build_export(client_id: Optional[str], fmt: str, ctx: RenderContext) -> str:
    """Renders the requested snippet. Raises ValueError for unknown combos."""
    fmt = (fmt or "json").lower()
    if fmt not in EXPORT_FORMATS:
        raise ValueError(f"unknown format {fmt!r}; choose from {', '.join(EXPORT_FORMATS)}")

    if client_id:
        spec = ClientRegistry.spec(client_id)
        if spec is None:
            raise ValueError(f"unknown client {client_id!r}; run `genesis clients` for the list")
        if fmt == "native":
            return ClientRegistry.render_config(spec.id, Path(ctx.daemon), Path(ctx.db), Path(ctx.hook))
        if fmt == "lua":
            return _lua(ctx)
        if fmt == "elisp":
            return ClientRegistry.render_config("emacs", Path(ctx.daemon), Path(ctx.db), Path(ctx.hook))
        if fmt == "env":
            return cf.to_env(ctx.proxy_env())
        payload = ClientRegistry.generate_patch(spec.id, Path(ctx.daemon), Path(ctx.db), Path(ctx.hook))
        if not payload:  # NATIVE clients
            payload = ctx.mcp_servers()
        return cf.render(payload, fmt)

    # No client: canonical gateway shapes
    if fmt == "env":
        return cf.to_env(ctx.proxy_env())
    if fmt == "lua":
        return _lua(ctx)
    if fmt == "elisp":
        return ClientRegistry.render_config("emacs", Path(ctx.daemon), Path(ctx.db), Path(ctx.hook))
    if fmt == "native":
        return _python_snippet(ctx)
    return cf.render(ctx.mcp_servers(), fmt)


def export_all(out_dir: Path, ctx: RenderContext) -> List[Path]:
    """Writes one native-dialect snippet per client into ``out_dir``."""
    out_dir.mkdir(parents=True, exist_ok=True)
    written: List[Path] = []
    ext = {"json": "json", "jsonc": "jsonc", "yaml": "yaml", "toml": "toml",
           "elisp": "el", "env": "env", "native": "md"}
    for spec in ClientRegistry.specs():
        if spec.format == ConfigFormat.NATIVE:
            body = f"# {spec.name}\n\n{spec.details}\n"
        else:
            body = ClientRegistry.render_config(spec.id, Path(ctx.daemon), Path(ctx.db), Path(ctx.hook))
        path = out_dir / f"{spec.id}.{ext[spec.format.value]}"
        path.write_text(body, encoding="utf-8")
        written.append(path)
    (out_dir / "gateway.env").write_text(cf.to_env(ctx.proxy_env()), encoding="utf-8")
    written.append(out_dir / "gateway.env")
    (out_dir / "neovim.lua").write_text(_lua(ctx), encoding="utf-8")
    written.append(out_dir / "neovim.lua")
    return written


def clients_matrix(as_json: bool = False) -> str:
    """Human table (or JSON) of every supported client with detection status."""
    clients = ClientRegistry.discover_all()
    if as_json:
        return json.dumps([c.to_dict() for c in clients], indent=2)
    lines = ["  Universal Client Matrix — %d AI environments" % len(clients), "  " + "-" * 92]
    fam = None
    for c in clients:
        if c.family != fam:
            fam = c.family
            lines.append(f"  [{fam}]")
        status = "configured" if c.configured else ("detected" if c.detected else "not found")
        mark = "\u2705" if c.configured else ("\u26a1" if c.detected else "\u00b7 ")
        lines.append(f"    {mark} {c.id:<16} {c.name[:44]:<44} [{c.capability_tags:<16}] {status}")
        lines.append(f"       \u2514\u2500 {c.format:<6} {c.config_path}")
    lines.append("  " + "-" * 92)
    lines.append("  Wire everything detected:  genesis setup --yes")
    lines.append("  Snippet for any tool:      genesis export-config --client <id> [--format json|yaml|toml|env|lua|native]")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="genesis export-config",
                                 description="Generate GENESIS config snippets for any AI tool")
    ap.add_argument("-f", "--format", default="json", choices=EXPORT_FORMATS,
                    help="Output dialect (default json). 'native' = the client's own file format")
    ap.add_argument("-c", "--client", default=None, help="Client id or alias (see `genesis clients`)")
    ap.add_argument("-o", "--out", default=None, help="Write to file (or directory with --all)")
    ap.add_argument("--all", action="store_true", help="Export a native snippet for every client into --out dir")
    ap.add_argument("--db", default=None, help="Override memory DB path")
    args = ap.parse_args(argv)

    ctx = RenderContext.build(db_path=Path(args.db) if args.db else None)
    try:
        if args.all:
            out_dir = Path(args.out or "./genesis-snippets")
            files = export_all(out_dir, ctx)
            print(f"[genesis] wrote {len(files)} snippets to {out_dir}")
            for f in files:
                print(f"  - {f.name}")
            return 0
        text = build_export(args.client, args.format, ctx)
    except ValueError as exc:
        print(f"[genesis] {exc}", file=sys.stderr)
        return 2
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"[genesis] wrote {args.out}")
    else:
        sys.stdout.write(text)
    return 0
