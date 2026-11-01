"""The DM's story graph seen as the party, a player character or an NPC (#2999): the DM's graph with
everything the viewer does not know removed, by the same gate the bot uses. DM only. Invented
names."""

from __future__ import annotations

from typing import Any

import httpx

from dmbot.memory import gate
from dmbot.memory.gate import ALIAS, CONNECTION, ENTRY
from dmbot.memory.models import DM as DM_SOURCE
from dmbot.memory.ontology import FALLBACK_PREDICATE
from dmbot.web import see_as, sessions
from dmbot.web.app import create_app
from dmbot.web.payments import FakeProvider
from dmbot.web.recaps import RecapError, _scoped
from tests.test_recap_web import BOB, NOW
from tests.test_web_api import API, THURSDAY, FakeDiscord, settings
from tests.test_web_relationships import Web


class SeeAs(Web):
    bob_has_a_hero = False  # (the party here is Rook and Okra, whom the tests set up)

    """Rook (a player character) knows Mira and her friendship with Rook; the Keep is the DM's
    alone. Okra is a second player character who knows nothing yet."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.okra = await self.add("Okra", "character", role="player_character")
        # (the fixture's characters start out knowing what the DM entered; start from nothing so
        # each test says exactly who knows what)
        async with self.db.guild(self.g) as conn:
            await conn.execute("DELETE FROM memory_knowers WHERE campaign_id = %s", (self.one.id,))
        await self.know(ENTRY, self.mira, [self.rook])
        await self.know(CONNECTION, self.links["mira-rook"], [self.rook])

    async def know(self, kind: str, statement: str, who: list[str]) -> None:
        await self.memory.set_statement_knowers(
            self.g, self.one.id, kind, statement, who, source=DM_SOURCE
        )

    async def seen(self, viewer: str, who: Any = None, **kw: Any) -> dict[str, Any]:
        return await see_as.read_see_as(
            self.web, who or self.session, self.cref, viewer, center=self.mira, hops=3, **kw
        )

    async def refused_see_as(self, viewer: str, who: Any = None, ref: str | None = None) -> str:
        with self.assertRaises(RecapError) as caught:
            await see_as.read_see_as(self.web, who or self.session, ref or self.cref, viewer)
        return caught.exception.code

    @staticmethod
    def names(got: dict[str, Any]) -> set[str]:
        return {n["name"] for n in got["nodes"]}


class WhatACharacterSees(SeeAs):
    async def test_a_player_character_sees_what_it_knows_and_nothing_else(self) -> None:
        got = await self.seen(self.rook)
        self.assertEqual(self.names(got), {"Rook", "Mira Voss"})
        (edge,) = got["edges"]
        self.assertEqual({edge["a"], edge["b"]}, {self.rook, self.mira})
        self.assertEqual([x["id"] for x in edge["links"]], [self.links["mira-rook"]])
        self.assertEqual(got["seeAs"], {"viewer": self.rook, "name": "Rook"})
        self.assertFalse(got["canEdit"])
        self.assertNotIn("Greywater Keep", str(got))

    async def test_an_npc_is_a_point_of_view_too(self) -> None:
        await self.know(ENTRY, self.keep, [self.mira])
        await self.know(CONNECTION, self.links["mira-keep"], [self.mira])
        got = await self.seen(self.mira)
        self.assertEqual(self.names(got), {"Mira Voss", "Greywater Keep"})
        self.assertEqual(got["seeAs"]["name"], "Mira Voss")

    async def test_the_party_sees_only_what_every_player_character_knows(self) -> None:
        alone = await self.seen(see_as.PARTY)
        self.assertEqual(self.names(alone), set())  # (Okra knows nothing of Mira)
        await self.know(ENTRY, self.mira, [self.rook, self.okra])
        await self.know(ENTRY, self.rook, [self.rook, self.okra])
        await self.know(CONNECTION, self.links["mira-rook"], [self.rook, self.okra])
        together = await self.seen(see_as.PARTY)
        self.assertEqual(self.names(together), {"Rook", "Mira Voss"})
        self.assertEqual(together["seeAs"]["name"], "The party")

    async def test_the_dm_marks_are_removed(self) -> None:
        secret = await self.link("knows", self.mira, self.rook, "secret", secret=True)
        await self.know(CONNECTION, secret, [self.rook])
        got = await self.seen(self.rook)
        links = [x for e in got["edges"] for x in e["links"]]
        self.assertEqual(len(links), 2)
        for link in links:
            self.assertEqual(
                (link["secret"], link["revealed"], link["pinned"], link["check"]),
                (False, True, False, ""),
            )
            self.assertNotIn("who", link)
            self.assertNotIn("knownBy", link)
        for node in got["nodes"]:
            self.assertTrue(node["revealed"])
            self.assertIsNone(node.get("secret"))


class AMainNameStaysSecret(SeeAs):
    async def test_knowing_a_connection_does_not_give_away_a_main_name(self) -> None:
        # Rook knows the Keep and Mira's link to it exists only through "mira-keep"; Rook does not
        # know Mira under her own name (no row of its own for her), only the connection.
        async with self.db.guild(self.g) as conn:  # (a name every player may read is shown)
            await conn.execute(
                "UPDATE memory_entities SET hidden_from_players = true WHERE id = %s", (self.mira,)
            )
        await self.know(ENTRY, self.mira, [])
        await self.know(CONNECTION, self.links["mira-keep"], [self.rook])
        got = await self.seen(self.rook)
        self.assertNotIn("Mira Voss", str(got))
        self.assertEqual(got["edges"], [])  # an edge to a name nobody may be shown is not drawn

    async def test_it_is_shown_by_the_other_name_the_viewer_knows(self) -> None:
        await self.know(ENTRY, self.mira, [])
        await self.know(CONNECTION, self.links["mira-keep"], [self.rook])
        made = await self.memory.add_alias(
            self.g, self.one.id, self.mira, "the ferrywoman",
            kind="nickname", source=DM_SOURCE, status="confirmed", secret=True,
        )  # fmt: skip
        alias = made.value.id
        await self.know(ALIAS, alias, [self.rook])
        got = await self.seen(self.rook)
        self.assertIn("the Ferrywoman", self.names(got))
        self.assertNotIn("Mira Voss", str(got))


class NothingSecretInTheWords(SeeAs):
    async def test_a_note_that_names_a_main_name_is_not_shown_with_another_name(self) -> None:
        await self.know(ENTRY, self.mira, [])
        made = await self.memory.add_alias(
            self.g, self.one.id, self.mira, "the ferrywoman",
            kind="nickname", source=DM_SOURCE, status="confirmed", secret=True,
        )  # fmt: skip
        await self.know(ALIAS, made.value.id, [self.rook])
        note = await self.memory.add_relation(
            self.g, self.one.id, self.rook, FALLBACK_PREDICATE, self.mira,
            source=DM_SOURCE, confidence=1.0, status="confirmed", detail="serves Mira Voss",
        )  # fmt: skip
        await self.know(CONNECTION, note.value[0].id, [self.rook])
        got = await self.seen(self.rook)
        self.assertEqual(len(got["edges"]), 1)
        self.assertNotIn("Mira Voss", str(got))
        self.assertNotIn("serves", str(got))

    async def test_a_secret_own_name_needs_a_row_on_that_name_itself(self) -> None:
        async with self.db.guild(self.g) as conn:
            await conn.execute(
                "UPDATE memory_aliases SET secret = true WHERE entity_id = %s AND key = %s",
                (self.mira, "mira voss"),
            )
        got = await self.seen(self.rook)  # (knows the entry, not its secret name)
        self.assertNotIn("Mira Voss", str(got))
        self.assertEqual(got["edges"], [])

    async def test_the_centre_and_the_npc_page_are_not_given_away(self) -> None:
        got = await self.seen(self.okra)  # Mira is asked for; Okra knows nothing of her
        self.assertNotEqual(got["center"], self.mira)
        self.assertNotIn("Mira Voss", str(got))
        self.assertTrue(all(n["page"] is False for n in got["nodes"]))


class NamedAsTheBotNamesThem(SeeAs):
    """Every name in the view is the one the bot's own `display_names` gives that viewer, for a
    campaign with a hidden person, a joined unnamed person, an unnamed person off a known name, a
    secret other name, a partial reveal and a sleeping knower."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.bruno = await self.add("Bruno", "character", role="npc", hidden_from_players=True)
        self.ken = await self.add("Ken", "character", role="npc", hidden_from_players=True)
        sister = await self.memory.add_unnamed(
            self.g, self.one.id, what="sister", role="sibling", anchor_id=self.bruno
        )
        self.sister = sister.entity.id
        brother = await self.memory.add_unnamed(
            self.g, self.one.id, what="brother", role="sibling", anchor_id=self.mira
        )
        self.brother = brother.entity.id
        self.sister_link = await self.link("knows", self.mira, self.sister, "sister")
        self.brother_link = await self.link("knows", self.mira, self.brother, "brother")
        for who in ([self.okra], [self.rook, self.okra]):
            await self.know(ENTRY, self.sister, who)
            await self.know(CONNECTION, self.sister_link, who)
        await self.know(ENTRY, self.mira, [self.okra])
        await self.know(ENTRY, self.brother, [self.okra])
        await self.know(CONNECTION, self.brother_link, [self.okra])
        await self.know(ENTRY, self.keep, [self.rook])  # (a partial reveal: Okra does not)
        await self.memory.join_same_person(self.g, self.one.id, self.sister, self.ken)
        made = await self.memory.add_alias(
            self.g, self.one.id, self.keep, "the old fort",
            kind="nickname", source=DM_SOURCE, status="confirmed", secret=True,
        )  # fmt: skip
        await self.know(ALIAS, made.value.id, [self.rook])
        async with self.db.guild(self.g) as conn:  # a knower put to sleep grants nothing
            await conn.execute(
                "UPDATE memory_knowers SET hidden_by = ARRAY['x'] WHERE campaign_id = %s"
                " AND about_entity_id = %s",
                (self.one.id, self.hall),
            )

    async def test_every_name_is_the_one_the_bot_gives(self) -> None:
        full = await self.graph(center=self.mira, hops=6, view="full")
        ids = [n["id"] for n in full["nodes"]]
        for label, viewer, who in (
            ("okra", self.okra, gate.Viewer.character(self.okra)),
            ("rook", self.rook, gate.Viewer.character(self.rook)),
            ("mira", self.mira, gate.Viewer.character(self.mira)),
            ("party", see_as.PARTY, gate.Viewer.the_party()),
        ):
            with self.subTest(label):
                expected = await self.memory.display_names(self.g, self.one.id, who, ids)
                got = await see_as.read_see_as(
                    self.web, self.session, self.cref, viewer, center=self.mira, hops=6
                )
                async with self.memory._read(self.g, self.one.id) as scope:
                    knows = await gate.visible_to(scope, who)
                shown = {
                    n["id"]: n["name"] for n in got["nodes"] if not n["id"].startswith("alias:")
                }
                aliased = [n["name"] for n in got["nodes"] if n["id"].startswith("alias:")]
                # the same people under the same names: known to the gate and nameable by it. A
                # person reached only through a fact learned by a secret other name has no node of
                # their own, so the node may be missing, but never differently named
                allowed = {
                    i: name
                    for i, name in expected.items()
                    if name is not None and knows.knows(gate.ENTRY, i)
                }
                for i, name in shown.items():
                    self.assertEqual(name, allowed[i])
                self.assertTrue(set(aliased) <= set(allowed.values()))
                links = {x["id"] for e in got["edges"] for x in e["links"]}
                self.assertTrue(all(knows.knows(gate.CONNECTION, i) for i in links))

    async def test_a_hidden_persons_name_never_comes_through_a_joined_label(self) -> None:
        got = await self.seen(self.okra)
        self.assertNotIn("Bruno", str(got))
        self.assertIn("someone's sister (name unknown)", self.names(got))


