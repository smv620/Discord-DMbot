"""The website's two lists of who has played with whom (#4226): a DM's remembered players with
"Forget this player", and a player's "DMs who remember you" with Clear. The rows come from the
database's own trigger (migration 0450) when a player is linked to a character; these tests check
the web doors around it: who sees what, the answers' shapes, and the refusals. Real Postgres;
invented names and ids."""

from __future__ import annotations

from typing import Any

from dmbot.web import dm_players
from dmbot.web.recaps import RecapError
from tests.test_web_api import ALICE
from tests.test_web_relationships import BOB_USER, Web


class RememberedPlayers(Web):
    async def refusal(self, call: Any) -> str:
        with self.assertRaises(RecapError) as caught:
            await call
        return str(caught.exception.code)

    async def test_the_dm_sees_the_player_who_plays_a_character_and_only_that(self) -> None:
        got = await dm_players.remembered(self.web, self.session)
        (item,) = got["items"]
        self.assertEqual(item["player"], str(BOB_USER.id))
        self.assertEqual(set(item), {"player", "name", "since", "last", "campaigns"})
        self.assertEqual(item["campaigns"], 1)

    async def test_a_player_sees_the_dms_who_remember_them_and_not_their_own_list(self) -> None:
        got = await dm_players.remembered_by(self.web, self.player)
        (item,) = got["items"]
        self.assertEqual(item["dm"], str(ALICE.id))
        self.assertEqual(set(item), {"dm", "name", "since", "campaigns"})
        # Bob is nobody's DM, so his own list of players is empty.
        self.assertEqual((await dm_players.remembered(self.web, self.player))["items"], [])
        # And Alice is remembered by nobody.
        self.assertEqual((await dm_players.remembered_by(self.web, self.session))["items"], [])

    async def test_forget_removes_the_row_and_a_second_forget_says_gone(self) -> None:
        self.assertEqual(
            await dm_players.forget_player(self.web, self.session, str(BOB_USER.id)),
            {"removed": 1},
        )
        self.assertEqual((await dm_players.remembered(self.web, self.session))["items"], [])
        self.assertEqual(
            await self.refusal(dm_players.forget_player(self.web, self.session, str(BOB_USER.id))),
            "gone",
        )

    async def test_a_player_cannot_forget_for_a_dm(self) -> None:
        # Bob has no list, so forgetting "Alice" from it removes nothing, and Alice's list stays.
        self.assertEqual(
            await self.refusal(dm_players.forget_player(self.web, self.player, str(ALICE.id))),
            "gone",
        )
        self.assertEqual(len((await dm_players.remembered(self.web, self.session))["items"]), 1)

    async def test_clear_one_dm_or_all(self) -> None:
        self.assertEqual(
            await dm_players.clear(self.web, self.player, str(ALICE.id)), {"removed": 1}
        )
        self.assertEqual((await dm_players.remembered_by(self.web, self.player))["items"], [])
        self.assertEqual((await dm_players.remembered(self.web, self.session))["items"], [])
        self.assertEqual(await dm_players.clear(self.web, self.player, "all"), {"removed": 0})

    async def test_a_malformed_address_never_reaches_the_database(self) -> None:
        for bad in ("x", "-5", "0", "١٢٣", "9" * 30, ""):
            self.assertEqual(
                await self.refusal(dm_players.forget_player(self.web, self.session, bad)),
                "not_found",
                bad,
            )
            if bad:
                self.assertEqual(
                    await self.refusal(dm_players.clear(self.web, self.player, bad)),
                    "not_found",
                    bad,
                )
