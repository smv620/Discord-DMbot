"""Scoring one transcript against its script line, and learning sounds-like forms."""

from __future__ import annotations

from dataclasses import dataclass, field

from dmbot.devtools.stt_bakeoff.data import LINE_BY_N, NAME_BY_CANONICAL, NAMES, Line
from dmbot.devtools.stt_bakeoff.normalize import Token, normalize, normalize_words

LOW_CONFIDENCE = 0.7

Pair = tuple[int | None, int | None]  # (reference index, hypothesis index)


def align(ref: list[str], hyp: list[str]) -> tuple[int, list[Pair]]:
    """Word-level edit distance and the alignment (Levenshtein with backtrace)."""
    n, m = len(ref), len(hyp)
    d = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        d[i][0] = i
    for j in range(m + 1):
        d[0][j] = j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            cost = 0 if ref[i - 1] == hyp[j - 1] else 1
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + cost)
    pairs: list[Pair] = []
    i, j = n, m
    while i > 0 or j > 0:
        if i > 0 and j > 0 and d[i][j] == d[i - 1][j - 1] + (ref[i - 1] != hyp[j - 1]):
            pairs.append((i - 1, j - 1))
            i, j = i - 1, j - 1
        elif i > 0 and d[i][j] == d[i - 1][j] + 1:
            pairs.append((i - 1, None))
            i -= 1
        else:
            pairs.append((None, j - 1))
            j -= 1
    pairs.reverse()
    return d[n][m], pairs


def find(tokens: list[str], phrase: list[str]) -> int:
    """Index where `phrase` starts in `tokens`, or -1."""
    k = len(phrase)
    for i in range(len(tokens) - k + 1):
        if tokens[i : i + k] == phrase:
            return i
    return -1


def _forms(canonical: str) -> list[list[str]]:
    return [normalize(f) for f in NAME_BY_CANONICAL[canonical].forms]


def _said(tokens: list[str], phrase_forms: list[list[str]]) -> bool:
    return any(find(tokens, f) >= 0 for f in phrase_forms)


@dataclass(slots=True)
class LineScore:
    line: int
    names_expected: int = 0
    names_correct: int = 0
    correct: list[str] = field(default_factory=list)
    missed: list[str] = field(default_factory=list)
    false_names: list[str] = field(default_factory=list)
    rules_expected: int = 0
    rules_correct: int = 0
    word_errors: int = 0
    ref_words: int = 0
    # How often names came back marked low-confidence, for missed and for correct names
    # (a service that marks everything doubtful shouldn't look good).
    missed_low_conf: int = 0
    missed_conf_known: int = 0
    correct_low_conf: int = 0
    correct_conf_known: int = 0


def _hyp_span(pairs: list[Pair], start: int, length: int) -> list[int]:
    """Hypothesis indexes standing in for reference tokens [start, start+length).

    Extra words between the neighbouring reference words count too, so "bell or us"
    (two insertions and a substitution) is all attributed to "belleros".
    """
    end = start + length
    out: list[int] = []
    for r, h in pairs:
        if r is not None and r < start:
            out.clear()  # anything so far belongs before the span
        elif r is not None and r >= end:
            break
        elif h is not None:
            out.append(h)
    return out


def _trim_span(span: list[int], hyp: list[str], name: list[str]) -> list[int]:
    """The run of heard words within `span` whose spelling is closest to the name.

    Drops neighbouring words that alignment lumped in: "the quill on" -> "quill on",
    "val zimmer please" -> "val zimmer". At most two extra words over the name's length.
    """
    target = "".join(name)
    best: list[int] = span
    best_key: tuple[int, int] | None = None
    longest = len(name) + 2
    for i in range(len(span)):
        for j in range(i + 1, min(len(span), i + longest) + 1):
            sub = span[i:j]
            dist, _ = align(list(target), list("".join(hyp[h] for h in sub)))
            key = (dist, len(sub))
            if best_key is None or key < best_key:
                best, best_key = sub, key
    return best


def score_line(line: Line, words: list[tuple[str, float | None]]) -> LineScore:
    hyp_tokens: list[Token] = normalize_words(words)
    hyp = [t for t, _ in hyp_tokens]
    ref = normalize(line.text)
    errors, pairs = align(ref, hyp)
    s = LineScore(line.n, word_errors=errors, ref_words=len(ref))
    for canonical in line.names:
        s.names_expected += 1
        found = next((f for f in _forms(canonical) if find(hyp, f) >= 0), None)
        if found is not None:
            s.names_correct += 1
            s.correct.append(canonical)
            at = find(hyp, found)
            known = [c for _, c in hyp_tokens[at : at + len(found)] if c is not None]
            if known:
                s.correct_conf_known += 1
                s.correct_low_conf += min(known) < LOW_CONFIDENCE
            continue
        s.missed.append(canonical)
        start = find(ref, normalize(canonical))
        if start >= 0:
            span = _hyp_span(pairs, start, len(normalize(canonical)))
            confs = [hyp_tokens[h][1] for h in span]
            known = [c for c in confs if c is not None]
            if known:
                s.missed_conf_known += 1
                s.missed_low_conf += min(known) < LOW_CONFIDENCE
    for rule in line.rules:
        s.rules_expected += 1
        if find(hyp, normalize(rule)) >= 0:
            s.rules_correct += 1
    for name in NAMES:
        if name.is_word or name.canonical in line.names:
            continue
        if _said(hyp, _forms(name.canonical)):
            s.false_names.append(name.canonical)
    return s


def score(line_n: int, words: list[tuple[str, float | None]]) -> LineScore:
    return score_line(LINE_BY_N[line_n], words)


def heard_as(line: Line, words: list[tuple[str, float | None]], canonical: str) -> str:
    """What the service wrote where `canonical` was said ("" if nothing)."""
    hyp = [t for t, _ in normalize_words(words)]
    ref = normalize(line.text)
    _, pairs = align(ref, hyp)
    phrase = normalize(canonical)
    start = find(ref, phrase)
    if start < 0:
        return ""
    span = _trim_span(_hyp_span(pairs, start, len(phrase)), hyp, phrase)
    return " ".join(hyp[h] for h in span)


def misheard_forms(line: Line, words: list[tuple[str, float | None]]) -> dict[str, str]:
    """For each name the service missed, what it wrote instead ("bell or us").

    These become learned sounds-like hints. Empty or digit-only results are skipped.
    """
    hyp = [t for t, _ in normalize_words(words)]
    ref = normalize(line.text)
    _, pairs = align(ref, hyp)
    out: dict[str, str] = {}
    for canonical in line.names:
        if _said(hyp, _forms(canonical)):
            continue
        phrase = normalize(canonical)
        start = find(ref, phrase)
        if start < 0:
            continue
        span = _trim_span(_hyp_span(pairs, start, len(phrase)), hyp, phrase)
        heard = " ".join(hyp[h] for h in span)
        if heard and heard != " ".join(phrase) and not any(c.isdigit() for c in heard):
            out[canonical] = heard
    return out
