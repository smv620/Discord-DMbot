"""The questions a sample run leaves for the DM, and what the DM answered (#3545; the owner's ask
and Supervisor's ruling of 2026-11-15).

A. `asked`: how many questions wait for the DM, by kind, counted by the one definition the bot and
   the website use (`questions.counts.count_kinds`). Every run.
B. `snapshot`, then `answered`: on the run that is kept, the items waiting at its end are written
   to a server-only file (their ids and DMbot's suggestion). Once the owner has answered in
   Discord, `sample_run --part2` reads the same items again and sorts each one:
   - "kept as suggested": the DM kept DMbot's own answer, unchanged (DMbot had it right);
   - "changed": the DM kept it with another kind, other words or as another entry;
   - "turned down";
   - "not answered yet".
C. Those counts over deploys are evidence only: no kind is left to DMbot without a design note that
   cites at least five deploys of them (Supervisor).

Counts only ever leave the server. The snapshot holds ids and DMbot's suggested kinds and words, so
it is written beside the backups, owner-only, never to GitHub. `classify` is pure.
"""

from __future__ import annotations

import json
import os
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

KEPT, CHANGED, TURNED_DOWN, OPEN = "kept as suggested", "changed", "turned down", "not answered yet"
OUTCOMES = (KEPT, CHANGED, TURNED_DOWN, OPEN)
# The kinds part 2 follows, by what is stored for each (a kind not here is counted in A only)
NAMES, CONNECTIONS, FACTS, PLACES, LANGUAGES, TRAITS = (
    "names", "connections", "things said", "world notes (places)", "world notes (languages)",
    "lineage and class",
)  # fmt: skip
FOLLOWED = (NAMES, CONNECTIONS, FACTS, PLACES, LANGUAGES, TRAITS)


async def asked(sources: Any, guild_id: int, campaign_id: str) -> dict[str, int]:
    """Questions waiting for the DM now, by kind label (A). A kind the bot can't count here is
    left out, as in the bot's own count."""
    from dmbot.questions import counts, kinds

    got = await counts.count_kinds(sources, guild_id, campaign_id)
    return {kinds.LABELS.get(k, k): c.n for k, c in got.items() if c.n}


async def _rows(conn: Any, sql: str, ids: tuple[int, str]) -> list[dict[str, Any]]:
    cur = await conn.execute(sql, ids)
    return [dict(r) for r in await cur.fetchall()]


_SCOPE = " WHERE guild_id = %s AND campaign_id = %s"


async def read_state(db: Any, guild_id: int, campaign_id: str) -> dict[str, list[dict[str, Any]]]:
    """Everything `classify` needs, read now from the one campaign: the entries, the kept
    connections, the waiting connection rows, the claims, the place readings, the languages and the
    lineage offers with the characters' rules links."""
    ids = (guild_id, campaign_id)
    async with db.guild(guild_id) as conn:
        return {
            "entities": await _rows(
                conn,
                "SELECT id, type, name, status, merged_into FROM memory_entities" + _SCOPE,
                ids,
            ),
            "relations": await _rows(
                conn,
                "SELECT id, subject_id, predicate, object_id, status FROM memory_relations"
                + _SCOPE,
                ids,
            ),
            "links": await _rows(
                conn, "SELECT subject_id, predicate, object_id FROM backstory_links" + _SCOPE, ids
            ),
            "claims": await _rows(conn, "SELECT id, status FROM memory_claims" + _SCOPE, ids),
            "map_facts": await _rows(
                conn,
                "SELECT id, entity_id, layer, value, faction_id, status"
                " FROM memory_map_facts" + _SCOPE,
                ids,
            ),
            "languages": await _rows(
                conn,
                "SELECT id, entity_id, name_key, given_by FROM memory_story_languages" + _SCOPE,
                ids,
            ),
            "traits": await _rows(
                conn,
                "SELECT entity_id, kind, name_key, state FROM memory_trait_offers" + _SCOPE,
                ids,
            ),
            "rule_links": await _rows(
                conn, "SELECT entity_id, kind, name FROM memory_rule_links" + _SCOPE, ids
            ),
        }


