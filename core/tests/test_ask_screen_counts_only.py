"""The words an after-session question leaves in the DM Bot channel when no DM can be reached
privately (`Ask.screen`, #3874) hold counts only: that channel is read by the campaign's people, so
no name, quote or upload label may be in them. Every builder is checked with sample words that must
not appear. Invented names."""

from __future__ import annotations

import unittest

from dmbot.dm_waiting import WaitingChecks, asks_for
from dmbot.memory import kind_offers
from dmbot.ui import deed_card, kind_offer, reveal_card, theatre_button
from dmbot.ui.names import counts_after_session_text

GUILD, CAMPAIGN = 1, "c" * 32
SECRETS = ("Mirelle Vane", "the red door", "Tavin's backstory")


class CountsOnly(unittest.TestCase):
    def check(self, text: str) -> None:
        for word in SECRETS:
            self.assertNotIn(word, text)

    def test_the_names_question_is_a_count(self) -> None:
        self.check(counts_after_session_text(3))
        self.assertIn("3 new names to check", counts_after_session_text(3))

    def test_the_kind_question_quotes_no_table_words(self) -> None:
        made = kind_offer.ask_for(
            GUILD,
            CAMPAIGN,
            kind_offers.Offer("red door", "the red door", 4, ("a",), ("npc",), ("place",)),
        )
        self.check(made.screen)
        self.assertIn("the red door", made.alone)  # (the private chat may quote)

    def test_deeds_reveals_and_the_script_are_counts(self) -> None:
        for made in (
            deed_card.ask_for(GUILD, CAMPAIGN, 2),
            reveal_card.ask_for(GUILD, CAMPAIGN, 2),
            theatre_button.ask_for(GUILD, "s" * 32, 300),
        ):
            self.check(made.screen)

    def test_the_waiting_checks_never_carry_the_upload_label_in_their_screen_words(self) -> None:
        waiting = WaitingChecks(names=2, connections=3, claims=1, people=1)
        buttons = {kind: (lambda: None) for kind in ("names", "people", "claims", "connections")}
        for made in asks_for(waiting, buttons, where="Tavin's backstory, 9 Nov"):  # type: ignore[arg-type]
            self.check(made.screen)
            self.assertNotIn("Tavin", made.screen)