class WhoMayLook(SeeAs):
    async def test_a_player_is_refused_and_so_is_anyone_in_another_campaign(self) -> None:
        self.assertEqual(await self.refused_see_as(self.rook, self.player), "not_allowed")
        # (a campaign that is not here looks the same as one that is not yours)
        self.assertEqual(
            await self.refused_see_as(self.rook, ref=f"{THURSDAY.id}.{'0' * 32}"), "not_allowed"
        )

    async def test_a_name_that_is_not_a_character_here_is_not_found(self) -> None:
        for viewer in ("0" * 32, self.keep, "nonsense"):  # (a place is not a point of view)
            with self.subTest(viewer):
                self.assertEqual(await self.refused_see_as(viewer), "not_found")

    async def test_the_door_gives_a_player_no_picker_even_by_sql(self) -> None:
        async with self.web.user(self.player.user_id, session=self.player.id_hash) as conn:
            await _scoped(conn, THURSDAY.id)
            cur = await conn.execute(
                "SELECT dmbot_web_knowers_data(%s, %s) AS r", (THURSDAY.id, self.one.id)
            )
            row = await cur.fetchone()
            assert row is not None
            self.assertEqual((row["r"]["isDm"], row["r"]["viewers"]), (False, []))


class ThePicker(SeeAs):
    """The characters a DM may pick to see as (#2999)."""

    async def viewers(self, who: Any = None, query: str = "") -> dict[str, Any]:
        return await see_as.read_viewers(self.web, who or self.session, self.cref, query)

    async def test_a_dm_gets_player_characters_first_then_by_name(self) -> None:
        got = await self.viewers()
        names = [(v["kind"], v["name"]) for v in got["viewers"]]
        self.assertEqual(names, [("pc", "Okra"), ("pc", "Rook"), ("npc", "Mira Voss")])
        self.assertEqual(got["total"], 3)
        self.assertEqual(set(got["viewers"][0]), {"id", "name", "kind"})  # nothing about who plays

    async def test_a_search_narrows_a_big_cast_in_any_case(self) -> None:
        got = await self.viewers(query="  MIRA ")
        self.assertEqual([v["name"] for v in got["viewers"]], ["Mira Voss"])
        self.assertEqual((await self.viewers(query="zzz"))["viewers"], [])

    async def test_no_more_than_a_menu_full_is_offered_and_the_total_says_so(self) -> None:
        for n in range(30):
            await self.add(f"Extra {n:02d}", "character", role="npc")
        got = await self.viewers()
        self.assertEqual(len(got["viewers"]), see_as.VIEWERS_SHOWN)
        self.assertEqual(got["total"], 33)

    async def test_a_player_and_a_stranger_are_refused(self) -> None:
        for who, ref in ((self.player, None), (self.session, f"{THURSDAY.id}.{'0' * 32}")):
            with self.assertRaises(RecapError) as caught:
                await see_as.read_viewers(self.web, who, ref or self.cref)
            self.assertEqual(caught.exception.code, "not_allowed")


