"""What the site's short answer may read (#3856): a fixed allow-list of DMbot's public,
user-facing words, and nothing else.

The list is code on purpose. It names every file that may be read; a file that is not on it (the
repository's process files, `.claude/`, the testing logs, issues, code, anything about sessions,
servers or people) cannot reach a prompt however a question is worded, and a test pins both the
list and the absence of the forbidden names. The files are the website's own page text, the Help
page, the plans file and What's new entries that are live (a `held` entry is not said to be
released). They are read once when the service starts, from the checkout the image was built
from (mounted read-only, like the bot's knowledge, see docker-compose.yml), never fetched live.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

# Relative to the repository root. Every public page's own text, the Help page's, and the plans.
PAGE_FILES: tuple[tuple[str, str, str], ...] = (
    # (file, the page it is on, what to call it)
    ("web/src/pages/index.astro", "/", "The home page"),
    ("web/src/pages/how-it-works.astro", "/how-it-works", "How it works"),
    ("web/src/pages/players.astro", "/players", "For players"),
    ("web/src/pages/install.astro", "/install", "Add to Discord"),
    ("web/src/pages/legal/privacy.astro", "/legal/privacy", "Privacy"),
    ("web/src/pages/legal/terms.astro", "/legal/terms", "Terms of use"),
    ("web/src/pages/legal/refunds.astro", "/legal/refunds", "Refunds"),
    ("web/src/pages/credits.astro", "/credits", "Credits"),
)
# Pages whose words live in a content module (the page just lays them out).
CONTENT_FILES: tuple[tuple[str, str, str], ...] = (
    ("web/src/content/help.ts", "/help", "The Help page"),
    ("web/src/content/pricing.ts", "/pricing", "Prices and questions"),
)
PLANS_FILE = Path(__file__).resolve().parents[1] / "plans.json"  # in the image, with the code
WHATS_NEW_DIR = "web/src/content/whats-new"

# Names that must never be on the list (the test checks every path above against these).
FORBIDDEN = (
    "CLAUDE.md",
    ".claude",
    "testing-status",
    "testing-history",
    "next-test-step",
    "docs/PLAN.md",
    "docs/GO_LIVE.md",
    ".env",
)

PASSAGE_MAX = 900  # characters of one passage: a few sentences a small model reads well
TEXT_MIN = 20  # a string shorter than this in a content file is a label, not a sentence


@dataclass(frozen=True, slots=True)
class Passage:
    """A piece of public text: where it is on the site (`path`, with a section after `#` when
    known), what to call it, and the words."""

    path: str
    title: str
    text: str


_TAG = re.compile(r"<[^>]+>")
_BLOCKS = re.compile(r"<(script|style)\b.*?</\1>", re.S | re.I)
_FRONT = re.compile(r"\A---.*?\n---\n", re.S)
_BRACES = re.compile(r"\{[^{}]*\}")
_SPACE = re.compile(r"\s+")


def _clean(text: str) -> str:
    return _SPACE.sub(" ", text).strip()


def astro_text(source: str) -> str:
    """The words a page shows: its markup without the code, the styles, the tags and the
    `{expressions}` (which are looked up elsewhere, so what they say is read from the content
    module instead)."""
    body = _FRONT.sub("", source, count=1)
    body = _BLOCKS.sub(" ", body)
    for _ in range(4):  # nested braces: peel from the inside
        body = _BRACES.sub(" ", body)
    body = _TAG.sub(" ", body)
    return _clean(body.replace("&amp;", "&").replace("&nbsp;", " "))


def _skip_string(src: str, i: int) -> int:
    """The index after a quoted string that starts at `i` (an escape skips the next character)."""
    quote = src[i]
    i += 1
    while i < len(src) and src[i] != quote:
        i += 2 if src[i] == "\\" else 1
    return i + 1


def _template(src: str, i: int, out: list[str]) -> int:
    """Read a template literal that starts at `i` (a backtick); its text goes to `out`, the
    `${...}` parts are skipped (their code, however nested, is not words). Returns the index
    after it."""
    i += 1
    piece: list[str] = []
    while i < len(src) and src[i] != "`":
        if src[i] == "\\":
            piece.append(src[i + 1 : i + 2])
            i += 2
        elif src.startswith("${", i):
            depth, i = 1, i + 2
            while i < len(src) and depth:
                if src[i] in "\"'":
                    i = _skip_string(src, i)
                    continue
                if src[i] == "`":
                    i = _template(src, i, [])
                    continue
                depth += (src[i] == "{") - (src[i] == "}")
                i += 1
        else:
            piece.append(src[i])
            i += 1
    out.append("".join(piece))
    return i + 1


def module_strings(src: str) -> list[str]:
    """Every string and template literal of a TypeScript module, comments and code skipped."""
    out: list[str] = []
    i = 0
    while i < len(src):
        c = src[i]
        if src.startswith("/*", i):
            end = src.find("*/", i + 2)
            i = len(src) if end < 0 else end + 2
        elif src.startswith("//", i) and (i == 0 or src[i - 1] in " \t\n;,{(["):
            end = src.find("\n", i)
            i = len(src) if end < 0 else end
        elif c in "\"'":
            start = i
            i = _skip_string(src, i)
            out.append(src[start + 1 : i - 1].replace("\\'", "'").replace('\\"', '"'))
        elif c == "`":
            i = _template(src, i, out)
        else:
            i += 1
    return out


def module_text(source: str) -> str:
    """The sentences of a content module: its strings long enough to be a sentence, not its
    code, its names or its labels."""
    found = []
    for raw in module_strings(source):
        text = _clean(raw.replace("\\n", " "))
        if len(text) >= TEXT_MIN and " " in text and not text.startswith(("http", "/", "../", "@")):
            found.append(text)
    return " ".join(found)


def split(path: str, title: str, text: str) -> list[Passage]:
    """The text in passages of at most PASSAGE_MAX characters, cut at a sentence end."""
    sentences = re.split(r"(?<=[.!?])\s+", text)
    passages, current = [], ""
    for sentence in sentences:
        if current and len(current) + len(sentence) + 1 > PASSAGE_MAX:
            passages.append(current)
            current = ""
        current = f"{current} {sentence}".strip()[: PASSAGE_MAX * 2]
    if current:
        passages.append(current)
    return [Passage(path, title, p) for p in passages if len(p) >= TEXT_MIN]


def plans_passages(source: str) -> list[Passage]:
    """The plans as plain sentences, from the same file the prices on the site are built from."""
    try:
        data = json.loads(source)
        plans = data["plans"]
        order = data.get("order") or list(plans)
    except (ValueError, KeyError, TypeError):
        return []
    out = []
    for key in order:
        plan = plans.get(key)
        if not isinstance(plan, dict):
            continue
        cents = plan.get("priceCents")
        if cents is None:
            price = "priced on request"
        elif int(cents) == 0:
            price = "free"
        else:
            price = f"${int(cents) / 100:.2f} a month"
        parts = [f"The {plan.get('name', key)} plan is {price}."]
        if plan.get("hoursPerMonth"):
            parts.append(f"It includes {plan['hoursPerMonth']} hours of listening a month.")
        if plan.get("campaigns"):
            parts.append(f"It allows {plan['campaigns']} campaign(s) at a time.")
        if plan.get("trialDays"):
            parts.append(f"It lasts {plan['trialDays']} days.")
        parts.append("It has campaign backups." if plan.get("backups") else "It has no backups.")
        keep = plan.get("keepAfterLastSession")
        if isinstance(keep, dict) and keep.get("count") and keep.get("unit"):
            parts.append(
                f"A campaign is kept {keep['count']} {keep['unit']}(s) after its last session."
            )
        out.append(Passage("/pricing", "Prices", " ".join(parts)))
    return out


def _held(source: str) -> bool:
    return re.search(r"\bheld\s*:", source) is not None


def collect(root: Path) -> list[Passage]:
    """Every passage, read from the checkout at `root`. A missing file is skipped (with a log
    line naming the file, never its words), so a build without some page still answers from the
    rest."""
    passages: list[Passage] = []

    def read(relative: str) -> str | None:
        path = root / relative
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            log.warning("Site answers: %s was not found", relative)
            return None

    for relative, page, title in PAGE_FILES:
        source = read(relative)
        if source is not None:
            passages += split(page, title, astro_text(source))
    for relative, page, title in CONTENT_FILES:
        source = read(relative)
        if source is not None:
            passages += split(page, title, module_text(source))
    try:
        passages += plans_passages(PLANS_FILE.read_text(encoding="utf-8"))
    except OSError:
        log.warning("Site answers: the plans file was not found")
    folder = root / WHATS_NEW_DIR
    if folder.is_dir():
        for entry in sorted(folder.glob("*.ts"), reverse=True)[:60]:  # the newest 60
            source = entry.read_text(encoding="utf-8")
            if not _held(source):
                passages += split("/whats-new", "What's new", module_text(source))
    return passages


def allowed_paths() -> tuple[str, ...]:
    """Every file the service may read, for the test and for the compose mounts."""
    return (
        *(f for f, _p, _t in PAGE_FILES),
        *(f for f, _p, _t in CONTENT_FILES),
        "core/src/dmbot/plans.json",
        WHATS_NEW_DIR,
    )
