"""One-line, counts-only pointers the DM Bot channel gets for posts whose words must stay private.

The DM Bot channel is read by the campaign's people (#3874), so it never carries a name, a secret or
a file name. When a report has those (the secrets said at the table, the names an import fixed),
the report stays where only the DM sees it and this line tells the DM, in numbers, that it is
there.
"""

from __future__ import annotations


def _count(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def secrets_pointer(secrets: int, names: int, checks: int) -> str:
    """After a session: the summary of secrets said at the table is in the DM's private message
    (or on the DM screen while it is private). Numbers only."""
    parts = []
    if secrets:
        parts.append(_count(secrets, "secret said at the table", "secrets said at the table"))
    if names:
        parts.append(_count(names, "new name", "new names"))
    if checks:
        parts.append(_count(checks, "thing to check", "things to check"))
    what = ", ".join(parts) or "something to look at"
    return f"🤫 After the session: {what}. The DM has the details privately."


def names_fixed_pointer(fixed: int, unsure: int, new: int) -> str:
    """After an import: how many names the name pass fixed, and how many it left to the DM. The
    names, the spellings and the Undo buttons stay on the DM screen."""
    parts = [_count(fixed, "name fixed", "names fixed")]
    if unsure:
        parts.append(f"{unsure} not sure")
    if new:
        parts.append(f"{new} new")
    return f"📝 Your imported sessions: {', '.join(parts)}. The DM has the report with Undo."
