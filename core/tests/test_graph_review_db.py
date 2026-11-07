"""The careful check of the DM's graph clean-up fixes (#3741), on Postgres: the DM's own fix to an
isolated entry is queued only while the campaign shares its story to improve DMbot; the check runs
15 minutes after the last fix, under a lease, and keeps a report only of what DMbot missed; an
undone fix is dropped; "Stop recording me" deletes the reports that quote the person; a fixed
entry keeps its scene until the check has read it; the page's status door is the DM's only.
Invented names."""

from __future__ import annotations

import json
import time
from types import SimpleNamespace
from typing import Any

from dmbot.memory import graph_review as gr
from dmbot.memory.claims import ClaimStore
from dmbot.memory.graph_review_run import GraphReviews
from dmbot.memory.scenes import ENTITY, Scene, Scenes
from dmbot.web.recaps import _scoped
from tests.test_web_relationships import Web


class FakeAI:
    model = "careful-test"

    def __init__(self, answer: dict[str, Any]) -> None:
        self.answer = answer
        self.calls: list[str] = []

    async def complete(self, system: str, text: str, *, max_tokens: int = 0) -> Any:
        self.calls.append(text)
        return SimpleNamespace(text=json.dumps(self.answer), cut=False, refused=False)


class NoLines:
    async def around(self, *args: Any) -> None:
        return None


class Base(Web):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        # (the drunk and the sailors are linked only to each other: isolated, #3740)
        self.drunk = await self.add("Old Fenn", "character", role="npc")
        self.sailors = await self.add("The Salt Crew", "faction")
        await self.link("knows", self.drunk, self.sailors, "drunk-sailors")
        async with self.db.guild(self.g) as conn:
            await conn.execute(
                "UPDATE memory_entities SET description = %s WHERE id = %s",
                ("Old Fenn sails with the Salt Crew and owes Rook a debt.", self.drunk),
            )

    async def share(self, on: bool = True) -> None:
        await self.campaigns.set_share_improve(self.g, self.one.id, on)

    async def pending(self) -> dict[str, Any] | None:
        async with self.db.guild(self.g) as conn:
            cur = await conn.execute(
                "SELECT * FROM graph_review_pending WHERE campaign_id = %s", (self.one.id,)
            )
            return await cur.fetchone()

    async def reports(self) -> list[dict[str, Any]]:
        async with self.db.guild(self.g) as conn:
            cur = await conn.execute(
                "SELECT * FROM graph_reviews WHERE campaign_id = %s", (self.one.id,)
            )
            return list(await cur.fetchall())

    def runner(self, ai: FakeAI, *, at: float) -> GraphReviews:
        async def consenting(_guild: int) -> set[int]:
            return {7}

        async def allowed(ids: Any) -> set[int]:
            return set(ids)

        return GraphReviews(
            self.memory, NoLines(), ai,  # type: ignore[arg-type]  # (no transcript is read)
            consenting=consenting, improvement_allowed=allowed, clock=lambda: at,
        )  # fmt: skip


class Queuing(Base):
    async def test_with_the_switch_off_a_fix_queues_nothing(self) -> None:
        await self.link("knows", self.rook, self.drunk, "rook-drunk")
        self.assertIsNone(await self.pending())

    async def test_the_dm_s_fix_to_an_isolated_entry_queues_and_another_pushes_the_wait(
        self,
    ) -> None:
        await self.share()
        rid = await self.link("knows", self.rook, self.drunk, "rook-drunk")
        got = await self.pending()
        assert got is not None
        self.assertEqual(got["fixes"], [{"kind": "link", "relation": rid, "a": self.rook,
                                         "b": self.drunk}])  # fmt: skip
        self.assertEqual(set(got["entity_ids"]), {self.rook, self.drunk})
        keeper = await self.add("Gull Keeper", "character", role="npc")
        await self.link("knows", self.rook, keeper, "rook-keeper")
        again = await self.pending()
        assert again is not None
        self.assertEqual(len(again["fixes"]), 2)
        self.assertGreaterEqual(again["last_fix_at"], got["last_fix_at"])

    async def test_a_link_between_entries_already_linked_or_another_source_queues_nothing(
        self,
    ) -> None:
        await self.share()
        await self.link("knows", self.rook, self.keep, "rook-keep-2")  # (a place: never isolated)
        await self.memory.add_relation(
            self.g, self.one.id, self.rook, "knows", self.drunk, source="scan",
            confidence=0.8, status="proposed",
        )  # fmt: skip
        self.assertIsNone(await self.pending())


