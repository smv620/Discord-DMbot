"""What a merge moves outside the change log, remembered so Undo can hand it back (#2438).

A merge moves the merged-away name's private notes (`npc_notes`) and its D&D Beyond sheet
(`character_sheets`) to the kept name. Neither is in the change log: a note is its writer's own
and a sheet is refreshed by its player, so neither may be rolled back by a row-level undo that
would also throw away what they wrote since. Instead the merge saves the rows as they were
(`save`), and Undo puts back only what is still exactly as the merge left it (`restore`). A note
or sheet changed since stays where it is, and nothing here ever makes an Undo refuse."""

from __future__ import annotations

from typing import Any

from psycopg.types.json import Jsonb

from dmbot.db import Conn

_SHEET_COLUMNS = ("entity_id", "url", "sheet", "source", "fetched_at", "player_id")
_NOTE_COLUMNS = ("entry_id", "user_id", "name", "body", "updated_at", "orphaned_at")
# How a note joined another: the kept body, a blank line, the merged one's body.
_JOINER = "\n\n"


async def _rows(
    conn: Conn, table: str, columns: tuple[str, ...], key: str, ids: tuple[int, str], entry: str
) -> list[dict[str, Any]]:
    cur = await conn.execute(
        f"SELECT {', '.join(columns)} FROM {table}"
        f" WHERE guild_id = %s AND campaign_id = %s AND {key} = %s ORDER BY 1, 2",
        (*ids, entry),
    )
    return [dict(r) for r in await cur.fetchall()]


async def snapshot(
    conn: Conn, ids: tuple[int, str], keep_id: str, gone_id: str, keep_player: int | None
) -> dict[str, Any]:
    """The notes and sheets of both names, as they are now (call before the merge moves them).
    `keep_player`: who plays the kept entry, so a sheet left from an earlier player (one the merge
    drops, because nothing could show it) is told apart from the one in use."""
    gone_notes = await _rows(conn, "npc_notes", _NOTE_COLUMNS, "entry_id", ids, gone_id)
    keep_notes = {
        n["user_id"]: n
        for n in await _rows(conn, "npc_notes", _NOTE_COLUMNS, "entry_id", ids, keep_id)
    }
    gone_sheet = await _rows(conn, "character_sheets", _SHEET_COLUMNS, "entity_id", ids, gone_id)
    keep_sheet = await _rows(conn, "character_sheets", _SHEET_COLUMNS, "entity_id", ids, keep_id)
    return {
        "notes": [{"gone": n, "keep": keep_notes.get(n["user_id"])} for n in gone_notes],
        "gone_sheet": gone_sheet[0] if gone_sheet else None,
        "keep_sheet": keep_sheet[0] if keep_sheet else None,
        "keep_sheet_in_use": bool(keep_sheet) and keep_sheet[0]["player_id"] == keep_player,
    }


async def save(
    conn: Conn, ids: tuple[int, str], batch: int, keep_id: str, gone_id: str, before: dict[str, Any]
) -> None:
    """Remember a merge's snapshot under its batch, when there was anything to remember."""
    if not before["notes"] and before["gone_sheet"] is None and before["keep_sheet"] is None:
        return
    await conn.execute(
        "INSERT INTO merge_restores (guild_id, campaign_id, batch, gone_id, keep_id, before)"
        " VALUES (%s, %s, %s, %s, %s, %s)",
        (*ids, batch, gone_id, keep_id, Jsonb(before)),
    )


async def restore(conn: Conn, ids: tuple[int, str], batch: int) -> None:
    """Put back what a batch's merges moved, newest merge first, wherever it is still as the merge
    left it. Called after the batch's own rows are undone (the merged-away name is back)."""
    cur = await conn.execute(
        "SELECT gone_id, keep_id, before FROM merge_restores"
        " WHERE guild_id = %s AND campaign_id = %s AND batch = %s ORDER BY id DESC",
        (*ids, batch),
    )
    for found in await cur.fetchall():
        before = found["before"]
        await _restore_notes(conn, ids, found["keep_id"], found["gone_id"], before["notes"])
        await _restore_sheets(conn, ids, found["keep_id"], found["gone_id"], before)


