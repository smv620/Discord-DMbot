"""The website's continuity heads-ups, the bot half (#3695 half 1, migration 0605). The bot keeps
the heads-ups its check found as pointers for the campaign: one set, replaced whole on each ask,
no words. An answered claim leaves the set at once, a set older than a day is swept, and the queue
words `continuity-ask` and `continuity-answer` go through the card's own check and answer.
Postgres for the store and the queue door. Invented names."""

from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from typing import Any

from dmbot.memory import web_actions as wa
from dmbot.memory.continuity_hits import LIFE_S, ContinuityHits, Hit, hit_of
from dmbot.web.recaps import RecapError
from tests.test_memory_store import DM, GUILD_A, MemoryTest
from tests.test_recap_web import NOW
from tests.test_web_api import ALICE
from tests.test_web_relationships import Web

C1, C2, E1, E2, F1 = "1" * 32, "2" * 32, "a" * 32, "b" * 32, "c" * 32


def hit(claim: str, kind: str = "clash", **kw: Any) -> Hit:
    return Hit(claim, kind, kw.get("level", "probable"), kw.get("entity", E1),
               kw.get("earlier", F1), kw.get("session", 1000), kw.get("learner"),
               kw.get("pinned", False))  # fmt: skip


class ThePointers(MemoryTest):
    """The store on its own: no website role needed, so it runs wherever Postgres is."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.g = GUILD_A
        self.one = SimpleNamespace(id=self.c)
        self.hits = ContinuityHits(self.db, clock=lambda: float(NOW))
        self.other = await self.campaigns.create(self.g, "Other", DM)

    async def test_a_set_is_replaced_whole_in_order_and_kept_once_per_claim_and_kind(self) -> None:
        self.assertEqual(
            await self.hits.replace(self.g, self.one.id, [hit(C2), hit(C1), hit(C2)]), 2
        )
        self.assertEqual(
            [h.claim_id for h in await self.hits.listed(self.g, self.one.id)], [C2, C1]
        )
        await self.hits.replace(self.g, self.one.id, [hit(C1, "pinned", pinned=True)])
        self.assertEqual(
            await self.hits.listed(self.g, self.one.id), [hit(C1, "pinned", pinned=True)]
        )

    async def test_one_campaign_s_set_is_never_another_s(self) -> None:
        await self.hits.replace(self.g, self.one.id, [hit(C1)])
        await self.hits.replace(self.g, self.other.id, [hit(C2)])
        self.assertEqual([h.claim_id for h in await self.hits.listed(self.g, self.one.id)], [C1])
        await self.hits.replace(self.g, self.other.id, [])  # (emptying B leaves A alone)
        self.assertEqual(len(await self.hits.listed(self.g, self.one.id)), 1)

    async def test_an_answered_claim_leaves_the_set_whatever_its_kind(self) -> None:
        await self.hits.replace(self.g, self.one.id, [hit(C1), hit(C1, "pinned"), hit(C2)])
        await self.hits.drop(self.g, self.one.id, C1)
        self.assertEqual([h.claim_id for h in await self.hits.listed(self.g, self.one.id)], [C2])

    async def test_a_set_older_than_a_day_is_swept_and_a_fresh_one_stays(self) -> None:
        await self.hits.replace(self.g, self.one.id, [hit(C1)])
        later = ContinuityHits(self.db, clock=lambda: float(NOW + LIFE_S - 10))
        self.assertEqual(await later.sweep(self.g), 0)
        later = ContinuityHits(self.db, clock=lambda: float(NOW + LIFE_S + 10))
        self.assertEqual(await later.sweep(self.g), 1)
        self.assertEqual(await self.hits.listed(self.g, self.one.id), [])

    async def test_the_table_holds_no_words(self) -> None:
        async with self.db.guild(self.g) as conn:
            cur = await conn.execute(
                "SELECT column_name FROM information_schema.columns"
                " WHERE table_name = 'continuity_hits'"
            )
            columns = {r["column_name"] for r in await cur.fetchall()}
        self.assertEqual(
            columns,
            {"guild_id", "campaign_id", "claim_id", "kind", "level", "entity_id", "earlier_id",
             "earlier_session", "learner_id", "pinned", "position", "asked_at"},
        )  # fmt: skip


class TheQueueDoor(Web):
    async def test_the_queue_door_takes_the_two_words_and_refuses_a_bad_shape(self) -> None:
        from dmbot.web import relationships

        for action, text in (("continuity-ask", ""), ("continuity-answer", f"{C1} c")):
            await relationships._queue(
                self.web, self.session, self.cref, entity=None, action=action, target="",
                text=text, flag=False,
            )  # fmt: skip
        for action, text in (
            ("continuity-ask", "x"),
            ("continuity-answer", C1),
            ("continuity-answer", f"{C1} i"),  # ("Ignore" never reaches the queue)
            ("continuity-answer", "nope c"),
        ):
            with self.assertRaises(RecapError, msg=f"{action} {text!r}"):
                await relationships._queue(
                    self.web, self.session, self.cref, entity=None, action=action, target="",
                    text=text, flag=False,
                )  # fmt: skip
        actions = wa.ActionStore(self.db)
        taken = []
        while (a := await actions.claim_next(self.g)) is not None:
            taken.append(a.action)
            await actions.finish(self.g, a)
        self.assertEqual(taken, ["continuity-ask", "continuity-answer"])


def action(word: str, text: str = "", user: int = ALICE.id) -> Any:
    return SimpleNamespace(action=word, text=text, user_id=user, campaign_id="c1", target_id="")


def run(a: Any, **kw: Any) -> wa.Outcome:
    return asyncio.run(wa.apply(None, 5, {ALICE.id}, a, **kw))  # type: ignore[arg-type]


class TheApplier(unittest.TestCase):
    def test_an_ask_works_the_list_out_and_says_how_many(self) -> None:
        asked: list[tuple[int, str, int]] = []

        async def ask(guild: int, cid: str, user: int) -> int:
            asked.append((guild, cid, user))
            return 3

        self.assertEqual(run(action("continuity-ask"), continuity_ask=ask).counts, "n:3")
        self.assertEqual(asked, [(5, "c1", ALICE.id)])

    def test_an_answer_goes_through_and_one_no_longer_waiting_is_gone(self) -> None:
        said: list[tuple[str, str]] = []

        async def answer(guild: int, cid: str, claim: str, choice: str) -> bool:
            said.append((claim, choice))
            return claim == C1

        self.assertEqual(
            run(action("continuity-answer", f"{C1} c"), continuity_answer=answer).result_id, C1
        )
        self.assertEqual(
            run(action("continuity-answer", f"{C2} n"), continuity_answer=answer).error, wa.GONE
        )
        self.assertEqual(said, [(C1, "c"), (C2, "n")])

    def test_a_player_a_bad_text_or_a_failure_changes_nothing(self) -> None:
        async def answer(*_: Any) -> bool:
            raise RuntimeError("boom")

        self.assertEqual(run(action("continuity-ask", user=99)).error, wa.NOT_ALLOWED)
        self.assertEqual(run(action("continuity-answer", "nope c")).error, wa.FAILED)
        self.assertEqual(run(action("continuity-ask")).error, wa.FAILED)  # (no bot to ask)
        self.assertEqual(
            run(action("continuity-answer", f"{C1} c"), continuity_answer=answer).error, wa.FAILED
        )


class ThePointersOfAHeadsUp(unittest.TestCase):
    def conflict(self, **kw: Any) -> Any:
        base: dict[str, Any] = {
            "claim": SimpleNamespace(id=C1), "kind": "clash", "level": "likely", "entity_id": E1,
            "clash_ids": (F1,), "revive_id": None, "earlier": SimpleNamespace(session_started_at=7),
            "learner_id": None, "pinned": True,
        }  # fmt: skip
        base.update(kw)
        return SimpleNamespace(**base)

    def test_the_earlier_fact_its_session_and_the_pin_are_kept_and_no_words(self) -> None:
        self.assertEqual(hit_of(self.conflict()), Hit(C1, "clash", "likely", E1, F1, 7, None, True))

    def test_a_death_points_at_the_entry_it_would_bring_back(self) -> None:
        h = hit_of(self.conflict(clash_ids=(), revive_id=E2, earlier=None))
        assert h is not None
        self.assertEqual((h.earlier_id, h.earlier_session), (E2, None))

    def test_a_claim_or_entry_with_no_id_the_table_can_hold_is_left_out(self) -> None:
        self.assertIsNone(hit_of(self.conflict(entity_id="")))
        self.assertIsNone(hit_of(self.conflict(claim=SimpleNamespace(id="x"))))


class TheBotsAnswer(unittest.TestCase):
    def bot(self, *, fails: bool, headless: bool = False) -> Any:
        from dmbot.memory.models import MemoryRuleError

        calls: dict[str, list[Any]] = {"answer": [], "drop": [], "watch": []}

        class Store:
            def __init__(self, _memory: Any) -> None:
                pass

            async def answer(self, *a: Any, **kw: Any) -> str:
                calls["answer"].append(a)
                if fails:
                    raise MemoryRuleError("gone")
                return "done"

        class Hits:
            def __init__(self, _db: Any) -> None:
                pass

            async def drop(self, *a: Any) -> None:
                calls["drop"].append(a)

        async def consenting(_g: int) -> set[int]:
            return {1}

        async def get(*_: Any) -> Any:
            return SimpleNamespace(dm_screen_channel_id=9)

        async def watch(*a: Any, **kw: Any) -> None:
            calls["watch"].append(a)

        bot: Any = SimpleNamespace(
            _picture_db=object(), memory=object(), settings=SimpleNamespace(continuity=None),
            consent=SimpleNamespace(consenting=consenting), campaigns=SimpleNamespace(get=get),
            watch_story=watch, headless=headless, calls=calls, Store=Store, Hits=Hits,
        )  # fmt: skip
        return bot

    def answer(self, bot: Any, choice: str) -> bool:
        from unittest.mock import patch

        from dmbot.bot import DMBot

        bot._watch_after_answer = lambda g, c: DMBot._watch_after_answer(bot, g, c)
        with (
            patch("dmbot.bot.ContinuityStore", bot.Store),
            patch("dmbot.bot.ContinuityHits", bot.Hits),
        ):
            return asyncio.run(DMBot._answer_continuity(bot, 5, "c1", C1, choice))

    def test_the_story_changed_drops_the_line_and_the_story_watch_looks(self) -> None:
        bot = self.bot(fails=False)
        self.assertTrue(self.answer(bot, "c"))
        self.assertEqual(bot.calls["answer"][0][3], "c")
        self.assertEqual(len(bot.calls["drop"]), 1)
        self.assertEqual(len(bot.calls["watch"]), 1)

    def test_wasn_t_real_drops_the_line_and_watches_nothing(self) -> None:
        bot = self.bot(fails=False)
        self.assertTrue(self.answer(bot, "n"))
        self.assertEqual(bot.calls["answer"][0][3], "n")
        self.assertEqual(bot.calls["watch"], [])

    def test_a_claim_no_longer_waiting_is_gone_and_still_leaves_the_page(self) -> None:
        bot = self.bot(fails=True)
        self.assertFalse(self.answer(bot, "c"))
        self.assertEqual(len(bot.calls["drop"]), 1)
        self.assertEqual(bot.calls["watch"], [])

    def test_a_worker_with_no_discord_skips_the_story_watch(self) -> None:
        bot = self.bot(fails=False, headless=True)
        self.assertTrue(self.answer(bot, "c"))
        self.assertEqual(bot.calls["watch"], [])
