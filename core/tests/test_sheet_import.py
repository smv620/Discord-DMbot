"""Character sheets D2 (#1125): a player's own document or file read into their character by
the AI, through the same allow list and checks as every sheet. The AI is faked and the
documents are made up (a Google Doc export as text, a Word file and a PDF built here)."""

from __future__ import annotations

import io
import json
import unittest
import zipfile
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import discord
from pypdf import PdfWriter

from dmbot import fetch
from dmbot.ai import AIError, Reply
from dmbot.memory import sheet_checks as checks
from dmbot.memory import sheet_import
from dmbot.memory.document_reader import read_document
from dmbot.memory.sheet_store import PlayerCharacter, SheetRefused
from dmbot.ui import sheet_import as ui
from dmbot.ui import sheets as sheets_ui
from tests.test_sheets_ui import CAMPAIGN, ENTITY, GUILD, PLAYER, interaction

SHEET_TEXT = "Belleros\nWood Elf Ranger 3 / Druid 3\nAC 18  HP 26\nSTR 10 DEX 16 CON 14 INT 10"
CHARACTER = PlayerCharacter(CAMPAIGN, "Frostmaiden", ENTITY, "Belleros")


def answer(**more: Any) -> str:
    data: dict[str, Any] = {
        "name": "Belleros",
        "species": "Wood Elf",
        "classes": [
            {"name": "Ranger", "level": 3, "subclass": None},
            {"name": "Druid", "level": 3, "subclass": None},
        ],
        "abilities": {"str": 10, "dex": 16, "con": 14, "int": 10, "wis": 14, "cha": 8},
        "ac": 18,
        "max_hp": 26,
        "hp": 11,
        "temp_hp": 0,
        "feats": ["Alert"],
        "spells": ["Cure Wounds"],
    }
    data.update(more)
    return json.dumps(data)


def docx(*paragraphs: str) -> bytes:
    body = "".join(f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in paragraphs)
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("word/document.xml", f"<w:document><w:body>{body}</w:body></w:document>")
    return out.getvalue()


def text_pdf(line: str) -> bytes:
    """A one-page PDF whose page text is `line` (hand-built: pypdf can read, not write, text)."""
    stream = f"BT /F1 12 Tf 72 720 Td ({line}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out, offsets = b"%PDF-1.4\n", []
    for i, obj in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + obj + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF" % (len(objects) + 1, xref)
    return out


def blank_pdf() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(612, 792)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


class Replies(unittest.TestCase):
    def test_a_good_answer_becomes_a_checked_typed_snapshot(self) -> None:
        snap = sheet_import.parse_reply(answer())
        assert snap is not None
        self.assertEqual((snap["source"], snap["origin"]), ("typed", "document"))
        self.assertEqual(
            (snap["name"], snap["level"], snap["proficiency_bonus"]), ("Belleros", 6, 3)
        )
        self.assertEqual(snap["inventory"], None)  # not found: left out, never made up

    def test_fenced_or_wrapped_json_is_found(self) -> None:
        self.assertIsNotNone(sheet_import.parse_reply(f"Here you go:\n```json\n{answer()}\n```"))
        self.assertIsNone(sheet_import.parse_reply("I could not find a character."))
        self.assertIsNone(sheet_import.parse_reply("{not json}"))
        self.assertIsNone(sheet_import.parse_reply("[1, 2]"))
        self.assertIsNone(sheet_import.parse_reply(json.dumps({"species": "Elf"})))  # no name

    def test_only_the_allowed_fields_survive(self) -> None:
        snap = sheet_import.parse_reply(
            answer(
                secret_notes="LEAK: backstory",
                source="dndbeyond",
                v=99,
                origin="x",
                features=["Rage", "word " * 40],
                flags=["basic"],
                level=20,
            )
        )
        assert snap is not None
        self.assertNotIn("LEAK", json.dumps(snap))
        self.assertEqual((snap["source"], snap["origin"], snap["level"]), ("typed", "document", 6))
        self.assertEqual(snap["flags"], [])  # the AI cannot set the flags
        self.assertEqual(snap["features"], ["Rage"])  # the long one is text, not a name

    def test_a_negative_number_is_flagged_for_the_card(self) -> None:
        snap = sheet_import.parse_reply(answer(hp=-4, abilities={"str": -2}))
        assert snap is not None
        self.assertEqual(snap["flags"], ["abilities", "basic"])
        parts = {c.part: c.status for c in checks.checks(snap)}
        self.assertEqual((parts["basic"], parts["abilities"]), (checks.BAD, checks.BAD))

    def test_what_the_ai_got_wrong_shows_on_the_card(self) -> None:
        snap = sheet_import.parse_reply(
            answer(
                classes=[{"name": "Ranger", "level": 15}, {"name": "Druid", "level": 9}],
                hp=99,
                feats=["Alert", "Clowning"],
                spells=["Cure Wounds", "Xyzzy Plugh"],
            )
        )
        assert snap is not None
        by_part = {c.part: c for c in checks.checks(snap)}
        self.assertEqual(by_part["basic"].status, checks.BAD)  # 24 levels, HP over max
        self.assertEqual(by_part["feats"].status, checks.BAD)
        self.assertEqual(by_part["spells"].status, checks.BAD)
        card = checks.card(snap)
        self.assertIn("⛔ Feats: 'Clowning' not recognized", card)

    def test_a_document_sheet_is_not_marked_broken_for_what_it_does_not_say(self) -> None:
        snap = sheet_import.parse_reply(answer())
        assert snap is not None
        parts = {c.part: c.status for c in checks.checks(snap)}
        self.assertEqual(parts["inventory"], checks.OK)  # "not found in your document"
        self.assertIn(
            "not found in your document", {c.part: c.text for c in checks.checks(snap)}["inventory"]
        )
        self.assertNotIn(checks.BAD, [c.status for c in checks.checks(snap)])


