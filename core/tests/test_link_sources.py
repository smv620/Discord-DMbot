"""Where a connection came from, for the graph's line panel (#2946): the DM gets the full value; a
player gets the session number and `unknown` for everything else (an upload's label can name a
private backstory). Invented names."""

from __future__ import annotations

import unittest
from typing import Any

from dmbot.web import link_sources
from dmbot.web.recaps import _scoped
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


class OnTheDoor(Web):
    """The door itself decides who is given the label and reads only the connections asked for."""

    async def door(self, who: Any, ids: list[str]) -> dict[str, dict[str, Any]]:
        async with self.web.user(who.user_id, session=who.id_hash) as conn:
            await _scoped(conn, self.g)
            cur = await conn.execute(
                "SELECT dmbot_web_link_sources(%s, %s, %s) AS s", (self.g, self.one.id, ids)
            )
            found = await cur.fetchone()
        assert found is not None and found["s"] is not None
        return {str(r["id"]): r for r in found["s"]["links"]}

    async def test_a_player_calling_the_door_is_never_given_the_label(self) -> None:
        link = self.links["mira-keep"]
        await self.memory.record_link_source(self.g, self.one.id, link, LABEL)
        mine = await self.door(self.session, [link])
        theirs = await self.door(self.player, [link])
        self.assertEqual(mine[link]["label"], LABEL)
        self.assertIsNone(theirs[link]["label"])
        self.assertNotIn("Tavin", str(theirs))

    async def test_the_quote_goes_to_the_dm_alone(self) -> None:
        link = self.links["mira-keep"]
        said = "Tavin and Voss argued all night"
        await self.memory.record_link_source(self.g, self.one.id, link, LABEL, said)
        mine = await self.door(self.session, [link])
        theirs = await self.door(self.player, [link])
        self.assertEqual(mine[link]["quote"], said)
        self.assertIsNone(theirs[link]["quote"])
        self.assertNotIn("argued", str(theirs))

    async def test_a_long_quote_is_cut_and_spaces_are_collapsed(self) -> None:
        link = self.links["mira-keep"]
        await self.memory.record_link_source(self.g, self.one.id, link, LABEL, "x" * 400)
        self.assertEqual(len((await self.door(self.session, [link]))[link]["quote"]), 240)
        plain = self.links["rook-keep"]
        await self.memory.record_link_source(self.g, self.one.id, plain, LABEL, "a   b \n c")
        self.assertEqual((await self.door(self.session, [plain]))[plain]["quote"], "a b c")
        none = self.links["mira-rook"]
        await self.memory.record_link_source(self.g, self.one.id, none, LABEL)
        self.assertEqual((await self.door(self.session, [none]))[none]["quote"], "")  # (none given)

    async def test_only_the_asked_for_connections_are_read(self) -> None:
        one, two = self.links["mira-keep"], self.links["mira-rook"]
        got = await self.door(self.session, [one])
        self.assertEqual(set(got), {one})
        self.assertEqual(set(await self.door(self.session, [one, two])), {one, two})
        self.assertEqual(await self.door(self.session, []), {})


class OnTheAttach(unittest.TestCase):
    """`attach` sets the quote on a connection only for the DM's full view, and only when stored."""

    def page(self) -> dict[str, Any]:
        return {"edges": [{"links": [{"id": "a"}, {"id": "b"}]}]}

    def test_the_dm_gets_the_quote_where_there_is_one(self) -> None:
        page = self.page()
        rows = {"a": {"source": "background", "label": "Upload", "quote": "they argued"}, "b": {}}
        link_sources.attach(page, rows, True)
        first, second = page["edges"][0]["links"]
        self.assertEqual(first["quote"], "they argued")
        self.assertNotIn("quote", second)

    def test_nobody_else_is_given_it_even_if_the_door_sent_it(self) -> None:
        page = self.page()
        rows = {"a": {"source": "background", "label": "Upload", "quote": "they argued"}}
        link_sources.attach(page, rows, False)
        self.assertNotIn("quote", page["edges"][0]["links"][0])
        self.assertNotIn("argued", str(page))
