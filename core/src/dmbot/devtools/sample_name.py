"""The sample campaign's names (#4538): "test of the frostmaiden (sample N)", one for each deploy.

The sample tools may touch a campaign only when its name is exactly one of these (or the older
unnumbered name, for comparison runs), so nothing else on the server can ever be changed by them.
One module so every guard (`sample_run`, `accept_all`, the key checks) uses the same rule. Pure: no
database and no Discord here.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Collection, Iterable
from dataclasses import dataclass

OLD_NAME = "test of the frostmaiden (sample)"  # before #4538: still accepted, counted as oldest
PREFIX = "test of the frostmaiden (sample "
# "(sample 31)" is the deploy's one campaign; "(sample 31.2)" is run 2 of a parallel deploy (#4551)
_NAMED = re.compile(
    r"^test of the frostmaiden \(sample (?P<n>[0-9]{1,4})(?:\.(?P<k>[1-9][0-9]?))?\)$"
)
RUN_MAX = 99
KEEP = 3  # the newest campaigns kept alive, so the owner can compare their story graphs
DEPLOY_MAX = 9999
SAY_WHICH = "Say which deploy this is: --deploy N"


def name_for(deploy: int, run: int | None = None) -> str:
    """The campaign's name for deploy N (a whole number, 1 to 9999), or for run k of it when the
    deploy's runs go side by side ("sample 31.2", k from 1 to 99)."""
    if not isinstance(deploy, int) or isinstance(deploy, bool) or not 1 <= deploy <= DEPLOY_MAX:
        raise ValueError(f"the deploy is a whole number from 1 to {DEPLOY_MAX}")
    if run is None:
        return f"{PREFIX}{deploy})"
    if not isinstance(run, int) or isinstance(run, bool) or not 1 <= run <= RUN_MAX:
        raise ValueError(f"the run is a whole number from 1 to {RUN_MAX}")
    return f"{PREFIX}{deploy}.{run})"


def deploy_of(name: str) -> int | None:
    """N for "…(sample N)" and "…(sample N.k)"; None for any other name, the old unnumbered one
    included."""
    found = _NAMED.fullmatch(name)
    return int(found["n"]) if found else None


def run_of(name: str) -> int | None:
    """k for "…(sample N.k)"; None for the plain deploy name and any other name."""
    found = _NAMED.fullmatch(name)
    return int(found["k"]) if found and found["k"] else None


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
    not_deleted: int = 0  # candidates the guard refused: left alone, counted
    deploy: int = 0  # the deploy being made now
    replaced: bool = False  # this deploy's own campaign was there and is made again
    # Older kept deploys dropped, with all their runs, so the new runs have paid slots (#4551):
    # their campaign names, oldest first.
    slot_dropped: tuple[str, ...] = ()


def plan_keep(
    existing: Iterable[tuple[str, str]], deploy: int, protected: Collection[str] = ()
) -> Kept:
    """From (campaign id, name) of this DM's sample campaigns, and the deploy being made now: the
    campaign for `deploy` itself is deleted first (a re-run makes it again, never two of one
    number), then the newest `KEEP - 1` others stay beside the new one and the rest go. An old
    unnumbered name counts as older than any number. `protected`: campaigns the guard refuses to
    delete: they are left alone and counted, never reported as deleted. Names that are not
    samples are not here: callers pass only `is_sample` names."""
    numbered: list[tuple[int, str]] = []
    old: list[str] = []
    for campaign_id, name in existing:
        n = deploy_of(name)
        if n is None:
            old.append(campaign_id)
        else:
            numbered.append((n, campaign_id))
    same = [cid for n, cid in numbered if n == deploy]  # (every run of this deploy, if re-run)
    # A deploy counts once however many runs it has: the newest deploys stay with all their runs.
    deploys = sorted({n for n, _ in numbered if n != deploy}, reverse=True)
    stay_deploys = set(deploys[: KEEP - 1])
    stay = [(n, cid) for n, cid in numbered if n in stay_deploys]
    others = [(n, cid) for n, cid in numbered if n != deploy and n not in stay_deploys]
    go = [(n, cid) for n, cid in others if cid not in protected]
    old_go = [cid for cid in old if cid not in protected]
    refused = sum(1 for _, cid in others if cid in protected) + sum(
        1 for cid in old if cid in protected
    )
    return Kept(
        keep=tuple(cid for _, cid in stay),
        delete=tuple([*same, *old_go, *(cid for _, cid in go)]),
        kept_numbers=tuple(sorted(stay_deploys | {deploy})),
        deleted_numbers=tuple(sorted({n for n, _ in go})),
        deleted_old=len(old_go),
        not_deleted=refused,
        deploy=deploy,
        replaced=bool(same),
    )


