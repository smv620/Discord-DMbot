"""The private scenes kept for a campaign (#2996 rule 5, `memory_private_scenes`): found after a
session from the game clock and who spoke, the DM's "Not private" kept through a re-run, and every
consent path. Needs Postgres (skipped without it); invented names and numbers."""

from __future__ import annotations

from typing import Any

from dmbot.campaigns.models import CampaignError
from dmbot.memory.claims import ClaimStore
from dmbot.memory.models import CONFIRMED
from dmbot.memory.private_scene_store import Kept, PrivateScenes, forget_speaker, keep_recorded
from dmbot.memory.private_scenes_backup import PrivateScenesSection
from dmbot.transcript.models import Line
from dmbot.transcript.store import TranscriptStore
from tests.test_memory_names import DM, GUILD, NamesTest

P1, P2, P3 = 7001, 7002, 7003
START = 1_700_000_000  # (the session's start; the party splits 600 s in)
SPLIT = START + 600
EVERYONE = {DM, P1, P2, P3}


def ms(seconds_in: int) -> int:
    return (START + seconds_in) * 1000


class PrivateSceneStore(NamesTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.cid = self.campaign.id
        self.scenes = PrivateScenes(self.db)
        self.transcripts = TranscriptStore(self.db)
        self.wren = await self.pc("Wren", P1)
        self.ash = await self.pc("Ash", P2)
        self.bram = await self.pc("Bram", P3)
        self.sid = await self.transcripts.open_session(GUILD, self.cid, START)
        async with self.db.guild(GUILD) as conn:
            await conn.execute(
                "INSERT INTO game_clock_log (guild_id, campaign_id, position, at, minute)"
                " VALUES (%s, %s, NULL, %s, 0), (%s, %s, 1, %s, 0)",
                (GUILD, self.cid, START, GUILD, self.cid, SPLIT),
            )
            for character, group in ((self.wren, 1), (self.ash, 1), (self.bram, 2)):
                await conn.execute(
                    "INSERT INTO game_clock_group_log (guild_id, campaign_id, character_id,"
                    " position, at) VALUES (%s, %s, %s, %s, %s)",
                    (GUILD, self.cid, character, group, SPLIT),
                )

    async def pc(self, name: str, player: int) -> str:
        made = await self.memory.add_entity(
            GUILD, self.cid, name=name, type="character", role="player_character",
            played_by=player, status=CONFIRMED, source="dm",
        )  # fmt: skip
        return str(made.value.id)

    async def said(self, *lines: tuple[int, int]) -> None:
        await self.transcripts.add_lines(
            GUILD, self.sid, [Line(ms(at), who, "words", "x") for at, who in lines]
        )

    async def write(self, opted_in: set[int] = EVERYONE) -> Any:
        return await self.scenes.write_session(GUILD, self.cid, self.sid, opted_in, 5)

    async def kept(self) -> list[Kept]:
        return await self.scenes.listed(GUILD, self.cid, EVERYONE)

    async def all_rows(self) -> list[Any]:
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                "SELECT status, speaker_ids FROM memory_private_scenes WHERE guild_id = %s"
                " ORDER BY from_ms",
                (GUILD,),
            )
            return list(await cur.fetchall())

    async def test_two_characters_alone_both_speaking_are_kept_ids_and_times_only(self) -> None:
        await self.said((650, P1), (655, P3), (660, DM), (670, P2))
        written = await self.write()
        self.assertEqual((written.kept, written.not_private), (1, 0))
        (scene,) = await self.kept()
        self.assertEqual((scene.first_id, scene.second_id), tuple(sorted((self.wren, self.ash))))
        self.assertEqual((scene.session_started_at, scene.from_ms, scene.to_ms, scene.line_count),
                         (START, ms(650), ms(670), 2))  # fmt: skip

    async def test_nothing_while_the_party_is_together(self) -> None:
        await self.said((100, P1), (110, P2))
        self.assertEqual((await self.write()).kept, 0)

    async def test_a_rerun_replaces_what_it_found(self) -> None:
        await self.said((650, P1), (670, P2))
        await self.write()
        await self.write()
        self.assertEqual(len(await self.kept()), 1)

    async def test_a_scene_found_again_keeps_its_id(self) -> None:
        await self.said((650, P1), (670, P2))
        await self.write()
        (first,) = await self.kept()
        await self.said((680, P1))
        await self.write()
        (again,) = await self.kept()
        self.assertEqual((again.id, again.to_ms), (first.id, ms(680)))

    async def test_a_speakers_removal_in_one_campaign_leaves_others(self) -> None:
        await self.said((650, P1), (670, P2))
        await self.write()
        async with self.db.guild(GUILD) as conn:
            await forget_speaker(conn, GUILD, P1, "f" * 32)  # (another campaign)
        self.assertEqual(len(await self.kept()), 1)
        async with self.db.guild(GUILD) as conn:
            await forget_speaker(conn, GUILD, P1, self.cid)
        self.assertEqual(await self.kept(), [])

    async def test_a_move_strips_the_dms_mark_of_who_is_not_recorded_there(self) -> None:
        await self.said((650, P1), (670, P2))
        await self.write()
        (scene,) = await self.kept()
        await self.scenes.not_private(GUILD, self.cid, scene.id, 9)
        async with self.db.guild(GUILD) as conn:
            await keep_recorded(conn, GUILD, self.cid, {P2})
        self.assertEqual([(r["status"], list(r["speaker_ids"])) for r in await self.all_rows()],
                         [("not_private", [P2])])  # fmt: skip

    async def test_the_dms_not_private_survives_a_rerun(self) -> None:
        await self.said((650, P1), (670, P2))
        await self.write()
        (scene,) = await self.kept()
        self.assertTrue(await self.scenes.not_private(GUILD, self.cid, scene.id, 9))
        self.assertFalse(await self.scenes.not_private(GUILD, self.cid, scene.id, 9))  # (once)
        await self.said((680, P1))  # (the scene grows on a re-read: still the same one)
        written = await self.write()
        self.assertEqual((written.kept, written.not_private), (0, 1))
        self.assertEqual(await self.kept(), [])

    async def test_someone_not_recorded_now_is_not_read(self) -> None:
        await self.said((650, P1), (670, P2))
        self.assertEqual((await self.write({DM, P1, P3})).kept, 0)

    async def test_a_row_citing_someone_not_recorded_is_not_listed(self) -> None:
        await self.said((650, P1), (670, P2))
        await self.write()
        self.assertEqual(await self.scenes.listed(GUILD, self.cid, {DM, P1}), [])

    async def test_it_goes_with_the_sessions_transcript_but_the_dms_mark_stays(self) -> None:
        await self.said((650, P1), (670, P2), (700, P3))
        await self.transcripts.end_session(GUILD, self.sid, START + 1000)
        await self.write()
        (scene,) = await self.kept()
        await self.scenes.not_private(GUILD, self.cid, scene.id, 9)
        async with self.db.guild(GUILD) as conn:  # (and one kept scene beside it)
            await conn.execute(
                "INSERT INTO memory_private_scenes (guild_id, campaign_id, id, first_id,"
                " second_id, session_started_at, from_ms, to_ms, line_count, speaker_ids, status,"
                " created_at) VALUES (%s, %s, %s, %s, %s, %s, 1, 2, 2, %s, 'kept', 5)",
                (GUILD, self.cid, "c" * 32, *sorted((self.wren, self.ash)), START, [P1, P2]),
            )
        gone = await self.transcripts.delete_session_transcript(
            GUILD, self.cid, self.sid, 2_000_000_000, 1
        )
        self.assertEqual(gone, 3)  # (the control: its three lines did go)
        self.assertEqual([r["status"] for r in await self.all_rows()], ["not_private"])

    async def test_stop_recording_me_takes_the_scene_and_strips_the_mark(self) -> None:
        await self.said((650, P1), (670, P2))
        await self.write()
        (scene,) = await self.kept()
        await self.scenes.not_private(GUILD, self.cid, scene.id, 9)
        await ClaimStore(self.memory).forget_speaker(GUILD, P2)  # ("Stop recording me")
        rows = await self.all_rows()
        self.assertEqual([(r["status"], list(r["speaker_ids"])) for r in rows],
                         [("not_private", [P1])])  # fmt: skip

    async def test_delete_my_transcripts_takes_the_scene(self) -> None:
        await self.said((650, P1), (670, P2))
        await self.write()
        await self.transcripts.delete_person(GUILD, P1)
        self.assertEqual(await self.all_rows(), [])

    async def test_a_move_keeps_only_what_cites_people_recorded_there(self) -> None:
        await self.said((650, P1), (670, P2))
        await self.write()
        async with self.db.guild(GUILD) as conn:
            await keep_recorded(conn, GUILD, self.cid, {P1, P2})
        self.assertEqual(len(await self.kept()), 1)
        async with self.db.guild(GUILD) as conn:
            await keep_recorded(conn, GUILD, self.cid, {P1})
        self.assertEqual(await self.all_rows(), [])

    async def test_a_backup_carries_scenes_and_marks_and_refuses_a_damaged_row(self) -> None:
        await self.said((650, P1), (670, P2))
        await self.write()
        (scene,) = await self.kept()
        await self.scenes.not_private(GUILD, self.cid, scene.id, 9)
        section = PrivateScenesSection()
        async with self.db.guild(GUILD) as conn:
            (good,) = await section.dump(conn, GUILD, self.cid)
            self.assertEqual((good["status"], good["decided_at"]), ("not_private", 9))
            await section.clear(conn, GUILD, self.cid)
            await section.load(conn, GUILD, self.cid, section.check([good]))
        self.assertEqual([r["status"] for r in await self.all_rows()], ["not_private"])
        for bad in (
            {**good, "first_id": good["second_id"], "second_id": good["first_id"]},
            {**good, "line_count": 1},
            {**good, "decided_at": None},
            {**good, "status": "kept"},  # (a kept row has no DM decision)
            {**good, "from_ms": True},
            {**good, "words": "x"},
        ):
            with self.assertRaises(CampaignError):
                section.check([bad])
        with self.assertRaises(CampaignError):
            section.check([good, {**good, "id": "f" * 32}])  # (one scene once)
