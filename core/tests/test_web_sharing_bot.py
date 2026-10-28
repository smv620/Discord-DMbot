"""The bot's side of the website's rule sharing (#2793): a queued share, stop, code look-up, follow
or unfollow is claimed in order, checked again (a DM of the campaign, the plan) and applied with the
store calls Discord's buttons use. The queue's table and the page are #2783's; here the rows are
written directly. Invented names throughout."""

from __future__ import annotations

import asyncio
import dataclasses
import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, PropertyMock, patch

from dmbot.bot import DMBot
from dmbot.campaigns import Campaign, CampaignStore
from dmbot.rules import rule_sets
from dmbot.rules import web_sharing as ws
from dmbot.rules.house import HouseRule, HouseRuleStore
from dmbot.rules.rule_sets import RuleSetStore
from tests.pg import DatabaseTest

GUILD_A, GUILD_B = 1001, 2002
DM_A, DM_B, PLAYER = 11, 22, 33
NOW = 1_700_000_000
WORDS = "Crits double the dice"  # a rule's words: must never reach a note


class FakeHost:
    """The running bot as `apply` sees it. The rule calls go through the real stores."""

    def __init__(
        self, campaigns: CampaignStore, sets: RuleSetStore, houses: HouseRuleStore
    ) -> None:
        self.campaigns = campaigns
        self.rule_sets = sets
        self.houses = houses
        self.refusal: str | None = None
        self.settled: list[bool] = []

    async def get_campaign(self, guild_id: int, campaign_id: str) -> Campaign | None:
        return await self.campaigns.get(guild_id, campaign_id)

    async def share_refusal(self, campaign: Campaign, user_id: int) -> str | None:
        return self.refusal

    async def own_rules(self, campaign: Campaign) -> tuple[list[HouseRule], str | None]:
        return await self.houses.list(campaign.guild_id, campaign.id), None

    async def settle(self, campaign: Campaign, *, following: bool) -> str:
        self.settled.append(following)
        return ""


