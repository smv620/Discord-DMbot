"""The DM's three recap choices on the website (#1468), run as the website's own database role: the
same three as the DM screen's 📜 Recaps panel (recaps, narration, email), changed only through two
database doors that answer a DM of the campaign (never a server manager); the narration cannot be
turned on past the plan; turning anything off never needs a plan; the website's role cannot write
the campaigns table; and the words the page uses are the panel's own."""

from __future__ import annotations

from typing import Any

import httpx
import psycopg

from dmbot.dm_screen import recap_panel
from dmbot.web import sessions
from dmbot.web.app import create_app
from dmbot.web.payments import FakeProvider
from dmbot.web.recap_choices import read_choices, set_choice
from dmbot.web.recaps import RecapError
from tests.test_recap_web import BOB, HEADERS, NOW, Base, callers
from tests.test_web_api import ALICE, API, THURSDAY, FakeDiscord, settings


class Choices(Base):
    async def give_plan(self, user_id: int, plan: str, *, audio_until: int | None = None) -> None:
        async with self.db.plan_writer(user_id) as conn:
            await conn.execute("DELETE FROM entitlements WHERE user_id = %s", (user_id,))
            await conn.execute(
                "INSERT INTO entitlements (user_id, plan, status, hours_cap, extra_hours,"
                " campaign_cap, period_start, period_end, grace_ends_at, lapsed_at,"
                " plan_changed_at, provider, provider_subscription_id, last_event_at, updated_at,"
                " recap_audio_until) VALUES (%s, %s, 'active', 18, 0, 1, %s, %s, NULL, NULL, 0,"
                " 'fake', 'sub_1', 0, 0, %s)",
                (user_id, plan, NOW - 100, NOW + 86400, audio_until),
            )

    async def columns(self, campaign: str) -> tuple[bool, bool, bool]:
        async with self.db.guild(THURSDAY.id) as conn:
            cur = await conn.execute(
                "SELECT recaps_text, recaps_audio, recaps_email FROM campaigns WHERE id = %s",
                (campaign,),
            )
            row = await cur.fetchone()
        assert row is not None
        return row["recaps_text"], row["recaps_audio"], row["recaps_email"]

    async def session_of(self, user: Any) -> Any:
        token = await sessions.sign_in(self.web, user, [THURSDAY], now=NOW, days=30)
        found = await sessions.find(self.web, token, now=NOW)
        assert found is not None
        return found

    async def read(self, session: Any = None, ref: str | None = None) -> dict[str, Any]:
        return await read_choices(
            self.web, session or self.session, ref or self.cref, now=NOW, enforce_plans=True
        )

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.cref = f"{THURSDAY.id}.{self.one.id}"
        self.oref = f"{THURSDAY.id}.{self.two.id}"

    # ---- reading ----------------------------------------------------------------------

    async def test_a_dm_sees_the_three_choices_on_by_default(self) -> None:
        got = await self.read()
        self.assertEqual((got["text"], got["audio"], got["email"]), (True, True, True))
        self.assertEqual(
            set(got), {"text", "audio", "email", "canAudio", "audioHow", "audioUnavailable"}
        )

    async def test_anyone_who_is_not_a_dm_of_it_gets_not_found(self) -> None:
        bob = await self.session_of(BOB)
        for who, ref in (
            (bob, self.cref),  # Bob DMs "Two", not "One"
            (self.session, self.oref),  # Alice DMs "One", not "Two"
            (self.session, f"999.{self.one.id}"),  # a server outside the session's list
            (self.session, "nonsense"),
            (self.session, f"{THURSDAY.id}.{self.one.id}²"),
        ):
            with self.subTest(ref=ref), self.assertRaises(RecapError) as caught:
                await self.read(who, ref)
            self.assertEqual(caught.exception.code, "not_found")

    # ---- writing ----------------------------------------------------------------------

    async def test_each_choice_is_written_to_its_own_column_and_only_that_campaigns(self) -> None:
        await self.give_plan(ALICE.id, "guild")  # the narration may be turned back on
        for which, index in (("text", 0), ("audio", 1), ("email", 2)):
            await set_choice(
                self.web, self.session, self.cref, which, False, now=NOW, enforce_plans=True
            )
            mine = list(await self.columns(self.one.id))
            self.assertEqual([i for i, on in enumerate(mine) if not on], [index], which)
            await set_choice(
                self.web, self.session, self.cref, which, True, now=NOW, enforce_plans=True
            )
            self.assertEqual(await self.columns(self.one.id), (True, True, True))
        self.assertEqual(await self.columns(self.two.id), (True, True, True))  # never the other one

    async def test_it_returns_all_three_as_they_are_now(self) -> None:
        got = await set_choice(
            self.web, self.session, self.cref, "email", False, now=NOW, enforce_plans=True
        )
        self.assertEqual((got["text"], got["audio"], got["email"]), (True, True, False))

    async def test_a_non_dm_changes_nothing(self) -> None:
        bob = await self.session_of(BOB)
        with self.assertRaises(RecapError) as caught:
            await set_choice(self.web, bob, self.cref, "text", False, now=NOW, enforce_plans=True)
        self.assertEqual(caught.exception.code, "not_found")
        self.assertEqual(await self.columns(self.one.id), (True, True, True))

    async def test_an_unknown_choice_is_refused(self) -> None:
        with self.assertRaises(RecapError) as caught:
            await set_choice(
                self.web, self.session, self.cref, "voice", False, now=NOW, enforce_plans=True
            )
        self.assertEqual(caught.exception.code, "bad_choice")

    # ---- the plan ---------------------------------------------------------------------

    async def test_the_narration_cannot_be_turned_on_past_the_plan_but_can_always_be_turned_off(
        self,
    ) -> None:
        await set_choice(
            self.web, self.session, self.cref, "audio", False, now=NOW, enforce_plans=True
        )  # off: no plan
        self.assertFalse((await self.read())["canAudio"])  # no plan row at all
        with self.assertRaises(RecapError) as caught:
            await set_choice(
                self.web, self.session, self.cref, "audio", True, now=NOW, enforce_plans=True
            )
        self.assertEqual(caught.exception.code, "not_in_plan")
        self.assertEqual((await self.columns(self.one.id))[1], False)
        await self.give_plan(ALICE.id, "table")  # a plan without the narration
        self.assertFalse((await self.read())["canAudio"])
        with self.assertRaises(RecapError):
            await set_choice(
                self.web, self.session, self.cref, "audio", True, now=NOW, enforce_plans=True
            )

    async def test_a_plan_with_the_narration_allows_it_and_says_how(self) -> None:
        await self.give_plan(ALICE.id, "guild")
        got = await self.read()
        self.assertEqual((got["canAudio"], got["audioHow"]), (True, "part of your plan"))
        await self.give_plan(ALICE.id, "table", audio_until=NOW + 3600)  # the add-on
        got = await self.read()
        self.assertEqual((got["canAudio"], got["audioHow"]), (True, "an add-on"))
        await set_choice(
            self.web, self.session, self.cref, "audio", False, now=NOW, enforce_plans=True
        )
        got = await set_choice(
            self.web, self.session, self.cref, "audio", True, now=NOW, enforce_plans=True
        )
        self.assertTrue(got["audio"])
        # An add-on that has run out is no plan for it.
        await self.give_plan(ALICE.id, "table", audio_until=NOW - 1)
        self.assertFalse((await self.read())["canAudio"])

    async def test_with_plans_not_enforced_the_narration_may_be_offered_as_the_bot_allows(
        self,
    ) -> None:
        got = await read_choices(self.web, self.session, self.cref, now=NOW, enforce_plans=False)
        self.assertEqual((got["canAudio"], got["audioHow"]), (True, ""))
        await set_choice(  # off, then on: no plan row at all, and no refusal
            self.web, self.session, self.cref, "audio", False, now=NOW, enforce_plans=False
        )
        got = await set_choice(
            self.web, self.session, self.cref, "audio", True, now=NOW, enforce_plans=False
        )
        self.assertTrue(got["audio"])

    async def test_the_page_is_told_why_the_narration_cannot_be_made(self) -> None:
        """(#2419) The greyed 🎙️ button gives the reason: set-up first, then plan, then allowance."""

        async def why(**more: Any) -> str:
            got = await read_choices(
                self.web, self.session, self.cref, now=NOW, enforce_plans=True, **more
            )
            return str(got["audioUnavailable"])

        await self.give_plan(ALICE.id, "guild")
        self.assertEqual(await why(), "")
        self.assertEqual(await why(recaps_on=False), "not_set_up")
        self.assertEqual(await why(audio_allowed=0), "allowance")  # none left this month
        self.assertEqual(await why(audio_allowed=8), "")
        await self.give_plan(ALICE.id, "table")  # no narration in this plan
        self.assertEqual(await why(audio_allowed=8), "plan")
        # Plans not enforced (the test server): the bot holds nobody to the allowance either.
        got = await read_choices(
            self.web, self.session, self.cref, now=NOW, enforce_plans=False, audio_allowed=0
        )
        self.assertEqual(got["audioUnavailable"], "")

    async def test_a_plan_that_does_not_work_does_not_offer_the_narration(self) -> None:
        await self.give_plan(ALICE.id, "guild")
        async with self.db.plan_writer(ALICE.id) as conn:
            await conn.execute(
                "UPDATE entitlements SET period_start = %s, period_end = %s WHERE user_id = %s",
                (NOW - 60 * 86400, NOW - 30 * 86400, ALICE.id),  # long over, never renewed
            )
        self.assertFalse((await self.read())["canAudio"])

    async def test_a_co_dm_sees_the_narration_from_the_owners_plan_as_the_panel_does(self) -> None:
        await self.campaigns.add_dm(THURSDAY.id, self.one.id, BOB.id)
        bob = await self.session_of(BOB)
        # Alice owns the campaign. With no plan of hers, Bob's own plan changes nothing (#1470).
        await self.give_plan(BOB.id, "guild")
        self.assertEqual(((await self.read(bob))["canAudio"]), False)
        # Her plan has the narration: the co-DM is offered it, and may turn it on.
        await self.give_plan(ALICE.id, "guild")
        got = await self.read(bob)
        self.assertEqual(
            (got["text"], got["canAudio"], got["audioHow"]), (True, True, "part of your plan")
        )
        await set_choice(self.web, bob, self.cref, "audio", False, now=NOW, enforce_plans=True)
        got = await set_choice(self.web, bob, self.cref, "audio", True, now=NOW, enforce_plans=True)
        self.assertTrue(got["audio"])
        # Her plan has it as an add-on: said as the panel says it. And if her plan has no
        # narration, the co-DM is refused as the owner would be, even with a plan of their own.
        await self.give_plan(ALICE.id, "table", audio_until=NOW + 86400)
        self.assertEqual((await self.read(bob))["audioHow"], "an add-on")
        await self.give_plan(ALICE.id, "table")
        self.assertFalse((await self.read(bob))["canAudio"])
        with self.assertRaises(RecapError) as caught:
            await set_choice(self.web, bob, self.cref, "audio", True, now=NOW, enforce_plans=True)
        self.assertEqual(caught.exception.code, "not_in_plan")
        # A co-DM may always turn the others off, as on the DM screen.
        got = await set_choice(
            self.web, bob, self.cref, "email", False, now=NOW, enforce_plans=True
        )
        self.assertFalse(got["email"])

    async def test_nothing_about_the_owner_or_the_plan_is_in_what_a_co_dm_is_told(self) -> None:
        await self.campaigns.add_dm(THURSDAY.id, self.one.id, BOB.id)
        bob = await self.session_of(BOB)
        await self.give_plan(ALICE.id, "guild")
        got = await self.read(bob)
        self.assertEqual(set(got), {"text", "audio", "email", "canAudio", "audioHow"})
        shown = repr(got).casefold()
        for secret in (str(ALICE.id), "guild", "sub_1", "active", "fake"):
            self.assertNotIn(secret, shown.replace("part of your plan", ""))

    async def test_a_player_and_a_server_manager_who_is_no_dm_still_get_not_found(self) -> None:
        carol = await self.session_of(BOB)  # Bob DMs the other campaign only
        await self.give_plan(ALICE.id, "guild")
        with self.assertRaises(RecapError) as caught:
            await self.read(carol)
        self.assertEqual(caught.exception.code, "not_found")

    # ---- the doors and the role -------------------------------------------------------

    async def test_the_doors_answer_nothing_without_the_server_set_for_the_transaction(
        self,
    ) -> None:
        async with self.web.user(ALICE.id, session=self.session.id_hash) as conn:
            cur = await conn.execute(
                "SELECT dmbot_web_recap_choices(%s, %s) AS r,"
                " dmbot_web_set_recap_choice(%s, %s, 'text', false) AS w",
                (THURSDAY.id, self.one.id, THURSDAY.id, self.one.id),
            )
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual((row["r"], row["w"]), (None, "not_allowed"))
        self.assertEqual(await self.columns(self.one.id), (True, True, True))

    async def test_the_websites_role_cannot_write_the_choices_itself(self) -> None:
        async with self.web.user(ALICE.id, session=self.session.id_hash) as conn:
            await conn.execute("SELECT set_config('dmbot.guild_id', %s, true)", (str(THURSDAY.id),))
            for column in ("recaps_text", "recaps_audio", "recaps_email"):
                with self.subTest(column), self.assertRaises(psycopg.errors.InsufficientPrivilege):
                    async with conn.transaction():
                        await conn.execute(f"UPDATE campaigns SET {column} = false")

    async def test_each_door_has_one_caller(self) -> None:
        self.assertEqual(callers("dmbot_web_recap_choices("), {"schema.py", "web/recap_choices.py"})
        self.assertEqual(
            callers("dmbot_web_set_recap_choice("), {"schema.py", "web/recap_choices.py"}
        )

    async def test_how_the_plan_has_it_is_said_as_the_panel_says_it(self) -> None:
        self.assertEqual(
            recap_panel.audio_how("Table", "paid", frozenset({"guild", "pro"})), "an add-on"
        )
        self.assertEqual(
            recap_panel.audio_how("Guild", "paid", frozenset({"guild", "pro"})), "part of your plan"
        )


