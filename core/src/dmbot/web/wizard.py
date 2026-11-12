"""The campaign start-up wizard's draft, for the website (#3955, part of #3953;
docs/WIZARD_DESIGN.md).

A DM sets a campaign up step by step on the website before it exists. The website's database role
touches no campaign table and cannot call the AI, so this module only checks what a person typed
and passes it to the `dmbot_web_wizard_*` doors (migration 0409), which check the signed-in
account, that the server is in the session's own list, and keep the draft to its one DM. The bot
makes the campaign later (the finish run); nothing is read or sent anywhere from here.

Everything the doors store is the DM's own: answers, and the text of documents they gave. The text
is never echoed back (a document is shown as its name, pages, words and status) and never logged.
A draft past 7 days without a press is gone.
"""

from __future__ import annotations

from typing import Any

from psycopg.types.json import Jsonb

from dmbot.campaigns.invite import parse_invite
from dmbot.campaigns.models import FALLBACK_NONE, RULESETS, CampaignError
from dmbot.consent import TERMS_VERSION
from dmbot.db import Database
from dmbot.web.recaps import RecapError, _guild_of, _scoped
from dmbot.web.sessions import Session

# The supplements a table may name: only the choice is kept, never a word of a book (the IP rule;
# docs/PLAN.md, "The campaign start-up wizard", step 6).
SUPPLEMENTS = ("xanathar", "ravnica", "theros", "tasha", "fizban", "mordenkainen", "bigby")
SLOTS = ("lore", "secrets", "ruleset", "house", "homebrew")
SOURCES = ("upload", "link", "drive")
STATUSES = ("ok", "unreadable")
REASONS = ("", "empty", "scanned", "locked", "too_big", "not_text")
NAME_MAX = 100
LINK_MAX = 2_048
CHARACTERS_MAX = 12
# Door statuses the page words itself; any other non-"ok" status is a code bug and shows as a
# plain failure.
KNOWN = frozenset(
    {
        "bad_name", "bad_patch", "bad_key", "bad_document", "too_big", "too_many", "no_draft",
        "not_found", "needs_name", "needs_rights", "too_soon",
    }
)  # fmt: skip


# The pages speak camelCase (the website's own style); the database keeps snake_case. One place
# maps both ways, so neither side learns the other's spelling (#3955, #3956).
_TO_STORED = {
    "pageTextUsed": "page_text_used",
    "dataUse": "data_use",
    "partyName": "party_name",
    "sheetUrl": "sheet_url",
}
_TO_PAGE = {stored: page for page, stored in _TO_STORED.items()}


def _rename(value: Any, names: dict[str, str]) -> Any:
    """The answers with their keys renamed (and each character's), values untouched."""
    if isinstance(value, list):
        return [_rename(item, names) for item in value]
    if isinstance(value, dict):
        return {names.get(str(k), k): _rename(v, names) for k, v in value.items()}
    return value


def from_page(patch: dict[str, Any]) -> dict[str, Any]:
    """A page's answers in the database's spelling. A key already in the database's spelling is
    refused like any other unknown one, so there is one spelling on the wire."""
    renamed = _rename(patch, _TO_STORED)
    if any(k in patch and k in _TO_PAGE for k in patch):
        raise RecapError("bad_key")
    return renamed if isinstance(renamed, dict) else {}


def to_page(answers: dict[str, Any]) -> dict[str, Any]:
    """The stored answers in the page's spelling."""
    renamed = _rename(answers, _TO_PAGE)
    return renamed if isinstance(renamed, dict) else {}


def _text(value: Any, limit: int, key: str) -> str:
    # A NUL character is refused: the database cannot hold it, and it would come back as a crash
    # instead of a word the page shows.
    if not isinstance(value, str) or len(value) > limit or "\x00" in value:
        raise RecapError(f"bad_{key}")
    return value.strip()


