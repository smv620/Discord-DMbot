"""A table's guess goes on the mystery it is about when the DM places it (#4655, quests slice 7b;
migration 0529): in the same write as the belief, so one Undo takes both back; never on a thread
that is gone or turned down; with its belief and its thread by the database; and in a backup.
Needs the test database; invented names throughout."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from dmbot.campaigns.models import CampaignError
from dmbot.memory import gate, readings
from dmbot.memory import thread_guesses as guesses
from dmbot.memory.backup import MemorySection
from dmbot.memory.claims import ClaimStore
from dmbot.memory.models import MemoryRuleError
from tests.test_memory_store import DM, GUILD_A, ConfirmedMemoryTest
from tests.test_placing import PLAYER, belief


class ThreadGuesses(ConfirmedMemoryTest):
    """(`test_placing.Placing`'s set-up, without running its tests again)"""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.claims = ClaimStore(self.memory)
        self.seen: set[str] = set()
        self.bram = await self.add("Bram")
        self.sword = await self.add("Heartseeker", type="item")
        self.mira = await self.add(
            "Mira", type="character", role="player_character", played_by=PLAYER
        )
        self.mystery = await self.add("Who has Heartseeker?", type="mystery")

    async def guess(self, view: str = "pc:Mira", thread: Any = None, **kw: Any) -> str:
        """A theory waiting for the DM, said about `thread` (this test's mystery unless given)."""
        claim = readings.as_claim(belief(view, kw.pop("status", "theory"), 0.5, **kw), "dm_said")
        extra = {"thread_id": self.mystery if thread is None else thread}
        claim = replace(claim, reading={**(claim.reading or {}), **extra})
        await self.claims.propose(
            GUILD_A, self.c, [claim], {"1": DM},
            opted_in={DM}, dms={DM}, prompt_version="r1", model="m1", session_started_at=1_000,
        )  # fmt: skip
        rows = await self.rows("memory_claims")
        (fresh,) = [r["id"] for r in rows if r["id"] not in self.seen]
        self.seen.add(str(fresh))
        return str(fresh)

    async def place(self, claim_id: str) -> Any:
        return await self.claims.place(GUILD_A, self.c, claim_id, opted_in={DM})

    async def rows(self, table: str) -> list[dict[str, Any]]:
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute(f"SELECT * FROM {table} ORDER BY id")
            return [dict(r) for r in await cur.fetchall()]

    async def links(self) -> list[dict[str, Any]]:
        return await self.rows("memory_thread_guesses")

    async def test_a_placed_guess_goes_on_its_mystery(self) -> None:
        placed = await self.place(await self.guess())
        (link,) = await self.links()
        assert placed.belief is not None
        self.assertEqual((link["thread_id"], link["belief_id"]), (self.mystery, placed.belief.id))

    async def test_the_partys_guess_goes_on_it_too(self) -> None:
        placed = await self.place(await self.guess("party"))
        (link,) = await self.links()
        assert placed.belief is not None
        self.assertEqual(link["belief_id"], placed.belief.id)

    async def test_one_undo_takes_the_link_back_with_the_belief(self) -> None:
        claim_id = await self.guess()
        placed = await self.place(claim_id)
        await self.memory.undo(
            GUILD_A, self.c, placed.batch, after=lambda w: self.claims.reopen_in(w, claim_id)
        )
        self.assertEqual(await self.links(), [])

    async def test_a_thread_turned_down_since_takes_no_guess(self) -> None:
        claim_id = await self.guess()
        await self.memory.set_entity_status(GUILD_A, self.c, self.mystery, "rejected", source="dm")
        placed = await self.place(claim_id)
        self.assertIsNotNone(placed.belief)  # (a plain belief, as one about no mystery)
        self.assertEqual(await self.links(), [])

    async def test_an_entry_that_is_not_a_thread_takes_no_guess(self) -> None:
        await self.place(await self.guess(thread=self.bram))
        self.assertEqual(await self.links(), [])

    async def test_a_thread_on_a_reading_that_is_no_guess_is_refused(self) -> None:
        claim_id = await self.guess("party", status="revealed_fact", because="the letter")
        with self.assertRaises(MemoryRuleError):
            await self.place(claim_id)

    async def test_a_thread_id_that_is_not_an_id_is_refused(self) -> None:
        with self.assertRaises(MemoryRuleError):
            await self.place(await self.guess(thread="not an id"))

    async def test_the_link_goes_with_its_thread(self) -> None:
        await self.place(await self.guess())
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "DELETE FROM memory_entities WHERE campaign_id = %s AND id = %s",
                (self.c, self.mystery),
            )
        self.assertEqual(await self.links(), [])

    async def test_a_backup_carries_the_link(self) -> None:
        await self.place(await self.guess())
        section = MemorySection()
        async with self.db.guild(GUILD_A) as conn:
            rows = await section.dump(conn, GUILD_A, self.c)
        (row,) = [r for r in rows if r["table"] == "thread_guess"]
        self.assertEqual(row["thread_id"], self.mystery)
        copy = (await self.campaigns.create(GUILD_A, "Copy", DM)).id
        async with self.db.guild(GUILD_A) as conn:
            await section.load(conn, GUILD_A, copy, rows)
            cur = await conn.execute(
                "SELECT thread_id FROM memory_thread_guesses WHERE campaign_id = %s", (copy,)
            )
            self.assertEqual([r["thread_id"] for r in await cur.fetchall()], [self.mystery])

    async def test_a_backup_link_to_nothing_is_damage(self) -> None:
        await self.place(await self.guess())
        section = MemorySection()
        async with self.db.guild(GUILD_A) as conn:
            rows = await section.dump(conn, GUILD_A, self.c)
        (row,) = [r for r in rows if r["table"] == "thread_guess"]
        bad: list[dict[str, Any]] = [
            {**row, "thread_id": self.bram},  # not a thread
            {**row, "belief_id": "f" * 32},  # no such belief
            {**row, "created_at": "soon"},
        ]
        for wrong in bad:
            with self.subTest(wrong=wrong), self.assertRaises(CampaignError):
                section.check([wrong if r is row else r for r in rows])
        with self.assertRaises(CampaignError):  # (two links for one belief)
            section.check([*rows, {**row, "id": "e" * 32}])


