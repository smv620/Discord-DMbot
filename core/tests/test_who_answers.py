"""The stored answers to "Who is this?" (#3602, slice 2b): kept until the name is answered, wiped
with their speakers, one campaign at a time. Real Postgres; invented names."""

from __future__ import annotations

from typing import Any

from dmbot.memory.claims import ClaimStore
from dmbot.memory.who_answers import ENTITY, NEW, StoredAnswer, WhoAnswers, forget
from tests.test_web_relationships import Web

DM_ID, ALICE, BOB_ID = 7, 8, 9
RECORDED = {DM_ID, ALICE}


class Base(Web):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.answers = WhoAnswers(self.memory)
        made = await self.memory.add_unnamed(
            self.g, self.one.id, what="a patron at the bar", anchor_id=self.mira
        )
        self.waiting = made.entity.id

    def same(self, target: str | None = None, how: str = "likely") -> StoredAnswer:
        return StoredAnswer(ENTITY, how, target_id=target or self.mira)

    def new(self, kind: str = "character", how: str = "maybe") -> StoredAnswer:
        return StoredAnswer(NEW, how, new_kind=kind)

    async def put(
        self,
        *answers: StoredAnswer,
        speakers: tuple[int, ...] = (ALICE,),
        recorded: set[int] = RECORDED,
        entity: str | None = None,
        prompt_version: str = "who-1",
    ) -> int:
        return await self.answers.keep(
            self.g,
            self.one.id,
            entity or self.waiting,
            list(answers),
            speaker_ids=speakers,
            opted_in=recorded,
            session_started_at=500,
            prompt_version=prompt_version,
            model="m",
        )

    async def of(self, *ids: str, recorded: set[int] = RECORDED) -> Any:
        return await self.answers.of(
            self.g, self.one.id, list(ids or [self.waiting]), opted_in=recorded
        )

    async def sql(self, query: str, *args: Any) -> None:
        async with self.db.guild(self.g) as conn:
            await conn.execute(query, args)


class Keeping(Base):
    async def test_ranked_answers_are_kept_in_order_and_a_later_set_replaces_them(self) -> None:
        self.assertEqual(await self.put(self.same(), self.new()), 2)
        got = await self.of()
        self.assertEqual(got[self.waiting], [self.same(), self.new()])
        await self.put(self.new("place", "sure"))
        self.assertEqual((await self.of())[self.waiting], [self.new("place", "sure")])

    async def test_an_empty_set_clears_and_at_most_three_are_kept(self) -> None:
        await self.put(self.same(), self.new())
        self.assertEqual(await self.put(), 0)
        self.assertEqual(await self.of(), {})
        four = [self.new(k) for k in ("character", "place", "faction", "item")]
        self.assertEqual(await self.put(*four), 3)

    async def test_every_malformed_answer_is_left_out_and_never_fails_the_rest(self) -> None:
        bad = [
            StoredAnswer(ENTITY, "95%", target_id=self.mira),  # (a confidence off the three)
            StoredAnswer(NEW, "sure", new_kind="monster"),  # (a kind off the list)
            StoredAnswer(ENTITY, "sure", target_id="not-an-id"),
            StoredAnswer(
                ENTITY, "sure", target_id=self.waiting
            ),  # (an entry is not its own answer)
            StoredAnswer(ENTITY, "sure"),  # (no target)
            StoredAnswer(NEW, "sure"),  # (no kind)
            StoredAnswer(ENTITY, "sure", target_id=self.mira, new_kind="place"),
            StoredAnswer("other", "sure", target_id=self.mira),
        ]
        self.assertEqual(await self.put(*bad, self.same()), 1)
        self.assertEqual(await self.put(self.same(), prompt_version=" "), 0)

    async def test_answers_worked_out_from_someone_not_recorded_now_are_not_kept(self) -> None:
        self.assertEqual(await self.put(self.same(), speakers=(ALICE, BOB_ID)), 0)
        self.assertEqual(await self.of(), {})

    async def test_the_ones_with_no_answers_are_the_ones_worked_out_next(self) -> None:
        await self.put(self.same())
        got = await self.answers.missing(self.g, self.one.id, [self.waiting, self.mira])
        self.assertEqual(got, [self.mira])


