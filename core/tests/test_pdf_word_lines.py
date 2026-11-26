"""A PDF that draws each word on its own (Google Docs' "Download as PDF" does) is read as lines
and paragraphs, not one word per line (the owner's report, 2026-11-13: the same lore as a PDF gave
far fewer names and connections than as a Google Doc; every two-word name came apart). A PDF the
plain reading already reads well, such as a book in two columns, is read as before. No network:
small PDFs made here. Invented words only."""

from __future__ import annotations

import io
import unittest

from dmbot.memory import name_documents as nd


def _pdf(contents: list[str]) -> bytes:
    """A real, minimal PDF, one page for each content stream (no outside library)."""
    n = len(contents)
    kids = " ".join(f"{4 + 2 * i} 0 R" for i in range(n))
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{kids}] /Count {n} >>".encode(),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier >>",  # (fixed width: exact gaps),
    ]
    for i, content in enumerate(contents):
        objs.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {5 + 2 * i} 0 R"
            " /Resources << /Font << /F1 3 0 R >> >> >>".encode()
        )
        objs.append(f"<< /Length {len(content)} >>\nstream\n{content}\nendstream".encode())
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for k, body in enumerate(objs, 1):
        offsets.append(out.tell())
        out.write(f"{k} 0 obj\n".encode() + body + b"\nendobj\n")
    start = out.tell()
    out.write(f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode())
    for off in offsets:
        out.write(f"{off:010d} 00000 n \n".encode())
    out.write(
        f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{start}\n%%EOF".encode()
    )
    return out.getvalue()


CHAR = 14.67 * 0.6  # one Courier letter at the size drawn, in the text's own units


def word_by_word(paragraphs: list[list[str]]) -> str:
    """Each word drawn on its own, as Google Docs draws a page: the page turned upside down and
    back (a mirrored y), each line in its own scaled box, every few words a new text block that
    starts again from the line's left edge, and the words between moved along to the right."""
    parts, y = ["1 0 0 -1 0 792 cm"], 72.0
    for lines in paragraphs:
        for line in lines:
            words = line.split()
            parts.append(f"q 0.75 0 0 0.75 72 {y} cm")
            x = 0.0
            for n, word in enumerate(words):
                if True:  # (a new block for each word, placed from the line's left edge)
                    parts.append(
                        f"BT /F1 14.67 Tf 1 0 0 -1 0 0.48 Tm {x:.2f} -13.28 Td ({word}) Tj"
                    )
                else:
                    parts.append(f"{CHAR * (len(words[n - 1]) + 1):.2f} 0 Td ({word}) Tj")
                x += CHAR * (len(word) + 1)
                if True:
                    parts.append("ET")
            parts.append("Q")
            y += 14.5
        y += 14.5  # (a blank line between paragraphs)
    return " ".join(parts)


def two_columns(left: list[str], right: list[str]) -> str:
    """A book's page: the left column's lines, then the right column's (the reading order)."""
    out = []
    for x, lines in ((40, left), (320, right)):
        out.append(f"BT /F1 10 Tf {x} 760 Td 14 TL " + " ".join(f"({t}) Tj T*" for t in lines))
        out.append("ET")
    return " ".join(out)


TOWN = [
    [f"On day {day} Bryn Shander was the biggest of the Ten Towns and sat high",
     f"above the frozen road, where {day + 2} caravans stopped for the night."]
    if day % 2
    else [f"Tolly Brightwater kept the inn, and on day {day} the Gull's Rest was",
          "full of fishers waiting for the ice to break on the lake."]
    for day in range(24)
]  # fmt: skip
WORDS = sum(len(line.split()) for lines in TOWN for line in lines)


