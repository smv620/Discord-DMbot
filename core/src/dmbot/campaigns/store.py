"""Campaign storage in Postgres.

Isolation is enforced twice. Every public method takes the Discord server (guild) ID and
every query filters on it, and each method runs inside `Database.guild(guild_id)`, where
Postgres row-level security hides every other server's rows. Asking for another server's
campaign behaves exactly like asking for one that doesn't exist.

Future features keep their per-campaign data in their own tables (with `campaign_id` and
`guild_id` columns, referencing `campaigns (id, guild_id)`, isolated in `dmbot.schema`)
and plug into backups by registering an `ExportSection`.
"""

from __future__ import annotations

import asyncio
import gzip
import json
import logging
import re
import time
import uuid
import zlib
from collections.abc import Callable
from typing import Any, LiteralString, Protocol

from psycopg import errors as pg_errors
from psycopg import sql

from dmbot.campaigns.models import (
    CONFIRMATION_PURPOSES,
    DEFAULT_DM_SCREEN_LEVEL,
    DEFAULT_DM_SCREEN_VISIBILITY,
    DEFAULT_FALLBACK,
    DEFAULT_TARGET,
    DM_SCREEN_LEVELS,
    DM_SCREEN_VISIBILITY,
    NAME_MAX,
    Campaign,
    CampaignError,
    check_dm_screen_level,
    check_dm_screen_visibility,
    check_rulesets,
    clean_name,
    name_key,
)
from dmbot.db import Conn, Database, row_int
from dmbot.logs import log_context

log = logging.getLogger(__name__)

EXPORT_FORMAT = "dmbot-campaign"
# 2: written compressed since #164; contents unchanged from 1. Plain files of either
# version still restore: the file's first bytes, not its version, say if it's packed.
EXPORT_VERSION = 2

RULE_ID_MAX = 64
# Discord IDs and Unix timestamps must fit Postgres BIGINT (signed 64-bit).
INT64_MAX = 2**63 - 1
# Upper bound on a backup's JSON, checked before parsing it: on the file, and on a
# compressed file while it's unpacked (so a tiny file can't unpack to gigabytes).
MAX_BACKUP_BYTES = 25 * 1024 * 1024
GZIP_MAGIC = b"\x1f\x8b"

NOT_HERE = "That campaign doesn't exist in this server."
NAME_TAKEN = "This server already has a campaign with that name."
DAMAGED = (
    "This backup file is damaged or isn't a DMbot campaign backup. "
    "Pick the file you got from `/dmbot backup`."
)
TOO_BIG = "That file is too big to be a DMbot backup. Pick the file you got from `/dmbot backup`."
NOT_A_BACKUP = (
    "That file isn't a DMbot campaign backup. Pick the file you got from `/dmbot backup`."
)
CAMPAIGN_TOO_BIG = "This campaign is too big for DMbot to copy yet. Nothing was lost."


class BackupTooBig(CampaignError):
    """The campaign is bigger than a restore takes, so no copy is made of it."""


class ExportSection(Protocol):
    """One kind of per-campaign data that is included in backups.

    Contract for every section:
    - Tables carry `campaign_id` and `guild_id`, reference
      `campaigns (id, guild_id) ON DELETE CASCADE`, and are isolated with row-level
      security (`dmbot.schema`), so rows can't drift to another server.
    - `dump` returns JSON-friendly rows for one campaign; `load` writes rows into a (new
      or emptied) campaign; `clear` removes that campaign's rows. All three run inside the
      store's transaction and must filter by **both** guild_id and campaign_id.
    - `load` receives rows from an **untrusted file**: validate every field and raise
      `CampaignError` with a plain message when something is wrong. A section with a
      lot of rows may also have a pure `check(rows)`: it runs in a worker thread before
      the transaction opens, and `load` then gets what it returned (#164).
    - Never export server-specific IDs (channels, roles, messages); they mean nothing
      in another server.
    """

    name: str

    async def dump(self, conn: Conn, guild_id: int, campaign_id: str) -> list[Any]: ...

    async def load(self, conn: Conn, guild_id: int, campaign_id: str, rows: list[Any]) -> None: ...

    async def clear(self, conn: Conn, guild_id: int, campaign_id: str) -> None: ...


def _valid_user_id(raw: object) -> bool:
    return isinstance(raw, str) and raw.isascii() and raw.isdigit() and 0 < int(raw) <= INT64_MAX


def _valid_rule_id(rule: object) -> bool:
    return (
        isinstance(rule, str)
        and 0 < len(rule) <= RULE_ID_MAX
        and all(c.isalnum() or c in "-_." for c in rule)
    )


