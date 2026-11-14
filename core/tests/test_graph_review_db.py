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

    async def ended(self, checked: int, missed: int, *, stopped: bool = False) -> None:
        """The batch is done, and the row holds how the check ended, for the page."""
        got = await self.pending()
        assert got is not None
        self.assertEqual(got["fixes"], [])
        self.assertIsNone(got["claimed_at"])
        self.assertIsNotNone(got["checked_at"])
        self.assertEqual(
            (got["checked"], got["missed"], got["stopped"]), (checked, missed, stopped)
        )

    def runner(
        self, ai: FakeAI, *, at: float, transcripts: Any = None, allowed: Any = None
    ) -> GraphReviews:
        async def consenting(_guild: int) -> set[int]:
            return {7, 8}  # (9 never agreed to be recorded)

        async def everyone(ids: Any) -> set[int]:
            return set(ids)

        return GraphReviews(
            self.memory, transcripts or NoLines(), ai,  # type: ignore[arg-type]  # (a stand-in)
            consenting=consenting, improvement_allowed=allowed or everyone, clock=lambda: at,
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
        self.assertIn('In the story (description): "owes Rook a debt"', report["report"])
        await self.ended(1, 1)
        self.assertEqual(await self.runner(ai, at=later).round(self.g), 0)
        self.assertEqual(len(ai.calls), 1)

    async def test_only_in_the_dm_s_head_makes_no_report(self) -> None:
        await self.fix()
        ai = FakeAI({"fixes": [{"fix": 1, "in_story": False}]})
        self.assertEqual(await self.runner(ai, at=time.time() + gr.WAIT_S + 5).round(self.g), 0)
        self.assertEqual(await self.reports(), [])
        await self.ended(1, 0)  # (the page can say "Checked: DMbot missed 0")

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
        await self.ended(0, 0)

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
        self.assertIsNone(got["waiting"]["checkingSince"])
        from dmbot.web import graph_cleanup

        page = await graph_cleanup.read_cleanup(
            self.web, self.session, self.cref, now=time.time() + 60
        )
        self.assertEqual(page["review"]["state"], "waiting")
        self.assertEqual(page["review"]["fixes"], 1)
        self.assertIn(page["review"]["minutesLeft"], (13, 14))
        self.assertIsNone(await self.status(self.player))


class Around(SimpleNamespace):
    pass


class ThreeSpeakers:
    """Every mention's line with its neighbours from three people: 7 agrees, 8 agrees but opted
    out of improving DMbot, 9 never agreed. The real `around` is given who may be read; this
    stand-in ignores that, to prove the check leaves the others out itself as well."""

    async def around(self, guild_id: int, session: str, ms: int, user: int, agreed: Any) -> Any:
        return Around(
            before=(8, "Fenn told me he owes Rook a debt."),
            line=(7, "Old Fenn waved at Rook from the Gull."),
            after=(9, "Fenn and Rook are cousins, everyone knows."),
        )


class Consent(Base):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        await self.memory.add_mention(
            self.g, self.one.id, self.drunk, line_ref="s1:1000:7", span=(0, 4), confidence=0.9,
            method="exact", source="cleaner", session_started_at=1,
        )  # fmt: skip
        await self.share()
        await self.link("knows", self.rook, self.drunk, "rook-drunk")

    @staticmethod
    async def not_8(ids: Any) -> set[int]:
        return set(ids) - {8}

    async def test_only_the_words_of_people_who_agree_and_did_not_opt_out_reach_the_call(
        self,
    ) -> None:
        ai = FakeAI({"fixes": [{"fix": 1, "in_story": False}]})
        runner = self.runner(
            ai, at=time.time() + gr.WAIT_S + 5, transcripts=ThreeSpeakers(), allowed=self.not_8
        )
        await runner.round(self.g)
        (sent,) = ai.calls
        self.assertIn("waved at Rook", sent)
        self.assertNotIn("told me he owes", sent)  # (8 opted out of improving DMbot)
        self.assertNotIn("cousins", sent)  # (9 never agreed)

    async def test_someone_who_stops_during_the_call_is_not_quoted(self) -> None:
        asked = 0

        async def stops_after_the_first_ask(ids: Any) -> set[int]:
            nonlocal asked
            asked += 1
            return set(ids) if asked == 1 else set(ids) - {7}

        ai = FakeAI({"fixes": [{"fix": 1, "in_story": True, "quote": "waved at Rook"}]})
        runner = self.runner(
            ai, at=time.time() + gr.WAIT_S + 5, transcripts=ThreeSpeakers(),
            allowed=stops_after_the_first_ask,
        )  # fmt: skip
        self.assertEqual(await runner.round(self.g), 0)
        self.assertEqual(await self.reports(), [])
        await self.ended(1, 0)


class TheLease(Base):
    async def fix(self) -> str:
        await self.share()
        return await self.link("knows", self.rook, self.drunk, "rook-drunk")

    async def test_a_fix_made_during_the_check_stays_for_the_next(self) -> None:
        await self.fix()
        later = time.time() + gr.WAIT_S + 5
        runner = self.runner(FakeAI({"fixes": [{"fix": 1, "in_story": False}]}), at=later)
        taken = await runner._claim(self.g)
        assert taken is not None
        keeper = await self.add("Gull Keeper", "character", role="npc")
        await self.link("knows", self.rook, keeper, "rook-keeper")  # (while it checks)
        await runner._check(self.g, self.one.id, taken[1], taken[2])
        got = await self.pending()
        assert got is not None
        self.assertEqual([f["b"] for f in got["fixes"]], [keeper])
        self.assertEqual(got["checked"], 1)

    async def test_a_check_that_lost_its_lease_or_its_switch_writes_nothing(self) -> None:
        await self.fix()
        later = time.time() + gr.WAIT_S + 5
        ai = FakeAI({"fixes": [{"fix": 1, "in_story": True, "quote": "owes Rook a debt"}]})
        runner = self.runner(ai, at=later)
        taken = await runner._claim(self.g)
        assert taken is not None
        self.assertEqual(await runner._check(self.g, self.one.id, taken[1], taken[2] + 1), 0)
        self.assertEqual(await self.reports(), [])
        await self.share(False)  # (off: the batch goes, and nothing is written)
        self.assertEqual(await runner._check(self.g, self.one.id, taken[1], taken[2]), 0)
        self.assertEqual(await self.reports(), [])
        self.assertIsNone(await self.pending())

    async def test_a_dead_check_is_taken_again_and_after_its_tries_recorded_as_stopped(
        self,
    ) -> None:
        await self.fix()
        at = time.time() + gr.WAIT_S + 5
        for _ in range(gr.TRIES):
            self.assertIsNotNone(await self.runner(FakeAI({}), at=at)._claim(self.g))
            at += gr.RETAKE_AFTER_S + 1  # (its bot died: the lease runs out)
        self.assertEqual(await self.runner(FakeAI({}), at=at).round(self.g), 0)
        await self.ended(0, 0, stopped=True)

    async def test_a_cut_answer_is_asked_again_in_halves(self) -> None:
        await self.fix()
        keeper = await self.add("Gull Keeper", "character", role="npc")
        await self.link("knows", self.rook, keeper, "rook-keeper")
        replies = [
            SimpleNamespace(text="", cut=True, refused=False),
            SimpleNamespace(text=json.dumps({"fixes": [{"fix": 1, "in_story": False}]}),
                            cut=False, refused=False),
            SimpleNamespace(text=json.dumps({"fixes": [{"fix": 2, "in_story": False}]}),
                            cut=False, refused=False),
        ]  # fmt: skip

        class Cuts(FakeAI):
            async def complete(self, system: str, text: str, *, max_tokens: int = 0) -> Any:
                self.calls.append(text)
                return replies[len(self.calls) - 1]

        ai = Cuts({})
        await self.runner(ai, at=time.time() + gr.WAIT_S + 5).round(self.g)
        self.assertEqual(len(ai.calls), 3)
        await self.ended(2, 0)

    async def test_same_as_goes_through_the_store_s_own_write(self) -> None:
        await self.share()
        await self.memory.merge(
            self.g, self.one.id, self.mira, self.drunk, source="dm", dm_said_same=True
        )
        got = await self.pending()
        assert got is not None
        self.assertIn({"kind": "same_as", "entity": self.drunk, "into": self.mira}, got["fixes"])
        self.assertFalse([f for f in got["fixes"] if f["kind"] == "link"])  # (moved links)
