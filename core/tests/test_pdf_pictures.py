"""An uploaded PDF's pictures, taken out for reading (#4003 slice a; Supervisor's ruling on #4003):
only embedded images big enough to hold something, the largest 4 a page, the same image once, at
most 30 an upload with the rest counted; written into the upload's private folder, which goes with
the upload; and their cost shown before the DM's yes. PDFs made here; no AI, no database."""

from __future__ import annotations

import io
import os
import random
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

from PIL import Image
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from dmbot import credits
from dmbot.config import ConfigError, load_settings
from dmbot.memory import document_reader, pdf_pictures
from dmbot.memory.name_documents import DocumentError, ReadText, text_of
from dmbot.ui import name_background, name_lists
from tests.test_config import BASE


def noisy(width: int, height: int, seed: int) -> Image.Image:
    """A picture that stays big as a JPEG (noise doesn't compress), unlike a flat colour."""
    rng = random.Random(seed)
    return Image.frombytes("RGB", (width, height), rng.randbytes(width * height * 3))


def pdf(*pages: list[Image.Image] | str) -> bytes:
    """A PDF whose pages are each a line of text (a str) or some pictures (a list)."""
    out = PdfWriter()
    for page in pages:
        if isinstance(page, str):
            blank = out.add_blank_page(612, 792)
            font = DictionaryObject({
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            })  # fmt: skip
            blank[NameObject("/Resources")] = DictionaryObject(
                {NameObject("/Font"): DictionaryObject({NameObject("/F1"): out._add_object(font)})}
            )
            stream = DecodedStreamObject()
            stream.set_data(f"BT /F1 12 Tf 72 700 Td ({page}) Tj ET".encode())
            blank[NameObject("/Contents")] = out._add_object(stream)
            continue
        made = io.BytesIO()
        page[0].save(made, "PDF", save_all=True, append_images=page[1:])
        # (Pillow puts each picture on its own page; their images are moved onto one page here)
        one = PdfReader(io.BytesIO(made.getvalue()))
        first: Any = one.pages[0]  # (pypdf's own types can't index a page's resources)
        objects = first["/Resources"]["/XObject"]
        content = first.get_contents().get_data()
        extra: Any
        for n, extra in enumerate(one.pages[1:], start=1):
            for name, ref in extra["/Resources"]["/XObject"].items():
                objects[NameObject(f"{name}x{n}")] = ref
                content += f"\nq 10 0 0 10 0 0 cm {name}x{n} Do Q".encode()
        stream = DecodedStreamObject()
        stream.set_data(content)
        first[NameObject("/Contents")] = stream
        out.add_page(first)
    raw = io.BytesIO()
    out.write(raw)
    return raw.getvalue()


class Taking(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)

    def take(self, raw: bytes) -> tuple[pdf_pictures.Taken, list[str]]:
        taken = pdf_pictures.take(PdfReader(io.BytesIO(raw)), self.dir)
        return taken, sorted(os.listdir(self.dir))

    def test_big_pictures_are_written_and_icons_and_repeats_are_not(self) -> None:
        big = noisy(600, 400, 1)
        raw = pdf("Sunreach is a hot port.", [big], [noisy(40, 40, 2)], [big])
        taken, names = self.take(raw)
        self.assertEqual((taken.found, taken.over), (1, 0))
        self.assertEqual(names, ["0001-p002.jpg"])  # (page 3 holds the same image: read once)
        with Image.open(Path(self.dir) / names[0]) as written:
            self.assertEqual(written.size, (600, 400))

    def test_a_page_gives_its_four_largest_and_counts_the_rest(self) -> None:
        sizes = [(320, 320), (900, 600), (400, 400), (700, 500), (500, 500)]
        raw = pdf("A text page.", [noisy(w, h, i) for i, (w, h) in enumerate(sizes)])
        taken, names = self.take(raw)
        self.assertEqual((taken.found, taken.over), (4, 1))
        self.assertEqual(len(names), 4)
        areas = []
        for n in names:
            with Image.open(Path(self.dir) / n) as written:
                areas.append(written.size[0] * written.size[1])
        smallest = min(areas)
        self.assertGreater(smallest, 320 * 320)  # (the smallest one is the one not read)

    def test_an_upload_gives_at_most_its_share_in_page_order(self) -> None:
        raw = pdf("Text.", *[[noisy(400, 400, i)] for i in range(5)])
        with patch.object(pdf_pictures, "PER_UPLOAD", 3):
            taken, names = self.take(raw)
        self.assertEqual((taken.found, taken.over), (3, 2))
        self.assertEqual([n[6:9] for n in names], ["002", "003", "004"])

    def test_out_of_time_the_rest_are_counted_never_decoded(self) -> None:
        raw = pdf("Text.", [noisy(400, 400, 1)], [noisy(500, 500, 2)])
        taken = pdf_pictures.take(PdfReader(io.BytesIO(raw)), self.dir, budget_s=0)
        self.assertEqual((taken.found, taken.over), (0, 2))
        self.assertEqual(os.listdir(self.dir), [])

    def test_a_text_too_long_anyway_takes_no_pictures(self) -> None:
        raw = pdf("Sunreach is a hot port.", [noisy(600, 400, 1)])
        with self.assertRaises(DocumentError):
            text_of("a.pdf", raw, max_chars=5, pictures_to=self.dir)
        self.assertEqual(os.listdir(self.dir), [])

    def test_a_huge_picture_is_written_smaller(self) -> None:
        raw = pdf("Text.", [noisy(3000, 1500, 7)])
        _taken, names = self.take(raw)
        with Image.open(Path(self.dir) / names[0]) as written:
            self.assertEqual(max(written.size), pdf_pictures.KEEP_SIDE)

    def test_the_text_read_carries_the_counts_and_the_text_stands_alone(self) -> None:
        raw = pdf("Sunreach is a hot port.", [noisy(600, 400, 1)])
        read = text_of("a.pdf", raw, pictures_to=self.dir)
        assert isinstance(read, ReadText)
        self.assertIn("Sunreach", read)
        self.assertEqual((read.pictures_found, read.pictures_over), (1, 0))
        plain = text_of("a.pdf", raw)  # (without a folder, nothing is taken)
        assert isinstance(plain, ReadText)
        self.assertEqual(plain.pictures_found, 0)
        with patch.object(pdf_pictures, "take", side_effect=MemoryError):
            still = text_of("a.pdf", raw, pictures_to=self.dir)
        self.assertIn("Sunreach", still)  # (the pictures go, never the text)


