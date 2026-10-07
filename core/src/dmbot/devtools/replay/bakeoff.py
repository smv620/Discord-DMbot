"""Scores a replay of the bake-off script (docs/test-scripts/stt-bakeoff.md) the way its
scoring key asks: every time a campaign name is said, was it written right, wrong or not
at all; does the nickname "Bell" stay "Bell"; the rules words; names written where none
was said (the tricky lines); and the word error rate of the everyday lines.

Names are scored wherever they land in the text, not line by line: the recording is one
reader going straight through, so pieces of speech are cut wherever the pauses fall, as
at a real table. A name cut in half by a piece boundary is a real finding.

One known blur: when a wrongly written name sits right next to a name that was left out,
the alignment can match the heard word to either one (both cost the same edits), so
"wrong" and "not at all" may swap between those two names. The totals stay right.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from dmbot.devtools.replay.score import align, merge_splits, words

# Line ranges from the script's scoring key.
NAME_LINES = range(1, 49)
TRICKY_LINES = range(49, 58)
WER_LINES = range(58, 66)
NICKNAME = "Bell"

_LINE = re.compile(r"^(\d+)\.\s+(.+)$")
_ROW = re.compile(r"^\|(.+)\|\s*$")


@dataclass(frozen=True, slots=True)
class Term:
    name: str
    spellings: tuple[str, ...]  # the name and every accepted spelling
    capitalised: bool = True  # only counts where the script writes it with a capital


@dataclass(frozen=True, slots=True)
class Bakeoff:
    lines: dict[int, str]
    names: tuple[Term, ...]  # the name list, without the nickname
    nickname: Term
    rules: tuple[Term, ...]


def parse_bakeoff(text: str) -> Bakeoff:
    lines: dict[int, str] = {}
    names: list[Term] = []
    nickname: Term | None = None
    rules_text: list[str] = []
    in_rules = False
    section = ""
    for raw in text.splitlines():
        line = raw.strip()
        if in_rules:
            if line:
                rules_text.append(line)  # the list runs on until a blank line
                continue
            in_rules = False
        if line.startswith("## "):
            section = line[3:].casefold()
            continue
        if section == "the lines" and (match := _LINE.match(line)):
            lines[int(match.group(1))] = match.group(2)
        elif section == "name list" and (match := _ROW.match(line)):
            cells = [c.strip() for c in match.group(1).split("|")]
            if len(cells) < 2 or cells[0] in ("Name", "") or set(cells[0]) <= {"-"}:
                continue
            name = re.sub(r"\s*\(.*\)$", "", cells[0]).strip()
            spellings = (name, *(s.strip() for s in cells[1].split(",") if s.strip()))
            term = Term(name, spellings)
            if name == NICKNAME:
                nickname = term
            else:
                names.append(term)
        elif section == "name list" and line.startswith("**Rules words**"):
            if ":" not in line:
                raise ValueError("bake-off script: the rules words line needs a ':'")
            rules_text = [line.split(":", 1)[1]]
            in_rules = True
    listed = " ".join(rules_text).strip().rstrip(".")
    rules = [Term(w, (w,), capitalised=False) for w in (w.strip() for w in listed.split(",")) if w]
    if not lines or not names or nickname is None:
        raise ValueError("not a bake-off script: needs '## The lines' and '## Name list'")
    return Bakeoff(lines=lines, names=tuple(names), nickname=nickname, rules=tuple(rules))


def load_bakeoff(path: Path) -> Bakeoff:
    return parse_bakeoff(path.read_text(encoding="utf-8"))


def is_bakeoff(text: str) -> bool:
    return "## The lines" in text and "## Name list" in text


@dataclass(slots=True)
class TermScore:
    said: int = 0
    right: int = 0
    wrong: list[str] = field(default_factory=list)  # what was written instead
    missing: int = 0
    cut: int = 0  # written right, but split between two pieces of speech


@dataclass(slots=True)
class BakeoffScore:
    names: dict[str, TermScore]
    nickname: TermScore
    expanded: int  # times "Bell" came out as "Belleros"
    rules: dict[str, TermScore]
    false_names: list[str]  # names written in lines that have none
    wer_words: int
    wer_errors: int

    @staticmethod
    def total(scores: dict[str, TermScore]) -> TermScore:
        out = TermScore()
        for s in scores.values():
            out.said += s.said
            out.right += s.right
            out.wrong += s.wrong
            out.missing += s.missing
            out.cut += s.cut
        return out


def _initial(original: str) -> str:
    """The first letter of a script word, past any opening quote."""
    return next((c for c in original if c.isalnum()), "")


def _find(tokens: Sequence[str], originals: Sequence[str], term: Term) -> list[int]:
    """Where the term starts in the script's words (its main spelling)."""
    target = words(term.name)
    if not target:
        return []
    found = []
    for i in range(len(tokens) - len(target) + 1):
        if list(tokens[i : i + len(target)]) != target:
            continue
        if term.capitalised and not _initial(originals[i]).isupper():
            continue  # "ring the bell" isn't the nickname
        found.append(i)
    return found


