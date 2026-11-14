"""❓ Questions for you (#3435): the count of each kind of question that waits for the DM, the
table it is written to, and what counts as an answer. Each kind is held equal to what that kind's
own list shows. Invented names and numbers throughout."""

from __future__ import annotations

import unittest
from collections.abc import Collection
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, patch

from dmbot.campaigns import CampaignStore
from dmbot.memory import readings
from dmbot.memory.backstory_links import BackstoryLinks, NewLink, NewWhich
from dmbot.memory.claims import ClaimStore, Extracted
from dmbot.memory.models import BACKGROUND_SOURCE, CONFIRMED, DM, PROPOSED
from dmbot.memory.store import MemoryStore
from dmbot.questions import counts, kinds
from dmbot.questions.counts import Counted, Sources
from dmbot.questions.store import QuestionCounts, answered, grew, total
from dmbot.recaps.approval import text_hash
from dmbot.rules.homebrew import HomebrewStore
from dmbot.rules.house import HouseRuleStore
from dmbot.rules.pack_updates import PackUpdateStore
from dmbot.rules.rule_sets import RuleSetStore
from dmbot.transcript.models import Line
from dmbot.transcript.store import TranscriptStore
from dmbot.transcript.waiting_questions import Unanswered, WaitingQuestionStore
from dmbot.web import plot as web_plot
from dmbot.web import relationships
from tests.test_memory_claims import said
from tests.test_recap_web import BOB, NOW, Base
from tests.test_web_api import ALICE, THURSDAY

START = 1_700_000_000
MS = START * 1000
SAID_BY = 7  # a speaker who agreed to be recorded
NOT_RECORDED = 8


class TheKinds(unittest.TestCase):
    def test_every_kind_has_a_label_and_a_counter(self) -> None:
        self.assertEqual(set(kinds.ORDER), set(kinds.LABELS))
        self.assertEqual(set(kinds.ORDER), set(counts._COUNTERS))
        self.assertEqual(len(kinds.ORDER), len(set(kinds.ORDER)))

    def test_the_words_match_the_websites_list(self) -> None:
        """Discord's list and the Questions page say the same words (WebDev-lead's
        `questions-words.ts`). Skipped until that file is on this branch."""
        words = Path(__file__).resolve().parents[2] / "web/src/content/questions-words.ts"
        if not words.exists():
            self.skipTest("the website's questions-words.ts is not on this branch yet")
        text = words.read_text(encoding="utf-8")
        for label in kinds.LABELS.values():
            self.assertIn(label, text)

    def test_the_words_are_the_agreed_ones(self) -> None:
        self.assertEqual(
            [kinds.LABELS[k] for k in kinds.ORDER],
            [
                "New names to check",
                "Did they mean…?",
                "Names that look alike",
                "Connections waiting",
                "Which one?",
                "Family ties",
                "Lineage and class",
                "World notes",
                "Facts to check",
                "What people believe",
                "Plot check",
                "Recaps to approve",
                "House rule updates",
            ],
        )


class WhatCountsAsAnAnswer(unittest.TestCase):
    def test_a_count_going_down_is_an_answer_and_one_going_up_is_not(self) -> None:
        before = {kinds.NAMES: Counted(3), kinds.WHICH: Counted(1)}
        self.assertTrue(answered(before, {kinds.NAMES: Counted(2), kinds.WHICH: Counted(1)}))
        self.assertFalse(answered(before, {kinds.NAMES: Counted(4), kinds.WHICH: Counted(1)}))
        self.assertFalse(answered(before, before))
        self.assertTrue(grew(before, {kinds.NAMES: Counted(4), kinds.WHICH: Counted(1)}))
        self.assertFalse(grew(before, {kinds.NAMES: Counted(2)}))

    def test_a_kind_not_counted_before_is_never_an_answer(self) -> None:
        self.assertFalse(answered({}, {kinds.NAMES: Counted(0)}))
        self.assertFalse(answered({kinds.NAMES: Counted(5)}, {}))  # a count that failed now

    def test_a_question_that_expires_alone_is_not_an_answer_but_a_given_answer_is(self) -> None:
        before = {kinds.HEARD: Counted(3, settled=4)}
        self.assertFalse(answered(before, {kinds.HEARD: Counted(0, settled=4)}))  # expired
        self.assertTrue(answered(before, {kinds.HEARD: Counted(2, settled=5)}))  # answered

    def test_the_total_adds_every_kind(self) -> None:
        self.assertEqual(total({kinds.NAMES: Counted(2), kinds.WHICH: Counted(3)}), 5)


