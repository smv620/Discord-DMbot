"""The pure parts of the sample run (#3547, #3545): the one campaign name it may touch, the guard
that keeps it off a real campaign, the test accounts, the cost line and the report. No database,
no AI and no Discord here, so every rule is tested on its own.

The report is counts and times only (CLAUDE.md, "Repository is public"): it goes on #3545, so it
holds no name, no word of the lore or the session and no id of a person.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field

from dmbot.devtools.graph_key_check import SAMPLE_CAMPAIGN

# The only campaign this tool may delete or make: the one `graph_key_check` scores. It is
# deliberately long and odd (a real table would not call its campaign this), and `can_replace`
# checks the rest.
NAME = SAMPLE_CAMPAIGN

# (a label is the speaker as the file writes it: "ist", or Craig's "Mira (mira_v)")
# The four player characters of the sample (the README's table): the character's full name, and the
# `--players` label of the test account that plays it. Beside the campaign name, since both belong
# to the one sample.
PLAYER_CHARACTERS: tuple[tuple[str, str], ...] = (
    ("Cerric Vanya'elen", "ist"),
    ("Belleros Stormgrace", "jac"),
    ("Okthandom Korran", "eri"),
    ("Claude Ambere", "owe"),
)

# The sample's players are NOT on Discord (#3690): the session file names them `player_jac` and so
# on, and the DM's lines "DM". The older labels stay as aliases so an older session file still runs.
# (short key used by `PLAYER_CHARACTERS`, the labels a file may use for that player)
OFF_DISCORD: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("ist", ("player_ist", "user_ist", "ist")),
    ("jac", ("player_jac", "user_jac", "jac")),
    ("eri", ("player_eri", "user_eri", "eri")),
    ("owe", ("player_owe", "user_owe", "owe")),
)
DM_LABELS = ("dm",)

_LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9_ ()#.\-]{0,79}")
MAX_PLAYERS = 12


class SampleError(Exception):
    """A refusal the tool says in one plain line and stops on; nothing was changed."""


def parse_players(text: str) -> dict[str, int]:
    """`ist=111,jac=222`: the speaker label in the session file, then the test account. Whole
    positive numbers only, one account for each label and each label once."""
    out: dict[str, int] = {}
    for part in text.split(","):
        label, sep, raw = part.strip().partition("=")
        label, raw = label.strip(), raw.strip()
        if not sep or not _LABEL.fullmatch(label) or not raw.isascii() or not raw.isdigit():
            raise SampleError("--players is label=ACCOUNT pairs, such as ist=111,jac=222")
        who = int(raw)
        if who <= 0 or who > 2**63 - 1:
            raise SampleError("a player's account must be a positive Discord number")
        if label in out:
            raise SampleError(f"the label {label} is given twice")
        out[label] = who
    if not out or len(out) > MAX_PLAYERS:
        raise SampleError(f"give between 1 and {MAX_PLAYERS} players")
    if len(set(out.values())) != len(out):
        raise SampleError("two labels cannot be the same account")
    return out


# The run's steps, in order (owner, 2026-11-06, #3554): the lore is scored exactly as the reader
# left it, and only then are the four player characters corrected, before the session import (whose
# speakers and recap need them). `steps.run_steps` refuses to run if the actions do not match this.
STEPS: tuple[str, ...] = (
    "lore read",
    "player characters found by the read",
    "accept all (lore)",
    "backup and score (lore)",
    "players not on Discord",
    "player characters corrected",
    "session import",
    "accept all (session)",
    "backup and score (both)",
    "recap (no audio)",
)
# The same run since #3900, when it adds what DMbot is sure of and then asks the careful model about
# the rest, scoring the graph after each: the three columns (sure, AI-decided, left) never mix.
STEPS_DECIDE: tuple[str, ...] = (
    "lore read",
    "player characters found by the read",
    "sure (lore)",
    "backup and score (lore, sure)",
    "AI decides (lore)",
    "backup and score (lore, sure + AI)",
    "players not on Discord",
    "player characters corrected",
    "session import",
    "sure (session)",
    "backup and score (both, sure)",
    "AI decides (session)",
    "backup and score (both, sure + AI)",
    "recap (no audio)",
)


def off_discord_roles(
    labels: Collection[str], display: Mapping[str, str]
) -> tuple[dict[str, str], str | None]:
    """Which of a session file's speakers is which (#3690): `({label: short key}, the DM's label)`.
    `display` is each label's name as the import reads it (the part before any "(username)").
    A speaker that is neither one of the four players nor the DM is a plain refusal."""
    keys: dict[str, str] = {}
    dm_label: str | None = None
    for label in labels:
        shown = display[label].strip().casefold()
        if shown in DM_LABELS:
            dm_label = label
            continue
        for key, accepted in OFF_DISCORD:
            if shown in accepted:
                keys[label] = key
                break
        else:
            raise SampleError(
                "a speaker in the session file is not player_jac, player_ist, player_owe, "
                "player_eri or DM"
            )
    return keys, dm_label


def require_character_players(players: Mapping[str, int]) -> None:
    """The run sets the four player characters itself, so `--players` must name their accounts."""
    missing = [label for _name, label in PLAYER_CHARACTERS if label not in players]
    if missing:
        raise SampleError(
            "--players must give the four test accounts: "
            + ", ".join(f"{label}=ID" for _n, label in PLAYER_CHARACTERS)
        )


@dataclass(frozen=True, slots=True)
class Existing:
    """What the tool needs to know about a campaign that already has the sample's name."""

    campaign_id: str
    owner_user_id: int | None
    dm_user_ids: frozenset[int]
    sessions: int  # how many sessions it holds
    imported: int  # ...of which come from an import (a `session_imports` row)


def can_replace(existing: Existing, dm: int) -> str | None:
    """Why this campaign may NOT be deleted by the tool, or None. It must be the DM's own, and
    hold nothing but imported sessions: a campaign someone played in is a real one."""
    if existing.owner_user_id != dm or dm not in existing.dm_user_ids:
        return "a campaign with that name exists and is not this DM's"
    if existing.imported != existing.sessions:
        return "a campaign with that name holds a session that was not imported"
    return None


@dataclass(slots=True)
class Cost:
    """The most AI work the run may do, for the line printed before `--yes`."""

    lore_calls: int
    import_windows: int
    story_batches: int
    recap_calls: int

    @property
    def total(self) -> int:
        return self.lore_calls + self.import_windows + self.story_batches + self.recap_calls

    def line(self) -> str:
        return (
            f"Up to {self.total} paid AI calls: {self.lore_calls} for the lore read, "
            f"{self.import_windows} for cleaning the session, {self.story_batches} for the "
            f"story read and {self.recap_calls} for the recap (no audio)."
        )


@dataclass(slots=True)
class Step:
    name: str
    seconds: float
    counts: Mapping[str, int | str] = field(default_factory=dict)


def report(
    steps: Sequence[Step], *, commit: str, switches: Collection[str], today: str
) -> list[str]:
    """One counts-only block to paste on #3545: the deploy commit and switches first, then each
    step with how long it took and its counts."""
    lines = [
        f"Sample run {today}, commit {commit}",
        "Switches on: " + (", ".join(sorted(switches)) if switches else "none"),
    ]
    for step in steps:
        counts = ", ".join(f"{k} {v}" for k, v in step.counts.items())
        lines.append(f"- {step.name}: {step.seconds:.0f} s" + (f"; {counts}" if counts else ""))
    return lines
