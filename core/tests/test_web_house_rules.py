"""A campaign's house rules on the website (#1521), run as the website's own database role: anyone
in the server reads them (not only a DM) with no plan rule, a person outside it gets "not found",
the followed sets come after the campaign's own with the bot's citation, the download is the file
the bot's Download button sends byte for byte, and nothing private (scenario, who wrote a rule, a
set's publisher, the linked rules document) reaches the page."""

from __future__ import annotations

import json
from typing import Any

import httpx
import psycopg

from dmbot.rules import house_file
from dmbot.rules.house import HouseRuleStore
from dmbot.web import house_rules, sessions
from dmbot.web.app import create_app
from dmbot.web.payments import FakeProvider
from dmbot.web.recaps import RecapError
from tests.test_recap_web import BOB, HEADERS, NOW, Base, callers
from tests.test_web_api import ALICE, QUILLON, THURSDAY, FakeDiscord, settings

CAROL = type(BOB)(id=3003, name="Carol", email="carol@example.com")  # in the server, no DM at all
SECRET_SCENARIO = "The party burned the abbey, and Brother Hiro was the traitor"
PUBLISHER = "Publisher Campaign Name"


class HouseRulesBase(Base):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.cref = f"{THURSDAY.id}.{self.one.id}"
        self.oref = f"{THURSDAY.id}.{self.two.id}"
        self.store_h = HouseRuleStore(self.db)
        g, c = THURSDAY.id, self.one.id
        await self.store_h.add(g, c, ALICE.id, "Crits double the dice")
        await self.store_h.add(
            g, c, ALICE.id, "Potions are a bonus action", "Drinking a potion takes an action",
            scenario=SECRET_SCENARIO,
        )  # fmt: skip
        await self.store_h.add(g, c, ALICE.id, "Rests are 6 hours (instead of: 8)")
        await self.store_h.add(THURSDAY.id, self.two.id, BOB.id, "Another table's rule")
        self.carol = await self.session_of(CAROL)
        self.bob = await self.session_of(BOB)

    async def session_of(self, user: Any, guilds: Any = None) -> Any:
        token = await sessions.sign_in(
            self.web, user, guilds if guilds is not None else [THURSDAY], now=NOW, days=30
        )
        found = await sessions.find(self.web, token, now=NOW)
        assert found is not None
        return found

    async def follow(self, *, name: str = "Red Table", version: int = 4, rules: Any = None) -> None:
        content = {
            "rules": rules
            if rules is not None
            else [
                {"number": 2, "rule": "Falling is 1d6 per 10 feet", "instead": "1d6 per 10 feet"},
                {"number": 1, "rule": "Inspiration stacks"},
            ]
        }
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute(
                "INSERT INTO rule_set_follows (guild_id, campaign_id, set_id, kind, set_name,"
                " publisher, version, content, updating, followed_by, followed_at)"
                " VALUES (%s, %s, %s, 'house_rules', %s, %s, %s, %s::jsonb, TRUE, %s, 5)",
                (THURSDAY.id, self.one.id, "s" * 32, name, PUBLISHER, version, json.dumps(content),
                 ALICE.id),
            )  # fmt: skip


