"""Campaign storage in SQLite.

Isolation is enforced here, not by callers: every public method takes the Discord
server (guild) ID, and every query filters on it. Asking for another server's campaign
behaves exactly like asking for one that doesn't exist.

Future features keep their per-campaign data in their own tables (with `campaign_id`
and `guild_id` columns, deleted with the campaign) and plug into backups by
registering an `ExportSection`.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
import time
import uuid
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, Protocol, TypeVar

from dmbot.campaigns.models import (
    DEFAULT_DM_SCREEN_VISIBILITY,
    DEFAULT_FALLBACK,
    DEFAULT_TARGET,
    NAME_MAX,
    Campaign,
    CampaignError,
    check_dm_screen_visibility,
    check_rulesets,
    clean_name,
    name_key,
)
from dmbot.db import Migration, apply_migrations, connect, read_snapshot, transaction

T = TypeVar("T")

EXPORT_FORMAT = "dmbot-campaign"
EXPORT_VERSION = 1

MIGRATIONS: Sequence[Migration] = (
    (
        "campaigns_001",
        """
        CREATE TABLE campaigns (
            id                     TEXT PRIMARY KEY,
            guild_id               INTEGER NOT NULL,
            name                   TEXT NOT NULL,
            name_key               TEXT NOT NULL,
            created_at             INTEGER NOT NULL,
            last_played_at         INTEGER,
            target_ruleset         TEXT NOT NULL,
            fallback_ruleset       TEXT NOT NULL,
            optional_rules_default INTEGER NOT NULL DEFAULT 1,
            dm_screen_channel_id   INTEGER,
            last_voice_channel_id  INTEGER,
            UNIQUE (guild_id, name_key),
            UNIQUE (id, guild_id)
        );
        CREATE INDEX campaigns_by_guild ON campaigns (guild_id);
        CREATE TABLE campaign_dms (
            campaign_id TEXT NOT NULL,
            guild_id    INTEGER NOT NULL,
            user_id     INTEGER NOT NULL,
            PRIMARY KEY (campaign_id, user_id),
            FOREIGN KEY (campaign_id, guild_id)
                REFERENCES campaigns (id, guild_id) ON DELETE CASCADE
        );
        CREATE TABLE campaign_optional_rules (
            campaign_id TEXT NOT NULL,
            guild_id    INTEGER NOT NULL,
            rule_id     TEXT NOT NULL,
            enabled     INTEGER NOT NULL,
            PRIMARY KEY (campaign_id, rule_id),
            FOREIGN KEY (campaign_id, guild_id)
                REFERENCES campaigns (id, guild_id) ON DELETE CASCADE
        )
        """,
    ),
    (
        "campaigns_002_dm_screen_visibility",
        """
        ALTER TABLE campaigns
            ADD COLUMN dm_screen_visibility TEXT NOT NULL DEFAULT 'peek'
        """,
    ),
)

RULE_ID_MAX = 64
# Discord IDs and Unix timestamps must fit SQLite's signed 64-bit INTEGER.
INT64_MAX = 2**63 - 1
# Upper bound on an uploaded backup file, checked before parsing it.
MAX_BACKUP_BYTES = 25 * 1024 * 1024


class ExportSection(Protocol):
    """One kind of per-campaign data that is included in backups.

    Contract for every section:
    - Tables carry `campaign_id` and `guild_id` and reference
      `campaigns (id, guild_id) ON DELETE CASCADE`, so rows can't drift to another server.
    - `dump` returns JSON-friendly rows for one campaign; `load` writes rows into a (new
      or emptied) campaign; `clear` removes that campaign's rows. All three run inside the
      store's transaction and must filter by **both** guild_id and campaign_id.
    - `load` receives rows from an **untrusted file**: validate every field and raise
      `CampaignError` with a plain message when something is wrong.
    - Never export server-specific IDs (channels, roles, messages); they mean nothing
      in another server.
    """

    name: str

    def dump(self, conn: sqlite3.Connection, guild_id: int, campaign_id: str) -> list[Any]: ...

    def load(
        self, conn: sqlite3.Connection, guild_id: int, campaign_id: str, rows: list[Any]
    ) -> None: ...

    def clear(self, conn: sqlite3.Connection, guild_id: int, campaign_id: str) -> None: ...


class _DMSection:
    name = "dms"

    def dump(self, conn: sqlite3.Connection, guild_id: int, campaign_id: str) -> list[Any]:
        rows = conn.execute(
            "SELECT user_id FROM campaign_dms WHERE guild_id = ? AND campaign_id = ?"
            " ORDER BY user_id",
            (guild_id, campaign_id),
        )
        return [str(r["user_id"]) for r in rows]

    def load(
        self, conn: sqlite3.Connection, guild_id: int, campaign_id: str, rows: list[Any]
    ) -> None:
        for raw in rows:
            if not (
                isinstance(raw, str)
                and raw.isascii()
                and raw.isdigit()
                and 0 < int(raw) <= INT64_MAX
            ):
                raise CampaignError("This backup file is damaged (bad DM entry).")
            conn.execute(
                "INSERT OR IGNORE INTO campaign_dms (campaign_id, guild_id, user_id)"
                " VALUES (?, ?, ?)",
                (campaign_id, guild_id, int(raw)),
            )

    def clear(self, conn: sqlite3.Connection, guild_id: int, campaign_id: str) -> None:
        conn.execute(
            "DELETE FROM campaign_dms WHERE guild_id = ? AND campaign_id = ?",
            (guild_id, campaign_id),
        )


class _OptionalRulesSection:
    name = "optional_rules"

    def dump(self, conn: sqlite3.Connection, guild_id: int, campaign_id: str) -> list[Any]:
        rows = conn.execute(
            "SELECT rule_id, enabled FROM campaign_optional_rules"
            " WHERE guild_id = ? AND campaign_id = ? ORDER BY rule_id",
            (guild_id, campaign_id),
        )
        return [{"rule": r["rule_id"], "enabled": bool(r["enabled"])} for r in rows]

    def load(
        self, conn: sqlite3.Connection, guild_id: int, campaign_id: str, rows: list[Any]
    ) -> None:
        for row in rows:
            rule = row.get("rule") if isinstance(row, dict) else None
            enabled = row.get("enabled") if isinstance(row, dict) else None
            if not _valid_rule_id(rule) or not isinstance(enabled, bool):
                raise CampaignError("This backup file is damaged (bad optional rule).")
            conn.execute(
                "INSERT OR REPLACE INTO campaign_optional_rules"
                " (campaign_id, guild_id, rule_id, enabled) VALUES (?, ?, ?, ?)",
                (campaign_id, guild_id, rule, int(enabled)),
            )

    def clear(self, conn: sqlite3.Connection, guild_id: int, campaign_id: str) -> None:
        conn.execute(
            "DELETE FROM campaign_optional_rules WHERE guild_id = ? AND campaign_id = ?",
            (guild_id, campaign_id),
        )


def _valid_rule_id(rule: object) -> bool:
    return (
        isinstance(rule, str)
        and 0 < len(rule) <= RULE_ID_MAX
        and all(c.isalnum() or c in "-_." for c in rule)
    )


class CampaignStore:
    def __init__(self, path: Path | str, *, clock: Callable[[], float] = time.time) -> None:
        self._db = connect(path)
        apply_migrations(self._db, MIGRATIONS)
        # A *thread* lock, held by the worker thread itself: if the awaiting task is
        # cancelled, the thread keeps the lock until its SQL is finished, so two
        # threads never use the connection at once.
        self._lock = threading.Lock()
        self._clock = clock
        self._sections: dict[str, ExportSection] = {}
        self.register_section(_DMSection())
        self.register_section(_OptionalRulesSection())

    def close(self) -> None:
        self._db.close()

    def register_section(self, section: ExportSection) -> None:
        """Include another feature's per-campaign data in backups and deletes."""
        if section.name in self._sections:
            raise ValueError(f"Export section {section.name!r} is already registered")
        self._sections[section.name] = section

    # ---- async API ---------------------------------------------------------

    async def _run(self, fn: Callable[..., T], *args: Any) -> T:
        return await asyncio.to_thread(self._locked, fn, *args)

    def _locked(self, fn: Callable[..., T], *args: Any) -> T:
        with self._lock:
            return fn(*args)

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
    ) -> Campaign:
        return await self._run(
            self._create,
            guild_id,
            name,
            dm_user_id,
            target_ruleset,
            fallback_ruleset,
            optional_rules_default,
            dm_screen_visibility,
        )

    async def get(self, guild_id: int, campaign_id: str) -> Campaign | None:
        return await self._run(self._get, guild_id, campaign_id)

    async def list_campaigns(self, guild_id: int) -> list[Campaign]:
        """All of a server's campaigns, most recently used first."""
        return await self._run(self._list, guild_id)

    async def last_used(self, guild_id: int) -> Campaign | None:
        """The default for `/dmbot start`: the campaign played (or created) most recently."""
        return await self._run(self._last_used, guild_id)

    async def rename(self, guild_id: int, campaign_id: str, new_name: str) -> Campaign:
        return await self._run(self._rename, guild_id, campaign_id, new_name)

    async def set_rulesets(
        self, guild_id: int, campaign_id: str, target: str, fallback: str
    ) -> Campaign:
        return await self._run(self._set_rulesets, guild_id, campaign_id, target, fallback)

    async def set_optional_rules_default(
        self, guild_id: int, campaign_id: str, enabled: bool
    ) -> Campaign:
        return await self._run(self._set_optional_default, guild_id, campaign_id, enabled)

    async def set_optional_rule(
        self, guild_id: int, campaign_id: str, rule_id: str, enabled: bool
    ) -> None:
        await self._run(self._set_optional_rule, guild_id, campaign_id, rule_id, enabled)

    async def optional_rule_overrides(self, guild_id: int, campaign_id: str) -> dict[str, bool]:
        """Rules the DM switched away from the campaign default. Missing = use default."""
        return await self._run(self._optional_overrides, guild_id, campaign_id)

    async def add_dm(self, guild_id: int, campaign_id: str, user_id: int) -> Campaign:
        return await self._run(self._add_dm, guild_id, campaign_id, user_id)

    async def remove_dm(self, guild_id: int, campaign_id: str, user_id: int) -> Campaign:
        return await self._run(self._remove_dm, guild_id, campaign_id, user_id)

    async def set_dm_screen(
        self, guild_id: int, campaign_id: str, channel_id: int | None
    ) -> Campaign:
        """Store the campaign's DM screen channel.

        The caller must already have confirmed the channel belongs to this server and is
        private to the DM(s); the store can't see Discord.
        """
        return await self._run(
            self._set_column, guild_id, campaign_id, "dm_screen_channel_id", channel_id
        )

    async def set_dm_screen_visibility(
        self, guild_id: int, campaign_id: str, visibility: str
    ) -> Campaign:
        """Who besides the DM may see the DM screen: "private", "peek" or "open".

        This only stores the choice; applying it to Discord channel permissions is the
        DM-screen feature's job (#30).
        """
        check_dm_screen_visibility(visibility)
        return await self._run(
            self._set_column, guild_id, campaign_id, "dm_screen_visibility", visibility
        )

    async def set_last_voice_channel(
        self, guild_id: int, campaign_id: str, channel_id: int | None
    ) -> Campaign:
        return await self._run(
            self._set_column, guild_id, campaign_id, "last_voice_channel_id", channel_id
        )

    async def mark_played(self, guild_id: int, campaign_id: str) -> Campaign:
        return await self._run(
            self._set_column, guild_id, campaign_id, "last_played_at", int(self._clock())
        )

    async def delete(self, guild_id: int, campaign_id: str) -> None:
        await self._run(self._delete, guild_id, campaign_id)

    async def export(self, guild_id: int, campaign_id: str) -> dict[str, Any]:
        """A JSON-friendly backup of one campaign, for the DM to download."""
        return await self._run(self._export, guild_id, campaign_id)

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
        return await self._run(self._import, guild_id, data, importer_id, replace_campaign_id)

    # ---- sync implementation (runs in a worker thread, under the lock) -----

    def _dms_for(self, guild_id: int, campaign_id: str | None = None) -> dict[str, set[int]]:
        """DMs per campaign for one server (or one campaign), in a single query."""
        sql = "SELECT campaign_id, user_id FROM campaign_dms WHERE guild_id = ?"
        params: tuple[Any, ...] = (guild_id,)
        if campaign_id is not None:
            sql += " AND campaign_id = ?"
            params += (campaign_id,)
        result: dict[str, set[int]] = {}
        for r in self._db.execute(sql, params):
            result.setdefault(r["campaign_id"], set()).add(int(r["user_id"]))
        return result

    def _row_to_campaign(self, row: sqlite3.Row, dms: set[int]) -> Campaign:
        return Campaign(
            id=row["id"],
            guild_id=row["guild_id"],
            name=row["name"],
            created_at=row["created_at"],
            last_played_at=row["last_played_at"],
            target_ruleset=row["target_ruleset"],
            fallback_ruleset=row["fallback_ruleset"],
            optional_rules_default=bool(row["optional_rules_default"]),
            dm_user_ids=frozenset(dms),
            dm_screen_channel_id=row["dm_screen_channel_id"],
            last_voice_channel_id=row["last_voice_channel_id"],
            dm_screen_visibility=row["dm_screen_visibility"],
        )

    def _get(self, guild_id: int, campaign_id: str) -> Campaign | None:
        row = self._db.execute(
            "SELECT * FROM campaigns WHERE guild_id = ? AND id = ?", (guild_id, campaign_id)
        ).fetchone()
        if row is None:
            return None
        return self._row_to_campaign(
            row, self._dms_for(guild_id, campaign_id).get(row["id"], set())
        )

    def _require(self, guild_id: int, campaign_id: str) -> Campaign:
        campaign = self._get(guild_id, campaign_id)
        if campaign is None:
            raise CampaignError("That campaign doesn't exist in this server.")
        return campaign

    _ORDER = " ORDER BY COALESCE(last_played_at, created_at) DESC, created_at DESC, name"

    def _list(self, guild_id: int) -> list[Campaign]:
        with read_snapshot(self._db):
            rows = self._db.execute(
                "SELECT * FROM campaigns WHERE guild_id = ?" + self._ORDER, (guild_id,)
            ).fetchall()
            dms = self._dms_for(guild_id)
        return [self._row_to_campaign(r, dms.get(r["id"], set())) for r in rows]

    def _last_used(self, guild_id: int) -> Campaign | None:
        row = self._db.execute(
            "SELECT id FROM campaigns WHERE guild_id = ?" + self._ORDER + " LIMIT 1",
            (guild_id,),
        ).fetchone()
        return self._get(guild_id, row["id"]) if row else None

    def _name_taken(self, guild_id: int, name: str, except_id: str | None = None) -> bool:
        row = self._db.execute(
            "SELECT id FROM campaigns WHERE guild_id = ? AND name_key = ?",
            (guild_id, name_key(name)),
        ).fetchone()
        return row is not None and row["id"] != except_id

    def _insert(
        self,
        guild_id: int,
        name: str,
        target: str,
        fallback: str,
        optional_default: bool,
        created_at: int,
        last_played_at: int | None,
        visibility: str,
    ) -> str:
        campaign_id = uuid.uuid4().hex
        self._db.execute(
            "INSERT INTO campaigns (id, guild_id, name, name_key, created_at, last_played_at,"
            " target_ruleset, fallback_ruleset, optional_rules_default, dm_screen_visibility)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                campaign_id,
                guild_id,
                name,
                name_key(name),
                created_at,
                last_played_at,
                target,
                fallback,
                int(optional_default),
                visibility,
            ),
        )
        return campaign_id

    def _create(
        self,
        guild_id: int,
        raw_name: str,
        dm_user_id: int,
        target: str,
        fallback: str,
        optional_default: bool,
        visibility: str,
    ) -> Campaign:
        name = clean_name(raw_name)
        check_rulesets(target, fallback)
        check_dm_screen_visibility(visibility)
        with transaction(self._db):
            if self._name_taken(guild_id, name):
                raise CampaignError("This server already has a campaign with that name.")
            campaign_id = self._insert(
                guild_id,
                name,
                target,
                fallback,
                optional_default,
                int(self._clock()),
                None,
                visibility,
            )
            self._db.execute(
                "INSERT INTO campaign_dms (campaign_id, guild_id, user_id) VALUES (?, ?, ?)",
                (campaign_id, guild_id, dm_user_id),
            )
        return self._require(guild_id, campaign_id)

    def _rename(self, guild_id: int, campaign_id: str, raw_name: str) -> Campaign:
        name = clean_name(raw_name)
        with transaction(self._db):
            self._require(guild_id, campaign_id)
            if self._name_taken(guild_id, name, except_id=campaign_id):
                raise CampaignError("This server already has a campaign with that name.")
            self._db.execute(
                "UPDATE campaigns SET name = ?, name_key = ? WHERE guild_id = ? AND id = ?",
                (name, name_key(name), guild_id, campaign_id),
            )
        return self._require(guild_id, campaign_id)

    def _set_rulesets(
        self, guild_id: int, campaign_id: str, target: str, fallback: str
    ) -> Campaign:
        check_rulesets(target, fallback)
        with transaction(self._db):
            self._require(guild_id, campaign_id)
            self._db.execute(
                "UPDATE campaigns SET target_ruleset = ?, fallback_ruleset = ?"
                " WHERE guild_id = ? AND id = ?",
                (target, fallback, guild_id, campaign_id),
            )
        return self._require(guild_id, campaign_id)

    def _set_optional_default(self, guild_id: int, campaign_id: str, enabled: bool) -> Campaign:
        return self._set_column(guild_id, campaign_id, "optional_rules_default", int(enabled))

    _SETTABLE = frozenset(
        {
            "dm_screen_channel_id",
            "last_voice_channel_id",
            "last_played_at",
            "optional_rules_default",
            "dm_screen_visibility",
        }
    )

    def _set_column(self, guild_id: int, campaign_id: str, column: str, value: object) -> Campaign:
        if column not in self._SETTABLE:  # column names can't be bound as parameters
            raise ValueError(f"Not a settable campaign column: {column}")
        with transaction(self._db):
            self._require(guild_id, campaign_id)
            self._db.execute(
                f"UPDATE campaigns SET {column} = ? WHERE guild_id = ? AND id = ?",
                (value, guild_id, campaign_id),
            )
        return self._require(guild_id, campaign_id)

    def _set_optional_rule(
        self, guild_id: int, campaign_id: str, rule_id: str, enabled: bool
    ) -> None:
        if not _valid_rule_id(rule_id):
            raise ValueError(f"Invalid optional rule id: {rule_id!r}")
        with transaction(self._db):
            self._require(guild_id, campaign_id)
            self._db.execute(
                "INSERT OR REPLACE INTO campaign_optional_rules"
                " (campaign_id, guild_id, rule_id, enabled) VALUES (?, ?, ?, ?)",
                (campaign_id, guild_id, rule_id, int(enabled)),
            )

    def _optional_overrides(self, guild_id: int, campaign_id: str) -> dict[str, bool]:
        self._require(guild_id, campaign_id)
        rows = self._db.execute(
            "SELECT rule_id, enabled FROM campaign_optional_rules"
            " WHERE guild_id = ? AND campaign_id = ?",
            (guild_id, campaign_id),
        )
        return {r["rule_id"]: bool(r["enabled"]) for r in rows}

    def _add_dm(self, guild_id: int, campaign_id: str, user_id: int) -> Campaign:
        with transaction(self._db):
            self._require(guild_id, campaign_id)
            self._db.execute(
                "INSERT OR IGNORE INTO campaign_dms (campaign_id, guild_id, user_id)"
                " VALUES (?, ?, ?)",
                (campaign_id, guild_id, user_id),
            )
        return self._require(guild_id, campaign_id)

    def _remove_dm(self, guild_id: int, campaign_id: str, user_id: int) -> Campaign:
        with transaction(self._db):
            campaign = self._require(guild_id, campaign_id)
            if user_id in campaign.dm_user_ids and len(campaign.dm_user_ids) == 1:
                raise CampaignError("A campaign needs at least one DM. Add the new DM first.")
            self._db.execute(
                "DELETE FROM campaign_dms WHERE guild_id = ? AND campaign_id = ? AND user_id = ?",
                (guild_id, campaign_id, user_id),
            )
        return self._require(guild_id, campaign_id)

    def _delete(self, guild_id: int, campaign_id: str) -> None:
        with transaction(self._db):
            self._require(guild_id, campaign_id)
            for section in self._sections.values():
                section.clear(self._db, guild_id, campaign_id)
            self._db.execute(
                "DELETE FROM campaigns WHERE guild_id = ? AND id = ?", (guild_id, campaign_id)
            )

    def _export(self, guild_id: int, campaign_id: str) -> dict[str, Any]:
        with read_snapshot(self._db):
            return self._export_now(guild_id, campaign_id)

    def _export_now(self, guild_id: int, campaign_id: str) -> dict[str, Any]:
        campaign = self._require(guild_id, campaign_id)
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
            },
            "sections": {
                name: section.dump(self._db, guild_id, campaign_id)
                for name, section in self._sections.items()
            },
        }

    def _import(
        self,
        guild_id: int,
        data: object,
        importer_id: int,
        replace_campaign_id: str | None,
    ) -> Campaign:
        info, sections = _validate_backup(data, set(self._sections))
        with transaction(self._db):
            if replace_campaign_id is not None:
                existing = self._require(guild_id, replace_campaign_id)
                if importer_id not in existing.dm_user_ids:
                    raise CampaignError("Only this campaign's DM can replace it with a backup.")
                campaign_id = replace_campaign_id
                for section in self._sections.values():
                    section.clear(self._db, guild_id, campaign_id)
                self._db.execute(
                    "UPDATE campaigns SET target_ruleset = ?, fallback_ruleset = ?,"
                    " optional_rules_default = ?, last_played_at = ?,"
                    " dm_screen_visibility = ?"
                    " WHERE guild_id = ? AND id = ?",
                    (
                        info["target_ruleset"],
                        info["fallback_ruleset"],
                        int(info["optional_rules_default"]),
                        info["last_played_at"],
                        info["dm_screen_visibility"],
                        guild_id,
                        campaign_id,
                    ),
                )
            else:
                name = self._free_name(guild_id, info["name"])
                campaign_id = self._insert(
                    guild_id,
                    name,
                    info["target_ruleset"],
                    info["fallback_ruleset"],
                    info["optional_rules_default"],
                    info["created_at"],
                    info["last_played_at"],
                    info["dm_screen_visibility"],
                )
            for name, rows in sections.items():
                try:
                    self._sections[name].load(self._db, guild_id, campaign_id, rows)
                except CampaignError:
                    raise
                except (ValueError, TypeError, KeyError, AttributeError, OverflowError) as exc:
                    raise CampaignError(
                        "This backup file is damaged or isn't a DMbot campaign backup."
                    ) from exc
            self._db.execute(
                "INSERT OR IGNORE INTO campaign_dms (campaign_id, guild_id, user_id)"
                " VALUES (?, ?, ?)",
                (campaign_id, guild_id, importer_id),
            )
        return self._require(guild_id, campaign_id)

    def _free_name(self, guild_id: int, base: str) -> str:
        """`base`, or `base (restored)`, `base (restored 2)`, … whichever is free."""
        if not self._name_taken(guild_id, base):
            return base
        for n in range(1, 1000):
            suffix = " (restored)" if n == 1 else f" (restored {n})"
            candidate = clean_name(base[: NAME_MAX - len(suffix)].rstrip() + suffix)
            if not self._name_taken(guild_id, candidate):
                return candidate
        raise CampaignError("Too many campaigns with that name. Rename one and try again.")


