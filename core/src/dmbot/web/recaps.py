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
import time
from collections.abc import Mapping
from typing import Any

from dmbot.consent import TERMS_VERSION
from dmbot.db import Conn, Database
from dmbot.recaps import critics, cues
from dmbot.recaps import progress as recap_progress
from dmbot.recaps.allowance import month_bounds
from dmbot.recaps.approval import CHOICES, TEXT, text_hash
from dmbot.recaps.draft import BULLET_HARD, HOUSEKEEPING_HARD, SUMMARY_MAX
from dmbot.recaps.held import apply_held
from dmbot.recaps.notify import job_ref, parse_ref
from dmbot.recaps.store import MAX_TRIES
from dmbot.recaps.text import names_a_secret, strip_cues
from dmbot.web.me import MAX_GUILDS
from dmbot.web.sessions import Session

LIST_LIMIT = 20

_COLUMNS = (
    "r.guild_id, r.campaign_id, r.session_at, r.state, r.status, r.title, r.summary,"
    " r.housekeeping, r.music, r.tries, r.last_error, r.next_at, r.game_day, c.name AS campaign,"
    " r.audio, r.audio_error, r.audio_text_hash, r.to_confirm,"
    " r.approved_by, r.approved_at, r.approved_hash, r.audio_choice, r.speakers, r.share_token,"
    " r.respelled_at, r.critics, c.recaps_email AS mail_on,"
    # The posting clock's columns (#2435): the page's `postingSince`, `postingStep` and `postedUrl`
    # are made from them in `_view` (WebDev's #2436 owns those keys).
    " r.posting_since, r.posting_step, r.posted_url, r.restarted_at, r.previous,"
    " r.written_by, r.uploaded_at, r.earlier,"
    # How far the work has got (#2636): `progress` in `_view` is made from these (counts and times).
    " r.created_at, r.updated_at, r.window_count, r.window_done, r.calls, r.errors,"
    # The signed-in person's own address, the one `recaps/notify.py` mails (the payment company's,
    # else the sign-in one; the same COALESCE as `dmbot_recap_address`). web_users is forced to
    # the person's own row, so no other DM's address can come out of this (#2334).
    " (SELECT COALESCE(NULLIF(payment_email, ''), NULLIF(email, '')) FROM web_users LIMIT 1)"
    " AS mail_address,"
    " (SELECT COALESCE(payment_email, '') <> '' FROM web_users LIMIT 1) AS mail_is_payment"
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


def _shareable(row: dict[str, Any]) -> bool:
    """A recap that may have a public link (#2118, #2119): one being posted or posted, or a ready
    draft whose text is still exactly the text the DM approved (#2419, "Approve but don't post").
    The database's doors check the same rule themselves."""
    if row["status"] in ("posting", "posted"):
        return True
    return row["status"] == "draft" and row["state"] == "ready" and _approved(row)


def _share_view(row: dict[str, Any]) -> dict[str, Any]:
    token = row.get("share_token") if _shareable(row) else None
    return {"on": bool(token), "path": f"/r/{token}" if token else None}


def _unchecked_since(row: Mapping[str, Any], ready: bool) -> int:
    loop = critics.loop_from_json(row.get("critics"))
    return 0 if ready or loop.waits == 0 else loop.since


def _unchecked_sentences(row: Mapping[str, Any], ready: bool) -> list[int]:
    if _unchecked_since(row, ready) == 0:
        return []
    from dmbot.recaps import draft, motives

    return [
        n
        for n, text in enumerate(draft.sentences_of(strip_cues(row["summary"])), start=1)
        if motives.states_purpose(text)
    ]


def _unchecked_draft(row: Mapping[str, Any], ready: bool) -> list[str]:
    """The draft's sentences, for the DM to read, once it has waited for its check for 3 days
    (#2297): empty before that, and for a recap that is ready. The same split the marked sentence
    numbers count by, so the page can highlight them. Only the campaign's DMs are ever given a
    draft that is not posted; the signed-in read door already keeps to them."""
    import time

    since = _unchecked_since(row, ready)
    # (the database's door counts the same 3 days by its own clock; at the boundary the page can
    # offer the button a moment before the door agrees, and the door's answer stands)
    if since == 0 or time.time() - since < critics.UNCHECKED_AFTER_S:
        return []
    from dmbot.recaps import draft

    return draft.sentences_of(strip_cues(row["summary"]))


def _mail_view(row: dict[str, Any]) -> dict[str, Any]:
    """Where the recap-ready email goes, for the signed-in person only (#2334): the address (null
    when there is none), where it came from (`payment`: the payment company's, change it in the
    customer portal; `signin`: the address they signed in with, change it on the account page;
    `none`: nothing will be sent) and whether the campaign's email switch is on. Never logged."""
    address = row.get("mail_address") or None
    source = "none" if address is None else ("payment" if row.get("mail_is_payment") else "signin")
    return {"address": address, "source": source, "on": bool(row.get("mail_on", True))}


def _previous_view(row: Mapping[str, Any]) -> dict[str, Any] | None:
    """The earlier approved or posted recap, while it is still held for the DM (#2608): only until
    the new text is approved or posted, as `Job.keeps_previous` says."""
    kept = row.get("previous")
    if not isinstance(kept, dict) or row.get("approved_hash") or row["status"] != "draft":
        return None
    return {
        "title": kept.get("title", ""),
        "text": strip_cues(str(kept.get("summary", ""))),
        "status": kept.get("status", ""),  # draft (approved) or posted
        "postedUrl": kept.get("posted_url") or "",
    }


def _earlier_view(raw: object) -> list[dict[str, Any]]:
    return [
        {
            "title": str(e.get("title", "")),
            "text": strip_cues(str(e.get("summary", ""))),
            "writtenBy": e.get("written_by") or None,
            "replacedAt": int(e.get("replaced_at") or 0),
        }
        for e in (raw if isinstance(raw, list) else [])
        if isinstance(e, dict)
    ]


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
        # What the recap's critics still asked when they could not agree (#2255): the DM's copy
        # only, never in a posted recap or on a shared link. Soft notes in fixed wording
        # ("Check: ..."): things for the DM to look at, never orders.
        "criticNotes": list(critics.loop_from_json(row.get("critics")).notes),
        "error": row["last_error"],
        # The recap is written and waits for its last check (#2255): a note for the DM's card
        # (the words are `error`); not a failure and nothing to do.
        "waitingForCheck": row["last_error"] == critics.CHECK_WAIT and not ready,
        # When DMbot tries the check again (Unix seconds), while it waits; else 0 (#2596).
        "nextTryAt": (
            int(row["next_at"] or 0) if row["last_error"] == critics.CHECK_WAIT and not ready else 0
        ),
        # While it waits for its check: since when (Unix seconds, 0 before the first wait), and the
        # sentences the word list marks as likely to state a reason or a plan (their numbers, from
        # 1), which no check has read: for the DM's page, where they may read it and approve it
        # themselves. Players never see an unchecked draft unless the DM approves it.
        "uncheckedSince": _unchecked_since(row, ready),
        "uncheckedSentences": _unchecked_sentences(row, ready),
        "uncheckedDraft": (unchecked := _unchecked_draft(row, ready)),
        # The hash of the words the page shows (title and summary), sent back with "approve it as
        # it is" so that only the text the DM read is approved (#2305).
        "textHash": text_hash(row["title"], row["summary"]),
        # Only while it still waits (a failed or dropped recap is not opened this way).
        "canApproveUnchecked": bool(unchecked)
        and state == "prose"
        and not critics.loop_from_json(row.get("critics")).approved_at
        and status == "draft",
        "canEdit": ready,
        "canPost": ready,
        # While it goes out or its audio is made (#2419): when the work started on the server, the
        # step it is on (`check`, `post` or `audio`), the posted message's link, and the server's
        # clock now, so the page's running clock is right and resumes after a reload. The bot
        # records the first three (#2435); until its columns are read here they are null, and the
        # page copes.
        "postingSince": row.get("posting_since"),
        "postingStep": row.get("posting_step"),
        "postedUrl": row.get("posted_url"),
        "serverNow": (now := int(time.time())),
        # While it is made (#2636): the shared progress shape of every website wait, with the step
        # (`stage`), the parts of a long session read (`done` of `total`), when it began and when
        # it last moved, and `state` (running, stalled, failed or done); a failure is a fixed code
        # in `reason`. Counts and codes only. Null once the recap is posted or dropped.
        "progress": recap_progress.of(row, now=now),
        "canTryAgain": status == "draft"
        and state in ("ready", "failed")
        and row["tries"] < MAX_TRIES,
        "triesLeft": max(0, MAX_TRIES - row["tries"]),
        "canDrop": status == "draft",
        # Start the recap over (#2608): from a finished or failed one (not while it is being
        # written or sent). `restartedAt` is when the DM last did (Unix seconds, 0: never).
        # (a recap waiting for its check or between tries can be started over too: the DM's way out)
        "canMakeAgain": status in ("draft", "posted")
        and (state in ("ready", "failed") or row["last_error"] == critics.CHECK_WAIT),
        "restartedAt": int(row.get("restarted_at") or 0),
        # A recap written by someone at the table (#2609): who wrote it, null for DMbot's own;
        # the DM who added it and when; the recaps it replaced, newest first (words and who wrote
        # them, never where they were posted).
        "writtenBy": row.get("written_by") or None,
        "uploadedAt": int(row.get("uploaded_at") or 0),
        "earlier": _earlier_view(row.get("earlier")),
        # The recap that was approved or posted before the DM started over, kept until the new
        # one is approved (#2608); null otherwise. `Keep the earlier one` is `/keep` below.
        "previous": _previous_view(row),
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
        # Which session this is, as the transcripts number it, and how long it ran (#2418): both
        # null when the session can't be found or is still listening; an older page ignores them.
        "sessionNumber": row.get("session_number"),
        "sessionSeconds": row.get("session_seconds"),
        # A recap that exists is no longer waiting for the DM's name answers (#2174); a held one
        # has no job yet and is listed by `_held_view`.
        "waitingForNames": False,
        "namesToAnswer": 0,
        # When a fixed spelling changed this recap's text (Unix seconds, #2170), else null. A
        # posted recap's Discord message keeps the old spelling, so the page says so (#2178).
        "respelledAt": row.get("respelled_at"),
        "recapEmail": _mail_view(row),
    }