class TheKnowWords(SeeAs):
    """The word for each name and connection in the DM's full view, from who knows it (§9b)."""

    async def words(self) -> tuple[dict[str, str], dict[str, str]]:
        from dmbot.web import relationships

        got = await relationships.read_web(
            self.web, self.session, self.cref, center=self.mira, hops=3, view="full"
        )
        nodes = {n["name"]: n["knowWord"] for n in got["nodes"]}
        links = {x["id"]: x["knowWord"] for e in got["edges"] for x in e["links"]}
        return nodes, links

    async def test_a_name_nobody_but_the_dm_knows_is_secret_and_the_rest_are_worded_by_who(
        self,
    ) -> None:
        nodes, links = await self.words()
        self.assertEqual(nodes["Greywater Keep"], gate.SECRET)
        self.assertEqual(nodes["Mira Voss"], gate.PARTLY_REVEALED)  # (Rook knows her, Okra not)
        self.assertEqual(links[self.links["mira-rook"]], gate.PARTLY_REVEALED)
        await self.know(ENTRY, self.mira, [self.rook, self.okra])
        self.assertEqual((await self.words())[0]["Mira Voss"], gate.KNOWN_TO_THE_PARTY)

    async def test_what_only_npcs_know_is_hidden_from_the_players_not_secret(self) -> None:
        tran = await self.add("Tran", "character", role="npc")
        await self.know(ENTRY, self.keep, [tran])
        self.assertEqual((await self.words())[0]["Greywater Keep"], gate.KNOWN_TO_ONE_NPC)
        bruno = await self.add("Bruno", "character", role="npc")
        await self.know(ENTRY, self.keep, [tran, bruno])
        self.assertEqual((await self.words())[0]["Greywater Keep"], gate.HIDDEN_FROM_THE_PLAYERS)

    async def test_the_players_view_is_never_sent_the_words(self) -> None:
        from dmbot.web import relationships

        await self.know(ENTRY, self.mira, [self.rook, self.okra])  # (the party knows her)
        got = await relationships.read_web(
            self.web, self.session, self.cref, center=self.mira, hops=3, view="players"
        )
        self.assertTrue(got["nodes"])
        self.assertTrue(all("knowWord" not in n for n in got["nodes"]))
        self.assertTrue(all("knowWord" not in x for e in got["edges"] for x in e["links"]))


