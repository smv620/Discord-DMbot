"""The sample run's careful-model decision step (#3900, Supervisor's ruling with the owner's yes).

After the lore read and after the session import, `sample_run` first adds what DMbot is sure of
(`memory.auto_accept`, as an upload does, #3878): the "sure" column. What is still waiting is put to
the careful model here, one question per item, and it answers yes, no or not sure: the "AI-decided"
column. A yes or no goes through the store's own DM calls, so every rule is checked again and each
change can be undone like the DM's. "Not sure", and everything past `--max-decisions`, is left
waiting: the "left" column. The three are never mixed.

Each decision is scored against the sample's answer key (`judge.py`) and kept in a decision record:
a file on the server, in the run's own folder, never in the repository (it holds the sample's names
and the model's reasons). What the run prints, and what goes on GitHub, is counts and kinds only.

Kinds, in the order asked (names first: the rest hang on them):
- names: a waiting name (never a player character, which needs a person to say who plays it);
- which one: a "Which one?" question, answered with one of its candidates or "neither";
- things said: a claim from the session (a thing someone said happened);
- connections: a family tie DMbot worked out from two stated ones;
- look-alikes: an unnamed person who may be a named one (a yes joins them; a wrong one is counted
  apart, as "merged two the key keeps apart").

Only `sample_run` calls this, only on the campaign named "test of the frostmaiden (sample)" and
only with `--yes`: the caller checks the name first, and so does `run` here, with no override.
"""

from __future__ import annotations

import functools
import json
import logging
import os
import re
import time
from collections import Counter
from collections.abc import Awaitable, Callable, Collection, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol

from dmbot.ai import DEFAULT_MODELS, DEFAULT_PRICE, OUTPUT_FLOOR, PRICE_PER_MILLION, Reply
from dmbot.devtools.sample_run import judge
from dmbot.devtools.sample_run.judge import NO, NOT_SURE, YES, Agreement, Key
from dmbot.memory.models import (
    CONFIRMED,
    DM,
    PLAYER_CHARACTER,
    REJECTED,
    WORKED_OUT_SOURCE,
    Entity,
    MemoryRuleError,
)

log = logging.getLogger(__name__)

DEFAULT_MAX_DECISIONS = 60
NAMES, WHICH, SAID, CONNECTIONS, LOOKALIKES = (
    "names", "which one", "things said", "connections", "look-alikes",
)  # fmt: skip
KINDS = (NAMES, WHICH, SAID, CONNECTIONS, LOOKALIKES)
NAME_KINDS = ("npc", "place", "faction", "item", "event")
EXCERPTS = 4  # lines of the sample's text shown with each question
EXCERPT_CHARS = 400
REASON_MAX = 300  # the decision record keeps this much of the model's reason

SYSTEM = (
    "You help a Dungeon Master check what DMbot read from their campaign's own text. You get one "
    "item DMbot is unsure of and the lines of the text it came from. Decide only from those lines: "
    "never guess, never add story. Answer with JSON only: "
    '{"answer": "yes" | "no" | "not sure", "reason": "<one short sentence>"}. '
    'Say "not sure" whenever the lines do not settle it. '
    'For a name, "yes" means it is a real person, place, group, item or event in the story (not a '
    "rules word, a kind of creature, a title, a form of address or a description), and add "
    '"kind": one of npc, place, faction, item, event. '
    'For a "Which one?" question, "yes" means one candidate is clearly meant: add "pick": its '
    'number. "no" means none of them is meant.'
)


class Client(Protocol):
    async def complete(self, system: str, text: str, *, max_tokens: int) -> Reply: ...


@dataclass(frozen=True, slots=True)
class Decision:
    """One line of the decision record (kept on the server only, so it may say which item)."""

    stage: str  # lore or session
    kind: str
    item: str  # the entry, question, claim or connection's id, so the owner can look it up
    decision: str  # yes, no or not sure
    model: str
    tier: str
    reason: str
    at: int
    verdict: str | None  # against the key (judge.VERDICTS), None for not sure
    done: bool = True  # False: a rule refused the store call, so it stays waiting


@dataclass
class Budget:
    """The calls left for the whole run (`--max-decisions`), shared by the lore and session
    stages."""

    left: int

    def take(self) -> bool:
        if self.left <= 0:
            return False
        self.left -= 1
        return True


