"""May DMbot use this map? The one answer every map reader asks (#4173 slice 6b-1; Supervisor's
ruling, 2026-11-13, from the design's rule 3, `docs/design/mapbot_layers.md` section 13: two yeses,
and nothing reads a map closely before a DM's yes to using it).

Until MapBot slice 3 keeps each map's own yes (`campaign_maps`: who said yes to keeping it, and
the DM's yes to using it), the only map that has both is one the campaign's own DM posted: the
DM's own upload is both yeses. A player's map gets no close read (no sizes, no links) until then;
its plain read, its short transcript line and its note stay as they were. Slice 3 widens this one
function, and no caller changes. Pure: no Discord, no database."""

from __future__ import annotations

from collections.abc import Collection


def may_use_map(dm_user_ids: Collection[int], poster_id: int) -> bool:
    """Whether DMbot may read this map closely and, later, use it: today, only when the person
    who posted it is one of the campaign's DMs. For a picture in chat, before any map row exists;
    a kept map's answer is `dmbot.maps.store.may_use_map` (the DM's yes on its row), and 6b-2
    makes that the only one (#4349)."""
    return poster_id in dm_user_ids
