"""The 🧵 card's steps, rewards and more (#4471, quests slice 2b; the ux-critic's words on #4471,
⏰ for a time limit by Supervisor's ruling): the checklist with its count, what is in the way,
rewards with what is still owed, help, time limits, branches, 👁️ for what players see, and the
finished ones folded into one line. No Discord: the lines are pure; the card's one read needs
Postgres. Invented names."""

from __future__ import annotations

import unittest
from typing import ClassVar

from dmbot.memory import thread_parts as tp
from dmbot.memory.models import DM
from dmbot.ui import thread_parts as ui
from tests.test_memory_store import GUILD_A, MemoryTest
from tests.test_thread_parts import part


class Lines(unittest.TestCase):
    names: ClassVar[dict[str, str]] = {
        "hlin": "Hlin Trollbane", "jeb": "Jebidiah", "wit": "Who killed Wit?",
        "search": "Cerric's search",
    }  # fmt: skip

    def test_steps_with_their_count_and_sub_steps(self) -> None:
        lines = ui.card_lines(
            [
                part(tp.OBJECTIVE, id="a", words="Find where Sephek sleeps"),
                part(tp.OBJECTIVE, id="b", words="Ask at the Lucky Liar", state="done"),
                part(tp.OBJECTIVE, id="c", words="Talk to the barkeep", under_id="b"),
            ],
            self.names, None, [],
        )  # fmt: skip
        self.assertEqual(
            lines,
            [
                "**Steps (1 of 3 done):**",
                "☐ Find where Sephek sleeps",
                "☑ Ask at the Lucky Liar",
                "  ↳ ☐ Talk to the barkeep",
            ],
        )

    def test_a_reward_with_what_is_still_owed_and_a_gift_never_promised(self) -> None:
        lines = ui.card_lines(
            [
                part(tp.REWARD, id="r", words="100 gold", who_id="hlin", amount=100.0,
                     unit="gold", due=tp.AFTERWARDS),
                part(tp.REWARD_GIVEN, id="g", words="50 gold", under_id="r", amount=50.0,
                     unit="gold"),
                part(tp.REWARD_GIVEN, id="m", words="a warm meal", who_id="jeb"),
            ],
            self.names, None, [],
        )  # fmt: skip
        self.assertEqual(
            lines,
            [
                "🎁 **Reward from Hlin Trollbane:** 100 gold, when the job's done"
                " (50 given, 50 owed)",
                "🎁 **Given by Jebidiah:** a warm meal (not promised)",
            ],
        )

    def test_help_time_limits_branches_and_what_players_see(self) -> None:
        lines = ui.card_lines(
            [
                part(tp.HELP, id="h", words="two sleds and eight dogs", who_id="jeb",
                     state="lent", terms="unharmed", table_knows=True),
                part(tp.TIME_LIMIT, id="t", words="before the new moon", limit_kind=tp.HARD),
                part(tp.COMPLICATION, id="x", words="the bridge is out", state="overcome"),
                part(tp.COMPLICATION, id="y", words="the road north is cut"),
            ],
            self.names, "search", ["wit"],
        )  # fmt: skip
        self.assertEqual(
            lines,
            [
                "**In the way:** the road north is cut",
                "🤲 **Lent by Jebidiah:** two sleds and eight dogs (to return: unharmed) 👁️",
                "⏰ **Time limit:** before the new moon (hard: miss it and something's lost)",
                "Also: 1 overcome. Tap 📋 to see them.",
                "🌿 **Branches:** Who killed Wit?",
                "🌿 **Branch of:** Cerric's search",
                ui.SEEN_NOTE,
            ],
        )

    def test_no_stored_word_reaches_the_dm(self) -> None:
        """(§13 rule 10: the DM never sees a status word)"""
        for kind, states in tp.STATES.items():
            for state in states:
                p = part(kind, state=state, words="x")
                _label, desc = ui.option_for(p, self.names)
                self.assertNotIn("_", desc, (kind, state))
                self.assertIn(state, ui.STATE_BUTTONS[kind])
                for line in ui.card_lines([p], self.names, None, []):
                    for word in ("wont_be_given", "to_keep", "not_returned", "in_the_way"):
                        self.assertNotIn(word, line)

    def test_every_label_fits_discord(self) -> None:
        for label in (
            ui.PARTS_BUTTON,
            ui.BRANCH_BUTTON,
            ui.CHANGE_WORDS,
            ui.TAKE_OFF,
            ui.SUB_STEP,
            ui.TOWARD,
            ui.IN_STORY,
            ui.JUST_FIX,
            *(w for _, w, _ in ui.ADD_CHOICES),
        ):
            self.assertLessEqual(len(label), 80, label)  # fmt: skip
        for _, _, desc in ui.ADD_CHOICES:
            self.assertLessEqual(len(desc), 100, desc)
        for title, label, _ in (*ui.FORMS.values(), ui.SUB_FORM):
            self.assertLessEqual(len(title), 45, title)
            self.assertLessEqual(len(label), 45, label)


class Menus(unittest.IsolatedAsyncioTestCase):
    """Discord refuses a menu choice with an empty value (the reviewer's finding on #4471)."""

    async def test_no_choice_has_an_empty_value(self) -> None:
        owed = [part(tp.REWARD, id="r" * 32, words="100 gold", who_id="hlin")]
        menus = [
            ui.TowardMenu("c" * 32, "t" * 32, owed, Lines.names, part_id=None),
            ui.BranchMenu("c" * 32, "t" * 32, [("w" * 32, "Who killed Wit?", "mystery")], "s" * 32),
            ui.PartsScreen("c" * 32, "t" * 32, owed, Lines.names, undo=5),
        ]
        for menu in menus:
            for item in menu.children:
                for option in getattr(item, "options", []):
                    self.assertTrue(option.value, (type(menu).__name__, option.label))


class TheCardsRead(MemoryTest):
    async def test_parts_names_and_branches_in_one_read(self) -> None:
        quest = await self.add("Hlin's job: Sephek", type="quest", status="confirmed")
        hlin = await self.add("Hlin Trollbane", status="confirmed")
        wit = await self.add("Who killed Wit?", type="mystery", status="confirmed")
        await self.memory.add_thread_part(
            GUILD_A, self.c, quest, tp.REWARD, "100 gold", who_id=hlin, source=DM
        )
        await self.memory.branch_of(GUILD_A, self.c, wit, quest, source=DM)
        parts, names, bigger, branches = await self.memory.thread_card(GUILD_A, self.c, quest)
        self.assertEqual([p.words for p in parts], ["100 gold"])
        self.assertEqual((names[hlin], bigger, branches), ("Hlin Trollbane", None, [wit]))
        _, _, bigger, _ = await self.memory.thread_card(GUILD_A, self.c, wit)
        self.assertEqual(bigger, quest)


if __name__ == "__main__":
    unittest.main()
