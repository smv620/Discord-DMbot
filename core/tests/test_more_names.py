"""📝 More names (#5055 B; Supervisor's ruling): an imported session's names past the scan's first
10 wait in `import_more_names` instead of being lost, and one press by a DM makes them names to
check as the scan does. A live session keeps its short list. Invented names and ids."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from dmbot.memory.more_names import HeldName, MoreNames, forget_session
from dmbot.memory.scan import MAX_SUGGESTIONS
from dmbot.questions import kinds
from dmbot.transcript.import_job import ImportStore
from dmbot.transcript.store import TranscriptStore
from dmbot.ui.more_names import added_text
from tests.test_import_recaps import GUILD as G2
from tests.test_import_recaps import WithImports
from tests.test_memory_names import DM, GUILD, PLAYER, NamesTest

START = 1_700_000_000
# (invented, and none a common word, so the scan offers each: said twice, mid-sentence)
STRANGERS = [
    "Korvath", "Belmira", "Tasshen", "Oduvar", "Ylsabet", "Grennik", "Mavrolo", "Saphrine",
    "Dunwick", "Quellan", "Ostrava", "Briathe", "Hollorn", "Zebrenn",
]  # fmt: skip


class TheStore(WithImports):
    async def test_held_once_counted_taken_most_heard_first_and_gone(self) -> None:
        sid = await self.imported(1)
        held = MoreNames(self.db)
        names = [HeldName("Korvath", 2), HeldName("Belmira", 5, ("Bel",)), HeldName("Tasshen", 3)]
        self.assertEqual(await held.hold(G2, self.campaign.id, sid, names), 3)
        self.assertEqual(await held.hold(G2, self.campaign.id, sid, names), 0)  # (a rerun)
        self.assertEqual(await held.count(G2, self.campaign.id), 3)
        first = await held.take(G2, self.campaign.id, limit=2)
        self.assertEqual([h.name for h in first], ["Belmira", "Tasshen"])
        self.assertEqual(first[0].also, ("Bel",))
        self.assertEqual(await held.count(G2, self.campaign.id), 1)
        self.assertEqual([h.name for h in await held.take(G2, self.campaign.id)], ["Korvath"])
        self.assertEqual(await held.take(G2, self.campaign.id), [])

    async def test_they_go_with_their_sessions_transcript(self) -> None:
        sid = await self.imported(1)
        held = MoreNames(self.db)
        await held.hold(G2, self.campaign.id, sid, [HeldName("Korvath", 2)])
        async with self.db.guild(G2) as conn:
            await forget_session(conn, G2, sid)
        self.assertEqual(await held.count(G2, self.campaign.id), 0)


class TheScanAndThePress(NamesTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.transcripts = TranscriptStore(self.db)
        self.bot.transcripts = self.transcripts
        self.bot.import_job = SimpleNamespace(store=ImportStore(self.db))
        await self.consent.grant(GUILD, DM)
        await self.consent.grant(GUILD, PLAYER)
        self.sid = await self.transcripts.open_session(GUILD, self.campaign.id, START)

    def lines(self) -> list[str]:
        # each name twice, the first ones more often, so the 10 kept are the first 10
        out: list[str] = []
        for i, name in enumerate(STRANGERS):
            out += [f"we met {name} at the gate"] * (2 + len(STRANGERS) - i)
        return out

    async def proposed(self) -> set[str]:
        found = await self.memory.entities(GUILD, self.campaign.id, statuses=["proposed"])
        return {e.name for e in found}

    async def test_an_import_holds_what_is_past_the_ten_and_the_press_makes_them(self) -> None:
        await self.bot._propose_new_names(
            GUILD, self.campaign.id, self.lines(), {DM, PLAYER}, self.sid, import_scan=True
        )
        self.assertEqual(await self.proposed(), set(STRANGERS[:MAX_SUGGESTIONS]))
        held = MoreNames(self.db)
        self.assertEqual(await held.count(GUILD, self.campaign.id), 4)
        made, left = await self.bot.add_more_names(GUILD, self.campaign.id)
        self.assertEqual((made, left), (4, 0))
        self.assertEqual(await self.proposed(), set(STRANGERS))
        self.assertEqual(await held.count(GUILD, self.campaign.id), 0)

    async def test_a_live_session_holds_nothing(self) -> None:
        await self.bot._propose_new_names(
            GUILD, self.campaign.id, self.lines(), {DM, PLAYER}, self.sid
        )
        self.assertEqual(len(await self.proposed()), MAX_SUGGESTIONS)
        self.assertEqual(await MoreNames(self.db).count(GUILD, self.campaign.id), 0)

    async def test_a_held_name_learned_meanwhile_is_let_go(self) -> None:
        held = MoreNames(self.db)
        await held.hold(
            GUILD, self.campaign.id, self.sid, [HeldName("Korvath", 2), HeldName("Belmira", 2)]
        )
        await self.memory.add_entity(
            GUILD, self.campaign.id, type="npc", name="Korvath", source="dm"
        )
        made, left = await self.bot.add_more_names(GUILD, self.campaign.id)
        self.assertEqual((made, left), (1, 0))
        self.assertIn("Belmira", await self.proposed())


class TheWords(unittest.TestCase):
    def test_the_press_says_what_it_made_and_what_still_waits(self) -> None:
        self.assertIn("4 more names to check", added_text(4, 0))
        self.assertIn("1 more name to check", added_text(1, 0))
        self.assertIn("2 more wait: press 📝 More names again", added_text(300, 2))
        self.assertEqual(kinds.LABELS[kinds.MORE_NAMES], "More names from your sessions")
        self.assertIn(kinds.MORE_NAMES, kinds.ORDER)


if __name__ == "__main__":
    unittest.main()