class TheRoutes(Choices):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        app = create_app(
            settings(enforce_plans=True), self.web, FakeDiscord(), payments=FakeProvider(b"h" * 32, "https://s.example"),
            clock=lambda: NOW,
        )  # fmt: skip
        token = await sessions.sign_in(self.web, ALICE, [THURSDAY], now=NOW, days=30)
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=API, follow_redirects=False
        )
        self.client.cookies.set("__Host-dmbot_session", token, domain="api.dmbot.example")
        self.addAsyncCleanup(self.client.aclose)
        self.anonymous = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=API, follow_redirects=False
        )
        self.addAsyncCleanup(self.anonymous.aclose)

    async def test_read_and_change_over_http(self) -> None:
        url = f"/me/campaigns/{self.cref}/recap-choices"
        got = await self.client.get(url)
        self.assertEqual(got.status_code, 200)
        self.assertEqual((got.json()["text"], got.json()["email"]), (True, True))
        post = await self.client.post(url, json={"which": "email", "on": False}, headers=HEADERS)
        self.assertEqual(post.status_code, 200)
        self.assertFalse(post.json()["email"])
        self.assertEqual(await self.columns(self.one.id), (True, True, False))

    async def test_refusals_over_http(self) -> None:
        url = f"/me/campaigns/{self.cref}/recap-choices"
        other = f"/me/campaigns/{self.oref}/recap-choices"
        self.assertEqual((await self.client.get(other)).status_code, 404)
        self.assertEqual((await self.anonymous.get(url)).status_code, 401)
        for body in (
            {},
            {"which": "email"},
            {"which": "email", "on": "yes"},
            {"which": 3, "on": True},
        ):
            put = await self.client.post(url, json=body, headers=HEADERS)
            self.assertEqual(put.status_code, 422, body)
        unknown = await self.client.post(url, json={"which": "voice", "on": True}, headers=HEADERS)
        self.assertEqual((unknown.status_code, unknown.json()), (422, {"error": "bad_choice"}))
        past = await self.client.post(url, json={"which": "audio", "on": True}, headers=HEADERS)
        self.assertEqual((past.status_code, past.json()), (409, {"error": "not_in_plan"}))
        elsewhere = await self.client.post(
            other, json={"which": "text", "on": False}, headers=HEADERS
        )
        self.assertEqual(elsewhere.status_code, 404)
