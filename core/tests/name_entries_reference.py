"""Never edit this file: it is the old behaviour, verbatim, and can be deleted once the new
`gate.name_entries` has shipped.

The `gate.name_entries` as it was before the grouping rewrite (#3228): kept as the reference the
equivalence test checks the fast one against. Not used anywhere else."""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from typing import Any

from dmbot.memory.gate import (
    CONFIRMED,
    ENTRY,
    SOMEONE,
    NameRows,
    Viewer,
    Visible,
    _public_entry,
    _unnamed_label,
    display_name,
    lookup_key,
)


def reference_name_entries(
    seen: Visible,
    viewer: Viewer,
    entity_ids: Sequence[str],
    rows: NameRows,
    *,
    public: bool = False,
    texts_out: dict[str, str] | None = None,
    on_their_own: Collection[str] = (),
) -> dict[str, str | None]:
    """`names_for` without the reading: the same rules over rows already read, so the website
    names an entry exactly as Discord does. `on_their_own`: other names that facts were learned
    through, which the website draws on a node of their own (`split_connections`); such a name is
    never also the name of the person's own node, or the one person would be drawn twice."""
    entries = {
        k: r
        for k, r in rows.entities.items()
        if k in set(entity_ids) and r["status"] not in ("merged", "rejected")
    }
    names = [a for a in rows.aliases if str(a["entity_id"]) in entries]
    anchors = {
        str(r["unnamed_anchor"])
        for r in entries.values()
        if r["unnamed"] and r["unnamed_anchor"] is not None
    }
    anchor_rows = {k: r for k, r in rows.entities.items() if k in anchors}
    secret_own_names = [
        a
        for a in rows.aliases
        if str(a["entity_id"]) in anchor_rows
        and a["secret"]
        and a["key"] == lookup_key(str(anchor_rows[str(a["entity_id"])]["name"]))
    ]
    anchor_secret = {str(a["entity_id"]) for a in secret_own_names}
    anchor_held = {
        str(a["entity_id"]) for a in secret_own_names if str(a["id"]) in seen.aliases
    }  # (a row on the secret name itself is what makes it known)
    merged = [
        r
        for r in rows.entities.values()
        if r["merged_into"] is not None and str(r["merged_into"]) in entries and r["unnamed"]
    ]
    learned: dict[str, int] = {}
    if not seen.everything and names:
        listed = {str(a["id"]) for a in names}
        for alias_id, character, at in rows.learned:
            # the order THIS viewer learned them in: only its own characters' rows count
            if alias_id not in listed or (viewer.characters and character not in viewer.characters):
                continue
            learned[alias_id] = min(learned.get(alias_id, at), at)

    def anchor_name(row: Mapping[str, Any]) -> str | None:
        """The name of what an unnamed person hangs off, if THIS viewer may read it."""
        anchor = anchor_rows.get(str(row["unnamed_anchor"])) if row["unnamed_anchor"] else None
        if (
            anchor is None
            or anchor["unnamed"]
            or anchor["status"] != CONFIRMED
            or anchor["merged_into"] is not None
        ):
            return None
        known = seen.everything or (
            (ENTRY, str(anchor["id"])) in seen.sureness
            and (str(anchor["id"]) not in anchor_secret or str(anchor["id"]) in anchor_held)
        )
        if known or _public_entry(anchor, str(anchor["id"]) in anchor_secret):
            return str(anchor["name"])
        return None

    def safe_text(entity_id: str, text: str) -> str:
        """An other name as this viewer may read it: a joined person's label is rebuilt."""
        if not text.endswith(" (name unknown)") or seen.everything:
            return text
        for gone in merged:
            if str(gone["merged_into"]) == entity_id and str(gone["name"]) == text:
                return _unnamed_label(gone, anchor_name(gone))
        return "someone (name unknown)"

    out: dict[str, str | None] = {}
    for entity_id in entity_ids:
        row = entries.get(entity_id)
        if row is None:
            out[entity_id] = None
            continue
        main = str(row["name"])
        # (an entry's own name is stored as a name too: it stands for the main name, which the
        # entry's own row governs, so it is not one of the "other" names)
        others = sorted(
            (
                (str(a["id"]), safe_text(entity_id, str(a["text"])))
                for a in names
                if str(a["entity_id"]) == entity_id
                and a["key"] != lookup_key(main)
                and str(a["id"]) not in on_their_own
            ),
            key=lambda pair: (learned.get(pair[0], 0), pair[0]),
        )
        own_name = [
            str(a["id"])
            for a in names
            if str(a["entity_id"]) == entity_id and a["key"] == lookup_key(main)
        ]
        own_name_secret = any(
            a["secret"]
            for a in names
            if str(a["entity_id"]) == entity_id and a["key"] == lookup_key(main)
        )
        if row["unnamed"] and not seen.everything:
            main = _unnamed_label(row, anchor_name(row))
        if any(alias in seen.aliases for alias in own_name):
            shown: str | None = main
        elif own_name_secret and not seen.everything:
            # (an entry row does not make a secret name known: only a row on that name does)
            shown = next((text for alias_id, text in others if alias_id in seen.aliases), None)
        else:
            shown = display_name(seen, entity_id, main, others)
            if shown is None and _public_entry(row, own_name_secret):
                shown = main  # (a name every player may read, for a viewer who knows no other)
        out[entity_id] = SOMEONE if shown is None and public else shown
        if texts_out is not None:
            for a in names:
                if str(a["entity_id"]) == entity_id:
                    texts_out[str(a["id"])] = safe_text(entity_id, str(a["text"]))
    return out
