# ruff: noqa: E501  (the seed rows are SQL text, kept one statement to a line of reading)
"""A row in every table a backup section is listed as carrying comes out the other side (#2552).

The list in `campaigns/tables.py` says which tables a section carries. This puts a row in each of
them, takes a backup with the production sections, restores it into another server and counts: a
table that is listed and not really carried shows up as a row that did not come back.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any, cast
from unittest.mock import AsyncMock

from dmbot.bot import register_backup_sections
from dmbot.campaigns import CampaignStore
from dmbot.campaigns.tables import SECTION_TABLES, backed_up
from dmbot.memory.models import new_id
from tests.pg import DatabaseTest

GUILD, OTHER, DM, SPEAKER = 111, 222, 9, 31
A, B = sorted((new_id(), new_id()))  # two names ("a" sorts first, as some tables require)
REL, ST, DOC = new_id(), new_id(), new_id()


class EveryClaimedTableComesBack(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.store = CampaignStore(self.db)
        register_backup_sections(
            self.store,
            forget_audio=lambda g, c: None,
            consenting=cast(
                Callable[[int], Awaitable[frozenset[int]]],
                AsyncMock(return_value=frozenset({SPEAKER})),
            ),
        )
        self.campaign = await self.store.create(GUILD, "Frostmaiden", DM)

    async def sql(self, statement: str, *args: Any) -> None:
        async with self.db.guild(GUILD) as conn:
            await conn.execute(statement, args)

    async def count(self, guild: int, campaign_id: str, table: str) -> int:
        async with self.db.guild(guild) as conn:
            cur = await conn.execute(
                f"SELECT count(*) AS n FROM {table} WHERE campaign_id = %s",
                (campaign_id,),
            )
            row = await cur.fetchone()
            assert row is not None
            return int(row["n"])

    def seeds(self) -> dict[str, tuple[str, tuple[Any, ...]]]:
        """One row for each claimed table that a new campaign does not already have."""
        g, c = GUILD, self.campaign.id
        return {
            "memory_entities": (
                "INSERT INTO memory_entities (guild_id, campaign_id, id, type, name, description,"
                " status, source, created_at, played_by, role)"
                " VALUES (%s, %s, %s, 'character', 'Aaa', '', 'confirmed', 'dm', 1, NULL, NULL),"
                " (%s, %s, %s, 'character', 'Bbb', '', 'confirmed', 'dm', 1, %s,"
                " 'player_character')",
                (g, c, A, g, c, B, DM),
            ),
            "memory_relations": (
                "INSERT INTO memory_relations (guild_id, campaign_id, id, subject_id, predicate,"
                " object_id, detail, confidence, status, source, mention_ids, secret, created_at,"
                " definition_version) VALUES (%s, %s, %s, %s, 'knows', %s, '', 1.0, 'confirmed',"
                " 'dm', '{}', false, 1, 1)",
                (g, c, REL, A, B),
            ),
            "memory_aliases": (
                "INSERT INTO memory_aliases (guild_id, campaign_id, id, entity_id, text, key, kind,"
                " secret, status, sound_codes, source, created_at)"
                " VALUES (%s, %s, %s, %s, 'Aa', 'aa', 'nickname', false, 'confirmed', '{}', 'dm', 1)",
                (g, c, new_id(), A),
            ),
            "memory_types": (
                "INSERT INTO memory_types (guild_id, campaign_id, key, parent, label, description,"
                " examples, reason, status, created_at)"
                " VALUES (%s, %s, 'ship', 'item', 'Ship', 'A vessel', '{}', 'ours', 'active', 1)",
                (g, c),
            ),
            "memory_predicates": (
                "INSERT INTO memory_predicates (guild_id, campaign_id, key, label, description,"
                " examples, reason, subject_types, object_types, is_symmetric, conflicts_with,"
                " status, created_at) VALUES (%s, %s, 'sails', 'sails', 'sails on', '{}', 'ours',"
                " '{character}', '{place}', false, '{}', 'active', 1)",
                (g, c),
            ),
            "memory_claims": (
                "INSERT INTO memory_claims (guild_id, campaign_id, id, subject, relationship,"
                " object, how, lines, source, prompt_version, model, status, created_at)"
                " VALUES (%s, %s, %s, 'Aaa', 'knows', 'Bbb', 'dm_said', '{}', 'extractor', 'v1', 'm',"
                " 'proposed', 1)",
                (g, c, new_id()),
            ),
            "memory_impressions": (
                "INSERT INTO memory_impressions (guild_id, campaign_id, id, viewer_id, subject_id,"
                " score, weight, source, created_at) VALUES (%s, %s, %s, %s, %s, 1, 1, 'dm', 1)",
                (g, c, new_id(), A, B),
            ),
            "memory_attitudes": (
                "INSERT INTO memory_attitudes (guild_id, campaign_id, id, holder_id, toward_id,"
                " score, weight, source, created_at) VALUES (%s, %s, %s, %s, %s, 1, 1, 'dm', 1)",
                (g, c, new_id(), A, B),
            ),
            "memory_beliefs": (
                "INSERT INTO memory_beliefs (guild_id, campaign_id, id, perspective_id,"
                " relation_id, confidence, status, seq, recorded_at)"
                " SELECT %s, %s, md5(random()::text || p.id), p.id, %s, 1.0, 'theory', 1, 1"
                " FROM memory_perspectives p"
                " WHERE p.guild_id = %s AND p.campaign_id = %s",
                (g, c, REL, g, c),
            ),
            "memory_states": (
                "INSERT INTO memory_states (guild_id, campaign_id, id, entity_id, condition, source,"
                " created_at) VALUES (%s, %s, %s, %s, 'dead', 'dm', 1)",
                (g, c, ST, A),
            ),
            "memory_flags": (
                "INSERT INTO memory_flags (guild_id, campaign_id, id, kind, relation_id, status,"
                " created_at) VALUES (%s, %s, %s, 'contradiction', %s, 'open', 1)",
                (g, c, new_id(), REL),
            ),
            "memory_corrections": (
                "INSERT INTO memory_corrections (guild_id, campaign_id, id, heard, heard_key,"
                " action, source, created_at) VALUES (%s, %s, %s, 'Aah', 'aah', 'keep', 'dm', 1)",
                (g, c, new_id()),
            ),
            "memory_rule_links": (
                "INSERT INTO memory_rule_links (guild_id, campaign_id, id, entity_id, kind,"
                " rules_source, name, known, created_at)"
                " VALUES (%s, %s, %s, %s, 'species', 'srd', 'Goblin', true, 1)",
                (g, c, new_id(), A),
            ),
            "character_sheets": (
                "INSERT INTO character_sheets (guild_id, campaign_id, entity_id, url, sheet, source,"
                " fetched_at, player_id, ask_mismatch) VALUES (%s, %s, %s,"
                " 'https://www.dndbeyond.com/characters/1', '{}'::jsonb, 'dndbeyond', 1, %s, false)",
                (g, c, B, DM),
            ),
            "memory_heard": (
                "INSERT INTO memory_heard (guild_id, campaign_id, entity_id, session_started_at,"
                " speaker_id, times) VALUES (%s, %s, %s, 100, %s, 2)",
                (g, c, A, SPEAKER),
            ),
            "house_rules": (
                "INSERT INTO house_rules (guild_id, campaign_id, number, rule, created_by,"
                " created_at, updated_at) VALUES (%s, %s, 1, 'No flanking', %s, 1, 1),"
                " (%s, %s, 2, 'Crits double', %s, 1, 1)",
                (g, c, DM, g, c, DM),
            ),
            "house_rule_wins": (
                "INSERT INTO house_rule_wins (guild_id, campaign_id, a_number, b_number, winner,"
                " decided_by, decided_at) VALUES (%s, %s, 1, 2, 'a', %s, 1)",
                (g, c, DM),
            ),
            "homebrew_entries": (
                "INSERT INTO homebrew_entries (guild_id, campaign_id, kind, name, name_key,"
                " created_by, created_at, updated_at, added_by, text)"
                " VALUES (%s, %s, 'item', 'Ring', 'ring', %s, 1, 1, %s, 'A ring.')",
                (g, c, DM, DM),
            ),
            "adventure_documents": (
                "INSERT INTO adventure_documents (guild_id, campaign_id, id, file_name, fingerprint,"
                " characters, chunks, added_by, added_at)"
                " VALUES (%s, %s, %s, 'a.txt', repeat('0', 64), 1, 0, %s, 1)",
                (g, c, DOC, DM),
            ),
            "plan_spend": (
                "INSERT INTO plan_spend (guild_id, campaign_id, id, fingerprint, month, micro_usd,"
                " input_tokens, output_tokens, at) VALUES (%s, %s, %s, repeat('0', 64), '2026-10',"
                " 5, 1, 1, 1)",
                (g, c, new_id()),
            ),
            "site_approvals": (
                "INSERT INTO site_approvals (guild_id, campaign_id, domain, approved_by,"
                " approved_at) VALUES (%s, %s, 'dndbeyond.com', %s, 1)",
                (g, c, DM),
            ),
            "map_notes": (
                "INSERT INTO map_notes (guild_id, campaign_id, user_id, made_at, alt, layout,"
                " places) VALUES (%s, %s, %s, 1, 'a map', 'rooms', '[]'::json)",
                (g, c, DM),
            ),
            "recap_style_notes": (
                "INSERT INTO recap_style_notes (guild_id, campaign_id, position, text, created_by,"
                " created_at) VALUES (%s, %s, 1, 'Short', %s, 1)",
                (g, c, DM),
            ),
            "recap_style_examples": (
                "INSERT INTO recap_style_examples (guild_id, campaign_id, position, text,"
                " created_by, created_at) VALUES (%s, %s, 1, 'Once...', %s, 1)",
                (g, c, DM),
            ),
            "import_speakers": (
                "INSERT INTO import_speakers (guild_id, campaign_id, label, user_id, updated_at)"
                " VALUES (%s, %s, 'Cerric', %s, 1)",
                (g, c, DM),
            ),
            "rule_set_follows": (
                "INSERT INTO rule_set_follows (guild_id, campaign_id, set_id, kind, set_name,"
                " publisher, version, content, updating, followed_by, followed_at)"
                " VALUES (%s, %s, %s, 'house_rules', 'Theirs', 'Pat', 1,"
                " '{\"rules\": []}'::jsonb, true, %s, 1)",
                (g, c, "a" * 32, DM),
            ),
            "game_clocks": (
                "INSERT INTO game_clocks (guild_id, campaign_id, minute, last_long_rest,"
                " updated_at, picked_group) VALUES (%s, %s, 5, 5, 1, 1)",
                (g, c),
            ),
            "game_effects": (
                "INSERT INTO game_effects (guild_id, campaign_id, number, name, minutes,"
                " ends_minute) VALUES (%s, %s, 1, 'Bless', 10, 15)",
                (g, c),
            ),
            "game_clock_groups": (
                "INSERT INTO game_clock_groups (guild_id, campaign_id, position, name, minute,"
                " last_long_rest) VALUES (%s, %s, 1, 'Scouts', 5, 5), (%s, %s, 2, 'Camp', 9, 9)",
                (g, c, g, c),
            ),
            "recap_jobs": (
                "INSERT INTO recap_jobs (guild_id, campaign_id, session_at, dm_user_id, state,"
                " title, summary, status, approved_at, created_at)"
                " VALUES (%s, %s, 100, %s, 'ready', 'Done', 'It happened.', 'posted', 5, 1)",
                (g, c, DM),
            ),
            "shared_confirmations": (
                "INSERT INTO shared_confirmations (guild_id, campaign_id, id, user_id, purpose,"
                " fingerprint, confirmed_at) VALUES (%s, %s, %s, %s, 'shared_story',"
                " repeat('0', 64), 1)",
                (g, c, new_id(), DM),
            ),
            "campaign_optional_rules": (
                "INSERT INTO campaign_optional_rules (guild_id, campaign_id, rule_id, enabled)"
                " VALUES (%s, %s, 'flanking', true)",
                (g, c),
            ),
        }

    async def test_a_row_in_every_claimed_table_comes_back(self) -> None:
        failed: dict[str, str] = {}
        for table, (statement, args) in self.seeds().items():
            try:
                await self.sql(statement, *args)
            except Exception as exc:
                failed[table] = f"{type(exc).__name__}: {str(exc)[:160]}"
        self.maxDiff = None
        self.assertEqual(failed, {}, "the seeds themselves must work")
        claimed = sorted(backed_up())
        before = {t: await self.count(GUILD, self.campaign.id, t) for t in claimed}
        empty = sorted(t for t, n in before.items() if n == 0)
        self.assertEqual(empty, [], "a claimed table with no row proves nothing")
        data = await self.store.export(GUILD, self.campaign.id)
        json.dumps(data)  # (what a download is made of)
        restored = await self.store.import_backup(OTHER, data, DM)
        after = {t: await self.count(OTHER, restored.id, t) for t in claimed}
        self.assertEqual(after, before)

    async def test_the_sections_listed_are_the_registered_ones(self) -> None:
        self.assertEqual(set(self.store._sections), set(SECTION_TABLES))
