"""Universal native-skill sync across AI clients (file-level, zero tokens).

Every major client converged on the same open format: one folder per skill
with a ``SKILL.md`` (YAML frontmatter: ``name`` + ``description``). This
module syncs those folders across client skill directories so a skill
installed for one client becomes visible to all others.

Design rules (no hardcoding, no token overhead):
- Locations are DATA rows (``{"id", "path", "writable", "scans"}``), never
  branched on. ``id`` is an opaque label; ``scans`` lists row ids whose
  contents this location's client already reads natively (transitive
  coverage, so nothing is linked twice). Defaults derive ONLY from ``~``
  (portable); full override via ``GENESIS_SKILLS_SOURCES_JSON``; extra dirs
  via ``GENESIS_SKILLS_DIRS`` (pathsep-separated). No absolute machine paths
  anywhere in code.
- Token cost is exactly zero: pure filesystem ops. Nothing here touches the
  subconscious hook, the proxy, or any prompt. Clients discover synced skills
  through their own native ``skill`` tool.
- Dry-run by default: prints the plan (union, links to create, conflicts,
  warnings) and writes nothing. ``--apply`` executes. Nothing is ever
  deleted; conflicts keep first-seen and are reported (``--prefer <row-id>``
  picks the winner).
- Fully reversible: ``--revert`` removes exactly what ``--apply`` created
  (manifest-driven: recorded links, hash-verified copies, created dirs if
  empty, canonical store). User files are never touched — edited copies
  are kept and reported.
- Dedup is content-based (sha256 of ``SKILL.md``): identical content under
  one name collapses to a single canonical copy; same name with different
  content is a conflict, never silently merged.
"""

import hashlib
import json
import os
import re
import shutil
import sys

MANIFEST_NAME = ".manifest.json"
SKILL_FILE = "SKILL.md"

# Generic frontmatter scalar: strictest common denominator across clients
# (frontmatter ``name`` must match its folder; description required).
_NAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
_FRONTMATTER_KEY_RE = re.compile(r"^([A-Za-z_][\w-]*):\s*(.*?)\s*$")


def _home():
    return os.path.expanduser("~")


def default_rows():
    """Default location rows. Full override via GENESIS_SKILLS_SOURCES_JSON.

    Every default path joins from ``~`` — portable, no machine paths.
    ``scans`` = coverage edges: row ids this location's client already reads
    natively, so the engine links nothing twice (transitive closure).
    """
    try:
        override = json.loads(os.environ.get("GENESIS_SKILLS_SOURCES_JSON", "") or "null")
    except Exception:
        override = None
    if isinstance(override, list):
        rows = []
        for i, row in enumerate(override):
            if not isinstance(row, dict) or not row.get("path"):
                continue
            rows.append({
                "id": str(row.get("id") or "row-%d" % i),
                "path": os.path.expandvars(os.path.expanduser(str(row["path"]))),
                "writable": bool(row.get("writable", True)),
                "scans": [str(s) for s in (row.get("scans") or [])],
            })
        return rows
    rows = [
        {"id": "claude-skills", "path": os.path.join(_home(), ".claude", "skills"),
         "writable": True, "scans": []},
        {"id": "gemini-skills", "path": os.path.join(_home(), ".gemini", "config", "skills"),
         "writable": True, "scans": []},
        {"id": "agents-skills", "path": os.path.join(_home(), ".agents", "skills"),
         "writable": True, "scans": []},
        # This client's loader already scans the claude + agents rows above,
        # so it needs zero links once they hold the union (data, not logic).
        {"id": "opencode-skills", "path": os.path.join(_home(), ".config", "opencode", "skills"),
         "writable": True, "scans": ["claude-skills", "agents-skills"]},
        # Verified against official docs (agentskills.io standard): Cursor
        # natively scans the agents + claude + codex hubs below.
        {"id": "cursor-skills", "path": os.path.join(_home(), ".cursor", "skills"),
         "writable": True, "scans": ["agents-skills", "claude-skills", "codex-skills"]},
        # Verified: VS Code Copilot natively scans the claude + agents hubs.
        {"id": "copilot-skills", "path": os.path.join(_home(), ".copilot", "skills"),
         "writable": True, "scans": ["claude-skills", "agents-skills"]},
        # Codex hub reading is UNVERIFIED — no scans edge claimed. A missing
        # edge only risks one redundant identical link, never a missed skill.
        {"id": "codex-skills", "path": os.path.join(_home(), ".codex", "skills"),
         "writable": True, "scans": []},
    ]
    extra = os.environ.get("GENESIS_SKILLS_DIRS", "")
    for i, raw in enumerate(extra.split(os.pathsep) if extra else []):
        path = os.path.expandvars(os.path.expanduser(raw.strip()))
        if path:
            rows.append({"id": "extra-%d" % i, "path": path,
                         "writable": True, "scans": []})
    return rows


