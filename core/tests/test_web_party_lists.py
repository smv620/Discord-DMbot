"""The website's "who may know this" lists show the current party (#4778, migration 0551;
Supervisor's ruling on #4537): a player character who has left the party drops out of the lists
the DM picks from (the tick list, the "Some know" picker, the knowers page's player characters).
A character with no stretch at all stays in (as `party_record.current_members` says), and who
KNOWS a connection is untouched: a character who left still shows as knowing what it knew.
Postgres. Invented names."""

from __future__ import annotations

import time
from typing import Any

from dmbot.memory.models import DM as DM_SOURCE
from dmbot.web import party_characters, relationships
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
                        [{"id": self.rook, "name": "Rook"}, {"id": self.tavin, "name": "Tavin"}],
                    )
                    return
        self.fail("the connection is not in the answer")
