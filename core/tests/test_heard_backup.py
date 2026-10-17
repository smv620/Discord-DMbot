"""Heard counts in campaign backups (#1747): a restored campaign keeps its quiet threads and
name-hint order, but only for people recorded now in the restoring server, and only for entries
it has. Needs Postgres."""

from __future__ import annotations

import copy
from typing import Any
from unittest import mock

from dmbot.campaigns import CampaignError
from dmbot.consent import ConsentStore
from dmbot.memory import heard_backup
from dmbot.memory.heard_backup import HEARD_ROWS_MAX, HeardSection
from dmbot.memory.models import GOING, OPEN, Heard
from tests.test_memory_store import DM, GUILD_A, GUILD_B, ConfirmedMemoryTest

S1, S2, S3, S4 = 1_700_000_000, 1_700_100_000, 1_700_200_000, 1_700_300_000
KEPT, GONE = 7, 8  # a speaker who is recorded in the restoring server, and one who is not


class HeardBackup(ConfirmedMemoryTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.consent = ConsentStore(self.db)
        self.campaigns.register_section(HeardSection(self.consent.consenting))
        for guild in (GUILD_A, GUILD_B):
            await self.consent.grant(guild, KEPT)
        self.mira = await self.add("Mira")
        self.heir = (
            await self.memory.new_thread(
                GUILD_A, self.c, "The Missing Heir", "quest", OPEN, source="dm"
            )
        ).value.id

    async def hear(self, session: int, who: str, speaker: int = KEPT, times: int = 1) -> None:
        await self.memory.add_session_heard(GUILD_A, self.c, session, [Heard(who, speaker, times)])

    async def heard_rows(self, guild: int, campaign: str) -> list[tuple[int, int, int]]:
        async with self.db.guild(guild) as conn:
            cur = await conn.execute(
                "SELECT session_started_at, speaker_id, times FROM memory_heard"
                " WHERE guild_id = %s AND campaign_id = %s ORDER BY 1, 2",
                (guild, campaign),
            )
            return [
                (int(r["session_started_at"]), int(r["speaker_id"]), int(r["times"]))
                for r in await cur.fetchall()
            ]

    async def stalled(self, guild: int, campaign: str) -> list[tuple[str, bool]]:
        found = await self.memory.threads(guild, campaign, stalled_after=3)
        return [(t.thread.name, t.stalled) for t in found]

    async def test_a_restore_keeps_a_thread_quiet_as_before(self) -> None:
        await self.memory.set_condition(
            GUILD_A, self.c, self.heir, GOING, source="dm", from_session_at=S1
        )
        for s in (S2, S3, S4):
            await self.hear(s, self.mira)
        before = await self.stalled(GUILD_A, self.c)
        self.assertEqual(before, [("The Missing Heir", True)])
        backup = await self.campaigns.export(GUILD_A, self.c)
        restored = await self.campaigns.import_backup(GUILD_B, backup, DM)
        self.assertEqual(await self.stalled(GUILD_B, restored.id), before)
        self.assertEqual(
            await self.heard_rows(GUILD_B, restored.id), await self.heard_rows(GUILD_A, self.c)
        )

    async def test_the_file_holds_counts_and_nothing_else(self) -> None:
        await self.hear(S1, self.mira, times=3)
        rows = (await self.campaigns.export(GUILD_A, self.c))["sections"]["memory_heard"]
        self.assertEqual(
            rows,
            [{"entity_id": self.mira, "session_started_at": S1, "speaker_id": KEPT, "times": 3}],
        )

    async def test_a_speaker_not_recorded_now_is_dropped_at_restore(self) -> None:
        await self.hear(S1, self.mira, KEPT, 2)
        await self.hear(S1, self.mira, GONE, 5)  # recorded where it was made, not in guild B
        backup = await self.campaigns.export(GUILD_A, self.c)
        self.assertEqual(len(backup["sections"]["memory_heard"]), 2)
        restored = await self.campaigns.import_backup(GUILD_B, backup, DM)
        self.assertEqual(await self.heard_rows(GUILD_B, restored.id), [(S1, KEPT, 2)])

    async def test_someone_who_stopped_being_recorded_is_dropped_too(self) -> None:
        await self.hear(S1, self.mira, KEPT)
        backup = await self.campaigns.export(GUILD_A, self.c)
        await self.consent.revoke(GUILD_B, KEPT)
        restored = await self.campaigns.import_backup(GUILD_B, backup, DM)
        self.assertEqual(await self.heard_rows(GUILD_B, restored.id), [])

    async def test_a_person_who_deleted_their_lines_has_no_counts_in_the_export(self) -> None:
        await self.hear(S1, self.mira, KEPT)
        await self.hear(S1, self.mira, GONE)
        async with self.db.guild(GUILD_A) as conn:  # what delete_person does to the table
            await conn.execute(
                "DELETE FROM memory_heard WHERE guild_id = %s AND speaker_id = %s",
                (GUILD_A, GONE),
            )
        rows = (await self.campaigns.export(GUILD_A, self.c))["sections"]["memory_heard"]
        self.assertEqual([r["speaker_id"] for r in rows], [KEPT])

    async def test_an_entry_the_campaign_does_not_have_is_dropped(self) -> None:
        await self.hear(S1, self.mira)
        backup = await self.campaigns.export(GUILD_A, self.c)
        backup["sections"]["memory_heard"].append(
            {"entity_id": "ab" * 16, "session_started_at": S2, "speaker_id": KEPT, "times": 1}
        )
        restored = await self.campaigns.import_backup(GUILD_B, backup, DM)
        self.assertEqual(await self.heard_rows(GUILD_B, restored.id), [(S1, KEPT, 1)])

    async def test_each_kind_of_damage_refuses_the_section_whole(self) -> None:
        await self.hear(S1, self.mira)
        good = (await self.campaigns.export(GUILD_A, self.c))["sections"]["memory_heard"][0]
        too_many: list[Any] = [
            {**good, "session_started_at": i + 1} for i in range(HEARD_ROWS_MAX + 1)
        ]
        damaged: dict[str, list[Any]] = {
            "bad entity id": [{**good, "entity_id": "nope"}],
            "negative count": [{**good, "times": -1}],
            "zero count": [{**good, "times": 0}],
            "boolean count": [{**good, "times": True}],
            "boolean speaker": [{**good, "speaker_id": True}],
            "text count": [{**good, "times": "3"}],
            "too big a count": [{**good, "times": 2**31}],
            "bad speaker": [{**good, "speaker_id": 0}],
            "bad time": [{**good, "session_started_at": -5}],
            "extra key": [{**good, "line": "words"}],
            "missing key": [{k: v for k, v in good.items() if k != "times"}],
            "duplicate": [good, dict(good)],
            "not a row": ["x"],
            "too many rows": too_many,
        }
        for what, rows in damaged.items():
            backup = await self.campaigns.export(GUILD_A, self.c)
            backup["sections"]["memory_heard"] = rows
            with self.subTest(what), self.assertRaises(CampaignError):
                await self.campaigns.import_backup(GUILD_B, backup, DM)
        backup = await self.campaigns.export(GUILD_A, self.c)
        backup["sections"]["memory_heard"] = {"not": "a list"}
        with self.assertRaises(CampaignError):
            await self.campaigns.import_backup(GUILD_B, backup, DM)

    async def test_an_older_backup_without_the_section_still_loads(self) -> None:
        await self.hear(S1, self.mira)
        backup = copy.deepcopy(await self.campaigns.export(GUILD_A, self.c))
        del backup["sections"]["memory_heard"]
        restored = await self.campaigns.import_backup(GUILD_B, backup, DM)
        self.assertEqual(await self.heard_rows(GUILD_B, restored.id), [])

    async def test_a_replace_swaps_the_counts_for_the_backups(self) -> None:
        await self.hear(S1, self.mira, KEPT, 2)
        backup = await self.campaigns.export(GUILD_A, self.c)
        await self.hear(S2, self.mira, KEPT, 9)  # heard after the backup was made
        await self.campaigns.import_backup(GUILD_A, backup, DM, replace_campaign_id=self.c)
        self.assertEqual(await self.heard_rows(GUILD_A, self.c), [(S1, KEPT, 2)])

    async def test_nothing_crosses_between_campaigns_or_servers(self) -> None:
        other = (await self.campaigns.create(GUILD_A, "Other table", DM)).id
        mine = await self.memory.add_entity(
            GUILD_A, other, type="npc", name="Mira", source="dm", status="confirmed"
        )
        await self.memory.add_session_heard(GUILD_A, other, S3, [Heard(mine.value.id, KEPT, 4)])
        await self.hear(S1, self.mira)
        backup = await self.campaigns.export(GUILD_A, self.c)
        self.assertEqual(
            [r["session_started_at"] for r in backup["sections"]["memory_heard"]], [S1]
        )
        restored = await self.campaigns.import_backup(GUILD_B, backup, DM)
        self.assertEqual(await self.heard_rows(GUILD_B, restored.id), [(S1, KEPT, 1)])
        self.assertEqual(await self.heard_rows(GUILD_A, other), [(S3, KEPT, 4)])
        self.assertEqual(await self.heard_rows(GUILD_A, self.c), [(S1, KEPT, 1)])

    async def test_a_stop_during_the_restore_removes_the_rows_again(self) -> None:
        await self.hear(S1, self.mira, KEPT)
        backup = await self.campaigns.export(GUILD_A, self.c)
        real = self.consent.consenting
        calls = 0

        async def stopping(guild: int) -> frozenset[int]:
            nonlocal calls
            calls += 1
            found = await real(guild)
            return found if calls == 1 else found - {KEPT}  # the stop lands after the insert

        self.campaigns._sections["memory_heard"] = HeardSection(stopping)
        restored = await self.campaigns.import_backup(GUILD_B, backup, DM)
        self.assertEqual(calls, 2)
        self.assertEqual(await self.heard_rows(GUILD_B, restored.id), [])

    async def test_an_empty_section_and_a_server_with_nobody_recorded_load_nothing(self) -> None:
        backup = await self.campaigns.export(GUILD_A, self.c)
        self.assertEqual(backup["sections"]["memory_heard"], [])
        restored = await self.campaigns.import_backup(GUILD_B, backup, DM)
        self.assertEqual(await self.heard_rows(GUILD_B, restored.id), [])
        await self.hear(S1, self.mira)
        await self.consent.revoke(GUILD_B, KEPT)
        again = await self.campaigns.import_backup(
            GUILD_B, await self.campaigns.export(GUILD_A, self.c), DM
        )
        self.assertEqual(await self.heard_rows(GUILD_B, again.id), [])

    async def test_a_replace_drops_a_speaker_who_has_since_stopped(self) -> None:
        await self.hear(S1, self.mira, KEPT)
        backup = await self.campaigns.export(GUILD_A, self.c)
        await self.consent.revoke(GUILD_A, KEPT)
        await self.campaigns.import_backup(GUILD_A, backup, DM, replace_campaign_id=self.c)
        self.assertEqual(await self.heard_rows(GUILD_A, self.c), [])

    async def test_past_the_cap_the_oldest_sessions_are_left_out_of_the_file(self) -> None:
        for s in (S1, S2, S3):
            await self.hear(s, self.mira)
        with mock.patch.object(heard_backup, "HEARD_ROWS_MAX", 2):
            rows = (await self.campaigns.export(GUILD_A, self.c))["sections"]["memory_heard"]
        self.assertEqual([r["session_started_at"] for r in rows], [S2, S3])

    def test_the_cap_itself_is_accepted_and_one_more_is_not(self) -> None:
        def rows(n: int) -> list[Any]:
            return [
                {"entity_id": "ab" * 16, "session_started_at": i + 1, "speaker_id": 5, "times": 1}
                for i in range(n)
            ]

        self.assertEqual(len(heard_backup._checked(rows(HEARD_ROWS_MAX))), HEARD_ROWS_MAX)
        with self.assertRaises(CampaignError):
            heard_backup._checked(rows(HEARD_ROWS_MAX + 1))
        for bad in (1.0, "1", 2**63):
            with self.assertRaises(CampaignError):
                heard_backup._checked([{**rows(1)[0], "speaker_id": bad}])
        self.assertEqual(
            heard_backup._checked([{**rows(1)[0], "speaker_id": 2**63 - 1}])[0][2], 2**63 - 1
        )