class TheKnowWordsOnTheTray(SeeAs):
    """The overview's "Not connected yet" tray says who knows each name too (#3168)."""

    async def tray(self, **kw: Any) -> list[dict[str, Any]]:
        from dmbot.web import relationships

        got = await relationships.read_overview(self.web, self.session, self.cref, **kw)
        return list(got["tray"])

    async def test_the_dm_full_view_tray_has_a_word_for_each_name(self) -> None:
        lone = await self.add("Lone Wolf", "character", role="npc")
        await self.know(ENTRY, lone, [self.rook])
        hidden = await self.add("Hidden Hermit", "character", role="npc")
        await self.know(ENTRY, hidden, [])  # (nobody but the DM knows this one)
        tray = {n["name"]: n.get("knowWord") for n in await self.tray(view="full")}
        self.assertEqual(tray["Lone Wolf"], gate.PARTLY_REVEALED)
        self.assertEqual(tray["Hidden Hermit"], gate.SECRET)
        self.assertNotIn("Mira Voss", tray)  # (she is connected, so she is not in the tray)

    async def test_the_players_view_tray_has_no_words(self) -> None:
        lone = await self.add("Lone Wolf", "character", role="npc")
        await self.know(ENTRY, lone, [self.rook, self.okra])
        tray = await self.tray(view="players")
        self.assertIn("Lone Wolf", {n["name"] for n in tray})
        self.assertTrue(all("knowWord" not in n for n in tray))

    async def test_a_player_gets_no_words(self) -> None:
        from dmbot.web import relationships

        lone = await self.add("Lone Wolf", "character", role="npc")
        await self.know(ENTRY, lone, [self.rook, self.okra])
        got = await relationships.read_overview(self.web, self.player, self.cref)
        self.assertTrue(all("knowWord" not in n for n in got["tray"]))


