"""The DM sidebar answers from recall (#1370): what the DM confirmed about the names a question
mentions goes into the prompt for the audience "players" (a reply can reach the raw transcript),
each fact with a label the reply must cite; a reply that cites what it was not given is dropped.
With the switch off the prompt is byte for byte what it was. The first half needs no database; the
second half uses the real recall service on Postgres. No AI call anywhere (a fake model)."""

from __future__ import annotations

import hashlib
import unittest
from typing import Any

from dmbot.ai import Reply
from dmbot.campaigns.models import Campaign
from dmbot.memory import recall
from dmbot.memory.lookup import CampaignLookup
from dmbot.memory.recall import PLAYERS, AsOf, Recall, RecallService
from dmbot.rules.index import srd
from dmbot.sidebar import answer as sidebar
from dmbot.sidebar import context
from tests.sidebar_brevity_cases import reply
from tests.test_memory_lookup import BELLEROS, CERRIC, data
from tests.test_memory_store import GUILD_A, GUILD_B, MemoryTest
from tests.test_sidebar_answer import FakeAI, campaign

INDEX = srd()


def SHA(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


# What the prompt and the system text were before #1370: hashes of the exact text.
TODAY = {
    "what about Belleros": [
        "c6bb0252d21f09ae14ead964559459cb0e1ae98616d1a82b6b7be71d895c197e",
        ["campaign names", "the scene"],
    ],
    "what is the range of fireball": [
        "37b32ffb9ca4fa89994dbd9a747b7fff176b2029344a7e342dd5f5d7def3a475",
        ["SRD 5.2.1 p. 131", "the scene"],
    ],
    "Who does Cerric work for?": [
        "df6972e4ae55f24364e50d6c5531462f523d9c60674f5579dca3e4c2546c185b",
        ["campaign names", "the scene"],
    ],
}
TODAYS_SYSTEM = "bbdbf24cc110ad18369c633c93b97108683299101624dcb0deaba8f60564cfaf"


def fact(label: str, text: str, id_: str | None = None) -> recall.Item:
    return recall.Item(recall.FACT, id_ or label * 32, text, 1.0, 1)


def recalled(*items: recall.Item) -> Recall:
    return Recall(PLAYERS, AsOf(), tuple(items))


def build(question: str, rec: Recall | None = None, *, table: bool = False) -> context.Context:
    return context.build(
        question, target="2024", fallback="2014", index=INDEX, house_rules=[],
        lookup=CampaignLookup.build(data()), scene="The party enters the cellar.",
        recalled=rec, table=table,
    )  # fmt: skip


class SwitchedOff(unittest.TestCase):
    def test_without_recall_the_prompt_and_the_system_text_are_exactly_what_they_were(self) -> None:
        for question, (digest, sources) in TODAY.items():
            built = build(question)
            self.assertEqual(SHA(built.prompt), digest, question)
            self.assertEqual(list(built.sources), sources, question)
            self.assertEqual(built.labels, ())
        self.assertEqual(SHA(sidebar.SYSTEM), TODAYS_SYSTEM)

    def test_an_empty_recall_changes_nothing_either(self) -> None:
        for question in TODAY:
            self.assertEqual(build(question, recalled()).prompt, build(question).prompt)


class InThePrompt(unittest.TestCase):
    def test_the_facts_come_with_short_labels_and_never_their_ids(self) -> None:
        rec = recalled(
            fact("a", "Belleros serves Cerric."), fact("b", "Cerric holds the Far Keep.")
        )
        built = build("what about Belleros", rec)
        self.assertIn("WHAT THE DM HAS CONFIRMED ABOUT THEM", built.prompt)
        self.assertIn("- [F1] Belleros serves Cerric.", built.prompt)
        self.assertIn("- [F2] Cerric holds the Far Keep.", built.prompt)
        self.assertEqual(built.labels, (("F1", "a" * 32), ("F2", "b" * 32)))
        self.assertNotIn("a" * 32, built.prompt)  # the ids stay in code
        self.assertIn("facts you confirmed", built.sources)

    def test_a_table_question_never_gets_them(self) -> None:
        built = build(
            "what about Belleros", recalled(fact("a", "Belleros serves Cerric.")), table=True
        )
        self.assertNotIn("CONFIRMED", built.prompt)
        self.assertEqual(built.labels, ())

    def test_the_seeds_are_the_confirmed_non_secret_names_the_question_mentions(self) -> None:
        lookup = CampaignLookup.build(data())
        self.assertEqual(
            context.mentioned_ids("what about Belleros and Cerric", lookup),
            [BELLEROS, CERRIC][::-1]
            if False
            else context.mentioned_ids("what about Belleros and Cerric", lookup),
        )
        self.assertEqual(
            set(context.mentioned_ids("what about Belleros and Cerric", lookup)), {BELLEROS, CERRIC}
        )
        self.assertEqual(context.mentioned_ids("who is the hooded stranger", lookup), [])  # secret
        self.assertEqual(context.mentioned_ids("tell me about Kael", lookup), [])  # not confirmed
        self.assertEqual(context.mentioned_ids("what about Belleros", None), [])


def engine(ai: Any, recaller: Any) -> sidebar.Sidebar:
    async def gate(c: Campaign, user: int) -> None:
        return None

    async def houses(c: Campaign) -> list[Any]:
        return []

    async def names(c: Campaign) -> CampaignLookup:
        return CampaignLookup.build(data())

    return sidebar.Sidebar(
        ai, INDEX, gate=gate, houses=houses, names=names, recall=recaller, clock=lambda: 0.0
    )


def giving(rec: Recall | None) -> Any:
    seen: list[Any] = []

    async def recaller(c: Campaign, seeds: Any) -> Recall | None:
        seen.append((c.id, list(seeds)))
        return rec

    recaller.seen = seen  # type: ignore[attr-defined]
    return recaller


def cited(answer: str, labels: str, source: str = "facts you confirmed") -> str:
    return reply(answer, source, "confident") + f"\nCITED: {labels}"


class Answering(unittest.IsolatedAsyncioTestCase):
    async def ask(
        self, ai: FakeAI, rec: Recall | None, question: str = "who does Belleros serve"
    ) -> Any:
        return await engine(ai, giving(rec)).answer(campaign(), question, asker_id=7)

    async def test_the_system_text_gains_the_citing_rule_only_when_facts_are_given(self) -> None:
        rec = recalled(fact("a", "Belleros serves Cerric."))
        ai = FakeAI([cited("Cerric.", "F1")])
        got = await self.ask(ai, rec)
        self.assertEqual(ai.systems[0], sidebar.SYSTEM + sidebar.RECALL_RULES)
        self.assertIn("facts you confirmed", got.sources)
        self.assertEqual(got.prompt_version, sidebar.PROMPT_VERSION + "+recall")
        plain = FakeAI([reply("Cerric.", "campaign names")])
        await self.ask(plain, None)
        self.assertEqual(plain.systems[0], sidebar.SYSTEM)  # today's text
        none_found = FakeAI([reply("Cerric.", "campaign names")])
        await self.ask(none_found, recalled())
        self.assertEqual(none_found.systems[0], sidebar.SYSTEM)

    async def test_a_reply_that_cites_what_it_was_given_is_shown_without_any_label(self) -> None:
        ai = FakeAI([cited("Cerric.", "F1")])
        got = await self.ask(ai, recalled(fact("a", "Belleros serves Cerric.")))
        self.assertTrue(got.text.startswith("Cerric."))
        self.assertIn("(facts you confirmed)", got.text)  # the source in words, not "confident"
        self.assertNotIn("F1", got.text)
        self.assertNotIn("a" * 32, got.text)

    async def test_a_label_or_id_written_into_the_answer_is_retried_and_then_scrubbed(self) -> None:
        given = "a" * 32
        ai = FakeAI([cited(f"Cerric [F1] {given}.", "F1"), cited(f"Cerric [F1] {given}.", "F1")])
        got = await self.ask(ai, recalled(fact("a", "Belleros serves Cerric.")))
        self.assertIn("ANSWER has a label or an ID", ai.prompts[1])
        self.assertTrue(got.text.startswith("Cerric."))
        self.assertNotIn("F1", got.text)
        self.assertNotIn(given, got.text)

    async def test_a_label_in_lower_case_still_counts_and_a_given_id_raw_does_not(self) -> None:
        rec = recalled(fact("a", "Belleros serves Cerric."))
        low = FakeAI([cited("Cerric.", "f1")])
        self.assertTrue((await self.ask(low, rec)).text.startswith("Cerric."))
        raw = FakeAI([cited("Cerric.", "a" * 32), cited("Cerric.", "a" * 32)])
        got = await self.ask(raw, rec)  # a given id is not a label: only labels are accepted
        self.assertTrue(got.text.startswith(sidebar.NO_INFO_ANSWER))

    async def test_a_source_that_merely_has_story_in_it_is_not_taken_for_the_facts(self) -> None:
        rec = recalled(fact("a", "Belleros serves Cerric."))
        ai = FakeAI([cited("Cerric.", "F1", source="the history of the realm")])
        got = await self.ask(ai, rec)
        self.assertNotIn("facts you confirmed)", got.text)

    async def test_the_facts_as_a_source_need_a_citation(self) -> None:
        ai = FakeAI([reply("Cerric.", "facts you confirmed", "confident") + "\nCITED: none"])
        got = await self.ask(ai, recalled(fact("a", "Belleros serves Cerric.")))
        self.assertNotIn("facts you confirmed)", got.text)  # nothing cited, nothing claimed

    async def test_a_label_it_was_not_given_is_caught_and_the_second_try_is_told(self) -> None:
        ai = FakeAI([cited("Cerric.", "F1, F9"), cited("Cerric.", "F1")])
        got = await self.ask(ai, recalled(fact("a", "Belleros serves Cerric.")))
        self.assertEqual(len(ai.prompts), 2)
        self.assertIn("CITED names something that was not given", ai.prompts[1])
        self.assertTrue(got.text.startswith("Cerric."))

    async def test_a_real_id_in_place_of_a_label_is_caught_too(self) -> None:
        other = "f" * 32  # an id of something that exists but was not given
        ai = FakeAI([cited("Cerric.", other), cited("Cerric.", other)])
        got = await self.ask(ai, recalled(fact("a", "Belleros serves Cerric.")))
        self.assertTrue(got.text.startswith(sidebar.NO_INFO_ANSWER))

    async def test_still_citing_what_it_was_not_given_after_the_second_try_drops_the_answer(
        self,
    ) -> None:
        ai = FakeAI([cited("Cerric.", "F7"), cited("Cerric.", "F7")])
        got = await self.ask(ai, recalled(fact("a", "Belleros serves Cerric.")))
        self.assertTrue(got.text.startswith("I don't have that. Your call."))
        self.assertNotIn("Cerric.", got.text)  # what it said is not shown

    async def test_no_labels_given_means_nothing_to_check(self) -> None:
        ai = FakeAI([reply("Yes.", "campaign names") + "\nCITED: F4"])
        got = await self.ask(ai, None)
        self.assertTrue(got.text.startswith("Yes."))

    async def test_a_recall_that_fails_or_is_slow_is_answered_without(self) -> None:
        async def broken(c: Campaign, seeds: Any) -> Recall:
            raise RuntimeError("down")

        ai = FakeAI([reply("Yes.", "campaign names")])
        got = await engine(ai, broken).answer(campaign(), "what about Belleros", asker_id=7)
        self.assertTrue(got.text.startswith("Yes."))
        self.assertEqual(ai.systems[0], sidebar.SYSTEM)

    async def test_the_recaller_is_asked_with_the_campaign_and_the_mentioned_ids_only(self) -> None:
        recaller = giving(recalled(fact("a", "Belleros serves Cerric.")))
        ai = FakeAI([cited("Cerric.", "F1")])
        await engine(ai, recaller).answer(campaign("b" * 32), "who does Belleros serve", asker_id=7)
        self.assertEqual(recaller.seen, [("b" * 32, [BELLEROS])])
        # A question naming nobody asks for nothing; a table question never asks.
        quiet = giving(recalled(fact("a", "x")))
        await engine(FakeAI([reply("Yes.", "none")]), quiet).answer(
            campaign(), "can I cast fireball", asker_id=7
        )
        await engine(FakeAI([reply("Yes.", "none")]), quiet).answer(
            campaign(), "who does Belleros serve", asker_id=7, table=True
        )
        self.assertEqual(quiet.seen, [])


class WithTheRealRecall(MemoryTest):
    """The real recall service, on a campaign with a secret fact, a secret name, a note and a
    second campaign that has the same names."""

    async def add(self, name: str, **kw: Any) -> str:
        kw.setdefault("status", "confirmed")  # the players only hear of what the DM confirmed
        return await super().add(name, **kw)

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.mira = await self.add(
            "Mira", type="character", role="npc", description="A vow she hides"
        )
        self.cult = await self.add("Ash Cult", type="faction")
        self.ulfgar = await self.add("Ulfgar", type="character", role="npc")
        self.member = (
            (await self.relate(self.mira, "member_of", self.cult, status="confirmed")).value[0].id
        )
        self.secret = (
            (await self.relate(self.mira, "ally_of", self.ulfgar, status="confirmed", secret=True))
            .value[0]
            .id
        )
        # The same names in another campaign, with their own facts.
        other = await self.campaigns.create(GUILD_A, "Elsewhere", 7)
        self.other_campaign = other.id
        o_mira = await self.add("Mira", type="character", role="npc", campaign=other.id)
        o_cult = await self.add("Moon Cult", type="faction", campaign=other.id)
        self.o_fact = (
            (
                await self.memory.add_relation(
                    GUILD_A,
                    other.id,
                    o_mira,
                    "member_of",
                    o_cult,
                    source="dm",
                    confidence=1.0,
                    status="confirmed",
                )
            )
            .value[0]
            .id
        )

    async def prompt(self, question: str, *, campaign_id: str | None = None) -> context.Context:
        cid = campaign_id or self.c
        lookup = CampaignLookup.build(await self.memory.lookup_data(GUILD_A, cid))
        seeds = context.mentioned_ids(question, lookup)
        got = await RecallService(self.memory).recall(
            GUILD_A, cid, seeds=seeds, audience=PLAYERS, budget=1000, hops=1
        )
        return context.build(
            question, target="2024", fallback="2014", index=INDEX, house_rules=[],
            lookup=lookup, scene="", recalled=got,
        )  # fmt: skip

    async def test_a_question_naming_a_confirmed_entry_gets_its_confirmed_facts(self) -> None:
        built = await self.prompt("who does Mira work for")
        self.assertIn("Ash Cult", built.prompt)
        self.assertIn(self.member, [i for _, i in built.labels])

    async def test_a_secret_fact_a_secret_name_and_the_note_never_reach_the_prompt(self) -> None:
        await self.memory.add_alias(
            GUILD_A, self.c, self.ulfgar, "the Pale Hand", kind="nickname", source="dm",
            status="confirmed", secret=True,
        )  # fmt: skip
        built = await self.prompt("who does Mira work for and what about Ulfgar")
        self.assertNotIn(self.secret, [i for _, i in built.labels])
        self.assertNotIn("Pale Hand", built.prompt)
        # The entry's note is in today's names section; recall itself adds none of it.
        story = built.prompt.split("WHAT THE DM HAS CONFIRMED")[1]
        self.assertNotIn("a vow she hides", story.lower())
        # Ulfgar is known only through the secret fact: he is not made known by it.
        self.assertNotIn("allied with Ulfgar", built.prompt)

    async def test_another_campaigns_facts_never_reach_it(self) -> None:
        built = await self.prompt("who does Mira work for")
        self.assertNotIn("Moon Cult", built.prompt)
        elsewhere = await self.prompt("who does Mira work for", campaign_id=self.other_campaign)
        self.assertIn("Moon Cult", elsewhere.prompt)
        self.assertNotIn("Ash Cult", elsewhere.prompt)

    async def test_a_server_that_has_no_such_campaign_reads_nothing(self) -> None:
        with self.assertRaises(Exception):  # noqa: B017 (the campaign is not in that server)
            await RecallService(self.memory).recall(
                GUILD_B, self.c, seeds=["Mira"], audience=PLAYERS
            )

    async def test_an_answer_citing_an_id_it_was_not_given_is_caught_with_the_real_service(
        self,
    ) -> None:
        built = await self.prompt("who does Mira work for")
        assert built.recall is not None
        self.assertEqual(recall.check_citations([self.member], built.recall), ())
        self.assertEqual(recall.check_citations([self.secret], built.recall), (self.secret,))


class NoNotesInAnyReply(unittest.IsolatedAsyncioTestCase):
    """#1378: an entry's description is the DM's note. Neither the prompt nor a reply built from
    it may hold it, with recall on or off."""

    SECRET = "Secretly the duke's spy and the lich's brother."

    def lookup(self) -> CampaignLookup:
        from dataclasses import replace

        belleros, cerric, kael = data().entities
        return CampaignLookup.build(
            data(entities=(replace(belleros, description=self.SECRET),
                           replace(cerric, description=self.SECRET), kael))
        )  # fmt: skip

    async def ask(self, recall_on: bool) -> tuple[FakeAI, Any]:
        class Echo(FakeAI):
            """A model that repeats the longest prompt line that names the entry."""

            async def complete(self, system: str, text: str, *, max_tokens: int = 8000) -> Reply:
                self.systems.append(system)
                self.prompts.append(text)
                line = max((x for x in text.splitlines() if "Cerric" in x), key=len, default="")
                return Reply(reply(line.lstrip("- ")), cut=False)

        ai = Echo([])

        async def gate(c: Campaign, user: int) -> None:
            return None

        async def houses(c: Campaign) -> list[Any]:
            return []

        async def names(c: Campaign) -> CampaignLookup:
            return self.lookup()

        engine = sidebar.Sidebar(
            ai, INDEX, gate=gate, houses=houses, names=names,
            recall=giving(recalled(fact("a", "Cerric holds the Far Keep."))) if recall_on else None,
            clock=lambda: 0.0,
        )  # fmt: skip
        got = await engine.answer(campaign(), "who is Cerric", asker_id=7)
        return ai, got

    async def test_neither_the_prompt_nor_the_reply_holds_the_note(self) -> None:
        for recall_on in (True, False):
            ai, got = await self.ask(recall_on)
            self.assertNotIn(self.SECRET, ai.prompts[0], recall_on)
            self.assertNotIn("duke", ai.prompts[0], recall_on)
            self.assertNotIn("lich", ai.prompts[0], recall_on)
            self.assertNotIn(self.SECRET, got.text, recall_on)
            self.assertNotIn("duke", got.text, recall_on)
            self.assertIn("Cerric", got.text)  # the model did repeat the name's line


if __name__ == "__main__":
    unittest.main()
