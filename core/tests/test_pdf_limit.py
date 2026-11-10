"""A PDF may be much bigger than any other file (#4001): illustrated adventures are mostly art that
DMbot never reads. The byte limit is checked before any reading, and every other type keeps 10 MB.
The website takes a PDF up to its own, lower limit. Invented bytes only."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from dmbot.memory import document_reader
from dmbot.memory.name_documents import (
    MAX_DOCUMENT_BYTES,
    MAX_PDF_BYTES,
    DocumentError,
    max_bytes_for,
    text_of,
    too_big_words,
)
from dmbot.web import wizard_check
from tests.test_adventure import pdf

MB = 1024 * 1024


class TheLimit(unittest.TestCase):
    def test_a_pdf_has_its_own_limit_and_every_other_type_keeps_ten(self) -> None:
        self.assertEqual(max_bytes_for("book.pdf"), MAX_PDF_BYTES)
        self.assertEqual(max_bytes_for("BOOK.PDF"), MAX_PDF_BYTES)
        for name in ("notes.docx", "names.txt", "sheet.xlsx", "page.html", "rules.csv"):
            self.assertEqual(max_bytes_for(name), MAX_DOCUMENT_BYTES, name)
        self.assertEqual((MAX_DOCUMENT_BYTES // MB, MAX_PDF_BYTES // MB), (10, 100))

    def test_the_words_name_the_limit_that_applies(self) -> None:
        self.assertIn("up to 100 MB", too_big_words("book.pdf"))
        self.assertIn("up to 10 MB", too_big_words("notes.docx"))
        self.assertIn("Split it", too_big_words("book.pdf"))

    def test_a_pdf_over_ten_mb_and_under_the_limit_is_read(self) -> None:
        body = pdf([["The gate is open. " * 5]])
        padded = body + b"\n%" + b"x" * (15 * MB)  # (a comment after the file's end: pictures)
        self.assertGreater(len(padded), MAX_DOCUMENT_BYTES)
        self.assertIn("The gate is open", text_of("book.pdf", padded))

    def test_a_pdf_over_the_limit_is_refused_with_the_new_words(self) -> None:
        with self.assertRaises(DocumentError) as caught:
            text_of("book.pdf", b"%PDF" + b"x" * MAX_PDF_BYTES)
        self.assertEqual(str(caught.exception), too_big_words("book.pdf"))

    def test_a_fifteen_mb_word_file_is_still_refused_at_ten(self) -> None:
        with self.assertRaises(DocumentError) as caught:
            text_of("notes.docx", b"x" * (15 * MB))
        self.assertIn("up to 10 MB", str(caught.exception))


class TheChild(unittest.IsolatedAsyncioTestCase):
    def test_the_childs_memory_grows_only_for_a_file_over_the_usual_size(self) -> None:
        base = document_reader.MEMORY_BYTES
        self.assertEqual(document_reader.memory_for(0), base)
        self.assertEqual(document_reader.memory_for(10 * MB), base)
        self.assertEqual(document_reader.memory_for(100 * MB), base + 4 * 90 * MB)

    def test_the_time_allowed_grows_only_for_a_file_over_the_usual_size(self) -> None:
        base = document_reader.READ_TIME_S
        self.assertEqual(document_reader.read_time_for(10 * MB), base)
        self.assertEqual(document_reader.read_time_for(100 * MB), base + 0.75 * 90)

    async def test_a_callers_own_time_limit_is_kept(self) -> None:
        seen: list[float] = []

        def fake_run(
            parse: object, name: str, raw: bytes, limit_s: float, *_a: object
        ) -> tuple[str, str, str]:
            seen.append(limit_s)
            return ("ok", "", "words")

        with patch.object(document_reader, "_run", fake_run):
            await document_reader.read_document("a.pdf", b"x" * (20 * MB))
            await document_reader.read_document("a.pdf", b"x" * (20 * MB), limit_s=120.0)
        self.assertEqual(seen, [document_reader.read_time_for(20 * MB), 120.0])


class TheWebsite(unittest.IsolatedAsyncioTestCase):
    def test_the_website_takes_a_smaller_pdf_than_discord_and_ten_mb_for_the_rest(self) -> None:
        self.assertEqual(wizard_check.web_limit("book.pdf"), 50 * MB)
        self.assertEqual(wizard_check.web_limit("notes.docx"), 10 * MB)
        self.assertLess(wizard_check.MAX_WEB_PDF_BYTES, MAX_PDF_BYTES)
        # the body (base64 is a third bigger) stays under the tunnel's 100 MB request limit
        self.assertLess(wizard_check.MAX_BODY_BYTES, 100 * MB)

    def test_the_base64_check_before_decoding_follows_the_file_name(self) -> None:
        self.assertEqual(wizard_check.base64_limit("a.pdf"), 50 * MB * 4 // 3 + 8)
        self.assertEqual(wizard_check.base64_limit("a.docx"), 10 * MB * 4 // 3 + 8)
        self.assertEqual(wizard_check.too_big_file().reason, "too_big")

    async def test_a_big_pdf_is_checked_and_an_over_the_limit_one_is_too_big(self) -> None:
        body = pdf([["The gate is open. " * 5]])
        got = await wizard_check.check_file("book.pdf", body + b"\n%" + b"x" * (12 * MB))
        self.assertEqual((got.status, got.reason), ("ok", ""))
        with patch.object(wizard_check, "MAX_WEB_PDF_BYTES", 11 * MB):
            over = await wizard_check.check_file("book.pdf", body + b"\n%" + b"x" * (12 * MB))
        self.assertEqual(over.reason, "too_big")
        docx = await wizard_check.check_file("notes.docx", b"x" * (11 * MB))
        self.assertEqual(docx.reason, "too_big")
