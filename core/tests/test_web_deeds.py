"""The website's "Deeds the party did" (#3591, migration 0604): only a DM of the campaign reads or
answers it; the door lists the deeds waiting for a yes in Discord's order, leaves out a deed whose
speaker is no longer recorded, one already answered and another campaign's; the answers go to the
one function Discord's card uses (`deed_card.apply_answer`) and come back as a short code, never a
word; the Undo takes back only this DM's own recorded deed. Postgres. Invented names."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from dmbot.consent import TERMS_VERSION
from dmbot.memory import deed_proposals
from dmbot.memory import web_actions as wa
from dmbot.memory.deed_proposals import WAITING_MAX, DeedProposals
from dmbot.memory.deed_reading import DeedProposal
from dmbot.memory.models import MemoryRuleError
from dmbot.ui import deed_card
from dmbot.web import deeds, relationships, sessions
from dmbot.web.recaps import RecapError
from tests.test_recap_web import BOB, NOW
from tests.test_web_api import ALICE, THURSDAY
from tests.test_web_relationships import Web

SAID = 4242  # a speaker (a Discord-shaped number made up for the test)
OTHER = 4343


class DeedsOnTheWeb(Web):
    bob_has_a_hero = False

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.actions = wa.ActionStore(self.db)
        self.proposals = DeedProposals(self.memory)
        self.cerric = await self.add("Cerric", "character", role="player_character")
        self.brindle = await self.add("Brindle", "place")
        for user in (SAID, OTHER):
            await self.agree(user)
        self.recorded = {SAID, OTHER}

    async def agree(self, user: int, terms: int | None = None) -> None:
        async with self.db.guild(self.g) as conn:
            await conn.execute(
                "INSERT INTO consent (guild_id, user_id, granted_at, terms_version)"
                " VALUES (%s, %s, 0, %s) ON CONFLICT (guild_id, user_id)"
                " DO UPDATE SET terms_version = EXCLUDED.terms_version",
                (self.g, user, TERMS_VERSION if terms is None else terms),
            )

    async def stop(self, user: int) -> None:
        async with self.db.guild(self.g) as conn:
            await conn.execute(
                "DELETE FROM consent WHERE guild_id = %s AND user_id = %s", (self.g, user)
            )

    async def put(
        self,
        doer: str = "Cerric",
        viewer: str = "Brindle",
        text: str = "Saved the mill",
        *,
        speakers: tuple[int, ...] = (SAID,),
        campaign: str | None = None,
        kind: str = "help",
    ) -> None:
        stored = await self.proposals.store(
            self.g,
            campaign or self.one.id,
            [(DeedProposal(doer, text, (viewer,), "Helped them", ("1", "2"), kind), speakers)],
            opted_in=self.recorded,
            session_started_at=500,
            prompt_version="deeds-1",
            model="m",
        )
        self.assertEqual(stored.stored, 1)

    async def read(self, who: Any = None, ref: str | None = None) -> dict[str, Any]:
        return await deeds.read(
            self.web, who or self.session, ref or self.cref, outside_engine=None
        )

    async def answer(self, choice: str, **more: Any) -> str:
        return await deeds.queue_answer(
            self.web, self.session, self.cref, {"choice": choice, **more}
        )

    def fake_bot(self) -> Any:
        async def consenting(guild_id: int) -> frozenset[int]:
            return frozenset(self.recorded)

        return SimpleNamespace(memory=self.memory, consent=SimpleNamespace(consenting=consenting))

    async def discord_way(
        self, guild_id: int, campaign_id: str, proposal_id: str, record: bool
    ) -> tuple[str, int | None]:
        """What the bot hands the applier: Discord's own function, on a stand-in bot."""
        return await deed_card.apply_answer(
            self.fake_bot(), guild_id, campaign_id, proposal_id, record=record
        )

    async def bot_loop(self, dms: Any = None) -> list[wa.Outcome]:
        done = []
        while (action := await self.actions.claim_next(self.g)) is not None:
            outcome = await wa.apply(
                self.memory, self.g, dms or {ALICE.id}, action, actions=self.actions,
                deeds=self.discord_way,
            )  # fmt: skip
            await self.actions.finish(
                self.g, action, error=outcome.error, result_counts=outcome.counts,
                result_batches=outcome.batches,
            )  # fmt: skip
            done.append(outcome)
        return done

    async def status_of(self, proposal: str) -> str | None:
        async with self.db.guild(self.g) as conn:
            cur = await conn.execute(
                "SELECT status FROM memory_deed_proposals WHERE id = %s", (proposal,)
            )
            row = await cur.fetchone()
        return None if row is None else str(row["status"])

    async def bob(self) -> Any:
        who: Any = BOB
        found = await sessions.find(
            self.web, await sessions.sign_in(self.web, who, [THURSDAY], now=NOW, days=30), now=NOW
        )
        assert found is not None
        return found

    # The door

    async def test_a_dm_reads_the_waiting_deeds_with_their_words_and_nothing_else(self) -> None:
        await self.put(kind="rescue")
        got = await self.read()
        (deed,) = got["deeds"]
        self.assertEqual(got["total"], 1)
        self.assertEqual(
            {k: v for k, v in deed.items() if k != "id"},
            {
                "doer": "Cerric",
                "text": "Saved the mill",
                "viewers": ["Brindle"],
                "effect": "Helped them",
                "kind": "rescue",
            },
        )
        self.assertEqual(len(deed["id"]), 32)
        self.assertNotIn("lines", str(got))  # (no line numbers, speakers or session)
        self.assertNotIn(str(SAID), str(got))

    async def test_the_order_is_discords_and_the_page_is_limited_with_the_total(self) -> None:
        for n in range(WAITING_MAX + 3):
            await self.put(text=f"Deed {n:02d}", viewer=f"Place {n:02d}")
        # (the bot lists them in the same order, without changing any)
        discord = await self.proposals.waiting(self.g, self.one.id, opted_in=self.recorded)
        got = await self.read()
        self.assertEqual(got["total"], WAITING_MAX + 3)
        self.assertEqual(len(got["deeds"]), WAITING_MAX)
        self.assertEqual([d["id"] for d in got["deeds"]], [w.id for w in discord])

    async def test_a_deed_with_a_speaker_who_stopped_is_not_listed_or_counted(self) -> None:
        await self.put(text="Mine", speakers=(SAID,))
        await self.put(text="Theirs", viewer="Elsewhere", speakers=(SAID, OTHER))
        self.assertEqual((await self.read())["total"], 2)
        await self.stop(OTHER)
        got = await self.read()
        self.assertEqual((got["total"], [d["text"] for d in got["deeds"]]), (1, ["Mine"]))
        await self.agree(OTHER, terms=TERMS_VERSION + 1)  # (agreed to older words: not recorded)
        self.assertEqual((await self.read())["total"], 1)

    async def test_a_deed_answered_either_way_is_not_listed(self) -> None:
        await self.put(text="One")
        await self.put(text="Two", viewer="Elsewhere")
        await self.put(text="Three", viewer="Nowhere")
        by_text = {d["text"]: d["id"] for d in (await self.read())["deeds"]}
        first, second = by_text["One"], by_text["Two"]
        await self.proposals.dismiss(self.g, self.one.id, first, source="dm")
        await self.sql("UPDATE memory_deed_proposals SET status = 'recorded' WHERE id = %s", second)
        got = await self.read()
        self.assertEqual((got["total"], [d["text"] for d in got["deeds"]]), (1, ["Three"]))

    async def test_another_campaigns_deeds_are_never_listed(self) -> None:
        await self.put(text="Mine")
        await self.put(text="Bob's", campaign=self.two.id)
        self.assertEqual([d["text"] for d in (await self.read())["deeds"]], ["Mine"])
        # Bob (the DM of Two) reads his own, and not One's
        bob = await self.bob()
        got = await self.read(bob, f"{self.g}.{self.two.id}")
        self.assertEqual([d["text"] for d in got["deeds"]], ["Bob's"])
        with self.assertRaises(RecapError) as err:
            await self.read(bob)
        self.assertEqual(err.exception.code, "not_allowed")
        with self.assertRaises(RecapError) as err2:
            await self.read(self.session, f"{self.g}.{self.two.id}")  # Alice, not Two's DM
        self.assertEqual(err2.exception.code, "not_allowed")

    async def test_a_player_and_a_stranger_get_nothing(self) -> None:
        await self.put()
        for who in (self.player, await self.signed(BOB, THURSDAY)):
            with self.assertRaises(RecapError) as err:
                await self.read(who)
            self.assertEqual(err.exception.code, "not_allowed")
            with self.assertRaises(RecapError) as queued:
                await deeds.queue_answer(
                    self.web, who, self.cref, {"choice": "not", "deed": "a" * 32}
                )
            self.assertEqual(queued.exception.code, "not_allowed")
        self.assertIsNone(await self.actions.claim_next(self.g))

    async def test_a_bad_body_is_refused_before_anything_is_queued(self) -> None:
        bodies: list[object] = [
            5, {}, {"choice": "later"}, {"choice": "record"}, {"choice": "record", "deed": "x"},
            {"choice": "not", "deed": "A" * 32}, {"choice": "undo"},
            {"choice": "undo", "batch": "12"}, {"choice": "undo", "batch": True},
            {"choice": "undo", "batch": -1}, {"choice": "undo", "batch": 10**15},
        ]  # fmt: skip
        for body in bodies:
            with self.assertRaises(RecapError) as err:
                await deeds.queue_answer(self.web, self.session, self.cref, body)
            self.assertEqual(err.exception.code, "bad_action", body)
        self.assertIsNone(await self.actions.claim_next(self.g))

    # The answers

    async def test_record_writes_the_deed_once_and_answers_recorded_with_a_batch(self) -> None:
        await self.put()
        (deed,) = (await self.read())["deeds"]
        answer_id = await self.answer("record", deed=deed["id"])
        (done,) = await self.bot_loop()
        self.assertEqual((done.error, done.counts, len(done.batches)), ("", "deed:recorded", 1))
        self.assertEqual(await self.status_of(deed["id"]), "recorded")
        self.assertEqual((await self.read())["deeds"], [])
        read = await relationships.read_answer(self.web, self.session, self.cref, answer_id)
        self.assertEqual(
            read, {"state": "done", "error": "", "code": "recorded", "batch": done.batches[0]}
        )
        # a second press finds it answered, and writes nothing more
        before = await self.memory.version(self.g, self.one.id)
        await self.answer("record", deed=deed["id"])
        (again,) = await self.bot_loop()
        self.assertEqual(again.error, wa.GONE)
        self.assertEqual(await self.memory.version(self.g, self.one.id), before)

    async def test_not_this_dismisses_it_and_writes_no_deed(self) -> None:
        await self.put()
        (deed,) = (await self.read())["deeds"]
        before = await self.memory.version(self.g, self.one.id)
        answer_id = await self.answer("not", deed=deed["id"])
        (done,) = await self.bot_loop()
        self.assertEqual((done.error, done.counts, done.batches), ("", "deed:not", ()))
        self.assertEqual(await self.status_of(deed["id"]), "dismissed")
        self.assertEqual(await self.memory.version(self.g, self.one.id), before)
        read = await relationships.read_answer(self.web, self.session, self.cref, answer_id)
        self.assertEqual((read["state"], read["code"], read["batch"]), ("done", "not", None))

    async def test_a_deed_already_answered_or_made_up_is_gone(self) -> None:
        await self.put()
        (deed,) = (await self.read())["deeds"]
        await self.proposals.dismiss(self.g, self.one.id, deed["id"], source="dm")
        for choice in ("record", "not"):
            await self.answer(choice, deed=deed["id"])
        await self.answer("record", deed="b" * 32)
        done = await self.bot_loop()
        self.assertEqual([o.error for o in done], ["gone", "gone", "gone"])

    async def test_a_speaker_who_stopped_since_the_page_was_read_makes_it_gone(self) -> None:
        await self.put()
        (deed,) = (await self.read())["deeds"]
        before = await self.memory.version(self.g, self.one.id)
        self.recorded = {OTHER}  # (the bot asks who is recorded again, as Discord's card does)
        await self.answer("record", deed=deed["id"])
        (done,) = await self.bot_loop()
        self.assertEqual((done.error, done.batches), ("gone", ()))
        # (as on Discord's card, the deed of someone who stopped is deleted, never recorded)
        self.assertIsNone(await self.status_of(deed["id"]))
        self.assertEqual((await self.memory.version(self.g, self.one.id)), before)

    async def test_a_name_dmbot_does_not_know_is_a_code_with_no_words(self) -> None:
        await self.put(doer="Nobody Known")
        (deed,) = (await self.read())["deeds"]
        answer_id = await self.answer("record", deed=deed["id"])
        (done,) = await self.bot_loop()
        self.assertEqual((done.error, done.counts, done.batches), ("refused", "", ()))
        self.assertEqual(await self.status_of(deed["id"]), "proposed")  # (still waiting)
        read = await relationships.read_answer(self.web, self.session, self.cref, answer_id)
        self.assertEqual((read["state"], read["error"]), ("failed", "refused"))
        self.assertNotIn("Nobody", str(read))

    async def test_an_answer_from_someone_who_is_not_a_dm_now_is_refused(self) -> None:
        await self.put()
        (deed,) = (await self.read())["deeds"]
        await self.answer("record", deed=deed["id"])
        (done,) = await self.bot_loop(dms={BOB.id})
        self.assertEqual(done.error, wa.NOT_ALLOWED)
        self.assertEqual(await self.status_of(deed["id"]), "proposed")

    async def test_the_undo_takes_back_this_dms_own_recorded_deed(self) -> None:
        await self.put()
        (deed,) = (await self.read())["deeds"]
        await self.answer("record", deed=deed["id"])
        (done,) = await self.bot_loop()
        (batch,) = done.batches
        # a made-up number and a deed that was only turned down never reach `undo`
        await self.answer("undo", batch=batch + 1000)
        (bad,) = await self.bot_loop()
        self.assertEqual(bad.error, wa.GONE)
        answer_id = await self.answer("undo", batch=batch)
        (undone,) = await self.bot_loop()
        self.assertEqual(undone.error, "")
        read = await relationships.read_answer(self.web, self.session, self.cref, answer_id)
        self.assertEqual((read["state"], read["error"]), ("done", ""))
        # a second Undo of the same change is taken as done (a retry), not an error
        await self.answer("undo", batch=batch)
        (retry,) = await self.bot_loop()
        self.assertEqual(retry.error, "")

    async def test_an_undo_of_a_change_that_was_not_a_website_deed_is_refused(self) -> None:
        # a deed recorded from Discord's card (or by the other DM) is not in this DM's own answers
        await self.put()
        (deed,) = (await self.read())["deeds"]
        _words, batch = await self.discord_way(self.g, self.one.id, deed["id"], True)
        assert batch is not None
        await self.answer("undo", batch=batch)
        (refused,) = await self.bot_loop()
        self.assertEqual(refused.error, wa.GONE)

    async def test_an_undo_with_no_recorded_deed_of_this_dm_is_refused(self) -> None:
        await self.put()
        (deed,) = (await self.read())["deeds"]
        await self.answer("not", deed=deed["id"])
        await self.bot_loop()
        await self.answer("undo", batch=1)
        (refused,) = await self.bot_loop()
        self.assertEqual(refused.error, wa.GONE)

    async def test_an_undo_past_the_stores_limit_is_too_late(self) -> None:
        await self.put()
        (deed,) = (await self.read())["deeds"]
        await self.answer("record", deed=deed["id"])
        (done,) = await self.bot_loop()
        await self.sql("DELETE FROM memory_changes WHERE batch = %s", done.batches[0])
        await self.answer("undo", batch=done.batches[0])
        (late,) = await self.bot_loop()
        self.assertEqual(late.error, wa.TOO_LATE)

    async def test_the_stores_own_words_never_reach_the_answer(self) -> None:
        async def refusing(*_a: Any) -> tuple[str, int | None]:
            raise MemoryRuleError("DMbot doesn't know who “Cerric” is yet.")

        await self.answer("record", deed="c" * 32)
        action = await self.actions.claim_next(self.g)
        assert action is not None
        done = await wa.apply(
            self.memory, self.g, {ALICE.id}, action, actions=self.actions, deeds=refusing
        )
        self.assertEqual((done.error, done.counts), ("refused", ""))
        self.assertNotIn("Cerric", str(done))
        self.assertEqual(deed_proposals.GONE, "That deed is no longer waiting.")

    async def test_the_queue_door_checks_each_actions_text_shape_itself(self) -> None:
        good = {"deed-record": "a" * 32, "deed-not": "a" * 32, "deed-undo": "123"}
        bad = {"deed-record": "a" * 31, "deed-not": "A" * 32, "deed-undo": "1" * 16}
        for action, text in good.items():
            await relationships._queue(
                self.web, self.session, self.cref, entity=None, action=action, target="",
                text=text, flag=False,
            )  # fmt: skip
        for action, text in bad.items():
            with self.assertRaises(RecapError) as err:
                await relationships._queue(
                    self.web, self.session, self.cref, entity=None, action=action, target="",
                    text=text, flag=False,
                )  # fmt: skip
            self.assertEqual(err.exception.code, "bad_name", action)
