"""The website's "Catch up" from an entry's page (#4793, migration 0560): only a DM asks or gives,
the bot answers with the store's own rule (ids and counts only), giving a member what the rest of
the party knows is one change with an Undo this DM's own done answer alone may take back, and
nothing left or a member not in the party is told so. Postgres. Invented names."""

from __future__ import annotations

from typing import Any

from dmbot.memory import web_actions as wa
from dmbot.memory.gate import ENTRY
from dmbot.memory.models import DM as DM_SOURCE
from dmbot.web import party_catch_up, party_record, relationships, sessions
from dmbot.web.recaps import RecapError
from tests.test_recap_web import BOB, NOW
from tests.test_web_api import ALICE, THURSDAY
from tests.test_web_relationships import Web


class CatchingUpOnTheWeb(Web):
    bob_has_a_hero = True

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.actions = wa.ActionStore(self.db)
        self.cara = await self.add("Cara", "character", role="player_character")
        assert self.hero is not None
        # the rest of the party knows Mira's card; Cara joined without it
        await self.memory.set_statement_knowers(
            self.g, self.one.id, ENTRY, self.mira, [self.rook, self.hero],
            source=DM_SOURCE, dm_chose_secret=True,
        )  # fmt: skip

    async def bot_loop(self) -> list[wa.Outcome]:
        done = []
        while (action := await self.actions.claim_next(self.g)) is not None:
            outcome = await wa.apply(self.memory, self.g, {ALICE.id}, action, actions=self.actions)
            await self.actions.finish(
                self.g, action, error=outcome.error, result_counts=outcome.counts,
                result_batches=outcome.batches,
            )  # fmt: skip
            done.append(outcome)
        return done

    async def choices(self) -> list[dict[str, Any]]:
        answer = await party_catch_up.queue_choices(
            self.web, self.session, self.cref, {"entry": self.mira}
        )
        await self.bot_loop()
        read = await relationships.read_answer(self.web, self.session, self.cref, answer)
        assert read["choices"] is not None
        return list(read["choices"])

    async def tell(self, member: str) -> tuple[str, wa.Outcome]:
        answer = await party_catch_up.queue_tell(
            self.web, self.session, self.cref, {"member": member, "entry": self.mira}
        )
        (done,) = await self.bot_loop()
        return answer, done

    async def test_the_answer_lists_who_is_missing_what_by_id_and_count(self) -> None:
        got = await self.choices()
        self.assertEqual([c["id"] for c in got], [self.cara])
        self.assertGreater(got[0]["count"], 0)

    async def test_giving_a_member_the_card_is_one_change_with_an_undo_of_their_own(self) -> None:
        [(_, count)] = [(c["id"], c["count"]) for c in await self.choices()]
        answer, done = await self.tell(self.cara)
        self.assertEqual((done.error, len(done.batches)), ("", 1))
        self.assertEqual(done.counts, f"told:{count}")
        read = await relationships.read_answer(self.web, self.session, self.cref, answer)
        self.assertEqual((read["told"], read["batch"]), (count, done.batches[0]))
        self.assertEqual(await self.choices(), [])  # (nothing left to tell)
        _again, nothing = await self.tell(self.cara)
        self.assertEqual(nothing.error, wa.NOTHING_LEFT)
        # the Undo (the party record's route) takes back only this DM's own done change
        await party_record.queue_undo(self.web, self.session, self.cref, str(done.batches[0] + 999))
        (bad,) = await self.bot_loop()
        self.assertEqual(bad.error, wa.GONE)
        await party_record.queue_undo(self.web, self.session, self.cref, str(done.batches[0]))
        (undone,) = await self.bot_loop()
        self.assertEqual(undone.error, "")
        self.assertEqual([c["id"] for c in await self.choices()], [self.cara])
        self.assertIsNone(
            await self.actions.party_batch_made(self.g, self.one.id, 12345, done.batches[0])
        )

    async def test_someone_not_in_the_party_is_told_so(self) -> None:
        await self.memory.leave_party(self.g, self.one.id, self.cara, "left")
        _answer, done = await self.tell(self.cara)
        self.assertEqual(done.error, wa.NOT_IN_PARTY)

    async def test_only_a_dm_asks_or_gives(self) -> None:
        who: Any = BOB
        bob = await sessions.find(
            self.web, await sessions.sign_in(self.web, who, [THURSDAY], now=NOW, days=30), now=NOW
        )
        assert bob is not None
        for call in (
            party_catch_up.queue_choices(self.web, bob, self.cref, {"entry": self.mira}),
            party_catch_up.queue_tell(
                self.web, bob, self.cref, {"member": self.cara, "entry": self.mira}
            ),
        ):
            with self.assertRaises(RecapError) as err:
                await call
            self.assertEqual(err.exception.code, "not_allowed")

    async def test_a_bad_body_is_refused_before_anything_is_queued(self) -> None:
        bodies: list[object] = [{"entry": "nope"}, {}, 5]
        for body in bodies:
            with self.assertRaises(RecapError) as err:
                await party_catch_up.queue_choices(self.web, self.session, self.cref, body)
            self.assertEqual(err.exception.code, "bad_action")
        tells: list[object] = [{"member": self.cara}, {"member": "x", "entry": self.mira}, None]
        for body in tells:
            with self.assertRaises(RecapError) as err2:
                await party_catch_up.queue_tell(self.web, self.session, self.cref, body)
            self.assertEqual(err2.exception.code, "bad_action")
        self.assertIsNone(await self.actions.claim_next(self.g))

    async def test_a_big_answer_keeps_every_listed_pair_whole(self) -> None:
        # (the answer's column holds 200 characters: the bot lists at most five members, each a
        # whole id and count, so the page never reads a cut pair; the rest show once these are
        # caught up and it is read again)
        class Stub:
            async def tell_choices(self, *_: Any) -> list[tuple[str, str, int]]:
                return [(f"{i:032x}", f"M{i}", 10 + i) for i in range(8)]

        action = wa.Action(
            id="a", campaign_id=self.one.id, entity_id="", user_id=ALICE.id,
            action="party-catch-up-choices", text=self.mira,
        )  # fmt: skip
        out = await wa._party(Stub(), self.actions, self.g, self.one.id, action)  # type: ignore[arg-type]
        self.assertLessEqual(len(out.counts), 200)
        listed = relationships._catch_choices(out.counts)
        assert listed is not None
        self.assertEqual(len(listed), wa.CATCH_CHOICES_MAX)
        self.assertEqual([c["count"] for c in listed], [10, 11, 12, 13, 14])
