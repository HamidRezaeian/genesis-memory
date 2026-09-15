"""The MCP spec doc must never drift from the daemon's TOOLS again."""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from scripts.gen_mcp_spec import render  # noqa: E402


def test_mcp_spec_matches_code():
    doc = (REPO / "docs" / "MCP_SPEC.md").read_text(encoding="utf-8")
    assert doc == render(), (
        "docs/MCP_SPEC.md is stale — regenerate with: python scripts/gen_mcp_spec.py"
    )


def test_all_tools_present_with_real_params():
    doc = (REPO / "docs" / "MCP_SPEC.md").read_text(encoding="utf-8")
    for name in ("remember", "recall", "thread_update", "cross_client_resolve",
                 "synthesize_skill", "challenge_rule", "genesis"):
        assert f"`{name}`" in doc
    # stale param names from the hand-maintained era must be gone
    assert "`content` (str)" not in doc
    assert "`engram_id_a`" not in doc