def counts_line(kept: Kept) -> str:
    """ "Sample campaigns kept: 31, 32, 33. Deleted: 30." Numbers only."""
    keep = ", ".join(str(n) for n in kept.kept_numbers)
    gone = [str(n) for n in kept.deleted_numbers]
    if kept.deleted_old:
        gone.append(f"{kept.deleted_old} unnumbered")
    replaced = f" Replaced: {kept.deploy}." if kept.replaced else ""
    refused = f" Not deleted, not the tool's: {kept.not_deleted}." if kept.not_deleted else ""
    deleted = ", ".join(gone) if gone else "none"
    return f"Sample campaigns kept: {keep}. Deleted: {deleted}.{replaced}{refused}"


def slot_drops(
    existing: Iterable[tuple[str, str]],
    kept: Kept,
    need: int,
    free: int | None,
    deletable: Callable[[str], bool] = lambda _cid: True,
) -> tuple[tuple[str, ...], tuple[str, ...], int]:
    """The paid slots the new runs need, counted before anything is deleted or made (#4551): `free`
    is the DM's free slots now (None: no limit), `kept.delete` is already going. While the slots
    after those deletions are fewer than `need`, the oldest kept deploy goes with all its runs,
    never the newest kept deploy (a deploy any of whose campaigns `deletable` refuses is left
    alone). Returns the extra campaign ids to delete, their names (oldest deploy first), and how
    many runs there is room for (`need`, or fewer; 0 means none)."""
    if free is None:
        return (), (), need
    room = free + len(kept.delete)
    staying = set(kept.keep)
    by_deploy: dict[int, list[tuple[str, str]]] = {}
    for cid, name in existing:
        n = deploy_of(name)
        if n is not None and cid in staying:
            by_deploy.setdefault(n, []).append((cid, name))
    order = sorted(by_deploy)  # oldest first
    extra_ids: list[str] = []
    extra_names: list[str] = []
    for n in order[:-1]:  # (the newest kept deploy is never touched)
        if room >= need:
            break
        runs = by_deploy[n]
        if not all(deletable(cid) for cid, _ in runs):
            continue
        extra_ids += [cid for cid, _ in runs]
        extra_names += [name for _, name in sorted(runs, key=lambda r: run_of(r[1]) or 0)]
        room += len(runs)
    return tuple(extra_ids), tuple(extra_names), max(0, min(need, room))


def slot_line(names: Iterable[str]) -> str:
    """ "Not kept for lack of slots: 29.2 to 29.5, 28." from the dropped campaigns' names."""
    runs: dict[int, list[int]] = {}
    for name in names:
        n = deploy_of(name)
        if n is not None:
            runs.setdefault(n, []).append(run_of(name) or 0)
    parts = []
    for n in sorted(runs, reverse=True):
        ks = sorted(runs[n])
        if ks == [0]:
            parts.append(str(n))
        elif len(ks) == 1:
            parts.append(f"{n}.{ks[0]}")
        else:
            parts.append(f"{n}.{ks[0]}\u2013{n}.{ks[-1]}")
    return "Not kept for lack of slots: " + ", ".join(parts) + "." if parts else ""