async def _note(conn: Conn, ids: tuple[int, str], entry: str, user: int) -> dict[str, Any] | None:
    cur = await conn.execute(
        "SELECT body, updated_at, orphaned_at FROM npc_notes"
        " WHERE guild_id = %s AND campaign_id = %s AND entry_id = %s AND user_id = %s",
        (*ids, entry, user),
    )
    row = await cur.fetchone()
    return None if row is None else dict(row)


async def _restore_notes(
    conn: Conn, ids: tuple[int, str], keep_id: str, gone_id: str, notes: list[dict[str, Any]]
) -> None:
    for pair in notes:
        gone, keep = pair["gone"], pair["keep"]
        user = gone["user_id"]
        now_gone = await _note(conn, ids, gone_id, user)
        now_keep = await _note(conn, ids, keep_id, user)
        if keep is None:
            # it moved over whole: back it goes, if it is still the only copy and unedited
            if now_gone is None and now_keep is not None and now_keep["body"] == gone["body"]:
                await conn.execute(
                    "UPDATE npc_notes SET entry_id = %s, orphaned_at = %s"
                    " WHERE guild_id = %s AND campaign_id = %s AND entry_id = %s AND user_id = %s",
                    (gone_id, gone["orphaned_at"], *ids, keep_id, user),
                )
        elif now_gone is None:
            # it was joined onto the kept note: split again only if nobody wrote on it since
            joined = keep["body"] + _JOINER + gone["body"]
            if now_keep is not None and now_keep["body"] == joined:
                await conn.execute(
                    "UPDATE npc_notes SET body = %s, updated_at = %s, orphaned_at = %s"
                    " WHERE guild_id = %s AND campaign_id = %s AND entry_id = %s AND user_id = %s",
                    (keep["body"], keep["updated_at"], keep["orphaned_at"], *ids, keep_id, user),
                )
                await conn.execute(
                    "INSERT INTO npc_notes (guild_id, campaign_id, entry_id, user_id, name, body,"
                    " updated_at, orphaned_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                    (*ids, gone_id, user, gone["name"], gone["body"], gone["updated_at"],
                     gone["orphaned_at"]),
                )  # fmt: skip
        else:
            # too long to join, so it stayed apart and was marked: take the mark off again
            await conn.execute(
                "UPDATE npc_notes SET orphaned_at = %s"
                " WHERE guild_id = %s AND campaign_id = %s AND entry_id = %s AND user_id = %s",
                (gone["orphaned_at"], *ids, gone_id, user),
            )


def _same_sheet(row: dict[str, Any] | None, was: dict[str, Any]) -> bool:
    """`row` is `was`, whichever entry it is on."""
    return row is not None and all(row[c] == was[c] for c in _SHEET_COLUMNS if c != "entity_id")


async def _sheet(conn: Conn, ids: tuple[int, str], entry: str) -> dict[str, Any] | None:
    rows = await _rows(conn, "character_sheets", _SHEET_COLUMNS, "entity_id", ids, entry)
    return rows[0] if rows else None


async def _put_sheet(conn: Conn, ids: tuple[int, str], entry: str, was: dict[str, Any]) -> None:
    await conn.execute(
        "INSERT INTO character_sheets (guild_id, campaign_id, entity_id, url, sheet, source,"
        " fetched_at, player_id) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        (*ids, entry, was["url"], None if was["sheet"] is None else Jsonb(was["sheet"]),
         was["source"], was["fetched_at"], was["player_id"]),
    )  # fmt: skip


async def _restore_sheets(
    conn: Conn, ids: tuple[int, str], keep_id: str, gone_id: str, before: dict[str, Any]
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
