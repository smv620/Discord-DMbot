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
        )
        client.cookies.set("__Host-dmbot_session", token, domain="web-api.internal.test")
        self.addAsyncCleanup(client.aclose)
        return client

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
