"""A campaign's game masters on the website's Settings tab (#2768): who they are, and who can be
offered as one more. Read only, DM only, and never anyone who has not agreed to be recorded.
Invented names throughout."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import httpx

from dmbot.campaigns.store import MAX_DMS
from dmbot.members import MemberDirectory
from dmbot.web import campaign_settings, game_masters, sessions
from dmbot.web.app import create_app
from dmbot.web.discord import DiscordGuild
from dmbot.web.payments import FakeProvider
from dmbot.web.recaps import RecapError
from tests.test_recap_web import BOB, HEADERS, NOW, Base
from tests.test_web_api import ALICE, API, THURSDAY, FakeDiscord, settings

CARL = SimpleNamespace(id=3003, name="Carl", email="carl@example.com")  # plays; runs nothing
DANA = 6001  # agreed to be recorded; a member of the server
ELI = 6002  # a member who never agreed
FAY = 6003  # agreed to be recorded in another server only
GUS = 6004  # the campaign's own person, played by an account that agreed
OTHER_SERVER = 222


class GameMastersBase(Base):
    """Helpers only (no tests here, so none are run twice by the classes that build on it)."""

    def cref(self, campaign: Any = None) -> str:
        return f"{THURSDAY.id}.{(campaign or self.one).id}"

    async def agreed(self, user_id: int, name: str, guild_id: int = THURSDAY.id) -> None:
        async with self.db.guild(guild_id) as conn:
            await conn.execute(
                "INSERT INTO consent (guild_id, user_id, granted_at, terms_version)"
                " VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING",
                (guild_id, user_id, NOW, 1),
            )
        await MemberDirectory(self.db).remember(guild_id, user_id, name, name.lower())

    async def signed(self, user: Any, manage: bool) -> Any:
        guild = DiscordGuild(THURSDAY.id, THURSDAY.name, manage)
        token = await sessions.sign_in(self.web, user, [guild], now=NOW, days=30)
        found = await sessions.find(self.web, token, now=NOW)
        assert found is not None
        return found

    async def people(self, q: str = "") -> list[str]:
        got = await game_masters.read_candidates(self.web, self.session, self.cref(), q)
        return [p["name"] for p in got["people"]]

    async def refused(self, session: Any, ref: str) -> str:
        with self.assertRaises(RecapError) as caught:
            await game_masters.read_game_masters(self.web, session, ref)
        return str(caught.exception.code)

    async def queue(self, body: object, ref: str | None = None, session: Any = None) -> str:
        return await campaign_settings.queue_setting(
            self.web, session or self.session, ref or self.cref(), body
        )

    async def queue_refused(self, body: object, session: Any = None, ref: str | None = None) -> str:
        with self.assertRaises(RecapError) as caught:
            await self.queue(body, ref, session)
        return str(caught.exception.code)

    async def rows(self) -> list[dict[str, Any]]:
        async with self.db.guild(THURSDAY.id) as conn:
            cur = await conn.execute(
                "SELECT setting, value, turn_on, flag, user_id FROM setting_actions ORDER BY seq"
            )
            return list(await cur.fetchall())

    async def door(self, uid: str | None, on: bool | None) -> Any:
        """Call the queue door itself, as the website's role does, to run its own refusals."""
        async with self.web.user(self.session.user_id, session=self.session.id_hash) as conn:
            await conn.execute("SELECT set_config('dmbot.guild_id', %s, true)", (str(THURSDAY.id),))
            cur = await conn.execute(
                "SELECT dmbot_web_co_dm_add(%s, %s, %s, %s) AS r",
                (THURSDAY.id, self.one.id, uid, on),
            )
            row = await cur.fetchone()
        assert row is not None
        return row["r"]


