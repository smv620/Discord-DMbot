"""A campaign's shared and followed house rules for the House rules page (#2783): read only, DM
only, names and counts and never a rule's words or a share code, the sets of other campaigns in the
same server only, and the plan answered as a yes or no. Invented names throughout."""

from __future__ import annotations

import json
import time
from types import SimpleNamespace
from typing import Any

import httpx
import psycopg

from dmbot.web import rule_sharing, sessions
from dmbot.web.app import create_app
from dmbot.web.discord import DiscordGuild
from dmbot.web.recaps import RecapError
from tests.test_recap_web import BOB, HEADERS, NOW, Base
from tests.test_web_api import ALICE, THURSDAY, FakeDiscord, settings

CARL = SimpleNamespace(id=3003, name="Carl", email="carl@example.com")  # plays; runs nothing
SET_A = "a" * 32
SET_B = "b" * 32
SECRET_CODE = "ABCDEFGHJK"
WORDS = "Natural 20s always hit"  # a rule's words: must never leave the database in this read
OTHER = DiscordGuild(id=222, name="Other Table", manage=True)


class SharingBase(Base):
    async def sql(self, statement: str, *args: Any, guild_id: int = THURSDAY.id) -> None:
        async with self.db.guild(guild_id) as conn:
            await conn.execute(statement, args)

    async def publish(
        self, campaign: str, set_id: str, name: str, *, status: str = "shared", rules: int = 2,
        guild_id: int = THURSDAY.id,
    ) -> None:  # fmt: skip
        await self.sql(
            "INSERT INTO rule_sets (guild_id, campaign_id, id, kind, name, status, created_by,"
            " created_at) VALUES (%s, %s, %s, 'house_rules', %s, %s, %s, 5)",
            guild_id, campaign, set_id, name, status, ALICE.id, guild_id=guild_id,
        )  # fmt: skip
        for version in (1, 2):
            body = {"rules": [{"number": n, "rule": WORDS, "instead": None} for n in range(rules)]}
            await self.sql(
                "INSERT INTO rule_set_versions (guild_id, campaign_id, set_id, version, content,"
                " created_by, created_at) VALUES (%s, %s, %s, %s, %s::jsonb, %s, 5)",
                guild_id, campaign, set_id, version, json.dumps(body), ALICE.id, guild_id=guild_id,
            )  # fmt: skip

    async def code(self, campaign: str, set_id: str, *, active: bool = True) -> None:
        await self.sql(
            "INSERT INTO rule_set_codes (guild_id, campaign_id, code, set_id, active, created_at)"
            " VALUES (%s, %s, %s, %s, %s, 5)",
            THURSDAY.id, campaign, SECRET_CODE, set_id, active,
        )  # fmt: skip

    async def follow(self, campaign: str, set_id: str, name: str, *, updating: bool = True) -> None:
        await self.sql(
            "INSERT INTO rule_set_follows (guild_id, campaign_id, set_id, kind, set_name,"
            " publisher, version, content, updating, followed_by, followed_at)"
            " VALUES (%s, %s, %s, 'house_rules', %s, 'Their Table', 3, '{}'::jsonb, %s, %s, 5)",
            THURSDAY.id, campaign, set_id, name, updating, ALICE.id,
        )  # fmt: skip

    async def house_rule(self, campaign: str, number: int, *, from_set: str | None = None) -> None:
        await self.sql(
            "INSERT INTO house_rules (guild_id, campaign_id, number, rule, created_by, created_at,"
            " updated_at, from_set) VALUES (%s, %s, %s, %s, %s, 5, 5, %s)",
            THURSDAY.id, campaign, number, f"Rule {number}", ALICE.id, from_set,
        )  # fmt: skip

    async def signed(self, user: Any, guilds: Any = None) -> Any:
        token = await sessions.sign_in(
            self.web, user, guilds if guilds is not None else [THURSDAY], now=NOW, days=30
        )
        found = await sessions.find(self.web, token, now=NOW)
        assert found is not None
        return found

    def cref(self, campaign: Any = None) -> str:
        return f"{THURSDAY.id}.{(campaign or self.one).id}"

    async def read(self, session: Any = None, ref: str | None = None, **kw: Any) -> dict[str, Any]:
        return await rule_sharing.read_sharing(
            self.web, session or self.session, ref or self.cref(),
            now=NOW, enforce_plans=kw.pop("enforce_plans", False),
        )  # fmt: skip

    async def refused(self, session: Any, ref: str) -> str:
        with self.assertRaises(RecapError) as caught:
            await self.read(session, ref)
        return str(caught.exception.code)


