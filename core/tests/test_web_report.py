"""The after-session report on the website (#2661), read through the website's own database role: a
DM of the campaign reads one session's minor contradictions, what the party has wrong and the DM's
answers; a player or another campaign's DM is refused; evidence is only kinds and placeholders.
Invented names throughout."""

from __future__ import annotations

import json
from typing import Any

import httpx

from dmbot.memory import beliefs
from dmbot.memory import report_store as rs
from dmbot.memory.models import DM as DM_SOURCE
from dmbot.memory.store import MemoryStore
from dmbot.web import report, sessions
from dmbot.web.app import create_app
from dmbot.web.payments import FakeProvider
from dmbot.web.recaps import RecapError
from tests.test_recap_web import BOB, NOW, Base
from tests.test_web_api import ALICE, THURSDAY, FakeDiscord, settings

GUILD = THURSDAY.id
S1, S2, S3 = 1_000, 2_000, 3_000  # three sessions, by when each began


class Reading(Base):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.memory = MemoryStore(self.db)
        self.cref = f"{GUILD}.{self.one.id}"
        for n, at in enumerate((S1, S2, S3), start=1):
            await self.sql(
                "INSERT INTO transcript_sessions (id, guild_id, campaign_id, started_at, ended_at,"
                " line_count) VALUES (%s, %s, %s, %s, %s, 5)",
                f"{n:032x}", GUILD, self.one.id, at, at + 100,
            )  # fmt: skip
        self.aldric = await self.name("Aldric", self.one.id)
        self.circle = await self.name("the Ashen Crown", self.one.id)
        self.ally = await self.link("ally_of")
        self.enemy = await self.link("enemy_of")
        self.party = await self.perspective("party")
        self.truth = await self.perspective("truth")

    async def sql(self, statement: str, *args: Any) -> None:
        async with self.db.guild(GUILD) as conn:
            await conn.execute(statement, args)

    async def one_row(self, statement: str, *args: Any) -> Any:
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(statement, args)
            return await cur.fetchone()

    async def name(self, text: str, campaign: str) -> str:
        made = await self.memory.add_entity(
            GUILD, campaign, type="character", name=text, source=DM_SOURCE, status="confirmed"
        )
        return made.value.id

    async def link(self, predicate: str, *, campaign: str | None = None) -> str:
        a, b = (self.aldric, self.circle) if campaign is None else await self.pair(campaign)
        made = await self.memory.add_relation(
            GUILD, campaign or self.one.id, a, predicate, b,
            source=DM_SOURCE, confidence=1.0, status="confirmed",
        )  # fmt: skip
        return str(made.value[0].id)

    async def pair(self, campaign: str) -> tuple[str, str]:
        return await self.name("Cass", campaign), await self.name("the Veil", campaign)

    async def perspective(self, kind: str, campaign: str | None = None) -> str:
        row = await self.one_row(
            "SELECT id FROM memory_perspectives WHERE guild_id = %s AND campaign_id = %s"
            " AND kind = %s",
            GUILD, campaign or self.one.id, kind,
        )  # fmt: skip
        if row is None:  # (they are made with the first belief, or when asked for)
            async with self.memory._write(GUILD, campaign or self.one.id, "dm") as w:
                await beliefs.perspective_ids(w)
            row = await self.one_row(
                "SELECT id FROM memory_perspectives WHERE guild_id = %s AND campaign_id = %s"
                " AND kind = %s",
                GUILD, campaign or self.one.id, kind,
            )  # fmt: skip
        assert row is not None
        return str(row["id"])

    async def contradiction(
        self, grade: str = "minor", at: int | None = S1, *, reason: str = "They clash.",
        unknown: bool = False,
    ) -> str:  # fmt: skip
        first, second = sorted((self.ally, self.enemy))
        await self.sql(
            "INSERT INTO memory_contradictions (guild_id, campaign_id, perspective_id, kind,"
            " first_relation_id, second_relation_id, grade, reason, times_unknown, found_at,"
            " session_started_at) VALUES (%s, %s, %s, 'contradiction', %s, %s, %s, %s, %s, 1, %s)",
            GUILD, self.one.id, self.party, first, second, grade, reason, unknown, at,
        )  # fmt: skip
        return rs.contradiction_key(self.party, "contradiction", first, second)

    async def difference(
        self, sort: str = "false", at: int | None = S1, *, denied: bool = False,
        reason: str = "Not so.", relation: str | None = None,
    ) -> str:  # fmt: skip
        await self.sql(
            "INSERT INTO memory_differences (guild_id, campaign_id, perspective_id, relation_id,"
            " truth_relation_id, kind, sort, reason, found_at, session_started_at)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 1, %s)",
            GUILD, self.one.id, self.party, relation or self.ally,
            None if denied else (self.ally if relation else self.enemy),
            "denied" if denied else "clashes", sort, reason, at,
        )  # fmt: skip
        return rs.difference_key(self.party, relation or self.ally)

    async def answer(
        self, key: str, kind: str, answer: str, *, when: int, remind_from: int | None = None
    ) -> None:
        await self.sql(
            "INSERT INTO report_answers (guild_id, campaign_id, item_key, item_kind, answer,"
            " answered_by, answered_at, remind_from) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            GUILD, self.one.id, key, kind, answer, ALICE.id, when, remind_from,
        )  # fmt: skip

    async def read(self, at: int = S1, who: Any = None) -> dict[str, Any]:
        return await report.read_report(self.web, who or self.session, self.cref, at)

    async def refused(self, at: int = S1, who: Any = None, ref: str | None = None) -> str:
        with self.assertRaises(RecapError) as err:
            await report.read_report(self.web, who or self.session, ref or self.cref, at)
        return err.exception.code


