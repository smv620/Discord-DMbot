"""Who speaks for a character (#2562, docs/PLAN.md, "Characters are NPCs or PCs, and who speaks for
them"): a PC's own thoughts, feelings, wants and choices come only from its player; an NPC's only
from the DM. Pure: no database, no Discord.

`voice` says what a claim about such an inner state is, from who said it:
- `own`: it may go to the DM as the character's own (a PC's, said by its player; an NPC's, said by
  the DM). The DM still has to say yes.
- `effect`: the DM said it of a PC, so it is something that happened to the PC (told by the DM),
  not the PC's own feeling or choice.
- `party`: a player said it of someone else's character, so it is what that player said or
  believes (The party), never the character's own and never The truth.
A relationship that is not about an inner state, or whose subject is not a character, is `own`: the
rule changes nothing for `knows`, `owns`, `member_of`, deeds and the like (what came of a PC's
action stays the DM's). A group's attitude stays the DM's.
"""

from __future__ import annotations

from collections.abc import Collection

from dmbot.memory.models import NPC, PLAYER_CHARACTER
from dmbot.memory.ontology import CORE_PREDICATES

OWN, EFFECT, PARTY = "own", "effect", "party"

# The relationships that carry a character's own attitude, loyalty or vow (#2562): the ones whose
# definition has the `inner_state` flag (#2619), so the list lives with the definitions.
INNER_STATE = frozenset(t.key for t in CORE_PREDICATES if t.inner_state)

PARTY_REASON = "A player said that about someone else's character, so it isn't kept as true"
EFFECT_NOTE = "Told by the DM as something that happened to {name}, not as {name}'s own choice."


def voice(
    subject_role: str | None,
    predicate: str,
    how: str,
    speakers: Collection[int | None],
    plays: int | None,
    dms: Collection[int] = (),
) -> str:
    """`own`, `effect` or `party` for a claim about a character (see the module).
    `subject_role`: the character's role (a PC, an NPC, or None: not set yet, read as an NPC);
    `speakers`: who said the cited lines; `plays`: the PC's player (None: no player yet);
    `dms`: the campaign's DMs. `how` is the claim's way of saying it ("said", "heard", ...): a plan
    is dropped before this is asked."""
    if predicate not in INNER_STATE or how == "plan":
        return OWN
    said = {s for s in speakers if s is not None}
    if not said:
        return PARTY
    if subject_role == PLAYER_CHARACTER:
        if plays is not None and said == {plays}:
            return OWN
        if said <= set(dms):
            return EFFECT
        return PARTY
    # An NPC (or a character with no role yet): only the DM speaks for it.
    if subject_role in (NPC, None) and said <= set(dms):
        return OWN
    return PARTY