class TheRead(SharingBase):
    async def test_a_campaign_with_nothing_shared_reads_empty(self) -> None:
        got = await self.read()
        self.assertEqual(
            (got["published"], got["following"], got["here"], got["ruleCount"]), (None, [], [], 0)
        )

    async def test_what_it_publishes_follows_and_how_many_rules_it_has(self) -> None:
        await self.publish(self.one.id, SET_A, "Our Rules")
        await self.code(self.one.id, SET_A)
        await self.follow(self.one.id, SET_B, "Frost Rules", updating=False)
        await self.house_rule(self.one.id, 1)
        await self.house_rule(self.one.id, 2)
        await self.house_rule(
            self.one.id, 3, from_set=SET_B
        )  # came from a followed set: not its own
        got = await self.read()
        self.assertEqual(
            got["published"], {"name": "Our Rules", "shared": True, "version": 2, "hasCode": True}
        )
        self.assertEqual(
            got["following"],
            [{"name": "Frost Rules", "publisher": "Their Table", "version": 3, "updating": False}],
        )
        self.assertEqual(got["ruleCount"], 2)

    async def test_a_stopped_set_is_still_the_campaigns_own_but_not_shared(self) -> None:
        await self.publish(self.one.id, SET_A, "Old Rules", status="unshared")
        got = await self.read()
        self.assertEqual((got["published"] or {}).get("shared"), False)

    async def test_the_sets_other_campaigns_share_here_are_listed_by_name_and_count_only(
        self,
    ) -> None:
        await self.publish(self.two.id, SET_B, "Bob's Rules", rules=3)
        await self.code(self.two.id, SET_B)
        got = await self.read()
        self.assertEqual(
            got["here"],
            [{"setId": SET_B, "name": "Bob's Rules", "publisher": "Two", "version": 2, "rules": 3}],
        )
        text = json.dumps(got)
        self.assertNotIn(WORDS, text)  # none of the rules' words
        self.assertNotIn(SECRET_CODE, text)  # no share code, whoever's

    async def test_its_own_set_and_stopped_sets_and_other_servers_are_not_offered(self) -> None:
        await self.publish(self.one.id, SET_A, "Mine")
        await self.publish(self.two.id, SET_B, "Stopped", status="unshared")
        other = (await self.campaigns.create(OTHER.id, "Elsewhere", ALICE.id)).id
        await self.publish(other, "c" * 32, "Far Away", guild_id=OTHER.id)
        self.assertEqual((await self.read())["here"], [])

    async def test_a_share_code_is_never_in_the_answer(self) -> None:
        await self.publish(self.one.id, SET_A, "Mine")
        await self.code(self.one.id, SET_A)
        got = await self.read()
        self.assertTrue(got["published"]["hasCode"])
        self.assertNotIn(SECRET_CODE, json.dumps(got))

    async def test_the_owner_is_not_in_the_answer_only_whether_the_plan_allows_it(self) -> None:
        got = await self.read()
        self.assertEqual(set(got), {"published", "following", "here", "ruleCount", "canShare"})
        self.assertTrue(got["canShare"])  # (plans are not enforced in this test)

    async def test_without_a_paid_plan_the_page_is_told_no(self) -> None:
        got = await self.read(enforce_plans=True)  # the owner has no plan here
        self.assertFalse(got["canShare"])


