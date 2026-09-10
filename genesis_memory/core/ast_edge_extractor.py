"""
GENESIS AST Edge Extractor & MESI Graph Invalidation (Priority 2).
Extracts deterministic code dependencies (depends_on) using stdlib ast.
Pure static analysis: zero LLM calls, zero code body storage (path+relation only).
Tied to MESI coherence: file mutation/hash drift invalidates dependent edges.
Hermetic, incremental (<1s execution), Schema v5 compliant.
"""

import argparse
import ast
import hashlib
import os
import sqlite3
import subprocess
import sys
import time


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DEFAULT_DB = os.environ.get(
    "GENESIS_DAEMON_DB",
    os.path.join(os.path.expanduser("~"), ".genesis", "memory.db")
)



def compute_file_hash(filepath):
    """Compute deterministic SHA256 hash of file content."""
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def normalize_repo_path(path, root=REPO_ROOT):
    """Convert absolute path to normalized forward-slash repository-relative path."""
    try:
        rel = os.path.relpath(path, root)
        return rel.replace("\\", "/")
    except Exception:
        return path.replace("\\", "/")


def resolve_local_target(module_name, current_file, root=REPO_ROOT):
    """
    Resolve imported module name to repository file path if local, else canonical module name.
    Strict privacy invariant: returns path string ONLY (never code bodies).
    """
    # Handle relative imports (e.g. .genesis_daemon)
    curr_dir = os.path.dirname(current_file)
    if module_name.startswith("."):
        dots = len(module_name) - len(module_name.lstrip("."))
        target_mod = module_name.lstrip(".")
        target_dir = curr_dir
        for _ in range(dots - 1):
            target_dir = os.path.dirname(target_dir)
        candidate = os.path.join(target_dir, f"{target_mod.replace('.', '/')}.py")
        if os.path.isfile(candidate):
            return normalize_repo_path(candidate, root)
        candidate_init = os.path.join(target_dir, target_mod.replace('.', '/'), "__init__.py")
        if os.path.isfile(candidate_init):
            return normalize_repo_path(candidate_init, root)

    # Handle absolute imports within the package source tree
    parts = module_name.split(".")
    candidate_src = os.path.join(root, "genesis_memory", *parts) + ".py"
    if os.path.isfile(candidate_src):
        return normalize_repo_path(candidate_src, root)
    candidate_src_init = os.path.join(root, "genesis_memory", *parts, "__init__.py")
    if os.path.isfile(candidate_src_init):
        return normalize_repo_path(candidate_src_init, root)

    # External / stdlib dependency
    return module_name


def extract_ast_imports(filepath, root=REPO_ROOT):
    """
    Parse Python source via stdlib ast and extract module-level imports.
    Returns list of target identifiers/paths.
    """
    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            tree = ast.parse(f.read(), filename=filepath)
    except Exception:
        return []

    targets = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                resolved = resolve_local_target(alias.name, filepath, root)
                targets.add(resolved)
        elif isinstance(node, ast.ImportFrom):
            mod_prefix = "." * node.level if node.level > 0 else ""
            mod_name = f"{mod_prefix}{node.module or ''}"
            resolved = resolve_local_target(mod_name, filepath, root)
            targets.add(resolved)

    return sorted(targets)


def sync_file_edges(db, filepath, root=REPO_ROOT):
    """
    Synchronize AST edges for a single Python file into edges table.
    Applies MESI invalidation if file hash has changed.
    """
    if not os.path.isfile(filepath) or not filepath.endswith(".py"):
        return {"status": "skipped", "extracted": 0, "invalidated": 0}

    source_path = normalize_repo_path(filepath, root)
    current_hash = compute_file_hash(filepath)
    now = time.time()

    cur = db.cursor()
    # Check if active edges already exist with identical file hash
    existing = cur.execute(
        "SELECT target, file_hash FROM edges WHERE source = ? AND (status = 'active' OR status IS NULL)",
        (source_path,)
    ).fetchall()

    if existing and all(r[1] == current_hash for r in existing):
        return {"status": "unchanged", "source": source_path, "extracted": 0, "invalidated": 0}

    # Hash changed or new file: Invalidate previous active edges (MESI Coherence)
    inv_count = 0
    if existing:
        cur.execute(
            "UPDATE edges SET status = 'invalidated', updated = ? WHERE source = ? AND (status = 'active' OR status IS NULL)",
            (now, source_path)
        )
        inv_count = cur.rowcount

    # Extract new imports
    targets = extract_ast_imports(filepath, root)
    ext_count = 0
    for tgt in targets:
        cur.execute(
            "INSERT INTO edges(source, target, relation, file_hash, status, ts, updated) "
            "VALUES(?, ?, 'depends_on', ?, 'active', ?, ?) "
            "ON CONFLICT(source, target, relation) DO UPDATE SET "
            "file_hash = excluded.file_hash, status = 'active', updated = excluded.updated",
            (source_path, tgt, current_hash, now, now)
        )
        ext_count += 1

    # Update persistent counters
    if ext_count > 0:
        cur.execute(
            "INSERT INTO counters(name, count) VALUES('edges_extracted', ?) "
            "ON CONFLICT(name) DO UPDATE SET count = count + ?", (ext_count, ext_count)
        )
    if inv_count > 0:
        cur.execute(
            "INSERT INTO counters(name, count) VALUES('edges_invalidated', ?) "
            "ON CONFLICT(name) DO UPDATE SET count = count + ?", (inv_count, inv_count)
        )

    db.commit()
    return {
        "status": "synchronized",
        "source": source_path,
        "extracted": ext_count,
        "invalidated": inv_count,
        "file_hash": current_hash[:12]
    }