class TheReport(Reading):
    async def test_a_session_with_nothing_found_is_none_with_zero_counts(self) -> None:
        got = await self.read()
        self.assertEqual((got["state"], got["progress"]), ("none", None))
        self.assertEqual(
            got["counts"],
            {"contradictions": 0, "disguised": 0, "misunderstood": 0, "false": 0,
             "open": 0, "answered": 0},
        )  # fmt: skip
        self.assertEqual(got["session"], {"at": S1, "number": 1})

    async def test_a_minor_contradiction_reads_as_two_statements_in_dmbots_words(self) -> None:
        key = await self.contradiction(reason="One says friend, the other foe.", unknown=True)
        got = await self.read()
        self.assertEqual(got["state"], "ready")
        (item,) = got["contradictions"]
        self.assertEqual(item["id"], key)
        self.assertEqual(item["reason"], "One says friend, the other foe.")
        self.assertFalse(item["gameTimeKnown"])
        self.assertEqual({item["first"]["view"], item["second"]["view"]}, {"The party"})
        texts = {item["first"]["text"], item["second"]["text"]}
        self.assertEqual(len(texts), 2)
        for words in texts:
            self.assertTrue("Aldric" in words and "the Ashen Crown" in words, words)
        self.assertIsNone(item["answer"])
        self.assertEqual(got["counts"]["contradictions"], 1)
        self.assertEqual((got["counts"]["open"], got["counts"]["answered"]), (1, 0))

    async def test_only_minor_ones_are_listed_and_a_not_graded_one_is_only_counted(self) -> None:
        await self.contradiction("major")
        got = await self.read()
        self.assertEqual(got["contradictions"], [])
        await self.sql("DELETE FROM memory_contradictions WHERE guild_id = %s", GUILD)
        await self.contradiction("not_graded")
        got = await self.read()
        self.assertEqual((got["contradictions"], got["notSorted"]), ([], 1))

    async def test_what_the_party_has_wrong_is_grouped_by_sort_with_the_dm_view(self) -> None:
        await self.difference("misunderstood")
        got = await self.read()
        self.assertEqual(
            [len(got["differences"][s]) for s in ("disguised", "misunderstood", "false")], [0, 1, 0]
        )
        (d,) = got["differences"]["misunderstood"]
        self.assertEqual(d["view"], "The party")
        for words in (d["believes"], d["truth"]):
            self.assertTrue("Aldric" in words and "the Ashen Crown" in words, words)
        self.assertNotEqual(d["believes"], d["truth"])
        self.assertEqual(
            {d["open"]["a"], d["open"]["b"]}, {self.aldric, self.circle}
        )  # (any order)
        self.assertEqual(got["counts"]["misunderstood"], 1)

    async def test_a_denied_connection_says_the_dm_view_says_it_is_not_so(self) -> None:
        await self.difference("false", denied=True)
        (d,) = (await self.read())["differences"]["false"]
        self.assertEqual(d["truth"], report.NOT_SO)

    async def test_a_difference_never_sorted_is_counted_and_not_listed(self) -> None:
        await self.difference("not_sorted")
        got = await self.read()
        self.assertEqual([len(v) for v in got["differences"].values()], [0, 0, 0])
        self.assertEqual(got["notSorted"], 1)
        self.assertEqual(got["state"], "none")  # nothing to show, and no run

    async def test_an_item_found_in_another_session_is_not_in_this_ones_report(self) -> None:
        await self.contradiction(at=S2)
        await self.difference(at=S2)
        got = await self.read(S1)
        self.assertEqual((got["contradictions"], got["counts"]["false"]), ([], 0))
        self.assertEqual(len((await self.read(S2))["contradictions"]), 1)
        self.assertEqual((await self.read(S2))["session"]["number"], 2)

    async def test_an_answer_shows_and_one_made_before_the_session_is_not_raised_again(
        self,
    ) -> None:
        key = await self.contradiction(at=S2)
        await self.answer(key, "contradiction", "intended", when=S2 + 50)
        got = await self.read(S2)  # answered during this session: shown, so Undo is possible
        self.assertEqual(got["contradictions"][0]["answer"], {"action": "intended", "at": S2 + 50})
        self.assertEqual((got["counts"]["open"], got["counts"]["answered"]), (0, 1))
        await self.sql(
            "UPDATE memory_contradictions SET session_started_at = %s WHERE guild_id = %s",
            S3,
            GUILD,
        )  # (found again in the next session)
        self.assertEqual((await self.read(S3))["contradictions"], [])  # "That's intended" is kept

    async def test_not_a_problem_and_fixed_are_not_raised_again_either(self) -> None:
        key = await self.difference(at=S3)
        for action in ("not_a_problem", "fixed"):
            await self.sql("DELETE FROM report_answers WHERE guild_id = %s", GUILD)
            await self.answer(key, "difference", action, when=S1)
            self.assertEqual((await self.read(S3))["differences"]["false"], [], action)

    async def test_remind_me_comes_back_in_the_next_session_with_where_it_was_asked(self) -> None:
        key = await self.difference(at=S1)
        await self.answer(key, "difference", "remind", when=S1 + 50, remind_from=S1)
        here = (await self.read(S1))["differences"]["false"][0]
        self.assertEqual((here["answer"]["action"], here["remindedFrom"]), ("remind", None))
        nxt = (await self.read(S2))["differences"]["false"]  # the item is from session 1
        self.assertEqual((len(nxt), nxt[0]["remindedFrom"]), (1, 1))
        self.assertEqual(
            await self.read(S3),
            (await self.read(S3))
            | {"differences": {"disguised": [], "misunderstood": [], "false": []}},
        )

    async def test_a_reminded_item_is_listed_before_the_new_ones(self) -> None:
        old = await self.contradiction(at=S1)
        await self.answer(old, "contradiction", "remind", when=S1 + 5, remind_from=S1)
        await self.sql(
            "INSERT INTO memory_differences (guild_id, campaign_id, perspective_id, relation_id,"
            " truth_relation_id, kind, sort, reason, found_at, session_started_at)"
            " VALUES (%s, %s, %s, %s, NULL, 'denied', 'false', 'New.', 99, %s)",
            GUILD, self.one.id, self.party, self.enemy, S2,
        )  # fmt: skip
        got = await self.read(S2)
        self.assertEqual(len(got["contradictions"]), 1)
        self.assertEqual(got["contradictions"][0]["remindedFrom"], 1)
        self.assertEqual(len(got["differences"]["false"]), 1)

    async def test_a_reminded_item_comes_before_a_new_one_in_the_same_list(self) -> None:
        old = await self.difference(at=S1)
        await self.answer(old, "difference", "remind", when=S1 + 5, remind_from=S1)
        await self.sql(
            "INSERT INTO memory_differences (guild_id, campaign_id, perspective_id, relation_id,"
            " truth_relation_id, kind, sort, reason, found_at, session_started_at)"
            " VALUES (%s, %s, %s, %s, NULL, 'denied', 'false', 'Newer.', 99999, %s)",
            GUILD, self.one.id, self.party, self.enemy, S2,
        )  # fmt: skip
        reasons = [d["reason"] for d in (await self.read(S2))["differences"]["false"]]
        self.assertEqual(reasons, ["Not so.", "Newer."])  # the reminded one first, though older

    async def test_a_reminded_item_counts_as_open_again(self) -> None:
        key = await self.difference(at=S1)
        await self.answer(key, "difference", "remind", when=S1 + 5, remind_from=S1)
        here = (await self.read(S1))["counts"]
        self.assertEqual((here["open"], here["answered"]), (0, 1))  # just answered
        later = (await self.read(S2))["counts"]
        self.assertEqual((later["open"], later["answered"]), (1, 0))  # it has come back

    async def test_a_reminded_item_found_again_in_the_same_later_session_shows_once(self) -> None:
        key = await self.difference(at=S1)
        await self.answer(key, "difference", "remind", when=S1 + 5, remind_from=S1)
        await self.sql(
            "UPDATE memory_differences SET session_started_at = %s WHERE guild_id = %s", S2, GUILD
        )  # (found again in session 2)
        got = (await self.read(S2))["differences"]["false"]
        self.assertEqual((len(got), got[0]["remindedFrom"]), (1, 1))

    async def test_the_reminder_waits_for_the_next_session_the_page_lists(self) -> None:
        key = await self.difference(at=S1)
        await self.answer(key, "difference", "remind", when=S1 + 5, remind_from=S1)
        await self.sql(
            "INSERT INTO transcript_sessions (id, guild_id, campaign_id, started_at, line_count)"
            " VALUES (%s, %s, %s, %s, 0)",
            f"{9:032x}",
            GUILD,
            self.one.id,
            S1 + 10,
        )  # (an empty session in between, which the transcripts page never lists)
        self.assertEqual(len((await self.read(S2))["differences"]["false"]), 1)

    async def test_a_list_is_cut_at_200_keeping_the_newest(self) -> None:
        for n in range(201):  # (one point of view each: a connection holds one row per view)
            who = await self.name(f"Watcher {n}", self.one.id)
            await self.sql(
                "INSERT INTO memory_perspectives (guild_id, campaign_id, id, kind, entity_id,"
                " created_at) VALUES (%s, %s, %s, 'npc', %s, 1)",
                GUILD, self.one.id, f"{n + 1:032x}", who,
            )  # fmt: skip
            await self.sql(
                "INSERT INTO memory_differences (guild_id, campaign_id, perspective_id,"
                " relation_id, truth_relation_id, kind, sort, reason, found_at,"
                " session_started_at) VALUES (%s, %s, %s, %s, NULL, 'denied', 'false', %s, %s, %s)",
                GUILD, self.one.id, f"{n + 1:032x}", self.ally, f"r{n}", n, S1,
            )  # fmt: skip
        got = (await self.read())["differences"]["false"]
        self.assertEqual(len(got), 200)
        self.assertEqual(got[0]["reason"], "r200")  # the newest first
        self.assertNotIn("r0", [d["reason"] for d in got])  # the oldest is the one left out

    async def test_evidence_through_a_belief_counts_for_that_view_only_and_is_capped_at_10(
        self,
    ) -> None:
        await self.contradiction()
        await self.sql(
            "INSERT INTO memory_beliefs (guild_id, campaign_id, id, perspective_id, relation_id,"
            " confidence, status, seq, recorded_at, session_at) VALUES"
            " (%s, %s, %s, %s, %s, 1, 'revealed_fact', 9999, 1, NULL),"
            " (%s, %s, %s, %s, %s, 0.8, 'theory', 9998, 1, %s)",
            GUILD, self.one.id, "e" * 32, self.truth, self.ally,
            GUILD, self.one.id, "d" * 32, self.party, self.ally, S1,
        )  # fmt: skip
        for owner, kind, at in (
            ("d" * 32, "clue", S1),
            ("e" * 32, "event", S2),  # (another view's: must not appear)
        ):
            await self.sql(
                "INSERT INTO memory_evidence (guild_id, campaign_id, belief_id, kind, target_id,"
                " session_started_at, added_at) VALUES (%s, %s, %s, %s, 'x', %s, 1)",
                GUILD, self.one.id, owner, kind, at,
            )  # fmt: skip
        for n in range(12):  # twelve more on the connection itself
            await self.sql(
                "INSERT INTO memory_evidence (guild_id, campaign_id, relation_id, kind, target_id,"
                " session_started_at, added_at) VALUES (%s, %s, %s, 'claim', %s, %s, %s)",
                GUILD, self.one.id, self.ally, f"t{n}", S3, 10 + n,
            )  # fmt: skip
        got = await self.read()
        sides = [got["contradictions"][0]["first"], got["contradictions"][0]["second"]]
        side = max(sides, key=lambda x: len(x["evidence"]))  # (the side that is the ally link)
        kinds = [e["kind"] for e in side["evidence"]]
        self.assertEqual(len(kinds), 10)
        self.assertIn("clue", kinds)  # through this view's belief
        self.assertNotIn("event", kinds)  # another view's belief never leaks in

    async def test_an_item_whose_connection_is_gone_is_skipped_not_fatal(self) -> None:
        shaped = report.shape(
            {
                "now": 5,
                "run": None,
                "notSorted": 0,
                "contradictions": [{"key": "a" * 32, "first": None, "second": None}],
                "differences": [{"key": "b" * 32, "sort": "false", "believes": None}],
            },
            S1,
            [],
        )
        self.assertEqual((shaped["contradictions"], shaped["differences"]["false"]), ([], []))
        self.assertEqual(shaped["state"], "none")

    async def test_evidence_is_kinds_and_placeholders_never_a_line_or_a_speaker(self) -> None:
        await self.contradiction()
        owner = {"relation_id": self.ally}  # (a connection's own evidence)
        secret_ref = f"{1:032x}:61000:{ALICE.id}"
        await self.sql(
            "INSERT INTO memory_evidence (guild_id, campaign_id, relation_id, belief_id, kind,"
            " target_id, session_started_at, added_at) VALUES (%s, %s, %s, %s, 'line', %s, %s, 1)",
            GUILD, self.one.id, owner.get("relation_id"), owner.get("belief_id"), secret_ref, S1,
        )  # fmt: skip
        await self.sql(
            "INSERT INTO memory_evidence (guild_id, campaign_id, relation_id, belief_id, kind,"
            " target_id, session_started_at, added_at)"
            " VALUES (%s, %s, %s, %s, 'line', NULL, %s, 2)",
            GUILD, self.one.id, owner.get("relation_id"), owner.get("belief_id"), S2,
        )  # fmt: skip
        got = await self.read()
        seen = [
            e
            for c in got["contradictions"]
            for s in (c["first"], c["second"])
            for e in s["evidence"]
        ]
        self.assertIn({"kind": "line", "session": 1, "available": True}, seen)
        self.assertIn({"kind": "line", "session": 2, "available": False}, seen)
        text = json.dumps(got)
        self.assertNotIn(secret_ref, text)
        self.assertNotIn("quote", text)
        self.assertNotIn(str(ALICE.id), text)

    async def test_a_run_being_made_shows_its_stage_counts_and_times(self) -> None:
        now = int((await self.one_row("SELECT extract(epoch FROM now())::BIGINT AS n"))["n"])
        await self.sql(
            "INSERT INTO memory_report_runs (guild_id, campaign_id, session_at, state, stage,"
            " done, total, started_at, updated_at)"
            " VALUES (%s, %s, %s, 'making', 'grading', 3, 10, %s, %s)",
            GUILD, self.one.id, S1, now - 60, now - 5,
        )  # fmt: skip
        got = await self.read()
        self.assertEqual(got["state"], "making")
        p = got["progress"]
        self.assertEqual(
            (p["stage"], p["done"], p["total"], p["unit"], p["state"], p["reason"]),
            ("grading", 3, 10, "items", "running", None),
        )
        self.assertEqual((p["startedAt"], p["updatedAt"]), (now - 60, now - 5))
        self.assertAlmostEqual(p["serverNow"], now, delta=5)

    async def test_the_sorting_stage_shows_its_counts_too(self) -> None:
        await self.sql(
            "INSERT INTO memory_report_runs (guild_id, campaign_id, session_at, state, stage, done,"
            " total, started_at, updated_at) VALUES (%s, %s, %s, 'making', 'sorting', 2, 7,"
            " extract(epoch FROM now())::BIGINT - 10, extract(epoch FROM now())::BIGINT - 1)",
            GUILD, self.one.id, S1,
        )  # fmt: skip
        p = (await self.read())["progress"]
        self.assertEqual((p["stage"], p["done"], p["total"], p["unit"]), ("sorting", 2, 7, "items"))

    async def test_the_sql_item_keys_are_the_report_stores_keys(self) -> None:
        from dmbot import schema

        for perspective, kind, first, second in (
            ("a" * 32, "contradiction", "1" * 32, "2" * 32),
            ("0" * 32, "too_many", "f" * 32, "f" * 31 + "0"),
            (self.party, "held_twice", self.ally, self.enemy),
        ):
            row = await self.one_row(
                f"SELECT {schema._REPORT_KEY_C} AS k FROM (VALUES (%s, %s, %s, %s))"
                " m(perspective_id, kind, first_relation_id, second_relation_id)",
                perspective, kind, first, second,
            )  # fmt: skip
            self.assertEqual(row["k"], rs.contradiction_key(perspective, kind, first, second), kind)
        for perspective, relation in (("a" * 32, "b" * 32), (self.party, self.ally)):
            row = await self.one_row(
                f"SELECT {schema._REPORT_KEY_D} AS k FROM (VALUES (%s, %s))"
                " m(perspective_id, relation_id)",
                perspective, relation,
            )  # fmt: skip
            self.assertEqual(row["k"], rs.difference_key(perspective, relation))

    async def test_a_rejected_connection_is_never_shown(self) -> None:
        await self.difference("false")
        await self.sql(
            "UPDATE memory_relations SET status = 'rejected' WHERE guild_id = %s AND id = %s",
            GUILD, self.ally,
        )  # fmt: skip
        got = await self.read()
        self.assertEqual(got["differences"]["false"], [])

    async def test_a_waiting_guess_is_shown_only_for_an_item_marked_as_a_guess(self) -> None:
        await self.difference("false")
        await self.sql(
            "UPDATE memory_relations SET status = 'proposed', secret = true, source = 'extractor'"
            " WHERE guild_id = %s AND id = %s",
            GUILD, self.ally,
        )  # fmt: skip
        self.assertEqual((await self.read())["differences"]["false"], [])  # not marked: hidden
        await self.sql(
            "UPDATE memory_differences SET involves_guess = true WHERE guild_id = %s", GUILD
        )
        self.assertEqual(len((await self.read())["differences"]["false"]), 1)

    async def test_a_run_that_has_not_moved_for_a_long_time_is_stalled_and_a_failed_one_has_a_code(
        self,
    ) -> None:
        await self.sql(
            "INSERT INTO memory_report_runs (guild_id, campaign_id, session_at, state, stage,"
            " started_at, updated_at) VALUES (%s, %s, %s, 'making', 'reading', 1, 2)",
            GUILD, self.one.id, S1,
        )  # fmt: skip
        got = await self.read()
        self.assertEqual((got["progress"]["state"], got["progress"]["done"]), ("stalled", None))
        await self.sql(
            "UPDATE memory_report_runs SET state = 'failed', reason = 'out_of_funds'"
            " WHERE guild_id = %s",
            GUILD,
        )
        got = await self.read()
        self.assertEqual((got["state"], got["progress"]["state"]), ("failed", "failed"))
        self.assertEqual(got["progress"]["reason"], "out_of_funds")

    async def test_a_report_the_call_limit_stopped_is_ready_and_says_so(self) -> None:
        await self.difference("misunderstood")
        await self.difference("not_sorted", relation=self.enemy)  # (never asked about)
        await self.sql(
            "INSERT INTO memory_report_runs (guild_id, campaign_id, session_at, state, stage,"
            " started_at, updated_at, reason) VALUES (%s, %s, %s, 'ready', 'sorting', 1, 2,"
            " 'left_for_next_time')",
            GUILD, self.one.id, S1,
        )  # fmt: skip
        got = await self.read()
        self.assertEqual(
            (got["state"], got["progress"], got["leftForNextTime"]), ("ready", None, True)
        )

    async def test_a_player_and_another_campaigns_dm_are_refused(self) -> None:
        await self.contradiction()
        bob = await self.signed(BOB)
        self.assertEqual(await self.refused(who=bob), "not_allowed")  # BOB DMs only campaign two
        self.assertEqual(await self.refused(ref=f"{GUILD}.{'0' * 32}"), "not_allowed")
        for bad in (0, -1, 10**12, True):
            self.assertEqual(await self.refused(bad), "not_found")

    async def test_the_door_answers_nothing_to_anyone_who_is_not_a_dm_even_by_sql(self) -> None:
        await self.contradiction()
        bob = await self.signed(BOB)
        async with self.db.user(bob.user_id, session=bob.id_hash) as conn:
            await conn.execute("SELECT set_config('dmbot.guild_id', %s, true)", (str(GUILD),))
            cur = await conn.execute(
                "SELECT dmbot_web_report(%s, %s, %s) AS r", (GUILD, self.one.id, S1)
            )
            self.assertIsNone((await cur.fetchone() or {}).get("r"))

    async def test_another_campaigns_items_never_appear(self) -> None:
        other_a, other_b = await self.pair(self.two.id)
        made = await self.memory.add_relation(
            GUILD, self.two.id, other_a, "ally_of", other_b,
            source=DM_SOURCE, confidence=1.0, status="confirmed",
        )  # fmt: skip
        await self.memory.add_relation(
            GUILD, self.two.id, other_a, "enemy_of", other_b,
            source=DM_SOURCE, confidence=1.0, status="confirmed",
        )  # fmt: skip
        party_two = await self.perspective("party", self.two.id)
        await self.sql(
            "INSERT INTO memory_differences (guild_id, campaign_id, perspective_id, relation_id,"
            " truth_relation_id, kind, sort, reason, found_at, session_started_at)"
            " VALUES (%s, %s, %s, %s, NULL, 'denied', 'false', 'Secret of two.', 1, %s)",
            GUILD, self.two.id, party_two, str(made.value[0].id), S1,
        )  # fmt: skip
        got = await self.read()
        self.assertEqual(got["differences"]["false"], [])
        self.assertNotIn("Cass", json.dumps(got))
        self.assertNotIn("Secret of two", json.dumps(got))

    async def signed(self, user: Any) -> Any:
        token = await sessions.sign_in(self.web, user, [THURSDAY], now=NOW, days=30)
        found = await sessions.find(self.web, token, now=NOW)
        assert found is not None
        return found


