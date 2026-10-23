"""The pre-session note (#1403): its wording and limits (pure), what it picks from a campaign's
confirmed facts (Postgres), who gets it and where it goes (the bot's step, with fakes), and how
fast it is. No AI anywhere."""

from __future__ import annotations

import statistics
import time
import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

from dmbot.bot import DMBot
from dmbot.config import ConfigError, load_settings
from dmbot.memory import presession
from dmbot.memory.claims import ClaimStore, Extracted
from dmbot.memory.models import DEAD, Heard
from dmbot.memory.presession import Due, Facts, Note, PreSession, compose
from dmbot.memory.recall import RecallService
from tests import speed, test_recall
from tests.test_config import BASE
from tests.test_memory_store import GUILD_A, MemoryTest
from tests.test_recall import AGREED, S1, S2

LATER = S2 + 10 * 86400


class Wording(unittest.TestCase):
    def test_nothing_to_say_is_no_note_at_all(self) -> None:
        self.assertIsNone(compose(Facts()))
        self.assertIsNone(compose(Facts(waiting=0, conditions=[("Mira", "alive")])))  # not news

    def test_the_lines_come_in_the_order_the_dm_wants_them(self) -> None:
        note = compose(
            Facts(
                "The Ferry at Dusk", 12,
                ["Mira owes Mother Ilse a favour"], [("Heartseeker", "lost")], 3,
            )
        )  # fmt: skip
        assert note is not None
        self.assertEqual(
            note.lines,
            (
                "Last recap: *The Ferry at Dusk* (story day 12)",
                "• Mira owes Mother Ilse a favour",
                "• Heartseeker is lost",
                "🗣️ 3 things people said are waiting for your check",
            ),
        )
        self.assertTrue(
            note.text.startswith(
                "📜 **Before tonight**\n*From what you confirmed. Only you see this.*\n"
            )
        )
        self.assertEqual(note.waiting, 3)

    def test_quiet_threads_are_one_line_with_up_to_three_names_and_a_count(self) -> None:
        self.assertIsNone(compose(Facts(quiet_threads=())))
        note = compose(Facts(quiet_threads=["The Missing Heir", "Mira's debt"]))
        assert note is not None
        self.assertEqual(note.lines, ("🧵 Quiet threads: The Missing Heir, Mira's debt",))
        self.assertEqual(note.quiet, 2)
        many = compose(Facts(quiet_threads=["A", "B", "C", "D", "E"]))
        assert many is not None
        self.assertEqual(many.lines, ("🧵 Quiet threads: A, B, C (+2 more)",))
        self.assertEqual(many.quiet, 5)

    def test_due_soon_promises_are_two_lines_and_a_count(self) -> None:
        self.assertIsNone(compose(Facts(due_soon=())))
        a = Due("bring the healer back", "Kael", "Dunhollow", "the full moon")
        b = Due("guard the ford", "Mira", "The Harpers")
        note = compose(Facts(due_soon=[a, b]))
        assert note is not None
        self.assertEqual(
            note.lines,
            (
                "🤝 Due soon: Kael → Dunhollow: bring the healer back (due: the full moon)",
                "🤝 Due soon: Mira → The Harpers: guard the ford",
            ),
        )
        self.assertEqual(note.quiet, 2)  # the 🧵 Threads button covers them
        more = compose(Facts(due_soon=[a, b, Due("c"), Due("d")]))
        assert more is not None
        self.assertEqual(len(more.lines), 3)
        self.assertEqual(more.lines[2], "🤝 +2 more promises due soon")
        self.assertEqual(more.quiet, 4)

    def test_the_claims_line_survives_a_full_note_and_a_stalled_promise_counts_once(self) -> None:
        full = Facts(
            "Recap",
            3,
            ["a", "b", "c"],
            [("X", "dead"), ("Y", "lost"), ("Z", "missing")],
            2,
            ["Vow"],
            [Due("Vow"), Due("Other"), Due("Third")],
        )
        note = compose(full)
        assert note is not None
        self.assertEqual(len(note.lines), presession.MAX_LINES)
        self.assertTrue(any("waiting for your check" in x for x in note.lines))
        self.assertEqual(note.waiting, 2)
        room = compose(Facts(quiet_threads=["Vow"], due_soon=[Due("Vow"), Due("Other")]))
        assert room is not None
        self.assertEqual(room.quiet, 2)  # "Vow" is on the quiet line and a due line: once

    def test_no_orphaned_more_line_when_the_due_lines_did_not_fit(self) -> None:
        big = Facts(
            open_facts=["x" * 130] * 3,
            conditions=[("N" * 100, "dead")] * 3,
            quiet_threads=["q" * 100],
            due_soon=[Due("a"), Due("b"), Due("c")],
        )
        note = compose(big)
        assert note is not None
        if not any(x.startswith(presession.DUE_PREFIX) for x in note.lines):
            self.assertFalse([x for x in note.lines if x.startswith(presession.MORE_DUE)])

    def test_a_missing_due_note_leaves_out_the_due_part(self) -> None:
        note = compose(Facts(due_soon=[Due("p", "Kael", "Dunhollow")]))
        assert note is not None
        self.assertEqual(note.lines, ("🤝 Due soon: Kael → Dunhollow: p",))

    def test_a_promise_without_both_parties_uses_the_short_form(self) -> None:
        for d in (Due("pay the toll"), Due("pay the toll", "Kael"), Due("pay the toll", "", "X")):
            note = compose(Facts(due_soon=[d]))
            assert note is not None
            self.assertEqual(note.lines, ("🤝 Due soon: pay the toll",))

    def test_a_due_line_is_clipped_and_cannot_open_markdown(self) -> None:
        note = compose(Facts(due_soon=[Due("x" * 400, "*K*", "[Y](z)", "_soon_")]))
        assert note is not None
        self.assertLessEqual(len(note.lines[0]), presession.LINE_CHARS)
        short = compose(Facts(due_soon=[Due("p", "*K*", "[Y](z)", "_soon_")]))
        assert short is not None
        self.assertIn("\\*K\\*", short.lines[0])
        self.assertIn("\\[Y\\]\\(z\\)", short.lines[0])

    def test_no_button_when_the_quiet_line_did_not_fit(self) -> None:
        facts = Facts(
            "T" * 10, 1, [f"• fact {n} " + "x" * 120 for n in range(3)], [], 5,
            quiet_threads=["Quiet one"],
        )  # fmt: skip
        note = compose(facts)
        assert note is not None
        if not any(line.startswith(presession.QUIET_PREFIX) for line in note.lines):
            self.assertEqual(note.quiet, 0)  # no line, so no 🧵 Threads button
        full = compose(Facts(quiet_threads=["Quiet one"]))
        assert full is not None
        self.assertEqual(full.quiet, 1)

    def test_a_thread_name_cannot_open_markdown_in_the_note(self) -> None:
        note = compose(Facts(quiet_threads=["*bold* [x](y)"]))
        assert note is not None
        self.assertNotIn("[x](y)", note.text)

    def test_one_thing_is_singular_and_no_day_means_no_day(self) -> None:
        note = compose(Facts("Dusk", None, [], [], 1))
        assert note is not None
        self.assertEqual(
            note.lines, ("Last recap: *Dusk*", "🗣️ 1 thing someone said is waiting for your check")
        )

    def test_it_never_runs_over_eight_lines_or_a_thousand_characters(self) -> None:
        many = Facts("T", 1, [f"Name {n} owes a favour" for n in range(20)],
                     [(f"Item {n}", "dead") for n in range(20)], 5)  # fmt: skip
        note = compose(many)
        assert note is not None
        self.assertLessEqual(len(note.lines), presession.MAX_LINES)
        self.assertLessEqual(len(note.text), presession.MAX_CHARS)
        long = Facts(None, None, ["x" * 600] * 3, [], 0)
        note = compose(long)
        assert note is not None
        self.assertTrue(all(len(line) <= presession.LINE_CHARS for line in note.lines))
        self.assertLessEqual(len(note.text), presession.MAX_CHARS)

    def test_a_name_cannot_open_or_close_the_notes_markdown(self) -> None:
        note = compose(Facts("*The* _Ferry_ ||x||", None, ["a `b` *c*"], [("~d~", "dead")], 0))
        assert note is not None
        for bad in ("*The*", "_Ferry_", "||x||", "`b`", "*c*", "~d~"):
            self.assertNotIn(bad, note.text.replace("\\" + bad[0], ""))
        self.assertIn("\\*The\\*", note.text)

    def test_only_conditions_worth_remembering_are_words(self) -> None:
        note = compose(Facts(conditions=[("A", "alive"), ("B", "intact"), ("C", "captured")]))
        assert note is not None
        self.assertEqual(note.lines, ("• C is captured",))


