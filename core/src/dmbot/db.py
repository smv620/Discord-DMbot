"""Postgres access shared by every store: a connection pool, per-server transactions,
row-level security, and run-once migrations.

Isolation (CLAUDE.md, "Campaign and server isolation") is enforced twice:
1. every query filters on the Discord server (guild) ID, and
2. Postgres **row-level security**: every table with server data has a policy that only
   shows rows whose `guild_id` matches the server set for the current transaction. The
   policies are FORCEd, so they apply to the table owner too. Code must therefore reach
   server data through `Database.guild(guild_id)`; without it, those tables look empty.

DMbot must connect as an ordinary (non-superuser) role, because superusers skip
row-level security. `Database.open` refuses otherwise.
"""

from __future__ import annotations

import contextlib
import logging
import re
from collections.abc import AsyncGenerator, AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from typing import Any

from psycopg import AsyncConnection, sql
from psycopg import errors as pg_errors
from psycopg.conninfo import make_conninfo
from psycopg.rows import DictRow, dict_row
from psycopg_pool import AsyncConnectionPool

from dmbot.schema import MIGRATIONS, WEB_ROLE, WEB_ROLE_GRANTS, WEB_ROLE_POLICIES, Migration

log = logging.getLogger(__name__)

Conn = AsyncConnection[DictRow]

# Any constant works; it just has to be the same for every DMbot process.
MIGRATION_LOCK_KEY = 0x0D4B07
_SCHEMA_NAME = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


class DatabaseError(RuntimeError):
    """The database can't be used as configured. The message says how to fix it."""