class WordByWord(unittest.TestCase):
    def test_the_plain_reading_comes_apart_or_pypdf_has_mended_it(self) -> None:
        from pypdf import PdfReader

        raw = _pdf([word_by_word(TOWN)])
        plain = PdfReader(io.BytesIO(raw)).pages[0].extract_text()
        if nd.words_come_apart(plain):
            # (what the owner saw with older pypdf, and why this reader exists: one word a line)
            self.assertNotIn("Bryn Shander", plain)
        else:
            # pypdf 7.2 reads such a page in lines itself (#5344): the reader has nothing to mend
            self.assertIn("On day 1 Bryn Shander was the biggest of the Ten Towns", plain)

    def test_read_as_lines_and_paragraphs(self) -> None:
        text = nd._pdf_text(_pdf([word_by_word(TOWN)]))
        self.assertIn("On day 1 Bryn Shander was the biggest of the Ten Towns", text)
        self.assertIn("Tolly Brightwater", text)
        lines = [line for line in text.split("\n") if line.strip()]
        self.assertEqual(len(lines), 24)  # (each paragraph's two lines joined into one, #4307)
        self.assertIn("sat high above the frozen road", text)
        self.assertEqual(len(text.split()), WORDS)
        self.assertNotIn("   ", text)  # (no wide gaps left between words)
        # (the paragraphs stay apart: each line is one paragraph, whether or not the reading puts
        # a blank line between them: older pypdf's layout reading does, 7.2's plain one does not)
        self.assertTrue(all(line.startswith(("On day", "Tolly")) for line in lines))

    def test_the_page_count_and_picture_pages_are_kept(self) -> None:
        got = nd._pdf_text(_pdf([word_by_word(TOWN), word_by_word(TOWN)]))
        self.assertEqual((got.pages, got.picture_pages), (2, 0))


class Reflow(unittest.TestCase):
    """#4307: a paragraph wrapped at the page's edge is one paragraph again, for the lore reader,
    whose paragraphs are lines; headings, list items and short lines stay where they are."""

    def test_wrapped_lines_join_and_the_rest_stays(self) -> None:
        page = "\n".join([
            "Cerric Vanya'elen (Elf Wizard)",
            "Cerric grew up in the forests near Lonelywood, where his older",
            "sister taught him all of the old songs of the Caer-",
            "Konig shrine. He speaks Elvish and Common.",
            "",
            "- a sword of the north",
            "Bryn Shander",
            "Bryn Shander is the biggest of the Ten Towns and sits on a hill",
            "above the road.",
        ])  # fmt: skip
        self.assertEqual(
            nd.reflow(page).split("\n"),
            [
                "Cerric Vanya'elen (Elf Wizard)",
                "Cerric grew up in the forests near Lonelywood, where his older sister taught"
                " him all of the old songs of the Caer-Konig shrine. He speaks Elvish and Common.",
                "",
                "- a sword of the north",
                "Bryn Shander",
                "Bryn Shander is the biggest of the Ten Towns and sits on a hill above the road.",
            ],
        )

    def test_nothing_is_added_or_lost(self) -> None:
        page = (
            "one two three four five six seven eight nine ten eleven Caer-\n"
            "Konig twelve thirteen fourteen fifteen sixteen seventeen -\n"
            "eighteen nineteen.\nthirteen"
        )
        got = nd.reflow(page)
        self.assertEqual("".join(got.split()), "".join(page.split()))
        self.assertIn("Caer-Konig", got)
        self.assertIn("seventeen - eighteen", got)  # (a dash is not a broken word)

    def test_short_lines_stay_apart(self) -> None:
        for page in (
            "Bryn Shander\nTolly Brightwater\nCerric Vanya\nDuvessa Shane\nSephek Kaltro",
            "Contents\nChapter one: the Ten Towns 4\nChapter two: the Spine 12\nIndex 40",
            "",
        ):
            with self.subTest(page=page):
                self.assertEqual(nd.reflow(page), page)

    def test_lists_and_page_numbers_stay_apart(self) -> None:
        cut = "The road north from Bryn Shander runs past the lakes and the old"
        for after in ("a) the first way out of the dale", "— the other road", "12"):
            with self.subTest(after=after):
                self.assertEqual(nd.reflow(f"{cut}\n{after}"), f"{cut}\n{after}")


