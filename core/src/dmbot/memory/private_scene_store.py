"""The private scenes kept for a campaign (`memory_private_scenes`, migration 0419; #2996 rule 5).

After a session, `PrivateScenes.write_session` reads the session's spoken lines, the game clock and
who plays each player character, runs the pure finder (`private_scenes.find`) and keeps what it
finds. Ids and times only, never a word, so nothing here can leak what was said.

- **The DM decides.** A scene the DM marked "Not private" (`not_private`) is never found again: a
  later run replaces only the rows it wrote, and skips a scene of the same two that overlaps a
  marked one (Supervisor's ruling, 2026-11-10: "a re-read must not undo" it).
- **Consent.** Only lines of people recorded now are read, a row cites who spoke in it, and it goes
  when any of them stops being recorded (`forget_speaker`), with its session's transcript
  (`forget_session`) and, on a move to another server, when they are not recorded there
  (`keep_recorded`). The DM's mark has no words either: it loses that person's id and stays.
- **Counts only** leave this module in logs.
"""

from __future__ import annotations

import secrets
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from typing import Any

from dmbot.db import Database
from dmbot.memory.models import spelling_key
from dmbot.memory.private_scenes import Clock, Scene, find

LIST_MAX = 250  # rows a list shows; past it, the newest


@dataclass(frozen=True)
class Written:
    """What one run on a session did: scenes kept, scenes skipped because the DM said "Not
    private" about them before."""

    kept: int = 0
    not_private: int = 0
    new: int = 0  # (kept and not found before: the after-session note counts only these)


@dataclass(frozen=True)
class Kept:
    """One kept scene, as a list or PlotBot reads it."""

    id: str
    first_id: str
    second_id: str
    session_started_at: int
    from_ms: int
    to_ms: int
    line_count: int


