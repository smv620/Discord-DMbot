"""What DMbot knows about itself, so the sidebar can answer "how do I...?" from the docs that
shipped with the running build (#1195).

The index is built in memory, once, from the documents of the checkout the running image was
built from (mounted read-only by docker-compose: the plan, the README, the website's Help page)
and from the command descriptions in the code itself (see `knowledge_sources`), so there is no
generated file to keep up to date. Nothing is fetched at answer time: a live read of GitHub
would show `development`, which can describe features that are not deployed.

Pure Python, no AI and no network: a keyword ranker (BM25) picks the few chunks that answer a
question, each marked built, planned or decided, so the answer never promises what is not there.
"""

from __future__ import annotations

import logging
import math
import os
import re
from collections import Counter
from dataclasses import dataclass
from functools import cache

REPO_URL = "https://github.com/smv620/Discord-DMbot"

CHUNK_MAX = 1800  # characters: one idea per chunk keeps the prompt small and the ranking sharp
BUDGET = 6000  # characters of chunks added to one prompt (#1195)
# Weighted up because they are written for the people who use DMbot (a nudge, not a rule: the
# golden questions in the tests pin the outcome).
USER_FACING = {"commands": 1.4, "help card": 1.4, "help page": 1.4}
PLANNED_HEADINGS = {"phases", "open decisions"}

log = logging.getLogger(__name__)

BUILT, PLANNED, MIXED, DECIDED = "built", "planned", "mixed", "decided"

# Who decided what, and when, is for the people who build DMbot: never shown to a DM.
_PROCESS_NOTE = re.compile(
    r"\s*\((?=[^()]*(?:\d{4}-\d{2}-\d{2}|#\d+|\b(?:owner|Supervisor|CloudDev|WebDev|dev1|"
    r"RepoManager)\b))[^()]*\)"
)
_ISSUE_REF = re.compile(r"\s*,?\s*#\d{2,5}\b")
_WORD = re.compile(r"[a-z0-9]+")
_STOP = frozenset(
    {
        "a",
        "an",
        "the",
        "and",
        "or",
        "of",
        "to",
        "in",
        "on",
        "at",
        "for",
        "from",
        "with",
        "by",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "do",
        "does",
        "did",
        "can",
        "could",
        "would",
        "should",
        "will",
        "it",
        "its",
        "this",
        "that",
        "these",
        "those",
        "i",
        "me",
        "my",
        "you",
        "your",
        "we",
        "our",
        "they",
        "them",
        "how",
        "what",
        "when",
        "where",
        "which",
        "who",
        "why",
        "if",
        "so",
        "as",
        "not",
        "no",
        "yes",
        "about",
        "any",
        "some",
        "there",
        "here",
        "than",
        "then",
        "dmbot",
    }
)


@dataclass(frozen=True, slots=True)
class Chunk:
    """A piece of documentation: where it came from, what it is called, and whether the
    thing it describes is built."""

    file: str  # "docs/PLAN.md", "README.md", "commands", ...
    heading: str
    anchor: str  # the nearest real heading in the file, for a link ("" when none)
    status: str  # built, planned, mixed or decided
    text: str

    @property
    def words(self) -> list[str]:
        return tokens(f"{self.heading} {self.heading} {self.heading} {self.text}")


def tokens(text: str) -> list[str]:
    """Lower-case words without filler, plural "s" folded ("buttons" finds "button")."""
    out = []
    for word in _WORD.findall(text.lower()):
        if word in _STOP or len(word) < 2:
            continue
        if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
            word = word[:-1]
        out.append(word)
    return out


_SESSIONS = re.compile(
    r"\b(dev1|dev2|dev3|WebDev|CloudDev|Supervisor|RepoManager|pycharm|Master Web Session)\b", re.I
)
_DATE = re.compile(r"\s*\b\d{4}-\d{2}-\d{2}\b")
_SENTENCE = re.compile(r"(?<=[.!?])\s+")