class TheKnowWordsAlone(SeeAs):
    """`know_words` and `with_know_words` on their own: groups, sleeping rows, who may ask."""

    async def test_a_group_the_party_belongs_to_makes_it_known_to_the_party(self) -> None:
        from dmbot.web import relationships

        base = {
            "playerCharacters": ["pc1", "pc2"],
            "liveEntries": ["e1", "e2"],
            "connections": {"c1": ["e1", "e2"]},
            "states": {},
            "aliases": {},
            "groups": [["pc1", "g"], ["pc2", "g"]],
        }
        got = relationships.know_words({**base, "knowers": [["entry", "e1", "g", 1.0]]})
        self.assertEqual(got[0], {"e1": gate.KNOWN_TO_THE_PARTY, "e2": gate.SECRET})
        # (a member's own row wins, and a group's row reaches a member who has none)
        got = relationships.know_words(
            {**base, "knowers": [["entry", "e1", "g", 1.0], ["entry", "e1", "pc1", 0.5]]}
        )
        self.assertEqual(got[0]["e1"], gate.KNOWN_TO_THE_PARTY)
        got = relationships.know_words({**base, "knowers": [["connection", "c1", "npc", 1.0]]})
        self.assertEqual(got[1], {"c1": gate.KNOWN_TO_ONE_NPC})  # (an entry id is not a link id)
        got = relationships.know_words({**base, "knowers": [["property", "e1", "pc1", 1.0]]})
        self.assertEqual(got[0]["e1"], gate.SECRET)  # (a property is not the entry)

    async def test_a_name_not_in_the_data_gets_no_word(self) -> None:
        from dmbot.web import relationships

        page = {"nodes": [{"id": "new"}], "edges": []}
        await relationships.with_know_words(self.web, self.session, self.cref, page)
        self.assertNotIn("knowWord", page["nodes"][0])

    async def test_someone_who_is_not_the_dm_gets_no_words(self) -> None:
        from dmbot.web import relationships

        page = {"nodes": [{"id": self.mira}], "edges": []}
        await relationships.with_know_words(self.web, self.player, self.cref, page)
        self.assertNotIn("knowWord", page["nodes"][0])


