"""A campaign's transcripts in the extra files of its backup (#2552).

Transcript lines are by far the biggest data a campaign has (about 0.6 MB of text for a four-hour
session), so they would pass the one file's 25 MB limit after some forty sessions. The backup is
therefore a set: `campaign.json.gz` as before, plus `transcripts` files, each holding whole
sessions with their lines, suggested fixes, respelling history and import record. Each file is read
on its own, so a restore's memory stays bounded however long the campaign has run. A backup made
before this, or one with no transcripts, is a single file and restores as it always did.

What a file holds is what was there, unabridged: the raw `heard` words and the DM sidebar lines
with their lineage, as the shared-transcript decision says (CLAUDE.md). There is no consent filter
on a restore (a backup restores what was there). Ids are made again (a session's id, a line's id
are unique across the whole database, so restoring next to the original cannot clash), and the
rows that point at them are rewritten: a respelling points at its line by its place in the
session, not by an id.

Row contents are validated by the database itself (`jsonb_populate_recordset` against the table's
own types and CHECKs); this module checks the shape of the file and that every key is a real column,
so a forged file can write nothing the tables would not take from the bot.
"""

from __future__ import annotations

import gzip
import io
import json
import uuid
import zlib
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import Any

from psycopg.types.json import Jsonb

from dmbot.campaigns.models import CampaignError
from dmbot.db import Conn

PART_FORMAT = "dmbot-transcripts"
PART_VERSION = 1
PART_TEXT_BYTES = 16 * 1024 * 1024  # text a part is filled to; a restore reads up to 25 MB
PART_READ_BYTES = 25 * 1024 * 1024  # the most a part may unpack to
SESSIONS_MAX = 5000  # sessions in one part
LINES_MAX = 400_000  # lines in one session
DAMAGED = "This transcripts file is damaged. Use the files DMbot made, all of them."
TOO_BIG_PART = "This transcripts file is too big for DMbot to read."
# A restore command takes at most 25 files (Discord's limit on a command's boxes): the copy, and
# these. A campaign that needs more is not copied, and the DM is told so (#2606).
RESTORE_BOXES = 24
SESSION_TOO_BIG = "This campaign has a session too big for DMbot to copy yet. Nothing was lost."
TOO_MANY_PARTS = (
    f"This campaign's transcripts need more than {RESTORE_BOXES} files, which is the most a "
    "restore can take at once, so DMbot can't copy it yet. Nothing was lost. You can shorten "
    "the time transcripts are kept (Keep transcripts for) and copy again."
)

# What each row's JSON leaves out: ids that are made again and the scope columns.
_SESSION_DROP = ("id", "guild_id", "campaign_id")
_LINE_DROP = ("id", "guild_id", "session_id")
_SUGGESTION_DROP = ("id", "guild_id", "campaign_id", "session_id")
_RESPELL_DROP = ("id", "guild_id", "campaign_id", "session_id", "line_id")
_IMPORT_DROP = ("guild_id", "campaign_id", "session_id")
_SESSION_KEYS = ("session", "lines", "suggestions", "respells", "import")


def _without(alias: str, drop: Sequence[str]) -> str:
    minus = "".join(f" - '{c}'" for c in drop)
    return f"to_jsonb({alias}){minus}"  # (aliases and columns here are constants, not input)


@dataclass(slots=True)
class LeftOut:
    """What a restore did not bring back because the person had erased it (#2605): lines of
    people who used "Delete my transcripts" after the copy was made. A count only."""

    lines: int = 0


