"""Tiny SQLite helpers shared by the stores: connection settings, transactions, migrations.

Each store owns a list of (name, SQL) migrations. Names are recorded in
`schema_migrations`, so each one runs exactly once per database file, in order.
Never edit a migration that has shipped; add a new one instead.
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
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
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """BEGIN IMMEDIATE … COMMIT, or ROLLBACK if anything raises."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


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
    """Split a migration into statements. Migrations must not contain ';' in literals."""
    return [s.strip() for s in sql.split(";") if s.strip()]