class Base(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.campaigns = CampaignStore(self.db, clock=lambda: NOW)
        self.houses = HouseRuleStore(self.db)
        self.sets = RuleSetStore(self.db, clock=lambda: NOW)
        self.queue = ws.ActionStore(self.db, clock=lambda: NOW)
        self.pub = (await self.campaigns.create(GUILD_A, "Frostmaiden", DM_A)).id
        self.same = (await self.campaigns.create(GUILD_A, "Sunken City", DM_A)).id
        self.far = (await self.campaigns.create(GUILD_B, "Far Away", DM_B)).id
        await self.houses.add(GUILD_A, self.pub, DM_A, WORDS, "Crits double the damage")
        await self.houses.add(GUILD_A, self.pub, DM_A, "No flanking")
        self.host = FakeHost(self.campaigns, self.sets, self.houses)

    async def put(
        self, action: str, *, campaign: str | None = None, user: int = DM_A, arg: str = "",
        guild: int = GUILD_A, created: int = NOW, updated: int | None = None,
        state: str = "waiting",
    ) -> str:  # fmt: skip
        """One queued row, as the website's door would have written it."""
        rid = f"{len(await self.rows(guild)):032x}"
        stage = {"waiting": "waiting", "working": "sharing"}[state]
        async with self.db.guild(guild) as conn:
            await conn.execute(
                "INSERT INTO web_rule_actions (guild_id, campaign_id, id, user_id, action, arg,"
                " state, stage, created_at, claimed_at, updated_at)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 0, %s)",
                (
                    guild, campaign or self.pub, rid, user, action, arg, state, stage, created,
                    created if updated is None else updated,
                ),
            )  # fmt: skip
        return rid

    async def rows(self, guild: int = GUILD_A) -> list[dict[str, Any]]:
        async with self.db.guild(guild) as conn:
            cur = await conn.execute(
                "SELECT id, state, stage, error, note FROM web_rule_actions ORDER BY seq"
            )
            return [dict(r) for r in await cur.fetchall()]

    async def take_and_run(self, guild: int = GUILD_A) -> list[dict[str, Any]]:
        """The bot's round: every open row, in order."""
        while (item := await self.queue.claim_next(guild)) is not None:
            self.assertTrue(await ws.run_one(self.host, self.queue, guild, item))
        return await self.rows(guild)

    async def result(self, rid: str, guild: int = GUILD_A) -> tuple[str, str]:
        (row,) = [r for r in await self.rows(guild) if r["id"] == rid]
        return row["state"], row["error"]

    async def note(self, rid: str, guild: int = GUILD_A) -> str:
        (row,) = [r for r in await self.rows(guild) if r["id"] == rid]
        return str(row["note"])

    async def share(self) -> str:
        rid = await self.put(ws.PUBLISH, arg="Frostmaiden house rules")
        await self.take_and_run()
        self.assertEqual(await self.result(rid), ("done", ""))
        return await self.sets.make_code(GUILD_A, self.pub, DM_A)


class Publishing(Base):
    async def test_a_dm_shares_and_the_note_is_discords_confirmation(self) -> None:
        rid = await self.put(ws.PUBLISH, arg="Frostmaiden house rules")
        await self.take_and_run()
        self.assertEqual(await self.result(rid), ("done", ""))
        self.assertEqual(await self.note(rid), "✅ Sharing **Frostmaiden house rules**.")
        mine = await self.sets.published(GUILD_A, self.pub)
        assert mine is not None
        self.assertTrue(mine.shared)

    async def test_a_former_dm_still_in_the_server_is_not_allowed(self) -> None:
        rid = await self.put(ws.PUBLISH, user=PLAYER, arg="Mine")
        await self.take_and_run()
        self.assertEqual(await self.result(rid), ("failed", ws.NOT_ALLOWED))
        self.assertIsNone(await self.sets.published(GUILD_A, self.pub))

    async def test_a_lapsed_plan_is_plan_needed_with_discords_sentence(self) -> None:
        self.host.refusal = "Sharing needs a plan that includes it."
        rid = await self.put(ws.PUBLISH, arg="Mine")
        await self.take_and_run()
        self.assertEqual(await self.result(rid), ("failed", ws.PLAN_NEEDED))
        self.assertEqual(await self.note(rid), self.host.refusal)

    async def test_a_campaign_with_no_rules_is_no_rules(self) -> None:
        rid = await self.put(ws.PUBLISH, campaign=self.same, arg="Mine")
        await self.take_and_run()
        self.assertEqual(await self.result(rid), ("failed", ws.NO_RULES))
        self.assertEqual(await self.note(rid), rule_sets.NO_RULES)

    async def test_a_campaign_that_is_gone_is_not_found(self) -> None:
        await self.put(ws.PUBLISH, arg="Mine")
        item = await self.queue.claim_next(GUILD_A)
        assert item is not None
        self.host.get_campaign = self._none  # type: ignore[method-assign,assignment]
        out = await ws.apply(self.host, GUILD_A, item)
        self.assertEqual(out.error, ws.NOT_FOUND)

    async def _none(self, *_: Any) -> None:
        return None

    async def test_stop_sharing_turns_the_codes_off(self) -> None:
        code = await self.share()
        rid = await self.put(ws.STOP_SHARING)
        await self.take_and_run()
        self.assertEqual(await self.result(rid), ("done", ""))
        with self.assertRaises(rule_sets.RuleSetError):
            await self.sets.by_code(GUILD_B, DM_B, code)

    async def test_stop_sharing_when_nothing_is_shared_says_so(self) -> None:
        rid = await self.put(ws.STOP_SHARING)
        await self.take_and_run()
        self.assertEqual(await self.result(rid), ("failed", ws.FAILED))
        self.assertEqual(await self.note(rid), rule_sets.NOT_SHARED)


class Following(Base):
    async def test_look_up_names_the_set_and_counts_and_copies_nothing(self) -> None:
        code = await self.share()
        rid = await self.put(ws.LOOK_CODE, campaign=self.far, guild=GUILD_B, user=DM_B, arg=code)
        await self.take_and_run(GUILD_B)
        self.assertEqual(await self.result(rid, GUILD_B), ("done", ""))
        note = await self.note(rid, GUILD_B)
        self.assertIn("Frostmaiden house rules", note)
        self.assertIn("Frostmaiden", note)
        self.assertIn("2 house rules", note)
        self.assertEqual(await self.sets.following(GUILD_B, self.far), [])

    async def test_a_wrong_code_is_counted_against_discords_limit(self) -> None:
        await self.share()
        ids = [
            await self.put(
                ws.LOOK_CODE, campaign=self.far, guild=GUILD_B, user=DM_B, arg="AAAAAAAAAA"
            )
            for _ in range(rule_sets.GUESSES + 1)
        ]
        await self.take_and_run(GUILD_B)
        words = [await self.result(i, GUILD_B) for i in ids]
        self.assertEqual(
            words[: rule_sets.GUESSES], [("failed", ws.BAD_CHOICE)] * rule_sets.GUESSES
        )
        self.assertEqual(words[-1], ("failed", ws.TOO_SOON))
        self.assertEqual(await self.note(ids[-1], GUILD_B), rule_sets.TOO_MANY)

    async def test_following_by_code_copies_the_set_and_settles_it(self) -> None:
        code = await self.share()
        rid = await self.put(ws.FOLLOW_CODE, campaign=self.far, guild=GUILD_B, user=DM_B, arg=code)
        await self.take_and_run(GUILD_B)
        self.assertEqual(await self.result(rid, GUILD_B), ("done", ""))
        self.assertIn("Now using **Frostmaiden house rules**", await self.note(rid, GUILD_B))
        (follow,) = await self.sets.following(GUILD_B, self.far)
        self.assertEqual(follow.name, "Frostmaiden house rules")
        self.assertEqual(self.host.settled, [True])

    async def test_following_a_second_set_while_following_one_is_refused(self) -> None:
        first = await self.share()
        await self.houses.add(GUILD_A, self.same, DM_A, "Rest in the dark")
        other = await self.sets.publish(
            GUILD_A,
            self.same,
            DM_A,
            "Sunken rules",
            await self.houses.list(GUILD_A, self.same),
            None,
        )
        assert other.shared
        second = await self.sets.make_code(GUILD_A, self.same, DM_A)
        one = await self.put(ws.FOLLOW_CODE, campaign=self.far, guild=GUILD_B, user=DM_B, arg=first)
        two = await self.put(
            ws.FOLLOW_CODE, campaign=self.far, guild=GUILD_B, user=DM_B, arg=second
        )
        await self.take_and_run(GUILD_B)
        self.assertEqual(await self.result(one, GUILD_B), ("done", ""))
        self.assertEqual(await self.result(two, GUILD_B), ("failed", ws.FAILED))
        self.assertEqual(await self.note(two, GUILD_B), rule_sets.ALREADY_FOLLOWING)
        (follow,) = await self.sets.following(GUILD_B, self.far)
        self.assertEqual(follow.name, "Frostmaiden house rules")  # nothing of the second copied

    async def test_a_set_unshared_between_look_and_follow_is_not_copied(self) -> None:
        code = await self.share()
        (offer,) = await self.sets.shared_here(GUILD_A, self.same)
        look = await self.put(ws.LOOK_CODE, campaign=self.far, guild=GUILD_B, user=DM_B, arg=code)
        await self.take_and_run(GUILD_B)
        self.assertEqual(await self.result(look, GUILD_B), ("done", ""))
        await self.sets.stop_sharing(GUILD_A, self.pub, DM_A)  # the publisher stops
        by_code = await self.put(
            ws.FOLLOW_CODE, campaign=self.far, guild=GUILD_B, user=DM_B, arg=code
        )
        here = await self.put(ws.FOLLOW_HERE, campaign=self.same, arg=offer.set_id)
        await self.take_and_run(GUILD_B)
        await self.take_and_run(GUILD_A)
        self.assertEqual(await self.result(by_code, GUILD_B), ("failed", ws.BAD_CHOICE))
        self.assertEqual(await self.note(by_code, GUILD_B), rule_sets.BAD_CODE)
        self.assertEqual(await self.result(here), ("failed", ws.BAD_CHOICE))
        self.assertEqual(await self.note(here), rule_sets.NO_SUCH_SET)
        self.assertEqual(await self.sets.following(GUILD_B, self.far), [])
        self.assertEqual(await self.sets.following(GUILD_A, self.same), [])

    async def test_a_retaken_look_does_not_count_its_guess_again(self) -> None:
        await self.share()
        for _ in range(rule_sets.GUESSES - 1):
            await self.put(
                ws.LOOK_CODE, campaign=self.far, guild=GUILD_B, user=DM_B, arg="AAAAAAAAAA"
            )
        await self.take_and_run(GUILD_B)  # four wrong guesses counted
        retaken = await self.put(
            ws.LOOK_CODE, campaign=self.far, guild=GUILD_B, user=DM_B, arg="AAAAAAAAAA"
        )
        async with self.db.guild(GUILD_B) as conn:  # the door put it back: updated later than made
            await conn.execute(
                "UPDATE web_rule_actions SET updated_at = %s WHERE id = %s", (NOW + 700, retaken)
            )
        await self.take_and_run(GUILD_B)
        self.assertEqual(await self.result(retaken, GUILD_B), ("failed", ws.BAD_CHOICE))
        fifth = await self.put(
            ws.LOOK_CODE, campaign=self.far, guild=GUILD_B, user=DM_B, arg="AAAAAAAAAA"
        )
        await self.take_and_run(GUILD_B)  # the fifth real guess is still a bad choice, not too soon
        self.assertEqual(await self.result(fifth, GUILD_B), ("failed", ws.BAD_CHOICE))

    async def test_a_code_for_the_campaigns_own_set_is_refused(self) -> None:
        code = await self.share()
        rid = await self.put(ws.FOLLOW_CODE, arg=code)
        await self.take_and_run()
        self.assertEqual(await self.result(rid), ("failed", ws.FAILED))
        self.assertEqual(await self.note(rid), rule_sets.OWN_SET)

    async def test_following_twice_is_refused_the_second_time(self) -> None:
        code = await self.share()
        one = await self.put(ws.FOLLOW_CODE, campaign=self.far, guild=GUILD_B, user=DM_B, arg=code)
        two = await self.put(ws.FOLLOW_CODE, campaign=self.far, guild=GUILD_B, user=DM_B, arg=code)
        await self.take_and_run(GUILD_B)
        self.assertEqual(await self.result(one, GUILD_B), ("done", ""))
        self.assertEqual(await self.result(two, GUILD_B), ("failed", ws.FAILED))
        self.assertEqual(await self.note(two, GUILD_B), rule_sets.ALREADY_FOLLOWING)

    async def test_follow_here_takes_a_set_shared_in_the_same_server(self) -> None:
        await self.share()
        (offer,) = await self.sets.shared_here(GUILD_A, self.same)
        rid = await self.put(ws.FOLLOW_HERE, campaign=self.same, arg=offer.set_id)
        await self.take_and_run()
        self.assertEqual(await self.result(rid), ("done", ""))
        (follow,) = await self.sets.following(GUILD_A, self.same)
        self.assertEqual(follow.set_id, offer.set_id)

    async def test_follow_here_with_a_set_that_is_not_shared_here_is_a_bad_choice(self) -> None:
        rid = await self.put(ws.FOLLOW_HERE, campaign=self.same, arg="c" * 32)
        await self.take_and_run()
        self.assertEqual(await self.result(rid), ("failed", ws.BAD_CHOICE))

    async def test_unfollow_then_unfollow_again(self) -> None:
        code = await self.share()
        await self.put(ws.FOLLOW_CODE, campaign=self.far, guild=GUILD_B, user=DM_B, arg=code)
        one = await self.put(ws.UNFOLLOW, campaign=self.far, guild=GUILD_B, user=DM_B)
        two = await self.put(ws.UNFOLLOW, campaign=self.far, guild=GUILD_B, user=DM_B)
        await self.take_and_run(GUILD_B)
        self.assertEqual(await self.result(one, GUILD_B), ("done", ""))
        self.assertEqual(await self.result(two, GUILD_B), ("failed", ws.FAILED))
        self.assertEqual(await self.note(two, GUILD_B), rule_sets.NOT_FOLLOWING)
        self.assertEqual(await self.sets.following(GUILD_B, self.far), [])

    async def test_a_finished_row_no_longer_holds_the_code(self) -> None:
        code = await self.share()
        ok = await self.put(ws.FOLLOW_CODE, campaign=self.far, guild=GUILD_B, user=DM_B, arg=code)
        bad = await self.put(
            ws.LOOK_CODE, campaign=self.far, guild=GUILD_B, user=DM_B, arg="AAAAAAAAAA"
        )
        async with self.db.guild(GUILD_B) as conn:
            cur = await conn.execute("SELECT id, arg FROM web_rule_actions")
            held = {r["id"]: r["arg"] for r in await cur.fetchall()}
        self.assertEqual((held[ok], held[bad]), (code, "AAAAAAAAAA"))  # kept while it waits
        await self.take_and_run(GUILD_B)
        async with self.db.guild(GUILD_B) as conn:
            cur = await conn.execute("SELECT arg FROM web_rule_actions")
            self.assertEqual({r["arg"] for r in await cur.fetchall()}, {""})  # done and failed

    async def test_a_name_is_kept_on_a_finished_share(self) -> None:
        rid = await self.put(ws.PUBLISH, arg="Frostmaiden house rules")
        await self.take_and_run()
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute("SELECT arg FROM web_rule_actions WHERE id = %s", (rid,))
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual(row["arg"], "Frostmaiden house rules")

    async def test_a_dm_removed_since_the_check_is_not_allowed(self) -> None:
        await self.put(ws.PUBLISH, arg="Mine")
        item = await self.queue.claim_next(GUILD_A)
        assert item is not None
        campaign = await self.campaigns.get(GUILD_A, self.pub)
        assert campaign is not None
        # the in-memory check passes (the campaign object still lists them) but the store's own
        # check, in its transaction, no longer does
        stale = dataclasses.replace(campaign, dm_user_ids=frozenset({DM_A, PLAYER}))
        self.host.get_campaign = lambda *_: self._stale(stale)  # type: ignore[method-assign]
        out = await ws.apply(
            self.host, GUILD_A, ws.Action(item.id, self.pub, PLAYER, ws.PUBLISH, "Mine")
        )
        self.assertEqual(out.error, ws.NOT_ALLOWED)

    async def _stale(self, campaign: Any) -> Any:
        return campaign

    async def test_no_note_holds_a_rules_words_or_a_code(self) -> None:
        code = await self.share()
        await self.put(ws.LOOK_CODE, campaign=self.far, guild=GUILD_B, user=DM_B, arg=code)
        await self.put(ws.FOLLOW_CODE, campaign=self.far, guild=GUILD_B, user=DM_B, arg=code)
        await self.put(ws.UNFOLLOW, campaign=self.far, guild=GUILD_B, user=DM_B)
        for row in await self.take_and_run(GUILD_B) + await self.rows(GUILD_A):
            self.assertNotIn(WORDS, row["note"])
            self.assertNotIn(code, row["note"])


class TheQueue(Base):
    async def test_two_choices_run_in_the_order_made(self) -> None:
        one = await self.put(ws.PUBLISH, arg="Frostmaiden house rules")
        two = await self.put(ws.STOP_SHARING)
        first = await self.queue.claim_next(GUILD_A)
        assert first is not None and first.id == one
        self.assertIsNone(await self.queue.claim_next(GUILD_A))  # two waits for one
        await self.queue.finish(GUILD_A, first, ws.Outcome())
        second = await self.queue.claim_next(GUILD_A)
        assert second is not None and second.id == two

    async def test_a_claim_moves_the_row_to_working_and_sharing(self) -> None:
        await self.put(ws.PUBLISH, arg="Mine")
        item = await self.queue.claim_next(GUILD_A)
        assert item is not None and not item.retried
        (row,) = await self.rows()
        self.assertEqual((row["state"], row["stage"]), ("working", "sharing"))

    async def test_only_the_claiming_worker_finishes_a_row(self) -> None:
        await self.put(ws.PUBLISH, arg="Mine")
        item = await self.queue.claim_next(GUILD_A)
        assert item is not None
        self.assertTrue(await self.queue.finish(GUILD_A, item, ws.Outcome()))
        self.assertFalse(await self.queue.finish(GUILD_A, item, ws.Outcome(ws.SERVER)))

    async def test_a_stale_working_row_is_taken_again_and_applied_once(self) -> None:
        rid = await self.put(ws.PUBLISH, arg="Frostmaiden house rules", state="working")
        async with self.db.guild(GUILD_A) as conn:  # claimed long ago by a bot that stopped
            await conn.execute("UPDATE web_rule_actions SET claimed_at = 1000")
        item = await self.queue.claim_next(GUILD_A)
        assert item is not None
        self.assertTrue(item.retried)
        self.assertTrue(await ws.run_one(self.host, self.queue, GUILD_A, item))
        self.assertEqual(await self.result(rid), ("done", ""))
        # the door put it back again after another slow try: the share is already there
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "UPDATE web_rule_actions SET state = 'waiting', stage = 'waiting', claimed_at = 0,"
                " updated_at = %s",
                (NOW + 700,),
            )
        await self.take_and_run()
        self.assertEqual(await self.result(rid), ("done", ""))
        self.assertEqual(await self.note(rid), "✅ Sharing **Frostmaiden house rules**.")

    async def test_a_bug_ends_as_server_not_as_working(self) -> None:
        rid = await self.put(ws.PUBLISH, arg="Mine")

        async def boom(*_: Any) -> Any:
            raise RuntimeError("a database error quoting a rule")

        self.host.own_rules = boom  # type: ignore[method-assign,assignment]
        await self.take_and_run()
        self.assertEqual(await self.result(rid), ("failed", ws.SERVER))
        self.assertNotIn("quoting", await self.note(rid))

    async def test_old_rows_are_swept(self) -> None:
        await self.put(ws.PUBLISH, arg="Mine", created=NOW - ws.KEEP_FOR_S - 5)
        await self.put(ws.STOP_SHARING)
        self.assertEqual(await self.queue.sweep(GUILD_A), 1)
        self.assertEqual(len(await self.rows()), 1)


