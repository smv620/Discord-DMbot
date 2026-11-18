"""The invented PDF that scores DMbot's picture reading (#4003 slice b; Supervisor's ruling on
#4003: the switch stays off until dev1's counts are in). Every name and word here is made up.

    python -m dmbot.devtools.picture_cases make OUT.pdf
    python -m dmbot.devtools.picture_cases score OUT.pdf [--key KEY.json] [--json OUT.json]

`make` draws the PDF: a page of text, then a map with five labelled places, a two-column stat
table, a handout letter, a portrait (expected "other"), a decoration too small to read, and a map
drawn as vector lines (nothing to take out: counted as a page of pictures, never read).

`score` takes the pictures out as an upload does (`pdf_pictures.take`), reads each with the fast
model as the bot does (`picture_vision.read_pictures`, ANTHROPIC_API_KEY and the AI_MODEL_*
settings from the environment), and compares the answers with the key
(`core/tests/fixtures/story_graph/picture_cases_key.json`). It prints counts only, for #3545:
the kind right or wrong, the key's words found or missed, words read that the picture doesn't
hold, pictures skipped by reason, and the tokens used against the estimate. It writes nothing
anywhere but `--json`.
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import os
import random
import re
import sys
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

DEFAULT_KEY = (
    Path(__file__).resolve().parents[3]
    / "tests"
    / "fixtures"
    / "story_graph"
    / "picture_cases_key.json"
)
TEXT_PAGE = "The Saltmarsh Reach (invented for DMbot's tests): five villages along a grey coast."
MAP_PLACES = ("Gullhaven", "Brinewick", "Thornmoor", "Ostry Ford", "Kelpie Point")
TABLE_ROWS = (("Mire Hound", ""), ("Armor Class", "13"), ("Hit Points", "22"), ("Speed", "40 ft."))
LETTER = ("To the keeper of Gullhaven:", "The ferry at Ostry Ford is closed until spring.",
          "Signed, Warden Halvard Crane")  # fmt: skip
VECTOR_LABELS = ("Saltgate", "Reedmere")


def _paper(width: int, height: int, seed: int) -> Any:
    """A page-coloured background with a faint grain, so the picture is stored at a real size
    (a flat colour compresses to nothing and would be skipped as an ornament)."""
    from PIL import Image

    rng = random.Random(seed)
    grain = bytes(235 + rng.randrange(20) for _ in range(width * height))
    plain = Image.frombytes("L", (width, height), grain)
    return Image.merge("RGB", (plain, plain, plain.point(lambda v: v - 12)))


def pictures() -> list[tuple[str, Any]]:
    """The fixture's pictures in page order, each with the kind the key expects."""
    from PIL import ImageDraw, ImageFont

    font = ImageFont.load_default(size=30)
    small = ImageFont.load_default(size=26)
    drawn: list[tuple[str, Any]] = []

    town = _paper(1100, 800, 1)
    pen = ImageDraw.Draw(town)
    pen.line(
        [(60, 700), (300, 520), (620, 560), (900, 300), (1040, 120)], fill=(70, 90, 160), width=9
    )
    spots = ((160, 640), (360, 470), (560, 260), (760, 520), (830, 160))
    for (x, y), name in zip(spots, MAP_PLACES, strict=True):
        pen.ellipse((x - 12, y - 12, x + 12, y + 12), fill=(150, 40, 40))
        pen.text((x + 18, y - 16), name, font=font, fill=(30, 30, 30))
    drawn.append(("map", town))

    table = _paper(900, 520, 2)
    pen = ImageDraw.Draw(table)
    for i, (left, right) in enumerate(TABLE_ROWS):
        top = 40 + i * 110
        pen.rectangle((30, top, 870, top + 100), outline=(60, 60, 60), width=3)
        pen.line([(520, top), (520, top + 100)], fill=(60, 60, 60), width=3)
        pen.text((50, top + 32), left, font=font, fill=(20, 20, 20))
        pen.text((540, top + 32), right, font=font, fill=(20, 20, 20))
    drawn.append(("table", table))

    letter = _paper(900, 600, 3)
    pen = ImageDraw.Draw(letter)
    for i, line in enumerate(LETTER):
        pen.text((60, 80 + i * 120), line, font=small, fill=(40, 30, 20))
    drawn.append(("handout", letter))

    portrait = _paper(700, 900, 4)
    pen = ImageDraw.Draw(portrait)
    pen.ellipse((200, 150, 500, 520), fill=(225, 190, 160), outline=(80, 60, 40), width=5)
    pen.ellipse((270, 280, 310, 320), fill=(40, 40, 40))
    pen.ellipse((390, 280, 430, 320), fill=(40, 40, 40))
    pen.arc((290, 380, 410, 450), 20, 160, fill=(120, 40, 40), width=6)
    pen.rectangle((150, 560, 550, 900), fill=(70, 90, 120))
    drawn.append(("other", portrait))

    ornament = _paper(120, 120, 5)  # (too small to read: skipped before any AI call)
    ImageDraw.Draw(ornament).ellipse((20, 20, 100, 100), outline=(90, 70, 40), width=6)
    drawn.append(("too small", ornament))
    return drawn


