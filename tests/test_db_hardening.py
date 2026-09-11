"""SQLite concurrency hardening: WAL + busy_timeout + atomic transactions.

Multiple agents (daemon, proxy, hooks, sleep, dashboard) share one memory.db.
These tests hammer the store from many threads *and* processes at once and
assert that ``database is locked`` never escapes.
"""

from __future__ import annotations

import multiprocessing as mp
import sqlite3
import threading
import time
from pathlib import Path

import pytest

from genesis_memory.core import db as dbx
from genesis_memory.daemon.server import Store, handle


def test_connect_applies_concurrency_contract(tmp_path: Path):
    conn = dbx.connect(tmp_path / "a.db")
    try:
        assert dbx.journal_mode(conn) == "wal"
        assert dbx.busy_timeout_ms(conn) == 5000
        assert conn.execute("PRAGMA synchronous").fetchone()[0] == 1  # NORMAL
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    finally:
        conn.close()


def test_connect_creates_parent_directory(tmp_path: Path):
    target = tmp_path / "nested" / "deeper" / "m.db"
    conn = dbx.connect(target)
    conn.close()
    assert target.exists()


def test_readonly_connection_cannot_write(tmp_path: Path):
    path = tmp_path / "ro.db"
    w = dbx.connect(path)
    w.execute("CREATE TABLE t(x)")
    w.commit()
    w.close()
    r = dbx.connect(path, readonly=True)
    try:
        with pytest.raises(sqlite3.OperationalError):
            r.execute("INSERT INTO t VALUES (1)")
        assert dbx.busy_timeout_ms(r) == 5000
    finally:
        r.close()


def test_transaction_commits_and_rolls_back(tmp_path: Path):
    conn = dbx.connect(tmp_path / "t.db")
    conn.execute("CREATE TABLE t(x INTEGER)")
    conn.commit()
    with dbx.transaction(conn):
        conn.execute("INSERT INTO t VALUES (1)")
        conn.execute("INSERT INTO t VALUES (2)")
    assert conn.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 2
    with pytest.raises(RuntimeError):
        with dbx.transaction(conn):
            conn.execute("INSERT INTO t VALUES (3)")
            raise RuntimeError("boom")
    assert conn.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 2
    assert not conn.in_transaction
    conn.close()


def test_retry_on_lock_retries_then_succeeds(monkeypatch):
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise sqlite3.OperationalError("database is locked")
        return "ok"

    monkeypatch.setattr(dbx.time, "sleep", lambda _s: None)
    assert dbx.retry_on_lock(flaky) == "ok"
    assert calls["n"] == 3


def test_retry_on_lock_gives_up_and_ignores_other_errors(monkeypatch):
    monkeypatch.setattr(dbx.time, "sleep", lambda _s: None)

    def always_locked():
        raise sqlite3.OperationalError("database is locked")

    with pytest.raises(sqlite3.OperationalError):
        dbx.retry_on_lock(always_locked, retries=2)

    def real_fault():
        raise sqlite3.OperationalError("no such table: nope")

    with pytest.raises(sqlite3.OperationalError, match="no such table"):
        dbx.retry_on_lock(real_fault)


def test_is_lock_error_classification():
    assert dbx.is_lock_error(sqlite3.OperationalError("database is locked"))
    assert dbx.is_lock_error(sqlite3.OperationalError("database table is locked"))
    assert not dbx.is_lock_error(sqlite3.OperationalError("syntax error"))
    assert not dbx.is_lock_error(ValueError("database is locked"))


def test_store_uses_hardened_connection(tmp_path: Path):
    store = Store(str(tmp_path / "s.db"))
    try:
        assert dbx.journal_mode(store.db) == "wal"
        assert dbx.busy_timeout_ms(store.db) == 5000
    finally:
        store.db.close()