class TheCounts(Base):
    """Each kind equals what its own list shows."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.memory = MemoryStore(self.db)
        self.heard = WaitingQuestionStore(self.db, clock=lambda: float(START))
        self.src = Sources(db=self.db, memory=self.memory, heard=self.heard)
        self.g, self.c = THURSDAY.id, self.one.id

    async def count(self, kind: str) -> Counted:
        found = await counts.count_kinds(self.src, self.g, self.c, only={kind})
        return found[kind]

    async def test_world_notes_equal_the_lines_the_screen_shows(self) -> None:
        # (#4285: DMbot's reads of places and languages the DM hasn't checked)
        from dmbot.memory import map_facts as mf
        from dmbot.memory.map_fact_store import Read

        self.assertEqual((await self.count(kinds.WORLD)).n, 0)
        made = await self.memory.add_entity(
            self.g, self.c, name="Frostvale", type="place", status="confirmed", source="dm"
        )
        await self.memory.set_place_level(self.g, self.c, made.value.id, "region", source="dm")
        await self.memory.add_read_map_facts(
            self.g, self.c, [Read(made.value.id, mf.TERRAIN, "arctic")], source="background"
        )
        shown = await self.memory.world_notes(self.g, self.c)
        got = await self.count(kinds.WORLD)
        self.assertEqual((got.n, got.oldest), (len(shown), shown[0].made_at))

    async def test_new_names_equal_the_names_the_review_opens_with(self) -> None:
        """The count equals the queue of the real 📝 Check new names review, and an old pronoun
        suggestion (hidden from every list) is in neither."""
        from unittest.mock import AsyncMock, patch

        from dmbot.ui import names as ui

        self.assertEqual((await self.count(kinds.NAMES)).n, 0)
        for name in ("Hrothgar", "Maren"):
            await self.memory.add_entity(
                self.g, self.c, type="character", name=name, source=DM, status=PROPOSED
            )
        old = await self.memory.add_entity(  # from before the pronoun gate: never offered
            self.g, self.c, type="character", name="We", source="background", status=PROPOSED
        )
        async with self.db.guild(self.g) as conn:
            await conn.execute(
                "UPDATE memory_entities SET made_before_name_gate = true WHERE id = %s",
                (old.value.id,),
            )
        await self.memory.add_entity(  # another campaign's name is not ours
            self.g, self.two.id, type="character", name="Elsewhere", source=DM, status=PROPOSED
        )
        seen: list[ui.SuggestionReview] = []

        async def capture(_it: object, _text: str, view: ui.SuggestionReview) -> None:
            seen.append(view)

        campaign = await CampaignStore(self.db).get(self.g, self.c)
        with (
            patch.object(ui, "_memory", lambda _it: self.memory),
            patch.object(ui, "_campaign_for", AsyncMock(return_value=campaign)),
            patch.object(ui, "_send", capture),
            patch.object(ui, "_offer_add_all", AsyncMock(return_value=False)),
            patch.object(ui.SuggestionReview, "prepare", AsyncMock(return_value="card")),
        ):
            await ui._review(AsyncMock(), self.memory, campaign, bulk=False)  # type: ignore[arg-type]
        (review,) = seen
        self.assertEqual((await self.count(kinds.NAMES)).n, len(review.queue))
        self.assertEqual(len(review.queue), 2)
        self.assertNotIn(old.value.id, review.queue)

    async def test_did_they_mean_equals_the_open_list_and_counts_answers_not_expiry(self) -> None:
        transcripts = TranscriptStore(self.db)
        session = await transcripts.open_session(self.g, self.c, START)
        await transcripts.add_lines(
            self.g,
            session,
            [Line(MS + 1000, 9, "I met Marin", "I met Marin"), Line(MS + 2000, 9, "Maro", "Maro")],
        )
        ids = ("a" * 32, "b" * 32)
        await self.heard.save(
            self.g,
            self.c,
            session,
            [Unanswered(9, MS + 1000, "Marin", ids), Unanswered(9, MS + 2000, "Maro", ids)],
        )
        found = await self.count(kinds.HEARD)
        self.assertEqual(
            (found.n, found.settled), (len(await self.heard.waiting(self.g, self.c)), 0)
        )
        self.assertEqual(found.n, 2)
        (first, _second) = await self.heard.waiting(self.g, self.c)
        await self.heard.decide(self.g, first.id, "answered", 7)
        after = await self.count(kinds.HEARD)
        self.assertEqual((after.n, after.settled), (1, 1))
        self.assertTrue(answered({kinds.HEARD: found}, {kinds.HEARD: after}))

    async def test_recaps_to_approve_equal_the_waiting_drafts_less_the_approved(self) -> None:
        listed = await self.store.waiting_drafts(self.g, self.c, limit=100)
        self.assertEqual((await self.count(kinds.RECAP_APPROVE)).n, len(listed))
        self.assertEqual(len(listed), 1)
        async with self.db.guild(self.g) as conn:  # the current text is approved
            await conn.execute(
                "UPDATE recap_jobs SET approved_hash = encode(sha256(convert_to(title || E'\\n'"
                " || summary, 'UTF8')), 'hex') WHERE campaign_id = %s",
                (self.c,),
            )
        self.assertEqual((await self.count(kinds.RECAP_APPROVE)).n, 0)
        # ... and a campaign's count is its own.
        self.assertEqual(
            (await counts.count_kinds(self.src, self.g, self.two.id, only={kinds.RECAP_APPROVE}))[
                kinds.RECAP_APPROVE
            ].n,
            1,
        )

    async def test_plot_equals_the_open_issues_the_recap_page_shows(self) -> None:
        job = await self.state(self.c)
        digest = text_hash(job.title, job.summary)
        async with self.db.guild(self.g) as conn:
            await conn.execute(
                "INSERT INTO plot_checks (guild_id, campaign_id, session_at, text_hash, state,"
                " created_at) VALUES (%s, %s, 100, %s, 'done', %s)",
                (self.g, self.c, digest, NOW),
            )
            for n, hash_ in enumerate((digest, digest, "0" * 64)):  # the last is an older text
                await conn.execute(
                    "INSERT INTO plot_issues (guild_id, campaign_id, id, session_at, text_hash,"
                    " kind, level, sentence, sentence_text, reason, source_text, fine_key,"
                    " created_at) VALUES (%s, %s, %s, 100, %s, 'dead_acts', 'probable', 1, 'x.',"
                    " 'r', 's', %s, %s)",
                    (self.g, self.c, f"i{n}", hash_, f"k{n}", NOW),
                )
        shown = await web_plot.read_plot(self.web, self.session, self.ref, on=True)
        open_ones = [i for i in shown["issues"] if i["answer"] is None]
        self.assertEqual((await self.count(kinds.PLOT)).n, len(open_ones))
        self.assertEqual(len(open_ones), 2)
        self.assertTrue(shown["approveBlocked"])

    async def test_a_kind_without_its_store_counts_zero_and_never_raises(self) -> None:
        bare = Sources(db=self.db)
        found = await counts.count_kinds(bare, self.g, self.c)
        self.assertEqual(
            {k: v.n for k, v in found.items() if k not in (kinds.PLOT,)},
            {k: 0 for k in kinds.ORDER if k not in (kinds.PLOT, kinds.RECAP_APPROVE)}
            | {kinds.RECAP_APPROVE: 1},
        )

    # --- the kinds whose list is read from several places (#3450): count == what the flow shows

    async def entry(self, name: str, *, campaign: str | None = None, **kw: object) -> str:
        kw.setdefault("type", "character")
        made = await self.memory.add_entity(
            self.g,
            campaign or self.c,
            name=name,
            source=DM,
            status=CONFIRMED,
            **kw,  # type: ignore[arg-type]
        )
        return made.value.id

    async def agreed(self, _guild: int) -> Collection[int]:
        return {SAID_BY}

    async def nobody(self, _guild: int) -> Collection[int]:
        return set()

    async def with_claims(self) -> ClaimStore:
        """The claim reader switched on, as the bot has it: only SAID_BY agreed to be recorded."""
        claims = ClaimStore(self.memory)
        self.src = replace(self.src, claims=claims, agreed=self.agreed)
        return claims

    async def propose(
        self, claims: ClaimStore, campaign: str, *found: Extracted, speakers: set[int] | None = None
    ) -> None:
        await claims.propose(
            self.g, campaign, list(found), {"1": SAID_BY, "2": NOT_RECORDED},
            opted_in=speakers or {SAID_BY}, dms={SAID_BY, NOT_RECORDED},
            prompt_version="p1", model="m1", session_started_at=500,
        )  # fmt: skip

    async def test_lookalikes_equal_the_groups_the_list_offers(self) -> None:
        pairs = (("Belleros", "Beleros"), ("Kellan", "Kelan"))
        ids = {}
        for one, two in pairs:
            ids[one] = [await self.entry(one), await self.entry(two)]
        await self.entry("Cerric")  # alone: in no group
        for one, two in pairs:  # another campaign's look-alikes are not ours
            await self.entry(one, campaign=self.two.id)
            await self.entry(two, campaign=self.two.id)
        groups = await self.memory.look_alikes(self.g, self.c)
        self.assertEqual((await self.count(kinds.LOOKALIKES)).n, len(groups))
        self.assertEqual(len(groups), 2)
        # "Not the same", as the list's button does, takes that group off the list
        await self.memory.not_the_same(self.g, self.c, ids["Belleros"], user_id=7)
        self.assertEqual((await self.count(kinds.LOOKALIKES)).n, 1)
        self.assertEqual(len(await self.memory.look_alikes(self.g, self.c)), 1)

    async def test_which_equals_the_which_one_questions(self) -> None:
        self.src = replace(self.src, backstory=(store := BackstoryLinks(self.db)))
        voss = await self.entry("Voss", type="npc")
        for campaign, who in ((self.c, "Mara"), (self.two.id, "Elsewhere")):
            both = [
                await self.entry(f"{who} Ash", campaign=campaign),
                await self.entry(f"{who} Vell", campaign=campaign),
            ]
            if campaign != self.c:
                voss_two = await self.entry("Voss", type="npc", campaign=campaign)
                await store.add_which(
                    self.g, campaign,
                    [NewWhich(voss_two, True, "member_of", who, tuple(both), "q")],
                )  # fmt: skip
            else:
                mara = both
        await store.add_which(
            self.g, self.c,
            [
                NewWhich(voss, True, "member_of", "Mara", tuple(mara), "q"),
                NewWhich(voss, True, "enemy_of", "Mara", tuple(mara), "q"),
            ],
        )  # fmt: skip
        asked = await store.which_waiting(self.g, self.c)
        self.assertEqual((await self.count(kinds.WHICH)).n, len(asked))
        self.assertEqual(len(asked), 2)
        await store.pick_which(self.g, self.c, asked[0].id, mara[0])  # the DM picks one
        self.assertEqual((await self.count(kinds.WHICH)).n, 1)
        self.assertEqual(len(await store.which_waiting(self.g, self.c)), 1)

    async def kept_for_an_answer(self, claims: ClaimStore, campaign: str, pc: str) -> None:
        """Two entries answer to "Mara", so a player character's family claim is kept."""
        await self.entry(pc, campaign=campaign, role="player_character", played_by=SAID_BY)
        for name in ("Mara Ashgrove", "Mara Vell"):
            made = await self.entry(name, campaign=campaign)
            await self.memory.add_alias(
                self.g, campaign, made, "Mara", kind="short", source=DM, status=CONFIRMED
            )
        await self.propose(
            claims, campaign,
            said(pc, "is the sister of", "Mara"), said(pc, "is the mother of", "Mara"),
        )  # fmt: skip

    async def test_family_ties_equal_the_claims_kept_for_an_answer(self) -> None:
        claims = await self.with_claims()
        await self.kept_for_an_answer(claims, self.c, "Mira")
        await self.kept_for_an_answer(claims, self.two.id, "Tavin")
        await self.entry("Bram")
        await self.entry("Veldmoor", type="place")
        await self.propose(claims, self.c, said("Bram", "is in", "Veldmoor"))
        family, facts = await self.count(kinds.FAMILY), await self.count(kinds.CLAIMS)
        self.assertEqual(family.n, await claims.unplaced_count(self.g, self.c))
        self.assertEqual(family.n, 2)
        # the claims list shows the kept ones too: the two kinds together are that list
        listed = await claims.listed_count(self.g, self.c, opted_in={SAID_BY})
        self.assertEqual((family.n + facts.n, facts.n), (listed, 1))
        (first, _second) = [
            c for c in await claims.waiting(self.g, self.c, opted_in={SAID_BY}) if c.reason
        ]
        (choice,) = await claims.which_one(self.g, self.c, first.id)
        await claims.pick_one(self.g, self.c, first.id, choice.side, choice.entries[0].id)
        self.assertEqual((await self.count(kinds.FAMILY)).n, 1)
        # (the answered claim is an ordinary fact to check now, so the two still add up)
        after = (await self.count(kinds.FAMILY)).n + (await self.count(kinds.CLAIMS)).n
        self.assertEqual(after, await claims.listed_count(self.g, self.c, opted_in={SAID_BY}))

    async def test_family_ties_equal_the_card_and_skip_a_speaker_who_stopped(self) -> None:
        """The message's count and the card's list are one list (#3463): a kept claim whose
        speaker stopped being recorded is in neither."""
        claims = await self.with_claims()
        await self.kept_for_an_answer(claims, self.c, "Mira")
        card = await claims.unplaced(self.g, self.c, opted_in={SAID_BY})
        self.assertEqual((await self.count(kinds.FAMILY)).n, len(card))
        self.assertEqual(len(card), 2)  # (the helper keeps two claims for the name)
        self.src = replace(self.src, agreed=self.nobody)  # the speaker stopped being recorded
        self.assertEqual((await claims.unplaced(self.g, self.c, opted_in=set())), [])
        self.assertEqual((await self.count(kinds.FAMILY)).n, 0)
        # the facts-to-check count follows the same list, so the two still add up to the page
        self.assertEqual((await self.count(kinds.CLAIMS)).n, 0)

    async def test_facts_to_check_equal_the_claims_waiting_and_skip_a_speaker_not_recorded(
        self,
    ) -> None:
        claims = await self.with_claims()
        for name in ("Bram", "Hesper"):
            await self.entry(name)
        await self.entry("Veldmoor", type="place")
        await self.memory.add_entity(
            self.g, self.c, name="Zed", type="character", source=DM, status=PROPOSED
        )
        await self.kept_for_an_answer(claims, self.c, "Mira")  # listed, but its own kind
        await self.propose(
            claims, self.c,
            said("Bram", "is in", "Veldmoor"),
            said("Hesper", "is in", "Veldmoor"),
            said("Bram", "knows", "Zed"),  # a still-proposed name: in neither the list nor count
            Extracted("Bram", "is the enemy of", "Hesper", "", ("2",), "dm_said"),  # not recorded
            speakers={SAID_BY, NOT_RECORDED},
        )  # fmt: skip
        async with self.db.guild(self.g) as conn:
            cur = await conn.execute(
                "SELECT count(*) AS n FROM memory_claims WHERE campaign_id = %s"
                " AND status = 'proposed' AND %s = ANY(speaker_ids)",
                (self.c, NOT_RECORDED),
            )
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual(row["n"], 1)  # it was stored: only `agreed` (read now) leaves it out
        found = await self.count(kinds.CLAIMS)  # counted first: the list wipes the stopped ones
        listed = await claims.waiting(self.g, self.c, opted_in=await self.agreed(self.g))
        unplaced = await claims.unplaced_count(self.g, self.c)
        self.assertEqual(found.n, len(listed) - unplaced)
        self.assertEqual((found.n, unplaced), (2, 2))
        await claims.confirm(self.g, self.c, listed[0].id, opted_in={SAID_BY})
        self.assertEqual((await self.count(kinds.CLAIMS)).n, 1)

    async def test_readings_equal_the_websites_readings_list(self) -> None:
        claims = await self.with_claims()
        await self.entry("Bram")
        await self.entry("Heartseeker", type="item")
        await self.entry("Crown", type="item")
        theory = readings.BeliefProposal(
            "pc:Mira", "Bram", "carries", "Heartseeker", 0.75, "accusation", ("1",)
        )
        for campaign, proposal in (
            (self.c, theory),
            (self.c, replace(theory, object="Crown")),
        ):
            await claims.propose(
                self.g, campaign, [readings.as_claim(proposal, "player_said")], {"1": SAID_BY},
                opted_in={SAID_BY}, dms={SAID_BY}, prompt_version="r1", model="m1",
                session_started_at=1_000,
            )  # fmt: skip
        await self.entry("Bram", campaign=self.two.id)  # a reading in another campaign: not ours
        await self.entry("Heartseeker", type="item", campaign=self.two.id)
        await claims.propose(
            self.g, self.two.id, [readings.as_claim(theory, "player_said")], {"1": SAID_BY},
            opted_in={SAID_BY}, dms={SAID_BY}, prompt_version="r1", model="m1",
            session_started_at=1_000,
        )  # fmt: skip
        from dmbot.consent import TERMS_VERSION

        async with self.db.guild(self.g) as conn:  # the door reads who is recorded now (#3462)
            await conn.execute(
                "INSERT INTO consent (guild_id, user_id, granted_at, terms_version)"
                " VALUES (%s, %s, 0, %s) ON CONFLICT (guild_id, user_id)"
                " DO UPDATE SET terms_version = EXCLUDED.terms_version",
                (self.g, SAID_BY, TERMS_VERSION),
            )
        ref = f"{self.g}.{self.c}"
        shown = await relationships.read_readings(self.web, self.session, ref)
        self.assertEqual((await self.count(kinds.READINGS)).n, shown["counts"]["readings"])
        self.assertEqual(shown["counts"]["readings"], len(shown["readings"]))
        self.assertEqual(len(shown["readings"]), 2)
        await claims.reject(self.g, self.c, shown["readings"][0]["id"])  # "no", as the page does
        again = await relationships.read_readings(self.web, self.session, ref)
        self.assertEqual((await self.count(kinds.READINGS)).n, again["counts"]["readings"])
        self.assertEqual(again["counts"]["readings"], 1)

    async def test_connections_add_up_their_four_parts_and_leave_out_which_one(self) -> None:
        links = BackstoryLinks(self.db)
        self.src = replace(self.src, backstory=links)
        voss = await self.entry("Voss", type="npc")
        council = await self.entry("Lantern Council", type="faction")
        await links.add(self.g, self.c, [NewLink(voss, "member_of", council, "q")])  # (1) backstory
        asha, ashb = await self.entry("Asha"), await self.entry("Ashe")
        await links.add_which(
            self.g, self.c, [NewWhich(voss, True, "enemy_of", "Ash", (asha, ashb), "q")]
        )  # "Which one?": its own kind
        tavin = await self.entry("Tavin")  # (2) a name an AI read suggested for a confirmed entry
        await self.memory.add_alias(
            self.g, self.c, tavin, "Tav", kind="short", source=BACKGROUND_SOURCE, status=PROPOSED
        )
        await self.memory.add_unnamed(  # (3) an unnamed person who may be a named one
            self.g, self.c, what="guard at the north gate", role="guard"
        )
        await self.entry("Ken", description="A guard who keeps the north gate")
        aldric, bryn, mira = [await self.entry(n) for n in ("Aldric", "Bryn", "Mira")]
        for one, role, two in ((aldric, "mother", bryn), (bryn, "sister", mira)):  # (4)
            await self.memory.add_relation(
                self.g, self.c, one, "kin_of", two, source=DM, confidence=1.0,
                detail=role, directional=True, status=CONFIRMED,
            )  # fmt: skip
        await self.memory.rework_family(self.g, self.c)
        parts = (
            await links.count(self.g, self.c) - await links.count_which(self.g, self.c),
            len(await self.memory.suggested_names(self.g, self.c)),
            len(offers := await self.memory.same_person_offers(self.g, self.c)),
            len(await self.memory.worked_out_waiting(self.g, self.c)),
        )
        self.assertEqual(parts, (1, 1, 1, 1))
        self.assertEqual((await self.count(kinds.CONNECTIONS)).n, sum(parts))
        self.assertEqual((await self.count(kinds.WHICH)).n, 1)  # counted there, not here
        await self.memory.not_the_same_person(
            self.g, self.c, offers[0].unnamed_id, offers[0].named_id, user_id=7
        )
        self.assertEqual((await self.count(kinds.CONNECTIONS)).n, sum(parts) - 1)

    async def test_house_rule_updates_count_a_set_and_each_pack(self) -> None:
        sets, packs = RuleSetStore(self.db), PackUpdateStore(self.db)
        houses, brew = HouseRuleStore(self.db), HomebrewStore(self.db)
        self.src = replace(self.src, rule_sets=sets, packs=packs)
        publisher_dm = 5001
        pub = (await self.campaigns.create(self.g, "Publisher", publisher_dm)).id
        await houses.add(self.g, pub, publisher_dm, "No flanking")
        await sets.publish(
            self.g, pub, publisher_dm, "Frost rules", await houses.list(self.g, pub), None
        )
        await brew.add(self.g, pub, publisher_dm, kind="item", name="Lamp", text="Glows.")
        await packs.publish_pack(
            self.g, pub, publisher_dm, "Frost pack", await brew.list(self.g, pub)
        )
        followers = ((self.c, ALICE.id), (self.two.id, BOB.id))
        for campaign, dm in followers:
            (offer,) = await sets.shared_here(self.g, campaign)
            await sets.follow(self.g, campaign, dm, offer)
            (pack_offer,) = await packs.packs_here(self.g, campaign)
            await packs.follow_pack(self.g, campaign, dm, pack_offer)
        self.assertEqual((await self.count(kinds.RULES)).n, 0)  # nothing new yet
        await houses.add(self.g, pub, publisher_dm, "Crits double the dice")
        await sets.publish_changes(
            self.g, pub, publisher_dm, await houses.list(self.g, pub), None, plan_ok=True
        )
        await brew.add(self.g, pub, publisher_dm, kind="item", name="Torch", text="Burns.")
        await packs.publish_pack_changes(self.g, pub, publisher_dm, await brew.list(self.g, pub))
        mine, dm = self.c, ALICE.id
        shown = len(await packs.pending_pack_updates(self.g, mine, for_dmbot=True)) + bool(
            await sets.pending_update(self.g, mine, for_dmbot=True)
        )
        self.assertEqual((await self.count(kinds.RULES)).n, shown)
        self.assertEqual(shown, 2)  # the set, and the one pack
        await sets.take_update(self.g, mine, dm)  # the DM takes the set's new version
        self.assertEqual((await self.count(kinds.RULES)).n, 1)
        (follow,) = await packs.followed_packs(self.g, mine)
        await packs.take_pack_update(self.g, mine, dm, follow.set_id, expected_version=2)
        await packs.undo_last_pack_take(self.g, mine, dm, follow.set_id, 2)  # ... and sends it back
        self.assertEqual((await self.count(kinds.RULES)).n, 0)  # DMbot does not ask for it again
        self.assertEqual(len(await packs.pending_pack_updates(self.g, mine)), 1)  # DM still sees
        other = await counts.count_kinds(self.src, self.g, self.two.id, only={kinds.RULES})
        self.assertEqual(other[kinds.RULES].n, 2)  # the other campaign's own

    async def test_a_kind_that_cannot_be_counted_is_left_out_not_zero(self) -> None:
        await self.entry("Belleros")
        await self.entry("Beleros")
        await self.memory.add_entity(
            self.g, self.c, type="character", name="Hrothgar", source=DM, status=PROPOSED
        )
        with (
            patch.object(MemoryStore, "look_alikes", AsyncMock(side_effect=RuntimeError("down"))),
            self.assertLogs("dmbot.questions.counts", level="WARNING") as logged,
        ):
            found = await counts.count_kinds(self.src, self.g, self.c)
        self.assertNotIn(kinds.LOOKALIKES, found)  # not Counted(0): its old number stays
        self.assertEqual(found[kinds.NAMES].n, 1)
        self.assertEqual(set(found), set(kinds.ORDER) - {kinds.LOOKALIKES})
        (line,) = logged.output
        self.assertIn("RuntimeError", line)
        self.assertNotIn("down", line)  # the error type only
        # ... and counted again once it works
        self.assertEqual((await self.count(kinds.LOOKALIKES)).n, 1)


