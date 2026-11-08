"""Scores a sample backup's importance ranks and story roles against the rank key (#3172), with
counts and the key's own (public sample) names only.

    python -m dmbot.devtools.rank_key_check BACKUP.dmbot.json.gz [--key rank_key.json]
        [--master master_key.json] [--i-know-this-is-the-test-campaign]

Each rank key entry is found in the backup the way the story graph check finds it (its name and
other names from the master key; a person never named by its description). Its rank is worked out
from the backup exactly as the bot works it out (`importance_rank.ranks_from`: the floor from the
counted facts and the newest assessment), and its roles are those its connections show
(`roles_backed`) and those stored. The report says, for the ranks, how many are exact, one step
off and further off (and which way), and for the roles how many of the key's were found and how
many the backup holds that the key does not list. While the key is marked draft (GraphEngineer's
reading, for the owner to correct) the report says it is scored "against the draft key". It reads
the backup file only: no database, no Discord, no AI.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dmbot.campaigns.store import decode_backup
from dmbot.devtools.graph_key_check import DEFAULT_KEY, SAMPLE_CAMPAIGN, Graph, _tables
from dmbot.memory import roles
from dmbot.memory.importance_rank import links_from, newest_by_group, ranks_from, roots
from dmbot.memory.roles_backed import backed_roles

DEFAULT_RANK_KEY = DEFAULT_KEY.with_name("rank_key.json")


@dataclass
class RankReport:
    draft: bool = False
    expected: int = 0
    missing: list[str] = field(default_factory=list)  # key entries not in the backup
    exact: int = 0
    one_off: int = 0
    far_off: int = 0
    too_high: int = 0
    too_low: int = 0
    off: list[tuple[str, int, int]] = field(default_factory=list)  # (name, key, backup)
    roles_expected: int = 0
    roles_found: int = 0
    roles_missed: Counter[str] = field(default_factory=Counter)
    roles_extra: Counter[str] = field(default_factory=Counter)


def _section(backup: Mapping[str, Any], name: str) -> list[Any]:
    sections = backup.get("sections")
    rows = sections.get(name) if isinstance(sections, dict) else None
    return rows if isinstance(rows, list) else []


def held_ranks_and_roles(backup: Mapping[str, Any]) -> tuple[dict[str, int], dict[str, set[str]]]:
    """Every live entry's rank and roles, from the backup alone. Pure."""
    tables = _tables(backup)
    entities = {
        str(e["id"]): {
            "type": e.get("type"), "role": e.get("role"), "status": e.get("status"),
            "merged_into": e.get("merged_into"),
        }
        for e in tables.get("entity", ())
        if e.get("status") in ("proposed", "confirmed", "merged") and e.get("type") != "person"
    }  # fmt: skip
    ended = roots(entities)
    relations = [
        {**r, "to_session_at": r.get("to_session_at")}
        for r in tables.get("relation", ())
        if r.get("status") == "confirmed" and r.get("to_session_at") is None
    ]
    counted = [
        (str(r["subject_id"]), str(r["object_id"]), str(r["predicate"]))
        for r in relations
        if r["predicate"] != "plays" and r.get("source") != "party"
    ]
    links, companions = links_from(counted, entities, ended)
    newest: dict[str, tuple[int, int]] = {}
    for seq, row in enumerate(_section(backup, "memory_importance"), start=1):
        if isinstance(row, dict) and str(row.get("entity_id")) in entities:
            newest[str(row["entity_id"])] = (seq, int(row["rank"]))  # (the file is oldest first)
    heard: dict[str, set[int]] = {}
    for row in _section(backup, "memory_heard"):
        if isinstance(row, dict):
            heard.setdefault(str(row.get("entity_id")), set()).add(int(row["session_started_at"]))
    ranks = ranks_from(entities, ended, newest_by_group(ended, newest), heard, links, companions)
    held: dict[str, set[str]] = {e: set(r) for e, r in backed_roles(entities, relations).items()}
    for row in _section(backup, "memory_roles"):
        role = row.get("role") if isinstance(row, dict) else None
        if isinstance(role, dict) and role.get("ended_at") is None:
            root = ended.get(str(role.get("entity_id")))
            if root is not None:
                held.setdefault(root, set()).add(str(role.get("role")))
    return ranks, held