class TheList(GameMastersBase):
    async def test_the_owner_alone_is_marked_as_the_plan_and_as_you(self) -> None:
        got = await game_masters.read_game_masters(self.web, self.session, self.cref())
        self.assertEqual(got["max"], MAX_DMS)
        self.assertEqual(
            [(g["userId"], g["plan"], g["you"]) for g in got["gameMasters"]],
            [(str(ALICE.id), True, True)],
        )

    async def test_a_co_dm_shows_by_the_name_the_server_directory_has(self) -> None:
        await self.agreed(DANA, "Dana")
        await self.campaigns.add_dm(THURSDAY.id, self.one.id, DANA)
        got = await game_masters.read_game_masters(self.web, self.session, self.cref())
        dana = next(g for g in got["gameMasters"] if g["userId"] == str(DANA))
        self.assertEqual((dana["name"], dana["plan"], dana["you"]), ("Dana", False, False))
        self.assertEqual(got["gameMasters"][0]["userId"], str(ALICE.id))  # the plan holder first

    async def test_a_game_master_nothing_knows_by_name_is_still_listed(self) -> None:
        await self.campaigns.add_dm(THURSDAY.id, self.one.id, ELI)
        got = await game_masters.read_game_masters(self.web, self.session, self.cref())
        eli = next(g for g in got["gameMasters"] if g["userId"] == str(ELI))
        self.assertEqual(eli["name"], game_masters.NAMELESS)

    async def test_the_list_holds_this_campaigns_dms_only(self) -> None:
        await self.campaigns.add_dm(THURSDAY.id, self.two.id, DANA)
        got = await game_masters.read_game_masters(self.web, self.session, self.cref())
        self.assertEqual([g["userId"] for g in got["gameMasters"]], [str(ALICE.id)])

    async def test_a_player_a_manager_who_is_not_a_dm_and_another_campaigns_dm_are_refused(
        self,
    ) -> None:
        player = await self.signed(CARL, manage=False)
        manager = await self.signed(CARL, manage=True)  # may manage the server, runs nothing
        bob = await self.signed(BOB, manage=True)  # a DM, but of Bob's campaign only
        for who in (player, manager, bob):
            self.assertEqual(await self.refused(who, self.cref()), "not_allowed")
        self.assertEqual(await self.refused(self.session, self.cref(self.two)), "not_allowed")

    async def test_a_server_the_session_does_not_have_is_not_found(self) -> None:
        self.assertEqual(await self.refused(self.session, f"999.{self.one.id}"), "not_found")
        self.assertEqual(await self.refused(self.session, "nonsense"), "not_found")

    async def test_the_website_role_cannot_read_the_server_directory_itself(self) -> None:
        async with self.db.guild(THURSDAY.id) as conn:
            cur = await conn.execute(
                "SELECT has_table_privilege('dmbot_web', 'member_directory', 'SELECT') AS can"
            )
            row = await cur.fetchone()
        assert row is not None
        self.assertFalse(row["can"])


class TheCandidates(GameMastersBase):
    async def test_only_members_who_agreed_are_offered(self) -> None:
        await self.agreed(DANA, "Dana")
        await MemberDirectory(self.db).remember(THURSDAY.id, ELI, "Eli", "eli")  # (refused: no row)
        self.assertEqual(await self.people(), ["Dana"])

    async def test_a_search_matches_the_name_or_the_username_in_any_case(self) -> None:
        await self.agreed(DANA, "Dana")
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute(
                "UPDATE member_directory SET username = 'sun_singer' WHERE user_id = %s", (DANA,)
            )
        self.assertEqual(await self.people("DAN"), ["Dana"])
        self.assertEqual(await self.people("sun_sing"), ["Dana"])
        self.assertEqual(await self.people("zzz"), [])

    async def test_a_game_master_already_is_never_offered(self) -> None:
        await self.agreed(DANA, "Dana")
        await self.campaigns.add_dm(THURSDAY.id, self.one.id, DANA)
        self.assertEqual(await self.people(), [])

    async def test_another_servers_members_are_never_offered(self) -> None:
        await self.agreed(FAY, "Fay", guild_id=OTHER_SERVER)
        self.assertEqual(await self.people(), [])

    async def test_at_most_twenty_five_come_back(self) -> None:
        for i in range(30):
            await self.agreed(7000 + i, f"Member {i:02d}")
        self.assertEqual(len(await self.people()), 25)

    async def test_an_answer_carries_only_an_id_and_a_name(self) -> None:
        await self.agreed(DANA, "Dana")
        got = await game_masters.read_candidates(self.web, self.session, self.cref(), "")
        self.assertEqual(got, {"people": [{"userId": str(DANA), "name": "Dana"}]})

    async def test_a_very_long_search_is_cut_not_refused(self) -> None:
        await self.agreed(DANA, "Dana")
        self.assertEqual(await self.people("dana" + "x" * 500), [])

    async def test_a_player_a_manager_who_is_not_a_dm_and_another_campaigns_dm_are_refused(
        self,
    ) -> None:
        for who in (
            await self.signed(CARL, manage=False),
            await self.signed(CARL, manage=True),
            await self.signed(BOB, manage=True),
        ):
            with self.assertRaises(RecapError) as caught:
                await game_masters.read_candidates(self.web, who, self.cref(), "")
            self.assertEqual(caught.exception.code, "not_allowed")


