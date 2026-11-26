"""What a backstory's keep treats as "shown to the players" is the table's knowledge, by the gate
(#4823, party knowledge slice 5 follow-up; Supervisor's ruling on #4821): a newcomer not yet told
an entry keeps a world connection to it unrevealed while the flag still says "shown", nobody at all
in the party leaves only common knowledge, and the read writes nothing. Invented names and ids."""

from __future__ import annotations

from dmbot.memory import backstory_keep as keep
from dmbot.memory.backstory_links import BackstoryLinks, NewLink, Waiting
from dmbot.memory.models import CONFIRMED, DM
from tests.test_party_join import CARA, GUILD, Party


class TheTable(Party):
    """Vell and Tuck know the harbourmaster; Cara joined and hasn't been told anything yet."""

    async def rows(self) -> tuple[int, int]:
        out = []
        async with self.db.guild(GUILD) as conn:
            for table in ("memory_knowers", "memory_changes"):
                cur = await conn.execute(
                    f"SELECT count(*) AS n FROM {table} WHERE guild_id = %s", (GUILD,)
                )
                row = await cur.fetchone()
                out.append(int(row["n"]) if row else 0)
        return out[0], out[1]

    async def shown(self) -> set[str]:
        return await keep.shown_entry_ids(self.memory, GUILD, self.c)

    async def test_the_party_knows_it_so_it_is_shown(self) -> None:
        self.assertIn(self.npc, await self.shown())

    async def test_a_newcomer_not_told_keeps_it_unshown_while_the_flag_says_shown(self) -> None:
        await self.pc("Cara", CARA)
        entity = await self.memory.entity(GUILD, self.c, self.npc)
        assert entity is not None
        self.assertFalse(entity.hidden_from_players)  # (the flag still says "shown")
        self.assertNotIn(self.npc, await self.shown())

    async def test_nobody_in_the_party_leaves_only_common_knowledge(self) -> None:
        for pc in (self.vell, self.tuck):
            await self.memory.leave_party(GUILD, self.c, pc, "left")
        made = await self.memory.add_entity(
            GUILD, self.c, name="Saltmarsh", type="place", status=CONFIRMED, source=DM
        )
        world = str(made.value.id)
        await self.memory.add_common_knowledge(GUILD, self.c, "entry", [world], source=DM)
        shown = await self.shown()
        self.assertIn(world, shown)
        self.assertNotIn(self.npc, shown)

    async def test_the_read_writes_nothing(self) -> None:
        await self.pc("Cara", CARA)
        before = await self.rows()
        await self.shown()
        self.assertEqual(await self.rows(), before)  # (§9 rule 6)

    async def test_a_limited_clue_end_marks_the_card_not_the_kept_row(self) -> None:
        # (the DM's "only some know this" stays on the card; the kept row's view is the gate's,
        # Supervisor's ruling (a) on #4823)
        made = await self.memory.add_entity(
            GUILD, self.c, name="The ledger's code", type="clue", status=CONFIRMED, source=DM
        )
        clue = str(made.value.id)
        await self.memory.add_knower(GUILD, self.c, clue, self.vell, source=DM)
        await self.memory.set_audience_limited(
            GUILD, self.c, clue, True, source=DM, dm_chose_secret=True
        )
        links = BackstoryLinks(self.db)
        await links.add(GUILD, self.c, [NewLink(self.npc, "knows", clue, "quote")])
        [w] = [w for w in await links.waiting(GUILD, self.c) if w.object_id == clue]
        self.assertTrue(w.secret)  # (the card: no 👥 Some know, no hand-over, a secret name)
        self.assertTrue(w.clue_end)
        self.assertFalse(w.own_secret)  # (the kept row: not secret by the limited end alone)
        self.assertNotIn(clue, await self.shown())  # (not the table's: kept unrevealed)
        self.assertTrue(keep.kept_unrevealed(w, w.own_secret, await self.shown()))

    def test_a_connection_secret_of_its_own_stays_secret(self) -> None:
        w = Waiting("a", "knows", "b", "q", True, "A", "B", True, clue_end=False)
        self.assertTrue(w.own_secret)

    async def test_a_limited_clue_the_whole_party_holds_is_still_kept_unrevealed(self) -> None:
        # (shown by the gate, but what only some know never reaches every player through a keep)
        made = await self.memory.add_entity(
            GUILD, self.c, name="The ledger's code", type="clue", status=CONFIRMED, source=DM
        )
        clue = str(made.value.id)
        for pc in (self.vell, self.tuck):
            await self.memory.add_knower(GUILD, self.c, clue, pc, source=DM)
        await self.memory.set_audience_limited(
            GUILD, self.c, clue, True, source=DM, dm_chose_secret=True
        )
        links = BackstoryLinks(self.db)
        await links.add(GUILD, self.c, [NewLink(self.npc, "knows", clue, "quote", world=1)])
        [w] = [w for w in await links.waiting(GUILD, self.c) if w.object_id == clue]
        self.assertTrue(w.clue_end and not w.own_secret)
        self.assertTrue(keep.kept_unrevealed(w, w.own_secret, await self.shown()))
