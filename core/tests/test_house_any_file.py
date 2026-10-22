"""House rules from any file or link (#2377): each format's reader, the AI list kept only if its
quotes are in the file, the numbered format read with no AI, and the offer the DM accepts. No
network and no AI service: stand-ins for both, and small files made here."""

from __future__ import annotations

import io
import json
import unittest
import zipfile
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import yarl
from pypdf import PdfWriter

from dmbot import fetch
from dmbot.ai import Reply
from dmbot.dm_screen import house_sync as hs
from dmbot.memory.name_documents import DocumentError
from dmbot.rules import house_ai, house_reader
from dmbot.rules.house_file_link import FileLink
from dmbot.ui import house_file as file_ui
from tests.test_house_file_sync import rule
from tests.test_house_sync import DM, DOC, FakeLinks, SyncTest, campaign

# ---- small files, made here --------------------------------------------------------------------


def xlsx(*sheets: list[list[str]], shared: bool = True) -> bytes:
    """An Excel file: each sheet a list of rows of cell texts (shared strings or inline)."""
    out = io.BytesIO()
    strings: list[str] = []
    with zipfile.ZipFile(out, "w") as z:
        for number, rows in enumerate(sheets, start=1):
            body = []
            for r, row in enumerate(rows, start=1):
                cells = []
                for c, text in enumerate(row):
                    ref = f"{'ABCDEF'[c]}{r}"
                    if shared:
                        strings.append(text)
                        cells.append(f'<c r="{ref}" t="s"><v>{len(strings) - 1}</v></c>')
                    else:
                        cells.append(f'<c r="{ref}" t="inlineStr"><is><t>{text}</t></is></c>')
                body.append(f'<row r="{r}">{"".join(cells)}</row>')
            z.writestr(
                f"xl/worksheets/sheet{number}.xml",
                f"<worksheet><sheetData>{''.join(body)}</sheetData></worksheet>",
            )
        if shared:
            items = "".join(f"<si><t>{s}</t></si>" for s in strings)
            z.writestr("xl/sharedStrings.xml", f"<sst>{items}</sst>")
    return out.getvalue()


def docx(*paragraphs: str) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        body = "".join(f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in paragraphs)
        z.writestr("word/document.xml", f"<w:document><w:body>{body}</w:body></w:document>")
    return out.getvalue()


def blank_pdf() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


NOTES = (
    "Our table's rules\n"
    "We roll initiative once at the start of a fight and keep the order all session.\n"
    "Healing potions can be drunk as a bonus action instead of an action.\n"
    "Next session is on Friday at seven.\n"
)
QUOTE_ONE = "roll initiative once at the start of a fight"
QUOTE_TWO = "healing potions can be drunk as a bonus action"


def answer(rules: list[dict[str, Any]], book: bool = False) -> str:
    return json.dumps({"published_book": book, "rules": rules})


def one(text: str, quote: str, instead: str | None = None) -> dict[str, Any]:
    return {"text": text, "instead_of": instead, "quote": quote}


GOOD = answer(
    [
        one("Roll initiative once, then keep the order.", QUOTE_ONE, "Roll every round"),
        one("A healing potion is a bonus action.", QUOTE_TWO),
    ]
)


class FakeAI:
    """Answers one text per call, in order; the last repeats. Records what it was asked."""

    def __init__(self, *replies: str | Reply | Exception) -> None:
        self.replies = list(replies) or [GOOD]
        self.calls: list[dict[str, Any]] = []

    async def complete(self, system: str, text: str, **kw: Any) -> Reply:
        self.calls.append({"text": text, **kw})
        item = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if isinstance(item, Exception):
            raise item
        return item if isinstance(item, Reply) else Reply(item, False, 10, 10, "m")


# ---- the readers --------------------------------------------------------------------------------