@dataclass
class Run:
    """What one decision stage did: counts only, and the record path."""

    decided: dict[str, Counter[str]] = field(default_factory=dict)  # kind -> yes/no/not sure
    over_cap: Counter[str] = field(default_factory=Counter)  # left because the cap was reached
    refused: Counter[str] = field(default_factory=Counter)  # a rule said no: left waiting
    failed: int = 0  # the call failed (service, network): left waiting as "not sure"
    cut: int = 0  # the answer stopped early: left waiting as "not sure", never read in part
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost: float = 0.0
    batches: list[int] = field(default_factory=list)
    agreement: Agreement = field(default_factory=Agreement)

    def add(self, kind: str, decision: str) -> None:
        self.decided.setdefault(kind, Counter())[decision] += 1

    def lines(self) -> list[str]:
        """The counts per kind (decided yes, no, not sure, left over the cap, refused), then the
        agreement with the key, then calls and cost. Never a name or a word of a text."""
        out = []
        for kind in KINDS:
            c = self.decided.get(kind, Counter())
            out.append(
                f"AI-decided {kind}: yes {c[YES]}, no {c[NO]}, not sure {c[NOT_SURE]};"
                f" left over the cap {self.over_cap[kind]}; refused by a rule {self.refused[kind]}"
            )
        out += self.agreement.lines(KINDS, upper=(SAID, CONNECTIONS))
        out.append(
            f"decisions: {self.calls} careful-model calls ({self.failed} failed, {self.cut} cut"
            f" short), {self.input_tokens} tokens in, {self.output_tokens} out,"
            f" about ${self.cost:.2f}"
        )
        return out


def parse(text: str) -> dict[str, Any]:
    """The model's answer, or "not sure" when it can't be read: never a guess."""
    start, end = text.find("{"), text.rfind("}")
    try:
        got = json.loads(text[start : end + 1]) if start >= 0 < end else {}
    except ValueError:
        got = {}
    if not isinstance(got, dict):
        got = {}
    answer = str(got.get("answer", "")).strip().casefold()
    got["answer"] = answer if answer in (YES, NO, NOT_SURE) else NOT_SURE
    got["reason"] = str(got.get("reason", ""))[:REASON_MAX]
    return got


_COMMON = frozenset({"the", "a", "an", "of", "and", "with", "at", "in", "on", "to", "from"})


def excerpts(docs: Sequence[str], names: Collection[str]) -> list[str]:
    """Up to `EXCERPTS` lines of the sample's text that hold a telling word of any of these names
    (whole words of three letters or more, an unnamed person's description read without its
    "unnamed - " start), each cut to `EXCERPT_CHARS`."""
    words = {
        w.casefold()
        for n in names
        for w in re.findall(r"[^\W\d_][\w'’-]*", n.removeprefix(judge.UNNAMED_PREFIX))
        if len(w) >= 3 and w.casefold() not in _COMMON
    }
    if not words:
        return []
    pattern = re.compile(r"\b(?:" + "|".join(re.escape(w) for w in sorted(words)) + r")\b", re.I)
    found: list[str] = []
    for doc in docs:
        for line in doc.splitlines():
            if line.strip() and pattern.search(line):
                found.append(line.strip()[:EXCERPT_CHARS])
                if len(found) >= EXCERPTS:
                    return found
    return found


# A question is a short prompt and a few lines, and the answer a sentence: what `estimate` assumes.
_ESTIMATE_IN, _ESTIMATE_OUT = 1_500, 150


def estimate(calls: int, model: str = "") -> float:
    """About what this many decisions cost on the careful model (the default one unless named),
    shown before `--yes`."""
    per_in, per_out = PRICE_PER_MILLION.get(model or DEFAULT_MODELS.careful, DEFAULT_PRICE)
    return calls * (_ESTIMATE_IN * per_in + _ESTIMATE_OUT * per_out) / 1_000_000


def _price(model: str, reply: Reply) -> float:
    per_in, per_out = PRICE_PER_MILLION.get(model, DEFAULT_PRICE)
    return (reply.input_tokens * per_in + reply.output_tokens * per_out) / 1_000_000


Act = Callable[[dict[str, Any]], Awaitable[int | None]]  # the store call: the batch, or None


