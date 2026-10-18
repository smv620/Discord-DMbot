"""Two campaigns, two servers: nothing one's DM keeps reaches the other (#1870).

CLAUDE.md, Hard rules, "Campaign and server isolation": every query, cache, file and AI prompt is
scoped to one campaign, and nothing crosses between campaigns or Discord servers. The coverage
report listed store methods that take a guild or campaign and had branches no test ran. Each
test here fills campaign A and campaign B with the same names (and a secret name in B only), then
reads or changes through A and asserts nothing of B came out or was touched. Each group has a
positive control, so a test cannot pass because the setup was empty. Needs Postgres.
"""

from __future__ import annotations

from typing import Any

from dmbot.campaigns import CampaignStore
from dmbot.memory.backup import MemorySection
from dmbot.memory.models import Heard
from dmbot.memory.store import MemoryStore
from tests.pg import DatabaseTest

SERVER_ONE, SERVER_TWO = 111, 222
DM, PLAYER = 7, 1234
SHARED = "Belleros"  # said in every campaign
ONLY_IN_B = "Tessaly"  # a name that exists in campaign B only
SECRET_IN_B = "The Hooded Stranger"  # B's secret name


class TwoCampaigns(DatabaseTest):
    """A and B in one server, S in another; all three hold `Belleros`, B also holds more."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.now = 1_700_000_000
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

    async def fill(self) -> str:
        """The same name in all three, and B's own and secret names; returns B's `Tessaly` id."""
        for guild, campaign in ((SERVER_ONE, self.a), (SERVER_ONE, self.b), (SERVER_TWO, self.s)):
            await self.name(guild, campaign, SHARED)
        tessaly = await self.name(SERVER_ONE, self.b, ONLY_IN_B)
        await self.memory.add_alias(
            SERVER_ONE, self.b, tessaly, SECRET_IN_B, kind="nickname", source="dm",
            status="confirmed", secret=True,
        )  # fmt: skip
        return tessaly


class LookupAndSoundAlikes(TwoCampaigns):
    """`lookup_data` and `sound_alikes` feed the name matching that fixes what was heard."""

    async def test_a_campaigns_lookup_holds_none_of_the_others_names(self) -> None:
        await self.fill()
        data = await self.memory.lookup_data(SERVER_ONE, self.a)
        said = {e.name for e in data.entities} | {x.text for x in data.aliases}
        self.assertEqual(said, {SHARED})
        for leak in (ONLY_IN_B, SECRET_IN_B):
            self.assertNotIn(leak, said)

    async def test_the_same_read_of_the_campaign_that_has_them_does_hold_them(self) -> None:
        await self.fill()
        data = await self.memory.lookup_data(SERVER_ONE, self.b)
        said = {x.text for x in data.aliases}
        self.assertIn(ONLY_IN_B, said)
        self.assertIn(SECRET_IN_B, said)  # (the lookup keeps secrets; they are filtered later)

    async def test_sound_alikes_search_only_the_asking_campaign(self) -> None:
        from dmbot.memory.sounds import sound_codes

        await self.fill()
        codes = sorted(sound_codes(ONLY_IN_B))
        self.assertEqual(await self.memory.sound_alikes(SERVER_ONE, self.a, codes), [])
        self.assertEqual(await self.memory.sound_alikes(SERVER_TWO, self.s, codes), [])
        found = await self.memory.sound_alikes(SERVER_ONE, self.b, codes)
        self.assertIn(ONLY_IN_B, [text for _, text, _ in found])
        # A secret name is never offered as a sound-alike, even in its own campaign.
        self.assertNotIn(SECRET_IN_B, [text for _, text, _ in found])


class TablePeople(TwoCampaigns):
    """`table_people` keeps its answer per (server, campaign) until the memory changes."""

    async def make_person(self, guild: int, campaign: str, name: str) -> None:
        await self.memory.add_person_names(guild, campaign, PLAYER, [name])

    async def test_a_cached_answer_is_not_read_by_another_campaign_or_server(self) -> None:
        await self.make_person(SERVER_ONE, self.a, "Mike")
        await self.make_person(SERVER_ONE, self.b, "Mikey the Secret")
        await self.make_person(SERVER_TWO, self.s, "Michael")
        got = {}
        for key in ((SERVER_ONE, self.a), (SERVER_ONE, self.b), (SERVER_TWO, self.s)):
            got[key] = await self.memory.table_people(*key)  # fills each cache
        again = {key: await self.memory.table_people(*key) for key in got}  # now from the caches
        self.assertEqual(again, got)
        self.assertEqual([also for _, _, also in got[(SERVER_ONE, self.a)]], [["Mike"]])
        self.assertEqual([also for _, _, also in got[(SERVER_ONE, self.b)]], [["Mikey the Secret"]])
        self.assertEqual([also for _, _, also in got[(SERVER_TWO, self.s)]], [["Michael"]])

    async def test_a_change_in_one_campaign_does_not_refresh_or_spoil_anothers_cache(self) -> None:
        await self.make_person(SERVER_ONE, self.a, "Mike")
        before = await self.memory.table_people(SERVER_ONE, self.a)
        await self.make_person(SERVER_ONE, self.b, "Mikey the Secret")  # B changes, A does not
        self.assertEqual(await self.memory.table_people(SERVER_ONE, self.a), before)


class StopRecordingMe(TwoCampaigns):
    """`forget_learned`: Stop recording me in one server leaves the person's other servers alone."""

    async def test_it_only_removes_what_was_learned_in_that_server(self) -> None:
        await self.memory.learn_people(SERVER_ONE, self.a, [("Mike", PLAYER, 2)], limit=10)
        await self.memory.learn_people(SERVER_TWO, self.s, [("Mikey", PLAYER, 2)], limit=10)

        async def learned(guild: int, campaign: str) -> list[int]:
            return [
                e.played_by
                for e in await self.memory.entities(guild, campaign)
                if e.played_by == PLAYER
            ]

        self.assertTrue(await learned(SERVER_ONE, self.a))
        self.assertTrue(await learned(SERVER_TWO, self.s))
        removed = await self.memory.forget_learned(SERVER_ONE, PLAYER)
        self.assertGreater(removed, 0)
        self.assertEqual(await learned(SERVER_ONE, self.a), [])
        self.assertTrue(await learned(SERVER_TWO, self.s))  # the other server keeps its own


