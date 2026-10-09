"""Builds the rules data files from the SRD 5.2.1 PDF (#866).

    python -m dmbot.devtools.srd PATH/TO/SRD_CC_v5.2.1.pdf

Writes `spells.json` and `conditions.json` into `src/dmbot/rules/data/srd52/` (or `--out`).
The same PDF always gives the same files, so a change shows up in review.

Where the PDF comes from: the SRD 5.2.1 is Wizards of the Coast's own release under
CC-BY-4.0, at the address in `SOURCE_URL`. Download it into a folder of its own (it is not
kept in the repository) and give that path. The files record the PDF's SHA-256, so anyone
can check that they were made from the file Wizards published. The attribution the licence
asks for is in `rules/data/srd52/ATTRIBUTION.md`.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any

from dmbot.devtools.srd import parse
from dmbot.devtools.srd.pdf import Line, read_pages

SOURCE = "SRD 5.2.1"
EDITION = "2024"
LICENCE = "CC-BY-4.0"
SOURCE_URL = "https://media.dndbeyond.com/compendium-images/srd/5.2/SRD_CC_v5.2.1.pdf"
OUT = Path(__file__).resolve().parents[2] / "rules" / "data" / "srd52"

SPELLS_HEADING = "Spell Descriptions"
GLOSSARY_HEADING = "Rules Glossary"
AFTER_GLOSSARY_HEADING = "Gameplay Toolbox"


def first_page_of(pages: Sequence[Sequence[Line]], heading: str, after: int = 0) -> int:
    """The number of the first page, after page `after`, that starts with this heading."""
    for number, lines in enumerate(pages, 1):
        if (
            number > after
            and lines
            and lines[0].text.strip() == heading
            and parse.is_title(lines[0])
        ):
            return number
    raise parse.SrdError(f"Can't find the heading {heading!r}: is this the SRD 5.2.1?")


def lines_between(pages: Sequence[Sequence[Line]], first: int, last: int) -> list[Line]:
    """The lines of pages `first` up to (not including) `last`, in reading order."""
    return [line for page in pages[first - 1 : last - 1] for line in page]


def sha256_of(path: str) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def build(path: str) -> dict[str, dict[str, Any]]:
    """The data files' contents, by file name, from the PDF at `path`."""
    pages = read_pages(path)
    document = {
        "title": "System Reference Document 5.2.1",
        "url": SOURCE_URL,
        "sha256": sha256_of(path),
        "pages": len(pages),
    }
    return build_files(pages, document)


def build_files(
    pages: Sequence[Sequence[Line]], document: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    """The same from pages already read (so it can be tested without the PDF)."""
    spells_at = first_page_of(pages, SPELLS_HEADING, after=10)  # not the contents page
    glossary_at = first_page_of(pages, GLOSSARY_HEADING, after=spells_at)
    toolbox_at = first_page_of(pages, AFTER_GLOSSARY_HEADING, after=glossary_at)
    spells = parse.parse_spells(lines_between(pages, spells_at, glossary_at))
    conditions = parse.parse_conditions(lines_between(pages, glossary_at, toolbox_at))

    def header(kind: str, section: str) -> dict[str, Any]:
        return {
            "source": SOURCE,
            "edition": EDITION,
            "licence": LICENCE,
            "document": document,
            "kind": kind,
            "section": section,
        }

    return {
        "spells.json": {
            **header("spell", SPELLS_HEADING),
            "entries": [asdict(s) | {"classes": list(s.classes)} for s in spells],
        },
        "conditions.json": {
            **header("condition", GLOSSARY_HEADING),
            "entries": [asdict(c) for c in conditions],
        },
    }


def dump(data: dict[str, Any]) -> str:
    """The file's text: stable, readable in review, with the words as they are."""
    return json.dumps(data, ensure_ascii=False, indent=1) + "\n"


def write(files: dict[str, dict[str, Any]], out: Path = OUT) -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    written = []
    for name, data in files.items():
        target = out / name
        target.write_text(dump(data), encoding="utf-8")
        written.append(target)
    return written