class Database:
    def __init__(self, pool: AsyncConnectionPool[Conn], conninfo: str) -> None:
        self._pool = pool
        self._conninfo = conninfo  # for connections outside the pool (listen)

    @classmethod
    async def open(
        cls,
        url: str,
        *,
        schema: str | None = None,
        min_size: int = 1,
        max_size: int = 10,
        open_timeout: float = 15,
        migrations: Sequence[Migration] = MIGRATIONS,
        migrate: bool = True,
        options: str = "",
        require_role: str | None = None,
    ) -> Database:
        """Connect, check the role is safe, and bring the schema up to date.

        `schema` puts everything in a separate Postgres schema (used by tests).
        `migrate=False` only checks the schema is up to date: for the website's role,
        which may not change it (the bot updates it). `options` are extra server
        settings, e.g. "-c statement_timeout=5000". `require_role` refuses any other
        database user: the website's limits only hold for its own role (#498).
        """
        conninfo = url
        if schema is not None:
            if not _SCHEMA_NAME.match(schema):
                raise ValueError(f"Invalid schema name: {schema!r}")
            if migrate:
                await _create_schema(url, schema)
            options = f"{options} -c search_path={schema}".strip()
        if options:
            conninfo = make_conninfo(url, options=options)
        pool: AsyncConnectionPool[Conn] = AsyncConnectionPool(
            conninfo,
            connection_class=AsyncConnection[DictRow],
            kwargs={"autocommit": True, "row_factory": dict_row, "connect_timeout": 5},
            min_size=min_size,
            max_size=max_size,
            timeout=10,  # longest wait for a free connection before giving up
            # Test each connection before use, so a Postgres restart doesn't break the
            # next command.
            check=AsyncConnectionPool.check_connection,
            open=False,
            name="dmbot",
        )
        try:
            await pool.open(wait=True, timeout=open_timeout)
        except Exception as exc:
            await pool.close()
            raise DatabaseError(
                "Can't connect to the database. Check DATABASE_URL and that Postgres is running."
            ) from exc
        db = cls(pool, conninfo)
        try:
            await db._check_role(require_role)
            if migrate:
                await db.migrate(migrations)
            else:
                await db._check_up_to_date(migrations)
        except BaseException:
            await db.close()
            raise
        return db

    async def close(self) -> None:
        await self._pool.close()

    @asynccontextmanager
    async def guild(self, guild_id: int, *, snapshot: bool = False) -> AsyncIterator[Conn]:
        """A transaction that can only see one Discord server's rows.

        `snapshot=True` makes every read in the transaction see the same moment (for
        backups). The transaction commits when the block ends, or rolls back on error.
        """
        async with self._pool.connection() as conn, conn.transaction():
            if snapshot:
                await conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            await conn.execute(
                "SELECT set_config('dmbot.guild_id', %s, true)", (str(int(guild_id)),)
            )
            yield conn

    @asynccontextmanager
    async def _with(self, **settings: str) -> AsyncIterator[Conn]:
        """A transaction with these `dmbot.*` settings, all cleared when it ends."""
        async with self._pool.connection() as conn, conn.transaction():
            for name, value in settings.items():
                await conn.execute(
                    sql.SQL("SELECT set_config({}, %s, true)").format(sql.Literal(f"dmbot.{name}")),
                    (value,),
                )
            yield conn

    @asynccontextmanager
    async def user(
        self, user_id: int, *, install_guild: int | None = None, session: str | None = None
    ) -> AsyncIterator[Conn]:
        """A transaction that can only see one person's website rows (#435): their
        account, plan (read only), sessions and installs. Server rows stay hidden unless
        code also sets a server (only dmbot.web.me does, for servers in the session's own
        list; the website's role is held to that list, #498).

        `install_guild` lets it record or link DMbot's install on that one server, and
        nothing else of that server's. The caller must first have checked, with Discord,
        that the person manages that server. `session` (the cookie's hash) is the
        signed-in session whose server list the website's role is held to (#498)."""
        settings = {"user_id": str(int(user_id))}
        if install_guild is not None:
            settings["install_guild"] = str(int(install_guild))
        if session is not None:
            settings["session"] = session
        async with self._with(**settings) as conn:
            yield conn

    @asynccontextmanager
    async def session(self, id_hash: str) -> AsyncIterator[Conn]:
        """A transaction that can see only the unexpired website session with this cookie
        hash, to find who is signed in before the person is known. Read only."""
        async with self._with(session=id_hash) as conn:
            yield conn

    @asynccontextmanager
    async def plan_writer(self, user_id: int) -> AsyncIterator[Conn]:
        """The only way to change a person's plan (`entitlements`): for the payment
        webhook and Try It (dmbot.web, #435). Everything else reads plans.

        One plan change per person at a time: a lock held until the transaction ends, so
        two payment events (or an event and Try It) arriving together can't both act on
        "no plan yet" and overwrite each other."""
        async with self._with(user_id=str(int(user_id)), plan_writer="payments") as conn:
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (f"dmbot.plan_writer:{int(user_id)}",),
            )
            yield conn

    @asynccontextmanager
    async def cleanup(self) -> AsyncIterator[Conn]:
        """A transaction that can see and delete expired website sessions, nothing else."""
        async with self._with(cleanup="expired-sessions") as conn:
            yield conn

    @asynccontextmanager
    async def unscoped(self) -> AsyncIterator[Conn]:
        """A transaction with no server set: server tables look empty.

        For schema work and for routing tables, which hold only server IDs (see
        dmbot.schema).
        """
        async with self._pool.connection() as conn, conn.transaction():
            yield conn

    async def listen(
        self, channel: str, on_listening: Callable[[], None] | None = None
    ) -> AsyncGenerator[str, None]:
        """Payloads of Postgres notifications on `channel`, until the connection fails.

        Uses its own connection (not one from the pool), with TCP keepalives so a link
        that silently died is noticed within about a minute. `on_listening` is called
        once LISTEN is in effect: anything committed before then must be re-read.
        Notifications skip row-level security, so they must never carry server data
        (IDs and versions only).
        """
        conn = await AsyncConnection.connect(
            self._conninfo,
            autocommit=True,
            connect_timeout=5,
            keepalives=1,
            keepalives_idle=30,
            keepalives_interval=10,
            keepalives_count=3,
        )
        try:
            await conn.execute(sql.SQL("LISTEN {}").format(sql.Identifier(channel)))
            if on_listening is not None:
                on_listening()
            # Closed before the connection, so nothing is left waiting on it.
            async with contextlib.aclosing(conn.notifies()) as notes:
                async for note in notes:
                    yield note.payload
        finally:
            await conn.close()

    async def _check_role(self, require_role: str | None = None) -> None:
        async with self.unscoped() as conn:
            cur = await conn.execute(
                "SELECT rolname, rolsuper, rolbypassrls,"
                " current_setting('server_encoding') AS encoding"
                " FROM pg_roles WHERE rolname = current_user"
            )
            row = await cur.fetchone()
        if require_role is not None and (row is None or row["rolname"] != require_role):
            raise DatabaseError(
                f"This must connect to the database as {require_role}, its own user with "
                "only the rights it needs. Check DATABASE_URL. See README, 'Database'."
            )
        if row is None or row["rolsuper"] or row["rolbypassrls"]:
            raise DatabaseError(
                "DMbot's database user must be an ordinary user, not a superuser, so "
                "each Discord server's data stays separate. See README, 'Database'."
            )
        if row["encoding"] != "UTF8":
            raise DatabaseError(
                "DMbot's database must use UTF8 encoding (campaign and player names can "
                "contain any character). See README, 'Database'."
            )

    async def _check_up_to_date(self, migrations: Sequence[Migration]) -> None:
        try:
            async with self.unscoped() as conn:
                cur = await conn.execute("SELECT name FROM schema_migrations")
                done = {r["name"] for r in await cur.fetchall()}
        except pg_errors.InsufficientPrivilege as exc:
            raise DatabaseError(
                "This database user has no rights yet. Restart core (the bot gives them "
                "on start), then this. See README, 'Database'."
            ) from exc
        except pg_errors.UndefinedTable as exc:
            raise DatabaseError(
                "The database isn't set up yet. Start the bot first (it updates the "
                "database), then this."
            ) from exc
        if any(name not in done for name, _ in migrations):
            raise DatabaseError(
                "The database is older than this code. Start the updated bot first (it "
                "updates the database), then this."
            )

    async def migrate(self, migrations: Sequence[Migration] = MIGRATIONS) -> list[str]:
        """Apply migrations not yet recorded, all in one transaction.

        An advisory lock makes this safe when several DMbot processes start together.
        Returns the names applied now.
        """
        applied: list[str] = []
        async with self.unscoped() as conn:
            await _take_migration_lock(conn)
            await conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                " name TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
            )
            cur = await conn.execute("SELECT name FROM schema_migrations")
            done = {r["name"] for r in await cur.fetchall()}
            for name, statements in migrations:
                if name in done:
                    continue
                await conn.execute(statements)  # no parameters: several statements are fine
                await conn.execute("INSERT INTO schema_migrations (name) VALUES (%s)", (name,))
                applied.append(name)
            await _grant_web_role(conn)
        if applied:
            log.info("Database updated: %s", ", ".join(applied))
        return applied