class InTheChild(unittest.IsolatedAsyncioTestCase):
    async def test_the_counts_cross_the_reply_and_the_files_stay_in_the_folder(self) -> None:
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder, True)
        raw = pdf("Sunreach is a hot port.", [noisy(600, 400, 1)], [noisy(500, 500, 2)])
        read = await document_reader.read_document("a.pdf", raw, pictures_to=folder)
        assert isinstance(read, ReadText)
        self.assertEqual((read.pages, read.pictures_found, read.pictures_over), (3, 2, 0))
        self.assertEqual(sorted(os.listdir(folder)), ["0001-p002.jpg", "0002-p003.jpg"])


class TheFolder(unittest.TestCase):
    def setUp(self) -> None:
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        patcher = patch.object(pdf_pictures, "ROOT", root / "dmbot-pictures")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_only_the_childs_names_are_taken_and_close_removes_it(self) -> None:
        files = pdf_pictures.PictureFiles.new()
        self.assertEqual(oct(files.path.parent.stat().st_mode & 0o777), "0o700")
        for name in ("0002-p005.png", "0001-p003.jpg", "notes.txt", "0003-p1.jpg"):
            (files.path / name).write_bytes(b"x")
        self.assertEqual([(f.page, f.path.name) for f in files.files()],
                         [(3, "0001-p003.jpg"), (5, "0002-p005.png")])  # fmt: skip
        files.close()
        self.assertFalse(files.path.exists())

    def test_at_start_up_every_leftover_folder_goes(self) -> None:
        one, two = pdf_pictures.PictureFiles.new(), pdf_pictures.PictureFiles.new()
        (one.path / "0001-p001.jpg").write_bytes(b"x")
        self.assertEqual(pdf_pictures.sweep(), 2)
        self.assertFalse(one.path.exists() or two.path.exists())

    def test_a_root_that_is_a_link_is_refused(self) -> None:
        elsewhere = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, elsewhere, True)
        pdf_pictures.ROOT.symlink_to(elsewhere)
        with self.assertRaises(OSError):
            pdf_pictures.PictureFiles.new()
        self.assertEqual(pdf_pictures.sweep(), 0)