class _DMSection:
    name = "dms"

    async def dump(self, conn: Conn, guild_id: int, campaign_id: str) -> list[Any]:
        cur = await conn.execute(
            "SELECT user_id FROM campaign_dms WHERE guild_id = %s AND campaign_id = %s"
            " ORDER BY user_id",
            (guild_id, campaign_id),
        )
        return [str(r["user_id"]) for r in await cur.fetchall()]

    async def load(self, conn: Conn, guild_id: int, campaign_id: str, rows: list[Any]) -> None:
        for raw in rows:
            if not _valid_user_id(raw):
                raise CampaignError("This backup file is damaged (bad DM entry).")
        async with conn.cursor() as cur:
            await cur.executemany(
                "INSERT INTO campaign_dms (campaign_id, guild_id, user_id) VALUES (%s, %s, %s)"
                " ON CONFLICT DO NOTHING",
                [(campaign_id, guild_id, int(raw)) for raw in rows],
            )

    async def clear(self, conn: Conn, guild_id: int, campaign_id: str) -> None:
        await conn.execute(
            "DELETE FROM campaign_dms WHERE guild_id = %s AND campaign_id = %s",
            (guild_id, campaign_id),
        )


_FINGERPRINT = re.compile(r"[0-9a-f]{64}")


class _ConfirmationsSection:
    """Who confirmed the right to use shared material, and when (#252): part of the
    campaign's history, so it travels with its backups. Rows read from a backup file are
    marked `restored`: anyone in the server may restore one, so they say what the file
    says, not what DMbot saw pressed. Rows are checked before the restore's transaction
    opens (`check`, #164)."""

    name = "confirmations"

    async def dump(self, conn: Conn, guild_id: int, campaign_id: str) -> list[Any]:
        cur = await conn.execute(
            "SELECT id, user_id, purpose, fingerprint, confirmed_at, restored"
            " FROM shared_confirmations"
            " WHERE guild_id = %s AND campaign_id = %s ORDER BY confirmed_at, id",
            (guild_id, campaign_id),
        )
        return [
            {
                "id": r["id"],
                "user": str(r["user_id"]),
                "purpose": r["purpose"],
                "fingerprint": r["fingerprint"],
                "at": int(r["confirmed_at"]),
                "restored": bool(r["restored"]),
            }
            for r in await cur.fetchall()
        ]

    def check(self, rows: list[Any]) -> list[tuple[str, int, str, str, int]]:
        """Every field from the untrusted file, checked (in a worker thread). A backup is
        complete, so two rows with one id mean it's damaged, never a row quietly lost."""
        values: list[tuple[str, int, str, str, int]] = []
        seen: set[str] = set()
        for row in rows:
            got = row if isinstance(row, dict) else {}
            rid, user, purpose = got.get("id"), got.get("user"), got.get("purpose")
            fp, at = got.get("fingerprint"), got.get("at")
            if not (
                isinstance(rid, str)
                and 0 < len(rid) <= 64
                and rid.isascii()
                and rid.isalnum()
                and _valid_user_id(user)
                and purpose in CONFIRMATION_PURPOSES
                and isinstance(fp, str)
                and _FINGERPRINT.fullmatch(fp)
                and isinstance(at, int)
                and not isinstance(at, bool)
                and 0 <= at <= INT64_MAX
                and rid not in seen
            ):
                raise CampaignError("This backup file is damaged (bad confirmation).")
            seen.add(rid)
            assert isinstance(user, str) and isinstance(purpose, str)
            values.append((rid, int(user), purpose, fp, at))
        return values

    async def load(self, conn: Conn, guild_id: int, campaign_id: str, rows: list[Any]) -> None:
        async with conn.cursor() as cur:
            await cur.executemany(
                "INSERT INTO shared_confirmations (campaign_id, guild_id, id, user_id, purpose,"
                " fingerprint, confirmed_at, restored)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, true)",
                [(campaign_id, guild_id, *row) for row in rows],
            )

    async def clear(self, conn: Conn, guild_id: int, campaign_id: str) -> None:
        await conn.execute(
            "DELETE FROM shared_confirmations WHERE guild_id = %s AND campaign_id = %s",
            (guild_id, campaign_id),
        )