class OddAnswers(unittest.IsolatedAsyncioTestCase):
    def test_odd_shapes_never_raise(self) -> None:
        for odd in (
            {"name": "X", "classes": 5},
            {"name": "X", "classes": [{"name": "Ranger", "level": True}]},
            {"name": "X", "classes": [{"name": "Ranger", "level": 99}] * 9},
            {"name": "X", "abilities": 7, "inventory": [1], "feats": {"a": 1}},
            {"name": "X", "hp": 10**400, "ac": float("nan")},
        ):
            snap = sheet_import.parse_reply(json.dumps(odd, allow_nan=True))
            assert snap is not None, odd
        # the level only counts the classes the allow list keeps
        snap = sheet_import.parse_reply(
            json.dumps(
                {"name": "X", "classes": [{"name": "A", "level": 99}, {"name": "B", "level": 3}]}
            )
        )
        assert snap is not None
        self.assertEqual(snap["level"], 3)

    async def test_a_deeply_nested_answer_is_a_plain_error(self) -> None:
        ai = SimpleNamespace(
            complete=AsyncMock(
                return_value=Reply('{"a":' + "[" * 200_000 + "]" * 200_000 + "}", False)
            )
        )
        with self.assertRaises((AIError,)) as caught:
            await sheet_import.read_character(ai, "hi")  # type: ignore[arg-type]
        self.assertEqual(str(caught.exception), sheet_import.UNCLEAR)


class OneCall(unittest.IsolatedAsyncioTestCase):
    async def test_one_call_with_the_text_capped_and_the_fixed_prompt(self) -> None:
        ai = SimpleNamespace(complete=AsyncMock(return_value=Reply(answer(), False)))
        long = "x" * (sheet_import.TEXT_MAX + 5000)
        snap = await sheet_import.read_character(ai, long)  # type: ignore[arg-type]
        assert snap is not None
        ai.complete.assert_awaited_once()
        system, text = ai.complete.await_args.args
        self.assertEqual(system, sheet_import.PROMPT)
        self.assertEqual(len(text), sheet_import.TEXT_MAX)
        self.assertIn("Never invent", system)

    async def test_an_ai_that_finds_no_character_gives_none(self) -> None:
        ai = SimpleNamespace(complete=AsyncMock(return_value=Reply("nothing here", False)))
        self.assertIsNone(await sheet_import.read_character(ai, "hello"))  # type: ignore[arg-type]


class Documents(unittest.IsolatedAsyncioTestCase):
    async def test_a_word_file_a_pdf_and_a_google_doc_export_are_read(self) -> None:
        self.assertIn("Belleros", await read_document("sheet.docx", docx("Belleros", "Ranger 3")))
        self.assertIn("Belleros", await read_document("sheet.pdf", text_pdf("Belleros Ranger 3")))
        self.assertIn("Belleros", await read_document("sheet.txt", SHEET_TEXT.encode()))


def file(name: str, data: bytes, size: int | None = None) -> Any:
    attachment = MagicMock(spec=discord.Attachment)
    attachment.filename, attachment.size = name, len(data) if size is None else size
    attachment.read = AsyncMock(return_value=data)
    return attachment


