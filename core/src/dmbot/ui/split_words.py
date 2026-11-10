"""What the DM is told when a session's new name was said only while the party was split
(#4037, the names half of #2996 slice 2a): never a silent hide. The words follow #4036's for
connections (the ux-critic's), so the two read alike."""

from __future__ import annotations

NAME_GROUP = "👁️ Only the characters who were there know this name: the party was split."
NAME_UNKNOWN = (
    "🙈 Kept hidden from your players: the party was split, and DMbot can't tell which group "
    "heard this name. To choose who knows: open the name, then 👁️ Who knows this?"
)


def for_one(split: str) -> str:
    """The line for one name a confirm hid ("group" or "unknown"), or "" when it didn't."""
    return {"group": NAME_GROUP, "unknown": NAME_UNKNOWN}.get(split, "")


def for_many(group: int, unknown: int) -> str:
    """The lines for a batch of names (a names list, an upload's Add all, an import)."""
    parts = []
    if group:
        parts.append(
            f"👁️ {group} name{'' if group == 1 else 's'} {'was' if group == 1 else 'were'} said "
            "while the party was split: only the characters who were there know "
            f"{'it' if group == 1 else 'them'}."
        )
    if unknown:
        parts.append(
            f"🙈 {unknown} name{'' if unknown == 1 else 's'} {'is' if unknown == 1 else 'are'} "
            "kept hidden from your players: the party was split, and DMbot can't tell which group "
            f"heard {'it' if unknown == 1 else 'them'}. To choose who knows: open the name, then "
            "👁️ Who knows this?"
        )
    return "\n".join(parts)
