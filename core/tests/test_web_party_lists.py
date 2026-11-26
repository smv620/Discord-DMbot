"""The website's "who may know this" lists show the current party (#4778, migration 0551;
Supervisor's ruling on #4537): a player character who has left the party drops out of the lists
the DM picks from (the tick list, the "Some know" picker, the knowers page's player characters).
A character with no stretch at all stays in (as `party_record.current_members` says), and who
KNOWS a connection is untouched: a character who left still shows as knowing what it knew.
Postgres. Invented names."""

from __future__ import annotations

import time
from typing import Any

from dmbot.memory import party_icons
from dmbot.memory.models import DM as DM_SOURCE
from dmbot.web import party_characters, relationships, see_as
from dmbot.web.recaps import _scoped
from tests.test_web_relationships import Web


class PartyLists(Web):
    bob_has_a_hero = False  # (these tests count the campaign's player characters)

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.tavin = await self.add("Tavin", "character", role="player_character")
        self.heir = await self.add("The Hidden Heir", "character", role="npc")
        self.secret_link = await self.link(
            "ally_of", self.mira, self.heir, "mira-heir", secret=True
        )

    async def forget_spans(self) -> None:
        """No stretch at all for anyone (a character no route recorded)."""
        async with self.db.guild(self.g) as conn:
            await conn.execute("DELETE FROM memory_party_spans WHERE guild_id = %s", (self.g,))

    async def left_span(self, character: str) -> None:
        """Only an ended stretch for this character (it left; nothing is open)."""
        now = int(time.time())
        async with self.db.guild(self.g) as conn:
            await conn.execute(
                "INSERT INTO memory_party_spans (guild_id, campaign_id, id, entity_id, from_at,"
                " to_at, ended, source, created_at) VALUES (%s, %s,"
                " replace(gen_random_uuid()::text, '-', ''), %s, %s, %s, 'retired', 'dm', %s)",
                (self.g, self.one.id, character, now - 100, now, now),
            )

    async def leave(self, character: str) -> None:
        """Close this character's open stretch now ("retired"), as the DM's action does."""
        now = int(time.time())
        async with self.db.guild(self.g) as conn:
            cur = await conn.execute(
                "UPDATE memory_party_spans SET to_at = GREATEST(from_at, %s), ended = 'retired'"
                " WHERE guild_id = %s AND campaign_id = %s AND entity_id = %s AND to_at IS NULL",
                (now, self.g, self.one.id, character),
            )
            self.assertEqual(cur.rowcount, 1)  # (a made character has an open stretch)

    def names(self, got: dict[str, Any]) -> list[str]:
        return [c["name"] for c in got["characters"]]

    async def knowers_data(self) -> dict[str, Any]:
        async with self.web.user(self.session.user_id, session=self.session.id_hash) as conn:
            await _scoped(conn, self.g)
            cur = await conn.execute(
                "SELECT dmbot_web_knowers_data(%s, %s) AS r", (self.g, self.one.id)
            )
            found = await cur.fetchone()
        assert found is not None
        data: dict[str, Any] = found["r"]
        return data

    async def test_a_character_with_no_stretch_is_in_the_party_and_one_who_left_is_not(
        self,
    ) -> None:
        everyone = {self.rook, self.tavin}
        await self.forget_spans()
        pick = await party_characters.read(self.web, self.session, self.cref)
        tick = await relationships.read_characters(self.web, self.session, self.cref)
        self.assertEqual({c["id"] for c in pick["characters"]}, everyone)
        self.assertEqual({c["id"] for c in tick["characters"]}, everyone)
        self.assertEqual(set((await self.knowers_data())["playerCharacters"]), everyone)
        await self.left_span(self.tavin)  # (Rook has no stretch at all: still in the party)
        pick = await party_characters.read(self.web, self.session, self.cref)
        tick = await relationships.read_characters(self.web, self.session, self.cref)
        self.assertEqual(self.names(pick), ["Rook"])
        self.assertEqual(self.names(tick), ["Rook"])
        self.assertEqual((await self.knowers_data())["playerCharacters"], [self.rook])

    async def test_a_character_who_left_still_shows_as_knowing_what_it_knew(self) -> None:
        await self.memory.set_connection_knowers(
            self.g, self.one.id, self.secret_link, [self.rook, self.tavin],
            source=DM_SOURCE, dm_chose_secret=True,
        )  # fmt: skip
        await self.leave(self.tavin)
        got = await self.graph(hops=2)
        for edge in got["edges"]:
            for each in edge["links"]:
                if each["id"] == self.links["mira-heir"]:
                    self.assertEqual(
                        each["knownBy"],
                        [
                            {"id": self.rook, "name": "Rook", "status": "party"},
                            {"id": self.tavin, "name": "Tavin", "status": "left"},
                        ],
                    )
                    return
        self.fail("the connection is not in the answer")

    async def test_what_only_a_leaver_knows_is_worded_as_theirs_never_as_an_npcs(self) -> None:
        """Supervisor's ruling on #4782: "Known to a character who has left the party"."""
        await self.memory.set_connection_knowers(
            self.g, self.one.id, self.secret_link, [self.tavin],
            source=DM_SOURCE, dm_chose_secret=True,
        )  # fmt: skip
        await self.leave(self.tavin)
        self.assertEqual((await self.knowers_data())["leftParty"], [self.tavin])
        words = {
            x["id"]: x["knowWord"] for e in (await self.graph(hops=2))["edges"] for x in e["links"]
        }
        self.assertEqual(words[self.links["mira-heir"]], "left_the_party")
        # with an NPC beside the leaver, it is no longer only a leaver's
        await self.memory.set_connection_knowers(
            self.g, self.one.id, self.secret_link, [self.tavin, self.heir],
            source=DM_SOURCE, dm_chose_secret=True,
        )  # fmt: skip
        words = {
            x["id"]: x["knowWord"] for e in (await self.graph(hops=2))["edges"] for x in e["links"]
        }
        self.assertNotEqual(words[self.links["mira-heir"]], "left_the_party")
        # nobody who is in the party is listed as having left
        await self.forget_spans()
        self.assertEqual((await self.knowers_data())["leftParty"], [])

    async def lines(self) -> dict[str, str]:
        got = await self.graph(hops=2)
        return {x["id"]: x["knowLine"] for e in got["edges"] for x in e["links"]}

    async def test_the_website_says_who_knows_in_core_s_own_party_icons_line(self) -> None:
        """Supervisor's ruling on #4537 (§7): the same words as Discord, worked out against the
        current party, a leaver named with why, the line never stored."""
        link = self.links["mira-heir"]
        wren = await self.add("Wren", "character", role="player_character")
        await self.memory.set_connection_knowers(
            self.g, self.one.id, self.secret_link, [self.rook],
            source=DM_SOURCE, dm_chose_secret=True,
        )  # fmt: skip
        self.assertEqual(
            (await self.lines())[link],
            party_icons.line(
                [self.rook], [(self.rook, "Rook"), (wren, "Wren"), (self.tavin, "Tavin")]
            ),
        )
        self.assertEqual((await self.lines())[link], "👤 Known to: Rook")
        await self.memory.set_connection_knowers(
            self.g, self.one.id, self.secret_link, [self.tavin],
            source=DM_SOURCE, dm_chose_secret=True,
        )  # fmt: skip
        await self.leave(self.tavin)
        self.assertEqual(
            (await self.lines())[link],
            "🤫 No one in the party knows. Known to: Tavin (retired)",
        )
        await self.leave(wren)
        await self.memory.set_connection_knowers(
            self.g, self.one.id, self.secret_link, [self.rook],
            source=DM_SOURCE, dm_chose_secret=True,
        )  # fmt: skip
        self.assertEqual((await self.lines())[link], "✅ The whole party knows (Rook)")

    async def test_common_knowledge_a_shown_connection_and_the_party_order_read_as_in_discord(
        self,
    ) -> None:
        """What the first version got wrong: 🌍 (the raw "everyone" row, which the gate swaps for
        the party), a connection the table sees outright (✅, never 🤫), and the party in the order
        Discord lists it (oldest first), not A to Z."""
        plain = self.links["mira-keep"]
        self.assertEqual((await self.lines())[plain], party_icons.WHOLE)
        await self.memory.add_common_knowledge(
            self.g, self.one.id, "connection", [plain], source=DM_SOURCE
        )
        self.assertEqual((await self.lines())[plain], party_icons.EVERYONE)
        # a character made after the others, who comes first A to Z
        aaron = await self.add("Aaron", "character", role="player_character")
        # (made in the same second as Rook, so the party order would fall to a random id: the
        # order is "oldest first, then id" in Discord and on the website alike; the fixture sets
        # the age it means instead of leaving it to chance, #4828)
        await self.sql(
            "UPDATE memory_entities SET created_at = created_at + 100 WHERE id = %s", aaron
        )
        await self.memory.set_connection_knowers(
            self.g, self.one.id, self.secret_link, [aaron, self.rook],
            source=DM_SOURCE, dm_chose_secret=True,
        )  # fmt: skip
        got = (await self.lines())[self.links["mira-heir"]]
        self.assertIn(got, ("👥 Known to: Rook and Aaron",))


