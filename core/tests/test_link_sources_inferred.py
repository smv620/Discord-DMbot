"""A connection DMbot inferred from the lore says why, for the DM alone (#3928, migration 0403's
door already sends `inferred`; the website's graph page now passes it on). Pure: no database."""

from __future__ import annotations

import unittest
from typing import Any

from dmbot.web import link_sources

LINK = "a" * 32


def page() -> dict[str, Any]:
    return {"edges": [{"links": [{"id": LINK}]}]}


class TheReason(unittest.TestCase):
    def rows(self, **row: Any) -> dict[str, dict[str, Any]]:
        return {LINK: {"n": 3, **row}}

    def test_the_dm_gets_the_reason_and_no_quote(self) -> None:
        got = page()
        link_sources.attach(got, self.rows(inferred="she is called his sister"), True)
        link = got["edges"][0]["links"][0]
        self.assertEqual(link["inferred"], "she is called his sister")
        self.assertNotIn("quote", link)

    def test_a_player_never_gets_it(self) -> None:
        got = page()
        link_sources.attach(got, self.rows(inferred="she is called his sister"), False)
        self.assertNotIn("inferred", got["edges"][0]["links"][0])

    def test_a_stated_connection_has_none(self) -> None:
        got = page()
        link_sources.attach(got, self.rows(inferred=None, quote="Mira is Tavin's sister"), True)
        link = got["edges"][0]["links"][0]
        self.assertNotIn("inferred", link)
        self.assertEqual(link["quote"], "Mira is Tavin's sister")

    def test_a_connection_with_no_row_has_none(self) -> None:
        got = page()
        link_sources.attach(got, {}, True)
        self.assertNotIn("inferred", got["edges"][0]["links"][0])

    def test_a_quote_and_a_reason_together_give_only_the_reason(self) -> None:
        got = page()
        link_sources.attach(got, self.rows(inferred="she is called his sister", quote="x"), True)
        link = got["edges"][0]["links"][0]
        self.assertEqual(link["inferred"], "she is called his sister")
        self.assertNotIn("quote", link)

    def test_an_empty_reason_gives_no_key(self) -> None:
        got = page()
        link_sources.attach(got, self.rows(inferred=""), True)
        self.assertNotIn("inferred", got["edges"][0]["links"][0])
