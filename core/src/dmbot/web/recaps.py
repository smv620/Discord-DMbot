"""The DM's recap page, as an API (#1382; docs/PLAN.md, "Cinematic recaps"): list, read, edit,
Post, Try again and Don't post.

The recap is a job the bot runs after the session and emails the DM about; this is where the DM
reads it, changes it and decides. Everything is read and written under the website's database
role, one server at a time, the servers in this session's list only. A row-level rule
(`web_dm_recaps`) shows a recap only to a DM of its campaign, so a player, or a DM of another
campaign, gets "not found" for it, as for a recap that does not exist.

Nothing is posted from here. Post only marks the recap `posting`; the bot, which holds the
Discord token, checks again (the plan, who is still recorded, secret names read fresh) and sends
it. Edits are limited in size here; the secret-name check is the bot's, at Post, since the
website's role cannot read the campaign's names.
"""

from __future__ import annotations

import secrets
from typing import Any

from dmbot.consent import TERMS_VERSION
from dmbot.db import Conn, Database
from dmbot.recaps import cues
from dmbot.recaps.allowance import month_bounds
from dmbot.recaps.approval import CHOICES, TEXT, text_hash
from dmbot.recaps.draft import BULLET_MAX, HOUSEKEEPING_BULLETS, SUMMARY_MAX
from dmbot.recaps.notify import job_ref, parse_ref
from dmbot.recaps.store import MAX_TRIES
from dmbot.recaps.text import names_a_secret, strip_cues
from dmbot.web.me import MAX_GUILDS
from dmbot.web.sessions import Session

LIST_LIMIT = 20

_COLUMNS = (
    "r.guild_id, r.campaign_id, r.session_at, r.state, r.status, r.title, r.summary,"
    " r.housekeeping, r.music, r.tries, r.last_error, r.game_day, c.name AS campaign,"
    " r.audio, r.audio_error, r.audio_text_hash, r.to_confirm,"
    " r.approved_by, r.approved_at, r.approved_hash, r.audio_choice, r.speakers, r.share_token,"
    " r.respelled_at"
)
_FROM = (
    "FROM recap_jobs r LEFT JOIN campaigns c ON c.id = r.campaign_id AND c.guild_id = r.guild_id"
)