class PickerOrder(Web):
    """ "Who knows this?" and See as… list the party first, then those who left, then the rest (the
    order of Discord's pickers, #4778)."""

    bob_has_a_hero = False

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.tavin = await self.add("Tavin", "character", role="player_character")

    async def leave(self, character: str) -> None:
        now = int(time.time())
        async with self.db.guild(self.g) as conn:
            await conn.execute(
                "UPDATE memory_party_spans SET to_at = GREATEST(from_at, %s), ended = 'retired'"
                " WHERE guild_id = %s AND campaign_id = %s AND entity_id = %s AND to_at IS NULL",
                (now, self.g, self.one.id, character),
            )

    async def viewers(self) -> list[tuple[str, str, str | None]]:
        got = await see_as.read_viewers(self.web, self.session, self.cref, "")
        return [(v["name"], v["group"], v.get("note")) for v in got["viewers"]]

    async def test_the_party_comes_first_then_those_who_left_with_why_then_the_rest(self) -> None:
        await self.leave(self.tavin)
        got = await self.viewers()
        self.assertEqual(got[0], ("Rook", "party", None))
        self.assertEqual(got[1], ("Tavin", "left", party_icons.left_note("retired")))
        rest = got[2:]
        self.assertTrue(rest)
        self.assertEqual({g for _, g, _ in rest}, {"other"})
        self.assertEqual([n for n, _, _ in rest], sorted((n for n, _, _ in rest), key=str.casefold))

    async def test_with_nobody_gone_everyone_in_the_party_is_first_and_none_has_a_note(
        self,
    ) -> None:
        got = await self.viewers()
        groups = [g for _, g, _ in got]
        rank = {"party": 0, "left": 1, "other": 2}
        self.assertEqual(groups, sorted(groups, key=lambda g: rank[g]))
        self.assertEqual({n for n, g, _ in got if g == "party"}, {"Rook", "Tavin"})
        self.assertTrue(all(note is None for _, _, note in got))

    async def test_a_search_keeps_the_order(self) -> None:
        await self.leave(self.tavin)
        got = await see_as.read_viewers(self.web, self.session, self.cref, "a")
        names = [v["name"] for v in got["viewers"]]
        self.assertLess(names.index("Tavin"), names.index("Mira Voss"))

    async def test_a_leaver_whose_why_is_missing_says_left_the_party_and_the_cap_keeps_the_order(
        self,
    ) -> None:
        await self.leave(self.tavin)
        for n in range(30):
            await self.add(f"Extra {n:02d}", "character", role="npc")
        got = await see_as.read_viewers(self.web, self.session, self.cref, "")
        self.assertEqual(len(got["viewers"]), see_as.VIEWERS_SHOWN)
        self.assertEqual([v["group"] for v in got["viewers"][:2]], ["party", "left"])
        self.assertEqual(got["viewers"][1]["note"], party_icons.left_note("retired"))
        self.assertEqual(party_icons.left_note("unknown-why"), party_icons.left_note("left"))
