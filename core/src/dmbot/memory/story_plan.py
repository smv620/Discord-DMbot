"""The story plan (docs/STORY_MEMORY.md, section 6; #1714): what the DM's written adventure
expects, kept as chapters, places and people (each with where in the text it came from and a short
quote as evidence) and "needs" links between them (Chapter 3 needs Captain Varn, alive). A layer
of its own: never table facts, never shown to players, nothing here decides anything.

This module is the pure part (what reading costs, cutting the text into batches, reading the AI's
answer as data and checking it against the text) and the store. The AI call itself is
`dmbot.memory.plan_reader`.

**Money.** Reading is paid for once, when a DM asks, and is capped (Supervisor, 2026-10-17):
`DOCUMENT_CAP_MICRO` for one document and `MONTH_CAP_MICRO` for a campaign's month. What each
call cost is written to `plan_spend` as soon as it returns, and that ledger is kept when the
document is removed, so removing a file and giving it again can't get round the caps.

**Not in backups,** the same rule as the text: it is deleted with its document and with the
campaign, and a restored campaign reads again after its files are given again.
"""

from __future__ import annotations

import json
import math
import re
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from dmbot.ai import DEFAULT_PRICE, PRICE_PER_MILLION
from dmbot.campaigns.models import CampaignError
from dmbot.campaigns.store import DAMAGED
from dmbot.db import Conn, Database
from dmbot.memory.adventure import AdventureError, AdventureStore
from dmbot.memory.claims import reads_like_instruction
from dmbot.word_end import at_word_end

# ---- money --------------------------------------------------------------------------------

# Dollars per million tokens is the same number as millionths of a dollar per token, so a cost in
# millionths ("micro-dollars", an integer) is tokens times the price. The prices are in
# `dmbot.ai.PRICE_PER_MILLION` (the one file that names models); a model with no checked price
# there is priced high on purpose, so the caps bite early, never late.
DOCUMENT_CAP_MICRO = 3_000_000  # $3 for one document
MONTH_CAP_MICRO = 10_000_000  # $10 for one campaign's calendar month (UTC)
CHARS_PER_TOKEN = 3.0  # cautious: real text runs nearer 4, so estimates come out high


