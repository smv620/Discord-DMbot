"""A confirmed alias of a name backs a link the AI wrote with the name (#2762): the document says
"the Ashen King" and the AI writes "Aldric Vane". Pure logic and the alias map's rules: no
database, no AI. Invented names throughout."""

from __future__ import annotations

import json
import unittest
from collections import Counter
from collections.abc import Mapping
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

from dmbot.memory import background as bg
from dmbot.memory.lookup import LookupData
from dmbot.memory.models import Alias
from dmbot.ui.name_background import confirmed_aliases, usable_alias
from tests.test_transcript_cleaner import alias, entity

ALDRIC, MIRA, VALE = "a" * 32, "b" * 32, "c" * 32
TEXT = "The Ashen King ruled Greyfen. Mira, called the Fox, served the Ashen King for years."
QUOTE = "Mira, called the Fox, served the Ashen King for years"
ROW = {"a": "Mira", "how": "serves", "words": "", "b": "Aldric Vane", "quote": QUOTE}
ALIASES = {"aldric vane": ("the ashen king",)}


def read(
    aliases: Mapping[str, tuple[str, ...]] | None, text: str = TEXT
) -> tuple[list[bg.Connection], Any]:
    lost: Any = Counter()
    reply = json.dumps({"connections": [ROW]})
    return bg.read_connections(reply, text, set(), lost, aliases), lost


class TheAliasBacksTheName(unittest.TestCase):
    def test_a_text_that_uses_only_the_alias_keeps_the_line(self) -> None:
        got, lost = read(ALIASES)
        (c,) = got
        self.assertEqual(
            (c.a, c.b, c.variant_b, c.variant_a), ("Mira", "Aldric Vane", "the ashen king", "")
        )
        self.assertFalse(lost)

    def test_without_the_alias_the_same_line_is_not_in_the_text(self) -> None:
        got, lost = read(None)
        self.assertEqual((got, dict(lost)), ([], {"not_in_text": 1}))

    def test_the_kept_line_resolves_to_the_name_the_alias_belongs_to(self) -> None:
        got, _ = read(ALIASES)
        found = [bg.Found("Mira", "npc", (), (), ""), bg.Found("Aldric Vane", "npc", (), (), "")]
        ends = {"the ashen king": "Aldric Vane"}
        lost: Any = Counter()
        kept = bg.keep_connections([got], found, ends, dropped=lost)
        self.assertEqual([(c.a, c.b) for c in kept], [("Mira", "Aldric Vane")])
        self.assertFalse(lost)

    def test_an_alias_that_is_now_another_entry_is_refused(self) -> None:
        got, _ = read(ALIASES)
        found = [
            bg.Found("Mira", "npc", (), (), ""),
            bg.Found("Aldric Vane", "npc", (), (), ""),
            bg.Found("The Ashen King", "npc", (), (), ""),  # a second entry, spelled so
        ]
        lost: Any = Counter()
        self.assertEqual(bg.keep_connections([got], found, {}, dropped=lost), [])
        self.assertEqual(dict(lost), {"spelling_clash": 1})


def lookup_of(*pairs: Alias, secret: tuple[Alias, ...] = ()) -> Any:
    entities = (
        entity(ALDRIC, "Aldric Vane"),
        entity(MIRA, "Mira"),
        entity(VALE, "Vale"),
    )
    aliases = (alias(ALDRIC, "Aldric Vane"), alias(MIRA, "Mira"), alias(VALE, "Vale"), *pairs)
    data = LookupData(1, entities, (*aliases, *secret), (), (), (), ())
    return SimpleNamespace(lookup_data=AsyncMock(return_value=data))


CAMPAIGN: Any = SimpleNamespace(guild_id=1, id="x" * 32)


class TheAliasMap(unittest.IsolatedAsyncioTestCase):
    async def test_a_confirmed_public_alias_is_listed_under_its_name(self) -> None:
        memory = lookup_of(alias(ALDRIC, "the Ashen King"))
        self.assertEqual(await confirmed_aliases(memory, CAMPAIGN), ALIASES)

    async def test_an_alias_two_entries_share_is_left_out(self) -> None:
        memory = lookup_of(alias(ALDRIC, "the Old One"), alias(VALE, "the Old One"))
        self.assertEqual(await confirmed_aliases(memory, CAMPAIGN), {})

    async def test_a_spelling_shared_with_a_secret_or_unconfirmed_or_hidden_entry_is_refused(
        self,
    ) -> None:
        # the same spelling is also a secret name of another entry, or a proposed one's
        secret = alias(VALE, "the Ashen King", secret=True)
        memory = lookup_of(alias(ALDRIC, "the Ashen King"), secret=(secret,))
        self.assertEqual(await confirmed_aliases(memory, CAMPAIGN), {})
        memory = lookup_of(
            alias(ALDRIC, "the Ashen King"), alias(VALE, "the Ashen King", status="proposed")
        )
        self.assertEqual(await confirmed_aliases(memory, CAMPAIGN), {})

    async def test_a_spelling_a_person_at_the_table_has_is_refused(self) -> None:
        data = LookupData(
            1,
            (entity(ALDRIC, "Aldric Vane"),),
            (alias(ALDRIC, "Aldric Vane"), alias(ALDRIC, "the Ashen King")),
            (), (), (), (),
            people=(alias("p" * 32, "the Ashen King"),),
        )  # fmt: skip
        memory = SimpleNamespace(lookup_data=AsyncMock(return_value=data))
        self.assertEqual(await confirmed_aliases(memory, CAMPAIGN), {})

    async def test_a_bare_title_or_a_short_word_is_no_alias(self) -> None:
        memory = lookup_of(
            alias(ALDRIC, "the King"), alias(ALDRIC, "Ash"), alias(ALDRIC, "the Ashen King")
        )
        self.assertEqual(await confirmed_aliases(memory, CAMPAIGN), ALIASES)
        self.assertFalse(usable_alias("the king"))
        self.assertFalse(usable_alias("ash king"))
        self.assertTrue(usable_alias("the ashen king"))
        self.assertTrue(usable_alias("grey wolf"))

    async def test_a_secret_alias_is_never_listed(self) -> None:
        memory = lookup_of(secret=(alias(ALDRIC, "the hooded stranger", secret=True),))
        self.assertEqual(await confirmed_aliases(memory, CAMPAIGN), {})

    async def test_an_alias_not_yet_confirmed_is_not_listed(self) -> None:
        memory = lookup_of(alias(ALDRIC, "the Ashen King", status="proposed"))
        self.assertEqual(await confirmed_aliases(memory, CAMPAIGN), {})

    async def test_no_aliases_is_an_empty_map(self) -> None:
        self.assertEqual(await confirmed_aliases(lookup_of(), CAMPAIGN), {})