class TheAnswers(Reading):
    async def put(self, key: str, action: str, at: int = S1, who: Any = None) -> dict[str, Any]:
        return await report.answer_item(
            self.web, who or self.session, self.cref, at, {"id": key, "action": action}
        )

    async def refused_answer(self, key: str, action: str, at: int = S1, who: Any = None) -> str:
        with self.assertRaises(RecapError) as err:
            await self.put(key, action, at, who)
        return err.exception.code

    async def stored(self, key: str) -> Any:
        return await self.one_row(
            "SELECT * FROM report_answers WHERE guild_id = %s AND item_key = %s", GUILD, key
        )

    async def test_an_answer_is_kept_at_once_and_the_report_shows_it(self) -> None:
        key = await self.contradiction()
        for action in ("intended", "not_a_problem"):
            await self.sql("DELETE FROM report_answers WHERE guild_id = %s", GUILD)
            got = await self.put(key, action)
            self.assertEqual(got["answer"]["action"], action)
            row = await self.stored(key)
            self.assertEqual(
                (row["answer"], row["item_kind"], row["answered_by"], row["remind_from"]),
                (action, "contradiction", ALICE.id, None),
            )
            shown = (await self.read())["contradictions"][0]["answer"]
            self.assertEqual(shown["action"], action)

    async def test_a_difference_is_answered_as_a_difference(self) -> None:
        key = await self.difference()
        await self.put(key, "intended")
        self.assertEqual((await self.stored(key))["item_kind"], "difference")

    async def test_remind_me_keeps_where_it_was_asked_and_can_be_answered_again_later(self) -> None:
        key = await self.difference(at=S1)
        await self.put(key, "remind")
        self.assertEqual((await self.stored(key))["remind_from"], S1)
        # it comes back in the next session's report, where it can get a final answer
        await self.put(key, "intended", at=S2)
        row = await self.stored(key)
        self.assertEqual((row["answer"], row["remind_from"]), ("intended", None))

    async def test_undo_puts_the_item_back_to_open_and_a_second_undo_is_gone(self) -> None:
        key = await self.contradiction()
        await self.put(key, "intended")
        self.assertEqual((await self.put(key, "undo"))["answer"], None)
        self.assertIsNone(await self.stored(key))
        self.assertIsNone((await self.read())["contradictions"][0]["answer"])
        self.assertEqual(await self.refused_answer(key, "undo"), "gone")

    async def test_two_presses_at_once_cannot_both_win(self) -> None:
        import asyncio

        key = await self.contradiction()
        results = await asyncio.gather(
            self.put(key, "intended"), self.put(key, "not_a_problem"), return_exceptions=True
        )
        won = [r for r in results if isinstance(r, dict)]
        lost = [r for r in results if isinstance(r, RecapError)]
        self.assertEqual((len(won), len(lost)), (1, 1))
        self.assertEqual((await self.stored(key))["answer"], won[0]["answer"]["action"])

    async def test_remind_me_again_from_a_later_session_moves_it_on(self) -> None:
        key = await self.difference(at=S1)
        await self.put(key, "remind")
        await self.put(key, "remind", at=S2)  # (it came back in session 2 and is put off again)
        self.assertEqual((await self.stored(key))["remind_from"], S2)

    async def test_an_item_answered_before_the_session_is_gone_for_that_session(self) -> None:
        key = await self.contradiction(at=S2)
        await self.answer(key, "contradiction", "intended", when=S1 + 5)
        self.assertEqual(await self.refused_answer(key, "undo", at=S2), "gone")
        self.assertEqual(await self.refused_answer(key, "not_a_problem", at=S2), "gone")

    async def test_the_answer_door_accepts_what_the_read_door_lists(self) -> None:
        mine = await self.contradiction(at=S2)
        reminded = await self.difference(at=S1)
        await self.answer(reminded, "difference", "remind", when=S1 + 5, remind_from=S1)
        listed = {c["id"] for c in (await self.read(S2))["contradictions"]} | {
            d["id"] for v in (await self.read(S2))["differences"].values() for d in v
        }
        self.assertEqual(listed, {mine, reminded})
        for key in listed:  # every listed item can be answered in that session
            self.assertEqual(
                (await self.put(key, "intended", at=S2))["answer"]["action"], "intended"
            )

    async def test_a_second_final_answer_is_gone_and_changes_nothing(self) -> None:
        key = await self.contradiction()
        await self.put(key, "intended")
        self.assertEqual(await self.refused_answer(key, "not_a_problem"), "gone")
        self.assertEqual((await self.stored(key))["answer"], "intended")

    async def test_fix_writes_nothing(self) -> None:
        key = await self.difference()
        self.assertEqual((await self.put(key, "fix"))["answer"], None)
        self.assertIsNone(await self.stored(key))

    async def test_a_key_that_is_in_no_report_of_this_session_is_gone(self) -> None:
        key = await self.contradiction(at=S2)  # (found in session 2)
        self.assertEqual(await self.refused_answer(key, "intended", at=S1), "gone")
        self.assertEqual(await self.refused_answer("a" * 32, "intended"), "gone")
        self.assertEqual(await self.refused_answer("not a key", "intended"), "gone")
        self.assertIsNone(await self.stored(key))

    async def test_a_major_or_unsorted_item_cannot_be_answered(self) -> None:
        major = await self.contradiction("major")
        self.assertEqual(await self.refused_answer(major, "intended"), "gone")
        await self.sql("DELETE FROM memory_contradictions WHERE guild_id = %s", GUILD)
        unsorted = await self.difference("not_sorted")
        self.assertEqual(await self.refused_answer(unsorted, "intended"), "gone")

    async def test_another_campaigns_item_key_is_gone(self) -> None:
        other_a, other_b = await self.pair(self.two.id)
        rel = await self.memory.add_relation(
            GUILD, self.two.id, other_a, "ally_of", other_b,
            source=DM_SOURCE, confidence=1.0, status="confirmed",
        )  # fmt: skip
        party_two = await self.perspective("party", self.two.id)
        await self.sql(
            "INSERT INTO memory_differences (guild_id, campaign_id, perspective_id, relation_id,"
            " truth_relation_id, kind, sort, reason, found_at, session_started_at)"
            " VALUES (%s, %s, %s, %s, NULL, 'denied', 'false', 'x', 1, %s)",
            GUILD, self.two.id, party_two, str(rel.value[0].id), S1,
        )  # fmt: skip
        key = rs.difference_key(party_two, str(rel.value[0].id))
        self.assertEqual(await self.refused_answer(key, "intended"), "gone")
        self.assertIsNone(await self.stored(key))

    async def test_a_bad_action_is_refused_and_a_player_cannot_answer(self) -> None:
        key = await self.contradiction()
        self.assertEqual(await self.refused_answer(key, "delete"), "bad_action")
        bob = await self.signed(BOB)
        self.assertEqual(await self.refused_answer(key, "intended", who=bob), "not_allowed")
        self.assertIsNone(await self.stored(key))
        with self.assertRaises(RecapError):
            await report.answer_item(self.web, self.session, self.cref, S1, "intended")

    async def test_the_door_does_nothing_for_anyone_who_is_not_a_dm_even_by_sql(self) -> None:
        key = await self.contradiction()
        bob = await self.signed(BOB)
        async with self.db.user(bob.user_id, session=bob.id_hash) as conn:
            await conn.execute("SELECT set_config('dmbot.guild_id', %s, true)", (str(GUILD),))
            cur = await conn.execute(
                "SELECT dmbot_web_report_answer(%s, %s, %s, %s, 'intended') AS r",
                (GUILD, self.one.id, S1, key),
            )
            self.assertIsNone((await cur.fetchone() or {}).get("r"))
        self.assertIsNone(await self.stored(key))

    async def signed(self, user: Any) -> Any:
        token = await sessions.sign_in(self.web, user, [THURSDAY], now=NOW, days=30)
        found = await sessions.find(self.web, token, now=NOW)
        assert found is not None
        return found