def open_record(path: Path) -> None:
    """The decision record, made readable by its owner only, as the backups are."""
    os.close(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600))


@dataclass
class Asker:
    """Asks the model about one item, acts on the answer and records it. One for the whole run, so
    the cap, the counts and the cost add up across the lore and session stages."""

    ai: Client
    model: str
    budget: Budget
    run: Run
    record: Path
    clock: Callable[[], float] = time.time
    stage: str = ""

    async def ask(
        self,
        kind: str,
        item: str,
        question: str,
        lines: Sequence[str],
        *,
        on_yes: Act,
        on_no: Act,
        key_says: Callable[[dict[str, Any]], str | None],
        merge: bool = False,
    ) -> None:
        if not self.budget.take():
            self.run.over_cap[kind] += 1
            return
        text = question + "\n\nThe lines:\n" + "\n".join(f"- {line}" for line in lines)
        self.run.calls += 1
        try:
            reply = await self.ai.complete(SYSTEM, text, max_tokens=OUTPUT_FLOOR)
        except Exception as exc:  # the service or the network: left waiting, the run goes on
            log.warning("sample_run decide: a call failed (%s)", type(exc).__name__)
            got: dict[str, Any] = {"answer": NOT_SURE, "reason": "the call failed"}
            answered_by = ""
            self.run.failed += 1
        else:
            self.run.input_tokens += reply.input_tokens
            self.run.output_tokens += reply.output_tokens
            self.run.cost += _price(reply.model or self.model, reply)
            got = parse(reply.text) if not reply.cut else {"answer": NOT_SURE, "reason": "cut"}
            self.run.cut += int(reply.cut)
            answered_by = reply.model
        decision = got["answer"]
        done = True
        if decision in (YES, NO):
            try:
                batch = await (on_yes if decision == YES else on_no)(got)
            except (MemoryRuleError, LookupError) as exc:
                done = False
                self.run.refused[kind] += 1
                log.info("sample_run decide: a %s was refused (%s)", kind, type(exc).__name__)
            else:
                if batch is not None:
                    self.run.batches.append(batch)
        key = key_says(got) if decision in (YES, NO) else None
        self.run.add(kind, decision)
        if done:
            self.run.agreement.add(kind, decision, key, merge=merge)
        line = Decision(
            self.stage, kind, item, decision, answered_by or self.model, "careful",
            str(got.get("reason", "")), int(self.clock()),
            judge.verdict(decision, key) if done else None, done,
        )  # fmt: skip
        open_record(self.record)
        with self.record.open("a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(line)) + "\n")


async def run(
    db: Any,
    memory: Any,
    links: Any,
    claims: Any,
    asker: Asker,
    key: Key,
    guild: int,
    campaign: str,
    *,
    docs: Sequence[str],
    opted_in: Collection[int],
    dm: int,
) -> Run:
    """One decision stage on the sample campaign: every kind, in order. Refuses any other
    campaign before anything is read or asked."""
    from dmbot.devtools.accept_all import check_campaign

    await check_campaign(db, guild, campaign)  # (raises WrongCampaign: no override)
    every = {
        e.id: e for e in await memory.entities(guild, campaign, statuses=("proposed", CONFIRMED))
    }

    def name(entity_id: str) -> str:
        e = every.get(entity_id)
        return e.name if e is not None else ""

    # names
    for e in await memory.entities(guild, campaign, statuses=("proposed",)):
        # (who plays a character is a person's to say; a described person belongs to the unnamed
        # and look-alike questions, never confirmed or refused as a name, #3431 and #3878; and a
        # table member is never a story entry: all left, as in the sure step)
        if e.role == PLAYER_CHARACTER or e.type == "person" or _unnamed(e):
            continue
        await asker.ask(
            NAMES,
            e.id,
            f'A name DMbot found: "{e.name}". DMbot\'s guess of its kind: {e.type}'
            f"{f' ({e.role})' if e.role else ''}. Is it a real name in this story?",
            excerpts(docs, [e.name]),
            on_yes=_confirm_name(memory, guild, campaign, e),
            on_no=_reject_name(memory, guild, campaign, e),
            key_says=fixed(key.name_says(e.name)),
        )

    # which one
    for q in await links.which_waiting(guild, campaign):
        if len(q.candidates) < 2:
            continue
        numbered = "\n".join(f"{i}. {n}" for i, (_c, n) in enumerate(q.candidates, 1))
        await asker.ask(
            WHICH,
            q.id,
            f'Which one does the text mean? "{q.other}" {q.predicate.replace("_", " ")} '
            f'"{q.word}". The candidates:\n{numbered}\nThe quote: {q.quote[:EXCERPT_CHARS]}',
            excerpts(docs, [q.other, q.word]),
            on_yes=_pick(links, guild, campaign, q),
            on_no=_neither(links, guild, campaign, q),
            key_says=functools.partial(_which_says, key, q),
        )

    # things said
    for c in await claims.waiting(guild, campaign, opted_in=opted_in):
        said = " ".join(p for p in (c.subject, c.relationship, c.object) if p)
        await asker.ask(
            SAID,
            c.id,
            f'From the session: "{said}"{f" ({c.details})" if c.details else ""}. Is this a true '
            "fact of the story, said by the DM or plainly shown?",
            excerpts(docs, [c.subject, *([c.object] if c.object else [])]),
            on_yes=_claim_yes(claims, guild, campaign, c.id, opted_in),
            on_no=_claim_no(claims, guild, campaign, c.id),
            # (a condition, "X is dead", has no tie for the key to hold: not in the key)
            key_says=fixed(key.tie_says(c.subject, c.object) if c.object else None),
        )

    # connections DMbot worked out
    for r in await memory.relations(guild, campaign, include_secret=True):
        if r.status != "proposed" or r.source != WORKED_OUT_SOURCE:
            continue
        a, b = name(r.subject_id), name(r.object_id)
        await asker.ask(
            CONNECTIONS,
            r.id,
            f'DMbot worked out: "{a}" is family of "{b}" ({r.detail or "family"}). Do the lines '
            "support it?",
            excerpts(docs, [a, b]),
            on_yes=_relation(memory, guild, campaign, r.id, CONFIRMED),
            on_no=_relation(memory, guild, campaign, r.id, REJECTED),
            key_says=fixed(key.tie_says(a, b)),
        )

    # look-alikes: read again after every answer, as the DM's list is (a join changes the rest)
    answered: set[tuple[str, str]] = set()  # (each pair once: a no leaves the person's others)
    while True:
        offers = [
            o for o in await memory.same_person_offers(guild, campaign)
            if (o.unnamed_id, o.named_id) not in answered
        ]  # fmt: skip
        if not offers:
            break
        o = offers[0]
        answered.add((o.unnamed_id, o.named_id))
        every.update({e.id: e for e in await memory.entities(guild, campaign)})
        u, n = name(o.unnamed_id), name(o.named_id)
        await asker.ask(
            LOOKALIKES,
            f"{o.unnamed_id}+{o.named_id}",
            f'Is "{u}" the same person as "{n}"? DMbot\'s reasons: {", ".join(o.reasons)}.',
            excerpts(docs, [u, n]),
            on_yes=_join(memory, guild, campaign, o.unnamed_id, o.named_id),
            on_no=_apart(memory, guild, campaign, o.unnamed_id, o.named_id, dm),
            key_says=fixed(key.same_says(u, n)),
            merge=True,
        )
    return asker.run


async def own_matches(db: Any, memory: Any, key: Key, guild: int, campaign: str) -> str:
    """DMbot's own matches (#3602: a waiting name the import's who step joined to an entry by
    itself, with `DMBOT_STORY_WHO_APPLY` on), scored against the key: the measure Supervisor set
    for switching it on. Read after the session import and before its sure step, so they are the
    who step's alone. Only on a sample campaign (`check_campaign`). Counts only."""
    from dmbot.devtools.accept_all import check_campaign

    await check_campaign(db, guild, campaign)  # (raises WrongCampaign: no override)
    joins = await memory.waiting_joins(guild, campaign)
    said = Counter(key.same_says(j.spelling, j.keep_name) for j in joins)
    return (
        f"DMbot's own matches: {len(joins)} (the key agrees with {said[YES]},"
        f" keeps apart {said[NO]}, doesn't hold {said[None]})"
    )


def fixed(says: str | None) -> Callable[[dict[str, Any]], str | None]:
    """What the key says about an item whatever the model's answer details."""

    def read(_got: dict[str, Any]) -> str | None:
        return says

    return read


def _unnamed(e: Any) -> bool:
    return bool(getattr(e, "unnamed", False)) or str(e.name).startswith(judge.UNNAMED_PREFIX)


def _which_says(key: Key, q: Any, got: dict[str, Any]) -> str | None:
    if got["answer"] == YES:
        picked = _picked(q, got)
        if picked is None:
            return None
        if key.tie_says(q.other, picked[1]) == YES:
            return YES
        # the key ties the name to another candidate: this pick is the wrong one
        others = [n for c, n in q.candidates if c != picked[0]]
        return NO if any(key.tie_says(q.other, n) == YES for n in others) else None
    # "neither": the key disagrees when it ties the name to one of the candidates
    if any(key.tie_says(q.other, n) == YES for _c, n in q.candidates):
        return YES
    return None


def _picked(q: Any, got: dict[str, Any]) -> tuple[str, str] | None:
    try:
        n = int(got.get("pick", 0))
    except (TypeError, ValueError):
        return None
    return q.candidates[n - 1] if 1 <= n <= len(q.candidates) else None


def _done(answer: str) -> None:
    """The queue answers DONE, or says why not (GONE, FULL) without raising: that is a refusal."""
    from dmbot.memory.backstory_links import DONE

    if answer != DONE:
        raise LookupError(answer)


def _confirm_name(memory: Any, guild: int, campaign: str, e: Entity) -> Act:
    async def act(got: dict[str, Any]) -> int | None:
        kind = str(got.get("kind", "")).casefold()
        if kind not in NAME_KINDS:  # the reader's own kind, when the model gave none it knows
            from dmbot.ui.names import background_kind

            kind = background_kind(e)
        if kind not in NAME_KINDS:
            raise LookupError("no kind")  # (a yes with no kind is not a decision: left waiting)
        written = await memory.confirm_entity(guild, campaign, e.id, kind, source=DM)
        return written.batch  # type: ignore[no-any-return]

    return act


def _reject_name(memory: Any, guild: int, campaign: str, e: Entity) -> Act:
    async def act(_got: dict[str, Any]) -> int | None:
        written = await memory.set_entity_status(guild, campaign, e.id, REJECTED, source=DM)
        return written.batch  # type: ignore[no-any-return]

    return act


def _pick(links: Any, guild: int, campaign: str, q: Any) -> Act:
    async def act(got: dict[str, Any]) -> int | None:
        picked = _picked(q, got)
        if picked is None:
            raise LookupError("no pick")
        _done(await links.pick_which(guild, campaign, q.id, picked[0]))
        return None  # (the queue's own answer: kept when the connection is, below)

    return act


def _neither(links: Any, guild: int, campaign: str, q: Any) -> Act:
    async def act(_got: dict[str, Any]) -> int | None:
        _done(await links.pick_which(guild, campaign, q.id, None))
        return None

    return act


def _claim_yes(claims: Any, guild: int, campaign: str, cid: str, opted_in: Collection[int]) -> Act:
    async def act(_got: dict[str, Any]) -> int | None:
        await claims.confirm(guild, campaign, cid, opted_in=opted_in)
        return await claims.placed_batch(guild, campaign, cid)  # type: ignore[no-any-return]

    return act


def _claim_no(claims: Any, guild: int, campaign: str, cid: str) -> Act:
    async def act(_got: dict[str, Any]) -> int | None:
        await claims.reject(guild, campaign, cid)
        return None

    return act


def _relation(memory: Any, guild: int, campaign: str, rid: str, status: str) -> Act:
    async def act(_got: dict[str, Any]) -> int | None:
        written = await memory.update_relation(guild, campaign, rid, source=DM, status=status)
        return written.batch  # type: ignore[no-any-return]

    return act


def _join(memory: Any, guild: int, campaign: str, unnamed: str, named: str) -> Act:
    async def act(_got: dict[str, Any]) -> int | None:
        written = await memory.join_same_person(guild, campaign, unnamed, named)
        return written.batch  # type: ignore[no-any-return]

    return act


def _apart(memory: Any, guild: int, campaign: str, unnamed: str, named: str, dm: int) -> Act:
    async def act(_got: dict[str, Any]) -> int | None:
        await memory.not_the_same_person(guild, campaign, unnamed, named, user_id=dm)
        return None

    return act