def waiting(state: Mapping[str, Sequence[Mapping[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
    """The items waiting for the DM in `state`, with DMbot's suggestion: what `snapshot` keeps."""
    return {
        NAMES: [
            {"id": r["id"], "type": r["type"], "name": r["name"]}
            for r in state["entities"]
            if r["status"] == "proposed"
        ],
        CONNECTIONS: [
            {"s": r["subject_id"], "p": r["predicate"], "o": r["object_id"]} for r in state["links"]
        ],
        FACTS: [{"id": r["id"]} for r in state["claims"] if r["status"] == "proposed"],
        PLACES: [
            {
                "id": r["id"],
                "place": r["entity_id"],
                "layer": r["layer"],
                "value": r["faction_id"] or r["value"],
            }
            for r in state["map_facts"]
            if r["status"] == "read"
        ],
        LANGUAGES: [{"id": r["id"]} for r in state["languages"] if r["given_by"] == "read"],
        TRAITS: [
            {"who": r["entity_id"], "kind": r["kind"], "key": r["name_key"]}
            for r in state["traits"]
            if r["state"] == "waiting"
        ],
    }


def _key(text: str) -> str:
    return " ".join(str(text).split()).casefold()


def classify(
    before: Mapping[str, Sequence[Mapping[str, Any]]],
    now: Mapping[str, Sequence[Mapping[str, Any]]],
) -> dict[str, Counter[str]]:
    """Each item that waited (`before`, from `waiting`) sorted by what became of it in `now` (from
    `read_state`). Pure."""
    out: dict[str, Counter[str]] = {kind: Counter() for kind in FOLLOWED}
    entities = {r["id"]: r for r in now["entities"]}
    for item in before.get(NAMES, []):
        e = entities.get(item["id"])
        if e is None or e["status"] == "rejected":
            out[NAMES][TURNED_DOWN] += 1
        elif e["status"] == "proposed":
            out[NAMES][OPEN] += 1
        elif e.get("merged_into"):
            out[NAMES][CHANGED] += 1  # (the DM said it is another entry)
        elif e["type"] == item["type"] and _key(e["name"]) == _key(item["name"]):
            out[NAMES][KEPT] += 1
        else:
            out[NAMES][CHANGED] += 1
    links = {(r["subject_id"], r["predicate"], r["object_id"]) for r in now["links"]}
    kept = [r for r in now["relations"] if r["status"] == "confirmed"]
    for item in before.get(CONNECTIONS, []):
        s, p, o = item["s"], item["p"], item["o"]
        pair = {s, o}
        if (s, p, o) in links:
            out[CONNECTIONS][OPEN] += 1
        elif any((r["subject_id"], r["predicate"], r["object_id"]) == (s, p, o) for r in kept):
            out[CONNECTIONS][KEPT] += 1
        elif any({r["subject_id"], r["object_id"]} == pair for r in kept):
            out[CONNECTIONS][CHANGED] += 1  # (another kind, or the other way round)
        else:
            out[CONNECTIONS][TURNED_DOWN] += 1
    claims = {r["id"]: r["status"] for r in now["claims"]}
    for item in before.get(FACTS, []):
        status = claims.get(item["id"])
        out[FACTS][
            KEPT if status == "confirmed" else OPEN if status == "proposed" else TURNED_DOWN
        ] += 1
    facts = {r["id"]: r for r in now["map_facts"]}
    for item in before.get(PLACES, []):
        f = facts.get(item["id"])
        if f is not None and f["status"] == "read":
            out[PLACES][OPEN] += 1
        elif f is not None and f["status"] == "confirmed":
            out[PLACES][KEPT] += 1
        elif any(
            r["entity_id"] == item["place"] and r["layer"] == item["layer"]
            and r["status"] == "confirmed"
            for r in now["map_facts"]
        ):  # fmt: skip
            out[PLACES][CHANGED] += 1  # (the DM set another value)
        else:
            out[PLACES][TURNED_DOWN] += 1
    languages = {r["id"]: r["given_by"] for r in now["languages"]}
    for item in before.get(LANGUAGES, []):
        given = languages.get(item["id"])
        out[LANGUAGES][KEPT if given == "dm" else OPEN if given == "read" else TURNED_DOWN] += 1
    offers = {(r["entity_id"], r["kind"], r["name_key"]): r["state"] for r in now["traits"]}
    rules = {(r["entity_id"], r["kind"]): _key(r["name"]) for r in now["rule_links"]}
    for item in before.get(TRAITS, []):
        state = offers.get((item["who"], item["kind"], item["key"]))
        if state == "waiting":
            out[TRAITS][OPEN] += 1
        elif state == "never":
            out[TRAITS][TURNED_DOWN] += 1
        elif rules.get((item["who"], item["kind"])) == item["key"]:
            out[TRAITS][KEPT] += 1
        else:
            out[TRAITS][CHANGED] += 1
    return out


def lines(result: Mapping[str, Counter[str]]) -> list[str]:
    """Counts only, a line a kind, then the share DMbot had right of those answered."""
    out: list[str] = []
    for kind in FOLLOWED:
        c = result.get(kind, Counter())
        total = sum(c.values())
        if not total:
            continue
        answered = total - c[OPEN]
        rate = f"{c[KEPT] / answered:.0%} right" if answered else "none answered yet"
        out.append(
            f"{kind}: {total} asked; " + ", ".join(f"{o} {c[o]}" for o in OUTCOMES) + f" ({rate})"
        )
    return out


def save(path: Path, items: Mapping[str, Any]) -> None:
    """The snapshot, owner-only (it holds DMbot's suggested names)."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(items, f)


def load(path: Path) -> dict[str, list[dict[str, Any]]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "campaign" not in data:
        raise ValueError("not a sample run's question snapshot")
    return data
