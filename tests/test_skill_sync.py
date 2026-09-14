"""Tests for universal native-skill sync (file-level, zero tokens).

All fixtures live under tmp_path + env overrides — never touches the real
home directory or any client config. Proves: inventory, content dedup,
conflict policy, scan-coverage closure, dry-run purity, apply idempotency.
"""

import json
import os

import pytest

from genesis_memory.cli import skill_sync as sync


def _skill(root, folder, name, description="Does things.", body="Do it."):
    d = os.path.join(str(root), folder)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "SKILL.md"), "w", encoding="utf-8") as fh:
        fh.write("---\nname: %s\ndescription: %s\n---\n\n%s\n" % (name, description, body))
    return d


@pytest.fixture
def env3(tmp_path, monkeypatch):
    """Three fake client dirs + isolated canonical store, JSON override only.

    No default location is used: proves paths are data, not hardcoded.
    """
    a = tmp_path / "client-a"
    b = tmp_path / "client-b"
    c = tmp_path / "client-c"
    canon = tmp_path / "canonical"
    rows = [
        {"id": "a", "path": str(a), "writable": True, "scans": []},
        {"id": "b", "path": str(b), "writable": True, "scans": []},
        # c natively scans a: needs zero links for a's skills.
        {"id": "c", "path": str(c), "writable": True, "scans": ["a"]},
    ]
    monkeypatch.setenv("GENESIS_SKILLS_SOURCES_JSON", json.dumps(rows))
    monkeypatch.setenv("GENESIS_SKILLS_CANONICAL", str(canon))
    monkeypatch.delenv("GENESIS_SKILLS_DIRS", raising=False)
    assert sync.default_rows() == rows  # override honored verbatim
    return a, b, c, canon


def test_inventory_and_union(env3):
    a, b, c, _canon = env3
    _skill(a, "alpha", "alpha")
    _skill(b, "beta", "beta")
    # Identical content, one name -> single union entry, no conflict.
    _skill(a, "shared", "shared", body="Same.")
    _skill(b, "shared", "shared", body="Same.")
    the_plan = sync.plan(sync.default_rows())
    assert sorted(the_plan["union"]) == ["alpha", "beta", "shared"]
    assert the_plan["conflicts"] == []
    # b needs alpha; a needs beta; c sees a natively, needs beta+shared.
    want = {(l["row"], l["name"]) for l in the_plan["links"]}
    assert ("b", "alpha") in want
    assert ("a", "beta") in want
    assert ("c", "alpha") not in want  # covered via scans
    assert ("c", "beta") in want


def test_conflict_keeps_first_prefer_overrides(env3):
    a, b, _c, _canon = env3
    _skill(a, "dupe", "dupe", body="Version A.")
    _skill(b, "dupe", "dupe", body="Version B.")
    the_plan = sync.plan(sync.default_rows())
    assert len(the_plan["conflicts"]) == 1
    assert the_plan["conflicts"][0]["kept"] == "a"
    assert the_plan["union"]["dupe"]["path"].startswith(str(a))
    flipped = sync.plan(sync.default_rows(), prefer="b")
    assert flipped["conflicts"][0]["kept"] == "b"
    assert flipped["union"]["dupe"]["path"].startswith(str(b))


def test_nameless_skill_skipped_with_warning(env3):
    a, _b, _c, _canon = env3
    d = os.path.join(str(a), "noname")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "SKILL.md"), "w", encoding="utf-8") as fh:
        fh.write("No frontmatter here.\n")
    the_plan = sync.plan(sync.default_rows())
    assert "noname" not in the_plan["union"]
    assert any("missing frontmatter" in w["issue"] for w in the_plan["warnings"])


def test_dry_run_writes_nothing(env3):
    a, b, _c, canon = env3
    _skill(a, "alpha", "alpha")
    the_plan = sync.plan(sync.default_rows())
    assert the_plan["links"]  # there IS work to do...
    assert not os.path.exists(str(canon))  # ...but nothing was written
    assert not os.path.exists(os.path.join(str(b), "alpha"))


def test_apply_is_additive_and_idempotent(env3):
    a, b, c, canon = env3
    _skill(a, "alpha", "alpha")
    _skill(b, "beta", "beta")
    the_plan = sync.plan(sync.default_rows())
    actions, errors, _drift = sync.apply_sync(the_plan)
    assert errors == []
    assert os.path.isfile(os.path.join(str(canon), "alpha", "SKILL.md"))
    assert os.path.isfile(os.path.join(str(canon), "beta", "SKILL.md"))
    assert os.path.exists(os.path.join(str(b), "alpha"))  # link or copy
    assert os.path.exists(os.path.join(str(c), "beta"))
    assert os.path.isfile(os.path.join(str(canon), ".manifest.json"))
    # Originals untouched: still real content, still inventoried.
    with open(os.path.join(str(a), "alpha", "SKILL.md"), encoding="utf-8") as fh:
        assert "name: alpha" in fh.read()
    # Second plan: zero links (materialized entries resolve to canonical
    # or dedup by identical hash).
    again = sync.plan(sync.default_rows())
    assert again["links"] == []