class TheRoute(Reading):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.app = create_app(
            settings(), self.web, FakeDiscord(), payments=FakeProvider(b"h" * 32, "https://s.example"),
            clock=lambda: NOW,
        )  # fmt: skip
        self.client = await self.client_for(ALICE)

    async def client_for(self, user: Any) -> httpx.AsyncClient:
        token = await sessions.sign_in(self.web, user, [THURSDAY], now=NOW, days=30)
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app),
            base_url="https://web-api.internal.test",
            follow_redirects=False,
            headers={"X-DMbot-Request": "1"},  # (the cross-site check every write passes)
        )
        client.cookies.set("__Host-dmbot_session", token, domain="web-api.internal.test")
        self.addAsyncCleanup(client.aclose)
        return client

    async def test_answering_over_http(self) -> None:
        key = await self.difference()
        url = f"/me/campaigns/{self.cref}/sessions/{S1}/report"
        got = await self.client.post(url, json={"id": key, "action": "not_a_problem"})
        self.assertEqual(got.status_code, 200)
        self.assertEqual(got.json()["answer"]["action"], "not_a_problem")
        again = await self.client.post(url, json={"id": key, "action": "intended"})
        self.assertEqual((again.status_code, again.json()), (409, {"error": "gone"}))
        bad = await self.client.post(url, json={"id": key, "action": "x"})
        self.assertEqual((bad.status_code, bad.json()), (400, {"error": "bad_action"}))
        undone = await self.client.post(url, json={"id": key, "action": "undo"})
        self.assertEqual((undone.status_code, undone.json()), (200, {"answer": None}))
        bob = await self.client_for(BOB)
        refused = await bob.post(url, json={"id": key, "action": "intended"})
        self.assertEqual(refused.status_code, 403)
        nothing = await self.client.post(url, content=b"not json")
        self.assertIn(nothing.status_code, (400, 422))

    async def test_the_report_over_http(self) -> None:
        await self.difference("disguised")
        got = await self.client.get(f"/me/campaigns/{self.cref}/sessions/{S1}/report")
        self.assertEqual(got.status_code, 200)
        self.assertEqual(len(got.json()["differences"]["disguised"]), 1)

    async def test_refusals_over_http(self) -> None:
        url = f"/me/campaigns/{self.cref}/sessions"
        for bad in ("abc", "0", "-5", "1.5", "9" * 40, "²"):
            got = await self.client.get(f"{url}/{bad}/report")
            self.assertEqual((got.status_code, got.json()), (404, {"error": "not_found"}), bad)
        bob = await self.client_for(BOB)
        got = await bob.get(f"{url}/{S1}/report")
        self.assertEqual((got.status_code, got.json()), (403, {"error": "not_allowed"}))
        other = await self.client.get(f"/me/campaigns/{GUILD}.{'0' * 32}/sessions/{S1}/report")
        self.assertEqual(other.status_code, 403)


