"""Tiny SQLite helpers shared by the stores: connection settings, transactions, migrations.

All stores open their database with `connect()`, so every connection to the shared file
uses the same settings (WAL, busy timeout, autocommit + explicit transactions).

Each store owns a list of (name, SQL) migrations. Names are recorded in
`schema_migrations`, so each one runs exactly once per database file, in order.
Never edit a migration that has shipped; add a new one instead.
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager, suppress
from pathlib import Path

Migration = tuple[str, str]


def connect(path: Path | str) -> sqlite3.Connection:
    """Open a SQLite connection with the settings every store uses.

    Autocommit mode (isolation_level=None): writes that must be atomic use
    `transaction()`, so schema changes and data changes are covered alike.
    """
    target = str(path)
    if target != ":memory:":
        Path(target).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target, check_same_thread=False, timeout=10, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if target != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """BEGIN IMMEDIATE … COMMIT, rolled back if anything (including COMMIT) fails.

    The connection is always left outside a transaction, so one failure (disk full,
    database busy) can't jam every later write.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
        conn.execute("COMMIT")
    except BaseException:
        _rollback_quietly(conn)
        raise


@contextmanager
def read_snapshot(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """A read-only transaction, so several SELECTs see one consistent state."""
    conn.execute("BEGIN")
    try:
        yield conn
        conn.execute("COMMIT")
    except BaseException:
        _rollback_quietly(conn)
        raise


def _rollback_quietly(conn: sqlite3.Connection) -> None:
    # SQLite may already have rolled back (e.g. on a full disk). Don't let a failing
    # ROLLBACK hide the original error.
    if conn.in_transaction:
        with suppress(sqlite3.Error):
            conn.execute("ROLLBACK")


def apply_migrations(conn: sqlite3.Connection, migrations: Sequence[Migration]) -> list[str]:
    """Apply any migrations not yet recorded. Returns the names applied now."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        " name TEXT PRIMARY KEY, applied_at INTEGER NOT NULL)"
    )
    done = {row[0] for row in conn.execute("SELECT name FROM schema_migrations")}
    applied: list[str] = []
    for name, sql in migrations:
        if name in done:
            continue
        with transaction(conn):
            for statement in _statements(sql):
                conn.execute(statement)
            conn.execute(
                "INSERT INTO schema_migrations (name, applied_at) VALUES (?, ?)",
                (name, int(time.time())),
            )
        applied.append(name)
    return applied


def _statements(sql: str) -> list[str]:
    """Split a migration into statements.

    Limitation: splits on every ';', so migrations must not contain ';' inside string
    literals or trigger bodies (CREATE TRIGGER … BEGIN …; END). Add a different runner
    before writing one of those.
    """
    return [s.strip() for s in sql.split(";") if s.strip()]