class TheRefusals(SharingBase):
    async def test_a_player_a_manager_who_is_not_a_dm_and_another_campaigns_dm_are_refused(
        self,
    ) -> None:
        player = await self.signed(CARL, [DiscordGuild(THURSDAY.id, THURSDAY.name, False)])
        manager = await self.signed(CARL)
        bob = await self.signed(BOB)
        for who in (player, manager, bob):
            self.assertEqual(await self.refused(who, self.cref()), "not_allowed")
        self.assertEqual(await self.refused(self.session, self.cref(self.two)), "not_allowed")

    async def test_a_server_the_session_does_not_have_is_not_found(self) -> None:
        self.assertEqual(await self.refused(self.session, f"999.{self.one.id}"), "not_found")
        self.assertEqual(await self.refused(self.session, "nonsense"), "not_found")

    async def test_the_website_role_cannot_read_the_rule_tables_itself(self) -> None:
        async with self.db.guild(THURSDAY.id) as conn:
            for table in ("rule_sets", "rule_set_versions", "rule_set_codes", "rule_set_follows"):
                cur = await conn.execute(
                    "SELECT has_table_privilege('dmbot_web', %s, 'SELECT') AS can", (table,)
                )
                row = await cur.fetchone()
                assert row is not None
                self.assertFalse(row["can"], table)


CODE = "ABCDEFGHJK"  # the shape of a share code (invented)


class TheQueue(SharingBase):
    """What a DM asks for on the page is queued for the bot (#2783), after the checks the door can
    make; the bot applies it and checks again, so these prove only who may ask and what is kept."""

    async def ask(
        self, action: str, arg: str = "", session: Any = None, ref: str | None = None, **kw: Any
    ) -> str:
        return await rule_sharing.queue_action(
            self.web, session or self.session, ref or self.cref(), {"action": action, "arg": arg},
            now=NOW, enforce_plans=kw.pop("enforce_plans", False),
        )  # fmt: skip

    async def refused_ask(self, *args: Any, **kw: Any) -> str:
        with self.assertRaises(RecapError) as caught:
            await self.ask(*args, **kw)
        return str(caught.exception.code)

    async def rows(self) -> list[dict[str, Any]]:
        async with self.db.guild(THURSDAY.id) as conn:
            cur = await conn.execute(
                "SELECT action, arg, state, stage, user_id FROM web_rule_actions ORDER BY seq"
            )
            return [dict(r) for r in await cur.fetchall()]

    async def test_each_choice_is_queued_waiting_for_the_bot(self) -> None:
        await self.house_rule(self.one.id, 1)
        await self.ask("publish", "  Our Rules ")
        await self.ask("stop_sharing")
        await self.ask("look_code", "abcde-fghjk")  # typed as a person types it
        await self.ask("follow_code", CODE)
        await self.ask("follow_here", SET_B)
        await self.ask("unfollow")
        got = await self.rows()
        self.assertEqual(
            [(r["action"], r["arg"]) for r in got],
            [
                ("publish", "Our Rules"),
                ("stop_sharing", ""),
                ("look_code", CODE),
                ("follow_code", CODE),
                ("follow_here", SET_B),
                ("unfollow", ""),
            ],
        )
        self.assertTrue(all((r["state"], r["stage"]) == ("waiting", "waiting") for r in got))
        self.assertTrue(all(r["user_id"] == ALICE.id for r in got))

    async def test_a_second_press_gets_the_open_choice_back_and_adds_none(self) -> None:
        first = await self.ask("unfollow")
        self.assertEqual(await self.ask("unfollow"), first)
        self.assertEqual(len(await self.rows()), 1)
        self.assertNotEqual(await self.ask("look_code", CODE), first)  # another choice is new

    async def test_a_player_a_manager_who_is_not_a_dm_and_another_campaigns_dm_are_refused(
        self,
    ) -> None:
        player = await self.signed(CARL, [DiscordGuild(THURSDAY.id, THURSDAY.name, False)])
        manager = await self.signed(CARL)
        bob = await self.signed(BOB)
        for who in (player, manager, bob):
            self.assertEqual(await self.refused_ask("unfollow", session=who), "not_allowed")
        self.assertEqual(await self.refused_ask("unfollow", ref=self.cref(self.two)), "not_allowed")
        self.assertEqual(await self.rows(), [])

    async def test_a_campaign_whose_plan_does_not_allow_sharing_cannot_publish(self) -> None:
        await self.house_rule(self.one.id, 1)
        self.assertEqual(await self.refused_ask("publish", "Rules", enforce_plans=True), "plan")
        self.assertEqual(await self.rows(), [])
        await self.ask("unfollow", enforce_plans=True)  # following is free, as in Discord

    async def test_publishing_needs_a_house_rule_of_its_own(self) -> None:
        self.assertEqual(await self.refused_ask("publish", "Rules"), "no_rules")
        await self.house_rule(self.one.id, 1, from_set=SET_B)  # a followed rule is not its own
        self.assertEqual(await self.refused_ask("publish", "Rules"), "no_rules")

    async def test_the_shape_of_what_is_asked_is_checked(self) -> None:
        await self.house_rule(self.one.id, 1)
        for action, arg, code in (
            ("share_code", "", "bad_action"),
            ("", "", "bad_action"),
            ("publish", "", "bad_arg"),
            ("publish", "x" * 61, "bad_arg"),
            ("look_code", "SHORT", "bad_arg"),
            ("follow_code", "ABCDEFGHJ1", "bad_arg"),  # 1 is not a letter of a code
            ("follow_here", "not-an-id", "bad_arg"),
            ("follow_here", "", "bad_arg"),
        ):
            self.assertEqual(await self.refused_ask(action, arg), code, (action, arg))
        bodies: list[object] = [
            None,
            [],
            "publish",
            {"arg": "x"},
            {"action": 5},
            {"action": "publish", "arg": 5},
        ]
        for body in bodies:
            with self.assertRaises(RecapError):
                await rule_sharing.queue_action(
                    self.web, self.session, self.cref(), body, now=NOW, enforce_plans=False
                )
        self.assertEqual(await self.rows(), [])

    async def test_a_campaign_has_ten_open_choices_at_most(self) -> None:
        for n in range(10):
            await self.ask("follow_here", f"{n:032x}")
        self.assertEqual(await self.refused_ask("follow_here", "f" * 32), "too_soon")
        self.assertEqual(len(await self.rows()), 10)

    async def test_a_server_the_session_does_not_have_is_not_found(self) -> None:
        self.assertEqual(await self.refused_ask("unfollow", ref=f"999.{self.one.id}"), "not_found")

    async def test_the_website_role_cannot_touch_the_queue_table_itself(self) -> None:
        async with self.db.guild(THURSDAY.id) as conn:
            for right in ("SELECT", "INSERT", "UPDATE", "DELETE"):
                cur = await conn.execute(
                    "SELECT has_table_privilege('dmbot_web', 'web_rule_actions', %s) AS can",
                    (right,),
                )
                row = await cur.fetchone()
                assert row is not None
                self.assertFalse(row["can"], right)