def get_git_diff_files(root=REPO_ROOT):
    """Retrieve list of modified/added Python files from git status and diff."""
    files = set()
    try:
        # Check staged & unstaged modified/untracked files
        res = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=root, capture_output=True, text=True, check=True
        )
        for line in res.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            # Format: XY PATH or XY "PATH"
            parts = line.split(maxsplit=1)
            if len(parts) == 2:
                path = parts[1].strip('"')
                if path.endswith(".py"):
                    full_p = os.path.join(root, path)
                    if os.path.isfile(full_p):
                        files.add(full_p)
    except Exception:
        pass
    return sorted(files)


def scan_all(db, root=REPO_ROOT, target_dir="genesis_memory"):
    """Full repository scan across target directory."""
    full_target = os.path.join(root, target_dir)
    total_extracted, total_invalidated, total_files = 0, 0, 0
    t0 = time.time()

    for dirpath, _, filenames in os.walk(full_target):
        for fn in filenames:
            if fn.endswith(".py"):
                fp = os.path.join(dirpath, fn)
                res = sync_file_edges(db, fp, root)
                total_files += 1
                total_extracted += res.get("extracted", 0)
                total_invalidated += res.get("invalidated", 0)

    elapsed = round(time.time() - t0, 3)
    return {
        "files_scanned": total_files,
        "edges_extracted": total_extracted,
        "edges_invalidated": total_invalidated,
        "elapsed_s": elapsed
    }


def install_post_commit_hook(root=REPO_ROOT):
    """Install git post-commit hook for automated incremental AST extraction."""
    hooks_dir = os.path.join(root, ".git", "hooks")
    if not os.path.isdir(hooks_dir):
        os.makedirs(hooks_dir, exist_ok=True)

    hook_path = os.path.join(hooks_dir, "post-commit")
    hook_script = f"""#!/bin/sh
# GENESIS Automated AST Dependency Extractor Hook (Priority 2)
python "{os.path.join(root, 'genesis_memory', 'core', 'ast_edge_extractor.py')}" --git-diff
"""
    with open(hook_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(hook_script)

    # On Windows/Git Bash, ensure executable if possible
    try:
        os.chmod(hook_path, 0o755)
    except Exception:
        pass

    return hook_path


def main():
    ap = argparse.ArgumentParser(description="GENESIS AST Edge Extractor & Graph Invalidation")
    ap.add_argument("--db", default=DEFAULT_DB, help="Path to genesis SQLite memory database")
    ap.add_argument("--scan-all", action="store_true", help="Perform full scan of src/ directory")
    ap.add_argument("--git-diff", action="store_true", help="Perform incremental scan on git-modified files")
    ap.add_argument("--install-hook", action="store_true", help="Install git post-commit hook")
    args = ap.parse_args()

    if args.install_hook:
        p = install_post_commit_hook(REPO_ROOT)
        print(f"[GENESIS AST Hook] Installed post-commit hook at: {p}")
        return

    # Ensure schema is migrated to v5 before extracting
    from genesis_memory.daemon.server import Store
    store = Store(args.db)
    db = store.db

    if args.scan_all:
        res = scan_all(db, REPO_ROOT, target_dir="genesis_memory")
        print(f"[GENESIS AST Extractor] Full scan complete: {res}")
    elif args.git_diff:
        files = get_git_diff_files(REPO_ROOT)
        ext, inv = 0, 0
        for f in files:
            r = sync_file_edges(db, f, REPO_ROOT)
            ext += r.get("extracted", 0)
            inv += r.get("invalidated", 0)
        print(f"[GENESIS AST Extractor] Incremental sync complete: {len(files)} files checked, {ext} extracted, {inv} invalidated")
    else:
        # Default behavior: incremental sync on modified files, fallback to scan-all if no diff
        files = get_git_diff_files(REPO_ROOT)
        if files:
            ext, inv = 0, 0
            for f in files:
                r = sync_file_edges(db, f, REPO_ROOT)
                ext += r.get("extracted", 0)
                inv += r.get("invalidated", 0)
            print(f"[GENESIS AST Extractor] Incremental sync: {len(files)} files checked, {ext} extracted, {inv} invalidated")
        else:
            res = scan_all(db, REPO_ROOT, target_dir="genesis_memory")
            print(f"[GENESIS AST Extractor] Repository scan complete: {res}")


if __name__ == "__main__":
    main()
