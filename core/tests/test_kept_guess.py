"""DMbot's guess of an NPC knower, kept or dropped by the DM's "Who knows this?" list (#4968;
Supervisor's ruling, 2026-11-21): kept on a list that showed it (the website's), it becomes the
DM's word (1.0); unticked, it goes; a list that never showed NPCs (Discord's) or never said what it
showed leaves it as it is; and a knower the list didn't show is never dropped. Invented names."""

from __future__ import annotations

import unittest

from dmbot.memory import link_edits
from dmbot.memory.models import CONFIRMED
from dmbot.memory.models import DM as DM_SOURCE
from tests.test_memory_store import GUILD_A as GUILD
from tests.test_memory_store import ConfirmedMemoryTest


class KeptGuess(ConfirmedMemoryTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.tala = await self.add("Tala", type="character", role="player_character", played_by=55)
        self.brin = await self.add("Brin", type="character", role="npc")
        self.oska = await self.add("Oska", type="character", role="npc")
        made = await self.relate(self.brin, "knows", self.oska, status=CONFIRMED)
        self.link = made.value[0]
        await link_edits.set_who_knows(  # (hidden, and Tala knows it)
            self.memory, GUILD, self.c, self.link, "some", [self.tala]
        )
        self.link = next(
            r
            for r in await self.memory.relations(GUILD, self.c, include_secret=True)
            if r.id == self.link.id
        )

    async def guess(self, who: str, sure: float = 0.6) -> None:
        await self.memory.add_guessed_knowers(
            GUILD, self.c, "connection", self.link.id, {who: sure}, source=DM_SOURCE
        )

    async def sure(self) -> dict[str, float]:
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                "SELECT p.entity_id, k.confidence FROM memory_knowers k"
                " JOIN memory_perspectives p ON p.id = k.perspective_id"
                " WHERE k.campaign_id = %s AND k.relation_id = %s",
                (self.c, self.link.id),
            )
            return {str(r["entity_id"]): float(r["confidence"]) for r in await cur.fetchall()}

    async def save(
        self, chosen: list[str], *, shown: list[str] | None, players_only: bool = False
    ) -> list[int]:
        made: list[int] = []
        await link_edits.set_who_knows(
            self.memory, GUILD, self.c, self.link, "some", chosen,
            players_only=players_only, made=made, listed=shown,
        )  # fmt: skip
        return made

    async def test_a_guess_kept_on_the_list_that_showed_it_becomes_the_dms_word(self) -> None:
        await self.guess(self.brin)
        await self.save([self.tala, self.brin], shown=[self.tala, self.brin])
        self.assertEqual((await self.sure())[self.brin], 1.0)

    async def test_its_undo_puts_the_guess_back(self) -> None:
        await self.guess(self.brin, 0.9)
        made = await self.save([self.tala, self.brin], shown=[self.tala, self.brin])
        for batch in reversed(made):
            await self.memory.undo(GUILD, self.c, batch, source=DM_SOURCE)
        self.assertEqual((await self.sure())[self.brin], 0.9)

    async def test_an_unticked_guess_goes(self) -> None:
        await self.guess(self.brin)
        await self.save([self.tala], shown=[self.tala, self.brin])
        self.assertNotIn(self.brin, await self.sure())

    async def test_a_guess_added_after_the_page_was_read_stays(self) -> None:
        await self.guess(self.brin)
        await self.save([self.tala], shown=[self.tala])  # (the page never showed Brin)
        self.assertEqual((await self.sure())[self.brin], 0.6)

    async def test_discords_player_list_leaves_a_guess_as_it_is(self) -> None:
        await self.guess(self.brin)
        await self.save([self.tala], shown=None, players_only=True)
        self.assertEqual((await self.sure())[self.brin], 0.6)

    async def test_without_the_shown_part_it_saves_as_before(self) -> None:
        await self.guess(self.brin)
        await self.guess(self.oska)
        await self.save([self.tala, self.brin], shown=None)
        got = await self.sure()
        self.assertEqual(got[self.brin], 0.6)  # (kept, as it was)
        self.assertNotIn(self.oska, got)  # (unticked, gone, as before)

    async def test_an_npc_the_dm_picks_the_page_never_showed_is_the_dms_word(self) -> None:
        await self.guess(self.brin)
        await self.save([self.tala, self.brin], shown=[self.tala])
        self.assertEqual((await self.sure())[self.brin], 1.0)

    async def test_an_npc_the_dm_named_and_unticks_goes(self) -> None:
        await self.save([self.tala, self.brin], shown=[self.tala, self.brin])
        await self.save([self.tala], shown=[self.tala, self.brin])
        self.assertNotIn(self.brin, await self.sure())

    async def test_a_player_characters_certainty_is_unchanged(self) -> None:
        await self.memory.set_statement_knowers(
            GUILD, self.c, "connection", self.link.id, {self.tala: 0.8}, source=DM_SOURCE,
            dm_chose_secret=True,
        )  # fmt: skip
        await self.guess(self.brin)
        await self.save([self.tala, self.brin], shown=[self.tala, self.brin])
        self.assertEqual((await self.sure())[self.tala], 0.8)

    async def test_a_player_character_the_page_never_showed_still_knows_it(self) -> None:
        await link_edits.set_who_knows(
            self.memory, GUILD, self.c, self.link, "some", [self.brin], listed=[self.brin]
        )
        self.assertIn(self.tala, await self.sure())
        known = await self.memory.connection_knowers(GUILD, self.c, [self.link.id])
        self.assertIn(self.tala, known.get(self.link.id, []))


class TheActionText(unittest.TestCase):
    def test_the_shown_part_is_read_apart_from_the_answer(self) -> None:
        from dmbot.memory.web_actions import _shown_of, _who_of

        a, b = "a" * 32, "b" * 32
        self.assertEqual(_who_of(f"some {a} shown {a} {b}"), ("some", [a]))
        self.assertEqual(_shown_of(f"some {a} shown {a} {b}"), [a, b])
        self.assertEqual(_shown_of(f"some {a}"), None)
        self.assertEqual(_shown_of("dm shown"), [])
        self.assertEqual(_who_of(f"shown {a}"), ("", []))