class PrivateScenes:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def write_session(
        self,
        guild_id: int,
        campaign_id: str,
        session_id: str,
        opted_in: Collection[int],
        now: int,
    ) -> Written:
        """Find the session's private scenes and keep them, in place of the ones an earlier run on
        it kept. `opted_in`: who is recorded in this server now; nobody else's lines are read. One
        transaction; two runs on one session wait for each other on a lock of their own, never on
        the session's row (a person's "Delete my transcripts" writes that row after deleting these,
        so holding it here could deadlock). A scene found again keeps its id, so a "Not private"
        button posted before a re-run still works."""
        recorded = sorted(set(opted_in))
        async with self._db.guild(guild_id) as conn:
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (f"dmbot.private_scenes:{guild_id}:{session_id}",),
            )
            cur = await conn.execute(
                "SELECT started_at FROM transcript_sessions WHERE guild_id = %s"
                " AND campaign_id = %s AND id = %s",
                (guild_id, campaign_id, session_id),
            )
            row = await cur.fetchone()
            if row is None:
                return Written()
            started_at = int(row["started_at"])
            # Spoken lines only: the DM's sidebar, table questions and typed lines are not the
            # scene (and a typed line has no voice to place).
            cur = await conn.execute(
                "SELECT started_ms, user_id, text, heard FROM transcript_lines WHERE guild_id = %s"
                " AND session_id = %s AND sidebar IS NULL AND NOT typed"
                " AND user_id = ANY(%s::bigint[]) ORDER BY started_ms, id",
                (guild_id, session_id, recorded),
            )
            rows = list(await cur.fetchall())
            lines = [(int(r["started_ms"]), int(r["user_id"])) for r in rows]
            # (each line's words, folded, in memory only: to find the names said in a scene)
            said = [
                (int(r["started_ms"]), int(r["user_id"]),
                 f" {spelling_key(str(r['text'] or ''))} {spelling_key(str(r['heard'] or ''))} ")
                for r in rows
            ]  # fmt: skip
            players = await _players(conn, guild_id, campaign_id)
            scenes: list[Scene] = []
            if lines and len(players) >= 2:
                end = lines[-1][0] // 1000
                clock = await _clock(conn, guild_id, campaign_id, list(players), end)
                scenes = find(lines, clock, players)
            cur = await conn.execute(
                "DELETE FROM memory_private_scenes WHERE guild_id = %s AND campaign_id = %s"
                " AND session_started_at = %s AND status = 'kept'"
                " RETURNING id, first_id, second_id, from_ms, to_ms",
                (guild_id, campaign_id, started_at),
            )
            before = [
                (str(r["id"]), (str(r["first_id"]), str(r["second_id"]), int(r["from_ms"]),
                                int(r["to_ms"])))
                for r in await cur.fetchall()
            ]  # fmt: skip
            cur = await conn.execute(
                "SELECT first_id, second_id, from_ms, to_ms FROM memory_private_scenes"
                " WHERE guild_id = %s AND campaign_id = %s AND session_started_at = %s"
                " AND status = 'not_private'",
                (guild_id, campaign_id, started_at),
            )
            rows = list(await cur.fetchall())
            # (a mark follows a merge: a character merged since is the one it became, so the DM's
            # "Not private" still holds for the same two under the kept entries)
            now_as = await _merged(conn, guild_id, campaign_id) if rows else {}
            marked: list[tuple[str, str, int, int]] = []
            for r in rows:
                a, b = sorted(
                    (_root(now_as, str(r["first_id"])), _root(now_as, str(r["second_id"])))
                )
                marked.append((a, b, int(r["from_ms"]), int(r["to_ms"])))
            used: set[str] = set()
            new = 0
            spellings: dict[str, str] | None = None  # (read once, when a scene is kept)
            kept = skipped = 0
            for scene in scenes:
                if any(_overlaps(scene, m) for m in marked):
                    skipped += 1
                    continue
                # (the same scene found again keeps its id: the first earlier one it overlaps)
                same = next((i for i, m in before if i not in used and _overlaps(scene, m)), None)
                scene_id = same or secrets.token_hex(16)
                used.add(scene_id)
                cur = await conn.execute(
                    "INSERT INTO memory_private_scenes (guild_id, campaign_id, id, first_id,"
                    " second_id, session_started_at, from_ms, to_ms, line_count, speaker_ids,"
                    " status, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s,"
                    " 'kept', %s) ON CONFLICT DO NOTHING",
                    (
                        guild_id, campaign_id, scene_id, scene.first_id,
                        scene.second_id, started_at, scene.from_ms, scene.to_ms,
                        scene.line_count, list(scene.speaker_ids), now,
                    ),
                )  # fmt: skip
                kept += int(cur.rowcount or 0)
                new += int(cur.rowcount or 0) if same is None else 0
                if cur.rowcount:
                    if spellings is None:
                        spellings = await _spellings(conn, guild_id, campaign_id)
                    await _keep_entries(conn, guild_id, campaign_id, scene_id, scene, said,
                                        spellings)  # fmt: skip
            return Written(kept, skipped, new)

    async def not_private(self, guild_id: int, campaign_id: str, scene_id: str, now: int) -> bool:
        """The DM's "Not private": the scene stays marked, so no later run finds it again. False
        when there is no such kept scene (already marked, or gone)."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE memory_private_scenes SET status = 'not_private', decided_at = %s"
                " WHERE guild_id = %s AND campaign_id = %s AND id = %s AND status = 'kept'"
                " RETURNING id",
                (now, guild_id, campaign_id, scene_id),
            )
            return await cur.fetchone() is not None

    async def roots(self, guild_id: int, campaign_id: str, ids: Sequence[str]) -> dict[str, str]:
        """Each entry id to the entry it ended up as, following merges (a list shows a merged
        character under the name it became)."""
        async with self._db.guild(guild_id) as conn:
            merged = await _merged(conn, guild_id, campaign_id)
        return {i: _root(merged, i) for i in ids}

    async def listed(
        self,
        guild_id: int,
        campaign_id: str,
        opted_in: Collection[int],
        session_started_at: int | None = None,
    ) -> list[Kept]:
        """The kept scenes, newest first (at most `LIST_MAX`), or one session's in the order they
        began. A row citing someone not recorded now is left out (the consent re-check on the
        read path); nothing is changed."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT id, first_id, second_id, session_started_at, from_ms, to_ms, line_count"
                " FROM memory_private_scenes WHERE guild_id = %s AND campaign_id = %s"
                " AND status = 'kept' AND speaker_ids <@ %s::bigint[]"
                " AND (%s::bigint IS NULL OR session_started_at = %s)"
                " ORDER BY session_started_at DESC, from_ms DESC, id LIMIT %s",
                (
                    guild_id, campaign_id, sorted(set(opted_in)), session_started_at,
                    session_started_at, LIST_MAX,
                ),
            )  # fmt: skip
            rows = [_kept(r) for r in await cur.fetchall()]
        return rows[::-1] if session_started_at is not None else rows