class TheRules(HouseRulesBase):
    async def test_any_member_reads_them_newest_number_first_not_only_a_dm(self) -> None:
        for who in (
            self.session,
            self.bob,
            self.carol,
        ):  # Alice DMs it, Bob DMs another, Carol none
            got = await house_rules.read_rules(self.web, who, self.cref)
            self.assertEqual(got["name"], "One")
            self.assertEqual([r["number"] for r in got["rules"]], [3, 2, 1])
        got = await house_rules.read_rules(self.web, self.carol, self.cref)
        by = {r["number"]: r for r in got["rules"]}
        self.assertEqual(by[1]["rule"], "Crits double the dice")
        self.assertEqual(by[2]["supersedes"], "Drinking a potion takes an action")
        self.assertIsNone(by[1]["supersedes"])
        self.assertEqual(got["sets"], [])

    async def test_it_is_this_campaigns_rules_only(self) -> None:
        got = await house_rules.read_rules(self.web, self.carol, self.oref)
        self.assertEqual([r["rule"] for r in got["rules"]], ["Another table's rule"])

    async def test_someone_outside_the_server_or_a_missing_campaign_gets_not_found(self) -> None:
        elsewhere = await self.session_of(CAROL, [QUILLON])
        for who, ref in (
            (elsewhere, self.cref),
            (self.carol, f"{THURSDAY.id}.nosuchcampaign"),
            (self.carol, "nonsense"),
            (self.carol, f"999.{self.one.id}"),
        ):
            with self.subTest(ref=ref), self.assertRaises(RecapError) as caught:
                await house_rules.read_rules(self.web, who, ref)
            self.assertEqual(caught.exception.code, "not_found")
        with self.assertRaises(RecapError):
            await house_rules.render(self.web, elsewhere, self.cref)

    async def test_followed_sets_come_after_with_the_bots_citation(self) -> None:
        await self.follow()
        got = await house_rules.read_rules(self.web, self.carol, self.cref)
        (one,) = got["sets"]
        self.assertEqual((one["name"], one["version"]), ("Red Table", 4))
        self.assertEqual(one["cite"], "Red Table house rules, v4")  # `rules.followed.cite`
        self.assertEqual(
            one["rules"],
            [
                {"number": 1, "rule": "Inspiration stacks", "supersedes": None},
                {
                    "number": 2,
                    "rule": "Falling is 1d6 per 10 feet",
                    "supersedes": "1d6 per 10 feet",
                },
            ],
        )

    async def test_a_followed_row_that_does_not_fit_is_skipped_not_fatal(self) -> None:
        await self.follow(rules=[{"number": "x", "rule": "bad"}, {"rule": "no number"},
                                 {"number": 3, "rule": "Fine"}, "junk"])  # fmt: skip
        got = await house_rules.read_rules(self.web, self.carol, self.cref)
        self.assertEqual([r["rule"] for r in got["sets"][0]["rules"]], ["Fine"])

    async def test_a_homebrew_follow_is_not_a_house_rules_set(self) -> None:
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute(
                "INSERT INTO rule_set_follows (guild_id, campaign_id, set_id, kind, set_name,"
                " publisher, version, content, updating, followed_by, followed_at)"
                " VALUES (%s, %s, %s, 'homebrew', 'Brew', 'p', 1, '{\"rules\": []}'::jsonb,"
                " TRUE, %s, 5)",
                (THURSDAY.id, self.one.id, "b" * 32, ALICE.id),
            )
        self.assertEqual(
            (await house_rules.read_rules(self.web, self.carol, self.cref))["sets"], []
        )

    async def test_no_plan_rule_applies(self) -> None:
        # Nobody here has a plan row at all: the bot gates neither the list nor its download.
        got = await house_rules.read_rules(self.web, self.carol, self.cref)
        self.assertEqual(len(got["rules"]), 3)

    async def test_nothing_private_reaches_the_page(self) -> None:
        await self.follow()
        got = await house_rules.read_rules(self.web, self.carol, self.cref)
        shown = json.dumps(got)
        for private in (SECRET_SCENARIO, "burned the abbey", PUBLISHER, str(ALICE.id), "s" * 32):
            self.assertNotIn(private, shown)
        self.assertEqual(set(got["rules"][0]), {"number", "rule", "supersedes", "fromSet"})
        self.assertEqual(set(got), {"name", "fileName", "rules", "sets"})
        self.assertEqual(got["fileName"], house_file.filename("One"))

    async def test_the_campaign_list_has_campaigns_with_a_rule_or_a_followed_set(self) -> None:
        got = await house_rules.list_campaigns(self.web, self.carol)
        self.assertEqual(
            [(c["ref"], c["name"], c["rules"], c["sets"]) for c in got],
            [(self.cref, "One", 3, 0), (self.oref, "Two", 1, 0)],
        )
        self.assertEqual({c["serverName"] for c in got}, {THURSDAY.name})
        three = await self.campaigns.create(THURSDAY.id, "Three", ALICE.id)  # nothing: not listed
        self.assertEqual(len(await house_rules.list_campaigns(self.web, self.carol)), 2)
        async with self.db.guild(THURSDAY.id) as conn:  # a follow alone lists it
            await conn.execute(
                "INSERT INTO rule_set_follows (guild_id, campaign_id, set_id, kind, set_name,"
                " publisher, version, content, updating, followed_by, followed_at)"
                " VALUES (%s, %s, %s, 'house_rules', 'S', 'p', 1, '{\"rules\": []}'::jsonb,"
                " TRUE, %s, 5)",
                (THURSDAY.id, three.id, "t" * 32, ALICE.id),
            )
        names = [c["name"] for c in await house_rules.list_campaigns(self.web, self.carol)]
        self.assertEqual(names, ["One", "Three", "Two"])
        elsewhere = await self.session_of(CAROL, [QUILLON])
        self.assertEqual(await house_rules.list_campaigns(self.web, elsewhere), [])


class TheFile(HouseRulesBase):
    async def test_the_download_is_the_file_the_bots_button_sends_byte_for_byte(self) -> None:
        name, text = await house_rules.render(self.web, self.carol, self.cref)
        rules = await self.store_h.list(THURSDAY.id, self.one.id)  # what the bot reads
        self.assertEqual(text.encode(), house_file.write("One", rules).encode())
        self.assertEqual(name, house_file.filename("One"))
        self.assertIn("1. Crits double the dice", text)
        self.assertIn(
            "2. Potions are a bonus action (instead of: Drinking a potion takes an action)", text
        )

    async def test_it_has_this_campaigns_rules_only_and_no_followed_sets(self) -> None:
        await self.follow()
        _name, text = await house_rules.render(self.web, self.carol, self.cref)
        self.assertNotIn("Inspiration stacks", text)
        self.assertNotIn("Another table's rule", text)
        self.assertNotIn(SECRET_SCENARIO, text)

    async def test_a_campaign_with_no_rules_has_no_file_as_the_bot_offers_none(self) -> None:
        empty = await self.campaigns.create(THURSDAY.id, "Empty", ALICE.id)
        with self.assertRaises(RecapError) as caught:
            await house_rules.render(self.web, self.carol, f"{THURSDAY.id}.{empty.id}")
        self.assertEqual(caught.exception.code, "not_found")