class TranscriptParts:
    """Export and import of transcripts as backup parts."""

    async def sessions(self, conn: Conn, guild_id: int, campaign_id: str) -> AsyncIterator[str]:
        """One JSON text for each session of the campaign, oldest first, read one at a time."""
        cur = await conn.execute(
            "SELECT id FROM transcript_sessions WHERE guild_id = %s AND campaign_id = %s"
            " ORDER BY started_at",
            (guild_id, campaign_id),
        )
        for row in await cur.fetchall():
            yield json.dumps(await self._one(conn, guild_id, campaign_id, str(row["id"])))

    async def _one(self, conn: Conn, guild_id: int, campaign_id: str, sid: str) -> dict[str, Any]:
        cur = await conn.execute(
            f"SELECT {_without('t', _SESSION_DROP)} AS r"
            " FROM transcript_sessions t WHERE guild_id = %s AND id = %s",
            (guild_id, sid),
        )
        session = (await cur.fetchone() or {"r": {}})["r"]
        cur = await conn.execute(
            f"SELECT id, {_without('l', _LINE_DROP)} AS r"
            " FROM transcript_lines l WHERE guild_id = %s AND session_id = %s ORDER BY id",
            (guild_id, sid),
        )
        found = await cur.fetchall()
        place = {int(r["id"]): n for n, r in enumerate(found)}
        lines = [r["r"] for r in found]
        cur = await conn.execute(
            f"SELECT {_without('s', _SUGGESTION_DROP)} AS r"
            " FROM transcript_suggestions s WHERE guild_id = %s AND session_id = %s ORDER BY id",
            (guild_id, sid),
        )
        suggestions = [r["r"] for r in await cur.fetchall()]
        cur = await conn.execute(
            f"SELECT line_id, {_without('p', _RESPELL_DROP)} AS r"
            " FROM transcript_respells p WHERE guild_id = %s AND session_id = %s ORDER BY id",
            (guild_id, sid),
        )
        respells = [
            {**r["r"], "line": place[int(r["line_id"])]}
            for r in await cur.fetchall()
            if int(r["line_id"]) in place
        ]
        cur = await conn.execute(
            f"SELECT {_without('i', _IMPORT_DROP)} AS r"
            " FROM session_imports i WHERE guild_id = %s AND session_id = %s",
            (guild_id, sid),
        )
        row = await cur.fetchone()
        return {
            "session": session,
            "lines": lines,
            "suggestions": suggestions,
            "respells": respells,
            "import": None if row is None else row["r"],
        }

    # ---- reading a file -------------------------------------------------------------------

    @staticmethod
    def check(data: object) -> dict[str, Any]:
        """The shape of one part, checked. Pure. Raises CampaignError."""
        if not isinstance(data, dict) or data.get("format") != PART_FORMAT:
            raise CampaignError(DAMAGED)
        version, backup, part = data.get("version"), data.get("backup"), data.get("part")
        if (
            type(version) is not int
            or version < 1
            or version > PART_VERSION
            or not isinstance(backup, str)
            or not 8 <= len(backup) <= 64
            or type(part) is not int
            or part < 1
        ):
            raise CampaignError(DAMAGED)
        if "guild" in data and (type(data["guild"]) is not int or data["guild"] < 1):
            raise CampaignError(DAMAGED)
        sessions = data.get("sessions")
        if not isinstance(sessions, list) or len(sessions) > SESSIONS_MAX:
            raise CampaignError(DAMAGED)
        for s in sessions:
            if not isinstance(s, dict) or set(s) != set(_SESSION_KEYS):
                raise CampaignError(DAMAGED)
            if not isinstance(s["session"], dict) or not _keys_ok(s["session"]):
                raise CampaignError(DAMAGED)
            for name in ("lines", "suggestions", "respells"):
                rows = s[name]
                if (
                    not isinstance(rows, list)
                    or len(rows) > LINES_MAX
                    or not all(isinstance(r, dict) and _keys_ok(r) for r in rows)
                ):
                    raise CampaignError(DAMAGED)
            if s["import"] is not None and (
                not isinstance(s["import"], dict) or not _keys_ok(s["import"])
            ):
                raise CampaignError(DAMAGED)
            count = len(s["lines"])
            for r in s["respells"]:
                line = r.get("line")
                if type(line) is not int or not 0 <= line < count:
                    raise CampaignError(DAMAGED)
        return data

    # ---- writing --------------------------------------------------------------------------

    async def clear(self, conn: Conn, guild_id: int, campaign_id: str) -> None:
        # (lines, suggestions, respells and the import record go with their session)
        await conn.execute(
            "DELETE FROM transcript_sessions WHERE guild_id = %s AND campaign_id = %s",
            (guild_id, campaign_id),
        )

    async def load(
        self,
        conn: Conn,
        guild_id: int,
        campaign_id: str,
        part: dict[str, Any],
        left_out: LeftOut | None = None,
    ) -> None:
        """Write one checked part. Every key must be a column of its table (else the file is
        damaged); a column the file does not have takes its default, so a copy made by an older
        DMbot restores into a newer table.

        A person who erased their transcripts (in the server the copy came from, or in this one)
        after a session began gets none of that session's lines back: their lines, the sidebar
        lines that carry their id, their suggested fixes and the respellings of those lines
        (#2605). The count goes to `left_out`."""
        allowed = await _columns(conn)
        erased = await self._erased(conn, guild_id, part)
        for s in part["sessions"]:
            if erased:
                s = _without_erased(s, erased, left_out)
            sid = uuid.uuid4().hex
            await self._insert(
                conn, "transcript_sessions", allowed, [s["session"]],
                {"id": sid, "guild_id": guild_id, "campaign_id": campaign_id},
            )  # fmt: skip
            ids: list[int] = []
            for start in range(0, len(s["lines"]), 1000):
                ids += await self._insert(
                    conn, "transcript_lines", allowed, s["lines"][start : start + 1000],
                    {"guild_id": guild_id, "session_id": sid}, returning="id",
                )  # fmt: skip
            if len(ids) != len(s["lines"]):
                raise CampaignError(DAMAGED)
            await self._insert(
                conn, "transcript_suggestions", allowed, s["suggestions"],
                {"guild_id": guild_id, "campaign_id": campaign_id, "session_id": sid},
            )  # fmt: skip
            for start in range(0, len(s["respells"]), 1000):
                batch = [
                    {**{k: v for k, v in r.items() if k != "line"}, "line_id": ids[r["line"]]}
                    for r in s["respells"][start : start + 1000]
                ]
                await self._insert(
                    conn, "transcript_respells", allowed, batch,
                    {"guild_id": guild_id, "campaign_id": campaign_id, "session_id": sid},
                )  # fmt: skip
            if s["import"] is not None:
                await self._insert(
                    conn, "session_imports", allowed, [s["import"]],
                    {"guild_id": guild_id, "campaign_id": campaign_id, "session_id": sid},
                )  # fmt: skip

    @staticmethod
    async def _erased(conn: Conn, guild_id: int, part: dict[str, Any]) -> dict[int, int]:
        """user -> when they last erased their transcripts, for the speakers in this part, in this
        server and in the one the copy came from (a door answers for one server at a time)."""
        speakers = sorted(
            {
                int(r["user_id"])
                for s in part["sessions"]
                for name in ("lines", "suggestions")
                for r in s[name]
                if type(r.get("user_id")) is int
            }
        )
        if not speakers:
            return {}
        source = part.get("guild")
        servers = {guild_id, source} if type(source) is int else {guild_id}
        out: dict[int, int] = {}
        for server in sorted(servers):
            cur = await conn.execute(
                "SELECT user_id, erased_at FROM dmbot_erased_speakers(%s, %s)", (server, speakers)
            )
            for r in await cur.fetchall():
                out[int(r["user_id"])] = max(out.get(int(r["user_id"]), 0), int(r["erased_at"]))
        return out

    @staticmethod
    async def _insert(
        conn: Conn,
        table: str,
        allowed: dict[str, set[str]],
        rows: list[dict[str, Any]],
        scope: dict[str, Any],
        returning: str | None = None,
    ) -> list[int]:
        """`rows` into `table`, each with `scope` (the ids made here) put over what the file
        says. The database's own types and CHECKs validate every value."""
        if not rows:
            return []
        keys = set(rows[0])
        if any(set(r) != keys for r in rows):  # one shape for a batch: what one DMbot wrote
            raise CampaignError(DAMAGED)
        if not keys <= allowed[table] or keys & set(scope):
            raise CampaignError(DAMAGED)
        columns = [*scope, *sorted(keys)]
        names = ", ".join(columns)
        picked = ", ".join(f"r.{c}" for c in sorted(keys))
        fixed = ", ".join(f"%s::{_SCOPE_TYPES[c]}" for c in scope)
        cur = await conn.execute(
            f"INSERT INTO {table} ({names}) SELECT {fixed}{', ' if keys else ''}{picked}"
            f" FROM jsonb_populate_recordset(NULL::{table}, %s) WITH ORDINALITY AS r"
            f" ORDER BY r.ordinality" + (f" RETURNING {returning}" if returning else ""),
            (*scope.values(), Jsonb(rows)),
        )
        if returning is None:
            return []
        return [int(r[returning]) for r in await cur.fetchall()]


