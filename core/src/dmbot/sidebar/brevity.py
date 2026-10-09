"""How short a sidebar answer must be (owner, 2026-10-09: short answers matter most; #934).

The prompt asks for brevity, but the rules also live here, in code, because a model slips:
an answer is at most `MAX_SENTENCES` sentences and `MAX_CHARS` characters (the short source
and "sure" come after, outside the limit) unless the DM asked for the full text. A yes-or-no
question gets "Yes." or "No." first. Greetings, repeated questions and offers of more help
are removed. All of it is pure text work, so it tests with no AI and no Discord.
"""

from __future__ import annotations

import re

MAX_SENTENCES = 2
MAX_CHARS = 200

# Words in the DM's question that mean "give me the whole thing".
_FULL_TEXT = re.compile(
    r"\b(description|full text|full description|whole (spell|rule|text|thing|description|entry)|"
    r"entire (spell|rule|text|description|entry)|read (me )?(out )?(the )?(whole|full|entire)|"
    r"word for word|exact (text|wording)|stat ?block|complete (text|description|entry))\b",
    re.IGNORECASE,
)
# A question that a plain yes or no answers: "do you need…", "can a…", "if you need…".
_YES_NO = re.compile(
    r"^\s*(?:(?:hold on|wait|quick|so|ok|okay|um|uh)[,\s]+)*"
    r"(?:(?:i )?(?:need to |want to )?(?:find|check|look up|know) )?"
    r"(?:(?:if|whether)\b|(?:do|does|did|can|could|is|are|was|were|will|would|should|has|have|"
    r"may|must|need|am)\b)",
    re.IGNORECASE,
)
_ANSWERS_FIRST = re.compile(
    r"^\s*(yes|no|not sure|the free rules don't say|i don't have|your call)\b", re.IGNORECASE
)

# Padding to strip: whole sentences that only fill space.
_PAD_SENTENCE = re.compile(
    r"(let me know|hope (this|that) helps|feel free|happy to help|glad to help|anything else|"
    r"if you (need|want|would like) (more|anything|further|me to)|i can (also )?(help|explain|"
    r"give|read)|would you like)",
    re.IGNORECASE,
)
_PAD_OPENER = re.compile(
    r"^\s*((great|good|excellent|fair|nice|interesting) (question|point)|sure thing|certainly|"
    r"of course|absolutely|sure)\s*[!.,:\-–—]*\s*",
    re.IGNORECASE,
)
# Dots that do not end a sentence.
_ABBREVIATIONS = ("p.", "pp.", "e.g.", "i.e.", "vs.", "etc.", "no.", "cf.", "approx.", "lvl.")


def asks_for_full_text(question: str) -> bool:
    """The DM asked for the whole text ("the spell description", "read me the whole rule")."""
    return bool(_FULL_TEXT.search(question))


def is_yes_no_question(question: str) -> bool:
    return bool(_YES_NO.match(question))


def starts_with_the_answer(text: str) -> bool:
    """A yes-or-no question's answer starts with Yes/No, or says honestly it can't say."""
    return bool(_ANSWERS_FIRST.match(text))


def sentences(text: str) -> list[str]:
    """The text as sentences. A dot after "p." or "e.g." or inside "5.2.1" does not end one."""
    text = " ".join(text.split())
    if not text:
        return []
    out: list[str] = []
    start = 0
    for match in re.finditer(r"[.!?]+[\"')\]]*\s+(?=[A-Z0-9\"'(\[])", text):
        end = match.end()
        piece = text[start : match.start() + len(match.group().rstrip())]
        if piece.lower().endswith(_ABBREVIATIONS):
            continue
        out.append(piece.strip())
        start = end
    tail = text[start:].strip()
    if tail:
        out.append(tail)
    return out


def strip_padding(text: str) -> str:
    """Drop a greeting and sentences that only offer more or fill space."""
    text = _PAD_OPENER.sub("", " ".join(text.split()), count=1)
    kept = [s for s in sentences(text) if not _PAD_SENTENCE.search(s)]
    return " ".join(kept).strip()


def violations(question: str, text: str, *, full_text: bool) -> list[str]:
    """What is wrong with an answer, in words fit to tell the model, or [] if it is fine."""
    if full_text:
        return []
    problems: list[str] = []
    parts = sentences(text)
    if len(parts) > MAX_SENTENCES:
        problems.append(f"use at most {MAX_SENTENCES} sentences")
    if len(text) > MAX_CHARS:
        problems.append(f"use at most {MAX_CHARS} characters")
    if is_yes_no_question(question) and not starts_with_the_answer(text):
        problems.append('start with "Yes." or "No."')
    if text != strip_padding(text):
        problems.append("leave out greetings and offers of more help")
    return problems


def shorten(text: str) -> str:
    """Cut an answer to the limit without sending a paragraph: the first sentences that fit,
    else the first sentence trimmed at a word. Never longer than `MAX_CHARS`."""
    kept: list[str] = []
    for part in sentences(strip_padding(text))[:MAX_SENTENCES]:
        candidate = " ".join([*kept, part])
        if len(candidate) > MAX_CHARS:
            break
        kept.append(part)
    if kept:
        return " ".join(kept)
    first = (sentences(strip_padding(text)) or [text.strip()])[0]
    if len(first) <= MAX_CHARS:
        return first
    cut = first[: MAX_CHARS - 1].rsplit(" ", 1)[0].rstrip(" ,;:")
    return f"{cut}…"


def within_limit(text: str) -> bool:
    return len(sentences(text)) <= MAX_SENTENCES and len(text) <= MAX_CHARS


def join_source(text: str, source: str | None, sure: str | None) -> str:
    """The answer with its source and confidence in a few words: `(SRD 5.2.1 p. 241, sure)`."""
    pieces = [p for p in (source, sure) if p]
    return f"{text} ({', '.join(pieces)})" if pieces else text
