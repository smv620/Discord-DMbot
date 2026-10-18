"""Two campaigns, two servers, part two: what feeds the story extractor and the recap, the lookup
cache, recaps waiting to post, and suggestions to a shared set (#1870).

CLAUDE.md, Hard rules, "Campaign and server isolation". Part one (`test_isolation_pass2.py`)
covered the memory and transcript stores and the sidebar prompt. Here: the extractor's prompt and
the claims it stores, the recap's inputs, the recap store, the lookup cache, and the per-campaign
suggestion records. Each test fills campaign A and campaign B with the same names (and a secret
name in B only), then works through A and asserts nothing of B came out or was touched. Each group
has a positive control. Needs Postgres.
"""

from __future__ import annotations

import json
from collections.abc import Collection
from typing import Any

from dmbot.ai import Reply
from dmbot.campaigns import CampaignStore
from dmbot.memory import extractor
from dmbot.memory.backup import MemorySection
from dmbot.memory.claims import ClaimStore
from dmbot.memory.lookup import LookupCache
from dmbot.memory.store import MemoryStore
from dmbot.recaps.store import RecapStore
from dmbot.transcript.models import Line
from dmbot.transcript.store import TranscriptStore
from tests.pg import DatabaseTest

SERVER_ONE, SERVER_TWO = 111, 222
DM, SPEAKER = 7, 8
OPENED_AT = 1_700_000_000
SECRET_IN_B = "The Hooded Stranger"
ONLY_IN_B = "Tessaly"


class Two(DatabaseTest):
    """A and B in one server, S in another."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.now = OPENED_AT
        self.campaigns = CampaignStore(self.db, clock=lambda: self.now)
        self.campaigns.register_section(MemorySection())
        self.memory = MemoryStore(self.db, clock=lambda: self.now)
        self.a = (await self.campaigns.create(SERVER_ONE, "Frozen Wastes", DM)).id
        self.b = (await self.campaigns.create(SERVER_ONE, "Sunken City", DM)).id
        self.s = (await self.campaigns.create(SERVER_TWO, "Other Server", DM)).id

    async def name(self, guild: int, campaign: str, name: str, **kw: Any) -> str:
        kw.setdefault("type", "npc")
        written = await self.memory.add_entity(
            guild, campaign, name=name, source="dm", status="confirmed", **kw
        )
        return written.value.id


class Canned:
    """A fake AI that records what it was sent and answers with one claim on the first line."""

    def __init__(self) -> None:
        self.sent: list[str] = []

    async def complete(self, system: str, text: str, *, max_tokens: int = 0) -> Reply:
        self.sent.append(text)
        first = text.split("\n")[1].split(" ")[0]
        claim = {
            "subject": "Bram",
            "relationship": "carries",
            "object": "Heartseeker",
            "details": "",
            "lines": [int(first)],
            "how": "dm_said",
        }
        return Reply(json.dumps({"claims": [claim]}), False, 10, 5, "m-fast")


def spoken(n: int, text: str) -> Line:
    return Line(n * 1000, SPEAKER, text, text)


class StoryExtractor(Two):
    """The extractor reads one session's lines and stores what it finds under that campaign."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        for guild, campaign in ((SERVER_ONE, self.a), (SERVER_ONE, self.b), (SERVER_TWO, self.s)):
            await self.name(guild, campaign, "Bram", type="character")
            await self.name(guild, campaign, "Heartseeker", type="item")
        self.claims = ClaimStore(self.memory)

    async def extract(self, guild: int, campaign: str, text: str) -> tuple[Canned, Any]:
        async def everyone() -> Collection[int]:
            return {DM, SPEAKER}

        ai = Canned()
        report = await extractor.run(
            ai, self.claims, [spoken(n, f"{text} {n}") for n in range(1, 11)],
            guild_id=guild, campaign_id=campaign, dm_ids={DM}, consenting=everyone,
            model=lambda: "m", session_started_at=1,
        )  # fmt: skip
        return ai, report

    async def test_what_it_sends_and_stores_is_the_one_campaigns(self) -> None:
        ai_b, _ = await self.extract(SERVER_ONE, self.b, f"{ONLY_IN_B} hides the key")
        ai_a, report = await self.extract(SERVER_ONE, self.a, "the bridge is old")
        self.assertEqual(report.proposed, 1)
        self.assertNotIn(ONLY_IN_B, "".join(ai_a.sent))  # B's lines never reach A's prompt
        self.assertIn(ONLY_IN_B, "".join(ai_b.sent))  # (the control: they did reach B's)
        opted_in = {DM, SPEAKER}
        self.assertEqual(len(await self.claims.waiting(SERVER_ONE, self.a, opted_in=opted_in)), 1)
        self.assertEqual(len(await self.claims.waiting(SERVER_ONE, self.b, opted_in=opted_in)), 1)
        # The other server holds none.
        self.assertEqual(await self.claims.waiting(SERVER_TWO, self.s, opted_in=opted_in), [])


