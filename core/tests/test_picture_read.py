"""The words read from a document's pictures, handed to the lore read (#4003): the extra pages and
each name sorted against the document's own. Pure, invented names."""

from __future__ import annotations

import asyncio
import time
import unittest
from types import SimpleNamespace
from typing import Any

from dmbot.memory import background as bg
from dmbot.memory.picture_read import MAX_PICTURES, PictureText, as_pages, sort


def found(name: str, kind: str = "npc") -> bg.Found:
    return bg.Found(name, kind, (), (), "")


DOC = [found("Sephek Kaltro"), found("Syllar Sparkwalker"), found("Termalaine", "place")]
TEXT = "Sephek Kaltro walked to Termalaine past the Blue Clam."
MAP = PictureText(3, "p" * 32, "Termalaine\nBlue Clam\nRed Larch", "map")
TABLE = PictureText(5, "", "Name | Role\nSephik Kaltro | guard", "table")


class Pages(unittest.TestCase):
    def test_each_picture_is_a_page_in_page_order_with_what_it_is(self) -> None:
        text = as_pages([TABLE, MAP])
        self.assertTrue(text.startswith("[A map on page 3]\nTermalaine"))
        self.assertIn("\n\n[A table on page 5]\nName | Role", text)

    def test_no_id_reaches_the_reader_and_an_empty_picture_is_left_out(self) -> None:
        text = as_pages([MAP, PictureText(4, "x" * 32, "  ")])
        self.assertNotIn("p" * 32, text)
        self.assertNotIn("page 4", text)

    def test_an_unknown_kind_is_a_picture(self) -> None:
        self.assertTrue(as_pages([PictureText(1, "", "x", "poster")]).startswith("[A picture"))

    def test_past_the_cap_the_first_pages_are_read(self) -> None:
        many = [PictureText(n, "", f"w{n}") for n in range(MAX_PICTURES + 5, 0, -1)]
        text = as_pages(many)
        self.assertIn(f"page {MAX_PICTURES}]", text)
        self.assertNotIn(f"page {MAX_PICTURES + 1}]", text)


class Sorting(unittest.TestCase):
    def test_a_name_in_the_document_or_its_text_is_kept_under_that_spelling(self) -> None:
        out = sort([found("termalaine", "place"), found("Blue Clam", "place")], DOC, TEXT,
                   [MAP])  # fmt: skip
        self.assertEqual([f.name for f in out.kept], ["Termalaine", "Blue Clam"])
        self.assertEqual((out.held, sum(out.corrected.values())), ([], 0))

    def test_a_plain_misreading_of_one_document_name_is_corrected_not_kept(self) -> None:
        out = sort([found("Sephik Kaltro")], DOC, TEXT, [TABLE])
        self.assertEqual((out.kept, out.held), ([], []))
        self.assertEqual(sum(out.corrected.values()), 1)

    def test_anything_else_is_held_for_the_dm_with_its_picture(self) -> None:
        out = sort([found("Red Larch", "place")], DOC, TEXT, [TABLE, MAP])
        (held,) = out.held
        self.assertEqual((held.found.name, held.page, held.picture_id), ("Red Larch", 3, "p" * 32))
        self.assertEqual(out.kept, [])

    def test_a_held_name_no_picture_holds_has_no_link(self) -> None:
        (held,) = sort([found("Zorbo")], DOC, TEXT, [MAP]).held
        self.assertEqual((held.page, held.picture_id), (0, ""))


class Extras(unittest.TestCase):
    def test_a_matched_name_keeps_nothing_else_the_picture_gave(self) -> None:
        picture = bg.Found("termalaine", "place", ("Red Larch",), ("The Hidden Town",), "x", "y")
        (kept,) = sort([picture], DOC, TEXT, [MAP]).kept
        self.assertEqual(kept, bg.Found("Termalaine", "place", (), (), "", ""))

    def test_a_short_name_never_links_the_picture_of_a_longer_one(self) -> None:
        kal = PictureText(2, "k" * 32, "Kaltro's camp")
        (held,) = sort([found("Kal")], DOC, TEXT, [kal]).held
        self.assertEqual((held.page, held.picture_id), (0, ""))


