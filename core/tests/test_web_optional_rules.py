"""A campaign's optional rules on the website's Settings tab (#2753): read only, DM only, the same
list and words as Discord's `/dmbot optionalrules`, and nothing but the catalog and the DM's choices
in the answer. Invented names throughout."""

from __future__ import annotations

from typing import Any

import httpx

from dmbot.rules import optional
from dmbot.ui import logic
from dmbot.web import campaign_settings, optional_rules, sessions
from dmbot.web.app import create_app
from dmbot.web.payments import FakeProvider
from dmbot.web.recaps import RecapError
from tests.test_recap_web import BOB, HEADERS, NOW, Base
from tests.test_web_api import ALICE, API, THURSDAY, FakeDiscord, settings


class Reading(Base):
    def cref(self, campaign: Any = None) -> str:
        return f"{THURSDAY.id}.{(campaign or self.one).id}"

    async def sql(self, statement: str, *args: Any) -> None:
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute(statement, args)

    async def refused(self, session: Any, ref: str) -> str:
        with self.assertRaises(RecapError) as caught:
            await optional_rules.read_rules(self.web, session, ref)
        return str(caught.exception.code)

    async def test_every_rule_that_applies_is_listed_with_the_campaign_default(self) -> None:
        got = await optional_rules.read_rules(self.web, self.session, self.cref())
        target = (await self.campaigns.get(THURSDAY.id, self.one.id)).target_ruleset  # type: ignore[union-attr]
        applying = optional.applying(target)
        self.assertEqual([r["id"] for r in got["rules"]], [r.id for r in applying])
        self.assertTrue(got["default"])
        self.assertTrue(all(r["on"] and not r["changed"] for r in got["rules"]))
        self.assertEqual(got["main"], optional_rules._main_rules(target))

    async def test_a_choice_the_dm_made_shows_and_follows_the_catalog_words(self) -> None:
        first = optional.applying("2024")[0]
        await self.campaigns.set_optional_rule(THURSDAY.id, self.one.id, first.id, False)
        got = await optional_rules.read_rules(self.web, self.session, self.cref())
        row = next(r for r in got["rules"] if r["id"] == first.id)
        self.assertEqual(
            (row["on"], row["changed"], row["name"], row["summary"], row["book"]),
            (False, True, first.name, first.summary, first.source),
        )
        others = [r for r in got["rules"] if r["id"] != first.id]
        self.assertTrue(all(r["on"] and not r["changed"] for r in others))

    async def test_a_default_of_off_turns_every_unchosen_rule_off(self) -> None:
        await self.campaigns.set_optional_rules_default(THURSDAY.id, self.one.id, False)
        got = await optional_rules.read_rules(self.web, self.session, self.cref())
        self.assertFalse(got["default"])
        self.assertTrue(all(not r["on"] for r in got["rules"]))

    async def test_the_note_names_the_rules_the_2024_main_rules_cover(self) -> None:
        note = optional_rules.covered_note("2024")
        self.assertEqual(
            note.startswith("Not listed, because the 2024 rules have their own"), bool(note)
        )
        self.assertEqual(optional_rules.covered_note("2014"), "")

    async def test_the_words_are_the_discord_ones(self) -> None:
        self.assertEqual(optional_rules._main_rules("2024"), "2024 rules")
        self.assertEqual(optional_rules._main_rules("none"), logic.ruleset_label("none"))

    async def test_a_player_and_another_campaigns_dm_are_not_allowed(self) -> None:
        bob = await sessions.find(
            self.web,
            await sessions.sign_in(self.web, BOB, [THURSDAY], now=NOW, days=30),  # type: ignore[arg-type]
            now=NOW,
        )
        assert bob is not None
        self.assertEqual(await self.refused(bob, self.cref()), "not_allowed")  # Alice's campaign
        self.assertEqual(
            await self.refused(self.session, self.cref(self.two)), "not_allowed"
        )  # Bob's

    async def test_a_server_the_session_does_not_have_is_not_found(self) -> None:
        self.assertEqual(await self.refused(self.session, f"999.{self.one.id}"), "not_found")
        self.assertEqual(await self.refused(self.session, "nonsense"), "not_found")

    async def test_the_door_gives_nothing_but_the_choices(self) -> None:
        async with self.web.user(self.session.user_id, session=self.session.id_hash) as conn:
            await conn.execute("SELECT set_config('dmbot.guild_id', %s, true)", (str(THURSDAY.id),))
            cur = await conn.execute(
                "SELECT dmbot_web_optional_rules(%s, %s) AS r", (THURSDAY.id, self.one.id)
            )
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual(set(row["r"]), {"target", "default", "overrides"})

    async def test_the_website_role_cannot_read_the_choices_table(self) -> None:
        async with self.db.guild(THURSDAY.id) as conn:
            cur = await conn.execute(
                "SELECT has_table_privilege('dmbot_web', 'campaign_optional_rules', 'SELECT')"
                " AS can"
            )
            row = await cur.fetchone()
        assert row is not None
        self.assertFalse(row["can"])