class TheRequest(Reading):
    """ "Make it again" (#2661): queued for the bot by a DM; the page sees the report being made, at
    the stage `waiting`, until the bot starts its run (which then speaks for itself)."""

    async def ask(self, at: int = S1, who: Any = None, ref: str | None = None) -> dict[str, Any]:
        return await report.make_again(self.web, who or self.session, ref or self.cref, at)

    async def refused_ask(self, at: int = S1, who: Any = None, ref: str | None = None) -> str:
        with self.assertRaises(RecapError) as err:
            await self.ask(at, who, ref)
        return err.exception.code

    async def rows(self) -> list[Any]:
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                "SELECT session_at, state, user_id, error FROM web_report_requests ORDER BY seq"
            )
            return list(await cur.fetchall())

    async def test_a_request_is_queued_and_the_report_reads_as_being_made_at_waiting(self) -> None:
        got = await self.ask()
        self.assertEqual(got["state"], "making")
        p = got["progress"]
        self.assertEqual(
            (p["stage"], p["state"], p["reason"], p["done"]), ("waiting", "running", None, None)
        )
        rows = await self.rows()
        self.assertEqual(
            [(r["session_at"], r["state"], r["user_id"]) for r in rows], [(S1, "waiting", ALICE.id)]
        )
        self.assertEqual((await self.read())["state"], "making")  # and a later look agrees

    async def test_a_second_press_queues_nothing_more(self) -> None:
        await self.ask()
        await self.ask()
        self.assertEqual(len(await self.rows()), 1)

    async def test_the_bots_own_run_speaks_for_itself_once_it_starts(self) -> None:
        await self.ask()
        now = int((await self.one_row("SELECT extract(epoch FROM now())::BIGINT AS n"))["n"])
        await self.sql(
            "INSERT INTO memory_report_runs (guild_id, campaign_id, session_at, state, stage,"
            " done, total, started_at, updated_at)"
            " VALUES (%s, %s, %s, 'making', 'grading', 3, 10, %s, %s)",
            GUILD, self.one.id, S1, now - 20, now - 2,
        )  # fmt: skip
        p = (await self.read())["progress"]
        self.assertEqual((p["stage"], p["done"], p["total"]), ("grading", 3, 10))

    async def test_a_report_being_made_right_now_is_not_queued_again(self) -> None:
        now = int((await self.one_row("SELECT extract(epoch FROM now())::BIGINT AS n"))["n"])
        await self.sql(
            "INSERT INTO memory_report_runs (guild_id, campaign_id, session_at, state, stage,"
            " done, total, started_at, updated_at)"
            " VALUES (%s, %s, %s, 'making', 'reading', 0, 0, %s, %s)",
            GUILD, self.one.id, S1, now - 20, now - 2,
        )  # fmt: skip
        got = await self.ask()
        self.assertEqual(got["state"], "making")
        self.assertEqual(await self.rows(), [])

    async def test_a_stalled_run_can_be_asked_for_again(self) -> None:
        now = int((await self.one_row("SELECT extract(epoch FROM now())::BIGINT AS n"))["n"])
        await self.sql(
            "INSERT INTO memory_report_runs (guild_id, campaign_id, session_at, state, stage,"
            " done, total, started_at, updated_at)"
            " VALUES (%s, %s, %s, 'making', 'grading', 1, 10, %s, %s)",
            GUILD, self.one.id, S1, now - 5000, now - 4000,
        )  # fmt: skip
        await self.ask()
        self.assertEqual(len(await self.rows()), 1)

    async def test_a_failed_request_with_no_report_reads_as_failed(self) -> None:
        await self.ask()
        await self.sql("UPDATE web_report_requests SET state = 'failed', error = 'unavailable'")
        got = await self.read()
        self.assertEqual(
            (got["state"], got["progress"]["state"], got["progress"]["reason"]),
            ("failed", "failed", "failed"),
        )

    async def test_a_failed_request_leaves_an_existing_report_as_it_was(self) -> None:
        await self.difference("disguised")
        await self.ask()
        await self.sql("UPDATE web_report_requests SET state = 'failed', error = 'unavailable'")
        self.assertEqual((await self.read())["state"], "ready")

    async def test_a_done_request_is_not_shown_as_making(self) -> None:
        await self.ask()
        await self.sql("UPDATE web_report_requests SET state = 'done'")
        self.assertEqual((await self.read())["state"], "none")

    async def test_a_request_the_bot_took_and_went_quiet_on_goes_back_to_waiting(self) -> None:
        await self.ask()
        await self.sql(
            "UPDATE web_report_requests SET state = 'working', claimed_at = 5,"
            " updated_at = extract(epoch FROM now())::BIGINT - 700"
        )
        await self.ask(S2)  # the next press tidies the queue
        rows = {r["session_at"]: r for r in await self.rows()}
        self.assertEqual(rows[S1]["state"], "waiting")

    async def test_five_requests_an_hour_for_a_campaign_and_no_more(self) -> None:
        await self.sql(
            "INSERT INTO web_report_requests (guild_id, campaign_id, id, user_id, session_at,"
            " state, created_at, updated_at) SELECT %s, %s, md5(g::text), %s, 9000 + g, 'done',"
            " extract(epoch FROM now())::BIGINT, extract(epoch FROM now())::BIGINT"
            " FROM generate_series(1, 5) g",
            GUILD, self.one.id, ALICE.id,
        )  # fmt: skip
        self.assertEqual(await self.refused_ask(S1), "too_soon")
        self.assertEqual(len(await self.rows()), 5)

    async def test_a_session_that_is_not_the_campaigns_is_not_found(self) -> None:
        self.assertEqual(await self.refused_ask(S1 + 7), "not_found")
        for bad in (0, -1, 10**12, True):
            self.assertEqual(await self.refused_ask(bad), "not_found")
        self.assertEqual(await self.rows(), [])

    async def test_a_player_and_another_campaigns_dm_are_refused(self) -> None:
        bob = await self.signed(BOB)
        self.assertEqual(await self.refused_ask(who=bob), "not_allowed")
        self.assertEqual(await self.refused_ask(ref=f"{GUILD}.{'0' * 32}"), "not_allowed")
        self.assertEqual(await self.rows(), [])

    async def test_the_doors_answer_nothing_to_anyone_who_is_not_a_dm_even_by_sql(self) -> None:
        await self.ask()
        bob = await self.signed(BOB)
        async with self.db.user(bob.user_id, session=bob.id_hash) as conn:
            await conn.execute("SELECT set_config('dmbot.guild_id', %s, true)", (str(GUILD),))
            for door in ("dmbot_web_report_make", "dmbot_web_report_request"):
                cur = await conn.execute(
                    f"SELECT {door}(%s, %s, %s) AS r", (GUILD, self.one.id, S1)
                )
                self.assertIsNone((await cur.fetchone() or {}).get("r"), door)
        self.assertEqual(len(await self.rows()), 1)

    async def test_the_website_role_cannot_touch_the_table_itself(self) -> None:
        async with self.db.guild(GUILD) as conn:
            for right in ("SELECT", "INSERT", "UPDATE", "DELETE"):
                cur = await conn.execute(
                    "SELECT has_table_privilege('dmbot_web', 'web_report_requests', %s) AS can",
                    (right,),
                )
                row = await cur.fetchone()
                assert row is not None
                self.assertFalse(row["can"], right)

    async def signed(self, user: Any) -> Any:
        token = await sessions.sign_in(self.web, user, [THURSDAY], now=NOW, days=30)
        found = await sessions.find(self.web, token, now=NOW)
        assert found is not None
        return found


