"""A campaign's game masters on the website's Settings tab (#2768): who they are, and who can be
offered as one more. Read only, DM only, and never anyone who has not agreed to be recorded.
Invented names throughout."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from dmbot.campaigns.store import MAX_DMS
from dmbot.members import MemberDirectory
from dmbot.web import game_masters, sessions
from dmbot.web.discord import DiscordGuild
from dmbot.web.recaps import RecapError
from tests.test_recap_web import BOB, NOW, Base
from tests.test_web_api import ALICE, THURSDAY

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