class TheStoredCounts(Base):
    async def test_counts_are_written_read_back_and_kept_per_campaign(self) -> None:
        store = QuestionCounts(self.db, clock=lambda: float(START))
        await store.write(
            THURSDAY.id,
            self.one.id,
            {kinds.NAMES: Counted(3, 50), kinds.HEARD: Counted(1, None, 4)},
        )
        await store.write(THURSDAY.id, self.one.id, {kinds.NAMES: Counted(2, 60)})
        got = await store.read(THURSDAY.id, self.one.id)
        self.assertEqual(got[kinds.NAMES], Counted(2, 60))
        self.assertEqual(got[kinds.HEARD], Counted(1, None, 4))  # (a kind not rewritten stays)
        self.assertEqual(await store.read(THURSDAY.id, self.two.id), {})

    async def test_the_tables_hold_numbers_and_ids_only_and_go_with_the_campaign(self) -> None:
        async with self.db.guild(THURSDAY.id) as conn:
            cur = await conn.execute(
                "SELECT column_name FROM information_schema.columns"
                " WHERE table_name IN ('question_counts', 'question_notices')"
                " AND data_type IN ('text', 'character varying')"
            )
            texty = {r["column_name"] for r in await cur.fetchall()}
        self.assertEqual(texty, {"campaign_id", "kind"})  # no free text anywhere


class FamilyTiesAreShownAndAnswerable(unittest.TestCase):
    """Family ties have their own screen since #3463, so they count in the total, answer a
    reminder when they go down and start one when a new one arrives."""

    def test_the_total_counts_them(self) -> None:
        self.assertEqual(total({kinds.NAMES: Counted(2), kinds.FAMILY: Counted(5)}), 7)

    def test_one_going_down_is_an_answer_and_one_going_up_is_new(self) -> None:
        before = {kinds.FAMILY: Counted(3)}
        self.assertTrue(answered(before, {kinds.FAMILY: Counted(1)}))
        self.assertTrue(grew(before, {kinds.FAMILY: Counted(4)}))
