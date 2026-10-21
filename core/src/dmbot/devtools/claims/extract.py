"""The draft extractor: one AI call per batch of transcript lines, returning claims with
how each was said (docs/STORY_MEMORY.md, section 1). The prompt lives in
`dmbot.memory.extractor`, which the bot runs after a session (#1363); this module measures the
same text on the golden set (#1282).

The transcript is untrusted: players can say anything, including instructions. It goes
to the AI as quoted data, and the answer is read as data too: every field is checked,
and anything else is dropped and counted.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from dmbot.ai import Reply
from dmbot.devtools.claims.scenes import Line
from dmbot.memory.claims import parse_answer as read_answer
from dmbot.memory.extractor import MAX_TOKENS, SYSTEM, transcript_of


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


def parse_answer(text: str, numbers: set[int]) -> tuple[list[Claim], int]:
    """The claims in an answer, and how many were dropped. The bot's own reader
    (`dmbot.memory.claims.parse_answer`) does the reading, so a measurement judges what the bot
    would keep: the same checks, the same lenient reading of a cut-off answer (#2152). Only line
    numbers that were sent count; a claim citing none of them is dropped."""
    found, dropped = read_answer(text, {str(n) for n in numbers})
    return [
        Claim(c.subject, c.relationship, c.object, c.details, tuple(int(n) for n in c.lines), c.how)
        for c in found
    ], dropped


async def extract(client: Client, lines: Sequence[Line]) -> Extraction:
    reply = await client.complete(SYSTEM, transcript(lines), max_tokens=MAX_TOKENS)
    claims, dropped = parse_answer(reply.text, {line.number for line in lines})
    return Extraction(tuple(claims), dropped, reply.input_tokens, reply.output_tokens, reply.cut)
