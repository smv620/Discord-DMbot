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
import dataclasses
import gzip
import inspect
import io
import json
import logging
import re
import time
import uuid
import zlib
from collections.abc import AsyncGenerator, Awaitable, Callable, Iterator, Sequence
from typing import Any, Literal, LiteralString, Protocol, cast

from psycopg import errors as pg_errors
from psycopg import sql

from dmbot import entitlements, fight_style
from dmbot.campaigns import offer_notify
from dmbot.campaigns.invite import parse_invite
from dmbot.campaigns.models import (
    CONFIRMATION_PURPOSES,
    DEFAULT_DM_SCREEN_LEVEL,
    DEFAULT_DM_SCREEN_VISIBILITY,
    DEFAULT_FALLBACK,
    DEFAULT_TARGET,
    DM_SCREEN_LEVELS,
    DM_SCREEN_VISIBILITY,
    HANDOVER_DAYS,
    HANDOVER_SECONDS,
    NAME_MAX,
    NAME_ON_OFFER_MAX,
    SHARED_STORY,
    STORY_MODES,
    STORY_NEEDS_CONFIRM,
    STORY_OFF,
    STORY_RIGHT_WARNING,
    Campaign,
    CampaignError,
    HandoverOffer,
    HandoverStatus,
    check_dm_screen_level,
    check_dm_screen_visibility,
    check_rulesets,
    clean_name,
    fingerprint,
    name_key,
)
from dmbot.campaigns.parts import (
    DAMAGED as TRANSCRIPTS_DAMAGED,
)
from dmbot.campaigns.parts import (
    PART_TEXT_BYTES,
    RESTORE_BOXES,
    SESSION_TOO_BIG,
    TOO_MANY_PARTS,
    LeftOut,
    TranscriptParts,
    decode_part,
    encode_part,
)
from dmbot.db import Conn, Database, row_int
from dmbot.logs import log_context

log = logging.getLogger(__name__)

EXPORT_FORMAT = "dmbot-campaign"
# 2: written compressed since #164; contents unchanged from 1. Plain files of either
# version still restore: the file's first bytes, not its version, say if it's packed.
# 3: carries the who-knows lists (`memory_knowers`, #2995). An older file has none, so its restore
# works them out from the old flags; a file of this version keeps the lists it holds, however few.
# 4: carries each session's notes (`session_notes`, #3026). An older file has none, and its restore
# simply has none; a DMbot that predates the section refuses a file of this version as too new.
EXPORT_VERSION = 4

RULE_ID_MAX = 64
# Discord IDs and Unix timestamps must fit Postgres BIGINT (signed 64-bit).
INT64_MAX = 2**63 - 1
# Upper bound on a backup's JSON, checked before parsing it: on the file, and on a
# compressed file while it's unpacked (so a tiny file can't unpack to gigabytes).
MAX_BACKUP_BYTES = 25 * 1024 * 1024
GZIP_MAGIC = b"\x1f\x8b"

NOT_HERE = "That campaign doesn't exist in this server."
PLAN_HOLDER_CHANGED = "Whose plan this campaign uses changed just now, so nothing was deleted."
NAME_TAKEN = "This server already has a campaign with that name."
DAMAGED = (
    "This backup file is damaged or isn't a DMbot campaign backup. "
    "Pick the file you got from `/dmbot backup`."
)
DAMAGED_DM = "This backup file is damaged (bad DM entry)."
DAMAGED_CONFIRMATION = "This backup file is damaged (bad confirmation)."
DAMAGED_OPTIONAL_RULE = "This backup file is damaged (bad optional rule)."
TOO_BIG = "That file is too big to be a DMbot backup. Pick the file you got from `/dmbot backup`."
NOT_A_BACKUP = (
    "That file isn't a DMbot campaign backup. Pick the file you got from `/dmbot backup`."
)
CAMPAIGN_TOO_BIG = "This campaign is too big for DMbot to copy yet. Nothing was lost."
PARTS_NOT_THIS_COPY = (
    "Those transcripts files don't belong to this copy. Use the ones DMbot gave with it."
)


NOT_THIS_DM = "Only this campaign's DM can replace it with a backup."


class PartsError(CampaignError):
    """The transcripts files are not exactly the set the copy came with (#2552): a file from
    another copy, repeated, or one missing."""


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
                raise CampaignError(DAMAGED_DM)
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
                raise CampaignError(DAMAGED_CONFIRMATION)
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
                raise CampaignError(DAMAGED_OPTIONAL_RULE)
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
        "gametime_channel_id",
        "dmbchat_channel_id",
        "dm_bot_channel_id",
        "dm_bot_message_id",
        "rules_channel_id",
        "npcs_channel_id",
        "dndbeyond_invite",
        "rules_cards",
        "side_initiative",
        "initiative_style",
        "transcript_keep_days",
        "transcript_deleted_noted",
        "quiet_stop_minutes",
        "recaps_text",
        "recaps_audio",
        "recaps_email",
    }
)
RECAP_SWITCHES = ("text", "audio", "email")  # the DM's recap choices (#1421)
QUIET_STOP_CHOICES = (5, 10, 15, 30)  # minutes of silence before a stop; no Off (#2288)
QUIET_STOP_DEFAULT = 10
QUIET_STOP_OLD = (0, 60, 90)  # the choices before #2288: a backup holding one loads as 30
KEEP_CHOICES = (None, 365, 90, 30)  # how long transcripts are kept (#1533); None is forever


# Hand-over (#437 part 1b): plain words for the person who tried.
NOT_THE_OWNER = "Only the campaign's owner can hand it over. Ask them to do it."
MOVE_UNDER_WAY = (
    "The new owner has accepted and DMbot is moving the campaign to their server. Nothing can be "
    "changed until that is done."
)
NO_OWNER_YET = (
    "This campaign has no owner yet. Whoever runs `/dmbot start` next will be asked to take it "
    "on (their plan pays for its hours)."
)
OFFER_TO_SELF = "This campaign is already yours. Pick someone else."
OFFER_WAITING = (
    "You already offered this campaign to someone. Take that offer back first (⚙️ Manage DMbot "
    "→ 🗂️ Campaign management, "
    f"then **Take back offer**), or wait: it ends by itself after {HANDOVER_DAYS} days."
)
NO_ROOM_TO_RESTORE = (
    "Your plan has no room for another campaign, so nothing was restored. Pause one or "
    "change your plan, then restore it again."
)
NOT_THE_OWNER_PAUSE = "Only the campaign's owner can pause or unpause it."
NO_ROOM_TO_UNPAUSE = (
    "Your plan has no room to unpause this campaign. Pause one of your other campaigns first "
    "(open it, then ⚙️ Manage DMbot → 🗂️ Campaign management → ⏸️ **Pause this campaign**)."
)
MAX_DMS = 5  # a campaign's game masters at most (#1600)
TOO_MANY_DMS = (
    f"A campaign can have at most {MAX_DMS} game masters. Remove one before adding another."
)
LAST_DM = "A campaign needs at least one game master. Add another first."
OWNER_STAYS = (
    "The campaign's owner can't be removed. Hand the campaign over first (only the owner can)."
)

AcceptResult = Literal["accepted", "no_free_slot", "gone"]
TakeResult = Literal["taken", "no_free_slot", "gone"]
# Whether a person may own one more campaign (conn, user id, now). By default having a
# plan that works is enough; with plans enforced the bot passes
# `campaign_cap.has_room_for_one_more`, which also counts what they own (#437 part 2c).
SlotCheck = Callable[[Conn, int, int], Awaitable[bool]]


async def plan_works(conn: Conn, user_id: int, now: int) -> bool:
    """They may run a campaign now: a paid plan that works, a grant or the free list
    (#435, #771; entitlements.effective)."""
    return (await entitlements.effective(conn, user_id, now)).works