class TheAnswer(TheQueue):
    async def answer(self, answer: str, session: Any = None, ref: str | None = None) -> Any:
        return await rule_sharing.read_action(
            self.web, session or self.session, ref or self.cref(), answer, now=NOW
        )

    async def test_a_waiting_choice_reads_in_the_shared_progress_shape(self) -> None:
        got = await self.answer(await self.ask("unfollow"))
        self.assertEqual(
            (got["state"], got["stage"], got["action"]), ("running", "waiting", "unfollow")
        )
        self.assertEqual((got["error"], got["note"]), ("", ""))

    async def test_what_the_bot_says_comes_back_in_its_words(self) -> None:
        answer = await self.ask("look_code", CODE)
        await self.sql(
            "UPDATE web_rule_actions SET state = 'done', stage = 'done', note = %s WHERE id = %s",
            "Frost Rules, from Their Table: 4 rules.", answer,
        )  # fmt: skip
        got = await self.answer(answer)
        self.assertEqual(got["state"], "done")
        self.assertEqual(got["note"], "Frost Rules, from Their Table: 4 rules.")

    async def test_a_failure_says_so_and_why(self) -> None:
        answer = await self.ask("unfollow")
        await self.sql(
            "UPDATE web_rule_actions SET state = 'failed', stage = 'failed', error = 'failed',"
            " note = 'Not following.' WHERE id = %s", answer,
        )  # fmt: skip
        got = await self.answer(answer)
        self.assertEqual(
            (got["state"], got["reason"], got["note"]), ("failed", "failed", "Not following.")
        )

    async def test_a_choice_the_bot_stopped_on_shows_as_stalled(self) -> None:
        answer = await self.ask("unfollow")
        await self.sql("UPDATE web_rule_actions SET state = 'working', stage = 'sharing'")
        later = int(time.time()) + 600  # past the five minutes a choice may go quiet
        got = await rule_sharing.read_action(self.web, self.session, self.cref(), answer, now=later)
        self.assertEqual((got["stage"], got["state"]), ("sharing", "stalled"))

    async def test_nobody_else_can_read_a_choice(self) -> None:
        answer = await self.ask("unfollow")
        bob = await self.signed(BOB)
        player = await self.signed(CARL, [DiscordGuild(THURSDAY.id, THURSDAY.name, False)])
        for who, ref in ((bob, self.cref()), (player, self.cref()), (bob, self.cref(self.two))):
            with self.assertRaises(RecapError) as caught:
                await self.answer(answer, session=who, ref=ref)
            self.assertIn(caught.exception.code, ("not_found", "not_allowed"))
        with self.assertRaises(RecapError):
            await self.answer("nonsense")
        with self.assertRaises(RecapError):
            await self.answer("0" * 32)

    async def test_the_state_and_the_stage_cannot_disagree(self) -> None:
        answer = await self.ask("unfollow")
        with self.assertRaises(psycopg.errors.CheckViolation):
            await self.sql(
                "UPDATE web_rule_actions SET state = 'done', stage = 'waiting' WHERE id = %s",
                answer,
            )