def encode_backup(backup: dict[str, Any]) -> bytes:
    """Serialise a backup for the DM to download. Run off the event loop for big ones."""
    return json.dumps(backup, ensure_ascii=False, indent=1).encode("utf-8")


def decode_backup(raw: bytes) -> object:
    """Parse an uploaded backup file, refusing oversized or non-JSON files."""
    if len(raw) > MAX_BACKUP_BYTES:
        raise CampaignError("That backup file is too big.")
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise CampaignError("That file isn't a DMbot campaign backup.") from exc


def _validate_backup(
    data: object, known_sections: set[str]
) -> tuple[dict[str, Any], dict[str, list[Any]]]:
    """Check a backup's shape before touching the database. Raises CampaignError."""
    damaged = "This backup file is damaged or isn't a DMbot campaign backup."
    if not isinstance(data, dict) or data.get("format") != EXPORT_FORMAT:
        raise CampaignError(damaged)
    version = data.get("version")
    if not isinstance(version, int) or isinstance(version, bool):
        raise CampaignError(damaged)
    if version > EXPORT_VERSION:
        raise CampaignError("This backup was made by a newer DMbot. Update DMbot and try again.")
    campaign = data.get("campaign")
    sections = data.get("sections")
    if not isinstance(campaign, dict) or not isinstance(sections, dict):
        raise CampaignError(damaged)

    raw_name = campaign.get("name")
    if not isinstance(raw_name, str):
        raise CampaignError(damaged)
    target = campaign.get("target_ruleset")
    fallback = campaign.get("fallback_ruleset")
    if not isinstance(target, str) or not isinstance(fallback, str):
        raise CampaignError(damaged)
    check_rulesets(target, fallback)

    def timestamp(value: object, *, optional: bool) -> int | None:
        if value is None and optional:
            return None
        if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= INT64_MAX:
            return value
        raise CampaignError(damaged)

    optional_default = campaign.get("optional_rules_default", True)
    if not isinstance(optional_default, bool):
        raise CampaignError(damaged)
    # Backups made before this setting existed fall back to the default.
    visibility = campaign.get("dm_screen_visibility", DEFAULT_DM_SCREEN_VISIBILITY)
    if not isinstance(visibility, str):
        raise CampaignError(damaged)
    check_dm_screen_visibility(visibility)

    unknown = set(sections) - known_sections
    if unknown:
        raise CampaignError("This backup was made by a newer DMbot. Update DMbot and try again.")
    for rows in sections.values():
        if not isinstance(rows, list):
            raise CampaignError(damaged)

    info = {
        "name": clean_name(raw_name),
        "created_at": timestamp(campaign.get("created_at"), optional=False),
        "last_played_at": timestamp(campaign.get("last_played_at"), optional=True),
        "target_ruleset": target,
        "fallback_ruleset": fallback,
        "optional_rules_default": optional_default,
        "dm_screen_visibility": visibility,
    }
    return info, {name: list(rows) for name, rows in sections.items()}