class Importing(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        ui._last.clear()
        ui._busy.clear()
        ui._reads.clear()
        self.ai = SimpleNamespace(complete=AsyncMock(return_value=Reply(answer(), False)))

    def it(self) -> Any:
        it = interaction([CHARACTER])
        it.guild = SimpleNamespace(id=GUILD)
        it.client.get_guild = MagicMock(
            return_value=MagicMock(get_member=MagicMock(return_value=MagicMock()))
        )
        it.client.sheet_ai = self.ai
        it.client.plan_gate = AsyncMock(return_value=None)
        it.client.campaigns.get = AsyncMock(
            return_value=SimpleNamespace(
                id=CAMPAIGN, guild_id=GUILD, target_ruleset="2024", fallback_ruleset="2014"
            )
        )
        it.client.sheets_changed = MagicMock()
        return it

    def everything(self, it: Any) -> str:
        sent = [str(c) for c in it.response.send_message.await_args_list]
        return " ".join([*self.texts(it), *sent])

    def texts(self, it: Any) -> list[str]:
        calls = it.followup.send.await_args_list + it.edit_original_response.await_args_list
        return [str(c.args[0] if c.args else c.kwargs.get("content", "")) for c in calls]

    async def test_a_file_is_read_saved_as_names_and_numbers_and_never_kept(self) -> None:
        it = self.it()
        secret = "LEAK: this exact document text must not be stored"
        upload = file("sheet.docx", docx("Belleros", secret))
        await ui.import_sheet(it, GUILD, CHARACTER, attachment=upload)
        self.ai.complete.assert_awaited_once()  # one AI call per import
        it.client.sheets.save.assert_awaited_once()
        args = it.client.sheets.save.await_args
        snapshot = args.args[3]
        self.assertEqual((snapshot["source"], snapshot["origin"]), ("typed", "document"))
        self.assertNotIn("LEAK", json.dumps(snapshot))  # only the checked snapshot is kept
        self.assertIsNone(args.kwargs.get("url"))  # a document sheet has no link
        self.assertEqual(args.kwargs["player"], PLAYER)
        self.assertTrue(any("DMbot read your sheet" in t for t in self.texts(it)))  # the card
        self.assertTrue(
            any("kept the names and numbers, not the file" in t for t in self.texts(it))
        )

    async def test_a_link_is_fetched_with_the_safe_fetcher_and_read(self) -> None:
        it = self.it()
        got = fetch.Fetched(SHEET_TEXT.encode(), "link.txt")
        with patch.object(ui.fetch, "fetch", AsyncMock(return_value=got)) as opened:
            await ui.import_sheet(it, GUILD, CHARACTER, link="https://docs.google.com/document/d/x")
        opened.assert_awaited_once()
        it.client.sheets.save.assert_awaited_once()

    async def test_refusals_save_nothing_and_cost_no_ai_call(self) -> None:
        for upload, expect in (
            (file("big.pdf", b"%PDF", size=6 * 1024 * 1024), ui.TOO_BIG),
            (file("sheet.png", b"png"), ui.WRONG_TYPE),
            (file("sheet.doc", b"doc"), ui.WRONG_TYPE),
        ):
            ui._last.clear()
            it = self.it()
            await ui.import_sheet(it, GUILD, CHARACTER, attachment=upload)
            self.assertIn(expect, self.everything(it))
            it.client.sheets.save.assert_not_awaited()
            upload.read.assert_not_awaited()  # not even downloaded
        self.ai.complete.assert_not_awaited()

    async def test_a_scanned_pdf_is_refused_in_one_plain_line(self) -> None:
        it = self.it()
        await ui.import_sheet(it, GUILD, CHARACTER, attachment=file("scan.pdf", blank_pdf()))
        self.assertIn(sheet_import.PICTURES, self.everything(it))
        self.ai.complete.assert_not_awaited()
        it.client.sheets.save.assert_not_awaited()

    async def test_no_ai_no_import(self) -> None:
        it = self.it()
        it.client.sheet_ai = None
        await ui.import_sheet(it, GUILD, CHARACTER, attachment=file("s.txt", SHEET_TEXT.encode()))
        self.assertIn(ui.NO_AI, str(it.response.send_message.await_args))
        it.client.sheets.save.assert_not_awaited()

    async def test_the_plan_can_refuse_and_a_person_must_wait_between_reads(self) -> None:
        it = self.it()
        it.client.plan_gate = AsyncMock(return_value="Your plan doesn't cover the AI.")
        await ui.import_sheet(it, GUILD, CHARACTER, attachment=file("s.txt", b"x"))
        self.assertIn("plan", str(it.response.send_message.await_args))
        ui._last.clear()
        again = self.it()
        await ui.import_sheet(
            again, GUILD, CHARACTER, attachment=file("s.txt", SHEET_TEXT.encode())
        )
        second = self.it()
        await ui.import_sheet(
            second, GUILD, CHARACTER, attachment=file("s.txt", SHEET_TEXT.encode())
        )
        self.assertIn(ui.WAIT, str(second.response.send_message.await_args))

    async def test_an_ai_that_fails_or_finds_nothing_saves_nothing(self) -> None:
        for reply in (AIError("The AI is busy."), Reply("no character here", False)):
            ui._last.clear()
            it = self.it()
            failing = isinstance(reply, Exception)
            self.ai.complete = AsyncMock(
                side_effect=reply if failing else None, return_value=None if failing else reply
            )
            await ui.import_sheet(
                it, GUILD, CHARACTER, attachment=file("s.txt", SHEET_TEXT.encode())
            )
            it.client.sheets.save.assert_not_awaited()

    async def test_a_character_given_to_someone_else_is_not_overwritten(self) -> None:
        it = self.it()
        it.client.sheets.save = AsyncMock(side_effect=SheetRefused("not yours"))
        await ui.import_sheet(it, GUILD, CHARACTER, attachment=file("s.txt", SHEET_TEXT.encode()))
        self.assertTrue(any(sheets_ui.NOT_YOURS in t for t in self.texts(it)))

    async def test_a_day_has_a_limit_per_server(self) -> None:
        it = self.it()
        import time
        from datetime import UTC, datetime

        day = datetime.fromtimestamp(time.time(), UTC).strftime("%Y-%m-%d")
        ui._reads[(GUILD, day)] = ui.READS_PER_DAY
        await ui.import_sheet(it, GUILD, CHARACTER, attachment=file("s.txt", SHEET_TEXT.encode()))
        self.assertIn(ui.DAILY, str(it.response.send_message.await_args))
        self.ai.complete.assert_not_awaited()

    async def test_the_documents_words_never_reach_the_log(self) -> None:
        it = self.it()
        secret = "LEAK-PRIVATE-SENTENCE"
        with self.assertLogs("dmbot", level="DEBUG") as logs:
            await ui.import_sheet(
                it, GUILD, CHARACTER, attachment=file("s.txt", f"{SHEET_TEXT}\n{secret}".encode())
            )
        self.assertNotIn(secret, "\n".join(logs.output))

    async def test_two_imports_at_once_for_one_server_read_only_once(self) -> None:
        import asyncio

        started = asyncio.Event()

        async def slow(*_a: Any, **_kw: Any) -> bool:
            started.set()
            await asyncio.sleep(0.05)
            return True

        first, second = self.it(), self.it()
        first.user, second.user = MagicMock(id=PLAYER), MagicMock(id=PLAYER + 1)
        for it in (first, second):
            it.client.get_guild = MagicMock(return_value=MagicMock())
        with patch.object(sheets_ui, "_still_member", slow):
            await asyncio.gather(
                ui.import_sheet(
                    first, GUILD, CHARACTER, attachment=file("a.txt", SHEET_TEXT.encode())
                ),
                ui.import_sheet(
                    second, GUILD, CHARACTER, attachment=file("b.txt", SHEET_TEXT.encode())
                ),
            )
        self.assertEqual(self.ai.complete.await_count, 1)
        self.assertIn(ui.BUSY, self.everything(first) + self.everything(second))

    async def test_a_wrong_file_does_not_cost_the_30_second_wait(self) -> None:
        it = self.it()
        await ui.import_sheet(it, GUILD, CHARACTER, attachment=file("sheet.png", b"png"))
        again = self.it()
        await ui.import_sheet(
            again, GUILD, CHARACTER, attachment=file("s.txt", SHEET_TEXT.encode())
        )
        again.client.sheets.save.assert_awaited_once()

    async def test_a_server_that_fetched_nothing_keeps_its_reads(self) -> None:
        it = self.it()
        await ui.import_sheet(it, GUILD, CHARACTER, attachment=file("scan.pdf", blank_pdf()))
        self.assertEqual(sum(ui._reads.values()), 0)  # the AI was never asked

    async def test_a_long_document_says_only_the_start_was_read(self) -> None:
        it = self.it()
        long = (SHEET_TEXT + "\n" + "filler " * 10_000).encode()
        await ui.import_sheet(it, GUILD, CHARACTER, attachment=file("long.txt", long))
        self.assertIn(ui.SHORTENED.strip(), " ".join(self.texts(it)))
        sent = self.ai.complete.await_args.args[1]
        self.assertEqual(len(sent), sheet_import.TEXT_MAX)

    async def test_the_panel_offers_both_ways(self) -> None:
        view = sheets_ui.SheetPanel(GUILD, CHARACTER, has_sheet=False)
        labels = [b.label for b in view.children]
        self.assertIn(sheets_ui.DOC_LABEL, labels)
        self.assertIn(sheets_ui.FILE_LABEL, labels)
        self.assertTrue(all(len(str(label)) <= 25 for label in labels))