class UntilAnswered(Base):
    async def test_the_answers_go_when_the_name_is_answered(self) -> None:
        await self.put(self.same())
        await self.sql("UPDATE memory_entities SET status = 'rejected' WHERE id = %s", self.waiting)
        self.assertEqual(await self.of(), {})

    async def test_the_answers_go_when_the_entry_they_name_is_gone(self) -> None:
        await self.put(self.same(), self.new())
        await self.sql("UPDATE memory_entities SET status = 'rejected' WHERE id = %s", self.mira)
        got = await self.of()
        self.assertEqual(got[self.waiting], [self.new()])  # (the "new" answer needs no entry)

    async def test_a_name_answered_by_a_merge_is_swept_too(self) -> None:
        await self.put(self.same())
        await self.sql(
            "UPDATE memory_entities SET merged_into = %s, status = 'merged' WHERE id = %s",
            self.mira,
            self.waiting,
        )
        self.assertEqual(await self.of(), {})


class OpenQuestions(Base):
    async def test_a_confirmed_unnamed_person_keeps_its_answers_and_a_named_one_loses_them(
        self,
    ) -> None:
        await self.sql(
            "UPDATE memory_entities SET status = 'confirmed' WHERE id = %s", self.waiting
        )
        await self.put(self.same())
        self.assertEqual(len((await self.of())[self.waiting]), 1)  # (still unnamed: still open)
        await self.sql(
            "UPDATE memory_entities SET unnamed = false, unnamed_what = NULL,"
            " unnamed_anchor = NULL, unnamed_role = NULL WHERE id = %s",
            self.waiting,
        )
        self.assertEqual(await self.of(), {})  # (named now: answered)

    async def test_an_answer_naming_an_entry_merged_away_is_swept(self) -> None:
        await self.put(self.same(), self.new())
        await self.sql(
            "UPDATE memory_entities SET merged_into = %s, status = 'merged' WHERE id = %s",
            self.waiting,
            self.mira,
        )
        self.assertEqual((await self.of())[self.waiting], [self.new()])

    async def test_a_second_keep_for_the_same_entry_replaces_and_trims_the_ranks(self) -> None:
        await self.put(self.same(), self.new(), self.new("place"))
        await self.put(self.new("item", "sure"))
        self.assertEqual((await self.of())[self.waiting], [self.new("item", "sure")])


class Consent(Base):
    async def test_purge_stopped_deletes_by_speaker_and_counts(self) -> None:
        await self.put(self.same(), self.new())
        self.assertEqual(await self.answers.purge_stopped(self.g, self.one.id, RECORDED), 0)
        self.assertEqual(await self.answers.purge_stopped(self.g, self.one.id, {DM_ID}), 2)

    async def test_forgetting_a_speaker_leaves_another_servers_rows_alone(self) -> None:
        await self.put(self.same())
        async with self.db.guild(self.g) as conn:
            self.assertEqual(await forget(conn, self.g + 1, ALICE), 0)
        self.assertEqual(len((await self.of())[self.waiting]), 1)

    async def test_a_stopped_speaker_wipes_the_answers_before_they_are_read(self) -> None:
        await self.put(self.same())
        self.assertEqual(await self.of(recorded={DM_ID}), {})

    async def test_stop_recording_me_wipes_theirs_in_every_campaign_of_the_server(self) -> None:
        other = await self.memory.add_unnamed(
            self.g, self.two.id, what="a stranger", anchor_id=None
        )
        await self.put(self.same())
        await self.answers.keep(
            self.g, self.two.id, other.entity.id, [self.new()], speaker_ids=(ALICE,),
            opted_in=RECORDED, session_started_at=1, prompt_version="who-1", model="m",
        )  # fmt: skip
        async with self.db.guild(self.g) as conn:
            self.assertEqual(await forget(conn, self.g, ALICE), 2)
        self.assertEqual(await self.of(), {})

    async def test_the_claim_stores_stop_recording_me_reaches_them(self) -> None:
        await self.put(self.same())
        await ClaimStore(self.memory).forget_speaker(self.g, ALICE)
        self.assertEqual(await self.of(), {})

    async def test_answers_for_the_dms_own_document_have_no_speakers_and_stay(self) -> None:
        await self.put(self.same(), speakers=())
        self.assertEqual(len((await self.of(recorded=set()))[self.waiting]), 1)


class OneCampaign(Base):
    async def test_another_campaigns_answers_are_untouched_and_unseen(self) -> None:
        other = await self.memory.add_unnamed(
            self.g, self.two.id, what="a stranger", anchor_id=None
        )
        await self.answers.keep(
            self.g, self.two.id, other.entity.id, [self.new()], speaker_ids=(), opted_in=set(),
            session_started_at=1, prompt_version="who-1", model="m",
        )  # fmt: skip
        self.assertEqual(await self.of(other.entity.id), {})
        mine = await self.answers.of(self.g, self.two.id, [other.entity.id], opted_in=set())
        self.assertEqual(len(mine[other.entity.id]), 1)
