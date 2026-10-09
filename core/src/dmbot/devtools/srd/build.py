"""Builds the rules data files from the SRD 5.2.1 PDF (#866) or the 2014 SRD 5.1 PDF (#873).

    python -m dmbot.devtools.srd PATH/TO/SRD_CC_v5.2.1.pdf
    python -m dmbot.devtools.srd PATH/TO/SRD_CC_v5.1.pdf

Which one it is, the tool reads from the PDF. It writes `spells.json` and `conditions.json`
into `src/dmbot/rules/data/srd52/` or `srd51/` (or `--out`). The same PDF always gives the
same files, so a change shows up in review. Build the 5.2.1 files first: the 5.1 text layer
is rougher, and its cut words are put back using the words of the 5.2.1 data.

Where the PDFs come from: both are Wizards of the Coast's own releases under CC-BY-4.0, at
the addresses in `SOURCE_URL` and `SOURCE_URL_51`. Download one into a folder of its own
(it is not kept in the repository) and give that path. The files record the PDF's SHA-256,
so anyone can check that they were made from the file Wizards published. The attribution
the licence asks for is in `rules/data/srd52/ATTRIBUTION.md` and `srd51/ATTRIBUTION.md`.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any

from dmbot.devtools.srd import parse, parse51
from dmbot.devtools.srd.pdf import Line, read_pages

SOURCE = "SRD 5.2.1"
EDITION = "2024"
LICENCE = "CC-BY-4.0"
SOURCE_URL = "https://media.dndbeyond.com/compendium-images/srd/5.2/SRD_CC_v5.2.1.pdf"
DATA = Path(__file__).resolve().parents[2] / "rules" / "data"
OUT = DATA / "srd52"
SOURCE_51 = "SRD 5.1"
EDITION_51 = "2014"
SOURCE_URL_51 = "https://media.dndbeyond.com/compendium-images/srd/5.1/SRD_CC_v5.1.pdf"
OUT_51 = DATA / "srd51"
SPELLS_HEADING_51 = "Spell Descriptions"
AFTER_SPELLS_HEADING_51 = "Traps"
CONDITIONS_HEADING_51 = "Appendix PH-A:"
AFTER_CONDITIONS_HEADING_51 = "Appendix PH-B:"
CONDITIONS_51 = (
    "Blinded", "Charmed", "Deafened", "Exhaustion", "Frightened", "Grappled", "Incapacitated",
    "Invisible", "Paralyzed", "Petrified", "Poisoned", "Prone", "Restrained", "Stunned",
    "Unconscious",
)  # fmt: skip

CONTENTS_PAGES = 10  # the table of contents names every heading too: look after it
SPELLS_HEADING = "Spell Descriptions"
GLOSSARY_HEADING = "Rules Glossary"
AFTER_GLOSSARY_HEADING = "Gameplay Toolbox"


def first_page_of(pages: Sequence[Sequence[Line]], heading: str, after: int = 0) -> int:
    """The number of the first page, after page `after`, that starts with this heading."""
    for number, lines in enumerate(pages, 1):
        if (
            number > after
            and lines
            and parse51.clean(lines[0].text) == heading
            and parse.is_title(lines[0])
        ):
            return number
    raise parse.SrdError(f"Can't find the heading {heading!r}: is this the SRD it should be?")


def lines_between(pages: Sequence[Sequence[Line]], first: int, last: int) -> list[Line]:
    """The lines of pages `first` up to (not including) `last`, in reading order."""
    return [line for page in pages[first - 1 : last - 1] for line in page]


def sha256_of(path: str) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def which_document(pages: Sequence[Sequence[Line]]) -> str:
    """ "5.2.1" or "5.1", from the PDF's first pages (the running footer names it)."""
    text = parse51.clean(" ".join(line.text for page in pages[:3] for line in page))
    if "System Reference Document 5.2.1" in text:
        return "5.2.1"
    if "System Reference Document 5.1" in text:
        return "5.1"
    raise parse.SrdError("This isn't the System Reference Document 5.2.1 or 5.1.")


