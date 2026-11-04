"""❓ Questions for you (#3435): the count of each kind of question that waits for the DM, the
table it is written to, and what counts as an answer. Each kind is held equal to what that kind's
own list shows. Invented names and numbers throughout."""

from __future__ import annotations

import unittest
from pathlib import Path

from dmbot.memory.models import DM, PROPOSED
from dmbot.memory.store import MemoryStore
from dmbot.questions import counts, kinds
from dmbot.questions.counts import Counted, Sources
from dmbot.questions.store import QuestionCounts, answered, grew, total
from dmbot.recaps.approval import text_hash
from dmbot.transcript.models import Line
from dmbot.transcript.store import TranscriptStore
from dmbot.transcript.waiting_questions import Unanswered, WaitingQuestionStore
from dmbot.web import plot as web_plot
from tests.test_recap_web import NOW, Base
from tests.test_web_api import THURSDAY

START = 1_700_000_000
MS = START * 1000


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

    async def test_new_names_equal_the_proposed_names_list(self) -> None:
        self.assertEqual((await self.count(kinds.NAMES)).n, 0)
        for name in ("Hrothgar", "Maren"):
            await self.memory.add_entity(
                self.g, self.c, type="character", name=name, source=DM, status=PROPOSED
            )
        await self.memory.add_entity(  # another campaign's name is not ours
            self.g, self.two.id, type="character", name="Elsewhere", source=DM, status=PROPOSED
        )
        listed = await self.memory.entities(self.g, self.c, statuses=[PROPOSED])
        self.assertEqual((await self.count(kinds.NAMES)).n, len(listed))
        self.assertEqual(len(listed), 2)

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