class Selecting(MemoryTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.mira = await self.entry("Mira", role="npc")
        self.ilse = await self.entry("Mother Ilse", role="npc")
        self.stranger = await self.entry("The Stranger", role="npc")
        self.sword = await self.entry("Heartseeker", type="item")
        self.other = await self.entry("Quiet Cerric", role="npc")
        await self.memory.add_session_heard(
            GUILD_A, self.c, S2,
            [Heard(self.mira, 7, 9), Heard(self.ilse, 7, 5), Heard(self.stranger, 7, 1)],
        )  # fmt: skip
        await self.fact(self.mira, "ally_of", self.ilse, from_session_at=S1)
        await self.fact(self.stranger, "ally_of", self.mira, from_session_at=S2, secret=True)
        self.bridge = SimpleNamespace(title="The Ferry at Dusk", game_day=12)
        self.recaps = SimpleNamespace(bridge=AsyncMock(return_value=self.bridge))

    async def entry(self, name: str, **kw: Any) -> str:
        kw.setdefault("type", "character")
        kw.setdefault("status", "confirmed")
        return await self.add(name, **kw)

    async def fact(self, a: str, pred: str, b: str, **kw: Any) -> str:
        kw.setdefault("status", "confirmed")
        written = await self.relate(a, pred, b, **kw)
        return str(written.value[0].id)

    def service(self, recaps: Any = "default") -> PreSession:
        return PreSession(
            self.memory,
            RecallService(self.memory),
            ClaimStore(self.memory),
            self.recaps if recaps == "default" else recaps,
        )

    async def note(self, **kw: Any) -> Note | None:
        kw.setdefault("campaign", self.c)
        return await self.service(kw.pop("recaps", "default")).note(
            GUILD_A, kw.pop("campaign"), now=LATER, opted_in=kw.pop("opted_in", AGREED)
        )

    async def thread_at(self, name: str, when: int, campaign: str | None = None) -> str:
        """A thread the DM started at `when` (the store's clock), live ("active")."""
        self.now = when
        made = await self.memory.new_thread(
            GUILD_A, campaign or self.c, name, "quest", "active", source="dm"
        )
        return made.value.id

    async def later_sessions(self, campaign: str | None = None) -> None:
        """Three more sessions after the one in setUp, in which no thread is heard."""
        who = (
            self.mira
            if campaign is None
            else await self.add("Z", campaign=campaign, type="character", status="confirmed")
        )
        for n in (1, 2, 3):
            await self.memory.add_session_heard(
                GUILD_A, campaign or self.c, S2 + n * 86400, [Heard(who, 7, 1)]
            )

    async def test_quiet_threads_come_longest_quiet_first_and_only_stalled_ones(self) -> None:
        await self.thread_at("Newer thread", S2 + 10)  # three sessions since
        await self.thread_at("Old thread", S1 - 100)  # four sessions since
        await self.thread_at("Fresh thread", LATER)  # none since
        await self.later_sessions()
        note = await self.service().note(
            GUILD_A, self.c, now=LATER, opted_in=AGREED, stalled_after=3
        )
        assert note is not None
        line = next(x for x in note.lines if x.startswith("🧵"))
        self.assertEqual(line, "🧵 Quiet threads: Old thread, Newer thread")
        self.assertEqual(note.quiet, 2)

    async def promise_at(
        self, name: str, when: int, state: str, campaign: str | None = None
    ) -> str:
        self.now = when
        made = await self.memory.new_thread(
            GUILD_A, campaign or self.c, name, "promise", state, source="dm"
        )
        return made.value.id

    async def test_due_soon_promises_come_quietest_first_with_their_words(self) -> None:
        kael = await self.entry("Kael")
        village = await self.entry("Dunhollow", type="place")
        await self.promise_at("Guard the ford", S2 + 10, "active")
        older = await self.promise_at("Bring the healer back", S1 - 100, "active")
        await self.promise_at("Quiet vow", S1 - 100, "open")  # not Due soon: no line
        await self.promise_at("Kept vow", S1 - 100, "resolved")
        await self.memory.promise_party(GUILD_A, self.c, older, kael, to=False, source="dm")
        await self.memory.promise_party(GUILD_A, self.c, older, village, to=True, source="dm")
        await self.memory.set_due(GUILD_A, self.c, older, "the full moon", source="dm")
        await self.later_sessions()
        note = await self.service().note(
            GUILD_A, self.c, now=LATER, opted_in=AGREED, stalled_after=3
        )
        assert note is not None
        due = [x for x in note.lines if x.startswith("🤝")]
        self.assertEqual(
            due,
            [
                "🤝 Due soon: Kael → Dunhollow: Bring the healer back (due: the full moon)",
                "🤝 Due soon: Guard the ford",
            ],
        )

    async def test_no_due_soon_promise_means_no_line(self) -> None:
        await self.promise_at("Open vow", LATER, "open")
        await self.later_sessions()
        note = await self.note()
        assert note is not None
        self.assertFalse([x for x in note.lines if x.startswith("🤝")])

    async def test_another_campaigns_promises_never_appear(self) -> None:
        other = (await self.campaigns.create(GUILD_A, "Elsewhere", 7)).id
        await self.promise_at("Theirs", S2 + 10, "active", campaign=other)
        await self.later_sessions()
        note = await self.note()
        assert note is not None
        self.assertNotIn("Theirs", note.text)

    async def test_no_quiet_thread_means_no_line(self) -> None:
        await self.thread_at("Fine thread", LATER)
        await self.later_sessions()
        note = await self.note()
        assert note is not None
        self.assertFalse([x for x in note.lines if x.startswith("🧵")])
        self.assertEqual(note.quiet, 0)

    async def test_a_resolved_thread_is_never_quiet(self) -> None:
        done = await self.thread_at("Done thread", S1 - 100)
        await self.memory.set_condition(GUILD_A, self.c, done, "resolved", source="dm")
        self.now = LATER
        await self.later_sessions()
        note = await self.note()
        assert note is not None
        self.assertEqual(note.quiet, 0)

    async def test_another_campaigns_threads_never_appear(self) -> None:
        other = (await self.campaigns.create(GUILD_A, "Elsewhere", 7)).id
        await self.thread_at("Theirs", S1 - 100, other)
        await self.later_sessions(other)
        self.now = LATER
        note = await self.note()
        assert note is not None
        self.assertNotIn("Theirs", note.text)
        self.assertEqual(note.quiet, 0)

    async def test_a_failed_thread_read_costs_the_line_never_the_note(self) -> None:
        self.memory.threads = AsyncMock(side_effect=RuntimeError("x"))  # type: ignore[method-assign]
        note = await self.note()
        assert note is not None  # the rest of the note stands
        self.assertEqual(note.quiet, 0)

    async def test_it_holds_the_last_recap_and_what_is_open_about_the_names_heard_last(
        self,
    ) -> None:
        got = await self.note()
        assert got is not None
        self.assertEqual(got.lines[0], "Last recap: *The Ferry at Dusk* (story day 12)")
        text = got.text
        self.assertIn("Mira", text)
        self.assertIn("Mother Ilse", text)
        self.assertNotIn("Quiet Cerric", text)  # not heard last session

    async def test_secret_facts_are_included_because_this_is_the_dm_screen(self) -> None:
        got = await self.note()
        assert got is not None
        self.assertIn("The Stranger", got.text)

    async def test_conditions_that_matter_are_listed_newest_first(self) -> None:
        await self.memory.set_condition(GUILD_A, self.c, self.sword, "lost", source="dm")
        self.now += 5
        await self.memory.set_condition(GUILD_A, self.c, self.ilse, DEAD, source="dm")
        got = await self.note()
        assert got is not None
        lines = [line for line in got.lines if line.endswith(("is dead", "is lost"))]
        self.assertEqual(lines, ["• Mother Ilse is dead", "• Heartseeker is lost"])

    async def test_a_closed_condition_is_not_news(self) -> None:
        await self.memory.set_condition(GUILD_A, self.c, self.ilse, DEAD, source="dm")
        self.now += 5
        await self.memory.set_condition(GUILD_A, self.c, self.ilse, "alive", source="dm")
        got = await self.note()
        assert got is not None
        self.assertFalse([line for line in got.lines if "dead" in line])

    async def test_questions_waiting_to_be_checked_are_counted(self) -> None:
        claims = ClaimStore(self.memory)
        await claims.propose(
            GUILD_A, self.c, [Extracted("Mira", "carries", "Heartseeker", "", ("C1",), "dm_said")],
            {"C1": 7}, opted_in=AGREED, prompt_version="p", model="m", session_started_at=S2,
        )  # fmt: skip
        got = await self.note()
        assert got is not None
        self.assertEqual(got.waiting, 1)
        self.assertIn("1 thing someone said is waiting for your check", got.text)

    async def test_a_name_that_is_only_a_proposal_is_not_in_the_note(self) -> None:
        guess = await self.entry("Stray Wolf", status="proposed")
        await self.memory.add_session_heard(GUILD_A, self.c, S2, [Heard(guess, 7, 99)])
        got = await self.note()
        assert got is not None
        self.assertNotIn("Stray Wolf", got.text)

    async def test_only_the_latest_session_counts_as_last_time(self) -> None:
        await self.memory.add_session_heard(
            GUILD_A, self.c, S1, [Heard(self.other, 7, 50)]
        )  # an older session, heard a lot
        await self.fact(self.other, "ally_of", self.sword, from_session_at=S1)
        got = await self.note()
        assert got is not None
        self.assertNotIn("Quiet Cerric", got.text)

    async def test_nothing_confirmed_gives_no_note_never_an_empty_one(self) -> None:
        empty = (await self.campaigns.create(GUILD_A, "Empty", 77)).id
        self.assertIsNone(await self.note(campaign=empty, recaps=None))

    async def test_another_campaigns_facts_never_appear(self) -> None:
        other = (await self.campaigns.create(GUILD_A, "Other", 77)).id
        again = await self.add("Mira", campaign=other, type="character", status="confirmed")
        stranger = await self.add("Zed", campaign=other, type="character", status="confirmed")
        await self.memory.add_session_heard(
            GUILD_A, other, S2, [Heard(again, 7, 5), Heard(stranger, 7, 5)]
        )
        await self.memory.add_relation(
            GUILD_A,
            other,
            again,
            "ally_of",
            stranger,
            source="dm",
            confidence=1.0,
            status="confirmed",
        )
        mine = await self.note()
        theirs = await self.note(campaign=other, recaps=None)
        assert mine is not None and theirs is not None
        self.assertNotIn("Zed", mine.text)
        self.assertNotIn("The Stranger", theirs.text)

    async def test_no_recap_yet_means_no_last_time_line(self) -> None:
        got = await self.note(recaps=SimpleNamespace(bridge=AsyncMock(return_value=None)))
        assert got is not None
        self.assertFalse([line for line in got.lines if line.startswith("Last recap")])

    async def test_it_asks_for_the_latest_posted_recap_before_now(self) -> None:
        await self.note()
        self.recaps.bridge.assert_awaited_once_with(GUILD_A, self.c, LATER + 1)

    async def test_a_claim_citing_someone_not_recorded_now_is_not_counted_and_not_wiped(
        self,
    ) -> None:
        claims = ClaimStore(self.memory)
        await claims.propose(
            GUILD_A, self.c, [Extracted("Mira", "carries", "Heartseeker", "", ("C1",), "dm_said")],
            {"C1": 7}, opted_in=AGREED, prompt_version="p", model="m", session_started_at=S2,
        )  # fmt: skip
        stopped = await self.note(opted_in={8})  # the speaker of C1 (7) is not recorded now
        assert stopped is not None
        self.assertEqual(stopped.waiting, 0)
        self.assertNotIn("waiting for your check", stopped.text)
        # And nothing was wiped: when they agree again the question is still there.
        again = await self.note(opted_in={7})
        assert again is not None
        self.assertEqual(again.waiting, 1)

    async def test_when_the_latest_session_heard_nothing_confirmed_the_one_before_is_used(
        self,
    ) -> None:
        guess = await self.entry("Stray Wolf", status="proposed")
        await self.memory.add_session_heard(GUILD_A, self.c, LATER, [Heard(guess, 7, 50)])
        got = await self.note()
        assert got is not None
        self.assertIn("Mira", got.text)

    async def test_a_condition_from_before_last_session_is_not_news(self) -> None:
        self.now = S1  # set long ago, before the last session (S2) began
        await self.memory.set_condition(GUILD_A, self.c, self.sword, "lost", source="dm")
        got = await self.note()
        assert got is not None
        self.assertNotIn("Heartseeker is lost", got.text)

    async def test_it_reads_no_transcript_line(self) -> None:
        # The recall is given no one as recorded, so it quotes nothing and reads no line.
        service = RecallService(self.memory, lines=AsyncMock(side_effect=AssertionError("read")))
        got = await PreSession(self.memory, service, ClaimStore(self.memory), self.recaps).note(
            GUILD_A, self.c, now=LATER, opted_in=AGREED
        )
        assert got is not None


# (The module, not the class, is imported: a TestCase imported by name is collected here too,
# and the recall's speed test would run twice in every job, #1897.)
class FastEnough(test_recall.Speed):
    """The note on the recall test's big campaign (3,000 entries, 10,000 facts): under 100 ms
    on the best of seven in CI (the goal is 50 ms, measured on the server)."""

    async def test_a_recall_on_a_big_campaign_is_fast(self) -> None:  # not this class's job
        self.skipTest("the recall's own speed test runs in test_recall")

    async def test_the_note_on_a_big_campaign_is_fast(self) -> None:
        await test_recall.Speed.build(self)
        async with self.db.guild(GUILD_A) as conn:  # a last session to start from
            await conn.execute(
                "INSERT INTO memory_heard (guild_id, campaign_id, entity_id, session_started_at,"
                " speaker_id, times) SELECT %s, %s, md5('e' || n), %s, 7, 1 + n %% 9"
                " FROM generate_series(1, 300) AS n",
                (GUILD_A, self.c, S2),
            )
            await conn.execute("ANALYZE memory_heard")

        def fresh() -> PreSession:  # a new service each time: the ontology is not cached
            return PreSession(
                self.memory, RecallService(self.memory), ClaimStore(self.memory), None
            )

        first = await fresh().note(GUILD_A, self.c, now=LATER, opted_in=AGREED)
        assert first is not None  # it really found something to say
        self.assertGreater(len(first.lines), 0)
        self.assertLessEqual(len(first.text), presession.MAX_CHARS)
        busy = speed.slowdown()  # how slow this machine is right now (#2490)
        times = []
        for _ in range(7):
            started = time.perf_counter()
            await fresh().note(GUILD_A, self.c, now=LATER, opted_in=AGREED)
            times.append((time.perf_counter() - started) * 1000)
        ceiling = speed.allowed(100, busy)
        self.assertLess(
            min(times),
            ceiling,
            f"best {min(times):.1f} ms, median {statistics.median(times):.1f} ms,"
            f" ceiling {ceiling:.0f} ms",
        )


def fake_bot(
    *,
    on: bool = True,
    allowed: bool = True,
    resumed: bool = False,
    note: Note | None = None,
    guide: bool = False,
) -> Any:
    table = SimpleNamespace(
        guild_id=GUILD_A, campaign_id="c" * 32, resumed=resumed, screen_channel_id=55
    )
    bot = SimpleNamespace(
        settings=SimpleNamespace(presession_note=on, thread_stalled_sessions=3),
        memory=object(),
        recaps=None,
        tables={GUILD_A: table},
        campaigns=SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(id="c" * 32))),
        plan_allows=AsyncMock(return_value=allowed),
        consent=SimpleNamespace(consenting=AsyncMock(return_value={7})),
        post=AsyncMock(return_value=True),
        guide_button_allowed=AsyncMock(return_value=guide),  # the 🧭 What's next? button (#1731)
    )
    return bot, table