class TheCheck(Base):
    async def fix(self) -> str:
        await self.share()
        return await self.link("knows", self.rook, self.drunk, "rook-drunk")

    async def test_after_15_minutes_a_missed_fix_is_reported_once(self) -> None:
        await self.fix()
        ai = FakeAI({"fixes": [{"fix": 1, "in_story": True, "quote": "owes Rook a debt"}]})
        later = time.time() + gr.WAIT_S + 5
        self.assertEqual(await self.runner(ai, at=later).round(self.g), 1)
        (report,) = await self.reports()
        self.assertEqual((report["checked"], report["missed"], report["speaker_ids"]), (1, 1, []))
        self.assertIn(f"`{self.one.id}`", report["report"])
        self.assertNotIn(self.one.name, report["report"])
        self.assertIn('"owes Rook a debt"', report["report"])
        self.assertIsNone(await self.pending())
        self.assertEqual(await self.runner(ai, at=later).round(self.g), 0)
        self.assertEqual(len(ai.calls), 1)

    async def test_only_in_the_dm_s_head_makes_no_report(self) -> None:
        await self.fix()
        ai = FakeAI({"fixes": [{"fix": 1, "in_story": False}]})
        self.assertEqual(await self.runner(ai, at=time.time() + gr.WAIT_S + 5).round(self.g), 0)
        self.assertEqual(await self.reports(), [])
        self.assertIsNone(await self.pending())

    async def test_before_15_minutes_nothing_runs_and_a_lease_is_not_taken_twice(self) -> None:
        await self.fix()
        ai = FakeAI({"fixes": []})
        self.assertEqual(await self.runner(ai, at=time.time() + 60).round(self.g), 0)
        self.assertEqual(ai.calls, [])
        later = time.time() + gr.WAIT_S + 5
        taken = await self.runner(ai, at=later)._claim(self.g)
        self.assertIsNotNone(taken)
        self.assertIsNone(await self.runner(ai, at=later)._claim(self.g))  # (held)

    async def test_a_fix_undone_before_the_check_is_dropped_without_a_call(self) -> None:
        rid = await self.fix()
        async with self.db.guild(self.g) as conn:
            await conn.execute(
                "UPDATE memory_relations SET status = 'rejected' WHERE id = %s", (rid,)
            )
        ai = FakeAI({"fixes": []})
        self.assertEqual(await self.runner(ai, at=time.time() + gr.WAIT_S + 5).round(self.g), 0)
        self.assertEqual(ai.calls, [])
        self.assertIsNone(await self.pending())

    async def test_a_fixed_entry_keeps_its_scene_until_the_check_has_read_it(self) -> None:
        scenes = Scenes(self.memory)
        await scenes.keep(
            self.g, self.one.id, [Scene(ENTITY, self.drunk, "Fenn drank at the Gull.", 2)],
            opted_in=set(), prompt_version="p1", model="m1",
        )  # fmt: skip
        await self.fix()  # (now linked to Rook: the sweep would take the scene)
        await scenes.sweep(self.g, self.one.id)
        got = await scenes.of(self.g, self.one.id, ENTITY, [self.drunk], opted_in=set())
        self.assertEqual(list(got), [self.drunk])
        ai = FakeAI({"fixes": [{"fix": 1, "in_story": False}]})
        await self.runner(ai, at=time.time() + gr.WAIT_S + 5).round(self.g)
        self.assertIn("Fenn drank at the Gull.", ai.calls[0])  # (the scene was read)
        await scenes.sweep(self.g, self.one.id)
        got = await scenes.of(self.g, self.one.id, ENTITY, [self.drunk], opted_in=set())
        self.assertEqual(got, {})


class Forgetting(Base):
    async def test_stop_recording_me_deletes_every_report_that_quotes_them(self) -> None:
        async with self.db.guild(self.g) as conn:
            for rid, speakers in (("a" * 32, [7, 8]), ("b" * 32, [8])):
                await conn.execute(
                    "INSERT INTO graph_reviews (guild_id, campaign_id, id, created_at, checked,"
                    " missed, report, speaker_ids, prompt_version, model)"
                    " VALUES (%s, %s, %s, 1, 1, 1, 'r', %s, 'p', 'm')",
                    (self.g, self.one.id, rid, speakers),
                )
        await ClaimStore(self.memory).forget_speaker(self.g, 7)
        self.assertEqual([r["id"] for r in await self.reports()], ["b" * 32])


class TheStatusDoor(Base):
    async def status(self, who: Any) -> Any:
        async with self.web.user(who.user_id, session=who.id_hash) as conn:
            await _scoped(conn, self.g)
            cur = await conn.execute(
                "SELECT dmbot_web_graph_review_status(%s, %s) AS r", (self.g, self.one.id)
            )
            return (await cur.fetchone() or {})["r"]

    async def test_counts_for_the_dm_and_nothing_for_a_player(self) -> None:
        await self.share()
        await self.link("knows", self.rook, self.drunk, "rook-drunk")
        got = await self.status(self.session)
        self.assertEqual((got["on"], got["waiting"]["fixes"], got["last"]), (True, 1, None))
        self.assertIsNone(await self.status(self.player))