def build(path: str) -> dict[str, dict[str, Any]]:
    """The data files' contents, by file name, from the PDF at `path` (5.2.1 or 5.1)."""
    pages = read_pages(path)
    if which_document(pages) == "5.1":
        document = {
            "title": "System Reference Document 5.1",
            "url": SOURCE_URL_51,
            "sha256": sha256_of(path),
            "pages": len(pages),
        }
        known = known_words()
        # The 5.1 words depend on the 5.2.1 data's words: record which data it was.
        document["word_list_sha256"] = word_list_sha256()
        return build_files_51(pages, document, known)
    document = {
        "title": "System Reference Document 5.2.1",
        "url": SOURCE_URL,
        "sha256": sha256_of(path),
        "pages": len(pages),
    }
    return build_files(pages, document)


def known_words() -> set[str]:
    """The words of the 5.2.1 data: the 5.1 reader puts its cut words back using them. The
    5.2.1 data must be there, or the 5.1 files would come out differently."""
    words: set[str] = set()
    for name in ("spells.json", "conditions.json"):
        path = OUT / name
        if not path.exists():
            raise parse.SrdError(f"Build the SRD 5.2.1 data first: {path} is missing.")
        for entry in json.loads(path.read_text(encoding="utf-8"))["entries"]:
            for field in ("name", "text"):
                words.update(w.lower() for w in parse51.WORD.findall(entry[field]))
                words.update(w.lower() for w in parse51.HYPHENATED_WORD.findall(entry[field]))
    return words


def word_list_sha256() -> str:
    """A fingerprint of the 5.2.1 data files that `known_words()` reads."""
    digest = hashlib.sha256()
    for name in ("spells.json", "conditions.json"):
        digest.update((OUT / name).read_bytes())
    return digest.hexdigest()


def folder_for(files: dict[str, dict[str, Any]]) -> Path:
    """Where these files belong: the folder of their edition."""
    return OUT_51 if files["spells.json"]["edition"] == EDITION_51 else OUT


def build_files(
    pages: Sequence[Sequence[Line]], document: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    """The same from pages already read (so it can be tested without the PDF)."""
    spells_at = first_page_of(pages, SPELLS_HEADING, after=CONTENTS_PAGES)
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


def build_files_51(
    pages: Sequence[Sequence[Line]], document: dict[str, Any], known: set[str]
) -> dict[str, dict[str, Any]]:
    """The 2014 SRD 5.1's files from pages already read."""
    spells_at = first_page_of(pages, SPELLS_HEADING_51, after=CONTENTS_PAGES)
    traps_at = first_page_of(pages, AFTER_SPELLS_HEADING_51, after=spells_at)
    conditions_at = first_page_of(pages, CONDITIONS_HEADING_51, after=traps_at)
    pantheons_at = first_page_of(pages, AFTER_CONDITIONS_HEADING_51, after=conditions_at)
    spell_lines = lines_between(pages, spells_at, traps_at)
    condition_lines = lines_between(pages, conditions_at, pantheons_at)
    # Every page of the PDF teaches it which words are words (the book says "her" often).
    vocab = parse51.Vocabulary(known, [line for page in pages for line in page])
    spells = parse51.parse_spells(spell_lines, vocab)
    conditions = parse51.parse_conditions(condition_lines, vocab, CONDITIONS_51)

    def header(kind: str, section: str) -> dict[str, Any]:
        return {
            "source": SOURCE_51,
            "edition": EDITION_51,
            "licence": LICENCE,
            "document": document,
            "kind": kind,
            "section": section,
        }

    return {
        "spells.json": {
            **header("spell", SPELLS_HEADING_51),
            "entries": [asdict(s) for s in spells],
        },
        "conditions.json": {
            **header("condition", "Appendix PH-A: Conditions"),
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
