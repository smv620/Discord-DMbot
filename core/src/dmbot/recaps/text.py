"""Small text helpers the recap stages share (#1382): the words of a text, the cues, the proper
nouns of a narration and the secret-name check. Pure functions; nothing here touches Discord or
the database."""

from __future__ import annotations

import re
from collections.abc import Collection

# The secret-name check is one matcher for every caller (#2087); re-exported so the recap stages
# keep importing it from the text helpers.
from dmbot.secrets_match import WHOLE_NAME as WHOLE_NAME
from dmbot.secrets_match import names_a_secret as names_a_secret
from dmbot.secrets_match import secret_rule as secret_rule
from dmbot.word_end import at_word_end as at_word_end  # (kept here for the recaps' callers)

_CUE = re.compile(r"《[^《》]{1,60}》")
_PERFORMANCE = re.compile(r"\(\([^()]{1,60}\)\)")
_WORD = re.compile(r"[\w'’]+")
_CAPITAL = re.compile(r"(?<![\w'’-])([A-ZÀ-Þ][\w'’-]*)")
_POSSESSIVE = re.compile(r"['’]s$", re.IGNORECASE)
NOT_NAMES = frozenset({"i", "the", "a", "an", "and", "but", "or", "as", "day", "night"})

CUE = _CUE
PERFORMANCE = _PERFORMANCE


def words(text: str) -> list[str]:
    """The words of a text, lower case, possessives taken off ("Garrick's" is "garrick")."""
    return [_POSSESSIVE.sub("", w).lower().strip("'’-") for w in _WORD.findall(text)]


def strip_cues(text: str) -> str:
    """The text as the table reads it: no 《emotion》 and no ((performance)) cue."""
    text = _PERFORMANCE.sub("", _CUE.sub("", text))
    return re.sub(r"[ \t]+([,.;:!?])", r"\1", re.sub(r"[ \t]{2,}", " ", text)).strip()


def narration_of(text: str) -> str:
    """The text with its cues taken out, a space where each was."""
    return _PERFORMANCE.sub(" ", _CUE.sub(" ", text))


# Ordinary words a narrated sentence starts with. A sentence-initial word is checked like any
# other name unless it is one of these, or appears in lower case elsewhere in the same text (then
# it is an ordinary word, not a name). Kept to words that are never names in a campaign.
OPENERS = frozenset(
    """a about above across after again against all almost along already also although always
    among an and another any anyone anything around as at away back be because before behind
    below beneath beside besides between beyond both but by cold dark deep despite did do does
    down during each either else enough even ever every everyone everything far few finally first
    for from further had has have he her here hers herself him himself his how however if in
    inside instead into is it its itself just last later less like little long many maybe me
    meanwhile might more most much must my near neither never next no nobody none nor not nothing
    now of off often on once one only or other others our out outside over own perhaps quickly
    quietly rather really same several she should shortly silence slowly slowly so some someone
    something sometimes somewhere soon still such suddenly than that the their them then there
    these they this those though three through throughout thus time to together too toward
    towards two under until up upon us very was we well were what when whenever where whether
    which while who whom whose why will with within without would yet you your""".split()  # noqa: SIM905
)


def proper_nouns(text: str, *, sentence_starts: bool = False) -> list[str]:
    """Capitalised words in the text that could be names. A sentence's first word counts too,
    unless it is an ordinary opener (`OPENERS`) or the text also uses it in lower case; with
    `sentence_starts` off, first words are skipped altogether (the names a beat names are checked
    from its `who` list instead)."""
    plain = narration_of(text)
    lower_elsewhere = {w for w in _WORD.findall(plain) if w[:1].islower()}
    found: list[str] = []
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", plain):
        first = _WORD.search(sentence)
        for match in _CAPITAL.finditer(sentence):
            if first is not None and match.start() == first.start():
                word = match.group(1)
                if not sentence_starts:
                    continue
                if word.lower() in OPENERS or word.lower() in lower_elsewhere:
                    continue
            found.append(match.group(1))
    return found


def is_known_name(name: str, allowed_names: Collection[str]) -> bool:
    """The name is one of the confirmed names (all its words are in one of them)."""
    mine = set(words(name))
    return bool(mine) and any(mine <= set(words(a)) for a in allowed_names)