class TheRoutes(SharingBase):
    async def start(self, **changes: Any) -> None:
        self.app = create_app(settings(**changes), self.web, FakeDiscord(), clock=lambda: NOW)
        token = await sessions.sign_in(self.web, ALICE, [THURSDAY], now=NOW, days=30)
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app),
            base_url="https://web-api.internal.test",
            follow_redirects=False,
        )
        self.client.cookies.set("__Host-dmbot_session", token, domain="web-api.internal.test")
        self.addAsyncCleanup(self.client.aclose)
        self.url = f"/me/campaigns/{self.cref()}/rule-sharing"

    async def test_off_by_default_nothing_is_queued_and_no_answer_is_read(self) -> None:
        await self.start()
        sent = await self.client.post(self.url, json={"action": "unfollow"}, headers=HEADERS)
        self.assertEqual((sent.status_code, sent.json()), (404, {"error": "not_found"}))
        gone = await self.client.get(f"{self.url}/answers/{'0' * 32}")
        self.assertEqual(gone.status_code, 404)
        async with self.db.guild(THURSDAY.id) as conn:
            cur = await conn.execute("SELECT count(*) AS n FROM web_rule_actions")
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual(row["n"], 0)

    async def test_post_then_poll_over_http_when_on(self) -> None:
        await self.start(rule_sharing_on=True)
        sent = await self.client.post(self.url, json={"action": "unfollow"}, headers=HEADERS)
        self.assertEqual(sent.status_code, 202)
        got = await self.client.get(f"{self.url}/answers/{sent.json()['answer']}")
        self.assertEqual((got.status_code, got.json()["stage"]), (200, "waiting"))

    async def test_refusals_over_http(self) -> None:
        await self.start(rule_sharing_on=True, enforce_plans=True)
        for body, status, word in (
            ({"action": "nope"}, 400, "bad_action"),
            ({"action": "follow_code", "arg": "SHORT"}, 400, "bad_arg"),
            ({"action": "publish", "arg": "Rules"}, 403, "plan"),
        ):
            got = await self.client.post(self.url, json=body, headers=HEADERS)
            self.assertEqual((got.status_code, got.json()), (status, {"error": word}), body)
        notjson = await self.client.post(self.url, content=b"x", headers=HEADERS)
        self.assertEqual(notjson.status_code, 400)
        anonymous = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app),
            base_url="https://web-api.internal.test",
        )
        self.addAsyncCleanup(anonymous.aclose)
        self.assertEqual(
            (await anonymous.post(self.url, json={}, headers=HEADERS)).status_code, 401
        )

    async def test_a_player_is_refused_over_http(self) -> None:
        await self.start(rule_sharing_on=True)
        carl: Any = CARL
        token = await sessions.sign_in(
            self.web, carl, [DiscordGuild(THURSDAY.id, THURSDAY.name, False)], now=NOW, days=30
        )
        self.client.cookies.set("__Host-dmbot_session", token, domain="web-api.internal.test")
        sent = await self.client.post(self.url, json={"action": "unfollow"}, headers=HEADERS)
        self.assertEqual((sent.status_code, sent.json()), (403, {"error": "not_allowed"}))