class RecapError(Exception):
    """Something the page shows in plain words. `code` is for the page's own message.
    `problems`: for "cue_problems", what is wrong with the cues (#2083), as `cues.Problem.as_dict`
    with the same problem in plain words under "words"."""

    def __init__(self, code: str, problems: list[dict[str, Any]] | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.problems = problems


def cue_refusal(summary: str) -> RecapError | None:
    """The refusal to give when this text's cues are wrong, else None. The API's answer is the
    only check: the page shows it and never checks cues itself (#2083)."""
    found = cues.cue_problems(summary)
    if not found:
        return None
    return RecapError("cue_problems", [{**p.as_dict(), "words": p.words()} for p in found])


def _audio_view(row: dict[str, Any], *, ready: bool, approved: bool = False) -> dict[str, Any]:
    """Where the narration and music have got to (#1383), as the page words it. `current`: the
    audio was made from the text as it is now, so the players may be shown; after an edit it is
    not. `canAskAgain` is the page's cue only; the database's door is the rule."""
    state = row["audio"]
    audio_chosen = row["audio_choice"] in ("voice", "music", "both")
    made_from = text_hash(row["title"], row["summary"])
    current = state == "ready" and row["audio_text_hash"] == made_from
    return {
        "state": state,
        "error": row["audio_error"] if state == "failed" else "",
        "current": current,
        # only for a text the DM approved with audio chosen (the database's door is the rule)
        # A posted recap may be narrated again only when its audio no longer matches its text
        # (#2183, a spelling fix, or a remake that failed); a draft or a posting one, whenever it
        # is made or failed.
        "canAskAgain": ready
        and state in ("ready", "failed")
        and approved
        and audio_chosen
        and (row["status"] != "posted" or not current),
        # The page says what the new narration is for: the Discord message keeps the old one.
        "postedNote": row["status"] == "posted",
    }


def _approved(row: dict[str, Any]) -> bool:
    return bool(row["approved_hash"]) and row["approved_hash"] == text_hash(
        row["title"], row["summary"]
    )


def _approval_view(row: dict[str, Any], *, ready: bool) -> dict[str, Any]:
    approved = _approved(row)
    return {
        "approved": approved,
        "choice": row["audio_choice"] if approved else "",
        "approvedAt": row["approved_at"] if approved else None,
        # The audio is under way or made, or the choice was text only: approving again is not on.
        "canApprove": ready and not approved,
    }


def _share_view(row: dict[str, Any]) -> dict[str, Any]:
    token = row.get("share_token") if row["status"] == "posted" else None
    return {"on": bool(token), "path": f"/r/{token}" if token else None}


def _view(row: dict[str, Any]) -> dict[str, Any]:
    state, status = row["state"], row["status"]
    ready = state == "ready" and status == "draft"
    return {
        "ref": job_ref(row["guild_id"], row["campaign_id"], row["session_at"]),
        "campaign": row["campaign"],
        "sessionAt": row["session_at"],
        "gameDay": row["game_day"],
        "state": state,
        "status": status,
        "working": state not in ("ready", "failed") and status == "draft",
        "title": row["title"],
        "text": strip_cues(row["summary"]),
        "summary": row["summary"],
        "housekeeping": list(row["housekeeping"]),
        "toConfirm": list(row["to_confirm"]),  # the DM's copy only (#2027): never posted
        "error": row["last_error"],
        "canEdit": ready,
        "canPost": ready,
        "canTryAgain": status == "draft"
        and state in ("ready", "failed")
        and row["tries"] < MAX_TRIES,
        "triesLeft": max(0, MAX_TRIES - row["tries"]),
        "canDrop": status == "draft",
        "audio": _audio_view(
            row,
            ready=status in ("draft", "posting", "posted") and state == "ready",
            approved=_approved(row),
        ),
        # The DM's approval of the text (#2083). It counts only while the text is what they
        # approved: an edit ends it. `choice` is what they picked (voice, music, both or text).
        "approval": _approval_view(row, ready=ready),
        # The public link of a posted recap (#2118): `path` is the page's address for it (the
        # page adds its own origin); null when the link is off.
        "share": _share_view(row),
        # The words the cue check allows, so the page shows exactly what the API checks.
        "cue_words": cues.allowed_words(),
        # When the session's transcript was deleted by the campaign's "Keep transcripts for"
        # setting (Unix seconds, as the transcripts page says it), else null; the recap stays. And
        # how many days the table keeps transcripts, for the page's words (#2022).
        "transcriptsDeletedAt": row.get("transcripts_deleted_at"),
        "keepDays": row.get("keep_days"),
        # When a fixed spelling changed this recap's text (Unix seconds, #2170), else null. A
        # posted recap's Discord message keeps the old spelling, so the page says so (#2178).
        "respelledAt": row.get("respelled_at"),
    }


async def _scoped(conn: Conn, guild_id: int) -> None:
    await conn.execute("SELECT set_config('dmbot.guild_id', %s, true)", (str(guild_id),))


async def _with_transcript_marks(
    conn: Conn, guild_id: int, rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """The rows, each with whether its session's transcript was deleted (#2022). The website's
    role has no grant on the transcript tables, so this asks the transcripts page's own door
    (`dmbot_web_transcript_sessions`), once for each campaign, in the server the caller has already
    set for the transaction. A recap is matched to its session by its start time, as the bot does
    (`recaps/inputs.py`). A campaign the door does not know adds nothing."""
    marks: dict[str, tuple[dict[int, int], int | None]] = {}
    for campaign_id in {r["campaign_id"] for r in rows}:
        cur = await conn.execute(
            "SELECT dmbot_web_transcript_sessions(%s, %s) AS found", (guild_id, campaign_id)
        )
        found = (await cur.fetchone() or {}).get("found") or {}
        # Keyed by start time: unique for a campaign (transcript_sessions' own key).
        deleted = {
            int(s["startedAt"]): int(s["transcriptsDeletedAt"])
            for s in found.get("sessions", [])
            if s.get("transcriptsDeletedAt") is not None
        }
        days = found.get("keepDays")
        marks[campaign_id] = (deleted, None if days is None else int(days))
    out: list[dict[str, Any]] = []
    for r in rows:
        deleted, days = marks[r["campaign_id"]]
        at = deleted.get(int(r["session_at"]))
        out.append({**r, "transcripts_deleted_at": at, "keep_days": days if at else None})
    return out


def _guild_of(session: Session, guild_id: int) -> int:
    if guild_id not in {int(g.id) for g in session.guilds[:MAX_GUILDS]}:
        raise RecapError("not_found")
    return guild_id


async def list_recaps(db: Database, session: Session) -> list[dict[str, Any]]:
    """The signed-in DM's latest recaps, newest first, in the servers of this session's list."""
    out: list[dict[str, Any]] = []
    async with db.user(session.user_id, session=session.id_hash) as conn:
        for guild in session.guilds[:MAX_GUILDS]:
            await _scoped(conn, int(guild.id))
            cur = await conn.execute(
                f"SELECT {_COLUMNS} {_FROM} WHERE r.guild_id = %s"
                " ORDER BY r.session_at DESC LIMIT %s",
                (int(guild.id), LIST_LIMIT),
            )
            rows = await _with_transcript_marks(conn, int(guild.id), await cur.fetchall())
            out += [{**_view(r), "serverName": guild.name} for r in rows]
    out.sort(key=lambda v: v["sessionAt"], reverse=True)
    return out[:LIST_LIMIT]


async def month_usage(
    db: Database, session: Session, *, now: float, recaps_allowed: int, audio_allowed: int
) -> list[dict[str, Any]]:
    """For each of the signed-in DM's campaigns that has recaps this month: how many were made
    and how many are allowed, for the page's quiet "N of M this month" line (#1428). The same
    counting as the bot's (`RecapStore.used_in_month`); the row rule shows only the campaigns
    this person is a DM of."""
    start, end = month_bounds(now)
    out: list[dict[str, Any]] = []
    async with db.user(session.user_id, session=session.id_hash) as conn:
        for guild in session.guilds[:MAX_GUILDS]:
            await _scoped(conn, int(guild.id))
            cur = await conn.execute(
                "SELECT r.campaign_id, c.name AS campaign,"
                " sum((CASE WHEN r.state = 'ready' THEN 1 ELSE 0 END) + r.tries) AS recaps,"
                " sum((CASE WHEN r.audio = 'ready' THEN 1 ELSE 0 END)"
                " + (CASE WHEN r.audio <> 'off' THEN r.tries ELSE 0 END)) AS narrated"
                f" {_FROM} WHERE r.guild_id = %s AND r.created_at >= %s AND r.created_at < %s"
                " GROUP BY r.campaign_id, c.name ORDER BY c.name",
                (int(guild.id), start, end),
            )
            for r in await cur.fetchall():
                out.append(
                    {
                        "serverName": guild.name,
                        "campaign": r["campaign"],
                        "used": int(r["recaps"]),
                        "allowed": recaps_allowed,
                        "audioUsed": int(r["narrated"]),
                        "audioAllowed": audio_allowed,
                    }
                )
    return out


async def _one(
    conn: Conn, ref: str, session: Session, *, marks: bool = True
) -> tuple[int, str, int, dict[str, Any]]:
    """The recap's row, with its session's transcript marks unless `marks` is off: the checks that
    only need its state before a write skip the extra door call (#2022)."""
    parsed = parse_ref(ref)
    if parsed is None:
        raise RecapError("not_found")
    guild_id, campaign_id, session_at = parsed
    _guild_of(session, guild_id)
    await _scoped(conn, guild_id)
    cur = await conn.execute(
        f"SELECT {_COLUMNS} {_FROM} WHERE r.guild_id = %s AND r.campaign_id = %s"
        " AND r.session_at = %s",
        (guild_id, campaign_id, session_at),
    )
    row = await cur.fetchone()
    if row is None:
        raise RecapError("not_found")
    if marks:
        [row] = await _with_transcript_marks(conn, guild_id, [row])
    return guild_id, campaign_id, session_at, row


async def read_recap(db: Database, session: Session, ref: str) -> dict[str, Any]:
    async with db.user(session.user_id, session=session.id_hash) as conn:
        *_, row = await _one(conn, ref, session)
    return _view(row)


async def edit_recap(
    db: Database, session: Session, ref: str, *, summary: str, housekeeping: list[str]
) -> dict[str, Any]:
    """Save the DM's text. Only while the recap is ready and undecided; the size limits are
    checked here, the secret-name check by the bot at Post."""
    text = summary.strip()
    bullets = [" ".join(b.split()) for b in housekeeping if b.strip()]
    if not text or len(text) > SUMMARY_MAX:
        raise RecapError("bad_text")
    if refusal := cue_refusal(text):  # the edit stays unsaved until the cues are right (#2083)
        raise refusal
    if len(bullets) > HOUSEKEEPING_BULLETS or any(len(b) > BULLET_MAX for b in bullets):
        raise RecapError("bad_text")
    async with db.user(session.user_id, session=session.id_hash) as conn:
        gid, cid, at, _row = await _one(conn, ref, session, marks=False)
        cur = await conn.execute(
            "UPDATE recap_jobs SET summary = %s, housekeeping = %s WHERE guild_id = %s"
            " AND campaign_id = %s AND session_at = %s AND state = 'ready' AND status = 'draft'",
            (text, bullets, gid, cid, at),
        )
        if not cur.rowcount:
            raise RecapError("not_editable")
        *_, row = await _one(conn, ref, session)
    return _view(row)


async def decide(db: Database, session: Session, ref: str, action: str) -> dict[str, Any]:
    """`post` (the bot sends it), `drop` (Don't post) or `again` (Try again, at most twice)."""
    async with db.user(session.user_id, session=session.id_hash) as conn:
        gid, cid, at, _row = await _one(conn, ref, session, marks=False)
        where = " WHERE guild_id = %s AND campaign_id = %s AND session_at = %s"
        args: tuple[Any, ...] = (gid, cid, at)
        if action == "post":
            cur = await conn.execute(
                "UPDATE recap_jobs SET status = 'posting' "
                + where
                + " AND state = 'ready' AND status = 'draft'",
                args,
            )
        elif action == "drop":
            cur = await conn.execute(
                "UPDATE recap_jobs SET status = 'dropped' " + where + " AND status = 'draft'", args
            )
        elif action == "again":
            cur = await conn.execute(
                # Only these two: the worker sees "queued" with an email already sent and resets
                # everything else itself (the page's role may write no more than it needs).
                "UPDATE recap_jobs SET tries = tries + 1, state = 'queued' "
                + where
                + " AND status = 'draft' AND state IN ('ready', 'failed') AND tries < %s",
                (*args, MAX_TRIES),
            )
        else:
            raise RecapError("not_found")
        if not cur.rowcount:
            raise RecapError("not_allowed")
        *_, row = await _one(conn, ref, session)
    return _view(row)


async def post_checks(
    conn: Conn,
    gid: int,
    cid: str,
    row: dict[str, Any],
    outside_engine: str | None,
    *,
    must_be_posted: bool = False,
) -> None:
    """The checks Post makes, for approving a text and for sharing it (#2083, #2118): no secret
    name in the text (the whole name, as the bot reads it) and everyone whose lines were used
    still recorded. The names come through a door and are used to check this one text; they are
    never returned. Raises `names_secret` or `stopped_recording`."""
    cur = await conn.execute(
        "SELECT dmbot_recap_check_names(%s, %s, %s, %s) AS r",
        (gid, cid, TERMS_VERSION, outside_engine),
    )
    names = (await cur.fetchone() or {}).get("r")
    if names is None:  # not a DM of the campaign: nothing is said about the recap
        raise RecapError("not_found" if must_be_posted else "not_allowed")
    if must_be_posted and row["status"] != "posted":
        raise RecapError("not_posted")
    text = "\n".join([row["title"], strip_cues(row["summary"]), *row["housekeeping"]])
    if names_a_secret(text, names["secret"], names["allowed"]):
        raise RecapError("names_secret")
    agreed = {int(u) for u in names["recorded"]}
    if not {int(u) for u in row["speakers"]} <= agreed:
        raise RecapError("stopped_recording")


async def approve_recap(
    db: Database,
    session: Session,
    ref: str,
    choice: str,
    *,
    audio_in_plan: bool,
    outside_engine: str | None = None,
) -> dict[str, Any]:
    """✅ Approve the text and pick what to make (#2083): the cues are checked here, then the
    database's door (`dmbot_recap_approve`) records who and when and the hash of this very text and
    queues the audio. `outside_engine`: the speech-to-text company in use, so that only consent
    given for it counts, as in the bot. The secret names and the plan are checked again by the
    audio step before any paid call. `audio_in_plan`: from the campaign owner's plan; an audio
    choice without it is refused ("not_in_plan")."""
    if choice not in CHOICES:
        raise RecapError("bad_choice")
    if choice != TEXT and not audio_in_plan:
        raise RecapError("not_in_plan")
    async with db.user(session.user_id, session=session.id_hash) as conn:
        gid, cid, at, row = await _one(conn, ref, session, marks=False)
        if refusal := cue_refusal(row["summary"]):
            raise refusal
        await post_checks(conn, gid, cid, row, outside_engine)
        cur = await conn.execute(
            "SELECT dmbot_recap_approve(%s, %s, %s, %s, %s) AS r",
            (gid, cid, at, choice, text_hash(row["title"], row["summary"])),
        )  # (the hash of the very text whose cues were just checked)
        outcome = (await cur.fetchone() or {}).get("r")
        if outcome != "approved":
            raise RecapError("not_allowed")
        *_, row = await _one(conn, ref, session)
    return _view(row)


SHARE_ACTIONS = ("on", "off", "new")


async def share_recap(
    db: Database, session: Session, ref: str, action: str, *, outside_engine: str | None = None
) -> dict[str, Any]:
    """Turn a posted recap's public link on, off, or make a new one (#2118). Only through the
    database's door, which holds it to a DM of the campaign. The token is made here (256 random
    bits, URL-safe) and is stored only with the recap. A draft or skipped recap has no link."""
    if action not in SHARE_ACTIONS:
        raise RecapError("bad_action")
    async with db.user(session.user_id, session=session.id_hash) as conn:
        gid, cid, at, row = await _one(conn, ref, session, marks=False)
        if action != "off":
            # Sharing a recap makes it public, so it must pass what Post checks (#2118).
            await post_checks(conn, gid, cid, row, outside_engine, must_be_posted=True)
        cur = await conn.execute(
            "SELECT dmbot_recap_share(%s, %s, %s, %s, %s) AS r",
            (gid, cid, at, action, secrets.token_urlsafe(32)),
        )
        found = (await cur.fetchone() or {}).get("r")
        if found is None:
            raise RecapError("not_found")
        if found["status"] == "not_posted":
            raise RecapError("not_posted")
        token = found["token"]
    return {"on": bool(token), "path": f"/r/{token}" if token else None}


async def audio_again(
    db: Database, session: Session, ref: str, *, audio_in_plan: bool = True
) -> dict[str, Any]:
    """ "Make the audio again" (#1418): only through the database's door, which holds it to a DM of
    the campaign, a recap that is ready, and once every 10 minutes. It is also for a posted recap
    whose narration no longer matches its text (#2183). The campaign owner's plan must include
    narration (`audio_in_plan`, from the API as for Approve; checked for every status, since a
    recap may be posted while the request runs), and the audio step checks the plan and the
    month's allowance again before any paid call. The recap page and its shared link get the new
    files; the Discord message already posted keeps its old attachment."""
    async with db.user(session.user_id, session=session.id_hash) as conn:
        gid, cid, at, row = await _one(conn, ref, session, marks=False)
        if not audio_in_plan:
            raise RecapError("not_in_plan")
        cur = await conn.execute("SELECT dmbot_recap_audio_again(%s, %s, %s) AS r", (gid, cid, at))
        found = await cur.fetchone()
        outcome = found["r"] if found else "not_allowed"
        if outcome == "too_soon":
            raise RecapError("too_soon")
        if outcome == "needs_approval":  # the text changed since it was approved
            raise RecapError("needs_approval")
        if outcome != "queued":
            raise RecapError("not_allowed")
        *_, row = await _one(conn, ref, session)
    return _view(row)