class CampaignStore:
    def __init__(
        self,
        db: Database,
        *,
        clock: Callable[[], float] = time.time,
        has_free_slot: SlotCheck = plan_works,
        restore_needs_slot: bool = False,
    ) -> None:
        self._db = db
        self._clock = clock
        self._has_free_slot = has_free_slot
        # Restoring makes the importer an owner, which takes a slot. Checked only when the
        # bot enforces plans (DMBOT_ENFORCE_PLANS, #437).
        self._restore_needs_slot = restore_needs_slot
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

    @property
    def db(self) -> Database:
        return self._db

    def register_section(self, section: ExportSection) -> None:
        """Include another feature's per-campaign data in backups and deletes."""
        if section.name in self._sections:
            raise ValueError(f"Export section {section.name!r} is already registered")
        self._sections[section.name] = section

    # ---- reading ------------------------------------------------------------

    async def get(self, guild_id: int, campaign_id: str) -> Campaign | None:
        async with self._db.guild(guild_id) as conn:
            return await self._get(conn, guild_id, campaign_id)

    async def special_channel_ids(self, guild_id: int) -> frozenset[int]:
        """The DM-screen, transcript and game-time channels of every campaign in the server
        (#1118): none of them may be read as table chat, whichever campaign picks it, because
        DM-screen content never goes into a transcript."""
        async with self._db.guild(guild_id, snapshot=True) as conn:
            cur = await conn.execute(
                "SELECT dm_screen_channel_id, transcript_channel_id, gametime_channel_id,"
                " dmbchat_channel_id, dm_bot_channel_id, rules_channel_id,"
                " npcs_channel_id"
                " FROM campaigns WHERE guild_id = %s",
                (guild_id,),
            )
            return frozenset(
                int(v) for row in await cur.fetchall() for v in row.values() if v is not None
            )

    async def table_chat_channel_ids(self, guild_id: int) -> frozenset[int]:
        """The extra text channels the server's campaigns have picked to be read as table chat
        (#1118), while the switch is on. For finding where DMbot may have answered a picture."""
        async with self._db.guild(guild_id, snapshot=True) as conn:
            cur = await conn.execute(
                "SELECT table_chat_channel_id FROM campaigns WHERE guild_id = %s"
                " AND table_chat AND table_chat_channel_id IS NOT NULL",
                (guild_id,),
            )
            return frozenset(int(row["table_chat_channel_id"]) for row in await cur.fetchall())

    async def list_campaigns(self, guild_id: int) -> list[Campaign]:
        """All of a server's campaigns, most recently used first."""
        async with self._db.guild(guild_id, snapshot=True) as conn:
            cur = await conn.execute(
                "SELECT * FROM campaigns WHERE guild_id = %s" + _ORDER, (guild_id,)
            )
            rows = await cur.fetchall()
            dms = await self._dms_for(conn, guild_id)
        return [_to_campaign(r, dms.get(r["id"], set())) for r in rows]

    async def guilds_with_campaigns(self) -> list[int]:
        """Every server that has held a campaign, from the database and not from the bot's
        current servers, so the campaigns of a server DMbot has left are still reached
        (#989). Server IDs only."""
        async with self._db.unscoped() as conn:
            cur = await conn.execute("SELECT guild_id FROM campaign_guilds ORDER BY guild_id")
            return [int(r["guild_id"]) for r in await cur.fetchall()]

    async def campaign_ids(self, guild_id: int) -> set[str]:
        """The IDs of the server's campaigns (IDs only), for sweeping what a deleted one left."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute("SELECT id FROM campaigns WHERE guild_id = %s", (guild_id,))
            return {str(r["id"]) for r in await cur.fetchall()}

    async def has_campaign(self, guild_id: int, campaign_id: str) -> bool:
        """Whether this server has a campaign with this ID."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT 1 FROM campaigns WHERE guild_id = %s AND id = %s", (guild_id, campaign_id)
            )
            return await cur.fetchone() is not None

    async def exists_anywhere(self, campaign_id: str) -> bool:
        """Whether any server has a campaign with this ID, read fresh. A moved campaign keeps its
        ID, so this is how a sweep tells a deleted campaign from one that changed servers."""
        for guild_id in await self.guilds_with_campaigns():
            if await self.has_campaign(guild_id, campaign_id):
                return True
        return False

    async def guild_of(self, campaign_id: str) -> int | None:
        """The server a campaign is in now, read fresh from the database (#3210). A question sent
        to a DM's private chat has no server around it, and a lookup kept in memory would be
        lost on every restart, leaving old buttons dead. A moved campaign keeps its ID, so this
        finds it in its new server."""
        for guild_id in await self.guilds_with_campaigns():
            if await self.has_campaign(guild_id, campaign_id):
                return guild_id
        return None

    async def forget_guild_if_empty(self, guild_id: int) -> bool:
        """Drop a server from the list once it holds no campaigns and no website-delete note
        waiting for the bot (#2554). Check and delete are one statement in the server's own
        scope, so a campaign made a moment ago keeps it listed."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "DELETE FROM campaign_guilds WHERE guild_id = %s"
                " AND NOT EXISTS (SELECT 1 FROM campaigns WHERE guild_id = %s)"
                " AND NOT EXISTS (SELECT 1 FROM web_delete_notices WHERE guild_id = %s)"
                " RETURNING guild_id",
                (guild_id, guild_id, guild_id),
            )
            return await cur.fetchone() is not None

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
        dndbeyond_invite: str | None = None,
    ) -> Campaign:
        """`dndbeyond_invite`: the DM's D&D Beyond invite link (#1119), saved in the same
        transaction so a campaign is never made without the link the DM typed."""
        clean = clean_name(name)
        invite = None if dndbeyond_invite is None else parse_invite(dndbeyond_invite)
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
                    dm_user_id,
                )
                await conn.execute(
                    "INSERT INTO campaign_dms (campaign_id, guild_id, user_id) VALUES (%s, %s, %s)",
                    (campaign_id, guild_id, dm_user_id),
                )
                if invite is not None:
                    await conn.execute(
                        "UPDATE campaigns SET dndbeyond_invite = %s"
                        " WHERE guild_id = %s AND id = %s",
                        (invite, guild_id, campaign_id),
                    )
                return await self._require(conn, guild_id, campaign_id)
        except pg_errors.UniqueViolation as exc:
            raise CampaignError(NAME_TAKEN) from exc

    async def rename(self, guild_id: int, campaign_id: str, new_name: str) -> Campaign:
        clean = clean_name(new_name)
        try:
            async with self._db.guild(guild_id) as conn:
                return await self._update(
                    conn,
                    guild_id,
                    campaign_id,
                    sql.SQL("name = %s, name_key = %s"),
                    (clean, name_key(clean)),
                )
        except pg_errors.UniqueViolation as exc:
            raise CampaignError(NAME_TAKEN) from exc

    async def set_rulesets(
        self, guild_id: int, campaign_id: str, target: str, fallback: str
    ) -> Campaign:
        check_rulesets(target, fallback)
        async with self._db.guild(guild_id) as conn:
            return await self._update(
                conn,
                guild_id,
                campaign_id,
                sql.SQL("target_ruleset = %s, fallback_ruleset = %s"),
                (target, fallback),
            )

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
        """Make someone one of the campaign's DMs (already one: nothing changes). At most
        `MAX_DMS`; the campaign row is locked so two adds at once cannot pass the cap (#1600)."""
        async with self._db.guild(guild_id) as conn:
            # Read with the row locked (#77: one read), so two adds cannot pass the cap.
            current = await self._require(conn, guild_id, campaign_id, lock=True)
            if user_id not in current.dm_user_ids and len(current.dm_user_ids) >= MAX_DMS:
                raise CampaignError(TOO_MANY_DMS)
            await conn.execute(
                "INSERT INTO campaign_dms (campaign_id, guild_id, user_id) VALUES (%s, %s, %s)"
                " ON CONFLICT DO NOTHING",
                (campaign_id, guild_id, user_id),
            )
            # (no read after: the locked row cannot change under this transaction)
            return dataclasses.replace(current, dm_user_ids=current.dm_user_ids | {user_id})

    async def remove_dm(self, guild_id: int, campaign_id: str, user_id: int) -> Campaign:
        async with self._db.guild(guild_id) as conn:
            # Lock the campaign row so two removals can't leave it with no DM.
            campaign = await self._require(conn, guild_id, campaign_id, lock=True)
            if user_id == campaign.owner_user_id:  # whose plan it uses (#437)
                raise CampaignError(OWNER_STAYS)
            if user_id in campaign.dm_user_ids and len(campaign.dm_user_ids) == 1:
                raise CampaignError(LAST_DM)
            await conn.execute(
                "DELETE FROM campaign_dms"
                " WHERE guild_id = %s AND campaign_id = %s AND user_id = %s",
                (guild_id, campaign_id, user_id),
            )
            return dataclasses.replace(campaign, dm_user_ids=campaign.dm_user_ids - {user_id})

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

    async def set_gametime_channel(
        self, guild_id: int, campaign_id: str, channel_id: int | None
    ) -> Campaign:
        """Store the campaign's game-time channel (#1116): not part of a backup."""
        return await self._set(guild_id, campaign_id, "gametime_channel_id", channel_id)

    async def set_dmbchat_channel(
        self, guild_id: int, campaign_id: str, channel_id: int | None
    ) -> Campaign:
        """Store the campaign's DMbot chat channel (#1187): not part of a backup."""
        return await self._set(guild_id, campaign_id, "dmbchat_channel_id", channel_id)

    async def set_rules_channel(
        self, guild_id: int, campaign_id: str, channel_id: int | None
    ) -> Campaign:
        """Store the campaign's rules archive channel (#1752): not part of a backup."""
        return await self._set(guild_id, campaign_id, "rules_channel_id", channel_id)

    async def set_npcs_channel(
        self, guild_id: int, campaign_id: str, channel_id: int | None
    ) -> Campaign:
        """Store the campaign's NPC roster channel (#1761): not part of a backup."""
        return await self._set(guild_id, campaign_id, "npcs_channel_id", channel_id)

    async def set_dm_bot_channel(
        self, guild_id: int, campaign_id: str, channel_id: int | None
    ) -> Campaign:
        """Store the campaign's DM Bot channel (#1666): not part of a backup. A new channel
        starts with no panel message, so the old message id goes with the old channel."""
        current = await self.get(guild_id, campaign_id)
        if current is not None and current.dm_bot_channel_id == channel_id:
            return current  # the same channel again (every start): its panel message stays
        await self._set(guild_id, campaign_id, "dm_bot_message_id", None)
        return await self._set(guild_id, campaign_id, "dm_bot_channel_id", channel_id)

    async def set_dm_bot_message(
        self, guild_id: int, campaign_id: str, message_id: int | None
    ) -> Campaign:
        """Store the id of the DM Bot channel's one panel message (#1666)."""
        return await self._set(guild_id, campaign_id, "dm_bot_message_id", message_id)

    async def set_table_chat(
        self, guild_id: int, campaign_id: str, *, on: bool, channel_id: int | None
    ) -> Campaign:
        """Typed chat at the table (#1118): the DM's switch and the one extra text channel
        (None = only the voice channel's own chat). Not part of a backup: a channel ID means
        nothing in another server."""
        async with self._db.guild(guild_id) as conn:
            await self._require(conn, guild_id, campaign_id)
            await conn.execute(  # one statement: the two never get out of step
                "UPDATE campaigns SET table_chat = %s, table_chat_channel_id = %s"
                " WHERE guild_id = %s AND id = %s",
                (on, channel_id, guild_id, campaign_id),
            )
            return await self._require(conn, guild_id, campaign_id)

    async def set_dndbeyond_invite(
        self, guild_id: int, campaign_id: str, invite: str | None
    ) -> Campaign:
        """Keep (or, with None, remove) the DM's D&D Beyond invite link (#1119). Checked here
        too, so no caller can store another kind of address."""
        return await self._set(
            guild_id,
            campaign_id,
            "dndbeyond_invite",
            None if invite is None else parse_invite(invite),
        )

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

    async def set_initiative_style(
        self, guild_id: int, campaign_id: str, style: str | None
    ) -> Campaign:
        """Keep the initiative style a DM's words set (#1784), or forget it (None). The caller
        writes only when it changes."""
        return await self._set(guild_id, campaign_id, "initiative_style", style)

    async def set_side_initiative(self, guild_id: int, campaign_id: str, on: bool) -> Campaign:
        """Side-based initiative (#1127): a DM's setting, off by default. DMbot tracks rounds
        only either way; it is kept so the table's way of running a fight is recorded."""
        return await self._set(guild_id, campaign_id, "side_initiative", bool(on))

    async def story_confirmed(self, guild_id: int, campaign_id: str) -> bool:
        """Has a DM already confirmed the right to use shared story material here (#1692)?
        Switching the story mode off keeps that record, so turning it on again doesn't ask."""
        async with self._db.guild(guild_id) as conn:
            await self._require(conn, guild_id, campaign_id)
            return await self._has_story_confirmation(conn, guild_id, campaign_id)

    @staticmethod
    async def _has_story_confirmation(conn: Conn, guild_id: int, campaign_id: str) -> bool:
        cur = await conn.execute(
            "SELECT 1 FROM shared_confirmations"
            " WHERE guild_id = %s AND campaign_id = %s AND purpose = %s"
            # A row that came from a backup file says what the file says, not what a DM pressed
            # here, so it never stands in for the confirmation (CLAUDE.md, IP rule).
            " AND NOT restored LIMIT 1",
            (guild_id, campaign_id, SHARED_STORY),
        )
        return await cur.fetchone() is not None

    async def set_story_mode(
        self, guild_id: int, campaign_id: str, mode: str, *, confirmed_by: int | None = None
    ) -> Campaign:
        """The Shared story switch (#1692): "off", "watch" or "guide". Turning it on needs the
        DM's confirmation of the right to use the material (CLAUDE.md, IP rule): already on
        record, or given now with `confirmed_by` (who; the time is the store's clock). Without
        one the mode is not changed and `STORY_NEEDS_CONFIRM` is raised. Off never asks and
        keeps the record. The record and the mode are written together. Nothing reads the mode
        yet (the later slices of Phase 5d)."""
        if mode not in STORY_MODES:
            raise CampaignError("That isn't a Shared story setting.")
        async with self._db.guild(guild_id) as conn:
            await self._require(conn, guild_id, campaign_id)
            if mode != STORY_OFF and not await self._has_story_confirmation(
                conn, guild_id, campaign_id
            ):
                if confirmed_by is None:
                    raise CampaignError(STORY_NEEDS_CONFIRM)
                await conn.execute(
                    "INSERT INTO shared_confirmations (campaign_id, guild_id, id, user_id,"
                    " purpose, fingerprint, confirmed_at) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                    (
                        campaign_id,
                        guild_id,
                        uuid.uuid4().hex,
                        confirmed_by,
                        SHARED_STORY,
                        fingerprint(STORY_RIGHT_WARNING),
                        int(self._clock()),
                    ),
                )
            return await self._update(
                conn,
                guild_id,
                campaign_id,
                sql.SQL("story_mode = %s"),
                (mode,),
            )

    async def set_transcript_keep(
        self, guild_id: int, campaign_id: str, days: int | None
    ) -> Campaign:
        """How long the campaign keeps its transcripts (#1533): None (forever), 365, 90 or 30
        days after a session ended. Changing it lets the DM screen say once again that old
        transcripts were deleted."""
        if days not in KEEP_CHOICES:
            raise CampaignError("Pick one of the choices: Forever, 1 year, 90 days or 30 days.")
        async with self._db.guild(guild_id) as conn:
            await self._require(conn, guild_id, campaign_id)
            await conn.execute(
                "UPDATE campaigns SET transcript_keep_days = %s, transcript_deleted_noted = FALSE"
                " WHERE guild_id = %s AND id = %s",
                (days, guild_id, campaign_id),
            )
            return await self._require(conn, guild_id, campaign_id)

    async def set_quiet_stop(self, guild_id: int, campaign_id: str, minutes: int) -> Campaign:
        """Minutes of silence before DMbot stops the session itself (#2034): 5, 10, 15 or 30,
        never off (#2288). A running session follows the change from its next check."""
        if minutes not in QUIET_STOP_CHOICES:
            raise CampaignError("Pick one of the choices: 5, 10, 15 or 30 minutes.")
        async with self._db.guild(guild_id) as conn:
            await self._require(conn, guild_id, campaign_id)
            await conn.execute(
                "UPDATE campaigns SET quiet_stop_minutes = %s WHERE guild_id = %s AND id = %s",
                (minutes, guild_id, campaign_id),
            )
            return await self._require(conn, guild_id, campaign_id)

    async def mark_transcript_deletion_noted(self, guild_id: int, campaign_id: str) -> bool:
        """The DM screen has now said that old transcripts were deleted. True if this call was
        the first to mark it, so the note is posted once even if two checks race."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE campaigns SET transcript_deleted_noted = TRUE"
                " WHERE guild_id = %s AND id = %s AND NOT transcript_deleted_noted",
                (guild_id, campaign_id),
            )
            return bool(cur.rowcount)

    async def set_recap_switch(
        self, guild_id: int, campaign_id: str, which: str, on: bool
    ) -> Campaign:
        """One of the DM's recap choices (#1421): "text", "audio" or "email"."""
        if which not in RECAP_SWITCHES:
            raise ValueError(which)
        return await self._set(guild_id, campaign_id, f"recaps_{which}", bool(on))

    async def set_rules_cards(self, guild_id: int, campaign_id: str, on: bool) -> Campaign:
        """Whether rules cards show on the DM screen when a spell or creature is named
        (#931). Who may change it is checked by the caller (the campaign's DMs)."""
        return await self._set(guild_id, campaign_id, "rules_cards", bool(on))

    async def set_paused(
        self, guild_id: int, campaign_id: str, user_id: int, paused: bool, now: int
    ) -> Campaign:
        """Pause or unpause a campaign (#957). Only its owner may: it is their plan's room
        that changes. Unpausing is a campaign more under the cap, so it needs room (checked
        here as the backstop; the bot checks first to give the plan's own words). Raises
        CampaignError in plain words."""
        async with self._db.guild(guild_id) as conn:
            await self._lock(conn, guild_id, campaign_id)
            campaign = await self._require(conn, guild_id, campaign_id)
            if campaign.owner_user_id is None or campaign.owner_user_id != user_id:
                raise CampaignError(NOT_THE_OWNER_PAUSE)
            if campaign.paused == paused:
                return campaign
            if not paused and not await self._has_free_slot(conn, user_id, now):
                raise CampaignError(NO_ROOM_TO_UNPAUSE)
            await conn.execute(
                "UPDATE campaigns SET paused = %s WHERE guild_id = %s AND id = %s",
                (paused, guild_id, campaign_id),
            )
            return await self._require(conn, guild_id, campaign_id)

    async def set_retention_warned(
        self, guild_id: int, campaign_id: str, stage: int, delete_at: int | None
    ) -> None:
        """Remember that the retention warning of this stage went out for this delete date
        (#964), so it isn't sent twice."""
        async with self._db.guild(guild_id) as conn:
            await conn.execute(
                "UPDATE campaigns SET retention_warned_stage = %s, retention_warned_for = %s"
                " WHERE guild_id = %s AND id = %s",
                (stage, delete_at, guild_id, campaign_id),
            )

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
            return await self._update(
                conn,
                guild_id,
                campaign_id,
                sql.SQL("channel_number = %s"),
                (number,),
                also=sql.SQL("channel_number IS NULL"),
            )

    async def mark_played(self, guild_id: int, campaign_id: str) -> Campaign:
        return await self._set(guild_id, campaign_id, "last_played_at", int(self._clock()))

    async def record_start(
        self, guild_id: int, campaign_id: str, screen_id: int, voice_id: int
    ) -> Campaign:
        """What a session start records, in one transaction and one UPDATE (#77): the DM screen
        channel, the voice channel, and now as the last time it was played. It used to be three
        separate writes (three transactions)."""
        async with self._db.guild(guild_id) as conn:
            return await self._update(
                conn,
                guild_id,
                campaign_id,
                sql.SQL(
                    "dm_screen_channel_id = %s, last_voice_channel_id = %s, last_played_at = %s"
                ),
                (screen_id, voice_id, int(self._clock())),
            )

    async def delete(
        self, guild_id: int, campaign_id: str, *, only_if_owner: int | None = None
    ) -> None:
        """Delete the campaign and all it holds. `only_if_owner` (#1608): the person deleting
        by hand; checked again here, under the campaign's lock, that they still hold its plan
        and are still a game master, so a hand-over between their last look and this delete
        can't let the old owner delete it."""
        async with self._db.guild(guild_id) as conn:
            # Lock the campaign first, as campaign-memory writes do, so the two can't
            # deadlock while the sections clear their rows.
            await self._lock(conn, guild_id, campaign_id)
            current = await self._require(conn, guild_id, campaign_id)
            # The holder is the owner or, with no owner yet, any game master (#2556); this is the
            # check of `delete_campaign.holder_may`, made again under the lock.
            if only_if_owner is not None and (
                only_if_owner not in current.dm_user_ids
                or (current.owner_user_id is not None and current.owner_user_id != only_if_owner)
            ):
                raise CampaignError(PLAN_HOLDER_CHANGED)
            had_session = await self.delete_in(conn, guild_id, campaign_id)
        if had_session:
            with log_context(guild_id=guild_id, campaign_id=campaign_id):
                log.info("Saved session removed: its campaign was deleted")

    async def move_in(
        self,
        conn: Conn,
        guild_id: int,
        target: int,
        campaign_id: str,
        *,
        new_owner: int,
        old_owner: int,
        consenting: frozenset[int],
    ) -> str:
        """Move the campaign to another server, inside a transaction the caller holds (and has
        locked the campaign in; #2532). Returns its name in the new server.

        The campaign's own row changes server and migration 0215's foreign keys carry every row
        that hangs off it, so nothing is read back and written again and nothing can be left
        behind (tests/test_campaign_tables.py lists the tables; tests/test_campaign_move.py proves
        them). What belongs to the old server is reset: channels and messages, which the new
        owner makes again with /dmbot start; who recorded is not carried (see `consenting`)."""
        await _scope(conn, guild_id)
        cur = await conn.execute(
            "SELECT name FROM campaigns WHERE guild_id = %s AND id = %s", (guild_id, campaign_id)
        )
        row = await cur.fetchone()
        if row is None:
            raise CampaignError(NOT_HERE)
        await _scope(conn, target)
        name = await self._free_name(conn, target, str(row["name"]), "moved")  # a clash is renamed
        await _scope(conn, guild_id)
        # What is only about the old server's Discord messages is dropped before the move.
        await conn.execute(
            "DELETE FROM npcs_channel_messages WHERE guild_id = %s AND campaign_id = %s",
            (guild_id, campaign_id),
        )
        await conn.execute(
            "UPDATE game_clocks SET channel_id = NULL, message_id = NULL,"
            " players_channel_id = NULL, players_message_id = NULL"
            " WHERE guild_id = %s AND campaign_id = %s",
            (guild_id, campaign_id),
        )
        await conn.execute(
            "UPDATE map_notes SET message_id = 0 WHERE guild_id = %s AND campaign_id = %s",
            (guild_id, campaign_id),
        )
        await conn.execute("SELECT set_config('dmbot.moving_campaign', %s, true)", (campaign_id,))
        await conn.execute("SELECT set_config('dmbot.moving_to', %s, true)", (str(target),))
        cur = await conn.execute(
            "UPDATE campaigns SET guild_id = %s, name = %s, name_key = %s, owner_user_id = %s,"
            " channel_number = NULL, dm_screen_channel_id = NULL, transcript_channel_id = NULL,"
            " gametime_channel_id = NULL, dmbchat_channel_id = NULL, dm_bot_channel_id = NULL,"
            " dm_bot_message_id = NULL, rules_channel_id = NULL, npcs_channel_id = NULL,"
            " last_voice_channel_id = NULL, table_chat_channel_id = NULL,"
            " dndbeyond_invite = NULL, owner_left_at = NULL, owner_missing_since = NULL,"
            " retention_warned_stage = 0, retention_warned_for = NULL,"
            " memory_version = memory_version + 1"
            " WHERE guild_id = %s AND id = %s RETURNING id",
            (target, name, name_key(name), new_owner, guild_id, campaign_id),
        )
        if await cur.fetchone() is None:
            raise CampaignError(NOT_HERE)
        await _scope(conn, target)  # from here the campaign is the new server's
        await conn.execute(
            "INSERT INTO campaign_dms (campaign_id, guild_id, user_id)"
            " SELECT %s, %s, u FROM unnest(%s::bigint[]) AS u ON CONFLICT DO NOTHING",
            (campaign_id, target, [new_owner, old_owner]),  # the old owner stays a DM
        )
        # Counts of names said are kept only for who is recorded in this server (#1747).
        await conn.execute(
            "DELETE FROM memory_heard WHERE guild_id = %s AND campaign_id = %s"
            " AND NOT (speaker_id = ANY(%s))",
            (target, campaign_id, sorted(consenting)),
        )
        return name

    async def delete_in(self, conn: Conn, guild_id: int, campaign_id: str) -> bool:
        """The deletion itself, inside a transaction the caller holds (and has locked the
        campaign in). True if a saved session went with it."""
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
            await conn.execute("DELETE FROM live_session_guilds WHERE guild_id = %s", (guild_id,))
        await conn.execute(
            "DELETE FROM campaigns WHERE guild_id = %s AND id = %s", (guild_id, campaign_id)
        )
        return had_session

    # ---- hand-over (#437 part 1b) --------------------------------------------
    # One place for every hand-over write, used by the bot's buttons and the website's
    # account page alike, so their rules can't drift apart. The campaign row is locked
    # first in each, like every other campaign write, so two can't cross.

    async def offer_handover(
        self,
        guild_id: int,
        campaign_id: str,
        from_user_id: int,
        to_user_id: int,
        now: int,
        *,
        from_name: str,
        to_name: str,
        delivered: bool = True,
    ) -> HandoverOffer:
        """The owner offers the campaign to another member, who accepts or not within 7
        days. Never refused for the other person's plan: the owner mustn't learn whether
        someone pays (#437 point 2, #713); Accept checks their plan has room. The names
        are the two people's display names now, kept on the offer for the account page.
        The bot checks the person offered is in this server when it delivers the offer,
        and withdraws it if they can't be reached (#437). Raises CampaignError in plain
        words.

        `delivered`: the caller sends the private message itself, now (the Discord
        button). The website passes False: the offer is saved as not sent yet and the bot
        is told, when this commits, to send it (#690)."""
        names = _offer_name(from_name), _offer_name(to_name)
        async with self._db.guild(guild_id) as conn:
            # The campaign lock is what keeps an old owner from offering again while an
            # accept commits: their old offer is no longer open, so the one-open-offer
            # index wouldn't stop a new one from someone who isn't the owner any more.
            await self._lock(conn, guild_id, campaign_id)
            campaign = await self._require(conn, guild_id, campaign_id)
            if campaign.owner_user_id is None:
                raise CampaignError(NO_OWNER_YET)
            if campaign.owner_user_id != from_user_id:
                raise CampaignError(NOT_THE_OWNER)
            if to_user_id == from_user_id:
                raise CampaignError(OFFER_TO_SELF)
            # An open offer past its 7 days no longer counts: mark it, so the new one can
            # be the campaign's one open offer.
            await conn.execute(
                "UPDATE campaign_handover_offers SET status = 'expired',"
                " decided_at = created_at + %s"
                " WHERE guild_id = %s AND campaign_id = %s AND status = 'open'"
                " AND created_at + %s <= %s",
                (HANDOVER_SECONDS, guild_id, campaign_id, HANDOVER_SECONDS, now),
            )
            if await self._open_offer(conn, guild_id, campaign_id) is not None:
                raise CampaignError(OFFER_WAITING)
            try:  # the index is the last word, should a writer ever skip the lock
                cur = await conn.execute(
                    "INSERT INTO campaign_handover_offers (guild_id, campaign_id,"
                    " from_user_id, to_user_id, from_name, to_name, created_at, delivered_at)"
                    " VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING *",
                    (
                        guild_id, campaign_id, from_user_id, to_user_id, *names, now,
                        now if delivered else None,
                    ),
                )  # fmt: skip
            except pg_errors.UniqueViolation as exc:
                raise CampaignError(OFFER_WAITING) from exc
            row = await cur.fetchone()
            assert row is not None
            offer = _to_offer(row)
            if not delivered:
                await offer_notify.send(conn, guild_id, offer.id)
            return offer

    async def offer_handover_by_email(
        self,
        guild_id: int,
        campaign_id: str,
        from_user_id: int,
        now: int,
        *,
        from_name: str,
        token_hash: str,
    ) -> HandoverOffer:
        """The owner offers the campaign by email (#2532): to whoever opens the one-use link
        on the website and accepts, not to a Discord account. The offer holds only a SHA-256
        of the link's token (`token_hash`) and no address. `to_user_id` is 0, which matches no
        one, until the website's claim sets it. A new offer takes back the campaign's open
        one, whichever way it was made. Raises CampaignError in plain words."""
        async with self._db.guild(guild_id) as conn:
            await self._lock(conn, guild_id, campaign_id)
            campaign = await self._require(conn, guild_id, campaign_id)
            if campaign.owner_user_id is None:
                raise CampaignError(NO_OWNER_YET)
            if campaign.owner_user_id != from_user_id:
                raise CampaignError(NOT_THE_OWNER)
            moving = await conn.execute(
                "SELECT 1 FROM campaign_handover_offers WHERE guild_id = %s AND campaign_id = %s"
                " AND status = 'open' AND move_to_guild_id IS NOT NULL",
                (guild_id, campaign_id),
            )
            if await moving.fetchone() is not None:  # a move that was asked for is not taken back
                raise CampaignError(MOVE_UNDER_WAY)
            await conn.execute(
                "UPDATE campaign_handover_offers SET status = 'withdrawn', decided_at = %s"
                " WHERE guild_id = %s AND campaign_id = %s AND status = 'open'",
                (now, guild_id, campaign_id),
            )
            try:  # the index is the last word, should a writer ever skip the lock
                cur = await conn.execute(
                    "INSERT INTO campaign_handover_offers (guild_id, campaign_id, from_user_id,"
                    " to_user_id, from_name, to_name, created_at, delivered_at, via_email,"
                    " token_hash) VALUES (%s, %s, %s, 0, %s, '', %s, %s, true, %s) RETURNING *",
                    (
                        guild_id,
                        campaign_id,
                        from_user_id,
                        _offer_name(from_name),
                        now,
                        now,
                        token_hash,
                    ),
                )  # (delivered_at: nothing is sent in Discord for it)
            except pg_errors.UniqueViolation as exc:
                raise CampaignError(OFFER_WAITING) from exc
            row = await cur.fetchone()
            assert row is not None
            return _to_offer(row)

    async def claim_delivery(self, guild_id: int, offer_id: int, now: int) -> HandoverOffer | None:
        """Claim an offer made on the website for a few minutes, just before the bot sends
        its private message (#690). None if it's sent, or claimed by someone else (another
        process, or the sweep and a notification together), or isn't open any more: then
        nothing is sent. After sending, `confirm_delivery`; if the send failed,
        `release_delivery`."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE campaign_handover_offers SET claimed_at = %s"
                " WHERE guild_id = %s AND id = %s AND " + _UNSENT + " RETURNING *",
                (now, guild_id, offer_id, now - CLAIM_SECONDS, HANDOVER_SECONDS, now),
            )
            row = await cur.fetchone()
        return None if row is None else _to_offer(row)

    async def confirm_delivery(
        self, guild_id: int, offer: HandoverOffer, now: int, message_id: int
    ) -> HandoverOffer | None:
        """The private message went out: the offer is sent. Only while this claim (the
        one in `offer`) still holds: a send that took longer than CLAIM_SECONDS may
        already have been claimed and sent again by someone else, so it can be sent
        twice (the second Accept just says it's settled). Returns the offer as it now
        stands (None if this claim no longer held): its status may have changed while
        the message went out (#797)."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE campaign_handover_offers SET delivered_at = %s, message_id = %s"
                " WHERE guild_id = %s AND id = %s AND delivered_at IS NULL AND claimed_at = %s"
                " RETURNING *",
                (now, message_id, guild_id, offer.id, offer.claimed_at),
            )
            row = await cur.fetchone()
        return None if row is None else _to_offer(row)

    async def set_offer_message(self, guild_id: int, offer_id: int, message_id: int) -> None:
        """The private message an offer made in Discord went out in (#690)."""
        async with self._db.guild(guild_id) as conn:
            await conn.execute(
                "UPDATE campaign_handover_offers SET message_id = %s"
                " WHERE guild_id = %s AND id = %s",
                (message_id, guild_id, offer_id),
            )

    async def offers_to_end(self, guild_id: int, now: int) -> list[HandoverOffer]:
        """Offers in this server whose 7 days are up and whose owner hasn't been told,
        oldest first. Open ones past their days are marked expired first (#690). Before
        telling the owner, `claim_end_notice` each."""
        async with self._db.guild(guild_id) as conn:
            await conn.execute(
                "UPDATE campaign_handover_offers SET status = 'expired',"
                " decided_at = created_at + %s"
                " WHERE guild_id = %s AND status = 'open' AND created_at + %s <= %s"
                " AND move_to_guild_id IS NULL",  # (a move that was asked for runs to its end)
                (HANDOVER_SECONDS, guild_id, HANDOVER_SECONDS, now),
            )
            cur = await conn.execute(
                "SELECT * FROM campaign_handover_offers"
                " WHERE guild_id = %s AND status = 'expired' AND end_told_at IS NULL"
                " ORDER BY id",
                (guild_id,),
            )
            return [_to_offer(row) for row in await cur.fetchall()]

    async def claim_end_notice(self, guild_id: int, offer_id: int, now: int) -> bool:
        """Mark an ended offer told, just before telling its owner: False if someone else
        got there first (so it's told once)."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE campaign_handover_offers SET end_told_at = %s"
                " WHERE guild_id = %s AND id = %s AND end_told_at IS NULL RETURNING id",
                (now, guild_id, offer_id),
            )
            return await cur.fetchone() is not None

    async def release_end_notice(self, guild_id: int, offer_id: int, told_at: int) -> None:
        """Telling the owner failed for a moment: the next sweep tries again."""
        async with self._db.guild(guild_id) as conn:
            await conn.execute(
                "UPDATE campaign_handover_offers SET end_told_at = NULL"
                " WHERE guild_id = %s AND id = %s AND end_told_at = %s",
                (guild_id, offer_id, told_at),
            )

    async def release_delivery(self, guild_id: int, offer: HandoverOffer) -> None:
        """The send failed: let this claim go so a later sweep tries again (unless
        someone claimed it since, once this claim had lapsed)."""
        async with self._db.guild(guild_id) as conn:
            await conn.execute(
                "UPDATE campaign_handover_offers SET claimed_at = NULL"
                " WHERE guild_id = %s AND id = %s AND claimed_at = %s"
                " AND delivered_at IS NULL",
                (guild_id, offer.id, offer.claimed_at),
            )

    async def undelivered_offers(self, guild_id: int, now: int) -> list[int]:
        """Open offers in this server waiting for their private message and not claimed
        (or whose claim lapsed), oldest first: the bot's sweeps (#690)."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT id FROM campaign_handover_offers"
                " WHERE guild_id = %s AND " + _UNSENT + " ORDER BY id",
                (guild_id, now - CLAIM_SECONDS, HANDOVER_SECONDS, now),
            )
            return [int(row["id"]) for row in await cur.fetchall()]

    def listen(
        self, channel: str, on_listening: Callable[[], None] | None = None
    ) -> AsyncGenerator[str, None]:
        """Offer notifications (for `SiteOffers.follow`): see `Database.listen`."""
        return self._db.listen(channel, on_listening)

    async def accept_handover(
        self, guild_id: int, offer_id: int, user_id: int, now: int, *, announce: bool = False
    ) -> AcceptResult:
        """The person offered takes the campaign: it uses their plan from now, and they
        become one of its DMs (the old owner stays one). "gone" if the offer isn't
        theirs, was answered or withdrawn, expired, or the campaign changed owner since;
        "no_free_slot" if their plan has no room now (checked now, not when offered).
        `announce`: answered on the website, so the bot is told, when this commits, to
        tell the owner in Discord (#737)."""
        async with self._db.guild(guild_id) as conn:
            offer = await self._offer(conn, guild_id, offer_id)
            if offer is None or offer.to_user_id != user_id:
                return "gone"
            await self._lock(conn, guild_id, offer.campaign_id)
            offer = await self._offer(conn, guild_id, offer_id, lock=True)
            campaign = await self._get(conn, guild_id, offer.campaign_id) if offer else None
            if offer is not None:
                await self._expire(conn, offer, now)
            if (
                offer is None
                or not offer.is_open(now)
                or campaign is None
                or campaign.owner_user_id != offer.from_user_id
            ):
                return "gone"
            if not await self._has_free_slot(conn, user_id, now):
                return "no_free_slot"
            await conn.execute(
                "UPDATE campaigns SET owner_user_id = %s WHERE guild_id = %s AND id = %s",
                (user_id, guild_id, campaign.id),
            )
            await conn.execute(
                "INSERT INTO campaign_dms (campaign_id, guild_id, user_id) VALUES (%s, %s, %s)"
                " ON CONFLICT DO NOTHING",
                (campaign.id, guild_id, user_id),
            )
            # Last on purpose: the website's policies (schema, web_accept_handover) allow
            # the owner and DM writes above only while this offer is still open.
            await self._decide(conn, guild_id, offer_id, "accepted", now)
            if announce:
                await offer_notify.send(conn, guild_id, offer_id, offer_notify.DECIDED)
            return "accepted"

    async def decline_handover(
        self, guild_id: int, offer_id: int, user_id: int, now: int, *, announce: bool = False
    ) -> Literal["declined", "gone"]:
        """The person offered says no thanks (`announce`: see accept_handover)."""
        closed = await self._close_offer(
            guild_id, offer_id, user_id, now, "declined", announce=announce
        )
        return "declined" if closed else "gone"

    async def withdraw_handover(
        self, guild_id: int, offer_id: int, user_id: int, now: int, *, announce: bool = False
    ) -> Literal["withdrawn", "gone"]:
        """The owner takes the offer back before it's answered (`announce`: see
        accept_handover; then the person offered is told)."""
        closed = await self._close_offer(
            guild_id, offer_id, user_id, now, "withdrawn", announce=announce
        )
        return "withdrawn" if closed else "gone"

    async def get_offer(self, guild_id: int, offer_id: int, now: int) -> HandoverOffer | None:
        """The offer as it stands now: one past its 7 days reads as expired, never open."""
        async with self._db.guild(guild_id) as conn:
            offer = await self._offer(conn, guild_id, offer_id)
        if offer is not None and offer.status == "open" and now >= offer.expires_at:
            return dataclasses.replace(offer, status="expired", decided_at=offer.expires_at)
        return offer

    async def open_offer(self, guild_id: int, campaign_id: str, now: int) -> HandoverOffer | None:
        """The campaign's offer waiting for an answer, if any (not one past its 7 days)."""
        async with self._db.guild(guild_id) as conn:
            offer = await self._open_offer(conn, guild_id, campaign_id)
        waiting = offer is not None and (
            offer.is_open(now) or offer.move_to_guild_id is not None  # (a move runs to its end)
        )
        return offer if waiting else None

    async def take_ownership(
        self, guild_id: int, campaign_id: str, user_id: int, now: int
    ) -> TakeResult:
        """A DM takes on a campaign that has no owner yet (from before owners were
        recorded and with several DMs, #437): it uses their plan from now. "gone" if it
        has an owner by now or they aren't one of its DMs."""
        async with self._db.guild(guild_id) as conn:
            await self._lock(conn, guild_id, campaign_id)
            campaign = await self._get(conn, guild_id, campaign_id)
            if (
                campaign is None
                or campaign.owner_user_id is not None
                or user_id not in campaign.dm_user_ids
            ):
                return "gone"
            if not await self._has_free_slot(conn, user_id, now):
                return "no_free_slot"
            # Taking it on also ends the 120-day countdown a deleted account started (#1059).
            await conn.execute(
                "UPDATE campaigns SET owner_user_id = %s, owner_left_at = NULL,"
                " owner_missing_since = NULL WHERE guild_id = %s AND id = %s",
                (user_id, guild_id, campaign_id),
            )
            return "taken"

    async def note_owner_presence(
        self, guild_id: int, campaign_id: str, owner_id: int, *, missing_since: int | None
    ) -> bool:
        """Record when the owner was first missed, or clear it with None (#1214). Only while
        `owner_id` is still the owner; False if the campaign or its owner changed meanwhile."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE campaigns SET owner_missing_since = %s"
                " WHERE guild_id = %s AND id = %s AND owner_user_id = %s",
                (missing_since, guild_id, campaign_id, owner_id),
            )
            return bool(cur.rowcount > 0)

    async def orphan_missing_owner(
        self,
        guild_id: int,
        campaign_id: str,
        owner_id: int,
        now: int,
        *,
        still_missing_since: int | None = None,
        any_state: bool = False,
    ) -> bool:
        """The owner is gone for good: the campaign becomes ownerless, as when a DMbot account
        is deleted (#1059), and its 120 days start (#1214). Only while `owner_id` is still the
        owner and (unless `any_state`: a deleted account can't come back) while the date the
        owner was first missed is still `still_missing_since`: an owner seen again, which
        cleared it, is not ended by an older look. True if it changed."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE campaigns SET owner_user_id = NULL, owner_left_at = %s,"
                " owner_missing_since = NULL"
                " WHERE guild_id = %s AND id = %s AND owner_user_id = %s"
                " AND (%s OR owner_missing_since IS NOT DISTINCT FROM %s)",
                (now, guild_id, campaign_id, owner_id, any_state, still_missing_since),
            )
            return bool(cur.rowcount > 0)

    async def _close_offer(
        self,
        guild_id: int,
        offer_id: int,
        user_id: int,
        now: int,
        status: Literal["declined", "withdrawn"],
        *,
        announce: bool = False,
    ) -> bool:
        """Decline (the person offered) or withdraw (the owner who offered). False if
        it isn't theirs to close or isn't open any more."""
        async with self._db.guild(guild_id) as conn:
            offer = await self._offer(conn, guild_id, offer_id, lock=True)
            if offer is None:
                return False
            await self._expire(conn, offer, now)
            who = offer.to_user_id if status == "declined" else offer.from_user_id
            if who != user_id or not offer.is_open(now):
                return False
            await self._decide(conn, guild_id, offer_id, status, now)
            if announce:
                await offer_notify.send(conn, guild_id, offer_id, offer_notify.DECIDED)
            return True

    async def _offer(
        self, conn: Conn, guild_id: int, offer_id: int, *, lock: bool = False
    ) -> HandoverOffer | None:
        cur = await conn.execute(
            "SELECT * FROM campaign_handover_offers WHERE guild_id = %s AND id = %s"
            + (" FOR UPDATE" if lock else ""),
            (guild_id, offer_id),
        )
        row = await cur.fetchone()
        return None if row is None else _to_offer(row)

    async def _open_offer(
        self, conn: Conn, guild_id: int, campaign_id: str
    ) -> HandoverOffer | None:
        cur = await conn.execute(
            "SELECT * FROM campaign_handover_offers"
            " WHERE guild_id = %s AND campaign_id = %s AND status = 'open'",
            (guild_id, campaign_id),
        )
        row = await cur.fetchone()
        return None if row is None else _to_offer(row)

    async def _expire(self, conn: Conn, offer: HandoverOffer, now: int) -> None:
        """An open offer found past its 7 days is marked so, for the website's lists."""
        if offer.status == "open" and now >= offer.expires_at and offer.move_to_guild_id is None:
            await self._decide(conn, offer.guild_id, offer.id, "expired", offer.expires_at)

    async def _decide(
        self, conn: Conn, guild_id: int, offer_id: int, status: HandoverStatus, decided_at: int
    ) -> None:
        await conn.execute(
            "UPDATE campaign_handover_offers SET status = %s, decided_at = %s"
            " WHERE guild_id = %s AND id = %s",
            (status, decided_at, guild_id, offer_id),
        )

    async def _lock(self, conn: Conn, guild_id: int, campaign_id: str) -> None:
        await conn.execute(
            "SELECT 1 FROM campaigns WHERE guild_id = %s AND id = %s FOR UPDATE",
            (guild_id, campaign_id),
        )

    # ---- backups -------------------------------------------------------------

    async def export(self, guild_id: int, campaign_id: str) -> dict[str, Any]:
        """A JSON-friendly backup of one campaign, read from one consistent moment. The main file
        only: its transcripts are the extra files of `export_set`."""
        return (await self.export_set(guild_id, campaign_id, with_parts=False))[0]

    async def export_set(
        self, guild_id: int, campaign_id: str, *, with_parts: bool = True
    ) -> tuple[dict[str, Any], list[bytes]]:
        """The whole backup: the main file and the `transcripts` files (#2552), all read from one
        consistent moment. The main file names the set (`parts`: its id and how many files) so a
        restore can tell a missing file from a campaign that has none. A session too big for one
        file raises BackupTooBig; no copy is made that cannot come back."""
        files: list[bytes] = []
        backup_id = uuid.uuid4().hex
        async with self._db.guild(guild_id, snapshot=True) as conn:
            campaign = await self._require(conn, guild_id, campaign_id)
            sections = {
                name: await section.dump(conn, guild_id, campaign_id)
                for name, section in self._sections.items()
            }
            if with_parts:
                pending: list[str] = []
                size = 0
                async for text in TranscriptParts().sessions(conn, guild_id, campaign_id):
                    if len(text) > PART_TEXT_BYTES:
                        raise BackupTooBig(SESSION_TOO_BIG)
                    if pending and size + len(text) > PART_TEXT_BYTES:
                        if len(files) >= RESTORE_BOXES:  # (stop early: a restore takes no more)
                            raise BackupTooBig(TOO_MANY_PARTS)
                        files.append(
                            await asyncio.to_thread(
                                encode_part, backup_id, len(files) + 1, pending, guild_id
                            )
                        )
                        pending, size = [], 0
                    pending.append(text)
                    size += len(text)
                if pending:
                    if len(files) >= RESTORE_BOXES:
                        raise BackupTooBig(TOO_MANY_PARTS)
                    files.append(
                        await asyncio.to_thread(
                            encode_part, backup_id, len(files) + 1, pending, guild_id
                        )
                    )
        main: dict[str, Any] = {
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
                "rules_cards": campaign.rules_cards,
                "side_initiative": campaign.side_initiative,
                "initiative_style": campaign.initiative_style,
                "story_mode": campaign.story_mode,
                "transcript_keep_days": campaign.transcript_keep_days,
                "quiet_stop_minutes": campaign.quiet_stop_minutes,
                "recaps_text": campaign.recaps_text,
                "recaps_audio": campaign.recaps_audio,
                "recaps_email": campaign.recaps_email,
                "dndbeyond_invite": campaign.dndbeyond_invite,
                # Only the switch (#1118): a channel ID means nothing in another server.
                "table_chat": campaign.table_chat,
            },
            "sections": sections,
        }
        if files:
            main["parts"] = {"id": backup_id, "count": len(files)}
        return main, files

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
        parts: Sequence[bytes] = (),
        left_out: LeftOut | None = None,
        progress: Callable[[int, int], Awaitable[None] | None] | None = None,
        in_transaction: Callable[[Conn, str], Awaitable[None]] | None = None,
        step: Callable[[], Awaitable[None] | None] | None = None,
    ) -> Campaign:
        """Restore a backup into this server, as a new campaign or replacing one.

        `progress(done, total)`: told only when there are transcripts files: 0 of N before the
        first and then after each one is loaded (#2564, #2746). Plain or awaited. It cannot change
        the restore; a failure in it is not the restore's.
        `step()`: told when the file has been checked and after each section is written, so a
        page can show the copy still moving while no transcripts file is loading (#2746).
        `in_transaction(conn, campaign_id)`: run on the restore's own connection just before it
        commits, so a record of the outcome commits with the copy or not at all.

        `left_out` counts the lines not brought back because their speaker erased them after the
        copy was made (#2605).
        `parts`: the `transcripts` files that came with the main file (#2552). None: the campaign
        is restored without them, as a copy made before they existed is. All of them or none: a
        file that is missing, repeated or from another copy is refused. Each is unpacked and
        checked on its own, so memory stays bounded however many sessions there are.

        The importer always becomes a DM of the restored campaign, and its owner (#437)
        when it's a new campaign: the owner isn't in a backup, as it is a person's plan.
        Replacing a campaign keeps its owner (handing it over is its own step), and the
        importer must already be a DM of the campaign being replaced. Channel settings
        are not restored, because channels belong to the server the backup came from.
        `data` is untrusted: it's fully validated before anything is written, and the
        whole restore happens in one transaction.
        """
        # Checking a big file is pure CPU: in a worker thread, so the event loop (voice,
        # other servers) carries on, and before the transaction, so no locks are held
        # meanwhile (#164).
        info, sections = await asyncio.to_thread(self._checked, data)
        order = await self._part_order(data, parts)

        async def tell(call: Callable[..., Awaitable[None] | None] | None, *args: int) -> None:
            if call is not None:
                try:
                    told = call(*args)  # (Discord's callback is a plain call; the website's awaits)
                    if inspect.isawaitable(told):
                        await told
                except Exception as exc:  # (only a progress line: never the restore's)
                    log.warning("A restore's progress line failed: %s", type(exc).__name__)

        await tell(step)
        # The Shared story switch comes back on only for the person who confirmed the right to
        # use the material (the file's confirmation is theirs); anyone else restoring the file
        # starts with it off and confirms for themselves (#1692, CLAUDE.md IP rule).
        if info["story_mode"] != STORY_OFF and not any(
            # (the checked rows: id, user, purpose, fingerprint, time)
            row[1] == importer_id and row[2] == SHARED_STORY
            for row in sections.get("confirmations", [])
        ):
            info = {**info, "story_mode": STORY_OFF}
        # Restoring counts as using the campaign: the retention clock starts from now, so a
        # copy of an old campaign isn't deleted by the next daily run (#964).
        restored_at = int(self._clock())
        async with self._db.guild(guild_id) as conn:
            if replace_campaign_id is not None:
                await self._lock(conn, guild_id, replace_campaign_id)
                existing = await self._require(conn, guild_id, replace_campaign_id)
                if importer_id not in existing.dm_user_ids:
                    raise CampaignError(NOT_THIS_DM)
                if existing.owner_user_id is None:  # the importer becomes its owner
                    await self._require_slot(conn, importer_id)
                campaign_id = replace_campaign_id
                for section in self._sections.values():
                    await section.clear(conn, guild_id, campaign_id)
                await conn.execute(
                    "UPDATE campaigns SET target_ruleset = %s, fallback_ruleset = %s,"
                    " optional_rules_default = %s, last_played_at = %s,"
                    " dm_screen_visibility = %s, dm_screen_level = %s, rules_cards = %s,"
                    " table_chat = %s, side_initiative = %s, initiative_style = %s,"
                    " story_mode = %s,"
                    " recaps_text = %s, recaps_audio = %s, recaps_email = %s,"
                    # The invite link is not touched: it is the DM's join link for this
                    # server's table, and a backup doesn't record which server it came from,
                    # so its link could be another server's (#1119).
                    " owner_user_id = COALESCE(owner_user_id, %s),"
                    " owner_left_at = CASE WHEN owner_user_id IS NULL THEN NULL"
                    "   ELSE owner_left_at END"
                    " WHERE guild_id = %s AND id = %s",
                    (
                        info["target_ruleset"],
                        info["fallback_ruleset"],
                        info["optional_rules_default"],
                        restored_at,
                        info["dm_screen_visibility"],
                        info["dm_screen_level"],
                        info["rules_cards"],
                        info["table_chat"],
                        info["side_initiative"],
                        info["initiative_style"],
                        info["story_mode"],
                        info["recaps_text"],
                        info["recaps_audio"],
                        info["recaps_email"],
                        importer_id,
                        guild_id,
                        campaign_id,
                    ),
                )
            else:
                await self._require_slot(conn, importer_id)
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
                        restored_at,
                        info["dm_screen_visibility"],
                        info["dm_screen_level"],
                        importer_id,
                    )
                    if info["rules_cards"]:  # a new campaign starts with them off
                        await conn.execute(
                            "UPDATE campaigns SET rules_cards = TRUE"
                            " WHERE guild_id = %s AND id = %s",
                            (guild_id, campaign_id),
                        )
                    for switch in RECAP_SWITCHES:  # a new campaign starts with them on
                        if not info[f"recaps_{switch}"]:
                            await conn.execute(
                                f"UPDATE campaigns SET recaps_{switch} = FALSE"
                                " WHERE guild_id = %s AND id = %s",
                                (guild_id, campaign_id),
                            )
                    if (
                        info["quiet_stop_minutes"] != QUIET_STOP_DEFAULT
                    ):  # a new one starts at the default
                        await conn.execute(
                            "UPDATE campaigns SET quiet_stop_minutes = %s"
                            " WHERE guild_id = %s AND id = %s",
                            (info["quiet_stop_minutes"], guild_id, campaign_id),
                        )
                    if info["transcript_keep_days"] is not None:  # a new one keeps forever
                        await conn.execute(
                            "UPDATE campaigns SET transcript_keep_days = %s"
                            " WHERE guild_id = %s AND id = %s",
                            (info["transcript_keep_days"], guild_id, campaign_id),
                        )
                    if info["side_initiative"]:  # a new campaign starts with it off
                        await conn.execute(
                            "UPDATE campaigns SET side_initiative = TRUE"
                            " WHERE guild_id = %s AND id = %s",
                            (guild_id, campaign_id),
                        )
                    if info["initiative_style"] is not None:  # a new campaign keeps none
                        await conn.execute(
                            "UPDATE campaigns SET initiative_style = %s"
                            " WHERE guild_id = %s AND id = %s",
                            (info["initiative_style"], guild_id, campaign_id),
                        )
                    if info["story_mode"] != STORY_OFF:  # a new campaign starts with it off
                        await conn.execute(
                            "UPDATE campaigns SET story_mode = %s WHERE guild_id = %s AND id = %s",
                            (info["story_mode"], guild_id, campaign_id),
                        )
                    if not info["table_chat"]:  # typed chat starts on; a backup may say off
                        await conn.execute(
                            "UPDATE campaigns SET table_chat = FALSE"
                            " WHERE guild_id = %s AND id = %s",
                            (guild_id, campaign_id),
                        )
                    # A new campaign made from a backup gets no invite link: it is the
                    # original DM's, for the original table. Its new DM sets their own.
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
                await tell(step)
            if _has_no_lists(sections, data):
                from dmbot.memory import knowers

                await knowers.move_flags(conn, guild_id, campaign_id)  # (#2995, slice 2b)
            if order:
                if replace_campaign_id is not None:  # the copy's transcripts replace the old ones
                    await TranscriptParts().clear(conn, guild_id, campaign_id)
                await tell(progress, 0, len(order))
                for done, index in enumerate(order, 1):
                    # (unpacked again here, one at a time: nothing but the part in hand is held)
                    part = await asyncio.to_thread(decode_part, parts[index])
                    try:
                        await TranscriptParts().load(conn, guild_id, campaign_id, part, left_out)
                    except (pg_errors.DataError, pg_errors.IntegrityError) as exc:
                        # (the tables' own types and CHECKs refused a value in the file)
                        raise CampaignError(TRANSCRIPTS_DAMAGED) from exc
                    await tell(progress, done, len(order))
            # The importer, and the owner when replacing (who stays the owner, #609), are
            # DMs whatever the copy's DM list says: the owner must stay one (#437).
            await conn.execute(
                "INSERT INTO campaign_dms (campaign_id, guild_id, user_id)"
                " SELECT %s, %s, u FROM unnest(ARRAY[%s, (SELECT owner_user_id FROM campaigns"
                "   WHERE guild_id = %s AND id = %s)]::BIGINT[]) AS u"
                " WHERE u IS NOT NULL ON CONFLICT DO NOTHING",
                (campaign_id, guild_id, importer_id, guild_id, campaign_id),
            )
            if in_transaction is not None:
                await in_transaction(conn, campaign_id)
            return await self._require(conn, guild_id, campaign_id)

    @staticmethod
    async def _part_order(data: object, parts: Sequence[bytes]) -> list[int]:
        """Which of `parts` to load, in the copy's own order, after checking that they are exactly
        the set the main file names (#2552). Empty when none were given. Each file is unpacked and
        checked here, one at a time, and let go."""
        if not parts:
            return []
        manifest = data.get("parts") if isinstance(data, dict) else None
        if (
            not isinstance(manifest, dict)
            or not isinstance(manifest.get("id"), str)
            or type(manifest.get("count")) is not int
        ):
            raise PartsError(PARTS_NOT_THIS_COPY)
        numbered: dict[int, int] = {}
        for index, raw in enumerate(parts):
            part = await asyncio.to_thread(decode_part, raw)
            if part["backup"] != manifest["id"]:
                raise PartsError(PARTS_NOT_THIS_COPY)
            if part["part"] in numbered or not 1 <= part["part"] <= manifest["count"]:
                raise PartsError(PARTS_NOT_THIS_COPY)
            numbered[part["part"]] = index
        if len(numbered) != manifest["count"]:
            raise PartsError(
                f"This copy has {manifest['count']} transcripts file"
                f"{'' if manifest['count'] == 1 else 's'}, and you gave {len(numbered)}. "
                "Give all of them, or none to restore without the transcripts."
            )
        return [numbered[n] for n in sorted(numbered)]

    # ---- helpers (run inside a guild transaction) ----------------------------

    async def _require_slot(self, conn: Conn, importer_id: int) -> None:
        """A restore that makes the importer an owner needs room in their plan (#437)."""
        if self._restore_needs_slot and not await self._has_free_slot(
            conn, importer_id, int(self._clock())
        ):
            raise CampaignError(NO_ROOM_TO_RESTORE)

    async def _set(self, guild_id: int, campaign_id: str, column: str, value: object) -> Campaign:
        if column not in _SETTABLE:
            raise ValueError(f"Not a settable campaign column: {column}")
        async with self._db.guild(guild_id) as conn:
            return await self._update(
                conn,
                guild_id,
                campaign_id,
                sql.SQL("{} = %s").format(sql.Identifier(column)),
                (value,),
            )

    async def _update(
        self,
        conn: Conn,
        guild_id: int,
        campaign_id: str,
        assignments: sql.Composable,
        values: tuple[object, ...],
        *,
        also: sql.Composable | None = None,
    ) -> Campaign:
        """One UPDATE ... RETURNING and one read of the DMs (#77), instead of reading the
        campaign before and after: two statements, whatever changed. `also`: an extra condition
        (the update then changes nothing and the campaign is returned as it is). No campaign
        here is `NOT_HERE`, as before."""
        where: sql.Composable = sql.SQL("guild_id = %s AND id = %s")
        if also is not None:
            where = sql.SQL("{} AND {}").format(where, also)
        cur = await conn.execute(
            sql.SQL("UPDATE campaigns SET {} WHERE {} RETURNING *").format(assignments, where),
            (*values, guild_id, campaign_id),
        )
        row = await cur.fetchone()
        if row is None:
            if also is None:
                raise CampaignError(NOT_HERE)
            return await self._require(conn, guild_id, campaign_id)  # the condition did not hold
        dms = await self._dms_for(conn, guild_id, campaign_id)
        return _to_campaign(row, dms.get(campaign_id, set()))

    async def _get(
        self, conn: Conn, guild_id: int, campaign_id: str, *, lock: bool = False
    ) -> Campaign | None:
        cur = await conn.execute(
            "SELECT * FROM campaigns WHERE guild_id = %s AND id = %s"
            + (" FOR UPDATE" if lock else ""),
            (guild_id, campaign_id),
        )
        row = await cur.fetchone()
        if row is None:
            return None
        dms = await self._dms_for(conn, guild_id, campaign_id)
        return _to_campaign(row, dms.get(campaign_id, set()))

    async def _require(
        self, conn: Conn, guild_id: int, campaign_id: str, *, lock: bool = False
    ) -> Campaign:
        campaign = await self._get(conn, guild_id, campaign_id, lock=lock)
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
        owner_user_id: int,
    ) -> str:
        campaign_id = uuid.uuid4().hex
        await conn.execute(
            "INSERT INTO campaigns (id, guild_id, name, name_key, created_at, last_played_at,"
            " target_ruleset, fallback_ruleset, optional_rules_default, dm_screen_visibility,"
            " dm_screen_level, owner_user_id)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
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
                owner_user_id,
            ),
        )
        return campaign_id

    async def _free_name(self, conn: Conn, guild_id: int, base: str, word: str = "restored") -> str:
        """`base`, or `base (<word>)`, `base (<word> 2)`, … whichever is free ("restored" for a
        restore, "moved" for a move)."""
        cur = await conn.execute("SELECT name_key FROM campaigns WHERE guild_id = %s", (guild_id,))
        taken = {r["name_key"] for r in await cur.fetchall()}
        if name_key(base) not in taken:
            return base
        for n in range(1, 1000):
            suffix = f" ({word})" if n == 1 else f" ({word} {n})"
            candidate = clean_name(base[: NAME_MAX - len(suffix)].rstrip() + suffix)
            if name_key(candidate) not in taken:
                return candidate
        raise CampaignError("Too many campaigns with that name. Rename one and try again.")