class TheReaders(unittest.TestCase):
    def test_excel_rows_are_one_line_each_with_filled_cells_joined(self) -> None:
        raw = xlsx(
            [["Initiative", "Roll once", ""], ["Potions", "Bonus action"]], [["Second", "x"]]
        )
        text = house_reader.house_text_of("rules.xlsx", raw)
        self.assertEqual(
            text.splitlines(), ["Initiative | Roll once", "Potions | Bonus action", "Second | x"]
        )

    def test_excel_inline_strings_and_numbers_are_read(self) -> None:
        raw = xlsx([["Crit range", "19"]], shared=False)
        self.assertEqual(house_reader.house_text_of("r.xlsx", raw), "Crit range | 19")

    def test_a_broken_or_empty_excel_file_is_said_in_plain_words(self) -> None:
        for raw, words in ((b"not a zip", "couldn't open that Excel"), (xlsx([[""]]), "no text")):
            with self.assertRaises(DocumentError) as caught:
                house_reader.house_text_of("r.xlsx", raw)
            self.assertIn(words, str(caught.exception))

    def test_a_huge_excel_sheet_is_refused_not_unpacked(self) -> None:
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w") as z:
            z.writestr("xl/worksheets/sheet1.xml", "<c>" * 8_000_000)
        with self.assertRaises(DocumentError) as caught:
            house_reader.house_text_of("r.xlsx", out.getvalue())
        self.assertIn("too big", str(caught.exception))

    def test_csv_rows_join_their_filled_cells(self) -> None:
        raw = b'Rule,Why\n"Roll once, then keep it",fast\n,\nPotions,"bonus action"\n'
        self.assertEqual(
            house_reader.house_text_of("r.csv", raw).splitlines(),
            ["Rule | Why", "Roll once, then keep it | fast", "Potions | bonus action"],
        )
        with self.assertRaises(DocumentError):
            house_reader.house_text_of("r.csv", b",,\n,,\n")

    def test_word_and_text_and_markdown_files_are_read(self) -> None:
        self.assertEqual(
            house_reader.house_text_of("r.docx", docx("One rule", "Two rule")).splitlines()[:2],
            ["One rule", "Two rule"],
        )
        self.assertEqual(house_reader.house_text_of("r.md", b"# Rules\n- one"), "# Rules\n- one")

    def test_a_hostile_run_of_cell_openings_is_read_quickly(self) -> None:
        import time as clock

        out = io.BytesIO()
        with zipfile.ZipFile(out, "w") as z:
            z.writestr("xl/worksheets/sheet1.xml", "<c " * 400_000)  # (no `>` anywhere)
        began = clock.monotonic()
        with self.assertRaises(DocumentError):
            house_reader.house_text_of("r.xlsx", out.getvalue())  # (no text in it)
        self.assertLess(clock.monotonic() - began, 3.0)

    def test_a_scanned_pdf_says_so(self) -> None:
        with self.assertRaises(DocumentError) as caught:
            house_reader.house_text_of("scan.pdf", blank_pdf())
        self.assertEqual(str(caught.exception), house_reader.SCANNED)

    def test_an_empty_file_says_so(self) -> None:
        with self.assertRaises(DocumentError) as caught:
            house_reader.house_text_of("r.txt", b"  \n")
        self.assertIn("no text", str(caught.exception))

    def test_the_fingerprint_ignores_spacing_and_case_and_is_not_the_text(self) -> None:
        a = house_reader.fingerprint_of("Roll  Once\nthen keep it")
        self.assertEqual(a, house_reader.fingerprint_of("roll once then keep it"))
        self.assertNotEqual(a, house_reader.fingerprint_of("roll twice then keep it"))
        self.assertRegex(a, r"^[0-9a-f]{64}$")

    def test_a_google_sheet_link_becomes_its_csv_export_and_keeps_its_tab(self) -> None:
        sheet = "a" * 30
        plain = fetch.direct_url(f"https://docs.google.com/spreadsheets/d/{sheet}/edit")
        self.assertEqual(
            plain, yarl.URL(f"https://docs.google.com/spreadsheets/d/{sheet}/export?format=csv")
        )
        tab = fetch.direct_url(
            f"https://docs.google.com/spreadsheets/d/{sheet}/edit?gid=123#gid=123"
        )
        self.assertTrue(str(tab).endswith("export?format=csv&gid=123"))
        # a link to a spreadsheet is still not a Google Doc for the strict reader
        self.assertIsNone(
            fetch.google_doc_export(f"https://docs.google.com/spreadsheets/d/{sheet}/edit")
        )