async def _spellings(conn: Any, guild_id: int, campaign_id: str) -> dict[str, str]:
    """Each folded spelling of a confirmed, live entry that is not a player character (their rank
    is 5 already) to its entry: its name and its confirmed, open other names. A spelling two
    entries share is left out (never a guess), and a secret one is never used."""
    cur = await conn.execute(
        "SELECT id AS entry, name AS text FROM memory_entities WHERE guild_id = %s"
        " AND campaign_id = %s AND status = 'confirmed' AND type <> 'person'"
        " AND role IS DISTINCT FROM 'player_character'"
        " UNION ALL SELECT a.entity_id AS entry, a.text FROM memory_aliases a"
        " JOIN memory_entities e ON e.guild_id = a.guild_id AND e.campaign_id = a.campaign_id"
        " AND e.id = a.entity_id WHERE a.guild_id = %s AND a.campaign_id = %s"
        " AND a.status = 'confirmed' AND NOT a.secret AND e.status = 'confirmed'"
        " AND e.type <> 'person' AND e.role IS DISTINCT FROM 'player_character'",
        (guild_id, campaign_id, guild_id, campaign_id),
    )
    owners: dict[str, set[str]] = {}
    for r in await cur.fetchall():
        key = spelling_key(str(r["text"]))
        if key:
            owners.setdefault(key, set()).add(str(r["entry"]))
    return {key: next(iter(ids)) for key, ids in owners.items() if len(ids) == 1}


async def _keep_entries(
    conn: Any,
    guild_id: int,
    campaign_id: str,
    scene_id: str,
    scene: Scene,
    said: Sequence[tuple[int, int, str]],
    spellings: dict[str, str],
) -> None:
    """The entries spoken of inside the scene by one of its two, as whole words (ids only)."""
    found: set[str] = set()
    for started_ms, speaker, words in said:
        if scene.from_ms <= started_ms <= scene.to_ms and speaker in scene.speaker_ids:
            found.update(e for key, e in spellings.items() if f" {key} " in words)
    for entity_id in sorted(found):
        await conn.execute(
            "INSERT INTO memory_private_scene_entries (guild_id, campaign_id, scene_id,"
            " entity_id) VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING",
            (guild_id, campaign_id, scene_id, entity_id),
        )


async def entries(conn: Any, guild_id: int, campaign_id: str) -> set[str]:
    """The entries said in a KEPT private scene of the campaign: the importance floor's input
    (#4110). A "Not private" scene, or one gone (its transcript, a speaker), counts for none."""
    cur = await conn.execute(
        "SELECT DISTINCT x.entity_id FROM memory_private_scene_entries x"
        " JOIN memory_private_scenes s ON s.guild_id = x.guild_id"
        " AND s.campaign_id = x.campaign_id AND s.id = x.scene_id"
        " WHERE x.guild_id = %s AND x.campaign_id = %s AND s.status = 'kept'",
        (guild_id, campaign_id),
    )
    return {str(r["entity_id"]) for r in await cur.fetchall()}


def _overlaps(scene: Scene, marked: tuple[str, str, int, int]) -> bool:
    """Whether a found scene is one the DM already said is not private: the same two, and their
    times touch."""
    first, second, from_ms, to_ms = marked
    return (
        (scene.first_id, scene.second_id) == (first, second)
        and scene.from_ms <= to_ms
        and from_ms <= scene.to_ms
    )


async def _merged(conn: Any, guild_id: int, campaign_id: str) -> dict[str, str]:
    """Each merged entry to the entry it was merged into (one step; `_root` follows the chain)."""
    cur = await conn.execute(
        "SELECT id, merged_into FROM memory_entities WHERE guild_id = %s AND campaign_id = %s"
        " AND merged_into IS NOT NULL",
        (guild_id, campaign_id),
    )
    return {str(r["id"]): str(r["merged_into"]) for r in await cur.fetchall()}


def _root(merged: dict[str, str], entity_id: str) -> str:
    """The entry `entity_id` ended up as, following merges (a cycle stops where it started)."""
    seen = {entity_id}
    while entity_id in merged and merged[entity_id] not in seen:
        entity_id = merged[entity_id]
        seen.add(entity_id)
    return entity_id


async def _players(conn: Any, guild_id: int, campaign_id: str) -> dict[str, int]:
    """Each confirmed player character with a player, to who plays it (as `timebot.stamp` reads
    them). A merged-away or not-yet-confirmed character is not one."""
    cur = await conn.execute(
        "SELECT id, played_by FROM memory_entities WHERE guild_id = %s AND campaign_id = %s"
        " AND type = 'character' AND role = 'player_character' AND status = 'confirmed'"
        " AND played_by IS NOT NULL",
        (guild_id, campaign_id),
    )
    return {str(r["id"]): int(r["played_by"]) for r in await cur.fetchall()}


