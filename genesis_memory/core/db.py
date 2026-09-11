"""Bulletproof SQLite connection layer shared by every GENESIS process.

Multiple agents (MCP daemon, proxy, sleep daemon, dashboard, hooks, CLI) read
and write the same ``~/.genesis/memory.db`` concurrently. SQLite handles this
fine *if* every writer agrees on the same discipline:

* WAL journal mode  -> readers never block writers and vice-versa.
* ``busy_timeout``   -> a writer that finds the lock held waits (5000 ms) instead
  of failing instantly with ``database is locked``.
* ``BEGIN IMMEDIATE`` -> write transactions acquire the RESERVED lock up front,
  so two writers serialize deterministically rather than deadlocking mid-txn.
* Bounded retry with jitter -> survives the rare lock storms that exceed the
  busy handler (e.g. a checkpoint racing a burst of hook writes).

Everything here is stdlib-only (RSS discipline: the daemon must stay <100 MB).
"""

from __future__ import annotations

import contextlib
import functools
import os
import sqlite3
import time
from pathlib import Path

# NOTE: no typing/random imports — loaded by the RSS-budgeted MCP daemon.

DEFAULT_BUSY_TIMEOUT_MS = 5000
DEFAULT_RETRIES = 6
_RETRY_BASE_SLEEP_S = 0.02


def _jitter() -> float:
    return int.from_bytes(os.urandom(2), "big") / 65535.0 * _RETRY_BASE_SLEEP_S


def is_lock_error(exc: BaseException) -> bool:
    """True for the SQLite errors that mean "try again", never for real faults."""
    if not isinstance(exc, sqlite3.OperationalError):
        return False
    msg = str(exc).lower()
    return "database is locked" in msg or "database table is locked" in msg or "busy" in msg


def apply_pragmas(conn: sqlite3.Connection, *, readonly: bool = False,
                  busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
                  cache_kib=None) -> None:
    """Applies the GENESIS concurrency contract to an existing connection.

    Safe to call on any connection, including ``mode=ro`` URIs (WAL switching
    is skipped there because it requires write access).
    """
    conn.execute(f"PRAGMA busy_timeout = {int(busy_timeout_ms)}")
    if not readonly:
        try:
            conn.execute("PRAGMA journal_mode = WAL")
        except sqlite3.OperationalError:
            pass  # another connection may hold an exclusive lock; WAL is persistent once set
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA temp_store = MEMORY")
    if cache_kib is not None:
        conn.execute(f"PRAGMA cache_size = -{int(cache_kib)}")


def connect(path, *, readonly: bool = False,
            busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
            cache_kib=None,
            check_same_thread: bool = True,
            row_factory=None) -> sqlite3.Connection:
    """Opens a hardened connection (WAL + busy timeout + sane pragmas).

    ``readonly=True`` opens with ``mode=ro`` so dashboards/report generators can
    never accidentally mutate the store.
    """
    spath = os.fspath(path)
    if readonly:
        uri = Path(spath).resolve().as_uri().replace("file://", "file:", 1) + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=busy_timeout_ms / 1000.0,
                               check_same_thread=check_same_thread)
    else:
        parent = Path(spath).expanduser().parent
        if str(parent) not in ("", "."):
            parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(spath, timeout=busy_timeout_ms / 1000.0,
                               check_same_thread=check_same_thread)
    if row_factory is not None:
        conn.row_factory = row_factory
    apply_pragmas(conn, readonly=readonly, busy_timeout_ms=busy_timeout_ms, cache_kib=cache_kib)
    return conn


def retry_on_lock(fn, *args, retries: int = DEFAULT_RETRIES, **kwargs):
    """Calls ``fn`` and retries with exponential backoff + jitter on lock errors."""
    attempt = 0
    while True:
        try:
            return fn(*args, **kwargs)
        except sqlite3.OperationalError as exc:
            if not is_lock_error(exc) or attempt >= retries:
                raise
            sleep_s = _RETRY_BASE_SLEEP_S * (2 ** attempt) + _jitter()
            time.sleep(min(sleep_s, 1.0))
            attempt += 1


def locked_retry(retries: int = DEFAULT_RETRIES):
    """Decorator form of :func:`retry_on_lock`."""
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            return retry_on_lock(fn, *args, retries=retries, **kwargs)
        return wrapper
    return deco


@contextlib.contextmanager
def transaction(conn: sqlite3.Connection, *, immediate: bool = True,
                retries: int = DEFAULT_RETRIES):
    """Atomic write scope: ``BEGIN IMMEDIATE`` ... ``COMMIT`` (or ``ROLLBACK``).

    Acquiring the write lock at BEGIN (rather than at the first write) means
    concurrent writers queue on the busy handler instead of hitting
    ``SQLITE_BUSY_SNAPSHOT`` half-way through. The BEGIN itself is retried;
    the body runs exactly once per successful BEGIN.
    """
    # Python's sqlite3 module may already have an implicit transaction open.
    if conn.in_transaction:
        try:
            conn.commit()
        except sqlite3.OperationalError:
            conn.rollback()
    begin_sql = "BEGIN IMMEDIATE" if immediate else "BEGIN"
    retry_on_lock(conn.execute, begin_sql, retries=retries)
    try:
        yield conn
    except BaseException:
        with contextlib.suppress(sqlite3.Error):
            conn.rollback()
        raise
    else:
        retry_on_lock(conn.commit, retries=retries)


def journal_mode(conn: sqlite3.Connection) -> str:
    return str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower()


def busy_timeout_ms(conn: sqlite3.Connection) -> int:
    return int(conn.execute("PRAGMA busy_timeout").fetchone()[0])


def checkpoint(conn: sqlite3.Connection, mode: str = "PASSIVE") -> None:
    """Folds the WAL back into the main file without blocking readers (PASSIVE)."""
    mode = mode.upper()
    if mode not in ("PASSIVE", "FULL", "RESTART", "TRUNCATE"):
        raise ValueError("invalid checkpoint mode")
    with contextlib.suppress(sqlite3.OperationalError):
        conn.execute(f"PRAGMA wal_checkpoint({mode})")
