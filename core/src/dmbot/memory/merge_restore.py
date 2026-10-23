"""What a merge moves outside the change log, remembered so Undo can hand it back (#2438).

A merge moves the merged-away name's private notes (`npc_notes`) and its D&D Beyond sheet
(`character_sheets`) to the kept name. Neither is in the change log: a note is its writer's own
and a sheet is refreshed by its player, so neither may be rolled back by a row-level undo that
would also throw away what they wrote since. Instead the merge saves what it needs to put them
back (`snapshot`, `save`), and Undo puts back only what is still exactly as the merge left it
(`restore`): a note or sheet changed since stays where it is, and nothing here ever makes an Undo
refuse.

A note's words are never copied here (a person who deletes their notes would otherwise leave a
copy behind): only its length and times. A joined note is split again at the length the kept note
had, and only if its time shows nobody has written on it since. Undoing an Undo (redo) merges
again, so the notes and sheet follow it: `restore` leaves a "redo" row under the Undo's batch,
and `redo` runs the same moves when that batch is undone."""

from __future__ import annotations

import time
from typing import Any

from psycopg.types.json import Jsonb

from dmbot.db import Conn

Ids = tuple[int, str]

_SHEET_COLUMNS = (
    "entity_id", "url", "sheet", "source", "fetched_at", "player_id", "ask_mismatch",
)  # fmt: skip
# How a note joined another: the kept body, a blank line, the merged one's body.
_JOINER = "\n\n"
NOTE_MAX = 5000  # a note's most characters (the table's own check)
UNDO, REDO = "undo", "redo"


async def move_notes(conn: Conn, ids: tuple[int, str], keep_id: str, gone_id: str) -> None:
    """A person's private notes on the merged entry go to the one kept (#2265, #2298): every note,
    the ones already marked "no longer listed" too. A person with a note on both keeps both, the
    kept entry's first, joined. A note keeps its place in the 30 days it was given: one already
    marked keeps its clock (the older of the two when joined), and the clock is never restarted. A
    marked note is unmarked only if the kept entry is listed (the roster has it).

    If the two together would be over the 5,000 characters a note may hold, the merged entry's note
    is not joined: it stays as a note on a name no longer listed (30 days, in the person's "Notes
    on names no longer listed", where they can copy it), so no word is lost. It is marked now
    only if it was not marked already. Nothing of a note is read here beyond its length, a note
    never leaves its writer's rows, and nothing is logged here: `snapshot` keeps only
    lengths and times, so Undo can move them back (#2438)."""
    now = int(time.time())
    cur = await conn.execute(
        "SELECT EXISTS (SELECT 1 FROM npc_roster WHERE guild_id = %s AND campaign_id = %s"
        " AND entry_id = %s) AS listed",
        (*ids, keep_id),
    )
    row = await cur.fetchone()
    listed = bool(row and row["listed"])
    # too long to join: the merged note stays apart, marked as no longer listed (a clock already
    # running keeps running)
    await conn.execute(
        "UPDATE npc_notes g SET orphaned_at = COALESCE(g.orphaned_at, %s) FROM npc_notes k"
        " WHERE g.guild_id = %s AND g.campaign_id = %s AND g.entry_id = %s"
        " AND k.guild_id = g.guild_id AND k.campaign_id = g.campaign_id AND k.entry_id = %s"
        " AND k.user_id = g.user_id AND char_length(k.body) + 2 + char_length(g.body) > 5000",
        (now, *ids, gone_id, keep_id),
    )
    # The notes that fit are decided from the rows as they are now, in one statement: the merged
    # note is deleted and its words are appended to the kept one (a separate UPDATE first would
    # make the kept note longer, and the delete's length check would then miss the twin).
    await conn.execute(
        "WITH joined AS ("
        " DELETE FROM npc_notes g USING npc_notes k WHERE g.guild_id = %s AND g.campaign_id = %s"
        " AND g.entry_id = %s AND k.guild_id = g.guild_id AND k.campaign_id = g.campaign_id"
        " AND k.entry_id = %s AND k.user_id = g.user_id"
        " AND char_length(k.body) + 2 + char_length(g.body) <= 5000"
        " RETURNING g.user_id, g.body, g.updated_at, g.orphaned_at)"
        " UPDATE npc_notes k SET body = k.body || E'\\n\\n' || j.body,"
        " updated_at = greatest(k.updated_at, j.updated_at),"
        " orphaned_at = CASE WHEN %s THEN NULL WHEN k.orphaned_at IS NULL THEN j.orphaned_at"
        "  WHEN j.orphaned_at IS NULL THEN k.orphaned_at"
        "  ELSE least(k.orphaned_at, j.orphaned_at) END"
        " FROM joined j WHERE k.guild_id = %s AND k.campaign_id = %s AND k.entry_id = %s"
        " AND k.user_id = j.user_id",
        (*ids, gone_id, keep_id, listed, *ids, keep_id),
    )
    # a note with no twin moves over, whether it was marked or not
    await conn.execute(
        "UPDATE npc_notes SET entry_id = %s,"
        " orphaned_at = CASE WHEN %s THEN NULL ELSE orphaned_at END"
        " WHERE guild_id = %s AND campaign_id = %s AND entry_id = %s"
        " AND NOT EXISTS (SELECT 1 FROM npc_notes x WHERE x.guild_id = %s AND x.campaign_id = %s"
        "  AND x.entry_id = %s AND x.user_id = npc_notes.user_id)",
        (keep_id, listed, *ids, gone_id, *ids, keep_id),
    )


