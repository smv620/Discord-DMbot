"""The website's recap check, as the bot's (#2404): a name the players haven't been shown yet
counts as secret for a recap unless the table said it in that session.

The database door that lists the recap's names counts every such name as secret. The door
`dmbot_recap_held_names` says which names those are, which stay secret whatever was said (a
secret spelling, a clue's names), and the session's table lines that could contain one. This reads
those lines exactly as the bot does (`recaps.inputs.recap_name_sets`: whole names, compared as
names are everywhere), and moves a name the table said from the secret list to the allowed list.
Never the other way.
"""

from __future__ import annotations

from typing import Any

from dmbot.memory.models import name_key


def apply_held(names: dict[str, Any], held: dict[str, Any] | None) -> dict[str, Any]:
    """`names` as the check door gave them, with the names the table said in the session moved
    from `secret` to `allowed`. `held` None (the door refused, or older): `names` unchanged."""
    if not held:
        return names
    said = " ".join(name_key(str(line)) for line in held["said"])
    hard = {str(t) for t in held["hard"]}
    freed = [
        str(t)
        for t in held["held"]
        if str(t) not in hard and name_key(str(t)) and f" {name_key(str(t))} " in f" {said} "
    ]
    if not freed:
        return names
    return {
        **names,
        "secret": [t for t in names["secret"] if t not in freed],
        "allowed": sorted({*names["allowed"], *freed}),
    }