def _without_erased(
    s: dict[str, Any], erased: dict[int, int], left_out: LeftOut | None
) -> dict[str, Any]:
    """One session of a part with the erased people's rows taken out (#2605): only people who
    erased after the session began lose it (a session after they came back stays whole)."""
    began = s["session"].get("started_at")
    gone = {u for u, at in erased.items() if type(began) is int and began <= at}
    if not gone:
        return s
    keep = [i for i, r in enumerate(s["lines"]) if r.get("user_id") not in gone]
    if len(keep) == len(s["lines"]) and not any(r.get("user_id") in gone for r in s["suggestions"]):
        return s
    place = {old: new for new, old in enumerate(keep)}
    lines = [s["lines"][i] for i in keep]
    if left_out is not None:
        left_out.lines += len(s["lines"]) - len(lines)
    heard = [r for r in lines if r.get("sidebar") is None]
    session = dict(s["session"])
    if "line_count" in session:
        session["line_count"] = len(heard)
    if "speakers" in session:
        session["speakers"] = sorted({r["user_id"] for r in heard if "user_id" in r})
    if isinstance(session.get("speaker_names"), dict):
        session["speaker_names"] = {
            k: v for k, v in session["speaker_names"].items() if k not in {str(u) for u in gone}
        }
    return {
        **s,
        "session": session,
        "lines": lines,
        "suggestions": [r for r in s["suggestions"] if r.get("user_id") not in gone],
        "respells": [{**r, "line": place[r["line"]]} for r in s["respells"] if r["line"] in place],
    }


