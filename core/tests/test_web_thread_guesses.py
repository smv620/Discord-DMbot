"""A story thread's guesses on the website (#4665, migration 0539): the DM sees Discord's own
lines, held against `MemoryStore.thread_guesses(…, Viewer.the_dm())`; nobody else gets any (the door
answers NULL); a connection turned down, an older belief of the same holder and a belief of no
sureness are left out. Invented names. Needs Postgres."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from dmbot.memory import beliefs, gate, readings
from dmbot.memory import thread_guesses as guesses
from dmbot.memory.claims import ClaimStore
from dmbot.memory.models import CONFIRMED, DM, OPEN
from dmbot.web import thread_guesses
from dmbot.web.recaps import RecapError
from tests.test_placing import belief
from tests.test_recap_web import BOB
from tests.test_web_api import ALICE, THURSDAY
from tests.test_web_name_actions import Acting

PLAYER = 9


class Guesses(Acting):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.claims = ClaimStore(self.memory)
        self.placed_ids: set[str] = set()
        made = await self.memory.new_thread(
            self.g, self.one.id, "Who has Heartseeker?", "mystery", OPEN, source=DM
        )
        self.thread = made.value.id
        self.bram = await self.entity("Bram", "character")
        self.sword = await self.entity("Heartseeker", "item")
        self.mira = await self.entity(
            "Mira", "character", role="player_character", played_by=PLAYER
        )

    async def entity(self, name: str, kind: str, **kw: Any) -> str:
        made = await self.memory.add_entity(
            self.g, self.one.id, name=name, type=kind, source=DM, status=CONFIRMED, **kw
        )
        return str(made.value.id)

    async def place(self, view: str = "pc:Mira", **kw: Any) -> Any:
        """A guess voiced by `view`, placed by the DM on this thread."""
        claim = readings.as_claim(belief(view, kw.pop("status", "theory"), 0.5, **kw), "dm_said")
        claim = replace(claim, reading={**(claim.reading or {}), "thread_id": self.thread})
        await self.claims.propose(
            self.g, self.one.id, [claim], {"1": ALICE.id},
            opted_in={ALICE.id}, dms={ALICE.id}, prompt_version="r1", model="m1",
            session_started_at=1_000,
        )  # fmt: skip
        async with self.db.guild(self.g) as conn:
            cur = await conn.execute("SELECT id FROM memory_claims ORDER BY id")
            ids = [str(r["id"]) for r in await cur.fetchall()]
        (fresh,) = [i for i in ids if i not in self.placed_ids]
        self.placed_ids.add(fresh)
        return await self.claims.place(self.g, self.one.id, fresh, opted_in={ALICE.id})

    async def door(self, who: Any = None) -> dict[str, Any]:
        return await thread_guesses.read(self.web, who or self.session, self.cref, self.thread)

    async def lines_of_the_store(self) -> list[str]:
        stored = await self.memory.thread_guesses(
            self.g, self.one.id, self.thread, gate.Viewer.the_dm()
        )
        return guesses.card_lines(stored)

    async def test_the_dm_gets_discords_lines_newest_first(self) -> None:
        await self.place("pc:Mira")
        await self.place("party")
        got = (await self.door())["lines"]
        self.assertEqual(got, await self.lines_of_the_store())
        self.assertEqual(got[0], "💭 **Guesses:**")
        self.assertEqual(len(got), 3)
        self.assertTrue(got[1].startswith("• The whole party"))

    async def test_one_guess_is_one_line_with_no_verdict(self) -> None:
        await self.place("pc:Mira")
        (line,) = (await self.door())["lines"]
        self.assertTrue(line.startswith("💭 **Guess:** Mira "))
        for word in ("right", "wrong", "warm", "cold", "solved"):
            self.assertNotIn(word, line.lower())

    async def test_an_accusation_reads_as_the_store_reads_it(self) -> None:
        await self.place("pc:Mira", status="accusation")
        got = (await self.door())["lines"]
        self.assertEqual(got, await self.lines_of_the_store())
        self.assertEqual(len(got), 1)

    async def test_no_guess_is_no_line(self) -> None:
        self.assertEqual(await self.door(), {"lines": []})

    async def test_a_guess_on_a_connection_turned_down_is_left_out(self) -> None:
        placed = await self.place("pc:Mira")
        async with self.db.guild(self.g) as conn:
            await conn.execute(
                "UPDATE memory_relations SET status = 'rejected' WHERE id = %s",
                (placed.relation_id,),
            )
        self.assertEqual(await self.door(), {"lines": []})
        self.assertEqual(await self.lines_of_the_store(), [])

    async def test_a_newer_belief_of_the_same_holder_replaces_the_guess(self) -> None:
        placed = await self.place("pc:Mira")
        assert placed.belief is not None
        async with self.memory._write(self.g, self.one.id, "dm") as w:  # (Mira changes her mind)
            await beliefs.place_belief(
                w, placed.belief.perspective_id, placed.relation_id, 0.25, "theory",
                session_at=2_000,
            )  # fmt: skip
        got = (await self.door())["lines"]
        self.assertEqual(got, await self.lines_of_the_store())

    async def test_only_a_dm_of_the_campaign_gets_any(self) -> None:
        await self.place("pc:Mira")
        bob = await self.signed(BOB, THURSDAY)  # DMs Two, not One
        with self.assertRaises(RecapError) as refused:
            await self.door(bob)
        self.assertEqual(refused.exception.code, "not_allowed")

    async def test_an_entry_that_is_not_here_is_not_found(self) -> None:
        with self.assertRaises(RecapError) as refused:
            await thread_guesses.read(self.web, self.session, self.cref, "a" * 32)
        self.assertEqual(refused.exception.code, "not_found")
        with self.assertRaises(RecapError) as bad:
            await thread_guesses.read(self.web, self.session, self.cref, "x")
        self.assertEqual(bad.exception.code, "not_found")
