"""A person's deleted account leaves 0 in the columns of 0455 (#4148), so a backup taken after it
holds "0" for who decided, and restoring that backup must accept it (as 0432's did). And a recap
uploaded by someone who has left is still not counted against the month's allowance. Needs
Postgres for the billing test only."""

from __future__ import annotations

import unittest
from typing import Any

from dmbot.campaigns import CampaignError, CampaignStore
from dmbot.memory.pc_icons_backup import PcIconsSection
from dmbot.memory.report_backup import ReportAnswersSection
from dmbot.recaps.accepted import AcceptedSection
from dmbot.recaps.approval import text_hash
from dmbot.recaps.plot_backup import PlotFineSection
from dmbot.recaps.store import RecapStore
from dmbot.rules.homebrew_sources import SiteApprovalsSection
from dmbot.rules.wins import WinsSection
from tests.pg import DatabaseTest

HEX32 = "a" * 32
GOOD: list[tuple[Any, list[dict[str, Any]], str, str | int]] = [
    (
        AcceptedSection(),
        [
            {
                "session_at": 1,
                "version": 1,
                "text_hash": text_hash("t", "s"),
                "title": "t",
                "summary": "s",
                "accepted_by": "7",
                "accepted_at": 1,
            }
        ],
        "accepted_by",
        "7",
    ),
    (
        ReportAnswersSection(),
        [
            {
                "item_key": HEX32,
                "item_kind": "difference",
                "answer": "intended",
                "answered_by": "7",
                "answered_at": 1,
                "remind_from": None,
            }
        ],
        "answered_by",
        "7",
    ),
    (
        PlotFineSection(),
        [{"fine_key": "k1", "note": "n", "by_user": "7", "at": 1}],
        "by_user",
        "7",
    ),
    (
        PcIconsSection(),
        [
            {
                "entity_id": HEX32,
                "choice": "hidden",
                "custom_ref": None,
                "set_by": 7,
                "updated_at": 1,
            }
        ],
        "set_by",
        7,
    ),
    (SiteApprovalsSection(), [{"domain": "dndbeyond.com", "by": "7", "at": 1}], "by", "7"),
    (
        WinsSection(),
        [
            {
                "winner": {"set": "", "number": 1},
                "loser": {"set": "", "number": 2},
                "by": "7",
                "at": 1,
            }
        ],
        "by",
        "7",
    ),
]


class RestoreAcceptsSomeoneWhoLeft(unittest.TestCase):
    def test_a_person_and_zero_are_both_accepted_and_junk_is_not(self) -> None:
        for section, rows, field, person in GOOD:
            name = type(section).__name__
            with self.subTest(section=name, who="a person"):
                section.check([dict(r) for r in rows])
            zero = "0" if isinstance(person, str) else 0
            with self.subTest(section=name, who="someone who has left"):
                section.check([{**r, field: zero} for r in rows])
            bad = "x" if isinstance(person, str) else -1
            with self.subTest(section=name, who="junk"), self.assertRaises(CampaignError):
                section.check([{**r, field: bad} for r in rows])


class UploadedRecapsStayFree(DatabaseTest):
    async def test_a_recap_uploaded_by_someone_who_has_left_is_still_not_counted(self) -> None:
        campaign = await CampaignStore(self.db).create(111, "c", 7)
        async with self.db.guild(111) as conn:
            await conn.execute(
                "INSERT INTO recap_jobs (guild_id, campaign_id, session_at, dm_user_id,"
                " uploaded_by, written_by, state, created_at)"
                " VALUES (111, %s, 1, 7, 0, 'the table', 'ready', 1000)",
                (campaign.id,),
            )
        usage = await RecapStore(self.db).used_in_month(111, campaign.id, 1000)
        self.assertEqual(usage.recaps, 0)
