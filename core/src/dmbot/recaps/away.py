"""The "reader who was away" check for a recap (#2680): can someone who missed the session follow
the draft? Pure: no database, no AI call, nothing invented.

The owner's test found a recap that was accurate and in order but hard to follow: it opened on a
pronoun, used a name before saying who that was, left a favour with no result, and gave a main
character no moment. This is the critic for those, beside PlotBot and the motive check
(`recaps.critics`). It returns **findings only**, never rewritten text: each is a fixed sentence
in code's words about one sentence of the draft, so the writer is never handed something to copy
and nothing here can add story. A finding goes back to the writer once, as a targeted change
(`critics.Change`); what is still there after that is a plain note for the DM, who decides.

What it looks for, all from the draft and what the table already has (the confirmed names, the
earlier recap and the beat sheet's player characters):
- **opening**: the first sentence starts with a pronoun, so the reader does not know who it is;
- **introduce**: a name the earlier recap did not mention is used with nothing that says who or what
  it is (no "the old smith Roan", "Roan, a smith", "a man called Roan"); since #2950 also a
  "the <noun>" for an item the story memory knows ("the lantern"), the earlier recap did not
  mention and the sentence does not explain;
- **thread**: a favour, debt, promise or threat is mentioned and nothing later says what came of it
  or that it is still open;
- **closing**: the last sentence is a threat or warning with no word of the party in it, so the
  reader is not told why they should care;
- **moment**: a player character who was in the session is not in the recap at all;
- **listed**: a player character is only named in a list ("Mira, Oskar and Brann …") and is the
  subject of no sentence, so the recap tells nothing they did (#2950);
- **goal**: the draft never says where the party is going or what it wants, though an open story
  thread says so (#2950).

Lengths are never looked at (owner, #2597), and these are patterns, not understanding: a finding is
a thing to check, so every one is soft and none is ever a refusal.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass

from dmbot.recaps.critics import Change

OPENING, INTRODUCE, THREAD, CLOSING, MOMENT = "opening", "introduce", "thread", "closing", "moment"
LISTED, GOAL = "listed", "goal"
KINDS = (OPENING, INTRODUCE, THREAD, CLOSING, MOMENT, LISTED, GOAL)
MAX_FINDINGS = 6  # for one reading: the writer is asked for a few things well, not everything
MAX_LISTED = 2  # and a party all named in one list should not take the other findings' places
MAX_INTRODUCE = 3  # a draft full of new names should not drown the other findings
# Most useful first, so the cap drops the least useful.
_PRIORITY = (OPENING, MOMENT, GOAL, LISTED, CLOSING, THREAD, INTRODUCE)

# Pronouns that open a sentence with nobody named ("That night, …" and "Then Maris …" are fine).
_PRONOUNS = frozenset(
    {"he", "she", "they", "it", "we", "his", "her", "their", "its", "him", "them"}
)
# A favour, a debt, a promise or a threat: something that should come to a result.
_THREAD = re.compile(
    r"\b(owe[sd]?|debt|favou?r|promis(?:e|ed|es)|vow(?:ed|s)?|swore|sworn|oath|bargain|"
    r"threat(?:en(?:ed|s)?)?|warn(?:ed|s|ing)?)\b",
    re.IGNORECASE,
)
# What says it came to something, or that it is still open.
_RESULT = re.compile(
    r"\b(paid|repaid|settled|kept|keeps|broke|broken|refused|refuses|accepted|accepts|forgave|"
    r"fulfil(?:led|ed)?|honou?red|released|cancel(?:led|ed)?|resolved|still|remains?|unpaid|"
    r"unresolved|open|yet|until|unanswered)\b",
    re.IGNORECASE,
)
# Wanting, seeking or heading somewhere: the draft says what the party is after.
_GOAL = re.compile(
    r"\b(seek(?:s|ing)?|sought|search(?:es|ed|ing)?|in search of|hunt(?:s|ed|ing)? for|"
    r"to find|bound for|head(?:s|ed|ing)? (?:to|for|toward|towards)|on (?:the|their) way to|"
    r"hop(?:e|es|ed|ing) to|aim(?:s|ed|ing)? to|want(?:s|ed)? to|need(?:s|ed)? to|"
    r"set out|journey(?:s|ed|ing)? to|trek to|quest|mission|goal|plan(?:s|ned)? to|"
    r"intend(?:s|ed)? to|determined to|vow(?:s|ed)? to|to reach|to stop|to save|"
    r"to recover|to reclaim|to rescue|to escape)\b",
    re.IGNORECASE,
)
# The "the <noun>" of a thing that needs a few words on what it is, like "the lantern".
_WHO_AFTER = re.compile(
    r"^\s*(?:,\s*(?:a|an|which|that|who)\b|\s(?:that|which|who)\b|\()", re.IGNORECASE
)
_PARTY = re.compile(r"\b(party|company|travellers|travelers|adventurers|heroes)\b", re.IGNORECASE)
# Right after, or right before, a name: something that says who or what it is.
_AFTER = re.compile(r"^\s*(?:,\s*(?:a|an|the|who|whose|once|former|formerly)\b|\()", re.IGNORECASE)
_BEFORE = re.compile(
    r"(?:\b(?:a|an|the|old|young|local|famed|named|called|known as)\s+(?:[\w'-]+\s+){0,1}|"
    r"\b(?:named|called|known as)\s+)$",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class Finding:
    kind: str
    sentence: int  # 1-based, as the critics number the draft's sentences; 0: the draft as a whole
    name: str = ""  # a name or a player character, where the kind has one


def _first_word(sentence: str) -> str:
    match = re.search(r"[A-Za-z][A-Za-z'’-]*", sentence)
    return match.group(0).casefold().replace("’", "'") if match else ""


def _mentions(sentence: str, spellings: Sequence[str]) -> re.Match[str] | None:
    for spelling in spellings:
        found = re.search(rf"(?<![\w'-]){re.escape(spelling)}(?![\w-])", sentence)
        if found:
            return found
    return None


def _spellings(name: str, aliases: Mapping[str, str]) -> list[str]:
    """The name and every other spelling the table has for it, longest first."""
    others = [spelling for spelling, canonical in aliases.items() if canonical == name]
    return sorted({name, *others}, key=len, reverse=True)


def _introduced(sentence: str, found: re.Match[str]) -> bool:
    """Does the sentence say, around this mention, who or what it is?"""
    return bool(_AFTER.match(sentence[found.end() :]) or _BEFORE.search(sentence[: found.start()]))


def _only_in_lists(
    sentences: Sequence[str], spellings: Sequence[str], run_names: Sequence[str]
) -> bool:
    """Is this character named only inside lists of three or more names ("Maris, Tavi and Roan
    balked …")? Any other mention counts as a moment, "Maris and Tavi slipped through" included: a
    pattern, not understanding, so it only calls a mention a list when it clearly is one (#2950)."""
    name = "|".join(re.escape(n) for n in sorted(run_names, key=len, reverse=True))
    run = re.compile(
        rf"(?<![\w'-])(?:{name})(?![\w-])(?:\s*(?:,|&|\band\b)\s*(?:and\s+)?"
        rf"(?<![\w'-])(?:{name})(?![\w-])){{2,}}"
    )
    seen = False
    for sentence in sentences:
        for found in _all_mentions(sentence, spellings):
            seen = True
            if not any(m.start() <= found.start() < m.end() for m in run.finditer(sentence)):
                return False
    return seen


def _all_mentions(sentence: str, spellings: Sequence[str]) -> list[re.Match[str]]:
    out: list[re.Match[str]] = []
    for spelling in spellings:
        out.extend(re.finditer(rf"(?<![\w'-]){re.escape(spelling)}(?![\w-])", sentence))
    return out


def _names_a_thread(text: str, threads: Sequence[str]) -> bool:
    """Does the draft speak of an open thread: two of its words, or all of a short one's?"""
    have = set(re.findall(r"[a-z']{4,}", text.casefold()))
    for thread in threads:
        mine = set(re.findall(r"[a-z']{4,}", thread.casefold()))
        if mine and len(mine & have) >= min(2, len(mine)):
            return True
    return False


def findings(
    sentences: Sequence[str],
    *,
    names: Collection[str],
    aliases: Mapping[str, str],
    previous: str,
    present: Collection[str],
    things: Collection[str] = (),
    threads: Sequence[str] = (),
) -> list[Finding]:
    """What a reader who was away could not follow. `names` are the confirmed names (never a
    secret one), `previous` the earlier recap, `present` the player characters in the session,
    `things` the nouns of the items the players know ("lantern"), `threads` the open story threads
    the players may know (#2950)."""
    if not sentences:
        return []
    out: list[Finding] = []
    if _first_word(sentences[0]) in _PRONOUNS:
        out.append(Finding(OPENING, 1))
    known = previous.casefold()
    in_party = {aliases.get(pc, pc) for pc in present}
    # a name is introduced where it first appears; the earlier recap and a player character
    # need none. A spelling that is only another way to write a name is looked at with it.
    for name in sorted(names):
        if name in aliases:
            continue
        spellings = _spellings(name, aliases)
        if name in in_party or any(sp.casefold() in known for sp in spellings):
            continue
        for number, sentence in enumerate(sentences, start=1):
            found = _mentions(sentence, spellings)
            if found:
                if not _introduced(sentence, found):
                    out.append(Finding(INTRODUCE, number, name))
                break
    for noun in sorted(t.casefold() for t in things):
        if not noun or noun in known:
            continue
        for number, sentence in enumerate(sentences, start=1):
            found = re.search(rf"(?<![\w'-]){re.escape(noun)}(?![\w-])", sentence, re.IGNORECASE)
            if not found:
                continue
            before = sentence[: found.start()]
            told = bool(re.search(r"\b(?:a|an)\s+(?:[\w'-]+\s+){0,2}$", before, re.IGNORECASE))
            if not told and not _WHO_AFTER.match(sentence[found.end() :]):
                out.append(Finding(INTRODUCE, number, f"the {noun}"))
            break  # the first mention is the one that has to say what it is
    for number, sentence in enumerate(sentences, start=1):
        if number == len(sentences) or not _THREAD.search(sentence) or _RESULT.search(sentence):
            continue
        if not _RESULT.search(" ".join(sentences[number:])):
            out.append(Finding(THREAD, number))
            break  # one thread at a time: the writer fixes the first and the rest is the DM's
    last = sentences[-1]
    if _THREAD.search(last) and not _PARTY.search(last) and not _mentions(last, sorted(present)):
        out.append(Finding(CLOSING, len(sentences)))
    whole = " ".join(sentences)
    for pc in sorted(present):
        spellings = _spellings(pc, aliases)
        if not _mentions(whole, spellings):
            out.append(Finding(MOMENT, 0, pc))
        elif _only_in_lists(sentences, spellings, [*names, *present]):
            out.append(Finding(LISTED, 0, pc))
    if threads and not _GOAL.search(whole) and not _names_a_thread(whole, threads):
        out.append(Finding(GOAL, 0))
    kept: list[Finding] = []
    for kind in _PRIORITY:
        mine = [f for f in out if f.kind == kind]
        cap = {INTRODUCE: MAX_INTRODUCE, LISTED: MAX_LISTED}.get(kind)
        kept.extend(mine if cap is None else mine[:cap])
    return kept[:MAX_FINDINGS]


_ITEM_LINE = re.compile(r"^(.+?) \(item[,)]")


def things_of(sheet: Sequence[str]) -> tuple[str, ...]:
    """The nouns of the items on the writer's who-and-what sheet: the head word of each item's name
    ("Warding Lantern (item)" gives "lantern", "Ring of Protection (item)" gives "ring"). A
    one-word name is a proper name, which the names check already handles, so it gives none. Only
    what the players know is on the sheet, so a secret item is never a noun here (#2950)."""
    out: list[str] = []
    for line in sheet:
        found = _ITEM_LINE.match(line.strip())
        if not found:
            continue
        head = re.split(r"\s+of\s+", found.group(1).strip(), maxsplit=1)[0]
        parts = re.findall(r"[A-Za-z][A-Za-z'-]{2,}", head)
        if len(re.findall(r"[A-Za-z][A-Za-z'-]*", found.group(1))) > 1 and parts:
            out.append(parts[-1].casefold())
    return tuple(dict.fromkeys(out))


def _quote(sentences: Sequence[str], number: int) -> str:
    return f'sentence {number} ("{sentences[number - 1].strip()}")' if number else "the draft"


def changes(found: Sequence[Finding], sentences: Sequence[str]) -> list[Change]:
    """The findings as soft changes: the writer is told in code's words (which add no story), the
    DM in a neutral note."""
    out: list[Change] = []
    for f in found:
        where = _quote(sentences, f.sentence)
        if f.kind == OPENING:
            ask = f"{where}: open with the name of who it is about, not a pronoun"
            note = "Check: the recap opens without saying who it is about."
        elif f.kind == INTRODUCE:
            ask = (
                f"{where}: say in a few words who or what {f.name} is, using only the names list, "
                "the beats, the transcript, the who-and-what sheet and the earlier recap; "
                "if none of them says, leave the detail out"
            )
            note = f"Check: {f.name} is named without saying who or what that is."
        elif f.kind == THREAD:
            ask = (
                f"{where}: say what came of it, or that it is still open, only if the beats say; "
                "otherwise leave it out"
            )
            note = f"Check: sentence {f.sentence} may leave a favour, promise or threat hanging."
        elif f.kind == CLOSING:
            ask = f"{where}: say why this matters to the party, from the beats"
            note = "Check: the recap ends on a threat without saying why the party should care."
        elif f.kind == LISTED:
            ask = (
                f"{f.name}: say something they did, as a sentence of its own, from the beats; "
                "a name in a list is not a moment"
            )
            note = f"Check: {f.name} is named but the recap tells nothing they did."
        elif f.kind == GOAL:
            ask = (
                "somewhere early in the recap: say where the party is going or what it wants, "
                "only if the beats, the transcript or an open story thread say so; otherwise "
                "leave it out"
            )
            note = "Check: the recap does not say where the party is going or what it wants."
        else:
            ask = f"{f.name}: give them a moment in the recap, something the beats say they did"
            note = f"Check: {f.name} is not in the recap."
        out.append(Change("away", ask, False, f.sentence, note))
    return out


def notes(found: Sequence[Finding], sentences: Sequence[str]) -> tuple[str, ...]:
    """What the DM reads for findings that remain after the writer's one fix."""
    return tuple(c.note for c in changes(found, sentences))


_NOTES = re.compile(
    r"^Check: (the recap opens without saying who it is about\."
    r"|.+ is named without saying who or what that is\."
    r"|sentence \d+ may leave a favour, promise or threat hanging\."
    r"|the recap ends on a threat without saying why the party should care\."
    r"|.+ is named but the recap tells nothing they did\."
    r"|the recap does not say where the party is going or what it wants\."
    r"|.+ is not in the recap\.)$"
)


def is_note(text: str) -> bool:
    """Is this one of this module's notes? The stop path rebuilds them from the final text, so
    an older reading's copy (with that draft's sentence numbers) must not stay beside them."""
    return bool(_NOTES.match(text))
