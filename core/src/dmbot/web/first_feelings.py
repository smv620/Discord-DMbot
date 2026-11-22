"""The first meeting's feeling reminder under a waiting "knows" claim on the website (#4942, the web
half of #4229 slice 4b; migration 0592): "🤔 **Mithral Keep** (Tala's home) is unfriendly toward
dwarves, and **Brewner** is a dwarf. You decide how they feel." One database door
(`dmbot_web_first_feeling_info`) gives the facts for the ids the page's own list showed, and the
line is worked out here by the store's own pure functions (`claims.feeling_pairs`,
`claims.feeling_lines`), so the website says exactly what Discord does. Worked out each time and
never stored, only from feelings the DM kept; it sets nothing. The line keeps Discord's own marks
(`**name**` for bold, and a backslash before a character that would otherwise be formatting) for
the page to show. Words only: no id, no transcript line."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from dmbot.memory.claims import Claim, claim_of, feeling_lines, feeling_pairs
from dmbot.memory.ontology import Ontology


def _claim(row: Mapping[str, Any]) -> Claim:
    """A claim from the door's few columns (the rest are filled with what no reminder reads)."""
    return claim_of(
        {
            "id": row["id"], "subject": row["subject"], "relationship": row["relationship"],
            "object": row["object"], "details": row.get("details") or "", "how": "", "lines": [],
            "speaker_ids": [],
            "source": "", "prompt_version": "", "model": "", "status": "proposed", "reason": "",
            "session_started_at": 0, "created_at": 0, "decided_at": None, "fact_id": None,
            "reading": None, "subject_picked": row.get("subject_picked"),
            "object_picked": row.get("object_picked"),
        }
    )  # fmt: skip


def lines_by_claim(info: Mapping[str, Any]) -> dict[str, str]:
    """The reminder for each claim in the door's answer that would be the first `knows` between
    two characters who have never been connected and that the DM's kept feelings speak to. The
    default ontology answers "is this a feeling between two characters", as it does for every
    campaign that has not changed `knows`."""
    claims = [_claim(c) for c in info["claims"]]
    pairs = feeling_pairs(Ontology.build(), claims, info["entities"], info["aliases"])
    if not pairs:
        return {}
    return feeling_lines(pairs, info["entities"], info["relations"], info["homes"], info["species"])