class Wiring(unittest.IsolatedAsyncioTestCase):
    """`name_background._read_pictures`: only with the switch on, pictures given and time left, one
    more lore read over the pictures' pages; only matched names join the read; the wait's step and
    counts only move forward; the log has counts, never a picture's words."""

    async def run_it(
        self, on: bool, pictures: tuple[PictureText, ...], left: float = 600.0, slow: bool = False
    ) -> tuple[list[str], list[str], Any, list[tuple[str, int, int]], list[str]]:
        from unittest.mock import patch

        from dmbot.memory import lore_read
        from dmbot.ui import name_background
        from dmbot.ui.name_lists import Upload

        asked: list[str] = []

        async def fake_read(*args: object, **kwargs: Any) -> lore_read.LoreRead:
            asked.append(str(args[2]))
            told = kwargs.get("progress")
            if told is not None:
                told("reading", 0, 0)
                told("counts", 40, 1)
                told("shaping", 1, 1)
            if slow:
                await asyncio.sleep(5)
            names = [found("Blue Clam", "place"), found("Sephik Kaltro"), found("Red Larch")]
            return lore_read.LoreRead(names, [], False, lore_read.Stats())

        steps: list[tuple[str, int, int]] = []
        bot = SimpleNamespace(settings=SimpleNamespace(picture_names_on=on))
        read = lore_read.LoreRead(list(DOC), [], False, lore_read.Stats())
        upload = Upload(TEXT, "map.pdf", True, pictures=pictures)
        told = name_background._picture_progress(lambda *a: steps.append(a), [1000, 7])
        with (
            patch.object(lore_read, "read_lore", fake_read),
            self.assertLogs("dmbot.ui.name_background", "INFO") as logs,
        ):
            got = await name_background._read_pictures(
                bot, upload, read, (None, None), {}, set(), {}, set(), told,
                until=time.monotonic() + left,
            )  # fmt: skip
            name_background.log.info("(end)")  # (so an empty log is not an error)
        return [f.name for f in read.names], asked, got, steps, logs.output

    async def test_off_reads_nothing_more(self) -> None:
        names, asked, got, _, _ = await self.run_it(False, (MAP,))
        self.assertEqual((names, asked, got), ([f.name for f in DOC], [], None))

    async def test_no_pictures_reads_nothing_more(self) -> None:
        self.assertEqual((await self.run_it(True, ()))[1], [])

    async def test_on_keeps_only_matched_names_and_holds_the_rest(self) -> None:
        names, asked, got, _, _ = await self.run_it(True, (MAP,))
        self.assertEqual(asked, [as_pages([MAP])])
        self.assertEqual(names, [*(f.name for f in DOC), "Blue Clam"])
        self.assertEqual([h.found.name for h in got.held], ["Red Larch"])
        self.assertEqual(sum(got.corrected.values()), 1)

    async def test_the_wait_only_moves_forward(self) -> None:
        steps = (await self.run_it(True, (MAP,)))[3]
        self.assertEqual(steps, [("pictures", 0, 0), ("counts", 1040, 8), ("pictures", 1, 1)])

    async def test_the_log_has_counts_never_a_pictures_words(self) -> None:
        log = "\n".join((await self.run_it(True, (MAP,)))[4])
        self.assertIn("1 names kept, 1 corrected, 1 held", log)
        for words in ("Blue Clam", "Red Larch", "Sephik", "Termalaine"):
            self.assertNotIn(words, log)

    async def test_too_little_time_left_reads_nothing_more(self) -> None:
        names, asked, got, _, _ = await self.run_it(True, (MAP,), left=10.0)
        self.assertEqual((asked, got, names), ([], None, [f.name for f in DOC]))

    async def test_out_of_time_keeps_the_texts_read(self) -> None:
        from unittest.mock import patch

        from dmbot.ui import name_background

        with patch.object(name_background, "PICTURES_MIN_S", 0.0):
            names, _, got, _, _ = await self.run_it(True, (MAP,), left=0.05, slow=True)
        self.assertEqual((got, names), (None, [f.name for f in DOC]))
