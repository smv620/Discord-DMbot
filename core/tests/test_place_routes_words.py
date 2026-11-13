"""The website's "Next to…" and "Travel to…" words (web/src/content/place-routes-words.ts, #4341)
are a copy of Discord's (`ui/place_links.py` with the stores' own words), so the two stay level.
This pure test fails when they drift: a choice or a sentence changed on one side only. No
database."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from dmbot.memory import link_words, next_words, route_words
from dmbot.ui import place_links

WORDS = Path(__file__).resolve().parents[2] / "web/src/content/place-routes-words.ts"
CHOICE = re.compile(r'\{ label: "([^"]+)", forward: "([^"]+)", pair: (?:"([^"]+)"|null) \}')


def _web(block: str) -> list[tuple[str, str, str | None]]:
    text = WORDS.read_text(encoding="utf-8")
    start = text.index(f"export const {block}")
    end = text.index("];", start)
    return [(m[1], m[2], m[3] or None) for m in CHOICE.finditer(text[start:end])]


def _core(how: str, choices: tuple[tuple[str, str], ...]) -> list[tuple[str, str, str | None]]:
    out = []
    for label, detail in choices:
        both = link_words.reads_both_ways(how, detail)
        out.append(
            (
                label,
                link_words.link_words(how, detail),
                link_words.pair_words(how, detail) if both else None,
            )
        )
    return out


class TheWebsiteWordsAreDiscords(unittest.TestCase):
    def test_next_to(self) -> None:
        # (the one left out on the website for now: "On a road or river", which stores plain "on")
        discord = [c for c in place_links.NEXT_CHOICES if c[1] != next_words.ON]
        self.assertEqual(_web("NEXT_CHOICES"), _core(place_links.NEXT_TO, tuple(discord)))

    def test_travel_to(self) -> None:
        self.assertEqual(
            _web("TRAVEL_CHOICES"), _core(route_words.ROUTE_TO, place_links.TRAVEL_CHOICES)
        )

    def test_the_questions(self) -> None:
        text = WORDS.read_text(encoding="utf-8")
        self.assertIn(place_links.asked("${name}", place_links.NEXT_TO), text)
        self.assertIn(place_links.asked("${name}", route_words.ROUTE_TO), text)
        self.assertIn(place_links.ask_words("${a}", "${b}").replace("**", ""), text)
        self.assertIn(
            place_links.ask_words("${a}", "${b}", route_words.ROUTE_TO).replace("**", ""), text
        )