async def move_sheets(conn: Conn, ids: tuple[int, str], keep_id: str, gone_id: str) -> None:
    """A character's D&D Beyond sheet (#723) goes with it, unless the kept entry has its own:
    then the merged one's is forgotten (nothing could show or remove it). The kept entry's own
    sheet counts only if it's its player's (a sheet left from an earlier player is shown nowhere):
    drop it first, so it can't beat a visible one."""
    await conn.execute(
        "DELETE FROM character_sheets s USING memory_entities e"
        " WHERE s.guild_id = %s AND s.campaign_id = %s AND s.entity_id = %s"
        " AND e.guild_id = s.guild_id AND e.campaign_id = s.campaign_id"
        " AND e.id = s.entity_id AND s.player_id IS DISTINCT FROM e.played_by",
        (*ids, keep_id),
    )
    await conn.execute(
        "DELETE FROM character_sheets"
        " WHERE guild_id = %s AND campaign_id = %s AND entity_id = %s"
        " AND EXISTS (SELECT 1 FROM character_sheets"
        "  WHERE guild_id = %s AND campaign_id = %s AND entity_id = %s)",
        (*ids, gone_id, *ids, keep_id),
    )
    await conn.execute(
        "UPDATE character_sheets SET entity_id = %s"
        " WHERE guild_id = %s AND campaign_id = %s AND entity_id = %s"
        " AND NOT EXISTS (SELECT 1 FROM character_sheets"
        "  WHERE guild_id = %s AND campaign_id = %s AND entity_id = %s)",
        (keep_id, *ids, gone_id, *ids, keep_id),
    )


# ---- what to remember, and putting it back -------------------------------------------------


async def _rows(
    conn: Conn, table: str, columns: tuple[str, ...], ids: Ids, key: str, entry: str
) -> list[dict[str, Any]]:
    cur = await conn.execute(
        f"SELECT {', '.join(columns)} FROM {table}"
        f" WHERE guild_id = %s AND campaign_id = %s AND {key} = %s ORDER BY 1, 2",
        (*ids, entry),
    )
    return [dict(r) for r in await cur.fetchall()]


async def snapshot(
    conn: Conn, ids: Ids, keep_id: str, gone_id: str, keep_player: int | None
) -> dict[str, Any]:
    """What the notes and sheets of both names look like now (call before the merge moves them):
    lengths and times for notes, never their words. `keep_player`: who plays the kept entry, so a
    sheet left from an earlier player (one the merge drops, because nothing could show it) is told
    apart from the one in use."""
    cols = "user_id, name, char_length(body) AS size, updated_at, orphaned_at"
    gone_notes = await _query(conn, ids, gone_id, cols)
    keep_notes = {n["user_id"]: n for n in await _query(conn, ids, keep_id, cols)}
    notes = []
    for g in gone_notes:
        k = keep_notes.get(g["user_id"])
        joined = k is not None and k["size"] + len(_JOINER) + g["size"] <= NOTE_MAX
        notes.append(
            {"gone": g, "keep": k, "how": "joined" if joined else "apart" if k else "moved"}
        )
    gone_sheet = await _rows(conn, "character_sheets", _SHEET_COLUMNS, ids, "entity_id", gone_id)
    keep_sheet = await _rows(conn, "character_sheets", _SHEET_COLUMNS, ids, "entity_id", keep_id)
    return {
        "notes": notes,
        "gone_sheet": gone_sheet[0] if gone_sheet else None,
        "keep_sheet": keep_sheet[0] if keep_sheet else None,
        "keep_sheet_in_use": bool(keep_sheet) and keep_sheet[0]["player_id"] == keep_player,
    }


