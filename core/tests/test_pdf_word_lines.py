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
    def test_the_plain_reading_comes_apart(self) -> None:
        from pypdf import PdfReader

        raw = _pdf([word_by_word(TOWN)])
        plain = PdfReader(io.BytesIO(raw)).pages[0].extract_text()
        self.assertNotIn("Bryn Shander", plain)  # (what the owner saw: why this test exists)

    def test_read_as_lines_and_paragraphs(self) -> None:
        text = nd._pdf_text(_pdf([word_by_word(TOWN)]))
        self.assertIn("On day 1 Bryn Shander was the biggest of the Ten Towns", text)
        self.assertIn("Tolly Brightwater", text)
        lines = [line for line in text.split("\n") if line.strip()]
        self.assertEqual(len(lines), 2 * 24)  # (one line of text for each line drawn)
        self.assertEqual(len(text.split()), WORDS)
        self.assertNotIn("   ", text)  # (no wide gaps left between words)
        self.assertGreaterEqual(text.count("\n\n"), 12)  # (the paragraphs stay apart)

    def test_the_page_count_and_picture_pages_are_kept(self) -> None:
        got = nd._pdf_text(_pdf([word_by_word(TOWN), word_by_word(TOWN)]))
        self.assertEqual((got.pages, got.picture_pages), (2, 0))


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
        self.assertEqual(nd._pdf_text(raw), plain)

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
