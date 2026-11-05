"""The counts the story summary's rank is worked out from (#3236): ids, kinds, statuses, plain
connections, the newest assessment and the sessions heard in; counts and dates only, for members of
the server. Run as the website's own database role; invented names."""

from __future__ import annotations

from typing import Any

from dmbot.web import rank_inputs
from dmbot.web.recaps import RecapError
from tests.test_recap_web import BOB
from tests.test_web_api import QUILLON
from tests.test_web_relationships import Web


class RankInputs(Web):
    @staticmethod
    def joined(got: dict[str, Any]) -> set[tuple[str, frozenset[str]]]:
        """The connections as (kind, the two ends): a tie that reads the same both ways is stored in
        one fixed direction, so the tests do not depend on which."""
        return {(str(p), frozenset({str(s), str(o)})) for s, o, p in got["relations"]}

    async def read(self, who: Any = None) -> dict[str, Any]:
        return await rank_inputs.read_rank_inputs(self.web, who or self.session, self.cref)

    async def test_it_carries_ids_kinds_and_the_plain_connections_and_no_word_of_the_story(
        self,
    ) -> None:
        got = await self.read()
        ids = {e["id"]: e for e in got["entities"]}
        self.assertIn(self.rook, ids)
        self.assertEqual(ids[self.rook]["role"], "player_character")
        self.assertEqual(set(ids[self.rook]), {"id", "type", "role", "status", "mergedInto"})
        self.assertIn(("ally_of", frozenset({self.mira, self.rook})), self.joined(got))
        self.assertFalse(got["limited"])
        self.assertEqual(
            set(got), {"status", "limited", "entities", "relations", "assessed", "heard"}
        )

    async def test_a_player_may_read_it_too_as_the_graph_source_is_open_to_members(self) -> None:
        self.assertEqual(
            (await self.read(self.player))["entities"], (await self.read())["entities"]
        )

    async def test_the_newest_assessment_and_the_sessions_heard_come_back(self) -> None:
        async with self.db.guild(self.g) as conn:
            for rank in (1, 3):
                await conn.execute(
                    "INSERT INTO memory_importance (guild_id, campaign_id, entity_id, rank, basis,"
                    " source, recorded_at) VALUES (%s, %s, %s, %s, 'recurring', 'rule', 1)",
                    (self.g, self.one.id, self.mira, rank),
                )
            for session_at in (100, 100, 200):
                await conn.execute(
                    "INSERT INTO memory_heard (guild_id, campaign_id, entity_id,"
                    " session_started_at, speaker_id, times) VALUES (%s, %s, %s, %s, %s, 1)"
                    " ON CONFLICT DO NOTHING",
                    (self.g, self.one.id, self.mira, session_at, 7),
                )
        got = await self.read()
        assessed = {e: (seq, rank) for e, seq, rank in got["assessed"]}
        self.assertEqual(assessed[self.mira][1], 3)
        heard = [at for e, at in got["heard"] if e == self.mira]
        self.assertEqual(heard, [100, 200])

    async def test_an_ended_connection_a_players_plays_and_the_party_s_are_left_out(self) -> None:
        async with self.db.guild(self.g) as conn:
            await conn.execute(
                "UPDATE memory_relations SET to_session_at = 5 WHERE guild_id = %s AND id = %s",
                (self.g, self.links["mira-keep"]),
            )
        got = await self.read()
        joined = self.joined(got)
        self.assertNotIn(("knows", frozenset({self.mira, self.keep})), joined)
        self.assertIn(("ally_of", frozenset({self.mira, self.rook})), joined)

    async def test_an_outsider_and_another_campaign_are_not_found(self) -> None:
        outsider = await self.signed(BOB, QUILLON)
        with self.assertRaises(RecapError) as caught:
            await self.read(outsider)
        self.assertEqual(caught.exception.code, "not_found")
        with self.assertRaises(RecapError) as caught:
            await rank_inputs.read_rank_inputs(self.web, self.player, f"{self.g}.{'f' * 32}")
        self.assertEqual(caught.exception.code, "not_found")