class PlainReadingJoined(unittest.TestCase):
    """#4307 itself: a page the plain reading reads well, one paragraph wrapped over three lines at
    the page's edge, comes out of the PDF reader as one line (one paragraph for the lore reader)."""

    def test_wrapped_paragraph_is_one_line(self) -> None:
        lines = [
            "Cerric Vanya grew up near Lonelywood where his older sister",
            "taught him the old songs of the shrine and the lake roads to",
            "Bryn Shander. He speaks Elvish and Common.",
            "",
            "Duvessa Shane is the speaker of Bryn Shander and its council.",
        ]
        content = "BT /F1 10 Tf 40 760 Td 14 TL " + " ".join(f"({t}) Tj T*" for t in lines) + " ET"
        got = [line for line in nd._pdf_text(_pdf([content])).split("\n") if line.strip()]
        self.assertEqual(len(got), 2)
        self.assertIn("sister taught him", got[0])
        self.assertIn("lake roads to Bryn Shander.", got[0])


class ReadAsBefore(unittest.TestCase):
    def test_a_book_in_two_columns_reads_column_by_column(self) -> None:
        left = [f"Left line {n} of the first column." for n in range(40)]
        right = [f"Right line {n} of the second column." for n in range(40)]
        text = nd._pdf_text(_pdf([two_columns(left, right)]))
        self.assertIn("Left line 39 of the first column.\nRight line 0", text)
        self.assertNotIn("first column.   Right", text)

    def test_columns_drawn_word_by_word_keep_the_plain_reading(self) -> None:
        # (the layout reading would run the two columns together: the gutter keeps it out)
        rows = [f"Left {n} side words here" for n in range(40)]
        drawn = ["1 0 0 -1 0 792 cm"]
        for n, row in enumerate(rows):
            for x0 in (0.0, 300.0):  # (the same row in each column, the right one far off)
                x = x0
                for word in row.split():
                    drawn.append(
                        f"q 0.75 0 0 0.75 72 {72 + 14.5 * n} cm BT /F1 14.67 Tf"
                        f" 1 0 0 -1 0 0.48 Tm {x:.2f} -13.28 Td ({word}) Tj ET Q"
                    )
                    x += CHAR * (len(word) + 1)
        raw = _pdf([" ".join(drawn)])
        from pypdf import PdfReader

        plain = PdfReader(io.BytesIO(raw)).pages[0].extract_text()
        got = str(nd._pdf_text(raw))
        # (every word kept, none added, and no wide gaps: the layout reading would leave them. With
        # older pypdf the result is the plain reading itself; 7.2's plain reading already puts a
        # row's two columns on one line, and joining its lines is the reflow's ordinary work)
        self.assertEqual(sorted(got.split()), sorted(plain.split()))
        self.assertNotIn("   ", got)
        if nd.words_come_apart(plain):
            self.assertEqual(got, plain)

    def test_a_short_list_of_names_reads_as_before(self) -> None:
        names = ["Mara", "Tolly", "Bram", "Ilse"]
        content = "BT /F1 10 Tf 40 760 Td 14 TL " + " ".join(f"({t}) Tj T*" for t in names) + " ET"
        self.assertEqual(nd._pdf_text(_pdf([content])).split(), names)


class TheTest(unittest.TestCase):
    def test_word_per_line_is_spotted_only_when_it_is(self) -> None:
        broken = "\n\n".join("word" for _ in range(200))
        self.assertTrue(nd.words_come_apart(broken))
        self.assertFalse(nd.words_come_apart("A whole line of words.\n" * 200))
        self.assertFalse(nd.words_come_apart("Mara\nTolly\nBram\n"))  # (a short list of names)