class TheDoorsAndRole(HouseRulesBase):
    async def test_the_websites_role_cannot_read_the_tables(self) -> None:
        async with self.web.user(CAROL.id, session=self.carol.id_hash) as conn:
            await conn.execute("SELECT set_config('dmbot.guild_id', %s, true)", (str(THURSDAY.id),))
            for table in ("house_rules", "rule_set_follows", "house_rules_file"):
                with self.subTest(table), self.assertRaises(psycopg.errors.InsufficientPrivilege):
                    async with conn.transaction():
                        await conn.execute(f"SELECT * FROM {table}")

    async def test_the_doors_answer_nothing_without_the_server_set(self) -> None:
        async with self.web.user(CAROL.id, session=self.carol.id_hash) as conn:
            cur = await conn.execute(
                "SELECT dmbot_web_house_rules(%s, %s) AS r,"
                " dmbot_web_house_rules_campaigns(%s) AS c",
                (THURSDAY.id, self.one.id, THURSDAY.id),
            )
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual((row["r"], row["c"]), (None, None))

    async def test_the_doors_check_the_sessions_server_list_themselves(self) -> None:
        """The second layer: a session that does not list the server gets NULL from the doors even
        with the server set, so a change to the Python check alone could not open them."""
        elsewhere = await self.session_of(CAROL, [QUILLON])
        async with self.web.user(CAROL.id, session=elsewhere.id_hash) as conn:
            await conn.execute("SELECT set_config('dmbot.guild_id', %s, true)", (str(THURSDAY.id),))
            cur = await conn.execute(
                "SELECT dmbot_web_house_rules(%s, %s) AS r,"
                " dmbot_web_house_rules_campaigns(%s) AS c",
                (THURSDAY.id, self.one.id, THURSDAY.id),
            )
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual((row["r"], row["c"]), (None, None))

    async def test_the_list_filters_before_it_limits(self) -> None:
        for n in range(101):  # 101 campaigns with nothing, named before "Zed"
            await self.campaigns.create(THURSDAY.id, f"Aaa {n:03d}", ALICE.id)
        zed = await self.campaigns.create(THURSDAY.id, "Zed", ALICE.id)
        await self.store_h.add(THURSDAY.id, zed.id, ALICE.id, "The last campaign's rule")
        names = [c["name"] for c in await house_rules.list_campaigns(self.web, self.carol)]
        self.assertEqual(names, ["One", "Two", "Zed"])

    async def test_each_door_has_one_caller(self) -> None:
        for door in ("dmbot_web_house_rules(", "dmbot_web_house_rules_campaigns"):
            self.assertEqual(callers(door), {"schema.py", "web/house_rules.py"}, door)


class TheRoutes(HouseRulesBase):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        app = create_app(
            settings(enforce_plans=True),
            self.web,
            FakeDiscord(),
            payments=FakeProvider(b"h" * 32, "https://s.example"),
            clock=lambda: NOW,
        )
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="https://web-api.internal.test",
            follow_redirects=False,
        )
        self.addAsyncCleanup(self.client.aclose)

    async def sign_in(self, user: Any, guilds: Any) -> None:
        token = await sessions.sign_in(self.web, user, guilds, now=NOW, days=30)
        self.client.cookies.set("__Host-dmbot_session", token, domain="web-api.internal.test")

    async def test_the_list_the_page_and_the_download_agree_with_plans_enforced(self) -> None:
        await self.sign_in(CAROL, [THURSDAY])
        mine = await self.client.get("/me/house-rules", headers=HEADERS)
        self.assertEqual([c["ref"] for c in mine.json()["campaigns"]], [self.cref, self.oref])
        page = await self.client.get(f"/me/campaigns/{self.cref}/house-rules", headers=HEADERS)
        self.assertEqual(page.status_code, 200, page.text)
        self.assertEqual([r["number"] for r in page.json()["rules"]], [3, 2, 1])
        file = await self.client.get(
            f"/me/campaigns/{self.cref}/house-rules/download", headers=HEADERS
        )
        self.assertEqual(file.status_code, 200)
        self.assertEqual(file.headers["content-type"], "text/plain; charset=utf-8")
        self.assertEqual(
            file.headers["content-disposition"], 'attachment; filename="house-rules-one.txt"'
        )
        rules = await self.store_h.list(THURSDAY.id, self.one.id)
        self.assertEqual(file.content, house_file.write("One", rules).encode())

    async def test_someone_outside_the_server_gets_404_and_the_list_is_empty(self) -> None:
        await self.sign_in(CAROL, [QUILLON])
        for path in (
            f"/me/campaigns/{self.cref}/house-rules",
            f"/me/campaigns/{self.cref}/house-rules/download",
        ):
            with self.subTest(path):
                self.assertEqual((await self.client.get(path, headers=HEADERS)).status_code, 404)
        self.assertEqual((await self.client.get("/me/house-rules")).json(), {"campaigns": []})