class OnTheDmScreen(unittest.IsolatedAsyncioTestCase):
    async def run_step(self, bot: Any, table: Any, note: Note | None, monkey: Any) -> None:
        async def fake_note(self: Any, *a: Any, **kw: Any) -> Note | None:
            return note

        monkey.setattr(PreSession, "note", fake_note)
        await DMBot._pre_session_note(bot, table)

    def patched(self) -> Any:
        from unittest import mock

        class Patch:
            def __init__(self) -> None:
                self.patches: list[Any] = []

            def setattr(self, obj: Any, name: str, value: Any) -> None:
                patch = mock.patch.object(obj, name, value)
                patch.start()
                self.patches.append(patch)

            def undo(self) -> None:
                for p in self.patches:
                    p.stop()

        return Patch()

    async def go(self, note: Note | None, **kw: Any) -> Any:
        bot, table = fake_bot(**kw)
        patch = self.patched()
        try:
            await self.run_step(bot, table, note, patch)
        finally:
            patch.undo()
        return bot

    NOTE = Note("📜 **Before tonight**\nLast recap: *X*", ("Last recap: *X*",), 0)

    async def test_it_goes_to_the_dm_screen_and_only_there(self) -> None:
        bot = await self.go(self.NOTE)
        bot.post.assert_awaited_once()
        self.assertEqual(bot.post.await_args.args[0], 55)  # the DM screen channel
        self.assertEqual(bot.post.await_args.args[1], self.NOTE.text)
        self.assertIsNone(bot.post.await_args.kwargs["view"])  # nothing waiting: no button

    async def test_quiet_threads_bring_the_threads_button_beside_the_claims_one(self) -> None:
        bot = await self.go(Note("t", ("l",), 0, 2))
        ids = [str(i.item.custom_id) for i in bot.post.await_args.kwargs["view"].children]
        self.assertEqual(ids, ["dmbot:threads:list:" + "c" * 32])
        both = await self.go(Note("t", ("l",), 2, 2))
        ids = [str(i.item.custom_id) for i in both.post.await_args.kwargs["view"].children]
        self.assertEqual(len(ids), 2)
        self.assertIn("dmbot:threads:list:" + "c" * 32, ids)

    async def test_guide_me_brings_the_whats_next_button_only_when_it_is_allowed(self) -> None:
        with_it = await self.go(self.NOTE, guide=True)
        ids = [str(i.item.custom_id) for i in with_it.post.await_args.kwargs["view"].children]
        self.assertEqual(len(ids), 1)
        self.assertIn("guide", ids[0])
        without = await self.go(self.NOTE, guide=False)
        self.assertIsNone(without.post.await_args.kwargs["view"])

    async def test_waiting_questions_bring_the_existing_button(self) -> None:
        bot = await self.go(Note("t", ("l",), 2))
        self.assertIsNotNone(bot.post.await_args.kwargs["view"])

    async def test_with_the_switch_off_nothing_is_posted(self) -> None:
        bot = await self.go(self.NOTE, on=False)
        bot.post.assert_not_awaited()

    async def test_a_plan_without_story_memory_or_one_that_cannot_be_checked_gets_none(
        self,
    ) -> None:
        bot = await self.go(self.NOTE, allowed=False)
        bot.post.assert_not_awaited()
        bot.plan_allows.assert_awaited_once()
        self.assertEqual(bot.plan_allows.await_args.args[0], "story")
        self.assertIs(bot.plan_allows.await_args.kwargs["fail_open"], False)

    async def test_a_session_picked_up_after_a_restart_gets_no_second_note(self) -> None:
        bot = await self.go(self.NOTE, resumed=True)
        bot.post.assert_not_awaited()

    async def test_nothing_confirmed_posts_nothing(self) -> None:
        bot = await self.go(None)
        bot.post.assert_not_awaited()

    async def test_a_session_that_ended_meanwhile_gets_no_note(self) -> None:
        bot, table = fake_bot()
        bot.tables.clear()
        patch = self.patched()
        try:
            await self.run_step(bot, table, self.NOTE, patch)
        finally:
            patch.undo()
        bot.post.assert_not_awaited()

    async def test_a_failure_costs_the_note_and_never_the_session(self) -> None:
        bot, table = fake_bot()
        bot.campaigns.get = AsyncMock(side_effect=RuntimeError("down"))
        await DMBot._pre_session_note(bot, table)  # does not raise
        bot.post.assert_not_awaited()


class Switch(unittest.TestCase):
    def test_it_is_on_by_default_and_read_from_the_environment(self) -> None:
        self.assertTrue(load_settings(BASE).presession_note)
        self.assertFalse(load_settings({**BASE, "DMBOT_PRESESSION_NOTE": "0"}).presession_note)
        with self.assertRaisesRegex(ConfigError, "DMBOT_PRESESSION_NOTE must be 1"):
            load_settings({**BASE, "DMBOT_PRESESSION_NOTE": "sometimes"})


if __name__ == "__main__":
    unittest.main()
