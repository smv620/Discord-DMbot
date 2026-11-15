"""The sample campaign's names (#4538): "test of the frostmaiden (sample N)", one for each deploy.

The sample tools may touch a campaign only when its name is exactly one of these (or the older
unnumbered name, for comparison runs), so nothing else on the server can ever be changed by them.
One module so every guard (`sample_run`, `accept_all`, the key checks) uses the same rule. Pure: no
database and no Discord here.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

OLD_NAME = "test of the frostmaiden (sample)"  # before #4538: still accepted, counted as oldest
PREFIX = "test of the frostmaiden (sample "
_NAMED = re.compile(r"^test of the frostmaiden \(sample (?P<n>[0-9]{1,4})\)$")
KEEP = 3  # the newest campaigns kept alive, so the owner can compare their story graphs
DEPLOY_MAX = 9999
SAY_WHICH = "Say which deploy this is: --deploy N"


def name_for(deploy: int) -> str:
    """The campaign's name for deploy N (a whole number, 1 to 9999)."""
    if not isinstance(deploy, int) or isinstance(deploy, bool) or not 1 <= deploy <= DEPLOY_MAX:
        raise ValueError(f"the deploy is a whole number from 1 to {DEPLOY_MAX}")
    return f"{PREFIX}{deploy})"


def deploy_of(name: str) -> int | None:
    """N for "…(sample N)"; None for any other name, the old unnumbered one included."""
    found = _NAMED.fullmatch(name)
    return int(found["n"]) if found else None


def is_sample(name: str) -> bool:
    """Exactly a sample name, numbered or the old one: never a look-alike (a different capital,
    a suffix, a space)."""
    return name == OLD_NAME or _NAMED.fullmatch(name) is not None


@dataclass(frozen=True, slots=True)
class Kept:
    """What a run keeps and deletes: campaign ids, and the deploy numbers for the counts line
    (the old unnumbered campaigns have no number)."""

    keep: tuple[str, ...]
    delete: tuple[str, ...]
    kept_numbers: tuple[int, ...]
    deleted_numbers: tuple[int, ...]
    deleted_old: int  # how many unnumbered ones go (always, as they count as oldest)


def plan_keep(existing: Iterable[tuple[str, str]], deploy: int) -> Kept:
    """From (campaign id, name) of this DM's sample campaigns, and the deploy being made now: the
    campaign for `deploy` itself is deleted first (a re-run makes it again, never two of one
    number), then the newest `KEEP - 1` others stay beside the new one and the rest go. An old
    unnumbered name counts as older than any number. Names that are not samples are not here:
    callers pass only `is_sample` names."""
    numbered: list[tuple[int, str]] = []
    old: list[str] = []
    for campaign_id, name in existing:
        n = deploy_of(name)
        if n is None:
            old.append(campaign_id)
        else:
            numbered.append((n, campaign_id))
    same = [cid for n, cid in numbered if n == deploy]
    others = sorted(((n, cid) for n, cid in numbered if n != deploy), reverse=True)
    stay = others[: KEEP - 1]
    go = others[KEEP - 1 :]
    return Kept(
        keep=tuple(cid for _, cid in stay),
        delete=tuple([*same, *old, *(cid for _, cid in go)]),
        kept_numbers=tuple(sorted([n for n, _ in stay] + [deploy])),
        deleted_numbers=tuple(sorted([deploy] * bool(same) + [n for n, _ in go])),
        deleted_old=len(old),
    )


def counts_line(kept: Kept) -> str:
    """ "Sample campaigns kept: 31, 32, 33. Deleted: 30." Numbers only."""
    keep = ", ".join(str(n) for n in kept.kept_numbers)
    gone = [str(n) for n in kept.deleted_numbers]
    if kept.deleted_old:
        gone.append(f"{kept.deleted_old} unnumbered")
    return f"Sample campaigns kept: {keep}. Deleted: {', '.join(gone) if gone else 'none'}."