def canonical_dir():
    """Single source of truth for deduped skill folders."""
    base = os.environ.get("GENESIS_SKILLS_CANONICAL", "")
    if base.strip():
        return os.path.expandvars(os.path.expanduser(base.strip()))
    return os.path.join(_home(), ".genesis", "skills")


def parse_frontmatter(skill_md_path):
    """Minimal frontmatter reader (stdlib): top-level ``key: value`` scalars.

    Handles one folded-scalar level (``description: >`` + indented lines).
    Returns dict of raw string values (may be empty).
    """
    try:
        with open(skill_md_path, "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.read().splitlines()
    except OSError:
        return {}
    if not lines or lines[0].strip() != "---":
        return {}
    out = {}
    i = 1
    while i < len(lines):
        line = lines[i]
        if line.strip() == "---":
            break
        m = _FRONTMATTER_KEY_RE.match(line)
        if m:
            key, val = m.group(1), m.group(2).strip().strip("'\"")
            if val in (">", "|", ">-", "|-", ""):
                # Folded block: consume following indented lines.
                buf = []
                j = i + 1
                while j < len(lines) and (lines[j].startswith((" ", "\t")) or not lines[j].strip()):
                    if lines[j].strip():
                        buf.append(lines[j].strip())
                    j += 1
                if buf:
                    val = " ".join(buf)
                i = j
                out[key] = val
                continue
            out[key] = val
        i += 1
    return out


def sha256_file(path):
    h = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
    except OSError:
        return ""
    return h.hexdigest()


def _skill_folders(row_path):
    """Yield (folder_name, folder_path) for ``*/SKILL.md`` under row_path."""
    try:
        entries = sorted(os.listdir(row_path))
    except OSError:
        return
    for entry in entries:
        if entry.startswith("."):
            continue
        full = os.path.join(row_path, entry)
        md = os.path.join(full, SKILL_FILE)
        try:
            if os.path.isdir(full) and os.path.isfile(md):
                yield entry, full
        except OSError:
            continue


def _owner_row(rows, realpath):
    """Physical owner: row whose base is the longest prefix of realpath.

    A row can *see* a skill through a junction without owning it (e.g. a
    link back to another client's folder). Owner attribution keeps the
    plan honest about where content physically lives. Pure path math —
    no client names involved.
    """
    best, best_len = None, -1
    target = os.path.normcase(os.path.abspath(realpath))
    for row in rows:
        base = os.path.normcase(os.path.abspath(row["path"]))
        if target == base or target.startswith(base + os.sep):
            if len(base) > best_len:
                best, best_len = row["id"], len(base)
    return best


def inventory(rows):
    """Scan rows -> (found, holders, warnings).

    found: {skill_name: [occurrences]} — one per DISTINCT content
      (``name`` + sha256). Junctions, copies and our own canonical links
      collapse here instead of faking new skills.
    holders: {skill_name: {row ids}} — every row that can already see the
      skill (direct copy, inbound junction, or our materialized link).
      Drives visibility, so nothing is ever linked where it is visible.
    Skills without a frontmatter ``name`` are skipped with a warning (every
    client filters those out anyway).
    """
    found, holders, warnings = {}, {}, []
    seen_content = set()  # (name, sha256) already recorded
    for row in rows:
        base = row["path"]
        if not os.path.isdir(base):
            continue
        for folder, full in _skill_folders(base):
            md = os.path.join(full, SKILL_FILE)
            fm = parse_frontmatter(md)
            name = (fm.get("name") or "").strip()
            if not name:
                warnings.append({
                    "row": row["id"], "folder": folder,
                    "issue": "missing frontmatter 'name' — skipped (all clients ignore it)",
                })
                continue
            sha = sha256_file(md)
            holders.setdefault(name, set()).add(row["id"])
            try:
                owner = _owner_row(rows, os.path.realpath(full))
            except OSError:
                owner = None
            if owner:
                holders[name].add(owner)
            if (name, sha) in seen_content:
                continue
            seen_content.add((name, sha))
            try:
                extra = sorted(
                    os.path.relpath(os.path.join(dp, f), full)
                    for dp, _, fns in os.walk(full) for f in fns
                    if not (dp == full and f == SKILL_FILE))
            except OSError:
                extra = []
            found.setdefault(name, []).append({
                "row": row["id"], "owner": owner or row["id"],
                "folder": folder, "path": full,
                "sha256": sha,
                "description": (fm.get("description") or "")[:1024],
                "has_compatibility": "compatibility" in fm,
                "extra_files": extra,
            })
    return found, holders, warnings


def _closure_scans(rows):
    """Transitive coverage: row_id -> set(row_ids visible to it, incl. self)."""
    by_id = {r["id"]: r for r in rows}
    closure = {}
    for rid in by_id:
        seen, stack = {rid}, [rid]
        while stack:
            cur = stack.pop()
            for nxt in (by_id.get(cur) or {}).get("scans", []):
                if nxt in by_id and nxt not in seen:
                    seen.add(nxt)
                    stack.append(nxt)
        closure[rid] = seen
    return closure


def validate_skill(name, folder, description):
    """Strictest-common-denominator warnings (all clients agree on these)."""
    warns = []
    if not _NAME_RE.match(name):
        warns.append("name %r breaks lowercase-hyphen<=64 rule" % name)
    if name != folder:
        warns.append("folder %r != frontmatter name %r (some loaders skip it)" % (folder, name))
    if not description:
        warns.append("missing description (filtered out by loaders)")
    elif len(description) > 1024:
        warns.append("description >1024 chars (may be rejected)")
    return warns


def plan(rows, prefer=None):
    """Build the sync plan. Pure computation — zero filesystem writes.

    Returns dict: union order, per-row links to create, conflicts, warnings.
    ``prefer`` (row id) wins same-name content conflicts.
    """
    order = [r["id"] for r in rows]
    if prefer and prefer in order:
        order = [prefer] + [i for i in order if i != prefer]
    rank = {rid: n for n, rid in enumerate(order)}
    found, holders, warnings = inventory(rows)
    closure = _closure_scans(rows)

    union = {}      # name -> winning occurrence
    conflicts = []  # same name, different content
    for name in sorted(found):
        occs = sorted(found[name], key=lambda o: rank.get(o["owner"], rank.get(o["row"], 999)))
        union[name] = occs[0]
        for other in occs[1:]:
            if other["sha256"] != occs[0]["sha256"]:
                conflicts.append({
                    "name": name, "kept": occs[0]["owner"], "dropped": other["owner"],
                    "note": "same name, different content — kept first-seen (use --prefer to override)",
                })

    # Where is each union skill already visible? Holders (direct copies,
    # inbound links, owners) plus transitive scan coverage.
    visible_at = {name: set(h) for name, h in holders.items()}

    links = []  # (row_id, name) to materialize
    for row in rows:
        if not row.get("writable", True):
            continue
        visible = set()
        for rid in closure.get(row["id"], {row["id"]}):
            for name, has in visible_at.items():
                if rid in has:
                    visible.add(name)
        for name in sorted(union):
            if name not in visible:
                links.append({"row": row["id"], "name": name,
                              "from": union[name]["owner"]})

    for name, win in union.items():
        for w in validate_skill(name, win["folder"], win["description"]):
            warnings.append({"row": win["row"], "folder": win["folder"], "issue": w})

    return {"rows": rows, "union": union, "links": links,
            "conflicts": conflicts, "warnings": warnings, "found": found,
            "holders": holders}


def load_manifest(canon):
    path = os.path.join(canon, MANIFEST_NAME)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_manifest(canon, manifest):
    os.makedirs(canon, exist_ok=True)
    tmp = os.path.join(canon, MANIFEST_NAME + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2, sort_keys=True)
    os.replace(tmp, os.path.join(canon, MANIFEST_NAME))


def _rmtree(path):
    """shutil.rmtree that retries read-only files (Windows .git objects)."""
    import stat

    def _onerror(func, target, _exc):
        try:
            os.chmod(target, stat.S_IWRITE)
            func(target)
        except OSError:
            pass

    shutil.rmtree(path, onerror=_onerror)


def _link_or_copy(src, dst):
    """Symlink first (live updates), else copy. Returns method name."""
    try:
        if os.path.lexists(dst):
            return "exists"
        os.symlink(src, dst, target_is_directory=True)
        return "symlink"
    except OSError:
        pass
    try:
        if os.path.isdir(dst) and not os.path.islink(dst):
            _rmtree(dst)
        elif os.path.lexists(dst):
            os.remove(dst)
        shutil.copytree(src, dst, symlinks=False)
        return "copy"
    except OSError:
        return "failed"


def apply_sync(the_plan):
    """Execute a plan. Only ADDITIVE writes: canonical copies + client links.

    Never deletes or overwrites user files. Returns (actions, errors).
    """
    canon = canonical_dir()
    manifest = load_manifest(canon)
    actions, errors = [], []
    union = the_plan["union"]

    for name in sorted(union):
        win = union[name]
        dest = os.path.join(canon, name)
        if os.path.isdir(dest) and not os.path.islink(dest):
            pass  # canonical copy already present
        else:
            try:
                if os.path.lexists(dest):
                    if os.path.isdir(dest) and not os.path.islink(dest):
                        _rmtree(dest)
                    else:
                        os.remove(dest)
                os.makedirs(canon, exist_ok=True)
                shutil.copytree(win["path"], dest, symlinks=False)
                actions.append({"op": "canonical-copy", "name": name, "from": win["row"]})
            except OSError as exc:
                errors.append({"op": "canonical-copy", "name": name, "error": type(exc).__name__})
                continue
        entry = manifest.get(name, {})
        entry.update({"sha256": win["sha256"], "sources": sorted(
            the_plan.get("holders", {}).get(name, {o["row"] for o in the_plan["found"].get(name, [])}))})
        manifest[name] = entry

    row_path = {r["id"]: r["path"] for r in the_plan["rows"]}
    created_dirs = set(manifest.get("_created_dirs", []))
    for link in the_plan["links"]:
        src = os.path.join(canon, link["name"])
        dst = os.path.join(row_path[link["row"]], link["name"])
        try:
            if not os.path.isdir(row_path[link["row"]]):
                os.makedirs(row_path[link["row"]], exist_ok=True)
                created_dirs.add(row_path[link["row"]])
        except OSError as exc:
            errors.append({"op": "mkdir", "row": link["row"], "error": type(exc).__name__})
            continue
        if os.path.lexists(dst):
            continue  # user file already there: never touch
        method = _link_or_copy(src, dst)
        if method == "failed":
            errors.append({"op": "link", "row": link["row"], "name": link["name"],
                           "error": "link-failed"})
        else:
            actions.append({"op": "link-" + method, "row": link["row"], "name": link["name"]})
            manifest.setdefault(link["name"], {}).setdefault("linked", {})[link["row"]] = method

    # Drift: canonical copy no longer matches a previously-recorded source.
    drift = []
    for name, entry in manifest.items():
        if name.startswith("_") or name not in union:
            continue
        if entry.get("sha256") and union[name]["sha256"] != entry["sha256"]:
            drift.append({"name": name, "note": "source changed since last sync — canonical kept"})
    manifest["_created_dirs"] = sorted(created_dirs)
    save_manifest(canon, manifest)
    return actions, errors, drift


def apply_revert():
    """Undo everything --apply created, driven ONLY by the manifest.

    Safety rules (user files are never touched):
    - Links (symlink/junction) are removed only if they resolve inside the
      canonical store (provably ours).
    - Recorded copies are removed only if their SKILL.md hash still matches
      the manifest (a user-edited copy is kept and reported).
    - Anything not recorded in the manifest is ignored entirely.
    - Dirs are removed with rmdir (empty-only); canonical content is removed
      entry-by-entry so foreign files a user dropped in there survive.
    Returns (removed, kept, errors). Read-only except for our own artifacts.
    """
    canon = canonical_dir()
    manifest = load_manifest(canon)
    removed, kept, errors = [], [], []
    if not manifest:
        return removed, kept, errors

    canon_real = os.path.realpath(canon)
    for name, entry in sorted(manifest.items()):
        if not isinstance(entry, dict):
            continue
        for row_id, method in (entry.get("linked") or {}).items():
            dst = None
            for r in default_rows():
                if r["id"] == row_id:
                    dst = os.path.join(r["path"], name)
                    break
            if not dst or not os.path.lexists(dst):
                continue
            try:
                if os.path.islink(dst) and os.path.realpath(dst).startswith(canon_real + os.sep):
                    os.remove(dst)
                    removed.append({"op": "unlink", "row": row_id, "name": name})
                elif method == "copy" and os.path.isdir(dst) and not os.path.islink(dst):
                    if sha256_file(os.path.join(dst, SKILL_FILE)) == entry.get("sha256"):
                        _rmtree(dst)
                        removed.append({"op": "remove-copy", "row": row_id, "name": name})
                    else:
                        kept.append({"row": row_id, "name": name,
                                     "note": "modified after sync — kept"})
                else:
                    kept.append({"row": row_id, "name": name,
                                 "note": "not ours (unrecorded target) — kept"})
            except OSError as exc:
                errors.append({"op": "revert", "row": row_id, "name": name,
                               "error": type(exc).__name__})

    # Our created dirs, empty-only. Then canonical entries we know, then
    # canonical itself (rmdir: survives if the user added foreign files).
    for path in manifest.get("_created_dirs", []):
        try:
            os.rmdir(path)
            removed.append({"op": "rmdir", "path": path})
        except OSError:
            kept.append({"path": path, "note": "not empty — kept"})
    for name, entry in sorted(manifest.items()):
        if not isinstance(entry, dict):
            continue
        entry_dir = os.path.join(canon, name)
        try:
            if os.path.islink(entry_dir):
                os.remove(entry_dir)
                removed.append({"op": "unlink-canonical", "name": name})
            elif os.path.isdir(entry_dir):
                _rmtree(entry_dir)
                removed.append({"op": "remove-canonical", "name": name})
        except OSError as exc:
            errors.append({"op": "revert-canonical", "name": name,
                           "error": type(exc).__name__})
    for leftover in (MANIFEST_NAME, MANIFEST_NAME + ".tmp"):
        try:
            os.remove(os.path.join(canon, leftover))
        except OSError:
            pass
    try:
        os.rmdir(canon)
        removed.append({"op": "rmdir-canonical", "path": canon})
    except OSError:
        pass
    return removed, kept, errors


def format_plan(the_plan, as_json=False):
    if as_json:
        slim = {"union": sorted(the_plan["union"]),
                "links": the_plan["links"],
                "conflicts": the_plan["conflicts"],
                "warnings": the_plan["warnings"]}
        return json.dumps(slim, ensure_ascii=False, indent=2)
    lines = []
    union = the_plan["union"]
    lines.append("[genesis] skill-sync plan: %d unique skills across %d locations" % (
        len(union), len(the_plan["rows"])))
    for row in the_plan["rows"]:
        status = "ok" if os.path.isdir(row["path"]) else "missing"
        lines.append("  location %-14s %s [%s]" % (row["id"], row["path"], status))
    lines.append("  union: %s" % (", ".join(sorted(union)) or "(empty)"))
    if the_plan["links"]:
        lines.append("  links to create (%d):" % len(the_plan["links"]))
        for link in the_plan["links"]:
            lines.append("    %-14s <- %s (from %s)" % (link["row"], link["name"], link["from"]))
    else:
        lines.append("  links to create: none (every location already sees the union)")
    for c in the_plan["conflicts"]:
        lines.append("  CONFLICT %s: kept %s, skipped %s — %s" % (
            c["name"], c["kept"], c["dropped"], c["note"]))
    for w in the_plan["warnings"]:
        lines.append("  warn [%s/%s]: %s" % (w["row"], w["folder"], w["issue"]))
    return "\n".join(lines)


def sync_status():
    """Read-only drift counts for doctor/setup reminders.

    Returns {"union", "pending", "conflicts", "locations"} or None on any
    error. Never writes, never raises — safe to call from diagnostics.
    """
    try:
        the_plan = plan(default_rows())
    except Exception:
        return None
    return {"union": len(the_plan["union"]), "pending": len(the_plan["links"]),
            "conflicts": len(the_plan["conflicts"]),
            "locations": len(the_plan["rows"])}


def main(argv=None):
    args = list(argv) if argv is not None else sys.argv[1:]
    apply = "--apply" in args
    as_json = "--json" in args
    prefer = None
    for i, a in enumerate(args):
        if a == "--prefer" and i + 1 < len(args):
            prefer = args[i + 1]
        elif a.startswith("--prefer="):
            prefer = a.split("=", 1)[1]
    if "--help" in args or "-h" in args:
        print("Usage: genesis skill-sync [--apply] [--revert] [--prefer <row-id>] [--json]\n"
              "  Default is dry-run: prints the plan, writes nothing.\n"
              "  --apply executes (additive only: canonical copies + links, never deletes).\n"
              "  --revert removes everything --apply created (manifest-driven;\n"
              "    user files are never touched, edited copies are kept).\n"
              "  Env: GENESIS_SKILLS_SOURCES_JSON (full row override),\n"
              "       GENESIS_SKILLS_DIRS (extra dirs), GENESIS_SKILLS_CANONICAL.")
        return 0
    if "--revert" in args:
        removed, kept, errors = apply_revert()
        if not removed and not kept and not errors:
            print("[genesis] nothing to revert (no sync manifest found).")
            return 0
        for r in removed:
            print("  - %s %s" % (r["op"], r.get("name", r.get("path", ""))))
        for k in kept:
            print("  kept %s: %s" % (k.get("name", k.get("path", "")), k["note"]))
        for e in errors:
            print("  ERROR %s: %s" % (e.get("op"), e.get("error")), file=sys.stderr)
        return 1 if errors else 0
    rows = default_rows()
    the_plan = plan(rows, prefer=prefer)
    if not apply:
        hint = "[genesis] dry-run: nothing written. Re-run with --apply to execute."
        if as_json:
            print(format_plan(the_plan, as_json=True))
            print(hint, file=sys.stderr)
        else:
            print(format_plan(the_plan))
            print(hint)
        return 0
    actions, errors, drift = apply_sync(the_plan)
    print(format_plan(the_plan, as_json=as_json))
    if actions:
        print("[genesis] applied (%d):" % len(actions))
        for a in actions:
            print("  + %s %s" % (a["op"], a.get("name", "")))
    for d in drift:
        print("  drift %s: %s" % (d["name"], d["note"]))
    for e in errors:
        print("  ERROR %s: %s" % (e.get("op"), e.get("error")), file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