async def _query(conn: Conn, ids: Ids, entry: str, cols: str) -> list[dict[str, Any]]:
    cur = await conn.execute(
        f"SELECT {cols} FROM npc_notes"
        " WHERE guild_id = %s AND campaign_id = %s AND entry_id = %s ORDER BY user_id",
        (*ids, entry),
    )
    return [dict(r) for r in await cur.fetchall()]


async def save(
    conn: Conn,
    ids: Ids,
    batch: int,
    keep_id: str,
    gone_id: str,
    before: dict[str, Any] | None,
    kind: str = UNDO,
) -> None:
    """Remember a merge under its batch (`before`: its snapshot), when there was anything to
    remember. A `redo` row has none: the moves are simply made again."""
    if before is not None and not (
        before["notes"] or before["gone_sheet"] is not None or before["keep_sheet"] is not None
    ):
        return
    await conn.execute(
        "INSERT INTO merge_restores (guild_id, campaign_id, batch, kind, gone_id, keep_id, before)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (*ids, batch, kind, gone_id, keep_id, Jsonb(before or {})),
    )


async def restore(conn: Conn, ids: Ids, batch: int, undo_batch: int | None) -> None:
    """Put back what a batch's merges moved, newest merge first, wherever it is still as the
    merge left it, then forget the record (the batch can't be undone twice). Called after the
    batch's own rows are undone (the merged-away name is back). `undo_batch`: the Undo's own
    batch, which gets a "redo" row for each merge so undoing the Undo moves them again."""
    cur = await conn.execute(
        "SELECT gone_id, keep_id, before FROM merge_restores"
        " WHERE guild_id = %s AND campaign_id = %s AND batch = %s AND kind = %s ORDER BY id DESC",
        (*ids, batch, UNDO),
    )
    found = await cur.fetchall()
    for row in found:
        before = row["before"]
        await _restore_notes(conn, ids, row["keep_id"], row["gone_id"], before["notes"])
        await _restore_sheets(conn, ids, row["keep_id"], row["gone_id"], before)
        if undo_batch is not None:
            await save(conn, ids, undo_batch, row["keep_id"], row["gone_id"], None, REDO)
    if found:
        await conn.execute(
            "DELETE FROM merge_restores WHERE guild_id = %s AND campaign_id = %s"
            " AND batch = %s AND kind = %s",
            (*ids, batch, UNDO),
        )


async def redo(conn: Conn, ids: Ids, batch: int, new_batch: int | None) -> None:
    """Undoing an Undo merges again: move the notes and sheet over once more, as the merge did,
    and remember it under this write's own batch so that can be undone in turn."""
    cur = await conn.execute(
        "SELECT gone_id, keep_id FROM merge_restores"
        " WHERE guild_id = %s AND campaign_id = %s AND batch = %s AND kind = %s ORDER BY id",
        (*ids, batch, REDO),
    )
    found = await cur.fetchall()
    for row in found:
        keep_id, gone_id = row["keep_id"], row["gone_id"]
        cur = await conn.execute(
            "SELECT played_by FROM memory_entities WHERE guild_id = %s AND campaign_id = %s"
            " AND id = %s",
            (*ids, keep_id),
        )
        kept = await cur.fetchone()
        before = await snapshot(conn, ids, keep_id, gone_id, kept["played_by"] if kept else None)
        await move_sheets(conn, ids, keep_id, gone_id)
        await move_notes(conn, ids, keep_id, gone_id)
        if new_batch is not None:
            await save(conn, ids, new_batch, keep_id, gone_id, before)
    if found:
        await conn.execute(
            "DELETE FROM merge_restores WHERE guild_id = %s AND campaign_id = %s"
            " AND batch = %s AND kind = %s",
            (*ids, batch, REDO),
        )


async def _note(conn: Conn, ids: Ids, entry: str, user: int) -> dict[str, Any] | None:
    cur = await conn.execute(
        "SELECT body, updated_at, orphaned_at FROM npc_notes"
        " WHERE guild_id = %s AND campaign_id = %s AND entry_id = %s AND user_id = %s FOR UPDATE",
        (*ids, entry, user),
    )
    row = await cur.fetchone()
    return None if row is None else dict(row)


