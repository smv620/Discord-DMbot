"""A place's, group's or people's feeling toward a lineage or a named culture, read from set phrases
in the lore (#4229 slice 4; Supervisor's ruling on #4229, 2026-11-21): "Mithral Hall distrusts
orcs", "the Lords' Alliance welcomes halflings". No AI: a holder the campaign already has, one
of a fixed list of feeling verbs right after it, and a lineage or one of the campaign's named
cultures right after the verb, all in one sentence. What it finds is DMbot's read: it waits in
World notes until the DM keeps it, and a people's standing never flows onto a single character (the
owner's rule, 2026-11-12), so a person is never a holder. Pure: no database, no AI.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

from dmbot.memory import place_peoples, standing

# The verbs, each to one of standing's five steps. Plain and few on purpose: a missed bias costs a
# line in World notes the DM can add by hand; a wrong one costs the DM a 🚫.
_HOSTILE = ("hates", "hate", "despises", "despise", "loathes", "loathe")
_UNFRIENDLY = (
    "distrusts", "distrust", "mistrusts", "mistrust", "dislikes", "dislike", "shuns", "shun",
    "resents", "resent",
)  # fmt: skip
_WARY = ("fears", "fear", "is wary of", "are wary of")
# The five steps' own words, as World notes says them ("is friendly toward dwarves"; Supervisor's
# ruling C3 on #4982): "is" or "are", the step, then its word before a people
_STEP_WORDS = (
    (standing.HOSTILE, ("hostile to", "hostile toward", "hostile towards")),
    (standing.UNFRIENDLY, ("unfriendly to", "unfriendly toward", "unfriendly towards")),
    (standing.FRIENDLY, ("friendly to", "friendly toward", "friendly towards")),
    (standing.DEVOTED, ("devoted to",)),
)
_FRIENDLY = ("welcomes", "welcome", "trusts", "trust", "likes", "like")
_DEVOTED = ("reveres", "revere", "loves", "love", "honours", "honour", "honors", "honor")
VERBS: dict[str, str] = {
    **dict.fromkeys(_HOSTILE, standing.HOSTILE),
    **dict.fromkeys(_UNFRIENDLY, standing.UNFRIENDLY),
    **dict.fromkeys(_WARY, standing.WARY),
    **dict.fromkeys(_FRIENDLY, standing.FRIENDLY),
    **dict.fromkeys(_DEVOTED, standing.DEVOTED),
    **{
        f"{be} {words}": step
        for step, said in _STEP_WORDS
        for words in said
        for be in ("is", "are")
    },
}
# The perfect tense, after "has" or "have" ("has long despised elves", Supervisor's ruling C2 on
# #4982). Only the perfect: a bare past ("despised elves") may be a feeling that has ended, so it
# stays a miss, the safe side.
_PERFECT: dict[str, str] = {
    **dict.fromkeys(("hated", "despised", "loathed"), standing.HOSTILE),
    **dict.fromkeys(
        ("distrusted", "mistrusted", "disliked", "shunned", "resented"), standing.UNFRIENDLY
    ),
    "feared": standing.WARY,
    **dict.fromkeys(("welcomed", "trusted", "liked"), standing.FRIENDLY),
    **dict.fromkeys(("revered", "loved", "honoured", "honored"), standing.DEVOTED),
}
_HAS = r"(?:has|have)(?:\s+(?:long|always|still|openly|deeply|greatly))*\s+"
# (a word or two the text may put between the holder and the verb, or the verb and the people)
_LINK = r"(?:\s+(?:openly|deeply|still|always|greatly|all|most|the|its|their))*\s+"
_SENTENCES = re.compile(r"(?<=[.!?])\s+|\n+")
# A sentence that asks or supposes states nothing ("Does X trust elves?", "If X trusts elves, …").
_NOT_A_STATEMENT = re.compile(
    r"^\W*(?:if|does|do|did|should|will|would|could|can|unless|whether|perhaps|maybe)\b", re.I
)
# The holder starts its sentence or clause: "Travelers to X fear orcs" and "Nobody believes X hates
# elves" are someone else's feeling or a doubted one (the reviewer's cases on #4229).
_CLAUSE_START = r"(?:^\W*|[,;:]\s*|\band\s+|\bbut\s+)"
# "The people of X distrust …", "Most folk in X distrust …": the holder's own people, said the long
# way (the fixture on #3545; "most folk in", Supervisor's ruling C1 on #4982)
_PEOPLE_OF = r"(?:(?:the|most)\s+(?:people|folk)\s+(?:of|in)\s+)?"
# A list after the verb ("welcomes halflings and gnomes"): each further people is read too, but
# only one a clause ends after, so "elves and dwarves trust nobody" gives no feeling toward dwarves
_MORE = r"(?:\s*,\s*(?:and\s+|or\s+)?|\s+(?:and|or)\s+)"
_ENDS = r"(?=\s*(?:[.,;:!]|\band\b|\bor\b|$))"
# After an adjective or a singular, the people is whole only before "folk", "people" or "kind", or
# at the end of a clause: "dwarven ale", "human traders" and "the elven dead" are no people.
_WHOLE_AFTER = r"(?:\s+(?:folk|people|kind)\b|(?=\s*(?:[.,;:!]|\band\b|\bor\b|$)))"


@dataclass(frozen=True, slots=True)
class Found:
    """One bias read: who holds it (an entry id), toward whom (a lineage key or a culture id, one
    of the two), and the step."""

    holder_id: str
    step: str
    lineage: str = ""
    culture_id: str = ""


def _people_words() -> tuple[dict[str, str], dict[str, str]]:
    """Each way the text may name a lineage, to its key: plurals ("dwarves", "elves") stand alone;
    an adjective or a singular ("dwarven", "dwarf") only before "folk" or at a clause's end."""
    plural: dict[str, str] = {}
    other: dict[str, str] = {}
    for key, (adjective, plurals) in place_peoples.LINEAGES.items():
        plural.setdefault(plurals.casefold(), key)
        plural.setdefault(key + "s", key)
        for w in (key, adjective):
            if w.casefold() not in plural:
                other.setdefault(w.casefold(), key)
    return plural, other


