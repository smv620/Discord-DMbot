"""No code decides whose Discord ID is whose except the campaign's own methods (#2533, slice 2a).

A campaign stores account numbers; `Campaign.dm_user_ids` and `owner_ids` hold every Discord ID that
can act as a DM or the owner, and are for MEMBERSHIP only (`in`, `is_dm`, `is_owner`,
`sees_secrets`). Anything that sends, lists, counts or loops per DM goes by `Campaign.dm_accounts`
(one number per DM), or a person with two Discord accounts is messaged twice and counted twice
(Supervisor's ruling on the issue). This reads the source and fails on a new place that iterates,
counts or sorts those sets, or compares a Discord ID with `owner_user_id`.

A place that is right to do so is listed below with the reason; the line must be copied exactly, so
a change to it asks for another look. A listed line that is gone fails too, so the list stays true.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "dmbot"
# Not read: migrations hold SQL; devtools build campaigns by hand on a database of their own;
# campaigns/store.py is the one place that turns account numbers into Discord IDs and back.
SKIPPED = ("migrations", "devtools")
STORE = "campaigns/store.py"

SETS = r"(dm_user_ids|owner_ids)"
LOOPING = re.compile(
    rf"\bfor\s+\w+(,\s*\w+)*\s+in\s+(sorted\(|reversed\()?[\w.]*\b{SETS}\b"
    rf"|\b(len|sorted|min|max|next|iter|sum)\(\s*[\w.()]*\b{SETS}\b"
    rf"|\.join\([^)]*\b{SETS}\b"
)
OWNER_COMPARED = re.compile(
    r"owner_user_id\s*(==|!=)"
    r"|(==|!=)\s*[\w.\[\]()]*owner_user_id"
    r"|\b(in|not in)\s+[\w.]*owner_user_id"
)

ALLOWED: dict[tuple[str, str], str] = {
    (
        "bot.py",
        "next(iter(campaign.dm_user_ids))",
    ): "the one DM who may be named 'DM' beside a speaker's Discord ID; with several logins, none",
    (
        "bot.py",
        "if len(campaign.dm_user_ids) == 1 and len(campaign.dm_accounts) == 1",
    ): "the same line: only when the account has exactly one Discord ID",
    (
        "bot.py",
        "order = sorted(campaign.dm_accounts, key=lambda u: (u != campaign.owner_user_id, u))",
    ): "account numbers against the owner's account number",
    (
        "campaigns/web_backup.py",
        "if item.user_id not in campaign.dm_user_ids and item.user_id != campaign.owner_user_id:",
    ): "item.user_id is the website session's user, which is the account number",
    (
        "dm_screen/game_masters.py",
        'plan = " (plan)" if user_id == campaign.owner_user_id else ""',
    ): "user_id comes from dm_accounts: an account number",
    (
        "dm_screen/game_masters.py",
        '+ (" (plan)" if uid == campaign.owner_user_id else "")',
    ): "uid comes from dm_accounts: an account number",
    (
        "dm_screen/game_masters.py",
        "if user_id == campaign.owner_user_id:",
    ): "user_id is the account number picked from the list of game masters",
    (
        "dm_screen/web_settings.py",
        "if item.user_id == campaign.owner_user_id and campaign.paused == item.turn_on:",
    ): "item.user_id is the website session's user: the account number",
    (
        "web/handover_offer.py",
        "if campaign is None or campaign.owner_user_id != session.user_id:",
    ): "the website session's user is the account number",
}


def _offending_lines() -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for path in sorted(SRC.rglob("*.py")):
        rel = path.relative_to(SRC).as_posix()
        if rel.split("/")[0] in SKIPPED or rel == STORE:
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if LOOPING.search(line) or OWNER_COMPARED.search(line):
                found.add((rel, line.strip()))
    return found


class OnlyTheCampaignKnowsWhoIsWho(unittest.TestCase):
    def test_no_new_place_loops_over_logins_or_compares_the_owner(self) -> None:
        extra = _offending_lines() - set(ALLOWED)
        self.assertFalse(
            extra,
            "Use campaign.dm_accounts to loop, send, list or count per DM, and "
            "campaign.is_owner / campaign.is_dm for a Discord user (#2533); or list the line in "
            f"ALLOWED with the reason: {sorted(extra)}",
        )

    def test_every_listed_line_is_still_there(self) -> None:
        gone = set(ALLOWED) - _offending_lines()
        self.assertFalse(gone, f"Remove these from ALLOWED, they no longer exist: {sorted(gone)}")

    def test_the_pattern_catches_what_it_is_for(self) -> None:
        for bad in (
            "for user_id in sorted(campaign.dm_user_ids):",
            "for uid in campaign.dm_user_ids:",
            "count = len(campaign.dm_user_ids)",
            "names = ', '.join(str(u) for u in campaign.dm_user_ids)",
            "if user_id == campaign.owner_user_id:",
            "if campaign.owner_user_id != interaction.user.id:",
        ):
            with self.subTest(bad=bad):
                self.assertTrue(LOOPING.search(bad) or OWNER_COMPARED.search(bad))
        for fine in (
            "if user_id in campaign.dm_user_ids:",
            "mine = [c for c in campaigns if interaction.user.id in c.dm_user_ids]",
            "for user_id in sorted(campaign.dm_accounts):",
            "if campaign.owner_user_id is None:",
            "if campaign.is_owner(user_id):",
        ):
            with self.subTest(fine=fine):
                self.assertFalse(LOOPING.search(fine) or OWNER_COMPARED.search(fine))