async def _restore_notes(
    conn: Conn, ids: Ids, keep_id: str, gone_id: str, notes: list[dict[str, Any]]
) -> None:
    for pair in notes:
        gone, keep, how = pair["gone"], pair["keep"], pair["how"]
        user = gone["user_id"]
        now_gone = await _note(conn, ids, gone_id, user)
        now_keep = await _note(conn, ids, keep_id, user)
        if now_keep is None:
            continue  # deleted since: nothing to give back
        if how == "moved":
            # it moved over whole: back it goes, if it is the only copy and nobody wrote on it
            if now_gone is None and now_keep["updated_at"] == gone["updated_at"]:
                await conn.execute(
                    "UPDATE npc_notes SET entry_id = %s, orphaned_at = %s"
                    " WHERE guild_id = %s AND campaign_id = %s AND entry_id = %s AND user_id = %s",
                    (gone_id, gone["orphaned_at"], *ids, keep_id, user),
                )
        elif how == "joined":
            # joined onto the kept note: split again at the length it had, unless written on since
            body = now_keep["body"]
            cut = keep["size"]
            untouched = (
                now_gone is None
                and now_keep["updated_at"] == max(keep["updated_at"], gone["updated_at"])
                and len(body) == cut + len(_JOINER) + gone["size"]
                and body[cut : cut + len(_JOINER)] == _JOINER
            )
            if untouched:
                await conn.execute(
                    "UPDATE npc_notes SET body = %s, updated_at = %s, orphaned_at = %s"
                    " WHERE guild_id = %s AND campaign_id = %s AND entry_id = %s AND user_id = %s",
                    (body[:cut], keep["updated_at"], keep["orphaned_at"], *ids, keep_id, user),
                )
                await conn.execute(
                    "INSERT INTO npc_notes (guild_id, campaign_id, entry_id, user_id, name, body,"
                    " updated_at, orphaned_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                    (*ids, gone_id, user, gone["name"], body[cut + len(_JOINER) :],
                     gone["updated_at"], gone["orphaned_at"]),
                )  # fmt: skip
        elif now_gone is not None and now_gone["updated_at"] == gone["updated_at"]:
            # too long to join, so it stayed apart and was marked: take the mark off again
            await conn.execute(
                "UPDATE npc_notes SET orphaned_at = %s"
                " WHERE guild_id = %s AND campaign_id = %s AND entry_id = %s AND user_id = %s",
                (gone["orphaned_at"], *ids, gone_id, user),
            )


def _same_sheet(row: dict[str, Any] | None, was: dict[str, Any]) -> bool:
    """`row` is `was`, whichever entry it is on."""
    return row is not None and all(row[c] == was[c] for c in _SHEET_COLUMNS if c != "entity_id")


async def _sheet(conn: Conn, ids: Ids, entry: str) -> dict[str, Any] | None:
    cur = await conn.execute(
        f"SELECT {', '.join(_SHEET_COLUMNS)} FROM character_sheets"
        " WHERE guild_id = %s AND campaign_id = %s AND entity_id = %s FOR UPDATE",
        (*ids, entry),
    )
    row = await cur.fetchone()
    return None if row is None else dict(row)


async def _put_sheet(conn: Conn, ids: Ids, entry: str, was: dict[str, Any]) -> None:
    await conn.execute(
        "INSERT INTO character_sheets (guild_id, campaign_id, entity_id, url, sheet, source,"
        " fetched_at, player_id, ask_mismatch) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (*ids, entry, was["url"], None if was["sheet"] is None else Jsonb(was["sheet"]),
         was["source"], was["fetched_at"], was["player_id"], was["ask_mismatch"]),
    )  # fmt: skip


async def _restore_sheets(
    conn: Conn, ids: Ids, keep_id: str, gone_id: str, before: dict[str, Any]
) -> None:
    gone, keep = before["gone_sheet"], before["keep_sheet"]
    in_use = before["keep_sheet_in_use"]
    if gone is not None and await _sheet(conn, ids, gone_id) is None:
        now_keep = await _sheet(conn, ids, keep_id)
        if (keep is None or not in_use) and _same_sheet(now_keep, gone):
            # it moved over: back it goes
            await conn.execute(
                "UPDATE character_sheets SET entity_id = %s"
                " WHERE guild_id = %s AND campaign_id = %s AND entity_id = %s",
                (gone_id, *ids, keep_id),
            )
        elif keep is not None and in_use and _same_sheet(now_keep, keep):
            # the kept name's own sheet won and the other was forgotten
            await _put_sheet(conn, ids, gone_id, gone)
    # a sheet left from an earlier player was dropped (nothing could show it): it was the kept
    # entry's own, so it returns there when nothing has taken its place
    if keep is not None and not in_use and await _sheet(conn, ids, keep_id) is None:
        await _put_sheet(conn, ids, keep_id, keep)