def without_process_notes(text: str) -> str:
    """The text without who-decided-what-when notes: parentheses about an owner or session,
    sentences naming a session, dates and issue numbers. They are for the people who build
    DMbot, and the AI should never be given something it could repeat to a DM."""
    text = _PROCESS_NOTE.sub("", text)
    text = " ".join(x for x in _SENTENCE.split(text) if not _SESSIONS.search(x))
    text = re.sub(r",?\s*\b(decided|chosen|owner)\b(?=\s*(,|$|\.|:|\)))", "", _DATE.sub("", text))
    return _ISSUE_REF.sub("", text)


def slug(heading: str) -> str:
    """GitHub's anchor for a heading."""
    text = re.sub(r"[^\w\- ]", "", heading.lower().strip())
    return text.replace(" ", "-")


def status_of(heading: str, text: str) -> str:
    planned = "Not built yet" in text or heading.lower() in PLANNED_HEADINGS
    built = bool(re.search(r"\*Built\b|\bbuilt\b", text.replace("Not built yet", "")))
    if built and planned:
        return MIXED
    if planned:
        return PLANNED
    return BUILT if built else DECIDED


_HEADING = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
_LEAD = re.compile(r"^\*\*([^*\n]{3,90}?)\.?\*\*")


def chunk_markdown(file: str, text: str) -> list[Chunk]:
    """Split a Markdown document into chunks at its headings and at the bold lead-ins that
    open a paragraph (PLAN.md's feature notes are written that way), each at most CHUNK_MAX
    characters."""
    pieces: list[tuple[str, str, list[str]]] = []  # (heading, anchor heading, lines)
    heading, anchor = file, ""
    lines: list[str] = []
    fenced = False
    previous_blank = True

    def flush() -> None:
        if any(line.strip() for line in lines):
            pieces.append((heading, anchor, lines[:]))
        lines.clear()

    for line in text.splitlines():
        if line.startswith("```"):
            fenced = not fenced
        found = None if fenced else _HEADING.match(line)
        lead = None if fenced or not previous_blank else _LEAD.match(line)
        if found:
            flush()
            heading = anchor = found.group(2).strip()
        elif lead:
            flush()
            heading = lead.group(1).strip()
        lines.append(line)
        previous_blank = not line.strip()
    flush()

    chunks: list[Chunk] = []
    for head, anchor_heading, body in pieces:
        paragraphs = [
            part for block in "\n".join(body).split("\n\n") for part in _split_long(block)
        ]
        current = ""
        for paragraph in [*paragraphs, ""]:
            if current and (len(current) + len(paragraph) > CHUNK_MAX or not paragraph):
                chunks.append(_make(file, head, anchor_heading, current))
                current = ""
            current = f"{current}\n\n{paragraph}".strip() if paragraph else current
    return [c for c in chunks if c.text]


def _split_long(block: str) -> list[str]:
    """A block with no blank line in it (a long list or table) cut at line ends."""
    if len(block) <= CHUNK_MAX:
        return [block]
    parts, current = [], ""
    for line in block.splitlines():
        if current and len(current) + len(line) + 1 > CHUNK_MAX:
            parts.append(current)
            current = ""
        current = f"{current}\n{line}" if current else line
    return [*parts, current] if current else parts


def _make(file: str, heading: str, anchor_heading: str, raw: str) -> Chunk:
    status = status_of(anchor_heading or heading, raw)
    text = " ".join(without_process_notes(raw).replace("**", "").split())
    heading = " ".join(without_process_notes(heading).split())
    return Chunk(file, heading, slug(anchor_heading) if anchor_heading else "", status, text)