def _held_view(
    guild_id: int, held: dict[str, Any], server_name: str | None = None
) -> dict[str, Any]:
    """A recap that is still waiting for the DM's answers about names (#2174): there is no job yet,
    so nothing to read, edit or post, only that it waits and how many answers it waits on. Built
    from the read door's counts and times alone."""
    at = int(held["sessionAt"])
    return {
        "ref": job_ref(guild_id, held["campaignId"], at),
        "campaign": held["campaign"],
        "sessionAt": at,
        "gameDay": None,
        "state": "waiting",
        "status": "draft",
        "working": False,
        "title": "",
        "text": "",
        "summary": "",
        "housekeeping": [],
        "toConfirm": [],
        "criticNotes": [],
        "error": "",
        "canEdit": False,
        "canPost": False,
        "canTryAgain": False,
        "triesLeft": 0,
        "canDrop": False,
        "audio": {"state": "off", "error": "", "current": False, "canAskAgain": False},
        "approval": {"approved": False, "choice": "", "approvedAt": None, "canApprove": False},
        "share": {"on": False, "path": None},
        "cue_words": cues.allowed_words(),
        "transcriptsDeletedAt": None,
        "keepDays": None,
        "sessionNumber": None,  # (#2418) no session has been saved for it yet
        "sessionSeconds": None,
        "waitingForNames": True,
        "namesToAnswer": int(held["namesOpen"]),
        **({"serverName": server_name} if server_name else {}),
    }


