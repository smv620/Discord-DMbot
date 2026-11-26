"""When a sentence states nothing a set-phrase reader may keep (#4982): a question, a supposition,
or someone else's view before the name. Pure. Invented names."""

from __future__ import annotations

import unittest

from dmbot.memory import hedges


def hedged(text: str, name: str) -> bool:
    at = text.index(name)
    return hedges.hedged(text, at, at + len(name))


class Hedged(unittest.TestCase):
    def test_what_states_nothing(self) -> None:
        for text in (
            "Is Coldwater Bridge out?",
            "If Hagmire Road is flooded, we turn back.",
            "Travelers say Ironpeak Pass is snowed in.",
            "Merchants claim Crowmarsh is mostly orcs.",
            "According to the guards, Kestrel Bridge is out.",
        ):
            with self.subTest(text):
                name = next(n for n in ("Coldwater Bridge", "Hagmire Road", "Ironpeak Pass",
                                        "Crowmarsh", "Kestrel Bridge") if n in text)  # fmt: skip
                self.assertTrue(hedged(text, name))

    def test_what_states_something(self) -> None:
        for text, name in (
            ("Frostgate Pass is snowed in.", "Frostgate Pass"),
            ("The road is fine. Is it? Yes. Greyspan has collapsed.", "Greyspan"),
            ("Oakhollow is mostly humans, they say.", "Oakhollow"),  # (said after the name)
        ):
            with self.subTest(text):
                self.assertFalse(hedged(text, name))
