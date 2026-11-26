"""`devtools.lore_shape` (#4307): where a PDF's paragraphs differ from the same text's, in counts
and never words. Small PDFs made here (no outside library). Invented words only."""

from __future__ import annotations

import io
import unittest

from pypdf import PdfReader

from dmbot.devtools import lore_shape
from dmbot.memory import name_documents as nd
from tests.test_pdf_word_lines import _pdf


def page(lines: list[str]) -> str:
    """A page that draws these lines one under another, as a word processor's PDF does."""
    body = " ".join(f"({t}) Tj T*" for t in lines)
    return f"BT /F1 10 Tf 40 760 Td 14 TL {body} ET"


WIDE = "Orrin Vael keeps the lantern of the old mill and walks the"
ON = "towpath every night until the bells of Hollow Reach ring twelve"
SHORT = "and the barge"  # (a line the edge did not cut: the joining leaves it)
LAST = "comes home with the fog."
TOP = "Sefa Dunmore trades salt with the river folk of the lower fens"
END = "and owes the ferryman two silver."

DOC = f"{WIDE} {ON} {SHORT} {LAST}\n{TOP} {END}\n"
PDF = _pdf([page([WIDE, ON, SHORT, LAST]), page([TOP]), page([END])])


class TheShape(unittest.TestCase):
    def test_page_by_page_is_the_upload_itself(self) -> None:
        pages = [nd.page_text(p) for p in PdfReader(io.BytesIO(PDF)).pages]
        self.assertEqual("\n".join(pages), nd.text_of("lore.pdf", PDF))

    def test_the_cuts_and_why(self) -> None:
        doc, pdf = (
            lore_shape.shape_of("lore.txt", DOC.encode()),
            lore_shape.shape_of("lore.pdf", PDF),
        )
        self.assertEqual(len(doc.paragraphs), 2)
        self.assertEqual(pdf.pages, 3)
        found, words = lore_shape.compare_breaks(doc, pdf)
        self.assertEqual(words["same"], len(doc.words))
        self.assertEqual(words["only_first"] + words["only_second"], 0)
        # (the short line stays its own paragraph; the second paragraph runs over a page's end)
        self.assertEqual(found.cuts["short_line"], 1)
        self.assertEqual(found.cuts["page_end"], 1)
        self.assertEqual(sum(found.joined.values()), 0)

    def test_a_page_number_is_a_word_only_the_pdf_has(self) -> None:
        numbered = _pdf([page([WIDE, ON, SHORT, LAST, "7"]), page([TOP]), page([END])])
        doc = lore_shape.shape_of("lore.txt", DOC.encode())
        _, words = lore_shape.compare_breaks(doc, lore_shape.shape_of("lore.pdf", numbered))
        self.assertEqual((words["only_second"], words["places"]), (1, 1))

    def test_the_report_holds_no_word_of_the_text(self) -> None:
        doc = lore_shape.shape_of("lore.txt", DOC.encode())
        lines = lore_shape.report(doc, lore_shape.shape_of("lore.pdf", PDF))
        said = " ".join(lines).casefold()
        for word in {w.strip(".,").casefold() for w in DOC.split()} - {"and", "the", "of"}:
            with self.subTest(word):
                self.assertNotRegex(said, rf"\b{word}\b")
        self.assertIn("page_end 1", said)

    def test_main_reads_two_files(self) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as folder:
            a, b = Path(folder, "lore.txt"), Path(folder, "lore.pdf")
            a.write_text(DOC, encoding="utf-8")
            b.write_bytes(PDF)
            self.assertEqual(lore_shape.main([str(a), str(b)]), 0)
            self.assertEqual(lore_shape.main([str(a), str(Path(folder, "missing.pdf"))]), 2)


class TheEdges(unittest.TestCase):
    """The reviewer's cases: what lies between two pages, one page, a page left a word a line, and
    a PDF that only restricts printing."""

    def test_a_page_number_mid_paragraph_is_a_cut(self) -> None:
        doc = lore_shape.shape_of("lore.txt", f"{WIDE} {ON} {TOP} {END}".encode())
        pdf = lore_shape.shape_of("lore.pdf", _pdf([page([WIDE, ON, "7"]), page([TOP, END])]))
        found, words = lore_shape.compare_breaks(doc, pdf)
        self.assertEqual(sum(found.cuts.values()), 1)  # (seen though only the PDF has the "7")
        self.assertEqual(words["only_second"], 1)

    def test_one_page_is_a_page(self) -> None:
        self.assertEqual(lore_shape.shape_of("lore.pdf", _pdf([page([TOP])])).pages, 1)
        self.assertEqual(lore_shape.shape_of("lore.txt", DOC.encode()).pages, 0)

    def test_a_page_still_a_word_a_line_says_so(self) -> None:
        apart = page([w for w in (WIDE + " " + ON).split()] * 2)  # (30+ lines of one word)
        pdf = lore_shape.shape_of("lore.pdf", _pdf([apart]))
        self.assertIn("not_joined", pdf.ends)
        self.assertEqual(pdf.ends[-1], "page_end")

    def test_a_print_locked_pdf_opens_as_an_upload_opens_it(self) -> None:
        from pypdf import PdfWriter

        writer = PdfWriter(clone_from=PdfReader(io.BytesIO(PDF)))
        writer.encrypt(user_password="", owner_password="owner-only")
        out = io.BytesIO()
        writer.write(out)
        locked = out.getvalue()
        self.assertTrue(PdfReader(io.BytesIO(locked)).is_encrypted)
        shape = lore_shape.shape_of("lore.pdf", locked)
        self.assertEqual(len(shape.words), len(lore_shape.shape_of("lore.txt", DOC.encode()).words))

    def test_a_mismatched_reason_list_is_an_error_never_a_guess(self) -> None:
        with self.assertRaises(ValueError):
            lore_shape._page("one\ntwo", ["page_end"])


class TheReasons(unittest.TestCase):
    """`reflow`'s reasons, one for each line it gives back, with the text itself unchanged."""

    def test_each_line_ends_for_its_reason(self) -> None:
        text = "\n".join(
            [WIDE, ON, SHORT, "Done.", "", WIDE, "- " + ON, WIDE, "12", TOP + ".", "Two words"]
        )
        why: list[str] = []
        self.assertEqual(nd.reflow(text, why), nd.reflow(text))
        self.assertEqual(len(why), len(nd.reflow(text).split("\n")))
        self.assertEqual(
            why,
            # (the item joins the wide line after it; "12" stays, and is short itself)
            ["short_line", "short_line", "blank", "next_is_item", "page_number", "short_line",
             "sentence_end", "page_end"],
        )  # fmt: skip

    def test_a_line_of_few_words_as_wide_as_the_page(self) -> None:
        why: list[str] = []
        nd.reflow("Brightwater-Harrowgate-Underhollow Thornfield-Ravenscroft\nnext line", why)
        self.assertEqual(why, ["few_words", "page_end"])