async def _scope(conn: Conn, guild_id: int) -> None:
    """Look at another server's rows for the rest of this transaction (a move reads and writes
    both; row-level security still shows only the one in scope at a time)."""
    await conn.execute("SELECT set_config('dmbot.guild_id', %s, true)", (str(int(guild_id)),))


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
        # Absent in a schema older than #1116 (a migration test opens one).
        gametime_channel_id=row_int(row, "gametime_channel_id")
        if "gametime_channel_id" in row
        else None,
        # Absent in a schema older than #1187, for the same reason.
        dmbchat_channel_id=row_int(row, "dmbchat_channel_id")
        if "dmbchat_channel_id" in row
        else None,
        # Absent in a schema older than #1666, for the same reason.
        dm_bot_channel_id=row_int(row, "dm_bot_channel_id") if "dm_bot_channel_id" in row else None,
        dm_bot_message_id=row_int(row, "dm_bot_message_id") if "dm_bot_message_id" in row else None,
        # Absent in a schema older than #1752, for the same reason.
        rules_channel_id=row_int(row, "rules_channel_id") if "rules_channel_id" in row else None,
        # Absent in a schema older than #1761, for the same reason.
        npcs_channel_id=row_int(row, "npcs_channel_id") if "npcs_channel_id" in row else None,
        # Absent in a schema older than #1119, for the same reason.
        dndbeyond_invite=row.get("dndbeyond_invite"),
        # Absent before #1118: the default is on, with no extra channel.
        table_chat=bool(row.get("table_chat", True)),
        table_chat_channel_id=row_int(row, "table_chat_channel_id")
        if "table_chat_channel_id" in row
        else None,
        dm_screen_level=row["dm_screen_level"],
        owner_user_id=row_int(row, "owner_user_id"),
        rules_cards=bool(row.get("rules_cards", False)),  # older schemas (tests) have none
        side_initiative=bool(row.get("side_initiative", False)),
        story_mode=str(row.get("story_mode", STORY_OFF)),
        initiative_style=row.get("initiative_style"),
        transcript_keep_days=row_int(row, "transcript_keep_days")
        if "transcript_keep_days" in row
        else None,
        transcript_deleted_noted=bool(row.get("transcript_deleted_noted", False)),
        quiet_stop_minutes=int(row.get("quiet_stop_minutes", QUIET_STOP_DEFAULT)),  # older: none
        recaps_text=bool(row.get("recaps_text", True)),  # older schemas (tests) have none
        recaps_audio=bool(row.get("recaps_audio", True)),
        recaps_email=bool(row.get("recaps_email", True)),
        paused=bool(row.get("paused", False)),
        retention_warned_stage=int(row.get("retention_warned_stage", 0) or 0),
        retention_warned_for=(
            int(row["retention_warned_for"])
            if row.get("retention_warned_for") is not None
            else None
        ),  # older schemas (tests) have none
        owner_left_at=(int(row["owner_left_at"]) if row.get("owner_left_at") is not None else None),
        owner_missing_since=(
            int(row["owner_missing_since"]) if row.get("owner_missing_since") is not None else None
        ),
    )


