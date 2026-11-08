"""The stored scene of a question (#3596): kept until answered, wiped with its speakers, DM-only
at the door, carried in a backup. Real Postgres; invented names."""

from __future__ import annotations

from typing import Any

from dmbot.campaigns.models import CampaignError
from dmbot.memory.claims import ClaimStore
from dmbot.memory.scenes import CLAIM, ENTITY, Scene, Scenes, forget
from dmbot.memory.scenes_backup import ScenesSection
from dmbot.web.recaps import _scoped
from tests.test_web_relationships import Web

DM_ID, ALICE, BOB_ID = 7, 8, 9
RECORDED = {DM_ID, ALICE}
TEXT = "When the party entered the tavern, this someone was just leaving."


class Base(Web):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.scenes = Scenes(self.memory)
        made = await self.memory.add_unnamed(
            self.g, self.one.id, what="a patron at the bar", anchor_id=self.mira
        )
        self.someone = made.entity.id

    async def put(self, *scenes: Scene, recorded: set[int] = RECORDED) -> int:
        return await self.scenes.keep(
            self.g,
            self.one.id,
            list(scenes),
            opted_in=recorded,
            prompt_version="scenes-1",
            model="m",
        )

    def scene(self, subject: str | None = None, speakers: tuple[int, ...] = (ALICE,)) -> Scene:
        return Scene(ENTITY, subject or self.someone, TEXT, 4, speakers, 500)

    async def of(self, *ids: str, recorded: set[int] = RECORDED) -> Any:
        return await self.scenes.of(self.g, self.one.id, ENTITY, list(ids), opted_in=recorded)


class Keeping(Base):
    async def test_a_scene_is_kept_for_an_open_question_and_a_later_one_replaces_it(self) -> None:
        self.assertEqual(await self.put(self.scene()), 1)
        self.assertEqual(await self.of(self.someone), {self.someone: TEXT})
        await self.put(Scene(ENTITY, self.someone, "A newer telling.", 2, (ALICE,), 600))
        self.assertEqual(await self.of(self.someone), {self.someone: "A newer telling."})

    async def test_a_scene_made_from_someone_not_recorded_now_is_not_kept(self) -> None:
        self.assertEqual(await self.put(self.scene(speakers=(ALICE, BOB_ID))), 0)
        self.assertEqual(await self.of(self.someone), {})

    async def test_a_scene_from_a_document_has_no_speakers_and_is_kept(self) -> None:
        self.assertEqual(await self.put(self.scene(speakers=())), 1)
        self.assertEqual(await self.of(self.someone, recorded=set()), {self.someone: TEXT})

    async def test_an_empty_scene_or_an_unknown_kind_is_ignored(self) -> None:
        bad = Scene("session", self.someone, TEXT, 1, (ALICE,))
        self.assertEqual(await self.put(Scene(ENTITY, self.someone, "  ", 1, (ALICE,)), bad), 0)

    async def test_the_ones_with_no_scene_are_the_ones_asked_for_next(self) -> None:
        await self.put(self.scene())
        got = await self.scenes.missing(self.g, self.one.id, ENTITY, [self.someone, self.mira])
        self.assertEqual(got, [self.mira])


class UntilAnswered(Base):
    async def test_naming_the_person_deletes_the_scene(self) -> None:
        await self.put(self.scene())
        async with self.db.guild(self.g) as conn:
            await conn.execute(
                "UPDATE memory_entities SET unnamed = false, unnamed_what = NULL,"
                " unnamed_anchor = NULL, unnamed_role = NULL, name = 'Pell',"
                " status = 'confirmed' WHERE id = %s",
                (self.someone,),
            )
        # (named but linked to no one at the table: the graph clean-up still shows it, #3740)
        self.assertEqual(len(await self.of(self.someone)), 1)
        await self.link("knows", self.rook, self.someone, "rook-pell")
        self.assertEqual(await self.of(self.someone), {})

    async def test_removing_or_merging_the_person_deletes_the_scene(self) -> None:
        for change in ("status = 'rejected'", f"status = 'merged', merged_into = '{self.mira}'"):
            with self.subTest(change):
                other = (
                    await self.memory.add_unnamed(self.g, self.one.id, what="a stranger")
                ).entity
                await self.put(self.scene(other.id))
                self.assertEqual(len(await self.of(other.id)), 1)
                async with self.db.guild(self.g) as conn:
                    await conn.execute(
                        f"UPDATE memory_entities SET {change} WHERE id = %s", (other.id,)
                    )
                self.assertEqual(await self.of(other.id), {})

    async def test_a_claim_scene_goes_when_the_claim_is_answered(self) -> None:
        claim = "c" * 32
        async with self.db.guild(self.g) as conn:
            await conn.execute(
                "INSERT INTO memory_claims (guild_id, campaign_id, id, subject, relationship,"
                " object, details, how, lines, speaker_ids, source, prompt_version, model, status,"
                " reason, created_at) VALUES (%s, %s, %s, 'a', 'b', 'c', '', 'dm_said', '{1}',"
                " '{8}', 'extractor', 'p', 'm', 'proposed', '', 1)",
                (self.g, self.one.id, claim),
            )
        await self.put(Scene(CLAIM, claim, TEXT, 3, (ALICE,), 500))
        got = await self.scenes.of(self.g, self.one.id, CLAIM, [claim], opted_in=RECORDED)
        self.assertEqual(got, {claim: TEXT})
        async with self.db.guild(self.g) as conn:
            await conn.execute(
                "UPDATE memory_claims SET status = 'rejected' WHERE id = %s", (claim,)
            )
        got = await self.scenes.of(self.g, self.one.id, CLAIM, [claim], opted_in=RECORDED)
        self.assertEqual(got, {})

    async def test_a_proposed_name_has_its_scene_until_it_is_confirmed(self) -> None:
        made = await self.memory.add_entity(
            self.g, self.one.id, type="npc", name="Orsk", source="scan", status="proposed"
        )
        await self.put(self.scene(made.value.id))
        self.assertEqual(len(await self.of(made.value.id)), 1)
        async with self.db.guild(self.g) as conn:
            await conn.execute(
                "UPDATE memory_entities SET status = 'confirmed' WHERE id = %s", (made.value.id,)
            )
        self.assertEqual(len(await self.of(made.value.id)), 1)  # (isolated: kept, #3740)
        await self.link("knows", self.rook, made.value.id, "rook-orsk")
        self.assertEqual(await self.of(made.value.id), {})