def _count(value: Any, key: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise RecapError(f"bad_{key}")
    return value


def check_patch(patch: dict[str, Any]) -> dict[str, Any]:
    """The answers a page may send, each checked; anything else is refused by name. The door
    keeps the key list and the size; this is where the shapes live."""
    clean: dict[str, Any] = {}
    for key, value in patch.items():
        if key == "name":
            clean[key] = _text(value, NAME_MAX, "name")
        elif key == "invite":
            try:
                clean[key] = parse_invite(_text(value, LINK_MAX, "invite")) or ""
            except CampaignError as exc:
                raise RecapError("bad_invite") from exc
        elif key == "source":
            if value not in ("dndbeyond", "file", "drive"):
                raise RecapError("bad_source")
            clean[key] = value
        elif key in ("page_text_used", "data_use"):
            if not isinstance(value, bool):
                raise RecapError(f"bad_{key}")
            clean[key] = value
        elif key == "target":
            if value not in RULESETS:
                raise RecapError("bad_target")
            clean[key] = value
        elif key == "fallback":
            if value != FALLBACK_NONE and value not in RULESETS:
                raise RecapError("bad_fallback")
            clean[key] = value
        elif key == "supplements":
            if not isinstance(value, list) or any(v not in SUPPLEMENTS for v in value):
                raise RecapError("bad_supplements")
            clean[key] = sorted(set(value), key=SUPPLEMENTS.index)
        elif key == "characters":
            clean[key] = _characters(value)
        elif key == "skipped":
            if not isinstance(value, list) or any(
                not isinstance(v, int) or isinstance(v, bool) or not 1 <= v <= 9 for v in value
            ):
                raise RecapError("bad_skipped")
            clean[key] = sorted(set(value))
        else:
            raise RecapError("bad_key")  # the door refuses it too
    target, fallback = clean.get("target"), clean.get("fallback")
    if target is not None and target == fallback:
        raise RecapError("bad_fallback")  # the other ruleset, or none
    return clean


def _characters(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) > CHARACTERS_MAX:
        raise RecapError("bad_characters")
    found: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            raise RecapError("bad_characters")
        player = item.get("player", "pending")
        if player != "pending" and not (isinstance(player, str) and player.isdecimal()):
            raise RecapError("bad_characters")
        found.append(
            {
                "full": _text(item.get("full", ""), NAME_MAX, "characters"),
                "party_name": _text(item.get("party_name", ""), NAME_MAX, "characters"),
                "sheet_url": _text(item.get("sheet_url", ""), LINK_MAX, "characters"),
                "player": player,
            }
        )
    return found


async def _door(
    db: Database, session: Session, guild_id: int, sql: str, args: tuple[Any, ...]
) -> dict[str, Any] | None:
    """One call to a door as the signed-in account, in one server of the session's list."""
    _guild_of(session, guild_id)
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(sql, (guild_id, *args))
        row = await cur.fetchone()
    found = row["found"] if row else None
    return found if isinstance(found, dict) else None


def _ok(found: dict[str, Any] | None) -> dict[str, Any]:
    """A door's answer, or the word the page shows when it is not `ok`."""
    if found is None:
        raise RecapError("not_found")  # not the caller's server, or no account
    status = str(found.get("status", ""))
    if status != "ok":
        raise RecapError(status if status in KNOWN else "failed")
    return found


async def has_room(db: Database, session: Session, *, now: int, enforce_plans: bool) -> bool:
    """Whether the signed-in account's plan has room for one more campaign: the check the + New
    campaign button makes (`campaign_cap.room`), made when a draft starts so the DM is told
    before nine steps, and again by the bot at Finish. With plans not enforced there is always
    room."""
    if not enforce_plans:
        return True
    from dmbot import campaign_cap

    async with db.user(session.user_id, session=session.id_hash) as conn:
        return (await campaign_cap.room(conn, session.user_id, now)).fits(1)


async def get(db: Database, session: Session, guild_id: int) -> dict[str, Any] | None:
    """The caller's draft in this server, or None. Never a document's text."""
    return await _door(db, session, guild_id, "SELECT dmbot_web_wizard_get(%s) AS found", ())


async def start(db: Database, session: Session, guild_id: int, name: str = "") -> None:
    """Make the draft, or keep the one there is (and give it another 7 days)."""
    _ok(
        await _door(
            db, session, guild_id, "SELECT dmbot_web_wizard_start(%s, %s) AS found",
            (_text(name, NAME_MAX, "name"),),
        )
    )  # fmt: skip


async def update(
    db: Database, session: Session, guild_id: int, step: int, patch: dict[str, Any]
) -> None:
    """Save the answers of a step and move to it."""
    _count(step, "patch")
    _ok(
        await _door(
            db, session, guild_id, "SELECT dmbot_web_wizard_set(%s, %s, %s) AS found",
            (step, Jsonb(check_patch(patch))),
        )
    )  # fmt: skip


async def skip(db: Database, session: Session, guild_id: int, step: int) -> None:
    _count(step, "patch")
    _ok(
        await _door(db, session, guild_id, "SELECT dmbot_web_wizard_skip(%s, %s) AS found", (step,))
    )


async def add_document(
    db: Database,
    session: Session,
    guild_id: int,
    *,
    slot: str,
    name: str,
    source: str,
    text: str = "",
    drive_id: str = "",
    pages: int = 0,
    words: int = 0,
    status: str = "ok",
    reason: str = "",
) -> str:
    """Keep one checked document in a slot; the id the page removes it by."""
    if (
        slot not in SLOTS
        or source not in SOURCES
        or status not in STATUSES
        or reason not in REASONS
    ):
        raise RecapError("bad_document")
    name = _text(name, 200, "document")
    drive_id = _text(drive_id, 200, "document")
    pages, words = _count(pages, "document"), _count(words, "document")
    if not isinstance(text, str) or "\x00" in text or not name:
        raise RecapError("bad_document")
    found = _ok(
        await _door(
            db, session, guild_id,
            "SELECT dmbot_web_wizard_doc_add(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) AS found",
            (slot, name, source, drive_id, text, pages, words, status, reason),
        )
    )  # fmt: skip
    return str(found["id"])


async def remove_document(db: Database, session: Session, guild_id: int, doc_id: str) -> None:
    _ok(
        await _door(
            db, session, guild_id, "SELECT dmbot_web_wizard_doc_remove(%s, %s) AS found", (doc_id,)
        )
    )


async def confirm_rights(db: Database, session: Session, guild_id: int, kind: str) -> None:
    """The right-to-use confirmation: `shared` for lore, secrets, house rules and homebrew;
    `ruleset` for an uploaded ruleset. Recorded with the account and the time."""
    _ok(
        await _door(
            db, session, guild_id, "SELECT dmbot_web_wizard_rights(%s, %s) AS found", (kind,)
        )
    )


async def discard(db: Database, session: Session, guild_id: int) -> None:
    _ok(await _door(db, session, guild_id, "SELECT dmbot_web_wizard_discard(%s) AS found", ()))


async def finish(db: Database, session: Session, guild_id: int) -> str:
    """Queue the finish run (the bot makes the campaign); the run's id. A second press while one
    is going gets that run's id."""
    found = _ok(
        await _door(db, session, guild_id, "SELECT dmbot_web_wizard_finish(%s) AS found", ())
    )
    return str(found["id"])


async def finish_state(db: Database, session: Session, guild_id: int) -> dict[str, Any] | None:
    """The latest finish run: state, stage, part of parts, error word, the campaign once it
    exists, and the times the progress card shows. Counts and words only."""
    return await _door(
        db, session, guild_id, "SELECT dmbot_web_wizard_finish_state(%s) AS found", ()
    )


async def members(db: Database, session: Session, guild_id: int, q: str) -> list[dict[str, Any]]:
    """This server's members the wizard's step 3 can offer as a character's player (#4204):
    `[{userId, name, before}]`, those who played with this DM in this server's earlier campaigns
    first (`before`), then by name; at most 50, matched by name when `q` is given. Only this one
    server is read, and only members recorded now. Anyone not allowed to use the wizard here gets
    "not found"."""
    _guild_of(session, guild_id)
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_wizard_members(%s, %s, %s) AS r", (guild_id, TERMS_VERSION, q[:60])
        )
        found = await cur.fetchone()
    got = found["r"] if found else None
    if got is None:
        raise RecapError("not_found")
    return [
        {"userId": str(m["userId"]), "name": str(m["name"]), "before": bool(m["before"])}
        for m in got
    ]