class TheMakeRoute(TheRoute):
    async def test_make_again_over_http(self) -> None:
        url = f"/me/campaigns/{self.cref}/sessions/{S1}/report/make"
        got = await self.client.post(url)
        self.assertEqual(got.status_code, 202)
        body = got.json()
        self.assertEqual((body["state"], body["progress"]["stage"]), ("making", "waiting"))
        again = await self.client.post(url)
        self.assertEqual((again.status_code, again.json()["state"]), (202, "making"))

    async def test_refusals_over_http_for_make_again(self) -> None:
        base = f"/me/campaigns/{self.cref}/sessions"
        for bad in ("abc", "0", "-5", "9" * 40, str(S1 + 7)):
            got = await self.client.post(f"{base}/{bad}/report/make")
            self.assertEqual((got.status_code, got.json()), (404, {"error": "not_found"}), bad)
        bob = await self.client_for(BOB)
        got = await bob.post(f"{base}/{S1}/report/make")
        self.assertEqual((got.status_code, got.json()), (403, {"error": "not_allowed"}))
        anonymous = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app),
            base_url="https://web-api.internal.test",
            headers={"X-DMbot-Request": "1"},
        )
        self.addAsyncCleanup(anonymous.aclose)
        self.assertEqual((await anonymous.post(f"{base}/{S1}/report/make")).status_code, 401)