def test_validation_warnings(env3):
    a, _b, _c, _canon = env3
    _skill(a, "odd-folder", "proper-name")
    the_plan = sync.plan(sync.default_rows())
    assert any("!= frontmatter name" in w["issue"] for w in the_plan["warnings"])


def test_inbound_link_counts_as_visible(env3):
    """A row linking to another row's folder sees the skill (no phantom link)."""
    a, b, _c, _canon = env3
    src = _skill(a, "alpha", "alpha")
    try:
        os.symlink(src, os.path.join(str(b), "alpha"), target_is_directory=True)
    except OSError:
        pytest.skip("symlinks need privileges on this host")
    the_plan = sync.plan(sync.default_rows())
    assert ("b", "alpha") not in {(l["row"], l["name"]) for l in the_plan["links"]}
    # ...and the physical owner is attributed honestly.
    assert the_plan["union"]["alpha"]["owner"] == "a"


def test_frontmatter_parser():
    assert sync.parse_frontmatter("/nonexistent/SKILL.md") == {}
    assert sync.parse_frontmatter.__doc__  # documented contract
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as fh:
        fh.write("---\nname: demo\ndescription: >\n  folded line one\n  folded line two\n---\n\nBody.\n")
        path = fh.name
    try:
        fm = sync.parse_frontmatter(path)
        assert fm["name"] == "demo"
        assert fm["description"] == "folded line one folded line two"
    finally:
        os.remove(path)