def _offer_name(name: str) -> str:
    """A display name kept on an offer: as shown, trimmed to fit. Callers always have
    one (Discord's display name); an empty one is a mistake, not a name."""
    clean = " ".join(name.split())[:NAME_ON_OFFER_MAX].rstrip()
    if not clean:
        raise ValueError("A hand-over offer needs both people's names")
    return clean


# An offer waiting for the bot to send it (#690). Parameters: lapsed-claim cutoff
# (now - CLAIM_SECONDS), HANDOVER_SECONDS, now.
_UNSENT: LiteralString = (
    "status = 'open' AND delivered_at IS NULL"
    " AND (claimed_at IS NULL OR claimed_at <= %s) AND created_at + %s > %s"
)
# How long a bot process's claim on sending an offer lasts (#690).
CLAIM_SECONDS = 10 * 60


def _to_offer(row: dict[str, Any]) -> HandoverOffer:
    return HandoverOffer(
        id=int(row["id"]),
        guild_id=int(row["guild_id"]),
        campaign_id=row["campaign_id"],
        from_user_id=int(row["from_user_id"]),
        to_user_id=int(row["to_user_id"]),
        created_at=int(row["created_at"]),
        from_name=row["from_name"],
        to_name=row["to_name"],
        status=cast(HandoverStatus, row["status"]),
        decided_at=row_int(row, "decided_at"),
        delivered_at=row_int(row, "delivered_at"),
        claimed_at=row_int(row, "claimed_at"),
        message_id=row_int(row, "message_id"),
        end_told_at=row_int(row, "end_told_at"),
        via_email=bool(row.get("via_email", False)),
        move_to_guild_id=row_int(row, "move_to_guild_id"),
        moved_campaign_id=row.get("moved_campaign_id"),
        move_claimed_at=row_int(row, "move_claimed_at"),
        move_asked_at=row_int(row, "move_asked_at"),
        move_failed_for=row_int(row, "move_failed_for"),
        move_failed_reason=row.get("move_failed_reason"),
    )


