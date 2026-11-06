"""Where a connection came from, for the graph's line panel (#2946): the DM gets the full value; a
player gets the session number and `unknown` for everything else (an upload's label can name a
private backstory). Invented names."""

from __future__ import annotations

import unittest
from typing import Any

from dmbot.web import link_sources
from tests.test_web_relationships import Web

LABEL = "Tavin's backstory, 24 Oct"


class Rule(unittest.TestCase):
    def test_the_dm_gets_each_kind_in_full(self) -> None:
        full = link_sources.source_value
        self.assertEqual(full({"source": "dm", "label": LABEL, "n": None}, True),
                         {"type": "upload", "label": LABEL})  # fmt: skip
        self.assertEqual(full({"source": "extractor", "label": None, "n": 4}, True),
                         {"type": "session", "n": 4})  # fmt: skip
        self.assertEqual(full({"source": "dm", "label": None, "n": None}, True), {"type": "dm"})
        self.assertEqual(full({"source": "background", "label": None, "n": None}, True),
                         {"type": "unknown"})  # fmt: skip
        self.assertEqual(full(None, True), {"type": "unknown"})

    def test_a_player_gets_the_session_and_never_a_label_or_the_dm(self) -> None:
        part = link_sources.source_value
        self.assertEqual(part({"source": "extractor", "label": None, "n": 4}, False),
                         {"type": "session", "n": 4})  # fmt: skip
        for row in (
            {"source": "dm", "label": LABEL, "n": None},
            {"source": "dm", "label": LABEL, "n": 2},  # (an upload that replaced a connection)
            {"source": "dm", "label": None, "n": None},
            None,
        ):
            self.assertEqual(part(row, False), {"type": "unknown"}, row)

    def test_no_word_of_an_upload_reaches_a_player(self) -> None:
        shown = str(link_sources.source_value({"source": "dm", "label": LABEL, "n": None}, False))
        self.assertNotIn("Tavin", shown)


class OnThePage(Web):
    async def sources(self, who: Any = None, **kw: Any) -> dict[str, dict[str, Any]]:
        got = await self.graph(who, **kw) if who is not None else await self.graph(**kw)
        return {x["id"]: x["source"] for e in got["edges"] for x in e["links"]}

    async def session_link(self) -> str:
        """A connection the story reader found in session 1 (a raw row: the reader's own path
        needs a whole transcript)."""
        link = self.links["mira-rook"]
        async with self.db.guild(self.g) as conn:
            await conn.execute(
                "INSERT INTO transcript_sessions (id, guild_id, campaign_id, started_at,"
                " line_count) VALUES ('s1', %s, %s, 1000, 1)",
                (self.g, self.one.id),
            )
            await conn.execute(
                "UPDATE memory_relations SET source = 'extractor', from_session_at = 1000"
                " WHERE guild_id = %s AND campaign_id = %s AND id = %s",
                (self.g, self.one.id, link),
            )
        return link

    async def test_an_empty_session_takes_no_number(self) -> None:
        # the Sessions list leaves out a session with no lines, so the panel must too
        session = await self.session_link()
        async with self.db.guild(self.g) as conn:
            await conn.execute(
                "INSERT INTO transcript_sessions (id, guild_id, campaign_id, started_at,"
                " line_count) VALUES ('s0', %s, %s, 500, 0)",
                (self.g, self.one.id),
            )
        got = await self.sources(center=self.mira, hops=1)
        self.assertEqual(got[session], {"type": "session", "n": 1})

    async def test_the_dm_sees_each_kind(self) -> None:
        uploaded, session = self.links["mira-keep"], await self.session_link()
        await self.memory.record_link_source(self.g, self.one.id, uploaded, LABEL)
        got = await self.sources(center=self.mira, hops=1)
        self.assertEqual(got[uploaded], {"type": "upload", "label": LABEL})
        self.assertEqual(got[session], {"type": "session", "n": 1})
        self.assertEqual(got[self.links["rook-keep"]], {"type": "dm"})

    async def test_a_player_gets_the_session_only(self) -> None:
        uploaded, session = self.links["mira-keep"], await self.session_link()
        await self.memory.record_link_source(self.g, self.one.id, uploaded, LABEL)
        got = await self.sources(self.player, center=self.mira, hops=1)
        self.assertEqual(got[session], {"type": "session", "n": 1})
        self.assertEqual(got[uploaded], {"type": "unknown"})
        self.assertEqual(got[self.links["rook-keep"]], {"type": "unknown"})
        self.assertNotIn("Tavin", str(got))

    async def test_players_view_for_a_dm_is_the_players(self) -> None:
        uploaded = self.links["mira-keep"]
        await self.memory.record_link_source(self.g, self.one.id, uploaded, LABEL)
        got = await self.sources(center=self.mira, hops=1, view="players")
        self.assertEqual(got[uploaded], {"type": "unknown"})

    async def test_another_campaigns_label_is_untouched(self) -> None:
        other = self.two
        made = await self.memory.add_entity(self.g, other.id, type="npc", name="Zed", source="dm")
        zed2 = await self.memory.add_entity(self.g, other.id, type="npc", name="Yra", source="dm")
        rel = await self.memory.add_relation(
            self.g, other.id, made.value.id, "knows", zed2.value.id, source="dm",
            confidence=1.0, status="confirmed",
        )  # fmt: skip
        await self.memory.record_link_source(self.g, other.id, rel.value[0].id, "Other upload")
        got = await self.sources(center=self.mira, hops=2)
        self.assertNotIn("Other upload", str(got))
