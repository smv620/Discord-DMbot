"""The Story Graph says how an item changed hands (#4122, the web half of #4115): the words are
`memory/hand_over.history`'s (Discord's name card), built from the item's confirmed owns and
carries ties, the ended ones included, and only from ties the viewer knows. The source door sends
when each tie began and ended and why (0428). Real Postgres; invented names."""

from __future__ import annotations

from typing import Any

from dmbot.memory.gate import CONNECTION, ENTRY
from tests.test_web_player_gate import OneCampaign


class ItemHistory(OneCampaign):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.brand = await self.add("Frost Brand", "item")
        self.first = await self.link("owns", self.wen, self.brand, "wen-brand")
        self.second = await self.link("owns", self.okra, self.brand, "okra-brand")
        await self.sql(
            "UPDATE memory_relations SET from_session_at = 1000 WHERE id = %s", self.first
        )
        await self.sql(
            "UPDATE memory_relations SET from_session_at = 2000 WHERE id = %s", self.second
        )
        # new things start out told to the player characters: say they are only Wen's to start
        for kind, statement in (
            (ENTRY, self.brand),
            (CONNECTION, self.first),
            (CONNECTION, self.second),
        ):
            await self.know(kind, statement, [self.wen])

    async def end_first(self, reason: str | None = "sold") -> None:
        await self.sql(
            "UPDATE memory_relations SET to_session_at = 2000, end_reason = %s WHERE id = %s",
            reason,
            self.first,
        )

    async def told_to_okra(self, *statements: tuple[str, str]) -> None:
        for kind, statement in statements:
            await self.know(kind, statement, [self.okra])

    def node(self, page: dict[str, Any]) -> dict[str, Any] | None:
        return next((n for n in page["nodes"] if n["id"] == self.brand), None)

    async def test_the_dm_is_told_how_it_changed_hands(self) -> None:
        await self.end_first()
        got = await self.graph(self.session, view="full", center=self.brand, hops=3)
        node = self.node(got)
        assert node is not None
        self.assertEqual(node["history"], "Wen sold it to Okra · Okra has it now")

    async def test_each_reason_has_its_words(self) -> None:
        for reason, words in (
            ("gave", "Wen gave it to Okra · Okra has it now"),
            ("traded", "Wen traded it to Okra · Okra has it now"),
            (None, "Wen used to have it · Okra has it now"),
        ):
            await self.end_first(reason)
            node = self.node(await self.graph(self.session, view="full", center=self.brand, hops=3))
            assert node is not None
            self.assertEqual(node["history"], words, reason)

    async def test_an_item_nobody_let_go_of_has_no_history(self) -> None:
        node = self.node(await self.graph(self.session, view="full", center=self.brand, hops=3))
        assert node is not None
        self.assertNotIn("history", node)

    async def test_a_player_is_told_only_from_ties_their_characters_know(self) -> None:
        await self.end_first()
        known = (
            (ENTRY, self.brand), (ENTRY, self.wen),
            (CONNECTION, self.first), (CONNECTION, self.second),
        )  # fmt: skip
        await self.told_to_okra(*known)
        node = self.node(await self.full_page(center=self.brand, hops=3))
        assert node is not None
        self.assertEqual(node["history"], "Wen sold it to Okra · Okra has it now")

    async def test_a_tie_the_player_does_not_know_is_not_told(self) -> None:
        await self.end_first()
        # the item, Okra and their own tie are known; Wen's ended tie is not
        await self.told_to_okra((ENTRY, self.brand), (CONNECTION, self.second))
        node = self.node(await self.full_page(center=self.brand, hops=3))
        assert node is not None
        self.assertNotIn("history", node)

    async def test_a_holder_the_player_is_not_shown_is_left_out_never_replaced(self) -> None:
        # Bruno is hidden from the players: Okra is told of the sale and of the item, never of Bruno
        sold = await self.link("owns", self.bruno, self.brand, "bruno-brand")
        await self.sql(
            "UPDATE memory_relations SET from_session_at = 500, to_session_at = 1500,"
            " end_reason = 'sold' WHERE id = %s",
            sold,
        )
        await self.sql("DELETE FROM memory_relations WHERE id = %s", self.first)
        await self.told_to_okra((ENTRY, self.brand), (CONNECTION, sold), (CONNECTION, self.second))
        node = self.node(await self.full_page(center=self.brand, hops=3))
        assert node is not None
        self.assertEqual(node["history"], "Okra has it now")
        self.assertNotIn("Bruno", node["history"])

    async def test_a_secret_tie_is_told_to_the_dm_only(self) -> None:
        await self.end_first()
        await self.sql("UPDATE memory_relations SET secret = TRUE WHERE id = %s", self.first)
        await self.told_to_okra(
            (ENTRY, self.brand), (ENTRY, self.wen), (CONNECTION, self.first),
            (CONNECTION, self.second),
        )  # fmt: skip
        dm = self.node(await self.graph(self.session, view="full", center=self.brand, hops=3))
        assert dm is not None
        self.assertEqual(dm["history"], "Wen sold it to Okra · Okra has it now")
        player = self.node(await self.full_page(center=self.brand, hops=3))
        assert player is not None
        self.assertNotIn("history", player)  # (the secret sale is left out: nothing ended is shown)

    async def test_the_dms_players_view_is_the_players_words_not_the_dms(self) -> None:
        await self.end_first()
        # nobody but Wen is told of the sale: in Players View the DM sees what a player sees
        node = self.node(await self.graph(self.session, view="players", center=self.brand, hops=3))
        assert node is None or "history" not in node

    async def test_the_two_paths_word_the_same_history_the_same(self) -> None:
        await self.end_first("traded")
        await self.told_to_okra(
            (ENTRY, self.brand), (ENTRY, self.wen), (CONNECTION, self.first),
            (CONNECTION, self.second),
        )  # fmt: skip
        dm = self.node(await self.graph(self.session, view="full", center=self.brand, hops=3))
        player = self.node(await self.full_page(center=self.brand, hops=3))
        assert dm is not None and player is not None
        self.assertEqual(dm["history"], player["history"])

    async def test_the_item_ties_door_gives_nothing_to_a_player(self) -> None:
        async with self.web.user(self.player.user_id, session=self.player.id_hash) as conn:
            await conn.execute("SELECT set_config('dmbot.guild_id', %s, true)", (str(self.g),))
            cur = await conn.execute(
                "SELECT dmbot_web_item_ties(%s, %s, %s) AS r", (self.g, self.one.id, [self.brand])
            )
            found = await cur.fetchone()
        assert found is not None
        self.assertIsNone(found["r"])