class _OptionalRulesSection:
    name = "optional_rules"

    async def dump(self, conn: Conn, guild_id: int, campaign_id: str) -> list[Any]:
        cur = await conn.execute(
            "SELECT rule_id, enabled FROM campaign_optional_rules"
            " WHERE guild_id = %s AND campaign_id = %s ORDER BY rule_id",
            (guild_id, campaign_id),
        )
        return [{"rule": r["rule_id"], "enabled": r["enabled"]} for r in await cur.fetchall()]

    async def load(self, conn: Conn, guild_id: int, campaign_id: str, rows: list[Any]) -> None:
        values: list[tuple[str, int, str, bool]] = []
        for row in rows:
            rule = row.get("rule") if isinstance(row, dict) else None
            enabled = row.get("enabled") if isinstance(row, dict) else None
            if not _valid_rule_id(rule) or not isinstance(enabled, bool):
                raise CampaignError("This backup file is damaged (bad optional rule).")
            assert isinstance(rule, str)
            values.append((campaign_id, guild_id, rule, enabled))
        async with conn.cursor() as cur:
            await cur.executemany(
                "INSERT INTO campaign_optional_rules (campaign_id, guild_id, rule_id, enabled)"
                " VALUES (%s, %s, %s, %s)"
                " ON CONFLICT (campaign_id, rule_id) DO UPDATE SET enabled = EXCLUDED.enabled",
                values,
            )

    async def clear(self, conn: Conn, guild_id: int, campaign_id: str) -> None:
        await conn.execute(
            "DELETE FROM campaign_optional_rules WHERE guild_id = %s AND campaign_id = %s",
            (guild_id, campaign_id),
        )


_ORDER: LiteralString = " ORDER BY COALESCE(last_played_at, created_at) DESC, created_at DESC, name"

# Columns that set_* methods may change. Column names can't be query parameters, so
# they are checked against this list before being put in SQL.
_SETTABLE = frozenset(
    {
        "dm_screen_channel_id",
        "last_voice_channel_id",
        "last_played_at",
        "optional_rules_default",
        "dm_screen_visibility",
        "dm_screen_level",
        "channel_number",
        "transcript_channel_id",
    }
)