class Ranker:
    """BM25 over a list of chunks: the usual k1 and b, no tuning."""

    K1, B = 1.5, 0.75

    def __init__(self, chunks: list[Chunk]) -> None:
        self.chunks = chunks
        self._docs = [Counter(c.words) for c in chunks]
        self._lengths = [sum(d.values()) for d in self._docs]
        self._average = (sum(self._lengths) / len(chunks)) if chunks else 1.0
        frequency: Counter[str] = Counter()
        for doc in self._docs:
            frequency.update(doc.keys())
        count = len(chunks)
        self._idf = {
            term: math.log(1 + (count - n + 0.5) / (n + 0.5)) for term, n in frequency.items()
        }

    def scores(self, question: str) -> list[tuple[float, Chunk]]:
        terms = set(tokens(question))
        ranked = []
        for doc, length, chunk in zip(self._docs, self._lengths, self.chunks, strict=True):
            score = 0.0
            for term in terms:
                count = doc.get(term, 0)
                if count:
                    norm = (
                        count
                        * (self.K1 + 1)
                        / (count + self.K1 * (1 - self.B + self.B * length / self._average))
                    )
                    score += self._idf[term] * norm
            if score:
                ranked.append((score * USER_FACING.get(chunk.file, 1.0), chunk))
        ranked.sort(key=lambda pair: pair[0], reverse=True)
        return ranked


@cache
def load() -> Ranker:
    """The index for this build (empty when the documents were not shipped)."""
    from dmbot.sidebar import knowledge_sources  # (it imports this module)

    try:
        return Ranker(knowledge_sources.collect())
    except Exception:  # broken or odd documents must not break the sidebar
        log.exception("DMbot couldn't read its own documents; answering from the digest only")
        return Ranker([])


# Words that name DMbot or one of its features. Only DMbot's own words: "record", "restore",
# "command", "who can see" and "between sessions" are game words too (the Command spell, a
# rogue unseen), so a bare one must never add DMbot's documents to a rules question.
_NAMES_DMBOT = re.compile(
    r"(\b(dmbot|discord|slash commands?|consent|transcripts?|back ?ups?|dm screen|dm sidebar|"
    r"fightbot|timebot|plotbot|npcbot|entitybot|game clock|game time|character sheets?|"
    r"wake phrase|share codes?|shared rulesets?|recording me|record me|being recorded|"
    r"stop recording|are you recording|how do you work)\b|(^|\s)/\w+|"
    r"\bshare\b.*\b(campaign|server)\b)",
    re.IGNORECASE,
)


def names_dmbot(question: str) -> bool:
    """The question names DMbot itself, a slash command or one of its features."""
    return bool(_NAMES_DMBOT.search(question))


def retrieve(question: str, ranker: Ranker | None = None, budget: int = BUDGET) -> list[Chunk]:
    """The chunks that answer a question about DMbot, best first, up to `budget` characters.
    Nothing for a question that is not about DMbot. The plan is full of game words, so no score can
    tell the two apart (measured on the real index: a game question scored above DMbot ones); the
    question must name DMbot, a slash command or one of its features (`names_dmbot`), and the
    ranking then picks the sections."""
    ranked = (ranker or load()).scores(question)
    if not ranked:
        return []
    if not names_dmbot(question):
        return []
    floor = ranked[0][0] * 0.4  # a far weaker chunk is noise next to the best one
    out: list[Chunk] = []
    used = 0
    for score, chunk in ranked:
        if score < floor or len(out) >= 8:
            break
        if used + len(chunk.text) > budget:
            continue
        out.append(chunk)
        used += len(chunk.text)
    return out


def render(chunks: list[Chunk]) -> str:
    """The chunks as the prompt shows them: what each is, and whether it is built."""
    return "\n".join(f"[{c.status.upper()}] {c.heading}: {c.text}" for c in chunks)


def build_commit() -> str | None:
    """The commit this build was made from (the image gets it as GIT_COMMIT), or None."""
    value = (os.environ.get("GIT_COMMIT") or "").strip().casefold()
    return value if re.fullmatch(r"[0-9a-f]{7,40}", value) else None


def link(chunk: Chunk, commit: str | None = None) -> str | None:
    """One link to the chunk's section on GitHub, at the running build's commit; None when
    the build's commit is not known or the chunk is not from a document in the repository."""
    commit = commit or build_commit()
    in_repo = chunk.file == "README.md" or "/" in chunk.file
    if not commit or not chunk.file.endswith(".md") or not in_repo:
        return None
    suffix = f"#{chunk.anchor}" if chunk.anchor else ""
    return f"{REPO_URL}/blob/{commit}/{chunk.file}{suffix}"
