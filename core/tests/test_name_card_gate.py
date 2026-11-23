"""The name card asks the gate who may see an entry, not the flags (#4804, party knowledge slice 5,
`docs/design/party_knowledge.md` §9 rules 2 and 6): a newcomer who has not been told is not shown
the card though the flags still say "shown", and the card says "only some people know this" while
the table (the newcomer too) does not; no read writes a row. Invented names and ids only."""

from __future__ import annotations

from typing import Any, ClassVar

from dmbot.memory import gate
from dmbot.memory.lookup import CampaignLookup
from dmbot.ui import name_card
from tests.test_party_join import ANNA, CARA, DM_ID, GUILD, Party


class ANewcomerAndTheCard(Party):
    """Vell and Tuck know the harbourmaster; Cara joined and hasn't been told anything yet."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.cara = await self.pc("Cara", CARA)

    async def rows(self) -> int:
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                "SELECT count(*) AS n FROM memory_knowers WHERE guild_id = %s", (GUILD,)
            )
            row = await cur.fetchone()
        return int(row["n"]) if row else 0

    async def card(self, character: str | None, *, secrets: bool = False) -> str | None:
        view: Any = CampaignLookup.build(await self.memory.lookup_data(GUILD, self.c))
        seen = (
            await self.memory.entries_held(GUILD, self.c, gate.Viewer.player([character]))
            if character
            else frozenset()
        )
        table = await self.memory.entries_held(GUILD, self.c)
        return name_card.card_text(view, self.npc, [], secrets=secrets, seen=seen, table=table)

    async def test_a_newcomer_not_yet_told_is_not_shown_the_card(self) -> None:
        self.assertIsNone(await self.card(self.cara))  # (the flags still say "shown")

    async def test_someone_who_knows_it_is_shown_it_and_no_empty_audience_line(self) -> None:
        text = await self.card(self.vell)
        assert text is not None
        self.assertIn("Harbourmaster", text)
        self.assertNotIn("Only some people know this", text)  # (no one to name: no "—" line)

    async def test_the_gate_sets_of_a_press(self) -> None:
        from types import SimpleNamespace

        for user, holds in ((CARA, False), (ANNA, True), (999_999, False)):
            it = SimpleNamespace(user=SimpleNamespace(id=user))
            seen, table = await name_card._gate_sets(it, self.memory, self.campaign, False)  # type: ignore[arg-type]
            assert seen is not None
            self.assertEqual(self.npc in seen, holds, user)  # (a stranger holds nothing of it)
            self.assertNotIn(self.npc, table)  # (Cara, told nothing yet, is at the table)
        it = SimpleNamespace(user=SimpleNamespace(id=DM_ID))
        seen, _ = await name_card._gate_sets(it, self.memory, self.campaign, True)  # type: ignore[arg-type]
        self.assertIsNone(seen)  # (the DM needs no filter)

    async def test_told_it_the_card_is_for_everyone_and_has_no_audience_line(self) -> None:
        [q] = await self.memory.join_questions_to_send(GUILD, self.c)
        assert q.group_id is not None
        await self.memory.join_answer_party(GUILD, self.c, q.group_id)
        text = await self.card(self.cara)
        assert text is not None
        self.assertNotIn("Only some people know this", text)

    async def test_a_reader_with_no_character_is_shown_nothing_of_it(self) -> None:
        self.assertIsNone(await self.card(None))  # (fail closed: common knowledge only)

    async def test_the_dm_card_needs_no_filter_and_nothing_is_written(self) -> None:
        before = await self.rows()
        self.assertIsNotNone(await self.card(None, secrets=True))
        await self.card(self.cara)
        await self.card(self.vell)
        self.assertEqual(await self.rows(), before)  # (§9 rule 6: never "repaired" by a reader)


class TheSummaryLinesAreTheDMs(Party):
    """The three lines at the top of a character's card (#5050) go to the DM's card only."""

    LINES: ClassVar[list[str]] = ["A harbour official.", "Not mentioned at the table yet."]

    async def with_lines(self, *, secrets: bool) -> str | None:
        view: Any = CampaignLookup.build(await self.memory.lookup_data(GUILD, self.c))
        seen = await self.memory.entries_held(GUILD, self.c, gate.Viewer.player([self.vell]))
        table = await self.memory.entries_held(GUILD, self.c)
        return name_card.card_text(
            view, self.npc, [], secrets=secrets, seen=seen, table=table, summary_lines=self.LINES
        )

    async def test_the_dm_reads_them_under_the_name(self) -> None:
        text = await self.with_lines(secrets=True)
        assert text is not None
        head, first, second = text.split("\n")[:3]
        self.assertTrue(head.startswith("🪪"))
        self.assertEqual([first, second], self.LINES)

    async def test_a_player_never_reads_them(self) -> None:
        text = await self.with_lines(secrets=False)
        assert text is not None
        self.assertNotIn("A harbour official.", text)
