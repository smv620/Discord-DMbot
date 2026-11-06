"""A lore text that plainly says a character is one of the players' own is proposed as a player
character, and the read never guesses who plays it (#3553). The sample lore's own statements:
a "Player Character Backgrounds" section and "our four adventurers (Cerric, Belleros, Okthandom,
and Claude)" (lore key `players`). Invented stand-in names where the test needs a database."""

from __future__ import annotations

import json
import unittest

from dmbot.memory import background as bg
from dmbot.memory import lore_read
from dmbot.memory.models import PROPOSED, MemoryRuleError, NewName
from tests.test_memory_names import GUILD, NamesTest


class TheReader(unittest.TestCase):
    def test_the_players_kind_becomes_a_player_character(self) -> None:
        self.assertEqual(bg.KINDS["player"], "player_character")

    def test_the_reader_is_told_what_makes_a_character_a_players(self) -> None:
        text = lore_read.read_instructions()
        self.assertIn("player", bg.KINDS)
        self.assertIn("kind 'player'", text)
        self.assertIn("Player character backgrounds".lower(), text.lower())
        self.assertIn("never guess who plays", text.lower())
        self.assertIn("player", lore_read.shape_instructions().split("exactly one of")[1][:80])

    def test_a_players_kind_is_kept_and_an_npc_beside_it_stays_one(self) -> None:
        text = "Cerric Vanya'elen is one of our heroes. Syllar Sparkwalker is his master."
        reply = json.dumps(
            {
                "names": [
                    {"name": "Cerric Vanya'elen", "kind": "player", "also": [], "secret": [],
                     "quote": ""},
                    {"name": "Syllar Sparkwalker", "kind": "person", "also": [], "secret": [],
                     "quote": ""},
                ]
            }
        )  # fmt: skip
        got = {f.name: f.kind for f in bg.read_answer(reply, text)}
        self.assertEqual(
            got, {"Cerric Vanya'elen": "player_character", "Syllar Sparkwalker": "npc"}
        )


class TheStore(NamesTest):
    async def test_a_waiting_player_character_is_stored_without_a_player(self) -> None:
        made = await self.memory.add_names(
            GUILD, self.campaign.id,
            [NewName("Cerric", "player_character", PROPOSED), NewName("Syllar", "npc", PROPOSED)],
            source="dm",
        )  # fmt: skip
        cerric, syllar = made.value
        assert cerric and syllar
        found = {e.name: (e.status, e.role, e.played_by) for e in await self.memory.entities(
            GUILD, self.campaign.id, statuses=("proposed",)
        )}  # fmt: skip
        self.assertEqual(found["Cerric"], ("proposed", "player_character", None))  # no account
        self.assertEqual(found["Syllar"], ("proposed", "npc", None))

    async def test_a_confirmed_player_character_still_needs_its_player(self) -> None:
        with self.assertRaises(MemoryRuleError):
            await self.memory.add_names(
                GUILD, self.campaign.id,
                [NewName("Cerric", "player_character", "confirmed")],
                source="dm",
            )  # fmt: skip


class TheCard(unittest.IsolatedAsyncioTestCase):
    def test_the_card_says_player_character_and_asks_who_plays_it(self) -> None:
        import discord

        from dmbot.memory.models import BACKGROUND_SOURCE, Entity
        from dmbot.ui import names as ui

        entity = Entity(
            "e" * 32, "character", "Cerric", "", PROPOSED, None, BACKGROUND_SOURCE, 1, None,
            "player_character", False, None,
        )  # fmt: skip
        self.assertEqual(ui.background_kind(entity), "player_character")
        view = ui.SuggestionReview("c" * 32, [entity.id])
        view.known_kind = ui.background_kind(entity)
        view._buttons()
        labels = [str(getattr(c, "label", "")) for c in view.children]
        self.assertIn("✅ Yes, a player character", labels)
        self.assertTrue(all(not isinstance(c, discord.ui.UserSelect) for c in view.children))


class TheBulkCard(unittest.TestCase):
    def labels(self, stored: dict[str, list[str]]) -> list[str]:
        from dmbot.ui import name_background as ui_bg

        view = ui_bg.BulkConfirm("c" * 32, stored)
        return [str(getattr(c, "label", "")) for c in view.children]

    def test_a_yes_to_all_never_covers_player_characters(self) -> None:
        labels = self.labels({"npc": ["a", "b"], "player_character": ["c", "d"], "place": ["e"]})
        self.assertFalse(any("player character" in label for label in labels), labels)
        self.assertIn("✅ Add all 3", labels)  # only the NPCs and the place
        self.assertIn("📝 Check them one by one", labels)

    def test_player_characters_alone_offer_only_the_one_by_one_check(self) -> None:
        labels = self.labels({"player_character": ["c", "d", "e", "f"]})
        self.assertFalse(any(label.startswith("✅ Add all") for label in labels), labels)
        self.assertIn("📝 Check them one by one", labels)
