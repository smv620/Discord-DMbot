"""The first meeting's feeling reminder on the website (#4942, migration 0592): the door gives a DM
the facts for the waiting claims asked, the website's line is the one Discord's store works out, a
feeling DMbot only read gives none, and nobody but a DM gets the facts. Invented names. Needs
Postgres."""

from __future__ import annotations

from typing import Any

from dmbot.memory import bias_read, standing
from dmbot.memory.claims import ClaimStore, Extracted
from dmbot.memory.models import CONFIRMED, SPECIES
from dmbot.web import first_feelings, sessions
from dmbot.web.recaps import _scoped
from tests.test_recap_web import BOB, NOW
from tests.test_web_api import THURSDAY
from tests.test_web_relationships import Web


class Reminder(Web):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.claims = ClaimStore(self.memory)
        self.tala = await self.add("Tala", "character")
        self.brew = await self.add("Brewner", "character")
        self.keep = await self.add("Mithral Keep", "place")
        await self.memory.add_relation(
            self.g, self.one.id, self.tala, "located_in", self.keep, source="dm",
            confidence=1.0, status=CONFIRMED, detail="lives in",
        )  # fmt: skip
        await self.memory.set_rule_link(
            self.g, self.one.id, self.brew, SPECIES, "Dwarf", source="dm"
        )
        await self.claims.propose(
            self.g, self.one.id, [Extracted("Tala", "knows", "Brewner", "", ("1",), "dm_said")],
            {"1": 7}, opted_in={7}, prompt_version="p", model="m", session_started_at=1,
        )  # fmt: skip
        self.waiting_page = await self.claims.waiting(self.g, self.one.id, opted_in={7})

    async def feel(self, *, keep: bool) -> None:
        found = [bias_read.Found(self.keep, standing.UNFRIENDLY, lineage="dwarf")]
        await self.memory.add_read_biases(self.g, self.one.id, found, source="background")
        if keep:
            (note,) = [
                n for n in await self.memory.world_notes(self.g, self.one.id) if n.kind == "bias"
            ]
            await self.memory.keep_world_notes(self.g, self.one.id, [note.id], source="dm")

    async def door(self, who: Any = None) -> Any:
        ids = [c.id for c in self.waiting_page]
        async with self.web.user(
            (who or self.session).user_id, session=(who or self.session).id_hash
        ) as conn:
            await _scoped(conn, self.g)
            cur = await conn.execute(
                "SELECT dmbot_web_first_feeling_info(%s, %s, %s) AS r", (self.g, self.one.id, ids)
            )
            return (await cur.fetchone() or {}).get("r")


class TheLine(Reminder):
    async def test_the_websites_line_is_the_one_discord_works_out(self) -> None:
        await self.feel(keep=True)
        mine = first_feelings.lines_by_claim(await self.door())
        theirs = await self.claims.starting_feelings(self.g, self.one.id, self.waiting_page)
        self.assertEqual(mine, theirs)
        (line,) = mine.values()
        self.assertIn("**Mithral Keep** (Tala's home) is unfriendly toward dwarves", line)
        self.assertIn("**Brewner** is a dwarf. You decide how they feel.", line)

    async def test_a_feeling_dmbot_only_read_gives_no_line(self) -> None:
        await self.feel(keep=False)
        self.assertEqual(first_feelings.lines_by_claim(await self.door()), {})

    async def test_two_once_connected_get_no_line(self) -> None:
        await self.feel(keep=True)
        await self.memory.add_relation(
            self.g, self.one.id, self.tala, "knows", self.brew, source="dm", confidence=1.0,
            status=CONFIRMED,
        )  # fmt: skip
        self.assertEqual(first_feelings.lines_by_claim(await self.door()), {})

    async def test_only_a_dm_gets_the_facts_and_no_ids_means_nothing(self) -> None:
        who: Any = BOB
        bob = await sessions.find(
            self.web, await sessions.sign_in(self.web, who, [THURSDAY], now=NOW, days=30), now=NOW
        )
        self.assertIsNone(await self.door(bob))
        self.waiting_page = []
        empty = await self.door()
        self.assertEqual(first_feelings.lines_by_claim(empty), {})

    async def test_too_many_ids_or_unknown_ones_give_no_facts(self) -> None:
        await self.feel(keep=True)
        self.waiting_page = self.waiting_page * 201  # (the page never asks for more than 200)
        self.assertEqual((await self.door())["claims"], [])
        async with self.web.user(self.session.user_id, session=self.session.id_hash) as conn:
            await _scoped(conn, self.g)
            cur = await conn.execute(
                "SELECT dmbot_web_first_feeling_info(%s, %s, %s) AS r",
                (self.g, self.one.id, ["f" * 32]),
            )
            got = (await cur.fetchone() or {})["r"]
        self.assertEqual(first_feelings.lines_by_claim(got), {})