def _forms(term: Term) -> list[list[str]]:
    """Each accepted spelling once, as compared ("Ka'zeth" and "Kazeth" are one)."""
    seen: dict[tuple[str, ...], None] = {}
    for spelling in term.spellings:
        seen.setdefault(tuple(words(spelling)), None)
    return [list(form) for form in seen if form]


def score_bakeoff(script: Bakeoff, pieces: Sequence[str]) -> BakeoffScore:
    """`pieces`: the text of each piece of speech, in order."""
    ref: list[str] = []
    originals: list[str] = []  # the script word each normalised word came from
    line_of: list[int] = []
    for number, text in sorted(script.lines.items()):
        for original in text.split():
            for word in words(original):
                ref.append(word)
                originals.append(original)
                line_of.append(number)
    hyp: list[str] = []
    hyp_raw: list[str] = []  # as written, for capitals
    piece_of: list[int] = []  # which piece of speech each heard word came from
    for index, text in enumerate(pieces):
        for raw in text.split():
            for word in words(raw):
                hyp.append(word)
                hyp_raw.append(raw)
                piece_of.append(index)
    # Plain alignment for names: a name heard as two words ("Draven Moor") keeps both.
    steps = align(ref, hyp)
    heard_at: dict[int, list[int]] = {}  # script word -> heard words aligned to it
    added_after: dict[int, list[int]] = {}  # heard words with no script word, by place
    last_ref = -1
    for step in steps:
        if step.ref is not None:
            last_ref = step.ref
            if step.hyp is not None:
                heard_at.setdefault(step.ref, []).append(step.hyp)
        elif step.hyp is not None:
            added_after.setdefault(last_ref, []).append(step.hyp)

    def heard(start: int, length: int) -> list[int]:
        """The heard words for script words start .. start+length-1."""
        got: list[int] = []
        for i in range(start, start + length):
            got += heard_at.get(i, [])
            if i < start + length - 1:
                got += added_after.get(i, [])  # an extra word inside a name: part of it
        return sorted(got)

    def score_term(term: Term, lines: range) -> TermScore:
        result = TermScore()
        accepted = {"".join(form) for form in _forms(term)}
        length = len(words(term.name))
        for start in _find(ref, originals, term):
            if line_of[start] not in lines:
                continue
            result.said += 1
            got = heard(start, length)
            # Words written just before or after may be part of it ("Draven" + "Moor").
            before = added_after.get(start - 1, [])[-2:]
            after = added_after.get(start + length - 1, [])[:2]
            tries = [
                b + got + a
                for b in (before[i:] for i in range(len(before) + 1))
                for a in (after[:i] for i in range(len(after) + 1))
            ]
            match = next((t for t in tries if t and "".join(hyp[j] for j in t) in accepted), None)
            if match is not None:
                if len({piece_of[j] for j in match}) > 1:
                    result.cut += 1  # live, the two halves arrive as separate pieces
                else:
                    result.right += 1
            elif not got:
                result.missing += 1
            else:
                result.wrong.append(" ".join(hyp[j] for j in [*before, *got, *after]))
        return result

    names = {term.name: score_term(term, NAME_LINES) for term in script.names}
    nickname = score_term(script.nickname, NAME_LINES)
    expanded_forms = next(
        ({"".join(f) for f in _forms(t)} for t in script.names if t.name == "Belleros"), set()
    )
    expanded = sum(1 for got in nickname.wrong if "".join(got.split()) in expanded_forms)
    rules = {term.name: score_term(term, range(1, 100)) for term in script.rules}

    # Names written in the lines that have none (the tricky, off-topic and everyday ones).
    region = sorted(
        [j for i, js in heard_at.items() if line_of[i] >= TRICKY_LINES.start for j in js]
        + [
            j
            for i, js in added_after.items()
            if i >= 0 and line_of[i] >= TRICKY_LINES.start
            for j in js
        ]
    )
    region_words = [hyp[j] for j in region]
    false_names: list[str] = []
    for term in script.names:
        for target in _forms(term):
            for i in range(len(region_words) - len(target) + 1):
                if region_words[i : i + len(target)] == target:
                    false_names.append(term.name)
    # The nickname is an everyday word too ("ring the bell"): only a capital counts.
    nick_forms = {f[0] for f in _forms(script.nickname) if len(f) == 1}
    for j in region:
        if hyp[j] in nick_forms and _initial(hyp_raw[j]).isupper():
            false_names.append(script.nickname.name)

    # Word error rate the README's way: a word split in two or two joined is one error.
    wer_words = sum(1 for n in line_of if n in WER_LINES)
    wer_errors = 0
    last_line: int | None = None
    for step in merge_splits(steps, ref, hyp):
        if step.ref is not None:
            last_line = line_of[step.ref]
        if last_line in WER_LINES and step.kind != "ok":
            wer_errors += 1
    return BakeoffScore(
        names=names,
        nickname=nickname,
        expanded=expanded,
        rules=rules,
        false_names=false_names,
        wer_words=wer_words,
        wer_errors=wer_errors,
    )