class Queueing(GameMastersBase):
    """An add or a removal is queued like the other settings, with quick refusals; the bot checks
    every rule of Discord's game masters again when it applies it."""

    async def test_an_add_is_queued_with_the_persons_id_and_which_way(self) -> None:
        await self.agreed(DANA, "Dana")
        answer = await self.queue({"setting": "co_dm", "value": str(DANA), "on": True})
        self.assertRegex(answer, r"^[0-9a-f]{32}$")
        (row,) = await self.rows()
        self.assertEqual(
            (row["setting"], row["value"], row["turn_on"], row["flag"], row["user_id"]),
            ("co_dm", str(DANA), True, False, ALICE.id),
        )

    async def test_a_removal_is_queued_and_the_waiting_answer_reads_back(self) -> None:
        await self.campaigns.add_dm(THURSDAY.id, self.one.id, DANA)
        answer = await self.queue({"setting": "co_dm", "value": str(DANA), "on": False})
        got = await campaign_settings.read_setting(self.web, self.session, self.cref(), answer)
        self.assertEqual(
            (got["state"], got["setting"], got["value"]), ("waiting", "co_dm", str(DANA))
        )

    async def test_the_plan_holder_cannot_be_removed(self) -> None:
        body = {"setting": "co_dm", "value": str(ALICE.id), "on": False}
        self.assertEqual(await self.queue_refused(body), "plan_holder")
        self.assertEqual(await self.rows(), [])

    async def test_an_add_at_the_limit_is_turned_away(self) -> None:
        await self.agreed(DANA, "Dana")
        for i in range(MAX_DMS - 1):  # (the owner is one already)
            await self.campaigns.add_dm(THURSDAY.id, self.one.id, 8000 + i)
        body = {"setting": "co_dm", "value": str(DANA), "on": True}
        self.assertEqual(await self.queue_refused(body), "too_many")
        self.assertEqual(await self.rows(), [])

    async def test_a_person_nobody_knows_is_not_queued(self) -> None:
        self.assertEqual(
            await self.queue_refused({"setting": "co_dm", "value": str(ELI), "on": True}),
            "unknown_person",
        )  # (a member who never agreed is not offered, so cannot be queued by a made-up id either)
        self.assertEqual(await self.rows(), [])

    async def test_an_agreement_in_another_server_does_not_count(self) -> None:
        await self.agreed(FAY, "Fay", guild_id=OTHER_SERVER)
        self.assertEqual(
            await self.queue_refused({"setting": "co_dm", "value": str(FAY), "on": True}),
            "unknown_person",
        )

    async def test_a_malformed_id_or_no_yes_or_no_is_a_bad_value(self) -> None:
        for body in (
            {"setting": "co_dm", "value": "dana", "on": True},
            {"setting": "co_dm", "value": "0", "on": True},
            {"setting": "co_dm", "value": "1" * 20, "on": True},
            {"setting": "co_dm", "value": 6001, "on": True},
            {"setting": "co_dm", "value": str(DANA)},
            {"setting": "co_dm", "value": str(DANA), "on": "yes"},
        ):
            self.assertEqual(await self.queue_refused(body), "bad_value", body)
        # The door says the same on its own: a route that forgot to check is still turned away.
        self.assertEqual((await self.door(None, True))["status"], "bad_value")
        self.assertEqual((await self.door("12", None))["status"], "bad_value")

    async def test_a_player_a_manager_who_is_not_a_dm_and_another_campaigns_dm_are_refused(
        self,
    ) -> None:
        await self.agreed(DANA, "Dana")
        body = {"setting": "co_dm", "value": str(DANA), "on": True}
        for who in (
            await self.signed(CARL, manage=False),
            await self.signed(CARL, manage=True),
            await self.signed(BOB, manage=True),
        ):
            self.assertEqual(await self.queue_refused(body, session=who), "not_allowed")
        self.assertEqual(await self.queue_refused(body, ref=self.cref(self.two)), "not_allowed")
        self.assertEqual(await self.rows(), [])

    async def test_the_queue_still_takes_the_other_choices(self) -> None:
        await self.queue({"setting": "visibility", "value": "private"})
        (row,) = await self.rows()
        self.assertEqual((row["setting"], row["turn_on"]), ("visibility", None))

    async def test_a_choice_that_is_not_a_game_master_change_cannot_carry_a_yes_or_no(self) -> None:
        async with self.db.guild(THURSDAY.id) as conn:
            with self.assertRaises(Exception):  # noqa: B017 (a CHECK refusing the row)
                await conn.execute(
                    "INSERT INTO setting_actions (guild_id, campaign_id, id, user_id, setting,"
                    " value, turn_on, created_at) VALUES (%s, %s, %s, %s, 'visibility',"
                    " 'open', true, %s)",
                    (THURSDAY.id, self.one.id, "f" * 32, ALICE.id, NOW),
                )