class TheCard(ThreadGuesses):
    """The read the 🧵 card shows (#4655): the DM sees every guess; a player only their own
    characters' and the party's (Supervisor's condition, tested both ways)."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.ash = await self.add("Ash", type="character", role="player_character", played_by=778)

    async def read(self, viewer: gate.Viewer) -> list[guesses.Guess]:
        return await self.memory.thread_guesses(GUILD_A, self.c, self.mystery, viewer)

    async def test_the_dm_sees_every_guess_newest_first(self) -> None:
        await self.place(await self.guess("pc:Mira"))
        await self.place(await self.guess("party"))
        seen = await self.read(gate.Viewer.the_dm())
        self.assertEqual([g.holder for g in seen], ["", "Mira"])
        self.assertEqual(seen[1].words, "Bram carries Heartseeker")

    async def test_a_player_sees_their_own_characters_guess_and_the_partys_only(self) -> None:
        await self.place(await self.guess("pc:Mira"))
        await self.place(await self.guess("pc:Ash"))
        await self.place(await self.guess("party"))
        mine = await self.read(gate.Viewer.player([self.mira]))
        self.assertEqual(sorted(g.holder for g in mine), ["", "Mira"])  # (never Ash's)
        theirs = await self.read(gate.Viewer.player([self.ash]))
        self.assertEqual(sorted(g.holder for g in theirs), ["", "Ash"])

    async def test_a_player_who_cannot_see_the_thread_sees_no_guess(self) -> None:
        await self.place(await self.guess("pc:Mira"))
        await self.memory.set_hidden_from_players(
            GUILD_A, self.c, self.mystery, True, source="dm", dm_chose_secret=True
        )
        self.assertEqual(await self.read(gate.Viewer.player([self.mira])), [])

    async def test_a_guess_whose_connection_was_turned_down_is_gone(self) -> None:
        placed = await self.place(await self.guess())
        await self.memory.update_relation(
            GUILD_A, self.c, placed.relation_id, status="rejected", source="dm"
        )
        self.assertEqual(await self.read(gate.Viewer.the_dm()), [])