class CampaignStore:
    def __init__(self, db: Database, *, clock: Callable[[], float] = time.time) -> None:
        self._db = db
        self._clock = clock
        self._sections: dict[str, ExportSection] = {}
        self.register_section(_DMSection())
        self.register_section(_OptionalRulesSection())
        self.register_section(_ConfirmationsSection())

    async def record_confirmation(
        self, guild_id: int, campaign_id: str, user_id: int, purpose: str, fingerprint: str
    ) -> None:
        """Who confirmed the right to use shared material, when, and what for (CLAUDE.md,
        IP rule: "Record who confirmed and when"; #252). Only a fingerprint of the text
        (`models.fingerprint`) is kept, never the text or its file's name."""
        if purpose not in CONFIRMATION_PURPOSES:
            raise ValueError(f"Unknown purpose {purpose!r}")
        if not _FINGERPRINT.fullmatch(fingerprint):
            raise ValueError("A fingerprint is 64 hex characters (models.fingerprint)")
        async with self._db.guild(guild_id) as conn:
            await self._require(conn, guild_id, campaign_id)
            await conn.execute(
                "INSERT INTO shared_confirmations (campaign_id, guild_id, id, user_id, purpose,"
                " fingerprint, confirmed_at) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (
                    campaign_id,
                    guild_id,
                    uuid.uuid4().hex,
                    user_id,
                    purpose,
                    fingerprint,
                    int(self._clock()),
                ),
            )

    async def confirmations(self, guild_id: int, campaign_id: str) -> list[dict[str, Any]]:
        """This campaign's confirmations, oldest first (as in its backups)."""
        async with self._db.guild(guild_id) as conn:
            await self._require(conn, guild_id, campaign_id)
            return await self._sections["confirmations"].dump(conn, guild_id, campaign_id)

    def register_section(self, section: ExportSection) -> None:
        """Include another feature's per-campaign data in backups and deletes."""
        if section.name in self._sections:
            raise ValueError(f"Export section {section.name!r} is already registered")
        self._sections[section.name] = section

    # ---- reading ------------------------------------------------------------

    async def get(self, guild_id: int, campaign_id: str) -> Campaign | None:
        async with self._db.guild(guild_id) as conn:
            return await self._get(conn, guild_id, campaign_id)

    async def list_campaigns(self, guild_id: int) -> list[Campaign]:
        """All of a server's campaigns, most recently used first."""
        async with self._db.guild(guild_id, snapshot=True) as conn:
            cur = await conn.execute(
                "SELECT * FROM campaigns WHERE guild_id = %s" + _ORDER, (guild_id,)
            )
            rows = await cur.fetchall()
            dms = await self._dms_for(conn, guild_id)
        return [_to_campaign(r, dms.get(r["id"], set())) for r in rows]

    async def last_used(self, guild_id: int) -> Campaign | None:
        """The default for `/dmbot start`: the campaign played (or created) most recently."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT id FROM campaigns WHERE guild_id = %s" + _ORDER + " LIMIT 1",
                (guild_id,),
            )
            row = await cur.fetchone()
            return await self._get(conn, guild_id, row["id"]) if row else None

    async def optional_rule_overrides(self, guild_id: int, campaign_id: str) -> dict[str, bool]:
        """Rules the DM switched away from the campaign default. Missing = use default."""
        async with self._db.guild(guild_id) as conn:
            await self._require(conn, guild_id, campaign_id)
            cur = await conn.execute(
                "SELECT rule_id, enabled FROM campaign_optional_rules"
                " WHERE guild_id = %s AND campaign_id = %s",
                (guild_id, campaign_id),
            )
            return {r["rule_id"]: r["enabled"] for r in await cur.fetchall()}

    # ---- creating and changing ------------------------------------------------

    async def create(
        self,
        guild_id: int,
        name: str,
        dm_user_id: int,
        *,
        target_ruleset: str = DEFAULT_TARGET,
        fallback_ruleset: str = DEFAULT_FALLBACK,
        optional_rules_default: bool = True,
        dm_screen_visibility: str = DEFAULT_DM_SCREEN_VISIBILITY,
        dm_screen_level: str = DEFAULT_DM_SCREEN_LEVEL,
    ) -> Campaign:
        clean = clean_name(name)
        check_rulesets(target_ruleset, fallback_ruleset)
        check_dm_screen_visibility(dm_screen_visibility)
        check_dm_screen_level(dm_screen_level)
        try:
            async with self._db.guild(guild_id) as conn:
                campaign_id = await self._insert(
                    conn,
                    guild_id,
                    clean,
                    target_ruleset,
                    fallback_ruleset,
                    optional_rules_default,
                    int(self._clock()),
                    None,
                    dm_screen_visibility,
                    dm_screen_level,
                )
                await conn.execute(
                    "INSERT INTO campaign_dms (campaign_id, guild_id, user_id) VALUES (%s, %s, %s)",
                    (campaign_id, guild_id, dm_user_id),
                )
                return await self._require(conn, guild_id, campaign_id)
        except pg_errors.UniqueViolation as exc:
            raise CampaignError(NAME_TAKEN) from exc

    async def rename(self, guild_id: int, campaign_id: str, new_name: str) -> Campaign:
        clean = clean_name(new_name)
        try:
            async with self._db.guild(guild_id) as conn:
                await self._require(conn, guild_id, campaign_id)
                await conn.execute(
                    "UPDATE campaigns SET name = %s, name_key = %s WHERE guild_id = %s AND id = %s",
                    (clean, name_key(clean), guild_id, campaign_id),
                )
                return await self._require(conn, guild_id, campaign_id)
        except pg_errors.UniqueViolation as exc:
            raise CampaignError(NAME_TAKEN) from exc

    async def set_rulesets(
        self, guild_id: int, campaign_id: str, target: str, fallback: str
    ) -> Campaign:
        check_rulesets(target, fallback)
        async with self._db.guild(guild_id) as conn:
            await self._require(conn, guild_id, campaign_id)
            await conn.execute(
                "UPDATE campaigns SET target_ruleset = %s, fallback_ruleset = %s"
                " WHERE guild_id = %s AND id = %s",
                (target, fallback, guild_id, campaign_id),
            )
            return await self._require(conn, guild_id, campaign_id)

    async def set_optional_rules_default(
        self, guild_id: int, campaign_id: str, enabled: bool
    ) -> Campaign:
        return await self._set(guild_id, campaign_id, "optional_rules_default", enabled)

    async def set_optional_rule(
        self, guild_id: int, campaign_id: str, rule_id: str, enabled: bool
    ) -> None:
        if not _valid_rule_id(rule_id):
            raise ValueError(f"Invalid optional rule id: {rule_id!r}")
        async with self._db.guild(guild_id) as conn:
            await self._require(conn, guild_id, campaign_id)
            await conn.execute(
                "INSERT INTO campaign_optional_rules (campaign_id, guild_id, rule_id, enabled)"
                " VALUES (%s, %s, %s, %s)"
                " ON CONFLICT (campaign_id, rule_id) DO UPDATE SET enabled = EXCLUDED.enabled",
                (campaign_id, guild_id, rule_id, enabled),
            )

    async def add_dm(self, guild_id: int, campaign_id: str, user_id: int) -> Campaign:
        async with self._db.guild(guild_id) as conn:
            await self._require(conn, guild_id, campaign_id)
            await conn.execute(
                "INSERT INTO campaign_dms (campaign_id, guild_id, user_id) VALUES (%s, %s, %s)"
                " ON CONFLICT DO NOTHING",
                (campaign_id, guild_id, user_id),
            )
            return await self._require(conn, guild_id, campaign_id)

    async def remove_dm(self, guild_id: int, campaign_id: str, user_id: int) -> Campaign:
        async with self._db.guild(guild_id) as conn:
            # Lock the campaign row so two removals can't leave it with no DM.
            await conn.execute(
                "SELECT 1 FROM campaigns WHERE guild_id = %s AND id = %s FOR UPDATE",
                (guild_id, campaign_id),
            )
            campaign = await self._require(conn, guild_id, campaign_id)
            if user_id in campaign.dm_user_ids and len(campaign.dm_user_ids) == 1:
                raise CampaignError("A campaign needs at least one DM. Add the new DM first.")
            await conn.execute(
                "DELETE FROM campaign_dms"
                " WHERE guild_id = %s AND campaign_id = %s AND user_id = %s",
                (guild_id, campaign_id, user_id),
            )
            return await self._require(conn, guild_id, campaign_id)

    async def set_dm_screen(
        self, guild_id: int, campaign_id: str, channel_id: int | None
    ) -> Campaign:
        """Store the campaign's DM screen channel.

        The caller must already have confirmed the channel belongs to this server and is
        private to the DM(s); the store can't see Discord.
        """
        return await self._set(guild_id, campaign_id, "dm_screen_channel_id", channel_id)

    async def set_transcript_channel(
        self, guild_id: int, campaign_id: str, channel_id: int | None
    ) -> Campaign:
        """Store the campaign's live transcript channel (#124)."""
        return await self._set(guild_id, campaign_id, "transcript_channel_id", channel_id)

    async def set_dm_screen_visibility(
        self, guild_id: int, campaign_id: str, visibility: str
    ) -> Campaign:
        """Who besides the DM may see the DM screen: "private", "peek" or "open".

        This only stores the choice; applying it to Discord channel permissions is the
        DM-screen feature's job (#30).
        """
        check_dm_screen_visibility(visibility)
        return await self._set(guild_id, campaign_id, "dm_screen_visibility", visibility)

    async def set_dm_screen_level(self, guild_id: int, campaign_id: str, level: str) -> Campaign:
        """How much DMbot says in the DM screen: quiet, normal or chatty (#504)."""
        check_dm_screen_level(level)
        return await self._set(guild_id, campaign_id, "dm_screen_level", level)

    async def set_last_voice_channel(
        self, guild_id: int, campaign_id: str, channel_id: int | None
    ) -> Campaign:
        return await self._set(guild_id, campaign_id, "last_voice_channel_id", channel_id)

    async def set_channel_number(
        self, guild_id: int, campaign_id: str, number: int, *, replace: bool = False
    ) -> Campaign:
        """The number in front of the campaign's channel names (1 = none).

        Chosen once: unless `replace` is set, an already stored number is kept (and
        returned), so two setups racing can't change it after the first one saved it.
        """
        if number < 1:
            raise ValueError("channel number must be 1 or more")
        if replace:
            return await self._set(guild_id, campaign_id, "channel_number", number)
        async with self._db.guild(guild_id) as conn:
            await self._require(conn, guild_id, campaign_id)
            await conn.execute(
                "UPDATE campaigns SET channel_number = %s"
                " WHERE guild_id = %s AND id = %s AND channel_number IS NULL",
                (number, guild_id, campaign_id),
            )
            return await self._require(conn, guild_id, campaign_id)

    async def mark_played(self, guild_id: int, campaign_id: str) -> Campaign:
        return await self._set(guild_id, campaign_id, "last_played_at", int(self._clock()))

    async def delete(self, guild_id: int, campaign_id: str) -> None:
        async with self._db.guild(guild_id) as conn:
            # Lock the campaign first, as campaign-memory writes do, so the two can't
            # deadlock while the sections clear their rows.
            await conn.execute(
                "SELECT 1 FROM campaigns WHERE guild_id = %s AND id = %s FOR UPDATE",
                (guild_id, campaign_id),
            )
            await self._require(conn, guild_id, campaign_id)
            for section in self._sections.values():
                await section.clear(conn, guild_id, campaign_id)
            # The saved session would go with the campaign anyway (ON DELETE CASCADE);
            # removing it here logs it and leaves no restart note behind (#147).
            cur = await conn.execute(
                "DELETE FROM active_sessions WHERE guild_id = %s AND campaign_id = %s"
                " RETURNING guild_id",
                (guild_id, campaign_id),
            )
            had_session = await cur.fetchone() is not None
            if had_session:
                await conn.execute(
                    "DELETE FROM live_session_guilds WHERE guild_id = %s", (guild_id,)
                )
            await conn.execute(
                "DELETE FROM campaigns WHERE guild_id = %s AND id = %s", (guild_id, campaign_id)
            )
        if had_session:
            with log_context(guild_id=guild_id, campaign_id=campaign_id):
                log.info("Saved session removed: its campaign was deleted")

    # ---- backups -------------------------------------------------------------

    async def export(self, guild_id: int, campaign_id: str) -> dict[str, Any]:
        """A JSON-friendly backup of one campaign, read from one consistent moment."""
        async with self._db.guild(guild_id, snapshot=True) as conn:
            campaign = await self._require(conn, guild_id, campaign_id)
            sections = {
                name: await section.dump(conn, guild_id, campaign_id)
                for name, section in self._sections.items()
            }
        return {
            "format": EXPORT_FORMAT,
            "version": EXPORT_VERSION,
            "exported_at": int(self._clock()),
            "campaign": {
                "name": campaign.name,
                "created_at": campaign.created_at,
                "last_played_at": campaign.last_played_at,
                "target_ruleset": campaign.target_ruleset,
                "fallback_ruleset": campaign.fallback_ruleset,
                "optional_rules_default": campaign.optional_rules_default,
                "dm_screen_visibility": campaign.dm_screen_visibility,
                "dm_screen_level": campaign.dm_screen_level,
            },
            "sections": sections,
        }

    def _checked(self, data: object) -> tuple[dict[str, Any], dict[str, Any]]:
        """The backup's campaign settings and each section's rows, checked. Pure."""
        info, sections = _validate_backup(data, set(self._sections))
        checked: dict[str, Any] = {}
        for name, rows in sections.items():
            check = getattr(self._sections[name], "check", None)
            if check is None:
                checked[name] = rows
                continue
            try:
                checked[name] = check(rows)
            except CampaignError:
                raise
            except (ValueError, TypeError, KeyError, AttributeError, OverflowError) as exc:
                raise CampaignError(DAMAGED) from exc
        return info, checked

    async def import_backup(
        self,
        guild_id: int,
        data: object,
        importer_id: int,
        *,
        replace_campaign_id: str | None = None,
    ) -> Campaign:
        """Restore a backup into this server, as a new campaign or replacing one.

        The importer always becomes a DM of the restored campaign. When replacing, the
        importer must already be a DM of the campaign being replaced. Channel settings
        are not restored, because channels belong to the server the backup came from.
        `data` is untrusted: it's fully validated before anything is written, and the
        whole restore happens in one transaction.
        """
        # Checking a big file is pure CPU: in a worker thread, so the event loop (voice,
        # other servers) carries on, and before the transaction, so no locks are held
        # meanwhile (#164).
        info, sections = await asyncio.to_thread(self._checked, data)
        async with self._db.guild(guild_id) as conn:
            if replace_campaign_id is not None:
                await conn.execute(
                    "SELECT 1 FROM campaigns WHERE guild_id = %s AND id = %s FOR UPDATE",
                    (guild_id, replace_campaign_id),
                )
                existing = await self._require(conn, guild_id, replace_campaign_id)
                if importer_id not in existing.dm_user_ids:
                    raise CampaignError("Only this campaign's DM can replace it with a backup.")
                campaign_id = replace_campaign_id
                for section in self._sections.values():
                    await section.clear(conn, guild_id, campaign_id)
                await conn.execute(
                    "UPDATE campaigns SET target_ruleset = %s, fallback_ruleset = %s,"
                    " optional_rules_default = %s, last_played_at = %s,"
                    " dm_screen_visibility = %s, dm_screen_level = %s"
                    " WHERE guild_id = %s AND id = %s",
                    (
                        info["target_ruleset"],
                        info["fallback_ruleset"],
                        info["optional_rules_default"],
                        info["last_played_at"],
                        info["dm_screen_visibility"],
                        info["dm_screen_level"],
                        guild_id,
                        campaign_id,
                    ),
                )
            else:
                name = await self._free_name(conn, guild_id, info["name"])
                try:
                    campaign_id = await self._insert(
                        conn,
                        guild_id,
                        name,
                        info["target_ruleset"],
                        info["fallback_ruleset"],
                        info["optional_rules_default"],
                        info["created_at"],
                        info["last_played_at"],
                        info["dm_screen_visibility"],
                        info["dm_screen_level"],
                    )
                except pg_errors.UniqueViolation as exc:
                    # Another restore took the same name a moment ago.
                    raise CampaignError(
                        "Another campaign just took that name. Please try again."
                    ) from exc
            for name, rows in sections.items():
                try:
                    await self._sections[name].load(conn, guild_id, campaign_id, rows)
                except CampaignError:
                    raise
                except (ValueError, TypeError, KeyError, AttributeError, OverflowError) as exc:
                    raise CampaignError(DAMAGED) from exc
            await conn.execute(
                "INSERT INTO campaign_dms (campaign_id, guild_id, user_id) VALUES (%s, %s, %s)"
                " ON CONFLICT DO NOTHING",
                (campaign_id, guild_id, importer_id),
            )
            return await self._require(conn, guild_id, campaign_id)

    # ---- helpers (run inside a guild transaction) ----------------------------

    async def _set(self, guild_id: int, campaign_id: str, column: str, value: object) -> Campaign:
        if column not in _SETTABLE:
            raise ValueError(f"Not a settable campaign column: {column}")
        async with self._db.guild(guild_id) as conn:
            await self._require(conn, guild_id, campaign_id)
            await conn.execute(
                sql.SQL("UPDATE campaigns SET {} = %s WHERE guild_id = %s AND id = %s").format(
                    sql.Identifier(column)
                ),
                (value, guild_id, campaign_id),
            )
            return await self._require(conn, guild_id, campaign_id)

    async def _get(self, conn: Conn, guild_id: int, campaign_id: str) -> Campaign | None:
        cur = await conn.execute(
            "SELECT * FROM campaigns WHERE guild_id = %s AND id = %s", (guild_id, campaign_id)
        )
        row = await cur.fetchone()
        if row is None:
            return None
        dms = await self._dms_for(conn, guild_id, campaign_id)
        return _to_campaign(row, dms.get(campaign_id, set()))

    async def _require(self, conn: Conn, guild_id: int, campaign_id: str) -> Campaign:
        campaign = await self._get(conn, guild_id, campaign_id)
        if campaign is None:
            raise CampaignError(NOT_HERE)
        return campaign

    async def _dms_for(
        self, conn: Conn, guild_id: int, campaign_id: str | None = None
    ) -> dict[str, set[int]]:
        """DMs per campaign for one server (or one campaign), in a single query."""
        if campaign_id is None:
            cur = await conn.execute(
                "SELECT campaign_id, user_id FROM campaign_dms WHERE guild_id = %s", (guild_id,)
            )
        else:
            cur = await conn.execute(
                "SELECT campaign_id, user_id FROM campaign_dms"
                " WHERE guild_id = %s AND campaign_id = %s",
                (guild_id, campaign_id),
            )
        result: dict[str, set[int]] = {}
        for r in await cur.fetchall():
            result.setdefault(r["campaign_id"], set()).add(int(r["user_id"]))
        return result

    async def _insert(
        self,
        conn: Conn,
        guild_id: int,
        name: str,
        target: str,
        fallback: str,
        optional_default: bool,
        created_at: int,
        last_played_at: int | None,
        visibility: str,
        level: str,
    ) -> str:
        campaign_id = uuid.uuid4().hex
        await conn.execute(
            "INSERT INTO campaigns (id, guild_id, name, name_key, created_at, last_played_at,"
            " target_ruleset, fallback_ruleset, optional_rules_default, dm_screen_visibility,"
            " dm_screen_level) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                campaign_id,
                guild_id,
                name,
                name_key(name),
                created_at,
                last_played_at,
                target,
                fallback,
                optional_default,
                visibility,
                level,
            ),
        )
        return campaign_id

    async def _free_name(self, conn: Conn, guild_id: int, base: str) -> str:
        """`base`, or `base (restored)`, `base (restored 2)`, … whichever is free."""
        cur = await conn.execute("SELECT name_key FROM campaigns WHERE guild_id = %s", (guild_id,))
        taken = {r["name_key"] for r in await cur.fetchall()}
        if name_key(base) not in taken:
            return base
        for n in range(1, 1000):
            suffix = " (restored)" if n == 1 else f" (restored {n})"
            candidate = clean_name(base[: NAME_MAX - len(suffix)].rstrip() + suffix)
            if name_key(candidate) not in taken:
                return candidate
        raise CampaignError("Too many campaigns with that name. Rename one and try again.")