class TheRoutes(GameMastersBase):
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

    async def test_the_dm_reads_the_list_and_the_people_over_http(self) -> None:
        await self.agreed(DANA, "Dana")
        base = f"/me/campaigns/{self.cref()}/game-masters"
        got = await self.client.get(base)
        self.assertEqual(got.status_code, 200, got.text)
        self.assertEqual(set(got.json()), {"max", "gameMasters"})
        found = await self.client.get(f"{base}/candidates", params={"q": "dan"})
        self.assertEqual(found.json(), {"people": [{"userId": str(DANA), "name": "Dana"}]})

    async def test_a_campaign_that_is_not_theirs_says_not_allowed(self) -> None:
        base = f"/me/campaigns/{self.cref(self.two)}/game-masters"
        for path in (base, f"{base}/candidates"):
            got = await self.client.get(path)
            self.assertEqual((got.status_code, got.json()["error"]), (403, "not_allowed"))

    async def test_a_change_is_queued_over_http_and_the_refusals_have_their_own_codes(self) -> None:
        await self.agreed(DANA, "Dana")
        base = f"/me/campaigns/{self.cref()}/settings"
        made = await self.client.post(
            base, json={"setting": "co_dm", "value": str(DANA), "on": True}, headers=HEADERS
        )
        self.assertEqual(made.status_code, 202, made.text)
        got = await self.client.get(f"{base}/answers/{made.json()['answer']}")
        self.assertEqual((got.status_code, got.json()["setting"]), (200, "co_dm"))
        for body, status, code in (
            ({"setting": "co_dm", "value": str(ALICE.id), "on": False}, 409, "plan_holder"),
            ({"setting": "co_dm", "value": str(ELI), "on": True}, 400, "unknown_person"),
            ({"setting": "co_dm", "value": "nope", "on": True}, 400, "bad_value"),
        ):
            bad = await self.client.post(base, json=body, headers=HEADERS)
            self.assertEqual((bad.status_code, bad.json()["error"]), (status, code))

    async def test_a_bad_address_is_not_found(self) -> None:
        got = await self.client.get("/me/campaigns/nonsense/game-masters")
        self.assertEqual(got.status_code, 404)
