"""Suggestions to a homebrew pack stay with their campaign (#1873, the last store of the pass).

CLAUDE.md, Hard rules, "Campaign and server isolation". A following campaign sends one of its own
homebrew entries to a pack's publisher (#1537). Two followers and two publishers sit in one
server, so only the campaign filter separates what each has sent, been told, noted, or answered.
Each refusal is paired with the same call through the right campaign. Needs Postgres.
"""

from __future__ import annotations

from dmbot.campaigns import CampaignStore
from dmbot.rules.homebrew import HomebrewStore
from dmbot.rules.pack_suggestions import PackSuggestionStore
from dmbot.rules.rule_sets import FollowedSetsSection
from tests.pg import DatabaseTest

SERVER_ONE, SERVER_TWO = 1001, 2002
DM = 11
NOW = 1_700_000_000


class TwoFollowersTwoPublishers(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.campaigns = CampaignStore(self.db, clock=lambda: NOW)
        self.campaigns.register_section(FollowedSetsSection())
        self.brew = HomebrewStore(self.db, clock=lambda: NOW)
        self.packs = PackSuggestionStore(self.db, clock=lambda: NOW)
        self.pub = (await self.campaigns.create(SERVER_ONE, "Frostmaiden", DM)).id
        self.other_pub = (await self.campaigns.create(SERVER_ONE, "Another Publisher", DM)).id
        self.far = (await self.campaigns.create(SERVER_TWO, "Far Away", DM)).id
        self.far2 = (await self.campaigns.create(SERVER_TWO, "Far Too", DM)).id
        await self.brew.add(
            SERVER_ONE, self.pub, DM, kind="monster", name="Hollow King", text="A skeleton."
        )
        await self.packs.publish_pack(
            SERVER_ONE, self.pub, DM, "Frost Pack", await self.brew.list(SERVER_ONE, self.pub)
        )
        code = await self.packs.make_pack_code(SERVER_ONE, self.pub, DM)
        self.sent: dict[str, str] = {}  # follower campaign -> the id of the suggestion it sent
        for follower, name in ((self.far, "Ember Lamp"), (self.far2, "Moss Blade")):
            offer = await self.packs.by_code(SERVER_TWO, DM, code)
            await self.packs.follow_pack(SERVER_TWO, follower, DM, offer)
            entry = await self.brew.add(
                SERVER_TWO, follower, DM, kind="item", name=name, text=f"{name} text"
            )
            sid, *_ = await self.packs.suggest_entry(
                SERVER_TWO, follower, DM, entry.id, entry.version, None
            )
            self.sent[follower] = sid


class TheFollowersOwnRecords(TwoFollowersTwoPublishers):
    async def test_a_followers_sent_list_is_its_own(self) -> None:
        mine = await self.packs.pack_sent_answers(SERVER_TWO, self.far)
        theirs = await self.packs.pack_sent_answers(SERVER_TWO, self.far2)
        self.assertEqual([s.name for s in mine], ["Ember Lamp"])
        self.assertEqual([s.name for s in theirs], ["Moss Blade"])

    async def test_marking_told_through_another_campaign_marks_nothing(self) -> None:
        await self.packs.mark_pack_told(SERVER_TWO, self.far2, [self.sent[self.far]])
        self.assertEqual(len(await self.packs.pack_sent_answers(SERVER_TWO, self.far)), 1)
        await self.packs.mark_pack_told(SERVER_TWO, self.far, [self.sent[self.far]])  # the control
        self.assertEqual(await self.packs.pack_sent_answers(SERVER_TWO, self.far), [])


class ThePublishersInbox(TwoFollowersTwoPublishers):
    async def test_another_publisher_cannot_read_or_note_a_suggestion(self) -> None:
        sid = self.sent[self.far]
        self.assertIsNone(await self.packs.pack_suggestion(SERVER_ONE, self.other_pub, sid))
        self.assertIsNotNone(await self.packs.pack_suggestion(SERVER_ONE, self.pub, sid))
        self.assertEqual(len(await self.packs.unnoted_pack_suggestions(SERVER_ONE, self.pub)), 2)
        await self.packs.mark_pack_noted(SERVER_ONE, self.other_pub, sid)
        self.assertEqual(len(await self.packs.unnoted_pack_suggestions(SERVER_ONE, self.pub)), 2)
        await self.packs.mark_pack_noted(SERVER_ONE, self.pub, sid)  # the control
        self.assertEqual(len(await self.packs.unnoted_pack_suggestions(SERVER_ONE, self.pub)), 1)
        self.assertEqual(await self.packs.unnoted_pack_suggestions(SERVER_ONE, self.other_pub), [])

    async def test_another_publisher_cannot_answer_a_suggestion(self) -> None:
        sid = self.sent[self.far]
        declined = await self.packs.decline_pack_suggestion(SERVER_ONE, self.other_pub, DM, sid)
        self.assertFalse(declined)
        got = await self.packs.pack_suggestion(SERVER_ONE, self.pub, sid)
        assert got is not None
        self.assertEqual(got.status, "open")  # still waiting for its own publisher
        self.assertTrue(await self.packs.decline_pack_suggestion(SERVER_ONE, self.pub, DM, sid))