class TheRecapReads(Two):
    """`load_inputs` is what the recap writes from: this campaign's session, names, last recap."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        from tests.test_recap_jobs import Clock

        self.transcripts = TranscriptStore(self.db)
        self.recaps = RecapStore(self.db, clock=Clock())
        self.cache = LookupCache(self.memory)
        # Both campaigns played at the very same moment, so only the campaign tells them apart.
        for guild, campaign, said in (
            (SERVER_ONE, self.a, "we cross the bridge"),
            (SERVER_ONE, self.b, f"{ONLY_IN_B} opens the vault"),
        ):
            sid = await self.transcripts.open_session(guild, campaign, OPENED_AT)
            await self.transcripts.add_lines(
                guild, sid, [Line(OPENED_AT * 1000 + 1000, DM, said, said)]
            )
            await self.transcripts.end_session(guild, sid, OPENED_AT + 3600)
        await self.name(SERVER_ONE, self.a, "Belleros")
        tessaly = await self.name(SERVER_ONE, self.b, ONLY_IN_B)
        await self.memory.add_alias(
            SERVER_ONE, self.b, tessaly, SECRET_IN_B, kind="nickname", source="dm",
            status="confirmed", secret=True,
        )  # fmt: skip

    async def inputs(self, guild: int, campaign: str) -> Any:
        from dmbot.recaps.inputs import load_inputs

        return await load_inputs(
            campaigns=self.campaigns, lookup=self.cache, transcripts=self.transcripts,
            recaps=self.recaps, clocks=None, guild_id=guild, campaign_id=campaign,
            session_at=OPENED_AT,
        )  # fmt: skip

    async def test_a_campaigns_recap_reads_none_of_the_others_session_or_names(self) -> None:
        got = await self.inputs(SERVER_ONE, self.a)
        self.assertEqual([x.text for x in got.lines], ["we cross the bridge"])
        self.assertEqual(got.allowed_names, ["Belleros"])
        self.assertEqual(got.secret_names, [])

    async def test_the_same_read_of_the_other_campaign_does_hold_its_own(self) -> None:
        got = await self.inputs(SERVER_ONE, self.b)
        self.assertEqual([x.text for x in got.lines], [f"{ONLY_IN_B} opens the vault"])
        self.assertEqual(got.allowed_names, [ONLY_IN_B])
        self.assertEqual(got.secret_names, [SECRET_IN_B])

    async def test_a_session_of_another_server_is_not_found(self) -> None:
        self.assertIsNone(await self.inputs(SERVER_TWO, self.a))  # (a campaign id from elsewhere)


class TheRecapStore(Two):
    """Jobs, the earlier recap the next one builds on, and the posting queue."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        from tests.test_recap_jobs import Clock

        self.store = RecapStore(self.db, clock=Clock())

    async def make(
        self, guild: int, campaign: str, at: int, title: str, *, posted: bool = False
    ) -> None:
        """A finished recap, a draft unless `posted` (the DM said post and the bot sent it)."""
        await self.store.enqueue(guild, campaign, at, dm_user_id=DM)
        await self.store.update(
            guild,
            campaign,
            at,
            state="ready",
            title=title,
            summary=title,
        )
        if posted:
            await self.store.decide(guild, campaign, at, "post")
            await self.store.posted(guild, campaign, at)

    async def test_the_earlier_recap_a_new_one_builds_on_is_the_same_campaigns(self) -> None:
        await self.make(SERVER_ONE, self.b, 100, "Tessaly opens the vault", posted=True)
        await self.make(SERVER_ONE, self.a, 50, "The bridge", posted=True)
        before_a = await self.store.bridge(SERVER_ONE, self.a, 1_000)
        assert before_a is not None
        self.assertEqual(before_a.title, "The bridge")
        self.assertIsNone(await self.store.bridge(SERVER_TWO, self.s, 1_000))

    async def test_the_posting_queue_is_per_server(self) -> None:
        await self.make(SERVER_ONE, self.a, 100, "one")
        await self.make(SERVER_TWO, self.s, 100, "two")
        await self.store.decide(SERVER_ONE, self.a, 100, "post")
        self.assertEqual(await self.store.to_post(SERVER_ONE), [(self.a, 100)])
        self.assertEqual(await self.store.to_post(SERVER_TWO), [])  # not the other server's
        await self.store.decide(SERVER_TWO, self.s, 100, "post")
        self.assertEqual(await self.store.to_post(SERVER_TWO), [(self.s, 100)])

    async def test_a_decision_through_another_campaign_changes_nothing(self) -> None:
        await self.make(SERVER_ONE, self.a, 100, "one")
        self.assertFalse(await self.store.decide(SERVER_ONE, self.b, 100, "post"))
        self.assertFalse(await self.store.decide(SERVER_TWO, self.a, 100, "post"))
        self.assertEqual(await self.store.to_post(SERVER_ONE), [])
        self.assertTrue(await self.store.decide(SERVER_ONE, self.a, 100, "post"))  # the control


