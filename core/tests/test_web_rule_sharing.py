"""A campaign's shared and followed house rules for the House rules page (#2783): read only, DM
only, names and counts and never a rule's words or a share code, the sets of other campaigns in the
same server only, and the plan answered as a yes or no. Invented names throughout."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

from dmbot.web import rule_sharing, sessions
from dmbot.web.discord import DiscordGuild
from dmbot.web.recaps import RecapError
from tests.test_recap_web import BOB, NOW, Base
from tests.test_web_api import ALICE, THURSDAY

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
