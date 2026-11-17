"""Common knowledge on the website's who-knows door (#4546, migration 0522; #4537 slice 1a): a row
of the "everyone" point of view reaches the website's gate input as the character `@everyone`, but
only for a statement the players are shown by its own flags, so a name every player character knows
no longer reads as "only the DM", and a hidden one does not. Postgres. Invented names."""

from __future__ import annotations

from typing import Any

from dmbot.memory import gate
from dmbot.memory.gate import COMMON, ENTRY, Viewer
from dmbot.memory.models import DM as DM_SOURCE
from dmbot.web import graph_view
from dmbot.web.recaps import _scoped
from tests.test_web_relationships import Web


class CommonOnTheWeb(Web):
    bob_has_a_hero = False

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.tavin = await self.add("Tavin", "character", role="player_character")
        self.heir = await self.add("The Hidden Heir", "character", role="npc")
        self.plain_link = self.links["mira-keep"]
        self.secret_link = await self.link(
            "ally_of", self.mira, self.heir, "mira-heir", secret=True
        )

    async def door(self) -> dict[str, Any]:
        async with self.web.user(self.session.user_id, session=self.session.id_hash) as conn:
            await _scoped(conn, self.g)
            cur = await conn.execute(
                "SELECT dmbot_web_knowers_data(%s, %s) AS r", (self.g, self.one.id)
            )
            found = await cur.fetchone()
        assert found is not None
        data: dict[str, Any] = found["r"]
        return data

    async def common(self, kind: str, *ids: str) -> None:
        await self.memory.add_common_knowledge(
            self.g, self.one.id, kind, list(ids), source=DM_SOURCE
        )

    def rows(self, data: dict[str, Any], statement: str) -> list[str]:
        return [str(c) for _k, s, c, _x in data["knowers"] if s == statement]

    async def test_a_shown_name_with_a_common_row_is_listed_as_everyone(self) -> None:
        await self.common("entry", self.mira)
        self.assertEqual(self.rows(await self.door(), self.mira), [COMMON])

    async def test_the_gate_input_then_gives_every_player_character_the_name(self) -> None:
        await self.common("entry", self.mira)
        seen = gate.visible(
            Viewer.character(self.tavin), graph_view.statements_of(await self.door())
        )
        self.assertTrue(seen.knows(ENTRY, self.mira))
        party = gate.visible(Viewer.the_party(), graph_view.statements_of(await self.door()))
        self.assertTrue(party.knows(ENTRY, self.mira))

    async def test_a_name_the_dm_hid_from_the_players_is_not_listed(self) -> None:
        await self.common("entry", self.mira)
        await self.memory.set_hidden_from_players(
            self.g, self.one.id, self.mira, True, source=DM_SOURCE, dm_chose_secret=True
        )
        self.assertEqual(self.rows(await self.door(), self.mira), [])
        await self.memory.set_hidden_from_players(
            self.g, self.one.id, self.mira, False, source=DM_SOURCE, dm_chose_secret=True
        )
        self.assertEqual(self.rows(await self.door(), self.mira), [COMMON])  # (the row was kept)

    async def test_a_shown_connection_is_listed_and_a_secret_one_is_not(self) -> None:
        await self.common("connection", self.plain_link)
        self.assertEqual(self.rows(await self.door(), self.plain_link), [COMMON])
        await self.common("connection", self.secret_link)
        self.assertEqual(self.rows(await self.door(), self.secret_link), [])

    async def test_a_connection_with_an_end_kept_from_the_players_is_not_listed(self) -> None:
        await self.common("connection", self.plain_link)
        await self.memory.set_hidden_from_players(
            self.g, self.one.id, self.mira, True, source=DM_SOURCE, dm_chose_secret=True
        )
        self.assertEqual(self.rows(await self.door(), self.plain_link), [])

    async def test_an_entry_not_confirmed_or_limited_to_an_audience_is_not_listed(self) -> None:
        proposed = await self.add("Ten-Towns", "place", status="proposed")
        await self.common("entry", proposed)
        self.assertEqual(self.rows(await self.door(), proposed), [])
        async with self.db.guild(self.g) as conn:  # (a clue only some know: `audience_limited`)
            await conn.execute(
                "UPDATE memory_entities SET audience_limited = true WHERE id = %s", (self.mira,)
            )
        await self.common("entry", self.mira)
        self.assertEqual(self.rows(await self.door(), self.mira), [])

    async def test_an_other_name_is_listed_only_while_it_is_not_secret(self) -> None:
        shown = await self.memory.add_alias(
            self.g, self.one.id, self.mira, "Red Mira", kind="nickname", source=DM_SOURCE,
            status="confirmed",
        )  # fmt: skip
        hidden = await self.memory.add_alias(
            self.g, self.one.id, self.mira, "The Widow", kind="nickname", source=DM_SOURCE,
            status="confirmed", secret=True, dm_chose_secret=True,
        )  # fmt: skip
        await self.common("alias", shown.value.id, hidden.value.id)
        data = await self.door()
        self.assertEqual(self.rows(data, shown.value.id), [COMMON])
        self.assertEqual(self.rows(data, hidden.value.id), [])

    async def test_an_everyone_row_asleep_under_a_hide_is_not_listed(self) -> None:
        await self.common("entry", self.mira)
        async with self.db.guild(self.g) as conn:
            await conn.execute(
                "UPDATE memory_knowers SET hidden_by = ARRAY['x'] WHERE about_entity_id = %s",
                (self.mira,),
            )
        self.assertEqual(self.rows(await self.door(), self.mira), [])

    async def test_the_door_answers_a_player_with_the_same_rows_and_hides_what_is_hidden(
        self,
    ) -> None:
        await self.common("entry", self.mira)
        await self.common("connection", self.secret_link)
        async with self.web.user(self.player.user_id, session=self.player.id_hash) as conn:
            await _scoped(conn, self.g)
            cur = await conn.execute(
                "SELECT dmbot_web_knowers_data(%s, %s) AS r", (self.g, self.one.id)
            )
            found = await cur.fetchone()
        assert found is not None
        theirs: dict[str, Any] = found["r"]
        self.assertEqual(self.rows(theirs, self.mira), [COMMON])
        self.assertEqual(self.rows(theirs, self.secret_link), [])

    async def test_a_characters_own_row_still_comes_with_its_character(self) -> None:
        await self.memory.set_connection_knowers(
            self.g, self.one.id, self.secret_link, [self.tavin],
            source=DM_SOURCE, dm_chose_secret=True,
        )  # fmt: skip
        self.assertEqual(self.rows(await self.door(), self.secret_link), [self.tavin])