class CountsAndHeard(TwoCampaigns):
    """The story graph's health counts, and how often names were heard, are per campaign."""

    async def test_health_counts_are_the_askers_own(self) -> None:
        await self.fill()
        for campaign_guild, campaign, expect in (
            (SERVER_ONE, self.a, 1),
            (SERVER_ONE, self.b, 2),
            (SERVER_TWO, self.s, 1),
        ):
            row = await self.memory.health_counts(campaign_guild, campaign, since=0, recent=3)
            self.assertEqual(int(row["confirmed"]), expect, campaign)

    async def test_names_heard_in_one_campaign_are_not_counted_in_another(self) -> None:
        a = await self.name(SERVER_ONE, self.a, "Cerric")
        b = await self.name(SERVER_ONE, self.b, "Cerric")
        written = await self.memory.add_session_heard(SERVER_ONE, self.b, 1_000, [Heard(b, DM, 3)])
        self.assertEqual(written, 1)
        in_a = await self.memory.lookup_data(SERVER_ONE, self.a)
        in_b = await self.memory.lookup_data(SERVER_ONE, self.b)
        self.assertEqual([h for h in in_a.heard if h.entity_id in (a, b)], [])
        self.assertTrue([h for h in in_b.heard if h.entity_id == b])


class SavedTranscripts(TwoCampaigns):
    """Transcript lines are per session of one campaign of one server. Deleting or taking back
    lines through the wrong campaign or server changes nothing (the row-level security is the
    second lock on the server; the campaign filter is the first on the campaign)."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        from dmbot.transcript.models import Line
        from dmbot.transcript.store import TranscriptStore

        self.store = TranscriptStore(self.db)
        self.sessions: dict[str, tuple[int, str, list[int]]] = {}
        for key, guild, campaign in (
            ("a", SERVER_ONE, self.a),
            ("b", SERVER_ONE, self.b),
            ("s", SERVER_TWO, self.s),
        ):
            sid = await self.store.open_session(guild, campaign, 1_700_000_000)
            ids = await self.store.add_lines(
                guild, sid, [Line(1_700_000_001_000, DM, f"secret of {key}", f"secret of {key}")]
            )
            await self.store.end_session(guild, sid, 1_700_001_000)
            self.sessions[key] = (guild, sid, ids)

    async def texts(self, key: str) -> list[str]:
        guild, sid, _ = self.sessions[key]
        return [x.text for x in await self.store.lines(guild, sid)]

    async def test_deleting_through_another_campaign_or_server_deletes_nothing(self) -> None:
        guild, sid, _ = self.sessions["a"]
        later = 2_000_000_000
        wrong_campaign = await self.store.delete_session_transcript(guild, self.b, sid, later, 1)
        wrong_server = await self.store.delete_session_transcript(SERVER_TWO, self.a, sid, later, 1)
        self.assertEqual((wrong_campaign, wrong_server), (0, 0))
        self.assertEqual(await self.texts("a"), ["secret of a"])

    async def test_deleting_one_campaigns_session_leaves_the_others(self) -> None:
        guild, sid, _ = self.sessions["a"]
        self.assertEqual(
            await self.store.delete_session_transcript(guild, self.a, sid, 2_000_000_000, 1), 1
        )
        self.assertEqual(await self.texts("a"), [])  # (the control: it did delete its own)
        self.assertEqual(await self.texts("b"), ["secret of b"])
        self.assertEqual(await self.texts("s"), ["secret of s"])

    async def test_taking_back_lines_through_another_server_takes_back_nothing(self) -> None:
        _, sid, ids = self.sessions["a"]
        await self.store.remove_lines(SERVER_TWO, sid, ids)
        self.assertEqual(await self.texts("a"), ["secret of a"])
        await self.store.remove_lines(SERVER_ONE, sid, ids)  # the control: its own server can
        self.assertEqual(await self.texts("a"), [])


class WhatTheAIIsTold(TwoCampaigns):
    """The words that go to the AI for campaign A hold nothing of campaign B: not its names, its
    secret names, or its house rules. (The AI client itself is a stand-in; the prompt is what
    would be sent.)"""

    B_HOUSE_RULE = "Crits in the Sunken City double the dice and the damage"

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        from dmbot.rules.house import HouseRuleStore

        self.house = HouseRuleStore(self.db, clock=lambda: self.now)
        await self.fill()
        await self.house.add(SERVER_ONE, self.b, DM, self.B_HOUSE_RULE)

    async def prompt(self, guild: int, campaign: str, question: str) -> str:
        from dmbot.memory.lookup import CampaignLookup
        from dmbot.rules.index import srd
        from dmbot.sidebar import context

        lookup = CampaignLookup.build(await self.memory.lookup_data(guild, campaign))
        built = context.build(
            question, target="2024", fallback="2014", index=srd(),
            house_rules=await self.house.list(guild, campaign), lookup=lookup, scene="",
        )  # fmt: skip
        return built.prompt

    async def test_a_campaigns_prompt_holds_none_of_bs_names_or_house_rules(self) -> None:
        question = f"what do we know about {ONLY_IN_B} and {SECRET_IN_B}, and do crits double?"
        prompt = await self.prompt(SERVER_ONE, self.a, question)
        for leak in (ONLY_IN_B, "Sunken City", self.B_HOUSE_RULE):
            self.assertNotIn(leak, prompt.replace(question, ""))

    async def test_the_same_question_in_the_campaign_that_has_them_does_use_them(self) -> None:
        question = f"what do we know about {ONLY_IN_B}, and do crits double?"
        prompt = await self.prompt(SERVER_ONE, self.b, question)
        self.assertIn(f"- {ONLY_IN_B} (", prompt)
        self.assertIn(self.B_HOUSE_RULE, prompt)
        self.assertNotIn(SECRET_IN_B, prompt)  # (and even there, a secret name is not sent)

    async def test_the_other_server_has_no_house_rules_of_the_first(self) -> None:
        self.assertEqual(await self.house.list(SERVER_TWO, self.b), [])


class SharedRulesetsAreTheOnlyCrossing(TwoCampaigns):
    """CLAUDE.md: "The one exception is shared rulesets ... Only rules cross, never story memory,
    names, transcripts, notes or sheets." A campaign in the same server and one in another server
    follow a set; they get its rules, and nothing else the publisher's DM keeps."""

    NOTE = "She is secretly the lich's daughter"

    async def test_a_follower_gets_the_rules_and_none_of_the_publishers_story(self) -> None:
        from dmbot.rules.house import HouseRuleStore
        from dmbot.rules.rule_sets import RuleSetStore
        from dmbot.transcript.models import Line
        from dmbot.transcript.store import TranscriptStore

        houses = HouseRuleStore(self.db, clock=lambda: self.now)
        sets = RuleSetStore(self.db, clock=lambda: self.now)
        await houses.add(SERVER_ONE, self.a, DM, "No flanking")
        # The publisher's story: a name with a secret one, the DM's note, and a spoken line.
        tessaly = await self.name(SERVER_ONE, self.a, ONLY_IN_B, description=self.NOTE)
        await self.memory.add_alias(
            SERVER_ONE, self.a, tessaly, SECRET_IN_B, kind="nickname", source="dm",
            status="confirmed", secret=True,
        )  # fmt: skip
        store = TranscriptStore(self.db)
        sid = await store.open_session(SERVER_ONE, self.a, 1_700_000_000)
        await store.add_lines(
            SERVER_ONE, sid, [Line(1_700_000_001_000, DM, "the vault", "the vault")]
        )
        await sets.publish(
            SERVER_ONE, self.a, DM, "Frozen rules", await houses.list(SERVER_ONE, self.a), None
        )
        code = await sets.make_code(SERVER_ONE, self.a, DM)

        same_server = (await sets.shared_here(SERVER_ONE, self.b))[0]
        other_server = await sets.by_code(SERVER_TWO, DM, code)
        for follower, guild, offer in (
            (self.b, SERVER_ONE, same_server),
            (self.s, SERVER_TWO, other_server),
        ):
            await sets.follow(guild, follower, DM, offer)
            (followed,) = await sets.following(guild, follower)
            # The rules crossed ...
            self.assertEqual([r["rule"] for r in followed.content["rules"]], ["No flanking"])
            # ... and nothing of the story did, in the set itself or in the follower's own data.
            for leak in (ONLY_IN_B, SECRET_IN_B, self.NOTE, "the vault"):
                self.assertNotIn(leak, repr(followed.content), leak)
            self.assertEqual(await self.memory.entities(guild, follower), [])
            data = await self.memory.lookup_data(guild, follower)
            said = {x.text for x in data.aliases}
            self.assertFalse(said & {ONLY_IN_B, SECRET_IN_B})
            self.assertEqual(await store.sessions(guild, follower), [])