def test_threaded_writers_never_see_database_is_locked(tmp_path: Path):
    db_path = str(tmp_path / "threads.db")
    Store(db_path).db.close()  # migrate once
    errors: list = []
    n_threads, n_ops = 8, 25

    def worker(tid: int):
        try:
            store = Store(db_path)
            for i in range(n_ops):
                store.remember(f"thread {tid} note {i} about concurrency hardening", kind="fact", project=f"p{tid}")
                if i % 5 == 0:
                    store.recall("concurrency hardening", project=f"p{tid}")
            store.db.close()
        except Exception as exc:  # pragma: no cover - failure path
            errors.append(repr(exc))

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert not errors, errors
    check = dbx.connect(db_path, readonly=True)
    total = check.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
    check.close()
    assert total == n_threads * n_ops


def _proc_worker(db_path: str, pid: int, n_ops: int, q) -> None:
    try:
        store = Store(db_path)
        for i in range(n_ops):
            handle(store, {"jsonrpc": "2.0", "id": i, "method": "tools/call",
                           "params": {"name": "remember",
                                      "arguments": {"text": f"process {pid} note {i} multi-agent write storm",
                                                    "kind": "outcome", "project": "storm"}}})
            if i % 4 == 0:
                handle(store, {"jsonrpc": "2.0", "id": 1000 + i, "method": "tools/call",
                               "params": {"name": "status", "arguments": {}}})
        store.db.close()
        q.put(("ok", pid))
    except Exception as exc:  # pragma: no cover - failure path
        q.put(("err", repr(exc)))


def test_multi_process_agents_share_one_db(tmp_path: Path):
    db_path = str(tmp_path / "procs.db")
    Store(db_path).db.close()
    ctx = mp.get_context("spawn")
    q = ctx.Queue()
    n_procs, n_ops = 4, 15
    procs = [ctx.Process(target=_proc_worker, args=(db_path, p, n_ops, q)) for p in range(n_procs)]
    for p in procs:
        p.start()
    results = [q.get(timeout=90) for _ in procs]
    for p in procs:
        p.join(timeout=30)
    errs = [r for r in results if r[0] == "err"]
    assert not errs, errs
    check = dbx.connect(db_path, readonly=True)
    total = check.execute("SELECT COUNT(*) FROM episodes WHERE project='storm'").fetchone()[0]
    check.close()
    assert total == n_procs * n_ops


def test_handle_retries_transient_lock(tmp_path: Path, monkeypatch):
    store = Store(str(tmp_path / "h.db"))
    calls = {"n": 0}
    real_remember = store.remember

    def flaky_remember(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise sqlite3.OperationalError("database is locked")
        return real_remember(*a, **k)

    monkeypatch.setattr(store, "remember", flaky_remember)
    res = handle(store, {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                         "params": {"name": "remember", "arguments": {"text": "retry me please now"}}})
    assert "result" in res, res
    assert calls["n"] == 2
    store.db.close()


def test_handle_surfaces_persistent_lock_as_error(tmp_path: Path, monkeypatch):
    store = Store(str(tmp_path / "h2.db"))
    monkeypatch.setattr(dbx, "DEFAULT_RETRIES", 1)
    monkeypatch.setattr(time, "sleep", lambda _s: None)

    def always_locked(*a, **k):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(store, "status", always_locked)
    res = handle(store, {"jsonrpc": "2.0", "id": 7, "method": "tools/call",
                         "params": {"name": "status", "arguments": {}}})
    assert "error" in res and "locked" in res["error"]["message"]
    store.db.close()


def test_checkpoint_is_safe_to_call(tmp_path: Path):
    conn = dbx.connect(tmp_path / "c.db")
    conn.execute("CREATE TABLE t(x)")
    conn.commit()
    dbx.checkpoint(conn)
    dbx.checkpoint(conn, "TRUNCATE")
    with pytest.raises(ValueError):
        dbx.checkpoint(conn, "DROP TABLE")
    conn.close()