# Built once: the C encoder behind .encode is what keeps a big copy fast.
_ENCODER = json.JSONEncoder(ensure_ascii=False, separators=(",", ":"))


def _json_pieces(value: object, depth: int = 0) -> Iterator[str]:
    """`json.dumps(value, ensure_ascii=False, separators=(",", ":"))` in pieces: the first
    levels of objects are walked here and every list row or deeper value goes through
    json.dumps itself, so the work stays in the fast C encoder (iterencode is pure Python
    and about 5 times slower, holding the GIL the voice relay needs)."""
    dump = _ENCODER.encode
    if isinstance(value, dict) and depth < 3:
        yield "{"
        for i, (key, item) in enumerate(value.items()):
            if not isinstance(key, str):  # json would turn 1 into "1", True into "true"...
                raise TypeError("a backup's keys are text")
            if i:
                yield ","
            yield dump(key) + ":"
            yield from _json_pieces(item, depth + 1)
        yield "}"
    elif isinstance(value, list):
        yield "["
        for i, item in enumerate(value):
            if i:
                yield ","
            yield dump(item)
        yield "]"
    else:
        yield dump(value)


def encode_backup(backup: dict[str, Any]) -> bytes:
    """Serialise a backup for the DM to download: compressed JSON (#164), so a big
    campaign still fits in one Discord file. Raises BackupTooBig rather than make a copy
    a restore would refuse, by its text or by its packed size (restore checks both). Run
    off the event loop for big ones.

    The JSON is fed to the compressor piece by piece, so the whole text (up to 25 MB) never
    sits in memory next to its compressed copy, and the size check stops it early."""
    out = io.BytesIO()
    size = 0
    with gzip.GzipFile(fileobj=out, mode="wb", compresslevel=6, mtime=0) as packed:
        for piece in _json_pieces(backup):
            data = piece.encode("utf-8")
            size += len(data)
            if size > MAX_BACKUP_BYTES:  # what decode_backup would refuse to unpack
                raise BackupTooBig(CAMPAIGN_TOO_BIG)
            packed.write(data)
    if out.tell() > MAX_BACKUP_BYTES:  # restore also refuses an upload this big
        raise BackupTooBig(CAMPAIGN_TOO_BIG)
    return out.getvalue()  # no second copy: CPython shares the buffer until it is written


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