def _to_campaign(row: dict[str, Any], dms: set[int]) -> Campaign:
    return Campaign(
        id=row["id"],
        guild_id=int(row["guild_id"]),
        name=row["name"],
        created_at=int(row["created_at"]),
        last_played_at=row_int(row, "last_played_at"),
        target_ruleset=row["target_ruleset"],
        fallback_ruleset=row["fallback_ruleset"],
        optional_rules_default=bool(row["optional_rules_default"]),
        dm_user_ids=frozenset(dms),
        dm_screen_channel_id=row_int(row, "dm_screen_channel_id"),
        last_voice_channel_id=row_int(row, "last_voice_channel_id"),
        dm_screen_visibility=row["dm_screen_visibility"],
        channel_number=row_int(row, "channel_number"),
        transcript_channel_id=row_int(row, "transcript_channel_id"),
        dm_screen_level=row["dm_screen_level"],
    )


def encode_backup(backup: dict[str, Any]) -> bytes:
    """Serialise a backup for the DM to download: compressed JSON (#164), so a big
    campaign still fits in one Discord file. Raises BackupTooBig rather than make a copy
    a restore would refuse. Run off the event loop for big ones."""
    text = json.dumps(backup, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(text) > MAX_BACKUP_BYTES:
        raise BackupTooBig(CAMPAIGN_TOO_BIG)
    return gzip.compress(text, compresslevel=6, mtime=0)


def decode_backup(raw: bytes) -> object:
    """Parse an uploaded backup file, compressed or (older backups) plain, refusing
    oversized or non-JSON files."""
    if len(raw) > MAX_BACKUP_BYTES:
        raise CampaignError(TOO_BIG)
    if raw.startswith(GZIP_MAGIC):
        raw = _unpack(raw)
    try:
        # From bytes, json finds the encoding itself (UTF-8, or UTF-16/32 with a BOM);
        # it decodes to a str internally, so memory is the same as decoding here.
        return json.loads(raw)
    # ValueError also covers bad UTF-8, bad JSON, and a number too long for Python's
    # int limit (4,300 digits), which isn't a JSONDecodeError.
    except (ValueError, RecursionError) as exc:
        raise CampaignError(NOT_A_BACKUP) from exc


def _unpack(raw: bytes) -> bytes:
    """Unpack a compressed backup, stopping as soon as it would pass MAX_BACKUP_BYTES."""
    unpacker = zlib.decompressobj(wbits=16 + zlib.MAX_WBITS)  # gzip
    try:
        text = unpacker.decompress(raw, MAX_BACKUP_BYTES + 1)
    except zlib.error as exc:
        raise CampaignError(NOT_A_BACKUP) from exc
    if len(text) > MAX_BACKUP_BYTES:
        raise CampaignError(TOO_BIG)
    if not unpacker.eof or unpacker.unused_data:  # cut short, or more after the end
        raise CampaignError(NOT_A_BACKUP)
    return text


def _validate_backup(
    data: object, known_sections: set[str]
) -> tuple[dict[str, Any], dict[str, list[Any]]]:
    """Check a backup's shape before touching the database. Raises CampaignError."""
    if not isinstance(data, dict) or data.get("format") != EXPORT_FORMAT:
        raise CampaignError(DAMAGED)
    version = data.get("version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise CampaignError(DAMAGED)
    if version > EXPORT_VERSION:
        raise CampaignError("This backup was made by a newer DMbot. Update DMbot and try again.")
    campaign = data.get("campaign")
    sections = data.get("sections")
    if not isinstance(campaign, dict) or not isinstance(sections, dict):
        raise CampaignError(DAMAGED)

    raw_name = campaign.get("name")
    if not isinstance(raw_name, str):
        raise CampaignError(DAMAGED)
    target = campaign.get("target_ruleset")
    fallback = campaign.get("fallback_ruleset")
    if not isinstance(target, str) or not isinstance(fallback, str):
        raise CampaignError(DAMAGED)
    check_rulesets(target, fallback)

    def timestamp(value: object, *, optional: bool) -> int | None:
        if value is None and optional:
            return None
        if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= INT64_MAX:
            return value
        raise CampaignError(DAMAGED)

    optional_default = campaign.get("optional_rules_default", True)
    if not isinstance(optional_default, bool):
        raise CampaignError(DAMAGED)
    # Backups made before this setting existed fall back to the default.
    visibility = campaign.get("dm_screen_visibility", DEFAULT_DM_SCREEN_VISIBILITY)
    if not isinstance(visibility, str) or visibility not in DM_SCREEN_VISIBILITY:
        raise CampaignError(DAMAGED)
    level = campaign.get("dm_screen_level", DEFAULT_DM_SCREEN_LEVEL)
    if not isinstance(level, str) or level not in DM_SCREEN_LEVELS:
        raise CampaignError(DAMAGED)

    unknown = set(sections) - known_sections
    if unknown:
        raise CampaignError("This backup was made by a newer DMbot. Update DMbot and try again.")
    for rows in sections.values():
        if not isinstance(rows, list):
            raise CampaignError(DAMAGED)

    info = {
        "name": clean_name(raw_name),
        "created_at": timestamp(campaign.get("created_at"), optional=False),
        "last_played_at": timestamp(campaign.get("last_played_at"), optional=True),
        "target_ruleset": target,
        "fallback_ruleset": fallback,
        "optional_rules_default": optional_default,
        "dm_screen_visibility": visibility,
        "dm_screen_level": level,
    }
    return info, {name: list(rows) for name, rows in sections.items()}
