"""The website's "Things the characters learned" list (#3863 step 2, migration 0586): the bot keeps
the waiting proposals in words for a DM; the page reads them through a DM-only door; asking again
replaces the set (one row for each proposal); Keep and Not this go to `RevealProposals.keep` /
`dismiss`, which check again; the Undo is the existing `join-undo` and takes back only a keep this
DM made; the rows are swept after an hour. Postgres. Invented names."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, ClassVar
from unittest.mock import patch

from dmbot.memory import web_actions as wa
from dmbot.memory.models import MemoryRuleError
from dmbot.memory.reveal_proposals import Shown
from dmbot.memory.web_reveals import LIFE_S, RevealsStore
from dmbot.web import reveals, sessions
from dmbot.web.recaps import RecapError
from tests.test_recap_web import BOB, NOW
from tests.test_web_api import ALICE, THURSDAY
from tests.test_web_relationships import Web

P1, P2 = "1" * 32, "2" * 32


def words(who: str, what: str, *, player: bool = False, guessing: bool = False) -> Shown:
    return Shown(who, what, "a successful Insight check", player, guessing)


class Fake:
    """`RevealProposals` stand-in: keep and dismiss as the store answers them."""

    kept: ClassVar[list[str]] = []
    dismissed: ClassVar[list[str]] = []
    gone: ClassVar[set[str]] = set()
    batch: ClassVar[int | None] = 77

    def __init__(self, _memory: Any) -> None:
        pass

    async def keep(self, guild: int, cid: str, pid: str, *, opted_in: Any, source: str) -> Any:
        if pid in Fake.gone:
            raise MemoryRuleError("gone")
        Fake.gone.add(pid)
        Fake.kept.append(pid)
        return Fake.batch

    async def dismiss(self, guild: int, cid: str, pid: str, *, source: str) -> None:
        if pid in Fake.gone:
            raise MemoryRuleError("gone")
        Fake.gone.add(pid)
        Fake.dismissed.append(pid)


class RevealsOnTheWeb(Web):
    bob_has_a_hero = False

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.actions = wa.ActionStore(self.db)
        self.kept_lines = RevealsStore(self.db, clock=lambda: float(NOW))
        Fake.kept, Fake.dismissed, Fake.gone, Fake.batch = [], [], set(), 77
        self.asked: list[tuple[int, str, int]] = []
        self.dropped: list[str] = []

    async def read(self, who: Any = None) -> list[dict[str, Any]]:
        return await reveals.read(self.web, who or self.session, self.cref)

    async def bob_session(self) -> Any:
        who: Any = BOB
        found = await sessions.find(
            self.web, await sessions.sign_in(self.web, who, [THURSDAY], now=NOW, days=30), now=NOW
        )
        assert found is not None
        return found

    async def loop(self) -> list[wa.Outcome]:
        async def ask(guild: int, cid: str, user: int) -> int:
            self.asked.append((guild, cid, user))
            return await self.kept_lines.replace(
                guild,
                cid,
                user,
                [(P1, words("Tran", "Bruno knows Shela")), (P2, words("Clyde", "x"))],
            )

        async def opted_in() -> set[int]:
            return {ALICE.id}

        async def drop(guild: int, cid: str, pid: str) -> None:
            self.dropped.append(pid)
            await self.kept_lines.drop(guild, cid, pid)

        done = []
        waiting = SimpleNamespace(opted_in=opted_in)
        while (action := await self.actions.claim_next(self.g)) is not None:
            with patch("dmbot.memory.web_actions.RevealProposals", Fake):
                outcome = await wa.apply(
                    self.memory, self.g, {ALICE.id}, action, waiting,  # type: ignore[arg-type]
                    actions=self.actions, reveals_ask=ask, reveals_drop=drop,
                )  # fmt: skip
            await self.actions.finish(
                self.g, action, error=outcome.error, result_counts=outcome.counts,
                result_batches=outcome.batches,
            )  # fmt: skip
            done.append(outcome)
        return done

    async def test_nothing_is_kept_until_the_bot_has_worked_it_out(self) -> None:
        self.assertEqual(await self.read(), [])

    async def test_the_kept_lines_come_back_in_order_to_their_dm_only(self) -> None:
        await self.kept_lines.replace(
            self.g, self.one.id, ALICE.id,
            [
                (P2, words("Clyde", "The Sword exists", player=True)),
                (P1, words("Tran", "a b", guessing=True)),
            ],
        )  # fmt: skip
        got = await self.read()
        self.assertEqual([r["id"] for r in got], [P2, P1])
        self.assertEqual(
            got[0],
            {
                "id": P2, "who": "Clyde", "what": "The Sword exists",
                "how": "a successful Insight check", "player": True, "guessing": False,
            },
        )  # fmt: skip
        # a co-DM gets none of Alice's set; a player is refused
        await self.sql(
            "INSERT INTO campaign_dms (guild_id, campaign_id, user_id) VALUES (%s, %s, %s)",
            self.g, self.one.id, BOB.id,
        )  # fmt: skip
        bob = await self.bob_session()
        self.assertEqual(await self.read(bob), [])
        await self.sql("DELETE FROM campaign_dms WHERE user_id = %s", BOB.id)
        with self.assertRaises(RecapError) as err:
            await self.read(bob)
        self.assertEqual(err.exception.code, "not_allowed")

    async def test_another_campaigns_dm_is_refused(self) -> None:
        with self.assertRaises(RecapError):
            await reveals.read(self.web, self.session, f"{self.g}-{'f' * 32}")

    async def test_asking_twice_leaves_one_row_for_each_proposal(self) -> None:
        for _ in range(2):
            await reveals.queue_ask(self.web, self.session, self.cref)
            (done,) = await self.loop()
            self.assertEqual((done.error, done.counts), ("", "n:2"))
        self.assertEqual(len(self.asked), 2)
        self.assertEqual([r["id"] for r in await self.read()], [P1, P2])
        async with self.db.guild(self.g) as conn:
            cur = await conn.execute("SELECT count(*) AS n FROM web_reveals")
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual(row["n"], 2)

    async def test_a_new_ask_replaces_the_set_so_an_answered_one_is_gone(self) -> None:
        await self.kept_lines.replace(self.g, self.one.id, ALICE.id, [(P1, words("Tran", "a"))])
        await self.kept_lines.replace(self.g, self.one.id, ALICE.id, [(P2, words("Clyde", "b"))])
        self.assertEqual([r["id"] for r in await self.read()], [P2])

    async def test_keep_gives_the_batch_for_the_undo_and_takes_the_line_off_the_page(self) -> None:
        await self.kept_lines.replace(self.g, self.one.id, ALICE.id, [(P1, words("Tran", "a"))])
        await reveals.queue_answer(self.web, self.session, self.cref, {"id": P1, "answer": "keep"})
        (done,) = await self.loop()
        self.assertEqual((done.error, done.batches), ("", (77,)))
        self.assertEqual((Fake.kept, self.dropped), ([P1], [P1]))
        self.assertEqual(await self.read(), [])

    async def test_a_keep_that_changed_nothing_is_nothing_left_not_a_failure(self) -> None:
        Fake.batch = None
        await reveals.queue_answer(self.web, self.session, self.cref, {"id": P1, "answer": "keep"})
        (done,) = await self.loop()
        self.assertEqual(done.error, wa.NOTHING_LEFT)

    async def test_not_this_dismisses_and_a_second_press_is_gone(self) -> None:
        await reveals.queue_answer(self.web, self.session, self.cref, {"id": P1, "answer": "not"})
        await reveals.queue_answer(self.web, self.session, self.cref, {"id": P1, "answer": "keep"})
        first, second = await self.loop()
        self.assertEqual((first.error, Fake.dismissed), ("", [P1]))
        self.assertEqual(second.error, wa.GONE)
        self.assertEqual(Fake.kept, [])

    async def test_the_queue_door_itself_refuses_a_bad_shape_and_a_player(self) -> None:
        from dmbot.web import relationships

        for action, text in (
            ("reveals-ask", "x"),
            ("reveals-keep", "not-an-id"),
            ("reveals-not", "g" * 32),
        ):
            with self.assertRaises(RecapError, msg=action):
                await relationships._queue(
                    self.web, self.session, self.cref, entity=None, action=action, target="",
                    text=text, flag=False,
                )  # fmt: skip
        bob = await self.bob_session()
        with self.assertRaises(RecapError):
            await reveals.queue_ask(self.web, bob, self.cref)
        self.assertIsNone(await self.actions.claim_next(self.g))

    async def test_a_bad_answer_is_refused_before_anything_is_queued(self) -> None:
        for body in (None, {}, {"id": "nope", "answer": "keep"}, {"id": P1, "answer": "maybe"}):
            with self.assertRaises(RecapError):
                await reveals.queue_answer(self.web, self.session, self.cref, body)
        self.assertIsNone(await self.actions.claim_next(self.g))

    async def test_the_undo_is_the_existing_one_and_only_for_this_dms_own_keep(self) -> None:
        await self.kept_lines.replace(self.g, self.one.id, ALICE.id, [(P1, words("Tran", "a"))])
        await reveals.queue_answer(self.web, self.session, self.cref, {"id": P1, "answer": "keep"})
        await self.loop()
        self.assertTrue(await self.actions.join_batch_made(self.g, self.one.id, ALICE.id, 77))
        self.assertFalse(await self.actions.join_batch_made(self.g, self.one.id, BOB.id, 77))
        self.assertFalse(await self.actions.join_batch_made(self.g, self.one.id, ALICE.id, 78))

    async def test_a_set_older_than_an_hour_is_swept_and_a_fresh_one_stays(self) -> None:
        await self.kept_lines.replace(self.g, self.one.id, ALICE.id, [(P1, words("Tran", "a"))])
        later = RevealsStore(self.db, clock=lambda: float(NOW + LIFE_S - 10))
        self.assertEqual(await later.sweep(self.g), 0)
        later = RevealsStore(self.db, clock=lambda: float(NOW + LIFE_S + 10))
        self.assertEqual(await later.sweep(self.g), 1)
        self.assertEqual(await self.read(), [])
