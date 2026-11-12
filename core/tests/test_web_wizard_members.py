"""Who a character's player can be in the wizard's step 3 (#4204, migration 0441): this server's
members by name, those who played with this DM here before first, only this server's, only those
recorded now. Run as the website's own database role; real Postgres; invented names."""

from __future__ import annotations

from typing import Any

from dmbot.consent import TERMS_VERSION
from dmbot.memory.store import MemoryStore
from dmbot.web import wizard
from dmbot.web.recaps import RecapError
from tests.test_recap_web import BOB, NOW, Base
from tests.test_web_api import ALICE, THURSDAY

ERIC, ADA, ZOE, CARL = 4101, 4102, 4103, 4104


class WizardMembers(Base):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.g = THURSDAY.id
        self.memory = MemoryStore(self.db)

    async def member(
        self, user: int, name: str, *, agreed: bool = True, guild: int | None = None
    ) -> None:
        g = guild or self.g
        async with self.db.guild(g) as conn:
            await conn.execute(
                "INSERT INTO member_directory (guild_id, user_id, display_name, username,"
                " updated_at)"
                " VALUES (%s, %s, %s, '', %s) ON CONFLICT (guild_id, user_id) DO UPDATE SET"
                " display_name = EXCLUDED.display_name",
                (g, user, name, NOW),
            )
            if agreed:
                await conn.execute(
                    "INSERT INTO consent (guild_id, user_id, granted_at, terms_version)"
                    " VALUES (%s, %s, %s, %s) ON CONFLICT (guild_id, user_id) DO UPDATE SET"
                    " terms_version = EXCLUDED.terms_version",
                    (g, user, NOW, TERMS_VERSION),
                )

    async def played(self, user: int, campaign: Any = None) -> None:
        """This user plays a character in the campaign (Alice DMs `one`, Bob DMs `two`)."""
        c = campaign or self.one
        await self.memory.add_entity(
            c.guild_id, c.id, type="character", role="player_character", name=f"Hero of {user}",
            played_by=user, source="dm", status="confirmed",
        )  # fmt: skip

    async def listed(self, q: str = "", who: Any = None) -> list[dict[str, Any]]:
        return await wizard.members(self.web, who or self.session, self.g, q)

    async def test_it_lists_the_servers_recorded_members_by_name(self) -> None:
        await self.member(ERIC, "Eric")
        await self.member(ADA, "Ada")
        got = await self.listed()
        self.assertEqual([m["name"] for m in got], ["Ada", "Eric"])
        self.assertEqual(got[0], {"userId": str(ADA), "name": "Ada", "before": False})

    async def test_those_who_played_with_this_dm_here_come_first_and_are_marked(self) -> None:
        await self.member(ADA, "Ada")
        await self.member(ERIC, "Eric")
        await self.member(ZOE, "Zoe")
        await self.played(ZOE)
        got = await self.listed()
        self.assertEqual(
            [(m["name"], m["before"]) for m in got],
            [("Zoe", True), ("Ada", False), ("Eric", False)],
        )

    async def remember(self, dm: int, player: int) -> None:
        """A row of the DM's remembered players (0450) for someone from a campaign elsewhere: the
        table is written by the database's own trigger, so the test opens its gate the same way."""
        async with self.db.unscoped() as conn:
            await conn.execute("SELECT set_config('dmbot.owner_sync', 'dm_players_note', true)")
            await conn.execute(
                "INSERT INTO dm_players (dm_user_id, player_user_id, last_name, first_played_at,"
                " last_played_at, campaigns) VALUES (%s, %s, 'Elsewhere', 1, 1, 1)",
                (dm, player),
            )

    async def test_someone_the_dm_played_with_in_another_server_is_marked_and_first(self) -> None:
        await self.member(ADA, "Ada")
        await self.member(ZOE, "Zoe")
        await self.remember(ALICE.id, ZOE)  # (a campaign on some other server)
        got = await self.listed()
        self.assertEqual([(m["name"], m["before"]) for m in got], [("Zoe", True), ("Ada", False)])

    async def test_another_dms_remembered_player_is_not_marked_for_this_dm(self) -> None:
        await self.member(ZOE, "Zoe")
        await self.remember(BOB.id, ZOE)
        self.assertEqual([m["before"] for m in await self.listed()], [False])

    async def test_a_remembered_player_who_is_not_in_this_server_is_never_listed(self) -> None:
        await self.remember(ALICE.id, ZOE)  # (no member row for Zoe here)
        self.assertEqual(await self.listed(), [])

    async def test_someone_who_played_in_another_dms_campaign_is_not_marked(self) -> None:
        await self.member(CARL, "Carl")
        await self.played(CARL, self.two)  # (Bob's campaign: Alice is not its DM)
        self.assertEqual([m["before"] for m in await self.listed()], [False])

    async def test_nothing_from_another_server_is_read(self) -> None:
        await self.member(ERIC, "Eric", guild=999)  # (a member of some other server)
        self.assertEqual(await self.listed(), [])

    async def test_a_member_with_no_consent_row_is_offered_too(self) -> None:
        # (naming a character's player records nothing about the person: a new table's players
        # mostly have not agreed yet)
        await self.member(ERIC, "Eric", agreed=False)
        await self.member(ADA, "Ada")
        self.assertEqual([m["name"] for m in await self.listed()], ["Ada", "Eric"])

    async def test_a_search_matches_the_name(self) -> None:
        await self.member(ERIC, "Eric the Red")
        await self.member(ADA, "Ada")
        self.assertEqual([m["name"] for m in await self.listed("eri")], ["Eric the Red"])
        self.assertEqual(await self.listed("nobody"), [])

    async def test_it_is_empty_when_the_server_has_no_members_yet(self) -> None:
        self.assertEqual(await self.listed(), [])

    async def test_a_server_the_person_is_not_in_is_not_found(self) -> None:
        with self.assertRaises(RecapError) as caught:
            await wizard.members(self.web, self.session, 12345, "")
        self.assertEqual(caught.exception.code, "not_found")
        self.assertTrue(BOB.id != 0)  # (the helpers import Bob only for the shared setup)
