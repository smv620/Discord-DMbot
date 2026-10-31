"""Supervisor's probes for #3085 as tests: nothing a hidden, unconfirmed or secret name could leak
through an unnamed person's label, a joined label or a connection's note reaches a player, on the
website's doors or in the reader, and the SQL label is the Python one. Invented names."""

from __future__ import annotations

from typing import Any

from dmbot.memory import unnamed
from dmbot.memory.gate import ALIAS, CONNECTION, ENTRY, Viewer
from tests.test_recap_web import BOB
from tests.test_web_my_characters_view import MyCharacters


class Probes(MyCharacters):
    async def everything(self) -> str:
        out = []
        for centre in (self.mira, None):
            kw: dict[str, Any] = {"hops": 8}
            if centre:
                kw["center"] = centre
            out.append(str(await self.graph(self.player, **kw)))
        out.append(str(await self.overview_names(self.player)))
        out.append(repr(await self.memory.what_my_characters_know(self.g, self.one.id, BOB.id)))
        return "\n".join(out)

    async def unnamed_on(self, anchor: str | None, what: str = "sister", know: bool = True) -> str:
        made = await self.memory.add_unnamed(
            self.g, self.one.id, what=what, role="sibling", anchor_id=anchor
        )
        sid = str(made.entity.id)
        await self.sql("UPDATE memory_entities SET status = 'confirmed' WHERE id = %s", sid)
        if know:
            await self.know(ENTRY, sid, [self.okra])
        return sid

    async def shown(self, ids: list[str]) -> dict[str, Any]:
        py = await self.memory.display_names(self.g, self.one.id, Viewer.player([self.okra]), ids)
        ov = await self.overview_names(self.player)
        return {i: (py.get(i), ov.get(i)) for i in ids}

    async def test_a_sister_of_a_hidden_ken_with_a_connection_leaks_nothing(self) -> None:
        s = await self.unnamed_on(self.ken)
        rel = await self.link("knows", s, self.mira, "s-m", secret=True)
        await self.know(CONNECTION, rel, [self.okra])
        self.assertEqual((await self.shown([s]))[s], ("someone's sister (name unknown)",) * 2)
        self.assertNotIn("Ken", await self.everything())

    async def test_a_sister_joined_into_a_hidden_lira_leaks_neither_name(self) -> None:
        lira = await self.add("Lira", "character", role="npc", hidden_from_players=True)
        s = await self.unnamed_on(self.ken)
        await self.memory.join_same_person(self.g, self.one.id, s, lira)
        found = await self.memory.aliases(self.g, self.one.id, entity_id=lira, include_secret=True)
        label = next(a.id for a in found if a.text.endswith("(name unknown)"))
        await self.know(ALIAS, label, [self.okra])
        await self.know(ENTRY, lira, [])
        everything = await self.everything()
        self.assertNotIn("Ken", everything)
        self.assertNotIn("Lira", everything)

    async def test_a_sister_of_a_friend_of_a_hidden_ken_leaks_nothing(self) -> None:
        friend = await self.unnamed_on(self.ken, what="friend", know=False)
        sister = await self.unnamed_on(friend)
        self.assertNotIn("Ken", await self.everything())
        await self.know(ENTRY, friend, [self.okra])
        self.assertNotIn("Ken", await self.everything())
        self.assertEqual((await self.shown([sister]))[sister][0], "someone's sister (name unknown)")

    async def test_an_anchor_known_by_another_name_or_another_player_leaks_nothing(self) -> None:
        await self.alias(self.ken, "Old Greycloak", secret=True)
        found = await self.memory.aliases(
            self.g, self.one.id, entity_id=self.ken, include_secret=True
        )
        grey = next(a.id for a in found if a.text == "Old Greycloak")
        await self.know(ALIAS, grey, [self.okra])
        await self.unnamed_on(self.ken)
        self.assertNotIn("Ken", await self.everything())
        await self.know(ENTRY, self.ken, [self.wen])  # only another player knows him by name
        self.assertNotIn("Ken", await self.everything())

    async def test_a_known_anchor_with_a_confidence_of_nothing_leaks_nothing(self) -> None:
        await self.unnamed_on(self.ken)
        await self.know(ENTRY, self.ken, [self.okra])
        await self.sql(
            "UPDATE memory_knowers SET confidence = 0 WHERE about_entity_id = %s", self.ken
        )
        self.assertNotIn("Ken", await self.everything())

    async def test_a_proposed_rejected_or_secretly_named_anchor_is_someone(self) -> None:
        for status, name in (("proposed", "Pending Pete"), ("rejected", "Rejected Rex")):
            anchor = await self.add(name, "character", role="npc")
            s = await self.unnamed_on(anchor)
            await self.sql("UPDATE memory_entities SET status = %s WHERE id = %s", status, anchor)
            self.assertEqual((await self.shown([s]))[s][1], "someone's sister (name unknown)")
        zed = await self.add("Zed", "character", role="npc")
        await self.alias(zed, "Zed", secret=True)
        s = await self.unnamed_on(zed)
        self.assertEqual((await self.shown([s]))[s][1], "someone's sister (name unknown)")

    async def test_a_note_on_an_unnamed_persons_connection_never_names_ken(self) -> None:
        s = await self.unnamed_on(self.ken)
        made = await self.memory.add_relation(
            self.g, self.one.id, s, "knows", self.mira, source="dm", confidence=1.0,
            status="confirmed", secret=True, detail="Ken's sibling",
        )  # fmt: skip
        await self.memory.set_connection_knowers(
            self.g, self.one.id, made.value[0].id, [self.okra], source="dm"
        )
        self.assertNotIn("Ken", await self.everything())

    async def test_the_sql_label_is_the_python_label(self) -> None:
        cases = [
            ("sister", "Ken"), ("the sister", "Boris"), ("an  heir ", "Ross  "),
            ("A sister", "x" * 45), ("sister", "abcdefghij" * 3 + "abcdefghis   x"),
            ("hooded figure", None), ("  The   old man ", "Thomas"), ("theory", "Ann"),
            ("sister\tof\nsorts", "Al"),
        ]  # fmt: skip
        async with self.db.guild(self.g) as conn:
            for what, anchor in cases:
                cur = await conn.execute("SELECT dmbot_unnamed_label(%s, %s) AS l", (what, anchor))
                row = await cur.fetchone()
                assert row is not None
                self.assertEqual(row["l"], unnamed.label(what, anchor), (what, anchor))