class Choosing(Reading):
    """A rule switched on the website is queued like the other settings, and read back alike."""

    async def queue(self, body: object, ref: str | None = None, session: Any = None) -> str:
        return await campaign_settings.queue_setting(
            self.web, session or self.session, ref or self.cref(), body
        )

    async def rows(self) -> list[dict[str, Any]]:
        async with self.db.guild(THURSDAY.id) as conn:
            cur = await conn.execute(
                "SELECT setting, value, turn_on, flag, user_id FROM setting_actions ORDER BY seq"
            )
            return list(await cur.fetchall())

    async def test_a_choice_is_queued_with_its_rule_and_which_way(self) -> None:
        rule = optional.applying("2024")[0].id
        answer = await self.queue({"setting": "optional_rule", "value": rule, "on": False})
        self.assertRegex(answer, r"^[0-9a-f]{32}$")
        (row,) = await self.rows()
        self.assertEqual(
            (row["setting"], row["value"], row["turn_on"], row["flag"], row["user_id"]),
            ("optional_rule", rule, False, False, ALICE.id),
        )
        read = await campaign_settings.read_setting(self.web, self.session, self.cref(), answer)
        self.assertEqual(
            (read["state"], read["setting"], read["value"]), ("waiting", "optional_rule", rule)
        )

    async def test_a_rule_that_is_not_in_the_catalog_or_has_no_yes_or_no_is_refused(self) -> None:
        good = optional.applying("2024")[0].id
        for body in (
            {"setting": "optional_rule", "value": "no-such-rule", "on": True},
            {"setting": "optional_rule", "value": good},
            {"setting": "optional_rule", "value": good, "on": "yes"},
            {"setting": "optional_rule", "on": True},
            {"setting": "optional_rule", "value": 5, "on": True},
        ):
            with self.assertRaises(RecapError) as caught:
                await self.queue(body)
            self.assertEqual(caught.exception.code, "bad_value", body)
        self.assertEqual(await self.rows(), [])

    async def test_the_three_settings_are_unchanged_and_carry_no_turn_on(self) -> None:
        await self.queue({"setting": "level", "value": "quiet"})
        await self.queue({"setting": "retention", "value": "90", "confirm": True})
        rows = await self.rows()
        self.assertEqual([r["turn_on"] for r in rows], [None, None])
        self.assertEqual([r["flag"] for r in rows], [False, True])

    async def test_only_a_dm_of_the_campaign_may_queue_one(self) -> None:
        rule = optional.applying("2024")[0].id
        body = {"setting": "optional_rule", "value": rule, "on": True}
        bob = await sessions.find(
            self.web,
            await sessions.sign_in(self.web, BOB, [THURSDAY], now=NOW, days=30),  # type: ignore[arg-type]
            now=NOW,
        )
        assert bob is not None
        for session, ref in ((bob, self.cref()), (self.session, self.cref(self.two))):
            with self.assertRaises(RecapError) as caught:
                await self.queue(body, ref, session)
            self.assertEqual(caught.exception.code, "not_allowed")
        self.assertEqual(await self.rows(), [])

    async def test_ten_waiting_choices_is_too_many(self) -> None:
        rule = optional.applying("2024")[0].id
        for n in range(10):
            await self.queue({"setting": "optional_rule", "value": rule, "on": n % 2 == 0})
        with self.assertRaises(RecapError) as caught:
            await self.queue({"setting": "optional_rule", "value": rule, "on": True})
        self.assertEqual(caught.exception.code, "too_soon")

    async def test_the_table_will_not_hold_a_turn_on_that_does_not_belong(self) -> None:
        import psycopg.errors

        for setting, value, turn_on in (
            ("optional_rule", "x", None),  # a rule with no yes or no
            ("level", "quiet", True),  # a setting that has none
        ):
            with self.assertRaises(psycopg.errors.CheckViolation):
                async with self.db.guild(THURSDAY.id) as conn:
                    await conn.execute(
                        "INSERT INTO setting_actions (guild_id, campaign_id, id, user_id, setting,"
                        " value, turn_on, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s, 1)",
                        (THURSDAY.id, self.one.id, "a" * 32, ALICE.id, setting, value, turn_on),
                    )

    async def test_a_campaign_that_follows_a_shared_ruleset_lists_the_same_rules(self) -> None:
        """Discord's /dmbot optionalrules does not look at what a campaign follows: the rules
        catalog and the campaign's own choices decide, so the website must not either."""
        before = await optional_rules.read_rules(self.web, self.session, self.cref())
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute(
                "INSERT INTO rule_set_follows (guild_id, campaign_id, set_id, kind, set_name,"
                " publisher, version, content, updating, followed_by, followed_at)"
                " VALUES (%s, %s, 'pub-1', 'house_rules', 'House Rules', 'Brannoc', 1,"
                " '{}'::jsonb, true, %s, 1)",
                (THURSDAY.id, self.one.id, ALICE.id),
            )
        after = await optional_rules.read_rules(self.web, self.session, self.cref())
        self.assertEqual(before, after)
        rule = optional.applying("2024")[0].id
        await self.queue({"setting": "optional_rule", "value": rule, "on": False})  # still allowed