class Consent(Base):
    async def test_a_speaker_who_stops_wipes_the_scene_before_it_is_shown(self) -> None:
        await self.put(self.scene(speakers=(DM_ID, ALICE)))
        self.assertEqual(await self.of(self.someone, recorded={ALICE}), {})
        self.assertEqual(await self.of(self.someone), {})  # (deleted, not hidden)

    async def test_stop_recording_me_deletes_theirs_in_every_campaign(self) -> None:
        await self.put(self.scene(speakers=(ALICE,)))
        async with self.db.guild(self.g) as conn:
            self.assertEqual(await forget(conn, self.g, ALICE), 1)
        self.assertEqual(await self.of(self.someone), {})

    async def test_the_claims_stop_recording_me_path_clears_them_too(self) -> None:
        await self.put(self.scene(speakers=(ALICE,)))
        await ClaimStore(self.memory).forget_speaker(self.g, ALICE)
        self.assertEqual(await self.of(self.someone), {})


class OnTheDoor(Base):
    async def door(self, who: Any, ids: list[str], kind: str = ENTITY) -> Any:
        async with self.web.user(who.user_id, session=who.id_hash) as conn:
            await _scoped(conn, self.g)
            cur = await conn.execute(
                "SELECT dmbot_web_scenes(%s, %s, %s, %s) AS s", (self.g, self.one.id, kind, ids)
            )
            found = await cur.fetchone()
        assert found is not None
        return found["s"]

    async def test_a_dm_is_given_the_scenes_asked_for(self) -> None:
        await self.put(self.scene())
        got = await self.door(self.session, [self.someone, self.mira])
        self.assertEqual(got, {"status": "ok", "scenes": {self.someone: TEXT}})

    async def test_a_player_or_a_member_who_is_not_a_dm_is_given_nothing_at_all(self) -> None:
        await self.put(self.scene())
        self.assertIsNone(await self.door(self.player, [self.someone]))

    async def test_an_answered_question_is_not_given_even_before_the_sweep(self) -> None:
        await self.put(self.scene())
        async with self.db.guild(self.g) as conn:
            await conn.execute(
                "UPDATE memory_entities SET status = 'rejected' WHERE id = %s", (self.someone,)
            )
        self.assertEqual((await self.door(self.session, [self.someone]))["scenes"], {})

    async def test_another_kind_or_another_campaign_is_not_read(self) -> None:
        await self.put(self.scene())
        self.assertEqual((await self.door(self.session, [self.someone], CLAIM))["scenes"], {})
        self.assertEqual((await self.door(self.session, []))["scenes"], {})


class Backup(Base):
    async def test_a_backup_round_trips_the_scenes(self) -> None:
        await self.put(self.scene())
        section = ScenesSection()
        async with self.db.guild(self.g) as conn:
            rows = await section.dump(conn, self.g, self.one.id)
            await section.clear(conn, self.g, self.one.id)
        self.assertEqual(await self.of(self.someone), {})
        async with self.db.guild(self.g) as conn:
            await section.load(conn, self.g, self.one.id, section.check(rows))
        self.assertEqual(await self.of(self.someone), {self.someone: TEXT})

    async def test_a_damaged_section_is_refused_whole(self) -> None:
        await self.put(self.scene())
        async with self.db.guild(self.g) as conn:
            (good,) = await ScenesSection().dump(conn, self.g, self.one.id)
        ScenesSection().check([good])
        for bad in (
            {**good, "subject_kind": "session"}, {**good, "subject_id": "x"},
            {**good, "scene": ""}, {**good, "scene": "x" * 601}, {**good, "speaker_ids": [True]},
            {**good, "lines_n": -1}, {**good, "extra": 1},
        ):  # fmt: skip
            with self.assertRaises(CampaignError):
                ScenesSection().check([bad])
        with self.assertRaises(CampaignError):
            ScenesSection().check([good, good])  # one scene for a question