# ---- the AI list --------------------------------------------------------------------------------


class TheList(unittest.IsolatedAsyncioTestCase):
    async def read(self, ai: FakeAI, text: str = NOTES, existing: Any = ()) -> house_ai.Found:
        return await house_ai.read(ai.complete, text, existing)

    async def test_rules_with_their_quotes_in_the_file_are_kept(self) -> None:
        found = await self.read(FakeAI())
        self.assertEqual(
            [(c.text, c.instead_of) for c in found.rules],
            [
                ("Roll initiative once, then keep the order.", "Roll every round"),
                ("A healing potion is a bonus action.", None),
            ],
        )

    async def test_an_invented_rule_is_dropped(self) -> None:
        made_up = answer(
            [one("Crits do double damage.", "a natural twenty is a double hit")]
            + json.loads(GOOD)["rules"]
        )
        found = await self.read(FakeAI(made_up))
        self.assertEqual((len(found.rules), found.unquoted), (2, 1))

    async def test_a_quote_matches_whatever_the_spacing_and_case(self) -> None:
        loud = answer([one("Roll once.", "ROLL   INITIATIVE\nONCE at the start of a fight")])
        self.assertEqual(len((await self.read(FakeAI(loud))).rules), 1)

    async def test_a_quote_too_short_to_prove_anything_is_dropped(self) -> None:
        short = answer([one("Roll once.", "initiative")])
        found = await self.read(FakeAI(short))
        self.assertEqual((found.rules, found.unquoted), ((), 1))

    async def test_the_caps_and_repeats_are_applied(self) -> None:
        text = "\n".join(
            f"Table rule number {n:03d} is that we do thing {n:03d}." for n in range(230)
        )
        rules = [
            one(f"Do thing {n:03d}.", f"Table rule number {n:03d} is that we do thing {n:03d}")
            for n in range(230)
        ]
        rules.append(one("Do thing 000.", "Table rule number 000 is that we do thing 000"))  # twice
        rules.append(one("x" * 501, "Table rule number 001 is that we do thing 001"))  # too long
        found = await self.read(FakeAI(answer(rules)), text, existing=[("Do thing 001.", None)])
        self.assertEqual(len(found.rules), 199)  # (200 in all with its own rule)
        self.assertEqual((found.repeats, found.too_long), (2, 1))
        self.assertEqual(found.over_cap, 30)  # 229 new - the 199 that fit beside the one it has

    async def test_a_cut_answer_is_asked_again_with_twice_the_room(self) -> None:
        cut = Reply('{"published_book": false, "rules": [{"text"', True, 10, 10, "m")
        ai = FakeAI(cut, GOOD)
        found = await self.read(ai)
        self.assertEqual(len(found.rules), 2)
        self.assertEqual(ai.calls[1]["max_tokens"], 2 * ai.calls[0]["max_tokens"])

    async def test_an_answer_cut_off_twice_is_unread_and_asked_no_more(self) -> None:
        cut = Reply("{", True, 10, 10, "m")
        ai = FakeAI(cut)
        found = await self.read(ai)
        self.assertTrue(found.unread)
        self.assertEqual(len(ai.calls), 2)

    async def test_the_room_follows_the_size_of_the_text(self) -> None:
        self.assertEqual(house_ai.room_for(10), house_ai.ROOM_MIN)
        self.assertGreater(house_ai.room_for(40_000), house_ai.room_for(10_000))
        self.assertEqual(house_ai.room_for(10**9), house_ai.ROOM_MAX)

    async def test_a_failing_declined_or_garbled_answer_is_unread(self) -> None:
        for bad in (
            RuntimeError("down"),
            Reply("", False, 1, 1, "m", refused=True),
            "not json",
            "[]",
        ):
            self.assertTrue((await self.read(FakeAI(bad))).unread, bad)

    async def test_a_published_book_is_reported_and_lists_nothing(self) -> None:
        found = await self.read(FakeAI(answer([], book=True)))
        self.assertEqual((found.published_book, found.rules, found.unread), (True, (), False))

    async def test_only_counts_are_logged_never_the_text(self) -> None:
        with self.assertLogs("dmbot.rules.house_ai", "INFO") as logged:
            await self.read(FakeAI())
        joined = " ".join(logged.output)
        self.assertNotIn("initiative", joined.casefold())
        self.assertIn("2 kept", joined)