def test_cli_help_and_json(env3, capsys):
    a, _b, _c, _canon = env3
    _skill(a, "alpha", "alpha")
    assert sync.main(["--help"]) == 0
    assert sync.main([]) == 0  # dry-run default
    assert "dry-run: nothing written" in capsys.readouterr().out
    # --json prints exactly one JSON document.
    assert sync.main(["--json"]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["union"] == ["alpha"]


def test_sync_status_helper(env3):
    a, b, _c, _canon = env3
    _skill(a, "alpha", "alpha")
    st = sync.sync_status()
    assert st["union"] == 1 and st["pending"] > 0 and st["conflicts"] == 0
    assert st["locations"] == 3
    sync.apply_sync(sync.plan(sync.default_rows()))
    st2 = sync.sync_status()
    assert st2["pending"] == 0


def test_doctor_check_warns_then_ok(env3):
    from genesis_memory.cli.doctor import check_skill_sync_drift
    a, _b, _c, _canon = env3
    _skill(a, "alpha", "alpha")
    ok, status, detail = check_skill_sync_drift()
    assert ok is False  # non-vital WARN, never FAIL
    assert "pending links" in status
    assert "skill-sync --apply" in detail
    sync.apply_sync(sync.plan(sync.default_rows()))
    ok2, status2, _d2 = check_skill_sync_drift()
    assert ok2 is True
    assert "In sync" in status2


def test_doctor_check_empty_union(tmp_path, monkeypatch):
    from genesis_memory.cli.doctor import check_skill_sync_drift
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("GENESIS_SKILLS_SOURCES_JSON", json.dumps(
        [{"id": "x", "path": str(empty), "writable": True, "scans": []}]))
    monkeypatch.setenv("GENESIS_SKILLS_CANONICAL", str(tmp_path / "canon"))
    monkeypatch.delenv("GENESIS_SKILLS_DIRS", raising=False)
    ok, status, _d = check_skill_sync_drift()
    assert ok is True
    assert "No native skills" in status


def test_default_rows_cover_known_clients(monkeypatch):
    """Defaults span every SKILL.md-native client; all paths derive from ~.

    No absolute machine paths: proof against hardcoding.
    """
    monkeypatch.delenv("GENESIS_SKILLS_SOURCES_JSON", raising=False)
    monkeypatch.delenv("GENESIS_SKILLS_DIRS", raising=False)
    rows = sync.default_rows()
    by_id = {r["id"]: r for r in rows}
    assert {"claude-skills", "gemini-skills", "agents-skills", "opencode-skills",
            "cursor-skills", "copilot-skills", "codex-skills"} <= set(by_id)
    home = os.path.expanduser("~")
    for r in rows:
        assert os.path.abspath(r["path"]).startswith(os.path.abspath(home)), r
    # Documented native coverage edges (official Cursor / VS Code docs).
    assert set(by_id["cursor-skills"]["scans"]) >= {"agents-skills", "claude-skills"}
    assert set(by_id["copilot-skills"]["scans"]) >= {"claude-skills", "agents-skills"}


def test_hub_coverage_needs_no_links(tmp_path, monkeypatch):
    """A cursor-like row scanning the hubs needs zero links once hubs hold union."""
    hubs = tmp_path / "hubs"
    agent_dir = hubs / "agents"
    claude_dir = hubs / "claude"
    cursor_dir = hubs / "cursor"
    rows = [
        {"id": "agents-skills", "path": str(agent_dir), "writable": True, "scans": []},
        {"id": "claude-skills", "path": str(claude_dir), "writable": True, "scans": []},
        {"id": "cursor-skills", "path": str(cursor_dir), "writable": True,
         "scans": ["agents-skills", "claude-skills"]},
    ]
    monkeypatch.setenv("GENESIS_SKILLS_SOURCES_JSON", json.dumps(rows))
    monkeypatch.setenv("GENESIS_SKILLS_CANONICAL", str(tmp_path / "canon"))
    monkeypatch.delenv("GENESIS_SKILLS_DIRS", raising=False)
    _skill(agent_dir, "s1", "s1")
    _skill(claude_dir, "s2", "s2")
    the_plan = sync.plan(sync.default_rows())
    assert sorted(the_plan["union"]) == ["s1", "s2"]
    cursor_links = [l for l in the_plan["links"] if l["row"] == "cursor-skills"]
    assert cursor_links == []  # sees everything via hub scans
    assert {l["name"] for l in the_plan["links"]} == {"s1", "s2"}  # hubs cross-fill


def test_revert_removes_only_ours(env3):
    """apply -> revert roundtrip: our links/copies/canonical gone, user files intact."""
    a, b, _c, canon = env3
    user_dir = _skill(a, "mine", "mine", body="User content.")
    _skill(b, "theirs", "theirs")
    sync.apply_sync(sync.plan(sync.default_rows()))
    assert os.path.exists(str(canon))
    removed, kept, errors = sync.apply_revert()
    assert errors == []
    assert not os.path.exists(str(canon))  # canonical fully removed
    # Originals untouched.
    assert os.path.isfile(os.path.join(user_dir, "SKILL.md"))
    with open(os.path.join(user_dir, "SKILL.md"), encoding="utf-8") as fh:
        assert "User content." in fh.read()
    assert os.path.isdir(os.path.join(str(b), "theirs"))
    # Nothing of ours left behind in client dirs.
    assert not os.path.lexists(os.path.join(str(b), "mine"))
    assert not os.path.lexists(os.path.join(str(a), "theirs"))
    # Revert is idempotent: second run is a clean no-op.
    removed2, _k2, errors2 = sync.apply_revert()
    assert removed2 == [] and errors2 == []


def test_revert_keeps_user_edited_copy(env3, monkeypatch):
    """A recorded copy the user modified afterwards is kept, never deleted."""
    a, b, _c, canon = env3
    _skill(a, "alpha", "alpha", body="Original.")
    # Force the copy method so the test is deterministic on any OS.
    monkeypatch.setattr(sync, "_link_or_copy", lambda _s, _d: (
        __import__("shutil").copytree(_s, _d, symlinks=False), "copy")[1])
    sync.apply_sync(sync.plan(sync.default_rows()))
    dst = os.path.join(str(b), "alpha", "SKILL.md")
    assert os.path.isfile(dst)
    with open(dst, "w", encoding="utf-8") as fh:
        fh.write("---\nname: alpha\ndescription: x\n---\n\nUser edited this.\n")
    _removed, kept, errors = sync.apply_revert()
    assert errors == []
    assert os.path.isfile(dst)  # kept!
    assert any(k.get("name") == "alpha" and "kept" in k.get("note", "") for k in kept)


def test_revert_without_manifest_is_noop(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("GENESIS_SKILLS_SOURCES_JSON", json.dumps([]))
    monkeypatch.setenv("GENESIS_SKILLS_CANONICAL", str(tmp_path / "nope"))
    monkeypatch.delenv("GENESIS_SKILLS_DIRS", raising=False)
    assert sync.main(["--revert"]) == 0
    assert "nothing to revert" in capsys.readouterr().out


def test_revert_handles_readonly_files(env3):
    """Windows .git-style read-only files must not break revert."""
    import stat
    a, b, canon = env3[0], env3[1], env3[3]
    src = _skill(a, "alpha", "alpha", body="Original.")
    ro = os.path.join(src, "locked.txt")
    with open(ro, "w", encoding="utf-8") as fh:
        fh.write("locked")
    os.chmod(ro, stat.S_IREAD)
    try:
        sync.apply_sync(sync.plan(sync.default_rows()))
        removed, _kept, errors = sync.apply_revert()
        assert errors == []
        assert not os.path.exists(str(canon))
        assert not os.path.lexists(os.path.join(str(b), "alpha"))
    finally:
        os.chmod(ro, stat.S_IWRITE)
