"""The per-name trace of the after-session name step (#5055 slice B; docs/design/session_names.md,
section 8 and rule 6): every candidate a reader found ends with one fixed reason, so a name the
sample run missed can be named and explained ("the reader never listed it", "too few hearings",
"over the number", "the DM said it isn't a name").

Dev tools only. Rows are written only when `DMBOT_NAME_TRACE_DIR` names a folder and
`DMBOT_NAME_TRACE_GUILD` names the test server (server only, never in production use), and only
for a sample campaign there (`is_sample`, the same rule as `devtools.sample_name`): a real DM
who gives a campaign the sample's name elsewhere is never traced. The people at the table are
never traced either, and a name only a speaker who stopped being recorded said is one count row
with no name (`reader_rows`). Production keeps counts (the log); this file is where the public
sample's names go, on the server, never on GitHub. `why_missed` reads it.
"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter
from collections.abc import Collection, Iterable, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path

from dmbot.memory import name_reader
from dmbot.memory.models import name_key

log = logging.getLogger(__name__)

# The sample campaign's names, as `devtools.sample_name` has them. Copied, because the bot never
# imports the dev tools (test_replay's isolation test); a test keeps the two the same.
_OLD_SAMPLE = "test of the frostmaiden (sample)"
_SAMPLE = re.compile(
    r"^test of the frostmaiden \(sample [0-9]{1,4}[a-z]?(?:\.[1-9][0-9]?)?"
    r"(?: (?:no capitals|spoken|respelled))?\)$"
)


def is_sample(name: str) -> bool:
    """Exactly a sample campaign's name, never a look-alike."""
    return name == _OLD_SAMPLE or _SAMPLE.fullmatch(name) is not None


# Where a candidate came from
SCAN = "scan"  # the capital-letter scan (`memory/scan.py`)
READER = "reader"  # the name reader (`memory/name_reader.py`)
ASKED = "name_pass"  # a word PlotBot's name pass was not sure of (#2628)

# How a candidate ended, beside the reader's own refusal reasons (`name_reader`)
OFFERED = "offered"  # suggested to the DM
OVER_CAP = "over_the_number"  # past the scan's number this session (held for later, #5082)
STORE_REFUSED = "store_refused"  # the memory store would not take it (a rule; the log says which)
JOINED = "joined_scan"  # the reader found a name the scan already offered


@dataclass(frozen=True, slots=True)
class Row:
    source: str  # SCAN, READER or ASKED
    name: str  # as found: the public sample's words only (a sample campaign, see `write`)
    reason: str  # one fixed word
    why: str = ""  # the reader's reason for a name (introduced, place...), when it gave one
    times: int = 0

    @property
    def key(self) -> str:
        return name_key(self.name)


def write(
    folder: str,
    campaign_name: str,
    campaign_id: str,
    session_id: str | None,
    rows: Iterable[Row],
    *,
    guild_id: int,
    test_guild: int,
) -> Path | None:
    """Append the rows to `trace-<campaign>.jsonl` in `folder`, only for a sample campaign on the
    test server (`test_guild`) and only when `folder` is set. Never raises: the trace is a dev
    tool, the step goes on without it. Returns the file written, or None."""
    if not folder or not test_guild or guild_id != test_guild:
        return None
    if not is_sample(campaign_name):
        return None
    try:
        path = Path(folder) / f"trace-{campaign_id}.jsonl"
        with path.open("a", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps({"session": session_id or "", **asdict(row)}) + "\n")
        return path
    except OSError as exc:  # (the kind only)
        log.warning("Name trace not written (%s)", type(exc).__name__)
        return None


def read(path: Path) -> list[Row]:
    """The rows of one trace file; a line that is not a row is passed over."""
    out: list[Row] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            data = json.loads(line)
            out.append(
                Row(str(data["source"]), str(data["name"]), str(data["reason"]),
                    str(data.get("why", "")), int(data.get("times", 0)))
            )  # fmt: skip
        except (ValueError, KeyError, TypeError):
            continue
    return out


def by_key(rows: Iterable[Row]) -> dict[str, list[Row]]:
    """Every row of one name key, in the order written."""
    out: dict[str, list[Row]] = {}
    for row in rows:
        out.setdefault(row.key, []).append(row)
    return out


def counts(rows: Iterable[Row]) -> Mapping[tuple[str, str], int]:
    """(source, reason) -> how many: the summary sample_run prints, counts only."""
    return Counter((r.source, r.reason) for r in rows)


def reader_rows(
    outcomes: Iterable[name_reader.Outcome],
    dropped: Iterable[name_reader.Outcome],
    kept: Iterable[name_reader.Found],
    scan_keys: Collection[str],
) -> list[Row]:
    """The name reader's rows: each refusal and each name with too few hearings, with its reason,
    and a kept name the scan also had as `joined_scan` (the rest are traced with the joined list).
    Consent has no exception, even here (Supervisor, on #5107): a name only the lines of someone
    who stopped being recorded hold is never written, only one count row for all of them
    (`speaker_stopped`, no name). The people at the table are never written either."""
    out: list[Row] = []
    stopped = 0
    for o in [*outcomes, *dropped]:
        if o.reason == name_reader.STOPPED:
            stopped += 1
        elif o.reason != name_reader.AT_TABLE:
            out.append(Row(READER, o.name, o.reason))
    if stopped:
        out.append(Row(READER, "", name_reader.STOPPED, times=stopped))
    out += [
        Row(READER, f.name, JOINED, f.why, f.times) for f in kept if name_key(f.name) in scan_keys
    ]
    return out