# ---- the offer ----------------------------------------------------------------------------------


class LinksThatRemember(FakeLinks):
    def __init__(self, link: str | None = None) -> None:
        super().__init__(link)
        self.read_fp: str | None = None

    async def get(self, guild_id: int, cid: str) -> FileLink | None:
        if self.link is None:
            return None
        return FileLink(self.link, self.ignored, DM, 1, self.read_fp)

    async def set_read(self, guild_id: int, cid: str, fingerprint: str) -> bool:
        self.read_fp = fingerprint
        return True


class Offering(SyncTest):
    def setUp(self) -> None:
        super().setUp()
        hs._ai_busy.clear()
        hs._ai_reads.clear()
        self.remembering = LinksThatRemember()
        self.links = self.remembering
        self.bot.house_file_links = self.remembering
        self.ai = FakeAI()
        self.bot.house_file_ai = self.ai
        self.bot.plan_allows = AsyncMock(return_value=True)

    async def compare(self, text: str = NOTES, **kw: Any) -> str | None:
        kw.setdefault("from_link", False)
        return await hs.compare_text(self.bot, campaign(), text, self.send, **kw)

    async def test_the_numbered_format_makes_no_ai_call(self) -> None:
        await self.compare("House rules: Frostmaiden\n1. Keep\n2. New words\n9. Fresh\n")
        self.assertEqual(self.ai.calls, [])
        self.assertIn("1 to add", self.sent[-1][0])

    async def test_other_text_is_read_by_one_ai_call_and_offered_not_saved(self) -> None:
        self.assertIsNone(await self.compare(file_name="our-rules.pdf"))
        self.assertEqual(len(self.ai.calls), 1)
        text, view = self.sent[-1]
        self.assertIn("I found 2 new house rules in **our-rules.pdf**", text)
        self.assertIn("Nothing is saved until you press a button.", text)
        self.assertEqual(text.count("press a button"), 1)  # said once
        self.assertEqual(
            [str(b.item.label) for b in view.children],  # type: ignore[attr-defined]
            ["Accept all changes", "Review", "Close", "Download what I found"],
        )
        self.assertEqual(len(self.store.rules), 3)

    async def test_review_comes_first_for_rules_nobody_has_read(self) -> None:
        import discord

        await self.compare()
        _, view = self.sent[-1]
        styles = {str(b.item.label): b.item.style for b in view.children}  # type: ignore[attr-defined]
        self.assertEqual(styles["Review"], discord.ButtonStyle.primary)
        self.assertEqual(styles["Accept all changes"], discord.ButtonStyle.secondary)

    async def test_accepting_adds_the_rules_with_the_files_name_as_their_source(self) -> None:
        await self.compare(file_name="our-rules.pdf")
        _, view = self.sent[-1]
        await self.button(view, "Accept all").callback(self.interaction())
        self.assertEqual(len(self.store.rules), 5)
        self.assertEqual({s for s in self.store.sources}, {("upload", "our-rules.pdf")})

    async def test_the_found_rules_can_be_downloaded_in_the_numbered_format(self) -> None:
        await self.compare(file_name="our-rules.pdf")
        _, view = self.sent[-1]
        it = self.interaction()
        await self.button(view, "Download what I found").callback(it)
        sent = it.response.sent[-1][1]
        body = sent["file"].fp.read().decode()
        self.assertIn(
            "4. Roll initiative once, then keep the order. (instead of: Roll every round)", body
        )
        self.assertTrue(sent["ephemeral"])
        self.assertEqual(it.response.sent[-1][0], hs.FOUND_FILE)

    async def test_a_player_cannot_download_it(self) -> None:
        await self.compare()
        _, view = self.sent[-1]
        it = self.interaction(user_id=8)
        await self.button(view, "Download what I found").callback(it)
        self.assertEqual(it.response.sent[-1][0], hs.ONLY_DMS)

    async def test_rules_the_campaign_already_has_are_not_offered_again(self) -> None:
        self.store.rules[:] = [
            *self.store.rules,
            rule(4, "A healing potion is a bonus action."),
        ]
        await self.compare()
        self.assertIn(
            "I found 1 new house rule in your file** (1 more already in your list, left out)",
            self.sent[-1][0],
        )

    async def test_a_rulebook_gets_its_line_and_no_offer(self) -> None:
        self.ai.replies = [answer([], book=True)]
        self.assertEqual(await self.compare(), hs.BOOK)
        self.assertEqual(self.sent, [])

    async def test_nothing_found_and_an_unread_answer_are_said(self) -> None:
        self.ai.replies = [answer([])]
        self.assertEqual(await self.compare(), hs.NOTHING_FOUND)
        self.ai.replies = ["garbage"]
        self.assertEqual(await self.compare(), hs.NOT_READ)
        self.assertEqual(self.sent, [])

    async def test_without_the_ai_or_a_plan_that_has_it_a_file_without_numbers_is_said(
        self,
    ) -> None:
        self.bot.plan_allows = AsyncMock(return_value=False)
        self.assertEqual(await self.compare(), hs.NO_AI_PLAN)
        self.assertEqual(self.ai.calls, [])
        self.bot.plan_allows = AsyncMock(return_value=True)
        self.bot.house_file_ai = None
        words = await self.compare()
        assert words is not None
        self.assertIn("couldn't find any house rules", words)

    async def test_an_unchanged_linked_file_makes_no_second_call_at_a_session_start(
        self,
    ) -> None:
        self.links.link = DOC
        await self.compare(from_link=True)
        self.assertEqual(len(self.ai.calls), 1)
        self.assertEqual(self.remembering.read_fp, house_reader.fingerprint_of(NOTES))
        self.assertIsNone(await self.compare(from_link=True))  # a session start: silent
        self.assertEqual(len(self.ai.calls), 1)
        await self.compare(NOTES + "Crits are doubled.\n", from_link=True)  # the file changed
        self.assertEqual(len(self.ai.calls), 2)

    async def test_compare_now_reads_again_so_a_lost_offer_can_be_asked_for(self) -> None:
        self.links.link = DOC
        await self.compare(from_link=True)
        await self.compare(from_link=True, respect_ignore=False)  # the DM asked to look
        self.assertEqual(len(self.ai.calls), 2)

    async def test_an_offer_that_could_not_be_sent_is_not_recorded_as_read(self) -> None:
        self.links.link = DOC
        self.send_works = False
        await self.compare(from_link=True)
        self.assertIsNone(self.remembering.read_fp)
        self.send_works = True
        await self.compare(from_link=True)  # the next session start offers it again
        self.assertEqual(len(self.ai.calls), 2)
        self.assertEqual(len(self.sent), 2)

    async def test_the_campaigns_own_rules_count_against_its_200(self) -> None:
        self.store.rules[:] = [rule(n, f"Own rule {n}") for n in range(1, 200)]  # 199 of 200
        await self.compare()
        text, _ = self.sent[-1]
        self.assertIn("I found 1 new house rule", text)
        self.assertIn("1 more are left out: a campaign holds at most 200", text)

    async def test_an_upload_is_read_every_time(self) -> None:
        await self.compare()
        await self.compare()
        self.assertEqual(len(self.ai.calls), 2)

    async def test_a_read_that_gave_nothing_does_not_use_up_the_days_reads(self) -> None:
        self.ai.replies = ["garbage"]
        await self.compare()
        today = time.strftime("%Y-%m-%d", time.gmtime())
        self.assertEqual(hs._ai_reads[(1, today)], 0)

    async def test_one_read_at_a_time_and_a_daily_limit_for_a_server(self) -> None:
        hs._ai_busy.add(1)
        self.assertEqual(await self.compare(), hs.BUSY)
        hs._ai_busy.clear()
        hs._ai_reads[(1, __import__("time").strftime("%Y-%m-%d", __import__("time").gmtime()))] = (
            hs.HOUSE_AI_READS_PER_DAY
        )
        self.assertEqual(await self.compare(), hs.TOO_MANY_READS)
        self.assertEqual(self.ai.calls, [])

    async def test_the_read_is_not_held_up_for_an_unread_answer_next_time(self) -> None:
        self.links.link = DOC
        self.ai.replies = ["garbage", GOOD]
        self.assertEqual(await self.compare(from_link=True), hs.NOT_READ)
        self.assertIsNone(self.remembering.read_fp)  # a failed read is read again
        self.assertIsNone(await self.compare(from_link=True))
        self.assertEqual(len(self.ai.calls), 2)

    async def test_a_linked_file_in_another_shape_is_read_by_the_reader(self) -> None:
        self.links.link = DOC
        got = fetch.Fetched(
            xlsx([["Roll initiative once at the start of a fight and keep it"]]), "link.xlsx"
        )
        self.ai.replies = [
            answer([one("Roll once.", "Roll initiative once at the start of a fight")])
        ]
        with patch.object(fetch, "fetch", AsyncMock(return_value=got)):
            await hs.check_linked(self.bot, campaign(), self.send, quiet_if_same=True)
        self.assertEqual(len(self.ai.calls), 1)
        self.assertIn("Roll initiative once", self.ai.calls[0]["text"])
        self.assertIn("I found 1 new house rule", self.sent[-1][0])