_PLURAL, _SINGLE = _people_words()


def _alternation(words: Mapping[str, object]) -> str:
    return "|".join(sorted((re.escape(w) for w in words), key=len, reverse=True))


def read(text: str, holders: Mapping[str, str], cultures: Mapping[str, str]) -> list[Found]:
    """The biases the text states. `holders`: each place, group or people's name the campaign has,
    to its entry id (a name two entries share is left out by the caller). `cultures`: each named
    culture's name, to its id. One per holder and target, the first the text gives. Only a holder
    the text names at all is tried, each with its pattern built once, so a long upload with many
    names costs one scan per name it holds (the reviewer's timing on #4229)."""
    out: dict[tuple[str, str, str], Found] = {}
    names = {n.casefold(): ("culture", i) for n, i in cultures.items()}
    if not holders:
        return []
    verb = rf"{_alternation(VERBS)}|{_HAS}(?P<perfect>{_alternation(_PERFECT)})"
    whole = _alternation({**_PLURAL, **names})  # (a culture's name is whole wherever it stands)
    single = _alternation(_SINGLE)
    target = rf"(?:(?P<whole>{whole})(?:\s+folk)?|(?P<single>{single}){_WHOLE_AFTER})(?!\w)"
    more = re.compile(
        rf"{_MORE}(?:the\s+)?(?:(?P<whole>{whole})(?:\s+folk)?|(?P<single>{single})){_ENDS}",
        re.I,
    )
    present = [
        (name, holder_id)
        for name, holder_id in holders.items()
        if re.search(rf"(?<!\w){re.escape(name)}(?!\w)", text, re.I)
    ]
    if not present:
        return []
    patterns = [
        (
            re.compile(
                rf"{_CLAUSE_START}{_PEOPLE_OF}{re.escape(name)}(?:'s people|'s folk)?{_LINK}"
                rf"(?P<verb>{verb})"
                rf"{_LINK}{target}",
                re.I,
            ),
            holder_id,
        )
        for name, holder_id in present
    ]
    for sentence in _SENTENCES.split(text):
        if sentence.rstrip().endswith("?") or _NOT_A_STATEMENT.search(sentence):
            continue
        for pattern, holder_id in patterns:
            for m in pattern.finditer(sentence):
                step = (
                    _PERFECT[m["perfect"].casefold()]
                    if m["perfect"]
                    else VERBS[m["verb"].casefold()]
                )
                words = [m["whole"] or m["single"]]
                at = m.end()
                while (n := more.match(sentence, at)) is not None:  # (the rest of a list)
                    words.append(n["whole"] or n["single"])
                    at = n.end()
                for said in words:
                    word = said.casefold()
                    kind, ref = names.get(word) or ("lineage", _PLURAL.get(word) or _SINGLE[word])
                    key = (holder_id, kind, ref)
                    if key not in out:
                        out[key] = Found(
                            holder_id,
                            step,
                            lineage=ref if kind == "lineage" else "",
                            culture_id=ref if kind == "culture" else "",
                        )
    return list(out.values())
