"""The private scenes kept for a campaign (#2996 rule 5, `memory_private_scenes`): found after a
session from the game clock and who spoke, the DM's "Not private" kept through a re-run, and every
consent path. Needs Postgres (skipped without it); invented names and numbers."""

from __future__ import annotations

import asyncio
from typing import Any

from dmbot.campaigns.models import CampaignError
from dmbot.consent import still_recorded
from dmbot.memory.claims import ClaimStore
from dmbot.memory.models import CONFIRMED
from dmbot.memory.private_scene_store import Kept, PrivateScenes, forget_speaker, keep_recorded
from dmbot.memory.private_scenes_backup import PrivateScenesSection
from dmbot.transcript.models import Line
from dmbot.transcript.store import TranscriptStore
from tests.agreed import agree
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
        await agree(self.db, GUILD, DM, P1, P2, P3)  # (the writer re-reads it, #4153)
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

    async def said(self, *lines: tuple[int, int], text: str = "words") -> None:
        await self.transcripts.add_lines(
            GUILD, self.sid, [Line(ms(at), who, text, "x") for at, who in lines]
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

    async def test_a_deletion_holding_the_session_runs_wholly_before_the_write(self) -> None:
        """#4091: the write waits on the session's row, which a deletion locks first, so it never
        cites someone whose lines the deletion took (one lock order: the row, then the lines)."""
        await self.said((650, P1), (670, P2))
        async with self.db.guild(GUILD) as conn:  # (the deletion, holding the row mid-way)
            await conn.execute(
                "SELECT 1 FROM transcript_sessions WHERE guild_id = %s AND id = %s FOR UPDATE",
                (GUILD, self.sid),
            )
            writing = asyncio.create_task(self.write())
            try:
                await asyncio.sleep(0.3)
                self.assertFalse(writing.done())  # (it waits for the row)
            except BaseException:
                writing.cancel()  # (never left running against an open transaction)
                raise
            await conn.execute(
                "DELETE FROM transcript_lines WHERE guild_id = %s AND user_id = %s", (GUILD, P2)
            )
        written = await asyncio.wait_for(writing, 10)
        self.assertEqual(written.kept, 0)
        self.assertEqual(await self.all_rows(), [])

    async def test_a_writer_that_read_agreed_before_a_stop_cites_nobody_who_stopped(self) -> None:
        """#4153: the writer's list came before the stop (it still holds P2); the consent row went
        before the write, so the writer's own re-read under the row leaves P2 out."""
        await self.said((650, P1), (670, P2))
        async with self.db.guild(GUILD) as conn:  # (as `consent.revoke` does)
            await conn.execute("DELETE FROM consent WHERE guild_id = %s AND user_id = %s",
                               (GUILD, P2))  # fmt: skip
        self.assertEqual((await self.write({P1, P2, DM})).kept, 0)
        self.assertEqual(await self.all_rows(), [])

    async def test_the_forget_waits_for_a_writer_holding_the_session(self) -> None:
        """#4153's lock order: "Stop recording me" takes the rows of the sessions the person
        spoke in first, so a writer mid-write (holding one, `FOR SHARE`) finishes first, and the
        forget then deletes what it wrote."""
        await self.said((650, P1), (670, P2))
        async with self.db.guild(GUILD) as conn:  # (the writer, holding the row mid-way)
            await conn.execute(
                "SELECT 1 FROM transcript_sessions WHERE guild_id = %s AND id = %s FOR SHARE",
                (GUILD, self.sid),
            )
            forgetting = asyncio.create_task(ClaimStore(self.memory).forget_speaker(GUILD, P2))
            try:
                await asyncio.sleep(0.3)
                self.assertFalse(forgetting.done())  # (it waits for the writer)
            except BaseException:
                forgetting.cancel()
                raise
        await asyncio.wait_for(forgetting, 10)
        await self.write()  # (a writer that starts now finds the forget done)
        await ClaimStore(self.memory).forget_speaker(GUILD, P2)  # (the second pass, after it)
        self.assertEqual(await self.all_rows(), [])

    async def test_a_speaker_without_discord_is_never_read_as_stopped(self) -> None:
        async with self.db.guild(GUILD) as conn:
            kept = await still_recorded(conn, GUILD, self.cid, [-5, P1, 999_999])
        self.assertEqual(kept, frozenset({-5, P1}))  # (999999 never agreed: left out)

    async def test_nothing_while_the_party_is_together(self) -> None:
        await self.said((100, P1), (110, P2))
        self.assertEqual((await self.write()).kept, 0)

    async def test_a_rerun_replaces_what_it_found(self) -> None:
        await self.said((650, P1), (670, P2))
        await self.write()
        await self.write()
        self.assertEqual(len(await self.kept()), 1)

    async def test_an_entry_said_in_it_is_at_least_recurring_until_not_private(self) -> None:
        """#4110 (Supervisor's ruling): the floor is worked out on read from the entries said in a
        kept scene, so "Not private" undoes it, and no assessment is ever stored for it."""
        from dmbot.memory.importance_rank import ImportanceStore

        wolf = await self.memory.add_entity(
            GUILD, self.cid, name="Greyfang", type="character", status=CONFIRMED, source="dm"
        )
        quiet = await self.memory.add_entity(
            GUILD, self.cid, name="Mossback", type="character", status=CONFIRMED, source="dm"
        )
        await self.said((650, P1), text="We follow Greyfang tonight.")
        await self.said((655, P3), text="Mossback waits by the gate.")  # (the other group)
        await self.said((670, P2), text="Agreed.")
        await self.write()
        ranks = ImportanceStore(self.db)
        got = await ranks.ranks(GUILD, self.cid)
        self.assertEqual((got[wolf.value.id], got[quiet.value.id]), (2, 0))
        (scene,) = await self.kept()
        await self.scenes.not_private(GUILD, self.cid, scene.id, 9)
        self.assertEqual((await ranks.ranks(GUILD, self.cid))[wolf.value.id], 0)
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                "SELECT count(*) AS n FROM memory_importance WHERE guild_id = %s", (GUILD,)
            )
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual(row["n"], 0)  # (nothing stored for it)

    async def test_a_speaker_who_stops_takes_the_floor_with_the_scene(self) -> None:
        from dmbot.memory.importance_rank import ImportanceStore

        wolf = await self.memory.add_entity(
            GUILD, self.cid, name="Greyfang", type="character", status=CONFIRMED, source="dm"
        )
        await self.said((650, P1), text="Greyfang!")
        await self.said((670, P2))
        await self.write()
        await ClaimStore(self.memory).forget_speaker(GUILD, P2)  # ("Stop recording me")
        self.assertEqual((await ImportanceStore(self.db).ranks(GUILD, self.cid))[wolf.value.id], 0)

    async def test_a_restore_keeps_entries_only_for_a_scene_wholly_recorded_there(self) -> None:
        """#4110: the floor reads no consent of its own, so a restore into a server where one of
        the two is not recorded brings the scene back without the entries that raise a rank."""
        from dmbot.memory.importance_rank import ImportanceStore

        wolf = await self.memory.add_entity(
            GUILD, self.cid, name="Greyfang", type="character", status=CONFIRMED, source="dm"
        )
        await self.said((650, P1), text="Greyfang!")
        await self.said((670, P2))
        await self.write()
        ranks = ImportanceStore(self.db)
        async with self.db.guild(GUILD) as conn:
            rows = await PrivateScenesSection().dump(conn, GUILD, self.cid)
        self.assertEqual(rows[0]["entries"], [wolf.value.id])
        for recorded, rank in (({P1}, 0), ({P1, P2}, 2)):

            async def consenting(_: int, who: set[int] = recorded) -> frozenset[int]:
                return frozenset(who)

            section = PrivateScenesSection(consenting)
            async with self.db.guild(GUILD) as conn:
                await section.clear(conn, GUILD, self.cid)
                await section.load(conn, GUILD, self.cid, section.check(rows))
            self.assertEqual((await ranks.ranks(GUILD, self.cid))[wolf.value.id], rank, recorded)

    async def test_only_a_scene_not_found_before_is_new(self) -> None:
        await self.said((650, P1), (670, P2))
        self.assertEqual((await self.write()).new, 1)
        self.assertEqual((await self.write()).new, 0)  # (a re-read: the note says nothing again)

    async def test_roots_follow_a_merge(self) -> None:
        ashe = await self.pc("Ashe", P2)
        await self.memory.merge(GUILD, self.cid, ashe, self.ash, source="dm", dm_said_same=True)
        roots = await self.scenes.roots(GUILD, self.cid, [self.ash, self.wren])
        self.assertEqual(roots, {self.ash: ashe, self.wren: self.wren})

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

    async def test_the_dms_mark_follows_a_merged_character(self) -> None:
        await self.said((650, P1), (670, P2))
        await self.write()
        (scene,) = await self.kept()
        await self.scenes.not_private(GUILD, self.cid, scene.id, 9)
        # (Ash turns out to be a second entry for the same character, kept as "Ashe")
        ashe = await self.pc("Ashe", P2)
        await self.memory.merge(GUILD, self.cid, ashe, self.ash, source="dm", dm_said_same=True)
        async with self.db.guild(GUILD) as conn:
            await conn.execute(
                "INSERT INTO game_clock_group_log (guild_id, campaign_id, character_id,"
                " position, at) VALUES (%s, %s, %s, 1, %s)",
                (GUILD, self.cid, ashe, SPLIT),
            )
        written = await self.write()
        self.assertEqual((written.kept, written.not_private), (0, 1))

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
            self.assertEqual(good["entries"], [])  # (nothing named in its lines)
            old = {k: v for k, v in good.items() if k != "entries"}  # (a file from before #4110)
            section.check([old])
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
            {**good, "entries": ["not an id"]},
        ):
            with self.assertRaises(CampaignError):
                section.check([bad])
        with self.assertRaises(CampaignError):
            section.check([good, {**good, "id": "f" * 32}])  # (one scene once)
