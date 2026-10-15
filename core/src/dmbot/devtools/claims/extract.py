"""The draft extractor: one AI call per batch of transcript lines, returning claims with
how each was said (docs/STORY_MEMORY.md, section 1). The prompt lives in
`dmbot.memory.extractor`, which the bot runs after a session (#1363); this module measures the
same text on the golden set (#1282).

The transcript is untrusted: players can say anything, including instructions. It goes
to the AI as quoted data, and the answer is read as data too: every field is checked,
and anything else is dropped and counted.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from dmbot.ai import Reply
from dmbot.devtools.claims.scenes import HOWS, Line
from dmbot.memory.extractor import MAX_TOKENS, SYSTEM, transcript_of

MAX_CLAIMS = 60
MAX_FIELD = 200


class Client(Protocol):
    async def complete(self, system: str, text: str, *, max_tokens: int = ...) -> Reply: ...


@dataclass(frozen=True, slots=True)
class Claim:
    subject: str
    relationship: str
    object: str
    details: str
    lines: tuple[int, ...]
    how: str


@dataclass(frozen=True, slots=True)
class Extraction:
    claims: tuple[Claim, ...]
    dropped: int  # claims in the answer that weren't well-formed
    input_tokens: int
    output_tokens: int
    cut: bool  # the answer hit MAX_TOKENS


def transcript(lines: Sequence[Line]) -> str:
    """The lines as quoted data. A line can't close the quote early."""
    return transcript_of([(str(line.number), line.speaker, line.text) for line in lines])


def _field(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())
    return text[:MAX_FIELD] if text else None


def parse_answer(text: str, numbers: set[int]) -> tuple[list[Claim], int]:
    """The claims in an answer, and how many were dropped. Only line numbers that were
    sent count; a claim citing none of them is dropped."""
    start, end = text.find("{"), text.rfind("}")
    try:
        data = json.loads(text[start : end + 1]) if 0 <= start < end else None
    except ValueError:
        data = None
    items = data.get("claims") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return [], 1
    claims: list[Claim] = []
    dropped = 0
    for item in items[:MAX_CLAIMS]:
        if not isinstance(item, dict):
            dropped += 1
            continue
        subject, relationship = _field(item.get("subject")), _field(item.get("relationship"))
        obj = _field(item.get("object")) or ""
        details = _field(item.get("details")) or ""
        how = item.get("how")
        cited = item.get("lines")
        lines = tuple(
            n for n in (cited if isinstance(cited, list) else []) if type(n) is int and n in numbers
        )
        if subject is None or relationship is None or how not in HOWS or not lines:
            dropped += 1
            continue
        assert isinstance(how, str)
        claims.append(Claim(subject, relationship, obj, details, lines, how))
    return claims, dropped + max(0, len(items) - MAX_CLAIMS)


async def extract(client: Client, lines: Sequence[Line]) -> Extraction:
    reply = await client.complete(SYSTEM, transcript(lines), max_tokens=MAX_TOKENS)
    claims, dropped = parse_answer(reply.text, {line.number for line in lines})
    return Extraction(tuple(claims), dropped, reply.input_tokens, reply.output_tokens, reply.cut)
