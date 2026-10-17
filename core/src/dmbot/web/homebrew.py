"""A campaign's homebrew on the website (#1525): read-only, for anyone in the server, with the
download of the same file Discord gives.

Two database doors (`dmbot_web_homebrew`, `dmbot_web_homebrew_campaigns`, schema.py) hand over
every entry with its on or off state: nothing is hidden from players any more (owner, 2026-10-16,
"Homebrew (part 2)"). The bot does not gate homebrew by plan, so neither does the site. The DM
adds and changes entries in Discord.

The page groups entries by kind, in the bot's order of kinds (`KINDS`), each by name as the bot
lists them. The download is `homebrew_file.write` over those entries and `homebrew_file.filename`,
so it is byte-equal to the file the bot would write for them: its own pick of the entries, as the
bot's Download button sends every entry, the ones that are off included, to the DM only.
Entries from a shared book carry `[Shared book]`, and `[Legacy 2014]` when the door says so (it
judges with `book_is_legacy`'s test, in the database, because the page has no ruleset), like the
bot's file.

The page also shows the homebrew packs the campaign follows (#1527, #1586), every entry of each
pack, as a player's `/dmbot rule` answers from them. The download stays the
campaign's own shown entries (what the bot's file holds): a followed pack's are not in it.

Each entry says where it came from (#1653), in the words of the bot's card: "from dndbeyond.com"
(read from a published page), "credited to dndbeyond.com" (a file credited to a site) or "by Sam"
(a file credited to a name the DM typed); nothing for the DM's own or an uploader's. The door sends
the credit's kind and words only.

Never shown: who added an entry (a Discord id), its id, the page's address, or the book's name (it
is never kept). A campaign is
named by `<server>.<campaign>`, as for the house-rules page.
"""

from __future__ import annotations

from typing import Any

from dmbot.db import Database
from dmbot.recaps.notify import parse_ref
from dmbot.rules import homebrew_file
from dmbot.rules.homebrew import KINDS, Homebrew, credit_label
from dmbot.web.me import MAX_GUILDS
from dmbot.web.recaps import RecapError, _guild_of, _scoped
from dmbot.web.sessions import Session


def _parse(ref: str) -> tuple[int, str]:
    parsed = parse_ref(f"{ref}.0")
    if parsed is None:
        raise RecapError("not_found")
    return parsed[0], parsed[1]


async def list_campaigns(db: Database, session: Session) -> list[dict[str, Any]]:
    """The campaigns with at least one shown entry, in every server of this session's list."""
    out: list[dict[str, Any]] = []
    async with db.user(session.user_id, session=session.id_hash) as conn:
        for guild in session.guilds[:MAX_GUILDS]:
            await _scoped(conn, int(guild.id))
            cur = await conn.execute(
                "SELECT dmbot_web_homebrew_campaigns(%s) AS found", (int(guild.id),)
            )
            row = await cur.fetchone()
            for item in (row["found"] if row else None) or []:
                out.append(
                    {
                        "ref": f"{guild.id}.{item['id']}",
                        "name": item["name"],
                        "serverName": guild.name,
                        "entries": int(item["entries"]),
                        "packs": int(item.get("packs", 0)),
                    }
                )
    return out


async def _read(db: Database, session: Session, ref: str) -> tuple[str, dict[str, Any]]:
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_homebrew(%s, %s) AS found", (guild_id, campaign_id)
        )
        row = await cur.fetchone()
    found = row["found"] if row else None
    if found is None:
        raise RecapError("not_found")
    return campaign_id, found


NAME_SHOWN = 80  # a credited name is short (`clean_name`); a cap here keeps any page tidy


def source(e: dict[str, Any]) -> str | None:
    """Where an entry came from, as words for the page: the bot's own `credit_label` ("from" a page,
    "credited to" a site, "by <name>"), so the two cannot drift. None when it is the DM's own or
    credited to the person who loaded it (a Discord user, who is never named here)."""
    text = e.get("creditText")
    entry = Homebrew(
        id=0, campaign_id="", kind="", name="", text="", summary=None, created_by=0, created_at=0,
        updated_at=0, version=1, source_kind=str(e.get("sourceKind") or "typed"),
        credit_kind=e.get("creditKind"),
        credit_text=str(text)[:NAME_SHOWN] if text else None,
        credit_domain=e.get("creditDomain"),
    )  # fmt: skip
    return credit_label(entry) or None


def _kind_order(entry: dict[str, Any]) -> int:
    return KINDS.index(entry["kind"]) if entry["kind"] in KINDS else len(KINDS)


def _pack(raw: dict[str, Any]) -> dict[str, Any]:
    """A followed pack as the page shows it: name, version and its entries by kind. Never
    the publisher or the share code (the door does not return them)."""
    # (a pack made before rules left homebrew, #1628, may still hold a rule: left out)
    kept = [e for e in raw["entries"] if e["kind"] in KINDS]
    entries = sorted(kept, key=_kind_order)  # stable: names stay in the door's order
    return {
        "name": raw["name"],
        "version": int(raw["version"]),
        "entries": [
            {"kind": e["kind"], "name": e["name"], "summary": e["summary"], "text": e["text"]}
            for e in entries
        ],
    }


async def read_homebrew(db: Database, session: Session, ref: str) -> dict[str, Any]:
    """The shown entries, grouped by kind (the bot's order of kinds), each kind by name. Anyone
    outside the server, and a campaign that is not there, get "not found"."""
    _c, found = await _read(db, session, ref)
    entries = sorted(found["entries"], key=_kind_order)  # stable: names stay in the door's order
    return {
        "name": found["name"],
        "fileName": homebrew_file.filename(found["name"]),
        "packs": [_pack(p) for p in found.get("packs", [])],
        "entries": [
            {
                "kind": e["kind"],
                "name": e["name"],
                "summary": e["summary"],
                "text": e["text"],
                # Tags like the Discord players' card: from a shared book, and a 2014 book while
                # the campaign plays a newer ruleset (booleans, false when the door is older).
                "fromBook": bool(e.get("fromBook", False)),
                "legacy": bool(e.get("legacy", False)),
                # Turned off by the DM (#1629): kept, but DMbot ignores it at the table.
                "enabled": bool(e.get("enabled", True)),
                "source": source(e),  # (#1653) never who added it
            }
            for e in entries
        ],
    }


async def render(db: Database, session: Session, ref: str) -> tuple[str, str]:
    """(the file's name, the file's text) for the shown entries. No shown entry, no file."""
    campaign_id, found = await _read(db, session, ref)
    if not found["entries"]:
        raise RecapError("not_found")
    entries = [
        Homebrew(
            id=i,
            campaign_id=campaign_id,
            kind=e["kind"],
            name=e["name"],
            text=e["text"],
            summary=e["summary"],
            created_by=0,
            created_at=0,
            updated_at=int(e["updatedAt"]),
            version=int(e["version"]),
            # Which come from a shared book, and whether the door judged one legacy: the same
            # tags as the bot's file (#1593). Absent flags (an older door) mean neither.
            from_book=bool(e.get("fromBook", False)),
            enabled=bool(e.get("enabled", True)),  # (#1629: the file says `off` like the bot's)
        )
        for i, e in enumerate(found["entries"], start=1)
    ]
    legacy_ids = {
        i for i, e in enumerate(found["entries"], start=1) if bool(e.get("legacy", False))
    }
    return homebrew_file.filename(found["name"]), homebrew_file.write(
        found["name"], entries, "", legacy=lambda entry: entry.id in legacy_ids
    )