def make() -> bytes:
    """The fixture PDF's bytes: a text page, one page per picture, then a vector-drawn map."""
    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    out = PdfWriter()

    def text_page(lines: Sequence[str], drawing: bytes = b"") -> None:
        page = out.add_blank_page(612, 792)
        font = DictionaryObject({
            NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        })  # fmt: skip
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): out._add_object(font)})}
        )
        shown = b"".join(
            f"BT /F1 14 Tf 72 {700 - 30 * i} Td ({line}) Tj ET\n".encode()
            for i, line in enumerate(lines)
        )
        stream = DecodedStreamObject()
        stream.set_data(drawing + shown)
        page[NameObject("/Contents")] = out._add_object(stream)

    text_page([TEXT_PAGE])
    for _kind, picture in pictures():
        made = io.BytesIO()
        picture.save(made, "PDF", resolution=150)
        out.add_page(PdfReader(io.BytesIO(made.getvalue())).pages[0])
    # A map drawn as vector shapes: lines and labels, no image to take out.
    roads = b"2 w 100 200 m 300 400 l 500 300 l S 0.6 0.1 0.1 RG 4 w 100 600 m 450 650 l S\n"
    labels = [f"{name}" for name in VECTOR_LABELS]
    text_page(labels, roads)
    raw = io.BytesIO()
    out.write(raw)
    return raw.getvalue()


def _words(text: str) -> list[str]:
    return [w.casefold() for w in re.findall(r"[\w'.]+", text)]


def score(found: Sequence[Any], key: Mapping[str, Any], taken: Any) -> dict[str, Any]:
    """Counts from what the pictures gave against the key (no words of the answers)."""
    expected = {int(p["page"]): p for p in key["pictures"]}
    out: dict[str, Any] = {
        "pictures_taken": taken.found, "pictures_over": taken.over, "kind_right": 0,
        "kind_wrong": 0, "words_found": 0, "words_missed": 0, "words_not_in_picture": 0,
        "other_skipped": 0,
    }  # fmt: skip
    read_pages = {p.page: p for p in found}
    for page, want in sorted(expected.items()):
        got = read_pages.get(page)
        if want["where"] == "other":
            out["other_skipped" if got is None else "kind_wrong"] += 1
            continue
        if got is None or got.where != want["where"]:
            out["kind_wrong"] += 1
            out["words_missed"] += len(want["words"])
            continue
        out["kind_right"] += 1
        said = set(_words(got.text))
        allowed = {w for phrase in want["words"] for w in _words(phrase)} | {"|", "[?]"}
        for phrase in want["words"]:
            hit = all(w in said for w in _words(phrase))
            out["words_found" if hit else "words_missed"] += 1
        out["words_not_in_picture"] += len([w for w in said if w not in allowed])
    return out


async def _score(raw: bytes, key: Mapping[str, Any]) -> dict[str, Any]:
    from pypdf import PdfReader

    from dmbot.ai import AIModelTier, AnthropicClient, Feature
    from dmbot.config import parse_ai_models
    from dmbot.memory import pdf_pictures, picture_vision

    models, _ = parse_ai_models(lambda name: os.environ.get(name, ""))
    client = AnthropicClient(os.environ.get("ANTHROPIC_API_KEY", ""), models, fallback=False)
    with tempfile.TemporaryDirectory() as folder:
        taken = pdf_pictures.take(PdfReader(io.BytesIO(raw)), folder)
        files = pdf_pictures.PictureFiles(Path(folder)).files()
        try:
            seen = await picture_vision.read_pictures(
                client.tier(AIModelTier.FAST, Feature.IMAGE), files, until=float("inf")
            )
        finally:
            await client.close()
    counts = score(seen.pictures, key, taken)
    counts.update(
        cut=seen.cut, failed=seen.failed, not_read=seen.late,
        estimate_credits=5 * taken.found,
    )  # fmt: skip
    return counts


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="what", required=True)
    made = sub.add_parser("make", help="write the invented PDF")
    made.add_argument("out", type=Path)
    scored = sub.add_parser("score", help="read the PDF's pictures with the fast model and score")
    scored.add_argument("pdf", type=Path)
    scored.add_argument("--key", type=Path, default=DEFAULT_KEY)
    scored.add_argument("--json", type=Path)
    args = parser.parse_args(argv)
    if args.what == "make":
        args.out.write_bytes(make())
        print(f"Wrote {args.out}")
        return 0
    key = json.loads(args.key.read_text(encoding="utf-8"))
    counts = asyncio.run(_score(args.pdf.read_bytes(), key))
    for name, value in counts.items():
        print(f"{name}: {value}")
    if args.json is not None:
        args.json.write_text(json.dumps(counts, indent=1) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
