"""Twin-launch guards: public tree stays community-only.

- The extension loader must be fail-open (empty env, broken entry points).
- The `genesis_pro` private package must NEVER leak into the public repo:
  no `genesis_memory/pro/` namespace and no hard imports of it. The ONLY
  bridge is genesis_memory/extensions.py (entry-point group name).
"""
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SELF = Path(__file__).name


def test_load_extensions_empty_is_clean():
    from genesis_memory.extensions import load_extensions, GROUP
    assert GROUP == "genesis_extensions"
    assert isinstance(load_extensions(), dict)


def test_load_extensions_skips_broken_entry_points(monkeypatch):
    import genesis_memory.extensions as ext

    class Boom:
        name = "broken-pro"
        def load(self):
            raise RuntimeError("nope")

    class FakeEps:
        def select(self, group=None):
            return [Boom()]

    import importlib.metadata as md
    monkeypatch.setattr(md, "entry_points", lambda: FakeEps())
    assert ext.load_extensions() == {}


def test_no_pro_namespace_in_public_tree():
    """genesis_memory/pro/ must never exist here — Pro lives in the private repo."""
    assert not (REPO / "genesis_memory" / "pro").exists()


def test_no_hard_import_of_private_package():
    """Only extensions.py may name the entry-point bridge; nothing imports it."""
    hits = []
    for path in list((REPO / "genesis_memory").rglob("*.py")) + list((REPO / "tests").rglob("*.py")):
        if path.name == SELF or path.name == "extensions.py":
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if "genesis_pro" in text or "genesis-memory-pro" in text:
            hits.append(str(path.relative_to(REPO)))
    assert hits == [], f"private-package references leak in public tree: {hits}"


def test_extensions_subcommand_lists_empty_channel(capsys):
    from genesis_memory.cli.run import main as cli_main
    assert cli_main(["extensions"]) == 0
    out = capsys.readouterr().out
    assert "No commercial extensions installed" in out