async def _grant_web_role(conn: AsyncConnection[Any]) -> None:
    """Give the website's role (#498) exactly WEB_ROLE_GRANTS in this schema, and its
    limits (WEB_ROLE_POLICIES), if the role exists (it's made outside DMbot): everything
    is revoked first, so a right taken out of the list is taken away too. Run on every
    start, in the migration's transaction, so rights and limits appear together."""
    cur = await conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (WEB_ROLE,))
    if await cur.fetchone() is None:
        return
    role = sql.Identifier(WEB_ROLE)
    cur = await conn.execute("SELECT current_schema() AS s")
    row = await cur.fetchone()
    if row is None or row["s"] is None:
        raise DatabaseError("The database's search_path names no schema that exists.")
    here = sql.Identifier(row["s"])
    await conn.execute(sql.SQL("REVOKE ALL ON ALL TABLES IN SCHEMA {} FROM {}").format(here, role))
    await conn.execute(
        sql.SQL("REVOKE ALL ON ALL SEQUENCES IN SCHEMA {} FROM {}").format(here, role)
    )
    await conn.execute(sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(here, role))
    await conn.execute(WEB_ROLE_POLICIES)  # constants: several statements, no parameters
    for privileges, tables in WEB_ROLE_GRANTS:
        await conn.execute(
            sql.SQL("GRANT {} ON {} TO {}").format(
                sql.SQL(privileges),  # constants from schema.py, never input
                sql.SQL(", ").join(sql.Identifier(t) for t in tables),
                role,
            )
        )


async def _take_migration_lock(conn: AsyncConnection[Any]) -> None:
    """Wait for any other DMbot updating the schema, but not forever."""
    await conn.execute("SET LOCAL lock_timeout = '60s'")
    try:
        await conn.execute("SELECT pg_advisory_xact_lock(%s)", (MIGRATION_LOCK_KEY,))
    except pg_errors.LockNotAvailable as exc:
        raise DatabaseError(
            "Another copy of DMbot has been updating the database for over a minute. "
            "Check it isn't stuck, then start this one again."
        ) from exc


async def _create_schema(url: str, schema: str) -> None:
    conn = await AsyncConnection.connect(url, autocommit=True)
    try:
        # Under the migration lock, so processes starting together don't race.
        async with conn.transaction():
            await _take_migration_lock(conn)
            await conn.execute(
                sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(schema))
            )
    finally:
        await conn.close()


async def drop_schema(url: str, schema: str) -> None:
    """Remove a schema made with `Database.open(schema=...)` (tests)."""
    if not _SCHEMA_NAME.match(schema):
        raise ValueError(f"Invalid schema name: {schema!r}")
    conn = await AsyncConnection.connect(url, autocommit=True)
    try:
        await conn.execute(
            sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema))
        )
    finally:
        await conn.close()


def row_int(row: dict[str, Any], key: str) -> int | None:
    value = row[key]
    return None if value is None else int(value)