class Uploading(SyncTest):
    def setUp(self) -> None:
        super().setUp()
        hs._ai_busy.clear()
        hs._ai_reads.clear()
        self.bot.house_file_ai = FakeAI()
        self.bot.plan_allows = AsyncMock(return_value=True)

    def form(self, data: bytes, name: str) -> Any:
        form = file_ui.UploadForm(campaign())
        attachment = SimpleNamespace(
            size=len(data), filename=name, read=AsyncMock(return_value=data)
        )
        form.file.component._values = [attachment]  # type: ignore[attr-defined]
        return form

    async def test_a_word_file_is_read_and_offered_privately(self) -> None:
        raw = docx("We roll initiative once at the start of a fight and keep the order.")
        self.bot.house_file_ai = FakeAI(
            answer([one("Roll once.", "roll initiative once at the start of a fight")])
        )
        it = self.interaction()
        await self.form(raw, "our-rules.docx").on_submit(it)
        self.assertIn(
            "I found 1 new house rule in **our-rules.docx**", it.followup.send.await_args.args[0]
        )

    async def test_a_scanned_pdf_and_an_empty_file_get_their_lines(self) -> None:
        for raw, name, words in (
            (blank_pdf(), "scan.pdf", house_reader.SCANNED),
            (b"   ", "empty.txt", "no text"),
        ):
            file_ui._last.clear()
            it = self.interaction()
            await self.form(raw, name).on_submit(it)
            self.assertIn(words, it.followup.send.await_args.args[0])
        self.assertEqual(self.bot.house_file_ai.calls, [])