def _has_no_lists(sections: dict[str, Any], data: object) -> bool:
    """A backup made before the lists existed (#2995): version 2 or older with no who-knows rows in
    its memory. Its restore works them out from the old flags (`knowers.move_flags`). A file of
    version 3 keeps its lists as they are, empty or trimmed, so a list the DM narrowed is never
    widened again and a campaign that showed nothing stays that way. A version-2 file that already
    has rows is kept as it is for the same reason."""
    version = data.get("version") if isinstance(data, dict) else None
    memory = sections.get("memory")
    return (
        isinstance(version, int)
        and version < 3
        and isinstance(memory, dict)
        and not memory.get("knower")
    )


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
    # Backups made before rules cards existed have them off.
    rules_cards = campaign.get("rules_cards", False)
    if not isinstance(rules_cards, bool):
        raise CampaignError(DAMAGED)

    # Backups made before combat state existed have side-based initiative off.
    side_initiative = campaign.get("side_initiative", False)
    if not isinstance(side_initiative, bool):
        raise CampaignError(DAMAGED)

    # Backups made before the initiative style was kept have none remembered (#1784).
    initiative_style = campaign.get("initiative_style")
    if initiative_style is not None and (
        not isinstance(initiative_style, str) or initiative_style not in fight_style.SAVED_STYLES
    ):
        raise CampaignError(DAMAGED)
    # A backup from before the style was kept, with the old switch on, said the table plays by
    # side: it restores as that style (#1791). A style the backup does keep wins.
    if initiative_style is None and side_initiative:
        initiative_style = "side"

    # Backups made before the Shared story switch existed have it off. (Whether a mode other
    # than off is kept depends on who restores the file: `import_backup`, #1692.)
    story_mode = campaign.get("story_mode", STORY_OFF)
    if not isinstance(story_mode, str) or story_mode not in STORY_MODES:
        raise CampaignError(DAMAGED)

    # Backups made before the quiet-stop setting existed use the default, 10 minutes (#2034,
    # #2288). One made when Off, 60 or 90 were choices loads as 30, the longest wait left.
    quiet_stop = campaign.get("quiet_stop_minutes", QUIET_STOP_DEFAULT)
    if type(quiet_stop) is not int or quiet_stop not in (*QUIET_STOP_CHOICES, *QUIET_STOP_OLD):
        raise CampaignError(DAMAGED)
    if quiet_stop in QUIET_STOP_OLD:
        quiet_stop = 30

    # Backups made before the keep setting existed keep transcripts forever.
    keep_days = campaign.get("transcript_keep_days")
    if keep_days is not None and (type(keep_days) is not int or keep_days not in KEEP_CHOICES):
        raise CampaignError(DAMAGED)

    # Backups made before the recap choices existed have them all on, as a new campaign does.
    recap_choices = {}
    for switch in RECAP_SWITCHES:
        value = campaign.get(f"recaps_{switch}", True)
        if not isinstance(value, bool):
            raise CampaignError(DAMAGED)
        recap_choices[f"recaps_{switch}"] = value

    # Backups made before typed chat existed have it on, as a new campaign does.
    table_chat = campaign.get("table_chat", True)
    if not isinstance(table_chat, bool):
        raise CampaignError(DAMAGED)

    # Backups made before the invite link existed have none. A link that isn't one is damage.
    invite = campaign.get("dndbeyond_invite")
    if invite is not None:
        if not isinstance(invite, str):
            raise CampaignError(DAMAGED)
        try:
            if parse_invite(invite) != invite:
                raise CampaignError(DAMAGED)
        except CampaignError as exc:
            raise CampaignError(DAMAGED) from exc

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
        "rules_cards": rules_cards,
        "side_initiative": side_initiative,
        "story_mode": story_mode,
        "initiative_style": initiative_style,
        "transcript_keep_days": keep_days,
        "quiet_stop_minutes": quiet_stop,
        **recap_choices,
        "dndbeyond_invite": invite,
        "table_chat": table_chat,
    }
    return info, {name: list(rows) for name, rows in sections.items()}