async def _holds(conn: Conn, guild_id: int) -> list[dict[str, Any]]:
    """The signed-in DM's held recaps in this server, through the database's read door (it keeps
    to the campaigns they are a DM of). The server must already be set for the transaction."""
    cur = await conn.execute("SELECT dmbot_web_recap_holds(%s) AS held", (guild_id,))
    found = (await cur.fetchone() or {}).get("held") or []
    return [h for h in found if isinstance(h, dict)]


async def _scoped(conn: Conn, guild_id: int) -> None:
    await conn.execute("SELECT set_config('dmbot.guild_id', %s, true)", (str(guild_id),))


async def _with_transcript_marks(
    conn: Conn, guild_id: int, rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """The rows, each with whether its session's transcript was deleted (#2022). The website's
    role has no grant on the transcript tables, so this asks the transcripts page's own door
    (`dmbot_web_transcript_sessions`), once for each campaign, in the server the caller has already
    set for the transaction. A recap is matched to its session by its start time, as the bot does
    (`recaps/inputs.py`). A campaign the door does not know adds nothing.

    The same answer gives the session's number and length (#2418): the number is the one the
    transcripts page shows, never a second count. A recap with no session adds nothing."""
    marks: dict[str, tuple[dict[int, int], int | None, list[dict[str, Any]]]] = {}
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
        marks[campaign_id] = (
            deleted,
            None if days is None else int(days),
            list(found.get("sessions", [])),
        )
    out: list[dict[str, Any]] = []
    for r in rows:
        deleted, days, sessions = marks[r["campaign_id"]]
        at = deleted.get(int(r["session_at"]))
        number, seconds = session_facts(sessions, int(r["session_at"]))
        out.append(
            {
                **r,
                "transcripts_deleted_at": at,
                "keep_days": days if at else None,
                "session_number": number,
                "session_seconds": seconds,
            }
        )
    return out


def session_facts(sessions: list[dict[str, Any]], started_at: int) -> tuple[int | None, int | None]:
    """The number and the length in seconds of the session that started at `started_at`, from the
    transcripts page's own list (#2418). The number is exactly the one the transcripts show. The
    length is when it ended minus when it started, for a live session and an imported one alike;
    null while it is still listening or if it never recorded an end, so the page leaves it out
    rather than guess. Both are null when no session starts then."""
    for s in sessions:
        if int(s["startedAt"]) == started_at:
            ended = s.get("endedAt")
            seconds = None if ended is None else max(0, int(ended) - started_at)
            return int(s["number"]), seconds
    return None, None


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
            made = {(r["campaign_id"], r["session_at"]) for r in rows}
            out += [
                _held_view(int(guild.id), h, guild.name)
                for h in await _holds(conn, int(guild.id))
                if (h["campaignId"], int(h["sessionAt"]))
                not in made  # (a job and a hold for one session)
            ]
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
        try:
            *_, row = await _one(conn, ref, session)
        except RecapError as err:
            if err.code != "not_found" or (parsed := parse_ref(ref)) is None:
                raise
            guild_id, campaign_id, session_at = parsed
            _guild_of(session, guild_id)
            await _scoped(conn, guild_id)
            for held in await _holds(conn, guild_id):
                if held["campaignId"] == campaign_id and int(held["sessionAt"]) == session_at:
                    return _held_view(guild_id, held)
            # Released between the list and this page: the job exists now (one more look).
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
    if len(bullets) > HOUSEKEEPING_HARD or any(len(b) > BULLET_HARD for b in bullets):
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


async def decide(
    db: Database,
    session: Session,
    ref: str,
    action: str,
    *,
    plot_gate: bool = False,
    outside_engine: str | None = None,
) -> dict[str, Any]:
    """`post` (the bot sends it), `drop` or `again` (Try again, at most twice). The page no longer
    offers `drop` (#2419: a recap the DM does not approve simply stays a draft); the route stays
    so an older open page still works. **Posting a draft that is not approved counts as approving
    it** (Supervisor, 2026-10-23, #2122): it goes through the approval door (text only, posted in
    the same statement), so the version is kept and PlotBot's open points hold it (`plot_open`),
    as for the Approve button. `plot_gate`, `outside_engine`: as for `approve_recap`."""
    async with db.user(session.user_id, session=session.id_hash) as conn:
        gid, cid, at, current = await _one(conn, ref, session, marks=False)
    if action == "post" and not _approved(current):
        return await approve_recap(
            db, session, ref, TEXT, audio_in_plan=False, post=True,
            outside_engine=outside_engine, plot_gate=plot_gate,
        )  # fmt: skip
    async with db.user(session.user_id, session=session.id_hash) as conn:
        gid, cid, at, _row = await _one(conn, ref, session, marks=False)
        where = " WHERE guild_id = %s AND campaign_id = %s AND session_at = %s"
        args: tuple[Any, ...] = (gid, cid, at)
        if action == "post":
            cur = await conn.execute(
                "UPDATE recap_jobs SET status = 'posting' "
                + where
                + " AND state = 'ready' AND status = 'draft'"
                # Only the text the DM approved: an edit between the read above and this statement
                # (or since the approval) posts nothing. The page's role may read the hash.
                + " AND approved_hash = encode(sha256(convert_to(title || E'\\n' || summary,"
                " 'UTF8')), 'hex')",
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
    must_be_shareable: bool = False,
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
        raise RecapError("not_found" if must_be_shareable else "not_allowed")
    # The bot's rule too: a name the players haven't been shown yet is allowed when the table said
    # it in this very session (#2404).
    cur = await conn.execute(
        "SELECT dmbot_recap_held_names(%s, %s, %s) AS r", (gid, cid, row["session_at"])
    )
    names = apply_held(names, (await cur.fetchone() or {}).get("r"))
    if must_be_shareable and not _shareable(row):
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
    audio_unavailable: str = "",
    post: bool = False,
    outside_engine: str | None = None,
    plot_gate: bool = False,
) -> dict[str, Any]:
    """✅ Approve the text and pick what to make (#2083): the cues are checked here, then the
    database's door (`dmbot_recap_approve`) records who and when and the hash of this very text and
    queues the audio. `outside_engine`: the speech-to-text company in use, so that only consent
    given for it counts, as in the bot. The secret names and the plan are checked again by the
    audio step before any paid call. `audio_in_plan`: from the campaign owner's plan; an audio
    choice without it is refused ("not_in_plan"), and one made while `audio_unavailable` says why
    it can't be ("not_set_up", "allowance", "unavailable") is refused ("audio_unavailable").
    `post` (#2419, "Approve and post text only"): approve a text-only choice and hand it to the bot
    to post in the same transaction, so a failure leaves nothing approved and half-posted; it is
    only for the text-only choice, since audio is made and reviewed before anything is posted.
    `plot_gate` (#2122): PlotBot's check is on, so the door refuses ("plot_open") while an issue it
    found has no answer, under the same lock as the approval."""
    if choice not in CHOICES or (post and choice != TEXT):
        raise RecapError("bad_choice")
    if choice != TEXT and not audio_in_plan:
        raise RecapError("not_in_plan")
    if choice != TEXT and audio_unavailable in ("not_set_up", "allowance", "unavailable"):
        raise RecapError("audio_unavailable")
    async with db.user(session.user_id, session=session.id_hash) as conn:
        gid, cid, at, row = await _one(conn, ref, session, marks=False)
        if refusal := cue_refusal(row["summary"]):
            raise refusal
        await post_checks(conn, gid, cid, row, outside_engine)
        if plot_gate:
            await conn.execute("SELECT set_config('dmbot.plot_gate', 'on', true)")
        cur = await conn.execute(
            "SELECT dmbot_recap_approve(%s, %s, %s, %s, %s) AS r",
            (gid, cid, at, choice, text_hash(row["title"], row["summary"])),
        )  # (the hash of the very text whose cues were just checked)
        outcome = (await cur.fetchone() or {}).get("r")
        if outcome == "plot_open":
            raise RecapError("plot_open")
        if outcome != "approved":
            raise RecapError("not_allowed")
        if post:  # the same hand-over as `decide(..., "post")`, in this transaction
            cur = await conn.execute(
                "UPDATE recap_jobs SET status = 'posting' WHERE guild_id = %s AND campaign_id = %s"
                " AND session_at = %s AND state = 'ready' AND status = 'draft'",
                (gid, cid, at),
            )
            if not cur.rowcount:
                raise RecapError("not_allowed")  # (the approval is rolled back with it)
        *_, row = await _one(conn, ref, session)
    return _view(row)


async def approve_unchecked(
    db: Database, session: Session, ref: str, expected: str
) -> dict[str, Any]:
    """The DM read a draft that waited 3 days for its check and approves it as it is (#2305).
    Only through the database's door, which holds it to a DM of the campaign, a draft still
    waiting in the critics' loop, and the 3 days (`critics.UNCHECKED_AFTER_S`, passed in so the
    number lives in one place). It does not make the recap ready by itself: it marks the check
    done and the bot's worker finishes it (title and housekeeping), after which the usual approval
    and posting apply. The sentences that were not checked stay marked in `criticNotes`, and
    players see nothing until the DM posts, as with any recap. `expected` is the `textHash` the
    page showed with the draft: a draft the worker revised since (a waiting try may) approves
    nothing (`changed`), so the DM only ever approves the words they read. Pressing again after an
    approval answers the same and changes nothing."""
    if not isinstance(expected, str) or not 0 < len(expected) <= 64:
        raise RecapError("bad_hash")
    async with db.user(session.user_id, session=session.id_hash) as conn:
        gid, cid, at, _row = await _one(conn, ref, session, marks=False)  # (not found: not a DM)
        cur = await conn.execute(
            "SELECT dmbot_recap_approve_unchecked(%s, %s, %s, %s, %s, %s) AS r",
            (gid, cid, at, critics.UNCHECKED_AFTER_S, critics.APPROVED_UNCHECKED, expected),
        )
        outcome = (await cur.fetchone() or {}).get("r")
        if outcome in ("not_yet", "changed"):
            raise RecapError(outcome)
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
            await post_checks(conn, gid, cid, row, outside_engine, must_be_shareable=True)
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


async def redo_recap(
    db: Database, session: Session, ref: str, *, replace: bool = False
) -> dict[str, Any]:
    """Make the recap of a session, or make it again (#2608), through the database's door, which
    holds it to a DM of the campaign, a session that still has its lines, and a recap that is not
    being written or sent. `ref` may name a session with no recap yet. An existing recap needs
    `replace` (the page asks once); an approved or posted one stays, in `previous`, until the new
    one is approved. The bot writes it next round, with the plan and the month's allowance checked
    there. Returns the recap as `GET /me/recaps/{ref}`."""
    parsed = parse_ref(ref)
    if parsed is None:
        raise RecapError("not_found")
    gid, cid, at = parsed
    _guild_of(session, gid)
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, gid)
        cur = await conn.execute(
            "SELECT dmbot_recap_redo(%s, %s, %s, %s) AS r", (gid, cid, at, replace)
        )
        found = await cur.fetchone()
        outcome = found["r"] if found else "not_allowed"
        if outcome != "queued":
            raise RecapError(
                outcome if outcome in ("needs_confirm", "busy", "no_lines") else "not_allowed"
            )
        *_, row = await _one(conn, ref, session)
    return _view(row)


