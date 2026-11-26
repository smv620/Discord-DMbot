"""The sample campaign's names (#4538): "test of the frostmaiden (sample N)", one for each deploy.

The sample tools may touch a campaign only when its name is exactly one of these (or the older
unnumbered name, for comparison runs), so nothing else on the server can ever be changed by them.
One module so every guard (`sample_run`, `accept_all`, the key checks) uses the same rule. Pure: no
database and no Discord here.

A deploy's label is its number, 1 to 9999, with an optional lowercase letter (#5057): "40" and
"40b" are two deploys, so a re-run of 40b replaces only 40b and never touches 40 or 40a.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Collection, Iterable
from dataclasses import dataclass

OLD_NAME = "test of the frostmaiden (sample)"  # before #4538: still accepted, counted as oldest
PREFIX = "test of the frostmaiden (sample "
# "(sample 31)" is the deploy's one campaign; "(sample 31b.2)" is run 2 of deploy 31b (#4551,
# #5057); "(sample 31b.2 no capitals)" is run 2 of that deploy on another version of the session
# (docs/design/session_names.md, section 8: typed, no capitals, spoken, and the dev-only respelled
# one, #5133; each its own series).
_NAMED = re.compile(
    r"^test of the frostmaiden \(sample (?P<n>[0-9]{1,4})(?P<l>[a-z])?(?:\.(?P<k>[1-9][0-9]?))?"
    r"(?: (?P<v>no capitals|spoken|respelled))?\)$"
)
_LABEL = re.compile(r"^(?P<n>[1-9][0-9]{0,3})(?P<l>[a-z])?$")  # no leading zero: "40", not "040"
RUN_MAX = 99
KEEP = 3  # the newest campaigns kept alive, so the owner can compare their story graphs
DEPLOY_MAX = 9999
SAY_WHICH = "Say which deploy this is: --deploy N"
# The session's versions (`sample_run --version`), and the words each adds to a campaign's name. The
# typed one, the sample as it always was, adds none, so every name made before stays the same.
TYPED = "typed"
# "respelled" (dev only, #5133): the typed sample with a few key names heard several ways, from a
# fixed table (`sample_respell`), so grouping can be scored before the spoken recording exists.
VERSIONS = {TYPED: "", "no-capitals": "no capitals", "spoken": "spoken", "respelled": "respelled"}
BAD_LABEL = (
    "the deploy is a number from 1 to 9999, with one lowercase letter after it if you like: "
    "40 or 40b"
)


@dataclass(frozen=True, order=True, slots=True)
class Label:
    """One deploy: its number (1 to 9999) and an optional lowercase letter. Ordered by number, then
    letter, so 40 < 40a < 40b < 41 (the empty letter sorts first)."""

    number: int
    letter: str = ""

    def __str__(self) -> str:
        return f"{self.number}{self.letter}"


def parse_label(text: str) -> Label:
    """A label typed on the command line: "40" or "40b". Anything else is refused with one plain
    message, so no capital, space, sign or second letter gets through."""
    found = _LABEL.fullmatch(text)
    if found is None or not 1 <= int(found["n"]) <= DEPLOY_MAX:
        raise ValueError(BAD_LABEL)
    return Label(int(found["n"]), found["l"] or "")


def as_label(deploy: Label | int) -> Label:
    """A plain number is the label with no letter (what the tests and older callers pass)."""
    if isinstance(deploy, Label):
        return deploy
    if isinstance(deploy, bool) or not isinstance(deploy, int):
        raise ValueError(f"the deploy is a whole number from 1 to {DEPLOY_MAX}")
    return Label(deploy)


def name_for(deploy: Label | int, run: int | None = None, version: str = TYPED) -> str:
    """The campaign's name for deploy N (a whole number, 1 to 9999, with an optional letter), or for
    run k of it when the deploy's runs go side by side ("sample 31b.2", k from 1 to 99), on one
    version of the session ("sample 31b.2 no capitals"; the typed one adds nothing)."""
    label = as_label(deploy)
    if not 1 <= label.number <= DEPLOY_MAX:
        raise ValueError(f"the deploy is a whole number from 1 to {DEPLOY_MAX}")
    if version not in VERSIONS:
        raise ValueError(f"the version is one of {', '.join(VERSIONS)}")
    words = f" {VERSIONS[version]}" if VERSIONS[version] else ""
    if run is None:
        return f"{PREFIX}{label}{words})"
    if not isinstance(run, int) or isinstance(run, bool) or not 1 <= run <= RUN_MAX:
        raise ValueError(f"the run is a whole number from 1 to {RUN_MAX}")
    return f"{PREFIX}{label}.{run}{words})"


def version_of(name: str) -> str | None:
    """The session's version a numbered sample name was made for ("typed" when it says none);
    None for any other name, the old unnumbered one included."""
    found = _NAMED.fullmatch(name)
    if not found:
        return None
    words = found["v"] or ""
    return next(key for key, said in VERSIONS.items() if said == words)


def deploy_of(name: str) -> Label | None:
    """The label for "…(sample 40b)" and "…(sample 40b.2)"; None for any other name, the old
    unnumbered one included."""
    found = _NAMED.fullmatch(name)
    return Label(int(found["n"]), found["l"] or "") if found else None


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
    """What a run keeps and deletes: campaign ids, and the deploy labels for the counts line
    (the old unnumbered campaigns have no label)."""

    keep: tuple[str, ...]
    delete: tuple[str, ...]
    kept_numbers: tuple[Label, ...]
    deleted_numbers: tuple[Label, ...]
    deleted_old: int  # how many unnumbered ones go (always, as they count as oldest)
    not_deleted: int = 0  # candidates the guard refused: left alone, counted
    deploy: Label = Label(0)  # the deploy being made now
    replaced: bool = False  # this deploy's own campaign was there and is made again
    # Older kept deploys dropped, with all their runs, so the new runs have paid slots (#4551):
    # their campaign names, oldest first.
    slot_dropped: tuple[str, ...] = ()


def plan_keep(
    existing: Iterable[tuple[str, str]],
    deploy: Label | int,
    protected: Collection[str] = (),
    version: str = TYPED,
) -> Kept:
    """From (campaign id, name) of this DM's sample campaigns, and the deploy being made now: the
    campaign for `deploy` itself is deleted first (a re-run makes it again, never two of one
    label), then the newest `KEEP - 1` other labels stay beside the new one and the rest go. An old
    unnumbered name counts as older than any label. Labels are compared as wholes: 40b's campaigns
    are never 40's. `protected`: campaigns the guard refuses to delete: they are left alone and
    counted, never reported as deleted. Names that are not samples are not here: callers pass only
    `is_sample` names. `version`: the session's version being made; this deploy's campaigns of
    another version are kept (their own series), and a deploy counts once toward `KEEP` whatever
    its versions."""
    current = as_label(deploy)
    numbered: list[tuple[Label, str]] = []
    old: list[str] = []
    other_version: list[str] = []  # this deploy's campaigns of another version: kept, not re-made
    for campaign_id, name in existing:
        label = deploy_of(name)
        if label is None:
            old.append(campaign_id)
        elif label == current and version_of(name) != version:
            other_version.append(campaign_id)
        else:
            numbered.append((label, campaign_id))
    same = [cid for lab, cid in numbered if lab == current]  # (every run of this label, if re-run)
    # A label counts once however many runs it has: the newest labels stay with all their runs.
    labels = sorted({lab for lab, _ in numbered if lab != current}, reverse=True)
    stay_labels = set(labels[: KEEP - 1])
    stay = [(lab, cid) for lab, cid in numbered if lab in stay_labels]
    others = [(lab, cid) for lab, cid in numbered if lab != current and lab not in stay_labels]
    go = [(lab, cid) for lab, cid in others if cid not in protected]
    old_go = [cid for cid in old if cid not in protected]
    refused = sum(1 for _, cid in others if cid in protected) + sum(
        1 for cid in old if cid in protected
    )
    return Kept(
        keep=(*other_version, *(cid for _, cid in stay)),
        delete=tuple([*same, *old_go, *(cid for _, cid in go)]),
        kept_numbers=tuple(sorted(stay_labels | {current})),
        deleted_numbers=tuple(sorted({lab for lab, _ in go})),
        deleted_old=len(old_go),
        not_deleted=refused,
        deploy=current,
        replaced=bool(same),
    )


def counts_line(kept: Kept) -> str:
    """ "Sample campaigns kept: 31, 31b, 32. Deleted: 30." Labels only."""
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
    many runs there is room for (`need`, or fewer; 0 means none). The deploy being made is never
    a candidate: its campaigns of another version of the session are kept whatever the slots
    (docs/design/session_names.md, section 8), and "the newest kept deploy" is the newest other
    one, as before the versions."""
    if free is None:
        return (), (), need
    room = free + len(kept.delete)
    staying = set(kept.keep)
    by_deploy: dict[Label, list[tuple[str, str]]] = {}
    for cid, name in existing:
        label = deploy_of(name)
        if label is not None and label != kept.deploy and cid in staying:
            by_deploy.setdefault(label, []).append((cid, name))
    order = sorted(by_deploy)  # oldest first
    extra_ids: list[str] = []
    extra_names: list[str] = []
    for label in order[:-1]:  # (the newest kept deploy is never touched)
        if room >= need:
            break
        runs = by_deploy[label]
        if not all(deletable(cid) for cid, _ in runs):
            continue
        extra_ids += [cid for cid, _ in runs]
        extra_names += [name for _, name in sorted(runs, key=lambda r: run_of(r[1]) or 0)]
        room += len(runs)
    return tuple(extra_ids), tuple(extra_names), max(0, min(need, room))


def slot_line(names: Iterable[str]) -> str:
    """ "Not kept for lack of slots: 29.2 to 29.5, 28." from the dropped campaigns' names."""
    runs: dict[Label, list[int]] = {}
    for name in names:
        label = deploy_of(name)
        if label is not None:
            runs.setdefault(label, []).append(run_of(name) or 0)
    parts = []
    for label in sorted(runs, reverse=True):
        ks = sorted(runs[label])
        if ks == [0]:
            parts.append(str(label))
        elif len(ks) == 1:
            parts.append(f"{label}.{ks[0]}")
        else:
            parts.append(f"{label}.{ks[0]}\u2013{label}.{ks[-1]}")
    return "Not kept for lack of slots: " + ", ".join(parts) + "." if parts else ""