class TheUpload(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        patcher = patch.object(pdf_pictures, "ROOT", root / "dmbot-pictures")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.root = root / "dmbot-pictures"

    async def test_a_pdf_with_pictures_carries_its_folder_until_closed(self) -> None:
        raw = pdf("Sunreach is a hot port.", [noisy(600, 400, 1)])
        upload, problem = await name_lists._document("a.pdf", raw, "a.pdf", pictures=True)
        assert upload is not None and upload.picture_files is not None, problem
        self.assertEqual((upload.pictures_found, len(upload.picture_files.files())), (1, 1))
        upload.close()
        self.assertEqual(list(self.root.iterdir()), [])

    async def test_no_folder_is_left_when_there_is_nothing_or_the_switch_is_off(self) -> None:
        bare, _ = await name_lists._document("a.pdf", pdf("Text only."), "a.pdf", pictures=True)
        assert bare is not None
        self.assertIsNone(bare.picture_files)
        self.assertEqual(list(self.root.iterdir()), [])
        off, _ = await name_lists._document(
            "a.pdf", pdf("Text.", [noisy(600, 400, 1)]), "a.pdf", pictures=False
        )
        assert off is not None
        self.assertEqual((off.picture_files, off.pictures_found), (None, 0))
        self.assertFalse(self.root.exists() and any(self.root.iterdir()))

    async def test_a_document_that_fails_leaves_no_folder(self) -> None:
        upload, problem = await name_lists._document("a.pdf", b"not a pdf", "a.pdf", pictures=True)
        self.assertIsNone(upload)
        self.assertTrue(problem)
        self.assertEqual(list(self.root.iterdir()), [])

    async def test_an_offer_not_sent_leaves_no_folder(self) -> None:
        # (no AI key, not a DM, or the campaign gone: no view owns the upload, so it is closed)
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        raw = pdf("Sunreach is a hot port.", [noisy(600, 400, 1)])
        upload, _ = await name_lists._document("a.pdf", raw, "a.pdf", pictures=True)
        assert upload is not None and upload.picture_files is not None
        campaign = SimpleNamespace(dm_user_ids=frozenset({7}))
        interaction = SimpleNamespace(user=SimpleNamespace(id=7))
        bot = SimpleNamespace(background_ai=None)
        with (
            patch.object(name_background, "_campaign_for", AsyncMock(return_value=campaign)),
            patch.object(name_background, "_bot", return_value=bot),
            patch.object(name_background, "_tell", AsyncMock()),
        ):
            sent = await name_background.offer(cast(Any, interaction), "c" * 32, upload)
        self.assertFalse(sent)
        file = SimpleNamespace(component=SimpleNamespace(values=[object()]))
        modal = SimpleNamespace(campaign_id="c" * 32, file=file)
        bot = SimpleNamespace(
            settings=SimpleNamespace(picture_reading_on=True, picture_names_on=True)
        )
        read = AsyncMock(return_value=(upload, None))
        with (
            patch.object(name_background, "_campaign_for", AsyncMock(return_value=campaign)),
            patch.object(name_background, "_bot", return_value=bot),
            patch.object(name_background, "read_attachment", read),
            patch.object(name_background, "offer", AsyncMock(return_value=False)),
        ):
            interaction.response = SimpleNamespace(defer=AsyncMock())
            await name_background.UploadBackground.on_submit(
                cast(Any, modal), cast(Any, interaction)
            )
        assert read.await_args is not None
        self.assertEqual(read.await_args.kwargs, {"pictures": True})
        self.assertEqual(list(self.root.iterdir()), [])

    async def test_cancel_and_a_dropped_offer_close_the_folder(self) -> None:
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        raw = pdf("Sunreach is a hot port.", [noisy(600, 400, 1)])
        for way in ("cancel", "timeout"):
            upload, _ = await name_lists._document("a.pdf", raw, "a.pdf", pictures=True)
            assert upload is not None and upload.picture_files is not None
            view = name_background.BackgroundOffer("c" * 32, upload)
            if way == "cancel":
                press = SimpleNamespace(response=SimpleNamespace(edit_message=AsyncMock()))
                await view._cancel(cast(Any, press))
            else:
                await view.on_timeout()
            with self.subTest(way=way):
                self.assertFalse(upload.picture_files.path.exists())

    def test_the_offer_shows_the_pictures_cost_and_what_wont_be_read(self) -> None:
        upload = name_lists.Upload("x" * 4000, "a.pdf", True, pictures_found=12, pictures_over=3)
        text = name_background.offer_text(upload, False)
        lore = credits.lore_estimate(4000).credits
        self.assertIn(f"About {lore + 60:,} AI credits for the lore and 12 of its pictures.", text)
        self.assertIn("3 more pictures are skipped: DMbot reads up to 30 a file, and up to 4 a "
                      "page.", text)  # fmt: skip
        self.assertIn(
            "and its 12 pictures.",
            name_background.offer_text(
                name_lists.Upload("x" * 4000, "a.pdf", True, pictures_found=12), False
            ),
        )
        plain = name_background.offer_text(name_lists.Upload("x" * 4000, "a.pdf", True), False)
        self.assertIn("for the lore.", plain)
        self.assertNotIn("picture", plain)


class TheCost(unittest.TestCase):
    def test_five_credits_a_picture_added_to_the_lore(self) -> None:
        lore = credits.lore_estimate(10_000)
        both = credits.lore_and_pictures(10_000, 30)
        self.assertEqual(both.credits, lore.credits + 150)
        self.assertEqual(credits.pictures_words(1), "for the lore and its picture")
        self.assertEqual(
            credits.pictures_words(30, more=True), "for the lore and 30 of its pictures"
        )
        self.assertEqual(name_background.pictures_over_note(1),
                         "1 more picture is skipped: DMbot reads up to 30 a file, and up to 4 a "
                         "page.")  # fmt: skip
        self.assertEqual(credits.lore_and_pictures(10_000, -2).credits, lore.credits)


class TheSwitch(unittest.TestCase):
    def test_off_by_default_and_checked(self) -> None:
        self.assertFalse(load_settings(BASE).picture_reading_on)
        self.assertTrue(load_settings({**BASE, "DMBOT_PICTURE_READING": "1"}).picture_reading_on)
        with self.assertRaises(ConfigError):
            load_settings({**BASE, "DMBOT_PICTURE_READING": "maybe"})


if __name__ == "__main__":
    unittest.main()