class TheSweep(unittest.IsolatedAsyncioTestCase):
    """The poll loop does not wait for a rule choice (#2793): a choice is a background task, one at
    a time for a server."""

    async def test_the_sweep_returns_while_a_choice_is_still_being_applied(self) -> None:
        started, release = asyncio.Event(), asyncio.Event()
        queue: dict[int, list[Any]] = {1: [SimpleNamespace(campaign_id="c")] * 2, 2: []}

        async def claim(guild_id: int) -> Any:
            return queue[guild_id].pop(0) if queue[guild_id] else None

        async def slow(self: Any, store: Any, guild_id: int, item: Any) -> None:
            started.set()
            await release.wait()

        b: Any = DMBot.__new__(DMBot)
        b.web_rule_actions = SimpleNamespace(claim_next=claim)
        b._rule_tasks = {}
        b._closing = False
        b._asking = set()
        b.rule_sets = object()
        guilds = PropertyMock(return_value=[SimpleNamespace(id=1), SimpleNamespace(id=2)])
        with (
            patch.object(DMBot, "guilds", guilds),
            patch.object(DMBot, "_run_rule_choice", slow),
        ):
            await asyncio.wait_for(b.run_web_rule_sharing(), timeout=1)  # returns at once
            await started.wait()
            await asyncio.wait_for(b.run_web_rule_sharing(), timeout=1)  # and again
            self.assertEqual(len(b._rule_tasks), 1)  # one at a time for the server
            self.assertEqual(len(queue[1]), 1)  # the second was not even claimed
            release.set()
            await asyncio.gather(*b._rule_tasks.values())
        await asyncio.sleep(0)
        self.assertEqual(b._rule_tasks, {})  # forgotten when done

    async def test_without_a_store_nothing_happens(self) -> None:
        b: Any = DMBot.__new__(DMBot)
        b.web_rule_actions = None
        b.rule_sets = None
        await b.run_web_rule_sharing()

    async def test_while_closing_rule_sharing_claims_nothing(self) -> None:
        b: Any = DMBot.__new__(DMBot)
        b.web_rule_actions = SimpleNamespace(claim_next=AsyncMock(return_value=None))
        b._rule_tasks = {}
        b._closing = True
        b.rule_sets = object()
        guilds = PropertyMock(return_value=[SimpleNamespace(id=1)])
        with patch.object(DMBot, "guilds", guilds):
            await b.run_web_rule_sharing()
        b.web_rule_actions.claim_next.assert_not_awaited()