async def _clock(
    conn: Any, guild_id: int, campaign_id: str, characters: Sequence[str], until: int
) -> Clock:
    """The clock's rows up to `until` (Unix seconds), oldest first, in the order the clock itself
    reads them (`at`, then `seq`)."""
    cur = await conn.execute(
        "SELECT at, position IS NOT NULL AS grouped FROM game_clock_log"
        " WHERE guild_id = %s AND campaign_id = %s AND at <= %s ORDER BY at, seq",
        (guild_id, campaign_id, until),
    )
    main = [(int(r["at"]), bool(r["grouped"])) for r in await cur.fetchall()]
    cur = await conn.execute(
        "SELECT character_id, at, position FROM game_clock_group_log WHERE guild_id = %s"
        " AND campaign_id = %s AND character_id = ANY(%s) AND at <= %s ORDER BY at, seq",
        (guild_id, campaign_id, list(characters), until),
    )
    placed: dict[str, list[tuple[int, int | None]]] = {}
    for r in await cur.fetchall():
        position = None if r["position"] is None else int(r["position"])
        placed.setdefault(str(r["character_id"]), []).append((int(r["at"]), position))
    return Clock(main, placed)


def _kept(r: Any) -> Kept:
    return Kept(
        str(r["id"]), str(r["first_id"]), str(r["second_id"]), int(r["session_started_at"]),
        int(r["from_ms"]), int(r["to_ms"]), int(r["line_count"]),
    )  # fmt: skip


async def forget_session(conn: Any, guild_id: int, session_id: str) -> int:
    """A session's transcript is deleted: the scenes found in it go (they point at its lines). The
    DM's "Not private" marks stay: no words, no lines, only that the DM said so. In the caller's
    transaction; returns how many went."""
    cur = await conn.execute(
        "DELETE FROM memory_private_scenes p USING transcript_sessions s"
        " WHERE p.guild_id = %s AND s.guild_id = p.guild_id AND s.id = %s"
        " AND s.campaign_id = p.campaign_id AND p.session_started_at = s.started_at"
        " AND p.status = 'kept'",
        (guild_id, session_id),
    )
    return int(cur.rowcount or 0)


async def forget_speaker(
    conn: Any, guild_id: int, user_id: int, campaign_id: str | None = None
) -> int:
    """Someone's words are forgotten ("Stop recording me", "Delete my transcripts", a DM's removal
    of a speaker): every kept scene they spoke in goes, and a "Not private" mark loses their id.
    `campaign_id`: only that campaign's rows. In the caller's transaction; returns how many went."""
    cur = await conn.execute(
        "DELETE FROM memory_private_scenes WHERE guild_id = %s AND %s = ANY(speaker_ids)"
        " AND status = 'kept' AND (%s::text IS NULL OR campaign_id = %s)",
        (guild_id, user_id, campaign_id, campaign_id),
    )
    gone = int(cur.rowcount or 0)
    await conn.execute(
        "UPDATE memory_private_scenes SET speaker_ids = array_remove(speaker_ids, %s)"
        " WHERE guild_id = %s AND %s = ANY(speaker_ids) AND status = 'not_private'"
        " AND (%s::text IS NULL OR campaign_id = %s)",
        (user_id, guild_id, user_id, campaign_id, campaign_id),
    )
    return gone


async def keep_recorded(
    conn: Any, guild_id: int, campaign_id: str, recorded: Collection[int]
) -> None:
    """A campaign moved to another server (`guild_id` is the new one): only what cites people
    recorded there stays, as for the beings met in play. In the caller's transaction."""
    who = sorted(set(recorded))
    await conn.execute(
        "DELETE FROM memory_private_scenes WHERE guild_id = %s AND campaign_id = %s"
        " AND status = 'kept' AND NOT (speaker_ids <@ %s::bigint[])",
        (guild_id, campaign_id, who),
    )
    await conn.execute(
        "UPDATE memory_private_scenes SET speaker_ids = ARRAY(SELECT u FROM unnest(speaker_ids)"
        " AS u WHERE u = ANY(%s::bigint[]) ORDER BY u) WHERE guild_id = %s AND campaign_id = %s"
        " AND status = 'not_private' AND NOT (speaker_ids <@ %s::bigint[])",
        (who, guild_id, campaign_id, who),
    )


__all__ = [
    "LIST_MAX", "Kept", "PrivateScenes", "Written", "forget_session", "forget_speaker",
    "keep_recorded",
]  # fmt: skip