def micro_usd(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_read: int = 0,
    cache_write: int = 0,
) -> int:
    """What a call cost, in millionths of a dollar, rounded up. A cache write is charged a
    quarter more than plain input and a read a tenth (the AI service's list rule)."""
    price_in, price_out = PRICE_PER_MILLION.get(model, DEFAULT_PRICE)
    weighted_in = input_tokens * 4 + cache_write * 5 + -(-cache_read * 4 // 10)
    # (a price may have cents, like the cheaper fast model's: round up to a whole millionth)
    return math.ceil(-(-(weighted_in * price_in) // 4) + output_tokens * price_out)


def dearer(model: str, than: str) -> bool:
    """Does `model` cost more per token than `than` (a model nobody priced counts as dear)?"""
    a, b = PRICE_PER_MILLION.get(model, DEFAULT_PRICE), PRICE_PER_MILLION.get(than, DEFAULT_PRICE)
    return a[0] > b[0] or a[1] > b[1]  # input and output each, so a swap of the two still counts


def tokens_in(text: str) -> int:
    """A cautious count of the tokens in a text: ASCII characters at `CHARS_PER_TOKEN` each,
    anything else (Chinese, Japanese, accents, symbols, emoji) at one token a character, which
    is nearer the truth for them and never under it."""
    plain = sum(1 for c in text if c.isascii())
    return int(plain / CHARS_PER_TOKEN) + (len(text) - plain) + 1


def estimate_micro(model: str, text: str, max_output_tokens: int) -> int:
    """The most a call could cost: its text, counted as above, and the longest answer."""
    return micro_usd(model, tokens_in(text), max_output_tokens)


def worst_micro(text: str, max_output_tokens: int) -> int:
    """The most a call could cost whichever model answers: the dearest input price and the
    dearest output price known (an unpriced fallback counts at `DEFAULT_PRICE`), so a reservation
    made with this can't be overrun by a fallback and the caps hold."""
    price_in = max([DEFAULT_PRICE[0], *(p[0] for p in PRICE_PER_MILLION.values())])
    price_out = max([DEFAULT_PRICE[1], *(p[1] for p in PRICE_PER_MILLION.values())])
    return math.ceil(tokens_in(text) * price_in + max_output_tokens * price_out)


def month_of(now: float) -> str:
    return datetime.fromtimestamp(now, UTC).strftime("%Y-%m")


# ---- batches ------------------------------------------------------------------------------

BATCH_CHARS = 36_000  # about 10,000 tokens in a call
BATCH_CHUNKS = 24


@dataclass(frozen=True, slots=True)
class ChunkRow:
    id: str
    position: int
    heading: str
    text: str


def batches_of(rows: Sequence[ChunkRow]) -> list[list[ChunkRow]]:
    """Consecutive chunks in groups of about `BATCH_CHARS` characters (at most `BATCH_CHUNKS`),
    in order; one chunk is never split."""
    out: list[list[ChunkRow]] = []
    current: list[ChunkRow] = []
    size = 0
    for row in rows:
        weight = len(row.text) + len(row.heading)
        if current and (size + weight > BATCH_CHARS or len(current) >= BATCH_CHUNKS):
            out.append(current)
            current, size = [], 0
        current.append(row)
        size += weight
    if current:
        out.append(current)
    return out


# ---- reading the answer -------------------------------------------------------------------

KINDS = ("chapter", "place", "person")
KIND_ANSWER_KEYS = {"chapters": "chapter", "places": "place", "people": "person"}
NAME_MAX = 120
SUMMARY_MAX = 500
QUOTE_MAX = 200
QUOTE_MIN = 12  # a shorter "quote" ("the") is found anywhere and proves nothing
NEED_MAX = 120
NODES_MAX = {"chapter": 300, "place": 1500, "person": 1500}  # per document
LINKS_MAX = 3000  # per document
PER_ANSWER = 200  # entries of each kind taken from one answer; the rest are let go

# What a dropped entry is called in the log: counts only, never a name or a quote.
DROPPED = (
    "no name", "instruction", "quote not in the text", "name not in the text", "unknown name",
    "too many",
)  # fmt: skip


@dataclass(frozen=True, slots=True)
class Node:
    kind: str
    name: str
    summary: str
    quote: str
    chunk_ids: tuple[str, ...]

    @property
    def key(self) -> str:
        return name_key(self.name)


@dataclass(frozen=True, slots=True)
class Need:
    """`start` needs `end`, `need` being what it expects of it ("alive"), or empty."""

    start: tuple[str, str]  # (kind, name key)
    end: tuple[str, str]
    need: str
    quote: str
    chunk_ids: tuple[str, ...]


@dataclass(slots=True)
class Parsed:
    nodes: list[Node] = field(default_factory=list)
    needs: list[Need] = field(default_factory=list)
    dropped: dict[str, int] = field(default_factory=dict)
    malformed: bool = False  # the answer could not be read at all

    def drop(self, why: str) -> None:
        self.dropped[why] = self.dropped.get(why, 0) + 1


def name_key(name: str) -> str:
    """A name as a key: lower case words, so "Captain Varn" and "captain  varn" are one."""
    return " ".join(name.casefold().split())[:NAME_MAX]


def squash(text: str) -> str:
    """Text for comparing: lower case, one space between words, curly quotes made straight, so a
    quote still matches when only the typography was changed."""
    table = {ord(c): "'" for c in "‘’‛′"} | {ord(c): '"' for c in "“”‟″"}
    return " ".join(text.translate(table).casefold().split())


def _clean(value: Any, limit: int, *, whole: bool = False) -> str | None:
    """A short text from the answer: one line, no control characters, within the limit. None
    when it is not text."""
    if not isinstance(value, str):
        return None
    text = "".join(c if c.isprintable() else " " for c in value)
    text = " ".join(text.split())
    if any("\ud800" <= c <= "\udfff" for c in text):
        return None  # a lone surrogate can't be stored as UTF-8
    if whole:  # a name: never shortened (#2607), a cut one is another name
        return text if len(text) <= limit else None
    return at_word_end(text, limit)


def _load(text: str) -> dict[str, Any] | None:
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(text[start : end + 1])
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _evidence(
    quote: str, cited: Any, batch: Sequence[ChunkRow], squashed: Sequence[str]
) -> tuple[str, tuple[str, ...]] | None:
    """The quote, if it really is in the text, and the chunks it is in. The AI says which chunks;
    if it numbers them wrongly the quote is looked for in the whole batch. A quote that is not in
    the text is not evidence, and the entry goes: the plan never makes up what the book says."""
    wanted = squash(quote)
    if len(wanted) < QUOTE_MIN:
        return None
    numbers = (
        [n for n in cited if type(n) is int and 1 <= n <= len(batch)]
        if isinstance(cited, list)
        else []
    )
    for pool in (numbers, range(1, len(batch) + 1)):
        found = [n for n in pool if wanted in squashed[n - 1]]
        if found:
            return quote, tuple(batch[n - 1].id for n in found[:8])
    return None


def _words_in(name: str, text: str) -> bool:
    """Does every word of the name appear in the text? A book may say
    "Varn" where the AI wrote "Captain Varn"; a name of words the book never uses is made up."""
    return all(word in text for word in re.findall(r"\w+", squash(name)))


def parse_answer(text: str, batch: Sequence[ChunkRow]) -> Parsed:
    """The AI's answer as data. Every entry is checked in code: a name, a quote that is in the
    cited text, and nothing that reads like an instruction to DMbot. What fails is dropped and
    counted; nothing here trusts the answer."""
    out = Parsed()
    data = _load(text)
    if data is None:
        out.malformed = True
        return out
    squashed = [squash(f"{row.heading} {row.text}") for row in batch]
    whole = " ".join(squashed)
    for answer_key, kind in KIND_ANSWER_KEYS.items():
        entries = data.get(answer_key, [])
        if not isinstance(entries, list):
            out.malformed = True
            continue
        for entry in entries[:PER_ANSWER]:
            if not isinstance(entry, dict):
                out.drop("no name")
                continue
            name = _clean(entry.get("name"), NAME_MAX, whole=True)
            summary = _clean(entry.get("summary", ""), SUMMARY_MAX)
            quote = _clean(entry.get("quote"), QUOTE_MAX * 4)  # cut to the limit after the check
            if not name or summary is None or quote is None:
                out.drop("no name")
                continue
            if any(reads_like_instruction(t) for t in (name, summary, quote)):
                out.drop("instruction")
                continue
            proof = _evidence(quote, entry.get("chunks"), batch, squashed)
            if proof is None:
                out.drop("quote not in the text")
                continue
            if not _words_in(name, whole):
                out.drop("name not in the text")  # a real quote can't vouch for an invented name
                continue
            out.nodes.append(Node(kind, name, summary, at_word_end(proof[0], QUOTE_MAX), proof[1]))
    needs = data.get("needs", [])
    if not isinstance(needs, list):
        out.malformed = True
        needs = []
    for entry in needs[:PER_ANSWER]:
        if not isinstance(entry, dict):
            out.drop("no name")
            continue
        ends = []
        for side in ("from", "to"):
            end = entry.get(side)
            end_kind = end.get("kind") if isinstance(end, dict) else None
            end_name = (
                _clean(end.get("name"), NAME_MAX, whole=True) if isinstance(end, dict) else None
            )
            if end_kind not in KINDS or not end_name:
                break
            ends.append((str(end_kind), name_key(end_name)))
        need = _clean(entry.get("need", ""), NEED_MAX)
        quote = _clean(entry.get("quote"), QUOTE_MAX * 4)
        if len(ends) != 2 or need is None or quote is None or ends[0] == ends[1]:
            out.drop("no name")
            continue
        if any(reads_like_instruction(t) for t in (need, quote)):
            out.drop("instruction")
            continue
        proof = _evidence(quote, entry.get("chunks"), batch, squashed)
        if proof is None:
            out.drop("quote not in the text")
            continue
        out.needs.append(Need(ends[0], ends[1], need, at_word_end(proof[0], QUOTE_MAX), proof[1]))
    return out


# ---- the store ----------------------------------------------------------------------------

MONTH_FULL = (
    "The AI has done all the reading it will do for this campaign this month. Try again next month."
)
DOCUMENT_FULL = "DMbot stopped reading this file because it reached its limit for one file."
NOT_READABLE = "DMbot has no text for that file. Give DMbot the file again, then press Read it now."
ALREADY_READ = "DMbot has already read that file."
GONE = "That document is already gone."
_HEX64 = re.compile(r"[0-9a-f]{64}")
_MONTH = re.compile(r"[0-9]{4}-(0[1-9]|1[0-2])")
SPEND_ROWS_MAX = 20 * 120  # twenty files over ten years of months
SPEND_MAX_MICRO = 10**10  # $10,000 in one row: far past any cap, a bound against nonsense


class CapError(AdventureError):
    """A money limit would be crossed: `which` is "file" or "month"."""

    def __init__(self, which: str) -> None:
        super().__init__(DOCUMENT_FULL if which == "file" else MONTH_FULL)
        self.which = which


@dataclass(frozen=True, slots=True)
class Reservation:
    id: str
    spent: Spent  # what had been spent before this call


@dataclass(frozen=True, slots=True)
class ReadState:
    document_id: str
    status: str  # reading, done or stopped
    next_chunk: int
    total_chunks: int
    stop_reason: str
    skipped_parts: int

    @property
    def percent(self) -> int:
        return min(100, self.next_chunk * 100 // max(1, self.total_chunks))


@dataclass(frozen=True, slots=True)
class Counts:
    chapters: int = 0
    places: int = 0
    people: int = 0
    links: int = 0


@dataclass(frozen=True, slots=True)
class Spent:
    document: int  # micro-dollars
    month: int


@dataclass(frozen=True, slots=True)
class Stored:
    nodes: int = 0
    links: int = 0
    full: int = 0  # entries let go because the document is at its limit
    unknown: int = 0  # links let go because an end was never found as an entry


@dataclass(frozen=True, slots=True)
class Entry:
    kind: str
    name: str
    summary: str
    quote: str


@dataclass(frozen=True, slots=True)
class Listed:
    """One line of the list on the card: an entry, or a "needs" link between two."""

    kind: str  # chapter, place, person or link
    title: str  # the name; for a link, "Chapter 3 → Captain Varn"
    summary: str  # for a link, what it needs ("alive") or empty
    quote: str


class PlanStore:
    """The story plan of every campaign, each read and written one campaign at a time (the server
    and the campaign are in every statement; row-level security does the rest)."""

    def __init__(self, db: Database, *, clock: Callable[[], float] | None = None) -> None:
        self._db = db
        self._clock = clock or time.time

    @property
    def db(self) -> Database:
        return self._db

    # -- starting and checking ---------------------------------------------------------------

    async def begin(
        self, guild_id: int, campaign_id: str, user_id: int, document_id: str
    ) -> ReadState:
        """Mark a document as being read, or as carrying on where an earlier read stopped. Only
        a DM, with Shared story on and the right confirmed, for a document that has its text, not
        yet read, and with money left in both caps. Refused in plain words."""
        now = int(self._clock())
        async with self._db.guild(guild_id) as conn:
            await AdventureStore.require_dm(conn, guild_id, campaign_id, user_id)
            await AdventureStore.require_on(conn, guild_id, campaign_id)
            doc = await self._document(conn, guild_id, campaign_id, document_id)
            if doc is None:
                raise AdventureError(GONE)
            if int(doc["chunks"]) == 0:
                raise AdventureError(NOT_READABLE)
            cur = await conn.execute(
                "SELECT status, next_chunk, total_chunks, stop_reason, skipped_parts"
                " FROM adventure_reads WHERE guild_id = %s AND campaign_id = %s"
                " AND document_id = %s FOR UPDATE",
                (guild_id, campaign_id, document_id),
            )
            row = await cur.fetchone()
            if row is not None and row["status"] == "done":
                raise AdventureError(ALREADY_READ)
            spent = await self._spent(conn, guild_id, campaign_id, doc["fingerprint"], now)
            if spent.document >= DOCUMENT_CAP_MICRO:
                raise AdventureError(DOCUMENT_FULL)
            if spent.month >= MONTH_CAP_MICRO:
                raise AdventureError(MONTH_FULL)
            total = int(doc["chunks"])
            if row is None:
                await conn.execute(
                    "INSERT INTO adventure_reads (guild_id, campaign_id, document_id, status,"
                    " next_chunk, total_chunks, started_by, started_at, updated_at)"
                    " VALUES (%s, %s, %s, 'reading', 0, %s, %s, %s, %s)",
                    (guild_id, campaign_id, document_id, total, user_id, now, now),
                )
                return ReadState(document_id, "reading", 0, total, "", 0)
            await conn.execute(
                "UPDATE adventure_reads SET status = 'reading', stop_reason = '',"
                " updated_at = %s WHERE guild_id = %s AND campaign_id = %s AND document_id = %s",
                (now, guild_id, campaign_id, document_id),
            )
            return ReadState(
                document_id,
                "reading",
                int(row["next_chunk"]),
                total,
                "",
                int(row["skipped_parts"]),
            )

    async def reserve(
        self, guild_id: int, campaign_id: str, document_id: str, worst: int
    ) -> Reservation:
        """Before every call: the switch is still on with a confirmation made here, the document is
        still here with its text, and the most the call could cost fits under both caps. If so,
        that much is written to the ledger as a reservation, to be settled to the real cost when
        the call returns. It is kept (not forgotten) when the call fails or the bot stops in the
        middle of it, because the AI service may still bill it. The campaign row is locked, so two
        reads in one campaign can't both see the same money as free. Refused in plain words."""
        now = self._clock()
        async with self._db.guild(guild_id) as conn:
            await conn.execute(
                "SELECT 1 FROM campaigns WHERE guild_id = %s AND id = %s FOR UPDATE",
                (guild_id, campaign_id),
            )
            await AdventureStore.require_on(conn, guild_id, campaign_id)
            doc = await self._document(conn, guild_id, campaign_id, document_id)
            if doc is None or int(doc["chunks"]) == 0:
                raise AdventureError(GONE)
            spent = await self._spent(conn, guild_id, campaign_id, doc["fingerprint"], now)
            if spent.document + worst > DOCUMENT_CAP_MICRO:
                raise CapError("file")
            if spent.month + worst > MONTH_CAP_MICRO:
                raise CapError("month")
            reservation = uuid.uuid4().hex
            await self._ledger(
                conn, guild_id, campaign_id, reservation, doc["fingerprint"], worst, 0, 0, now
            )
            return Reservation(reservation, spent)

    async def settle(
        self,
        guild_id: int,
        campaign_id: str,
        reservation: str,
        micro: int,
        input_tokens: int = 0,
        output_tokens: int = 0,
    ) -> None:
        """A reservation becomes what the call really cost (or the part of it that is certain)."""
        async with self._db.guild(guild_id) as conn:
            await conn.execute(
                "UPDATE plan_spend SET micro_usd = %s, input_tokens = %s, output_tokens = %s"
                " WHERE guild_id = %s AND campaign_id = %s AND id = %s",
                (
                    max(0, micro),
                    max(0, input_tokens),
                    max(0, output_tokens),
                    guild_id,
                    campaign_id,
                    reservation,
                ),
            )

    @staticmethod
    async def _ledger(
        conn: Conn,
        guild_id: int,
        campaign_id: str,
        row_id: str,
        fingerprint: str,
        micro: int,
        input_tokens: int,
        output_tokens: int,
        now: float,
    ) -> None:
        await conn.execute(
            "INSERT INTO plan_spend (guild_id, campaign_id, id, fingerprint, month, micro_usd,"
            " input_tokens, output_tokens, at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                guild_id,
                campaign_id,
                row_id,
                fingerprint,
                month_of(now),
                max(0, micro),
                max(0, input_tokens),
                max(0, output_tokens),
                int(now),
            ),
        )

    async def _document(self, conn: Conn, guild_id: int, campaign_id: str, document_id: str) -> Any:
        cur = await conn.execute(
            "SELECT chunks, fingerprint FROM adventure_documents WHERE guild_id = %s"
            " AND campaign_id = %s AND id = %s",
            (guild_id, campaign_id, document_id),
        )
        return await cur.fetchone()

    async def _spent(
        self, conn: Conn, guild_id: int, campaign_id: str, fingerprint: str, now: float
    ) -> Spent:
        cur = await conn.execute(
            "SELECT COALESCE(sum(micro_usd) FILTER (WHERE fingerprint = %s), 0) AS doc,"
            " COALESCE(sum(micro_usd) FILTER (WHERE month = %s), 0) AS month"
            " FROM plan_spend WHERE guild_id = %s AND campaign_id = %s",
            (fingerprint, month_of(now), guild_id, campaign_id),
        )
        row = await cur.fetchone()
        assert row is not None
        return Spent(int(row["doc"]), int(row["month"]))

    # -- reading and writing -----------------------------------------------------------------

    async def chunks_from(
        self, guild_id: int, campaign_id: str, document_id: str, start: int, limit: int
    ) -> list[ChunkRow]:
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT id, position, heading, text FROM adventure_chunks WHERE guild_id = %s"
                " AND campaign_id = %s AND document_id = %s AND position >= %s"
                " ORDER BY position LIMIT %s",
                (guild_id, campaign_id, document_id, start, limit),
            )
            return [
                ChunkRow(r["id"], int(r["position"]), r["heading"], r["text"])
                for r in await cur.fetchall()
            ]

    async def names(
        self, guild_id: int, campaign_id: str, document_id: str, limit: int
    ) -> list[str]:
        """The names found so far, newest first, so the next batch spells them the same way."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT name FROM plan_nodes WHERE guild_id = %s AND campaign_id = %s"
                " AND document_id = %s ORDER BY created_at DESC, position DESC, id LIMIT %s",
                (guild_id, campaign_id, document_id, limit),
            )
            return [r["name"] for r in await cur.fetchall()]

    async def record_spend(
        self,
        guild_id: int,
        campaign_id: str,
        document_id: str,
        micro: int,
        input_tokens: int,
        output_tokens: int,
    ) -> None:
        """Write what something cost straight to the ledger (the reader reserves and settles
        instead; this is for money spent some other way, and for tests)."""
        async with self._db.guild(guild_id) as conn:
            doc = await self._document(conn, guild_id, campaign_id, document_id)
            if doc is None:
                raise AdventureError(GONE)
            await self._ledger(
                conn,
                guild_id,
                campaign_id,
                uuid.uuid4().hex,
                doc["fingerprint"],
                micro,
                input_tokens,
                output_tokens,
                self._clock(),
            )

    async def store_batch(
        self,
        guild_id: int,
        campaign_id: str,
        document_id: str,
        parsed: Parsed,
        *,
        next_chunk: int,
        skipped: bool = False,
    ) -> Stored:
        """Keep what a batch found and move the read on, in one transaction. The switch and its
        confirmation are checked again here: this is after the AI answered (CLAUDE.md: after every
        async step). A name seen before keeps its first entry; a link to a name nobody found is
        let go (the plan never makes up an entry to hang a link on)."""
        now = int(self._clock())
        async with self._db.guild(guild_id) as conn:
            await AdventureStore.require_on(conn, guild_id, campaign_id)
            doc = await self._document(conn, guild_id, campaign_id, document_id)
            if doc is None:
                raise AdventureError(GONE)
            cur = await conn.execute(
                "SELECT kind, count(*) AS n FROM plan_nodes WHERE guild_id = %s"
                " AND campaign_id = %s AND document_id = %s GROUP BY kind",
                (guild_id, campaign_id, document_id),
            )
            held = {r["kind"]: int(r["n"]) for r in await cur.fetchall()}
            cur = await conn.execute(
                "SELECT count(*) AS n FROM plan_links WHERE guild_id = %s AND campaign_id = %s"
                " AND document_id = %s",
                (guild_id, campaign_id, document_id),
            )
            row = await cur.fetchone()
            links_held = int(row["n"]) if row else 0
            added = full = unknown = 0
            for node in parsed.nodes:
                if held.get(node.kind, 0) >= NODES_MAX[node.kind]:
                    full += 1
                    continue
                cur = await conn.execute(
                    "INSERT INTO plan_nodes (guild_id, campaign_id, id, document_id, kind, name,"
                    " name_key, summary, quote, chunk_ids, position, created_at)"
                    " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s,"
                    " %s, %s) ON CONFLICT DO NOTHING",
                    (
                        guild_id,
                        campaign_id,
                        uuid.uuid4().hex,
                        document_id,
                        node.kind,
                        node.name,
                        node.key,
                        node.summary,
                        node.quote,
                        list(node.chunk_ids),
                        next_chunk,
                        now,
                    ),
                )
                if cur.rowcount:
                    added += 1
                    held[node.kind] = held.get(node.kind, 0) + 1
            cur = await conn.execute(
                "SELECT id, kind, name_key FROM plan_nodes WHERE guild_id = %s"
                " AND campaign_id = %s AND document_id = %s",
                (guild_id, campaign_id, document_id),
            )
            ids = {(r["kind"], r["name_key"]): r["id"] for r in await cur.fetchall()}
            linked = 0
            for need in parsed.needs:
                start, end = ids.get(need.start), ids.get(need.end)
                if start is None or end is None:
                    unknown += 1
                    continue
                if links_held >= LINKS_MAX:
                    full += 1
                    continue
                cur = await conn.execute(
                    "INSERT INTO plan_links (guild_id, campaign_id, id, document_id, from_id,"
                    " to_id, need, quote, chunk_ids, created_at)"
                    " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                    (
                        guild_id,
                        campaign_id,
                        uuid.uuid4().hex,
                        document_id,
                        start,
                        end,
                        need.need,
                        need.quote,
                        list(need.chunk_ids),
                        now,
                    ),
                )
                if cur.rowcount:
                    linked += 1
                    links_held += 1
            # Counted once per batch even if this write is tried again after it already went
            # through: only a batch that moves the read on adds to the skipped parts.
            await conn.execute(
                "UPDATE adventure_reads SET skipped_parts = skipped_parts"
                " + CASE WHEN next_chunk < %s THEN %s ELSE 0 END, next_chunk = GREATEST(next_chunk,"
                " %s), updated_at = %s WHERE guild_id = %s AND campaign_id = %s"
                " AND document_id = %s",
                (
                    next_chunk,
                    1 if skipped else 0,
                    next_chunk,
                    now,
                    guild_id,
                    campaign_id,
                    document_id,
                ),
            )
            return Stored(added, linked, full, unknown)

    async def finish(
        self, guild_id: int, campaign_id: str, document_id: str, status: str, reason: str = ""
    ) -> None:
        """The read is done, or stopped (and can be carried on)."""
        async with self._db.guild(guild_id) as conn:
            await conn.execute(
                "UPDATE adventure_reads SET status = %s, stop_reason = %s, updated_at = %s"
                " WHERE guild_id = %s AND campaign_id = %s AND document_id = %s",
                (status, reason[:40], int(self._clock()), guild_id, campaign_id, document_id),
            )

    # -- what the card shows -----------------------------------------------------------------

    async def states(self, guild_id: int, campaign_id: str) -> dict[str, ReadState]:
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT document_id, status, next_chunk, total_chunks, stop_reason,"
                " skipped_parts FROM adventure_reads WHERE guild_id = %s AND campaign_id = %s",
                (guild_id, campaign_id),
            )
            return {
                r["document_id"]: ReadState(
                    r["document_id"],
                    r["status"],
                    int(r["next_chunk"]),
                    int(r["total_chunks"]),
                    r["stop_reason"],
                    int(r["skipped_parts"]),
                )
                for r in await cur.fetchall()
            }

    async def counts(self, guild_id: int, campaign_id: str) -> dict[str, Counts]:
        """What each document's read found, by document."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT document_id, kind, count(*) AS n FROM plan_nodes WHERE guild_id = %s"
                " AND campaign_id = %s GROUP BY document_id, kind",
                (guild_id, campaign_id),
            )
            nodes: dict[str, dict[str, int]] = {}
            for r in await cur.fetchall():
                nodes.setdefault(r["document_id"], {})[r["kind"]] = int(r["n"])
            cur = await conn.execute(
                "SELECT document_id, count(*) AS n FROM plan_links WHERE guild_id = %s"
                " AND campaign_id = %s GROUP BY document_id",
                (guild_id, campaign_id),
            )
            links = {r["document_id"]: int(r["n"]) for r in await cur.fetchall()}
        found = nodes.keys() | links.keys()
        return {
            doc: Counts(
                nodes.get(doc, {}).get("chapter", 0),
                nodes.get(doc, {}).get("place", 0),
                nodes.get(doc, {}).get("person", 0),
                links.get(doc, 0),
            )
            for doc in found
        }

    async def entries(
        self, guild_id: int, campaign_id: str, document_id: str, offset: int, limit: int
    ) -> tuple[list[Entry], int]:
        """A page of what was read, chapters first, then places, then people, each in the order
        found; and how many there are in all."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT count(*) AS n FROM plan_nodes WHERE guild_id = %s AND campaign_id = %s"
                " AND document_id = %s",
                (guild_id, campaign_id, document_id),
            )
            row = await cur.fetchone()
            total = int(row["n"]) if row else 0
            cur = await conn.execute(
                "SELECT kind, name, summary, quote FROM plan_nodes WHERE guild_id = %s"
                " AND campaign_id = %s AND document_id = %s ORDER BY"
                " CASE kind WHEN 'chapter' THEN 0 WHEN 'place' THEN 1 ELSE 2 END,"
                " document_id, position, created_at, name_key, id LIMIT %s OFFSET %s",
                (guild_id, campaign_id, document_id, limit, offset),
            )
            return [
                Entry(r["kind"], r["name"], r["summary"], r["quote"]) for r in await cur.fetchall()
            ], total

    async def listing(
        self, guild_id: int, campaign_id: str, offset: int, limit: int
    ) -> tuple[list[Listed], int]:
        """A page of everything read in the campaign, to scroll: chapters, places and people, then
        the links, and how many lines there are in all."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT (SELECT count(*) FROM plan_nodes WHERE guild_id = %s AND campaign_id = %s)"
                " AS nodes, (SELECT count(*) FROM plan_links WHERE guild_id = %s"
                " AND campaign_id = %s) AS links",
                (guild_id, campaign_id, guild_id, campaign_id),
            )
            row = await cur.fetchone()
            assert row is not None
            nodes, links = int(row["nodes"]), int(row["links"])
            out: list[Listed] = []
            if offset < nodes:
                cur = await conn.execute(
                    "SELECT kind, name, summary, quote FROM plan_nodes WHERE guild_id = %s"
                    " AND campaign_id = %s ORDER BY"
                    " CASE kind WHEN 'chapter' THEN 0 WHEN 'place' THEN 1 ELSE 2 END,"
                    " document_id, position, created_at, name_key, id LIMIT %s OFFSET %s",
                    (guild_id, campaign_id, limit, offset),
                )
                out = [
                    Listed(r["kind"], r["name"], r["summary"], r["quote"])
                    for r in await cur.fetchall()
                ]
            if len(out) < limit and links:
                cur = await conn.execute(
                    "SELECT a.name AS start, b.name AS stop, l.need, l.quote FROM plan_links l"
                    " JOIN plan_nodes a ON (a.guild_id, a.campaign_id, a.id)"
                    " = (l.guild_id, l.campaign_id, l.from_id)"
                    " JOIN plan_nodes b ON (b.guild_id, b.campaign_id, b.id)"
                    " = (l.guild_id, l.campaign_id, l.to_id)"
                    " WHERE l.guild_id = %s AND l.campaign_id = %s"
                    " ORDER BY l.created_at, l.id LIMIT %s OFFSET %s",
                    (guild_id, campaign_id, limit - len(out), max(0, offset - nodes)),
                )
                out += [
                    Listed("link", f"{r['start']} → {r['stop']}", r["need"], r["quote"])
                    for r in await cur.fetchall()
                ]
            return out, nodes + links

    async def spent(self, guild_id: int, campaign_id: str, document_id: str) -> Spent:
        async with self._db.guild(guild_id) as conn:
            doc = await self._document(conn, guild_id, campaign_id, document_id)
            if doc is None:
                return Spent(
                    0,
                    (await self._spent(conn, guild_id, campaign_id, "0" * 64, self._clock())).month,
                )
            return await self._spent(conn, guild_id, campaign_id, doc["fingerprint"], self._clock())