class TheRoute(Choosing):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        app = create_app(
            settings(), self.web, FakeDiscord(),
            payments=FakeProvider(b"h" * 32, "https://s.example"), clock=lambda: NOW,
        )  # fmt: skip
        token = await sessions.sign_in(self.web, ALICE, [THURSDAY], now=NOW, days=30)
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=API, follow_redirects=False
        )
        self.client.cookies.set("__Host-dmbot_session", token, domain="api.dmbot.example")
        self.addAsyncCleanup(self.client.aclose)

    async def test_the_dm_reads_the_list_over_http(self) -> None:
        got = await self.client.get(f"/me/campaigns/{self.cref()}/optional-rules", headers=HEADERS)
        self.assertEqual(got.status_code, 200, got.text)
        body = got.json()
        self.assertEqual(set(body), {"main", "rules", "note", "default"})
        self.assertTrue(body["rules"])

    async def test_a_campaign_that_is_not_theirs_says_not_allowed(self) -> None:
        got = await self.client.get(f"/me/campaigns/{self.cref(self.two)}/optional-rules")
        self.assertEqual((got.status_code, got.json()["error"]), (403, "not_allowed"))

    async def test_a_rule_is_queued_over_http_and_a_bad_one_is_refused(self) -> None:
        rule = optional.applying("2024")[0].id
        base = f"/me/campaigns/{self.cref()}/settings"
        made = await self.client.post(
            base, json={"setting": "optional_rule", "value": rule, "on": False}, headers=HEADERS
        )
        self.assertEqual(made.status_code, 202, made.text)
        got = await self.client.get(f"{base}/answers/{made.json()['answer']}")
        self.assertEqual((got.status_code, got.json()["setting"]), (200, "optional_rule"))
        bad = await self.client.post(
            base, json={"setting": "optional_rule", "value": "nope", "on": True}, headers=HEADERS
        )
        self.assertEqual((bad.status_code, bad.json()["error"]), (400, "bad_value"))

    async def test_a_bad_address_is_not_found(self) -> None:
        got = await self.client.get("/me/campaigns/nonsense/optional-rules")
        self.assertEqual(got.status_code, 404)