class TheLookupCache(Two):
    """One lookup per (server, campaign): a copy loaded for A is never given to B."""

    async def test_each_campaign_gets_its_own_copy_and_a_change_reloads_only_its_own(self) -> None:
        await self.name(SERVER_ONE, self.a, "Belleros")
        await self.name(SERVER_ONE, self.b, ONLY_IN_B)
        cache = LookupCache(self.memory)
        a = await cache.get(SERVER_ONE, self.a)
        b = await cache.get(SERVER_ONE, self.b)
        s = await cache.get(SERVER_TWO, self.s)
        self.assertEqual({n.text for n in a.names}, {"Belleros"})
        self.assertEqual({n.text for n in b.names}, {ONLY_IN_B})
        self.assertEqual(list(s.names), [])
        self.assertIs(await cache.get(SERVER_ONE, self.a), a)  # still the cached copy
        self.assertIsNot(a, b)


class SuggestionsToASharedSet(DatabaseTest):
    """A follower's sent suggestions and the publisher's inbox are kept per campaign: a server
    holds two followers and two publishers, and acting through one never touches the other."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        from dmbot.rules.house import HouseRuleStore
        from dmbot.rules.rule_sets import RuleSetStore

        self.campaigns = CampaignStore(self.db, clock=lambda: OPENED_AT)
        self.houses = HouseRuleStore(self.db)
        self.sets = RuleSetStore(self.db, clock=lambda: OPENED_AT)
        self.pub = (await self.campaigns.create(SERVER_ONE, "Frostmaiden", DM)).id
        self.other_pub = (await self.campaigns.create(SERVER_ONE, "Another Publisher", DM)).id
        self.far = (await self.campaigns.create(SERVER_TWO, "Far Away", DM)).id
        self.far2 = (await self.campaigns.create(SERVER_TWO, "Far Too", DM)).id
        await self.houses.add(SERVER_ONE, self.pub, DM, "No flanking")
        await self.sets.publish(
            SERVER_ONE,
            self.pub,
            DM,
            "Frostmaiden",
            await self.houses.list(SERVER_ONE, self.pub),
            None,
        )
        code = await self.sets.make_code(SERVER_ONE, self.pub, DM)
        for follower in (self.far, self.far2):
            await self.sets.follow(
                SERVER_TWO, follower, DM, await self.sets.by_code(SERVER_TWO, DM, code)
            )
        self.sent: dict[str, str] = {}  # follower campaign -> its suggestion's id
        for follower, rule in (
            (self.far, "Potions are a bonus action"),
            (self.far2, "No sneaking"),
        ):
            number = (await self.houses.add(SERVER_TWO, follower, DM, rule)).number
            version = {r.number: r for r in await self.houses.list(SERVER_TWO, follower)}[
                number
            ].version
            self.sent[follower] = (
                await self.sets.suggest(SERVER_TWO, follower, DM, number, version)
            )[0]

    async def test_a_followers_sent_list_is_its_own(self) -> None:
        mine = await self.sets.sent_answers(SERVER_TWO, self.far)
        theirs = await self.sets.sent_answers(SERVER_TWO, self.far2)
        self.assertEqual([s.rule for s in mine], ["Potions are a bonus action"])
        self.assertEqual([s.rule for s in theirs], ["No sneaking"])

    async def test_marking_told_through_another_campaign_marks_nothing(self) -> None:
        await self.sets.mark_told(SERVER_TWO, self.far2, [self.sent[self.far]])
        self.assertEqual(len(await self.sets.sent_answers(SERVER_TWO, self.far)), 1)
        await self.sets.mark_told(SERVER_TWO, self.far, [self.sent[self.far]])  # the control
        self.assertEqual(await self.sets.sent_answers(SERVER_TWO, self.far), [])

    async def test_noting_through_another_publisher_notes_nothing(self) -> None:
        self.assertEqual(len(await self.sets.unnoted_suggestions(SERVER_ONE, self.pub)), 2)
        await self.sets.mark_noted(SERVER_ONE, self.other_pub, self.sent[self.far])
        self.assertEqual(len(await self.sets.unnoted_suggestions(SERVER_ONE, self.pub)), 2)
        await self.sets.mark_noted(SERVER_ONE, self.pub, self.sent[self.far])  # the control
        self.assertEqual(len(await self.sets.unnoted_suggestions(SERVER_ONE, self.pub)), 1)
