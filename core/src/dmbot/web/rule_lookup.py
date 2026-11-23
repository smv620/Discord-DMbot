"""The website's rule lookup (#5156): a DM types a spell, a condition, a creature or a rule and
gets the same card `/dmbot rule` sends, with its source.

It calls `rules.lookup.find`, the search Discord uses, so the two cannot drift. What `find` is
handed comes from the campaign's own two web doors (house rules, homebrew), which answer for the
campaign's server only. Only the campaign's DMs may ask (the player's card, with its DM-only
leaving-out, is a later slice); anyone else gets "not found". Nothing is stored and the typed
words are never logged, here or by the route.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from dmbot.db import Database
from dmbot.rules import homebrew_packs, lookup
from dmbot.rules.followed import FollowedRule, own_first
from dmbot.rules.homebrew import Homebrew, name_key
from dmbot.rules.house import HouseRule
from dmbot.web import homebrew as homebrew_page
from dmbot.web import house_rules as house_page
from dmbot.web.campaign_page import read_campaign  # (the page's own DM check)
from dmbot.web.recaps import RecapError
from dmbot.web.sessions import Session

TYPED_MAX = 100  # Discord's box takes 100 characters (`ui.rule_lookup.BOX_MAX`)


@dataclass(frozen=True, slots=True)
class _Table:
    name: str
    target_ruleset: str
    fallback_ruleset: str


def _rules(found: dict[str, Any], campaign_id: str) -> list[HouseRule]:
    """The campaign's own rules (the website's door lists only those and the merged ones), then
    those of a set it follows, in the order the bot reads them (`own_first`)."""
    own = [
        HouseRule(
            number=int(r["number"]), campaign_id=campaign_id, rule=r["rule"],
            supersedes=r["supersedes"], scenario=None, session_id=None, created_by=0,
            created_at=0, updated_at=0, from_set=r.get("fromSet"),
        )
        for r in found["rules"]
    ]  # fmt: skip
    followed = [
        FollowedRule(
            number=r["number"], campaign_id=campaign_id, rule=r["rule"],
            supersedes=r["supersedes"], scenario=None, session_id=None, created_by=0,
            created_at=0, updated_at=0, set_name=str(s["name"]), set_version=int(s["version"]),
        )
        for s in found["sets"]
        for r in house_page._followed(s["rules"])
    ]  # fmt: skip
    return own_first(own, followed)


def _entry(raw: dict[str, Any], campaign_id: str, source: str | None = None) -> Homebrew:
    return Homebrew(
        id=-1 if source else 0, campaign_id=campaign_id, kind=raw["kind"], name=raw["name"],
        text=raw["text"], summary=raw.get("summary"), created_by=0, created_at=0, updated_at=0,
        from_book=bool(raw.get("fromBook", False)), book_edition=raw.get("bookEdition"),
        source=source, enabled=bool(raw.get("enabled", True)),
        source_kind=str(raw.get("sourceKind") or "typed"), credit_kind=raw.get("creditKind"),
        credit_text=raw.get("creditText"), credit_domain=raw.get("creditDomain"),
    )  # fmt: skip


def _own_brew(found: dict[str, Any], campaign_id: str, typed: str) -> Homebrew | None:
    """The campaign's own entry of this name that is on, else a followed pack's: the campaign's
    own shadows a pack's, as the bot's reader does."""
    key = name_key(typed)
    for raw in found["entries"]:
        if name_key(raw["name"]) == key and raw.get("enabled", True):
            return _entry(raw, campaign_id)
    for pack in found.get("packs", []):
        for raw in pack["entries"]:
            if name_key(raw["name"]) == key:
                cite = homebrew_packs.cite(str(pack["name"]), int(pack["version"]))
                return _entry(raw, campaign_id, source=cite)
    return None


async def look_up(db: Database, session: Session, ref: str, typed: str) -> dict[str, Any]:
    """The card for `typed`: `{"kind", "parts", "close"}`. Raises `not_found` for anyone who is
    not one of the campaign's DMs, and `bad_text` for an empty or too long name."""
    name = " ".join(typed.split())
    if not name or len(name) > TYPED_MAX:
        raise RecapError("bad_text")
    page = await read_campaign(db, session, ref)
    settings = page.get("settings")
    if page.get("role") == "player" or settings is None:
        raise RecapError("not_found")
    rules = await house_page._read(db, session, ref)
    campaign_id, brew = await homebrew_page._read(db, session, ref)
    table = _Table(str(page["name"]), settings["mainRules"], settings["fallbackRules"])
    answer = lookup.find(
        table,
        name,
        _rules(rules[2], campaign_id),
        _own_brew(brew, campaign_id, name),
        is_dm=True,
    )
    return {
        "kind": answer.kind,
        "parts": answer.parts,
        "close": [e.name for e in answer.close],
    }