class TheRoute(SeeAs):
    async def client(self, user: Any) -> httpx.AsyncClient:
        app = create_app(
            settings(), self.web, FakeDiscord(), payments=FakeProvider(b"h" * 32, "https://x"),
            clock=lambda: NOW,
        )  # fmt: skip
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=API,
            headers={"X-DMbot-Request": "1"},
        )  # fmt: skip
        token = await sessions.sign_in(self.web, user, [THURSDAY], now=NOW, days=30)
        client.cookies.set("__Host-dmbot_session", token, domain="api.dmbot.example")
        return client

    async def test_the_dm_gets_the_view_and_a_player_does_not(self) -> None:
        from tests.test_web_api import ALICE

        path = f"/me/campaigns/{self.cref}/web/see-as"
        async with await self.client(ALICE) as client:
            ok = await client.get(path, params={"viewer": self.rook, "center": self.mira})
            self.assertEqual(ok.status_code, 200)
            self.assertEqual({n["name"] for n in ok.json()["nodes"]}, {"Rook", "Mira Voss"})
            missing = await client.get(path, params={"viewer": "0" * 32})
            self.assertEqual(missing.status_code, 404)
            nothing = await client.get(path)
            self.assertEqual(nothing.status_code, 422)
        async with await self.client(BOB) as client:
            refused = await client.get(path, params={"viewer": self.rook})
            self.assertEqual(refused.status_code, 403)

    async def test_the_picker_list_is_for_the_dm_only(self) -> None:
        from tests.test_web_api import ALICE

        path = f"/me/campaigns/{self.cref}/web/viewers"
        async with await self.client(ALICE) as client:
            ok = await client.get(path)
            self.assertEqual(ok.status_code, 200)
            self.assertEqual(ok.json()["total"], 3)
            narrowed = await client.get(path, params={"q": "mira"})
            self.assertEqual([v["name"] for v in narrowed.json()["viewers"]], ["Mira Voss"])
        async with await self.client(BOB) as client:
            self.assertEqual((await client.get(path)).status_code, 403)