async def upload_recap(
    db: Database, session: Session, campaign_ref: str, body: object, *, read_link: Any
) -> dict[str, Any]:
    """Keep a recap written by someone at the table as a session's recap (#2609). `body`:
    `{"session": <Unix seconds>, "text": "..." | "link": <a shared Google Doc>, "writtenBy": "...",
    "title": "..." (optional), "replace": bool, "rights": bool}`. The session need not have a
    transcript: the DM names it by when it began. The text goes through the database's door
    whole (up to 50,000 characters), approved by the DM who adds it; the AI writer never sees it.
    `rights` is the one confirmation per campaign ("written by someone at your table, and you
    have their OK to use it"); `replace` is the DM's yes to replacing a recap the session has,
    which is then kept as an earlier version. `read_link` reads a Google Doc's text (raises
    `DocError`). The bot checks its names next round. Returns the recap as
    `GET /me/recaps/{ref}`."""
    from dmbot.recaps import uploaded
    from dmbot.transcript import import_link

    parsed = parse_ref(f"{campaign_ref}.0")
    if parsed is None or not isinstance(body, dict):
        raise RecapError("not_found")
    gid, cid = parsed[0], parsed[1]
    _guild_of(session, gid)
    at = body.get("session")
    replace, rights = body.get("replace", False), body.get("rights", False)
    if type(at) is not int or not 0 < at < 2**40 or not isinstance(replace, bool):
        raise RecapError("bad_text")
    if not isinstance(rights, bool):
        raise RecapError("bad_text")
    author = uploaded.clean_author(body.get("writtenBy"))
    if author is None:
        raise RecapError("bad_text")
    raw = body.get("text")
    if raw is None and body.get("link") is not None:
        try:
            raw = await read_link(body["link"])
        except import_link.DocError as err:
            raise RecapError(err.code) from None
    text = uploaded.clean_text(raw)
    if text is None:
        raise RecapError("too_big" if isinstance(raw, str) and raw.strip() else "bad_text")
    given = body.get("title")
    title = " ".join(given.split())[: uploaded.TITLE_MAX] if isinstance(given, str) else ""
    title = title or uploaded.title_for(text, author)
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, gid)
        cur = await conn.execute(
            "SELECT dmbot_recap_upload(%s, %s, %s, %s, %s, %s, %s, %s) AS r",
            (gid, cid, at, title, text, author, replace, rights),
        )
        found = await cur.fetchone()
        outcome = found["r"] if found else "not_allowed"
        if outcome != "stored":
            known = ("needs_rights", "needs_confirm", "busy", "bad_text")
            raise RecapError(outcome if outcome in known else "not_allowed")
        *_, row = await _one(conn, job_ref(gid, cid, at), session)
    return _view(row)