# ---- the ledger across a restore -------------------------------------------------------------


class SpendSection:
    """The money ledger in campaign backups (an `ExportSection`), amounts only and by file
    fingerprint (#1723): a restore must not give a file a fresh $3 and a campaign a fresh $10. Not
    the plan, not the text, no names: a fingerprint, a month and a sum. Loading adds only what the
    campaign's own ledger lacks for that file and month (so restoring an old backup over a campaign
    never lowers what it has spent), and `clear` leaves the ledger alone for the same reason."""

    name = "plan_spend"

    async def dump(self, conn: Conn, guild_id: int, campaign_id: str) -> list[Any]:
        cur = await conn.execute(
            "SELECT fingerprint, month, sum(micro_usd) AS micro FROM plan_spend"
            " WHERE guild_id = %s AND campaign_id = %s GROUP BY fingerprint, month"
            " ORDER BY month, fingerprint",
            (guild_id, campaign_id),
        )
        return [
            {"fingerprint": r["fingerprint"], "month": r["month"], "micro_usd": int(r["micro"])}
            for r in await cur.fetchall()
        ]

    async def load(self, conn: Conn, guild_id: int, campaign_id: str, rows: list[Any]) -> None:
        if not isinstance(rows, list) or len(rows) > SPEND_ROWS_MAX:
            raise CampaignError(DAMAGED)
        seen: set[tuple[str, str]] = set()
        for row in rows:
            if not (
                isinstance(row, dict)
                and set(row) == {"fingerprint", "month", "micro_usd"}
                and isinstance(row["fingerprint"], str)
                and _HEX64.fullmatch(row["fingerprint"])
                and isinstance(row["month"], str)
                and _MONTH.fullmatch(row["month"])
                and type(row["micro_usd"]) is int
                and 0 <= row["micro_usd"] <= SPEND_MAX_MICRO
                and (row["fingerprint"], row["month"]) not in seen
            ):
                raise CampaignError(DAMAGED)
            seen.add((row["fingerprint"], row["month"]))
        now = int(time.time())
        for row in rows:
            cur = await conn.execute(
                "SELECT COALESCE(sum(micro_usd), 0) AS held FROM plan_spend WHERE guild_id = %s"
                " AND campaign_id = %s AND fingerprint = %s AND month = %s",
                (guild_id, campaign_id, row["fingerprint"], row["month"]),
            )
            held = await cur.fetchone()
            missing = row["micro_usd"] - int(held["held"] if held else 0)
            if missing > 0:
                await PlanStore._ledger(
                    conn, guild_id, campaign_id, uuid.uuid4().hex, row["fingerprint"],
                    missing, 0, 0, now,
                )  # fmt: skip

    async def clear(self, conn: Conn, guild_id: int, campaign_id: str) -> None:
        return None  # kept: a replace must not give the money back


__all__ = [
    "CapError",
    "Counts",
    "Entry",
    "Listed",
    "Need",
    "Node",
    "Parsed",
    "PlanStore",
    "ReadState",
    "Reservation",
    "SpendSection",
    "Spent",
    "Stored",
    "batches_of",
    "dearer",
    "estimate_micro",
    "micro_usd",
    "parse_answer",
    "worst_micro",
]