def bakeoff_record(score: BakeoffScore) -> list[str]:
    """Lines for the record: counts and the script's own names only, never what was
    heard (that goes to the screen)."""
    total = BakeoffScore.total(score.names)
    rules = BakeoffScore.total(score.rules)
    per_name = ", ".join(f"{name} {s.right}/{s.said}" for name, s in score.names.items() if s.said)
    nick = score.nickname
    wer = 100 * score.wer_errors / score.wer_words if score.wer_words else 0.0
    return [
        f"names ({len(score.names)}, Ashen Crown included): {total.right} of {total.said} "
        f"right ({len(total.wrong)} wrong, {total.missing} missing, {total.cut} cut in half "
        "between two pieces of speech)",
        f"  per name: {per_name}",
        f'nickname "Bell": {nick.right} of {nick.said} kept ({score.expanded} written as Belleros)',
        f"rules words: {rules.right} of {rules.said} right",
        f"false names in lines with none: {len(score.false_names)}"
        + (f" ({', '.join(score.false_names)})" if score.false_names else ""),
        f"everyday and off-topic lines: word error rate {wer:.0f}% "
        f"({score.wer_errors} of {score.wer_words} words)",
    ]


def misheard(score: BakeoffScore) -> list[str]:
    """What each name was written as when it was wrong, for the screen only."""
    out = []
    for name, s in [*score.names.items(), (NICKNAME, score.nickname), *score.rules.items()]:
        if s.wrong or s.missing or s.cut:
            heard = "; ".join(f'"{w}"' for w in s.wrong) or "-"
            out.append(f"{name}: written as {heard}; missing {s.missing}; cut {s.cut}")
    return out