_SCOPE_TYPES = {"id": "text", "guild_id": "bigint", "campaign_id": "text", "session_id": "text"}


def _keys_ok(row: dict[str, Any]) -> bool:
    return all(isinstance(k, str) and k.isidentifier() for k in row)


async def _columns(conn: Conn) -> dict[str, set[str]]:
    """The columns a part may write, for each table: all but the ones made here (the id, the
    campaign and session columns), which the file never carries."""
    cur = await conn.execute(
        "SELECT table_name, column_name FROM information_schema.columns"
        " WHERE table_schema = current_schema() AND table_name = ANY(%s)"
        " AND is_identity = 'NO'",
        (list(_TABLES),),
    )
    out: dict[str, set[str]] = {t: set() for t in _TABLES}
    for r in await cur.fetchall():
        name = str(r["column_name"])
        if name not in _ALWAYS_MADE_HERE:
            out[str(r["table_name"])].add(name)
    out["transcript_respells"].add("line_id")  # (written here from the line's place, see `load`)
    return out


_TABLES = (
    "transcript_sessions",
    "transcript_lines",
    "transcript_suggestions",
    "transcript_respells",
    "session_imports",
)
_ALWAYS_MADE_HERE = frozenset({"id", "guild_id", "campaign_id", "session_id", "line_id"})


# ---- the files ---------------------------------------------------------------------------


def encode_part(
    backup_id: str, number: int, sessions: Sequence[str], guild_id: int | None = None
) -> bytes:
    """One part as a gzip file: `sessions` are the JSON texts `TranscriptParts.sessions` made."""
    head = json.dumps(
        {"format": PART_FORMAT, "version": PART_VERSION, "backup": backup_id, "part": number}
        | ({} if guild_id is None else {"guild": guild_id})  # (where the copy came from, #2605)
    )
    out = io.BytesIO()
    with gzip.GzipFile(fileobj=out, mode="wb", compresslevel=6, mtime=0) as packed:
        packed.write(head[:-1].encode() + b', "sessions": [')
        packed.write(",".join(sessions).encode("utf-8"))
        packed.write(b"]}")
    return out.getvalue()


def decode_part(raw: bytes) -> dict[str, Any]:
    """Unpack and check an uploaded transcripts file, refusing a big or damaged one. Pure and
    CPU-heavy: run it off the event loop."""
    if len(raw) > PART_READ_BYTES:
        raise CampaignError(TOO_BIG_PART)
    unpacker = zlib.decompressobj(wbits=16 + zlib.MAX_WBITS)
    try:
        text = unpacker.decompress(raw, PART_READ_BYTES + 1)
    except zlib.error as exc:
        raise CampaignError(DAMAGED) from exc
    if len(text) > PART_READ_BYTES:
        raise CampaignError(TOO_BIG_PART)
    if not unpacker.eof or unpacker.unused_data:
        raise CampaignError(DAMAGED)
    try:
        data = json.loads(text)
    except (ValueError, RecursionError) as exc:
        raise CampaignError(DAMAGED) from exc
    return TranscriptParts.check(data)