def check(
    backup: Mapping[str, Any], key: Mapping[str, Any], master: Mapping[str, Any]
) -> RankReport:
    report = RankReport(draft=key.get("status") == "draft")
    graph = Graph(_tables(backup))
    ranks, held = held_ranks_and_roles(backup)
    nodes = {str(n["name"]): n for n in master.get("nodes", ())}
    for entry in key.get("entries", ()):
        name, want = str(entry["name"]), int(entry["rank"])
        node = nodes.get(name, {})
        report.expected += 1
        ids = [i for i in graph.ids([name, *node.get("aliases", ())]) if i in ranks]
        if not ids:
            report.missing.append(name)
            continue
        got = max(ranks[i] for i in ids)  # (two entries for one name: the higher one counts)
        if got == want:
            report.exact += 1
        else:
            report.one_off += abs(got - want) == 1
            report.far_off += abs(got - want) > 1
            report.too_high += got > want
            report.too_low += got < want
            report.off.append((name, want, got))
        have = set().union(*(held.get(i, set()) for i in ids))
        wanted = set(entry.get("roles", ()))
        report.roles_expected += len(wanted)
        report.roles_found += len(wanted & have)
        report.roles_missed.update(wanted - have)
        report.roles_extra.update(have - wanted - {roles.COMMONER})
    return report


def render(r: RankReport) -> str:
    against = "against the draft key" if r.draft else "against the key"
    found = r.expected - len(r.missing)
    lines = [
        f"Importance check ({against})",
        f"Entries: {r.expected} in the key, {found} found, {len(r.missing)} not in the backup",
        f"Ranks: {r.exact} exact, {r.one_off} one step off, {r.far_off} further off "
        f"({r.too_high} too high, {r.too_low} too low)",
        f"Roles: {r.roles_found} of {r.roles_expected} found; extra roles not in the key: "
        f"{sum(r.roles_extra.values())}",
    ]
    if r.roles_missed:
        lines.append(
            "Roles missed by code: "
            + ", ".join(f"{k} {v}" for k, v in sorted(r.roles_missed.items()))
        )
    if r.roles_extra:
        lines.append(
            "Extra roles by code: "
            + ", ".join(f"{k} {v}" for k, v in sorted(r.roles_extra.items()))
        )
    if r.off:
        lines.append("Ranks off (key name: key rank -> backup rank):")
        lines += [f"  {name}: {want} -> {got}" for name, want, got in r.off]
    if r.missing:
        lines.append("Not in the backup: " + ", ".join(r.missing))
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("backup", type=Path, help="the campaign backup (.dmbot.json.gz or .json)")
    parser.add_argument("--key", type=Path, default=DEFAULT_RANK_KEY, help="the rank key")
    parser.add_argument("--master", type=Path, default=DEFAULT_KEY, help="the master key (names)")
    parser.add_argument("--i-know-this-is-the-test-campaign", action="store_true", dest="force")
    args = parser.parse_args(argv)
    try:
        backup = decode_backup(args.backup.read_bytes())
        key = json.loads(args.key.read_text())
        master = json.loads(args.master.read_text())
        campaign = backup.get("campaign") if isinstance(backup, dict) else None
        name = campaign.get("name") if isinstance(campaign, dict) else None
        if name != SAMPLE_CAMPAIGN and not args.force:
            raise ValueError(
                f'This campaign is not "{SAMPLE_CAMPAIGN}"; the check is only for the sample.'
            )
        if not isinstance(backup, dict):
            raise ValueError("That file is not a campaign backup.")
        report = check(backup, key, master)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        sys.stderr.write(f"{exc}\n")
        return 2
    sys.stdout.write(render(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
