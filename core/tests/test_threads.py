"""Story threads the DM keeps (#1662): the kinds and states, the DM-only writes, a same-value state
shown as "checked, still going" and never as a change, stalled worked out from the sessions heard,
recall's audience rules for a thread and its links, isolation, backups, and the screens."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any
from unittest import mock
from unittest.mock import AsyncMock, MagicMock

from dmbot.campaigns.store import decode_backup, encode_backup
from dmbot.memory import recall
from dmbot.memory import threads as th
from dmbot.memory.lookup import CampaignLookup
from dmbot.memory.models import (
    ABANDONED,
    GOING,
    HINTED,
    OPEN,
    RESOLVED,
    Heard,
    MemoryRuleError,
    State,
)
from dmbot.memory.recall import DM_SCREEN, PLAYERS, RecallService
from dmbot.memory.search import find
from dmbot.ui import threads as ui
from tests import speed
from tests.test_memory_store import DM, GUILD_A, GUILD_B, ConfirmedMemoryTest

# Session starts are real times like the clock's, later than anything the test writes by hand.
S1, S2, S3, S4 = (1_700_000_000 + n * 100_000 for n in (1, 2, 3, 4))


def row(condition: str, session: int | None, n: int = 0) -> State:
    return State(f"{n:032x}", "e", condition, session, "dm", n, None)


class Pure(unittest.TestCase):
    def test_stalled_only_for_a_thread_that_can_still_go_and_only_after_enough_sessions(
        self,
    ) -> None:
        for state in (HINTED, OPEN, GOING):
            self.assertTrue(th.is_stalled(state, 3, 3), state)
            self.assertFalse(th.is_stalled(state, 2, 3), state)
        for state in (RESOLVED, ABANDONED):
            self.assertFalse(th.is_stalled(state, 99, 3), state)
        self.assertFalse(th.is_stalled(OPEN, 99, 0))  # 0 turns it off

    def test_sessions_since_counts_only_the_sessions_that_started_after(self) -> None:
        self.assertEqual(th.sessions_since(S2, [S1, S2, S3, S4]), 2)
        self.assertEqual(th.sessions_since(S4, [S1, S2, S3, S4]), 0)

    def test_the_newest_of_everything_that_touches_a_thread_counts(self) -> None:
        got = th.last_touch(100, [row(OPEN, S1), row(GOING, None, 5)], [S2, None], [150], [S3])
        self.assertEqual(got, S3)
        self.assertEqual(th.last_touch(100, [], [], [], []), 100)

    def test_a_same_value_row_is_checked_never_a_change(self) -> None:
        history = [row(OPEN, S1, 1), row(GOING, S2, 2), row(GOING, S3, 3), row(GOING, S4, 4)]
        self.assertEqual(th.checked(history), [False, False, True, True])
        numbers = {S1: 1, S2: 2, S3: 3, S4: 4}
        self.assertEqual(
            th.history_lines(history, numbers),
            [
                "Not started (Session 1)",
                "Happening now (Session 2)",
                "checked, still going (Session 3)",
                "checked, still going (Session 4)",
            ],
        )

    def test_the_after_session_line_reads_plainly(self) -> None:
        self.assertEqual(th.stalled_line(1, 1), "1 thread hasn't moved for 1 or more sessions.")
        self.assertEqual(th.stalled_line(4, 3), "4 threads haven't moved for 3 or more sessions.")


class Threads(ConfirmedMemoryTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        for name in ("set_condition", "check_thread", "involve", "link_event", "new_thread"):
            self._tick_before(name)
        self.mira = await self.add("Mira", type="character", role="npc")
        self.ford = await self.add("The ambush at the ford", type="event")
        self.heir = (await self.start("The Missing Heir", "quest", OPEN)).id

    def _tick_before(self, name: str) -> None:
        """Rows saved in the same second tie on time; a real DM's taps are seconds apart."""
        real = getattr(self.memory, name)

        async def ticked(*a: Any, **kw: Any) -> Any:
            self.now += 5
            return await real(*a, **kw)

        setattr(self.memory, name, ticked)

    async def start(self, name: str, kind: str, state: str, **kw: Any) -> Any:
        return (
            await self.memory.new_thread(GUILD_A, self.c, name, kind, state, source="dm", **kw)
        ).value

    async def info(self, after: int = 3) -> th.ThreadInfo:
        found = await self.memory.threads(GUILD_A, self.c, stalled_after=after)
        (one,) = [t for t in found if t.thread.id == self.heir]
        return one

    async def heard(self, session: int, entity: str) -> None:
        await self.memory.add_session_heard(GUILD_A, self.c, session, [Heard(entity, 7, 1)])

    # ---- kinds, states, who may ----

    async def test_a_thread_is_a_quest_a_mystery_a_hook_or_a_goal(self) -> None:
        onto = await self.memory.ontology(GUILD_A, self.c)
        for kind in ("quest", "mystery", "hook", "goal"):
            self.assertTrue(onto.is_a(kind, "thread"), kind)
        made = await self.start("Who killed Ulf?", "mystery", HINTED)
        self.assertEqual((made.type, made.status), ("mystery", "confirmed"))
        for bad in ("concept", "character", "bogus"):
            with self.assertRaises(MemoryRuleError):
                await self.start("Nope", bad, OPEN)

    async def test_only_the_dm_writes_and_a_thread_needs_a_real_state(self) -> None:
        with self.assertRaises(MemoryRuleError):
            await self.memory.new_thread(GUILD_A, self.c, "X", "quest", OPEN, source="extractor")
        with self.assertRaises(MemoryRuleError):
            await self.start("Y", "quest", "alive")
        for call in (
            self.memory.check_thread(GUILD_A, self.c, self.heir, source="extractor"),
            self.memory.involve(GUILD_A, self.c, self.heir, self.mira, "x", source="extractor"),
            self.memory.link_event(
                GUILD_A, self.c, self.heir, self.ford, ends_it=False, source="extractor"
            ),
        ):
            with self.assertRaises(MemoryRuleError):
                await call

    async def test_a_state_change_keeps_the_history_and_the_way_back_is_the_dms(self) -> None:
        await self.memory.set_condition(
            GUILD_A, self.c, self.heir, GOING, source="dm", from_session_at=S1
        )
        await self.memory.set_condition(
            GUILD_A, self.c, self.heir, RESOLVED, source="dm", from_session_at=S2
        )
        got = await self.info()
        self.assertEqual([s.condition for s in got.history], [OPEN, GOING, RESOLVED])
        self.assertEqual(sum(s.closed_at is None for s in got.history), 1)
        with self.assertRaises(MemoryRuleError):  # a proposal can't reopen what the DM resolved
            await self.memory.set_condition(GUILD_A, self.c, self.heir, OPEN, source="extractor")
        await self.memory.set_condition(GUILD_A, self.c, self.heir, OPEN, source="dm")
        self.assertEqual((await self.info()).state.condition, OPEN)  # type: ignore[union-attr]

    async def test_who_is_involved_and_what_moved_it(self) -> None:
        await self.memory.involve(GUILD_A, self.c, self.heir, self.mira, "quest giver", source="dm")
        await self.memory.link_event(
            GUILD_A, self.c, self.heir, self.ford, ends_it=False, source="dm"
        )
        got = await self.info()
        self.assertEqual([(i.who.name, i.role) for i in got.involved], [("Mira", "quest giver")])
        self.assertEqual(
            [(m.event.name, m.ends_it) for m in got.movers], [("The ambush at the ford", False)]
        )
        end = await self.memory.new_thread(GUILD_A, self.c, "Finale", "quest", OPEN, source="dm")
        await self.memory.link_event(
            GUILD_A, self.c, end.value.id, self.ford, ends_it=True, source="dm"
        )
        (finale,) = [
            t for t in await self.memory.threads(GUILD_A, self.c) if t.thread.name == "Finale"
        ]
        self.assertTrue(finale.movers[0].ends_it)
        assert finale.state is not None
        self.assertEqual(finale.state.condition, OPEN)  # linking never changes the state

    # ---- checked, still going ----

    async def test_still_going_writes_the_same_state_again_and_is_shown_as_checked(self) -> None:
        await self.memory.set_condition(
            GUILD_A, self.c, self.heir, GOING, source="dm", from_session_at=S1
        )
        await self.memory.check_thread(GUILD_A, self.c, self.heir, source="dm", from_session_at=S3)
        got = await self.info()
        self.assertEqual([s.condition for s in got.history], [OPEN, GOING, GOING])
        self.assertEqual(th.checked(got.history), [False, False, True])
        self.assertEqual(
            th.history_lines(got.history, {S1: 1, S3: 3})[-1], "checked, still going (Session 3)"
        )
        self.assertEqual(sum(s.closed_at is None for s in got.history), 1)
        lines = ui.thread_lines(got, {S1: 1, S3: 3})
        self.assertIn("checked, still going (Session 3)", " ".join(lines))
        self.assertNotIn("Happening now (Session 3)", " ".join(lines))  # never a change of state

    async def test_only_a_thread_that_can_go_on_can_be_checked(self) -> None:
        await self.memory.set_condition(GUILD_A, self.c, self.heir, RESOLVED, source="dm")
        with self.assertRaises(MemoryRuleError):
            await self.memory.check_thread(GUILD_A, self.c, self.heir, source="dm")
        with self.assertRaises(MemoryRuleError):
            await self.memory.check_thread(GUILD_A, self.c, self.mira, source="dm")

    # ---- stalled ----

    async def test_stalled_is_worked_out_from_the_sessions_heard_since_it_last_moved(self) -> None:
        await self.memory.set_condition(
            GUILD_A, self.c, self.heir, GOING, source="dm", from_session_at=S1
        )
        for s in (S2, S3, S4):  # three sessions went by with other things heard
            await self.heard(s, self.mira)
        got = await self.info(3)
        self.assertEqual((got.quiet_sessions, got.stalled), (3, True))
        self.assertFalse((await self.info(4)).stalled)  # the setting
        # Checked in Session 4's time: touched again, no longer stalled.
        await self.memory.check_thread(GUILD_A, self.c, self.heir, source="dm", from_session_at=S4)
        after = await self.info(3)
        self.assertEqual((after.quiet_sessions, after.stalled), (0, False))

    async def test_a_session_where_nobody_was_heard_does_not_count(self) -> None:
        await self.memory.set_condition(
            GUILD_A, self.c, self.heir, GOING, source="dm", from_session_at=S1
        )
        await self.heard(S2, self.mira)
        self.assertEqual((await self.info(1)).quiet_sessions, 1)  # only S2 is known

    async def test_hearing_its_name_in_a_session_touches_it(self) -> None:
        await self.memory.set_condition(
            GUILD_A, self.c, self.heir, GOING, source="dm", from_session_at=S1
        )
        await self.heard(S2, self.mira)
        await self.heard(S3, self.heir)  # the table talked about it
        await self.heard(S4, self.mira)
        self.assertEqual((await self.info(3)).quiet_sessions, 1)

    async def test_a_resolved_or_dropped_thread_never_stalls_and_stalled_ones_come_first(
        self,
    ) -> None:
        done = await self.start("Done", "goal", RESOLVED)
        await self.memory.set_condition(
            GUILD_A, self.c, self.heir, GOING, source="dm", from_session_at=S1
        )
        for s in (S2, S3, S4):
            await self.heard(s, self.mira)
        found = await self.memory.threads(GUILD_A, self.c, stalled_after=3)
        self.assertEqual(
            [(t.thread.name, t.stalled) for t in found],
            [("The Missing Heir", True), ("Done", False)],
        )
        self.assertTrue(done.id)

    # ---- who sees it ----

    async def test_threads_are_the_dms_plot_notes_and_never_reach_players(self) -> None:
        await self.memory.involve(GUILD_A, self.c, self.heir, self.mira, "quest giver", source="dm")
        service = RecallService(self.memory)
        dm = await service.recall(GUILD_A, self.c, seeds=["Mira"], audience=DM_SCREEN, opted_in={7})
        self.assertIn("The Missing Heir", " ".join(i.text for i in dm.items))
        for seeds in (["Mira"], ["The Missing Heir"], [self.heir]):
            got = await service.recall(GUILD_A, self.c, seeds=seeds, audience=PLAYERS, opted_in={7})
            self.assertNotIn("Missing Heir", " ".join(i.text for i in got.items), seeds)

    async def test_a_manager_who_is_not_a_dm_finds_no_thread(self) -> None:
        names = await self.memory.lookup_data(GUILD_A, self.c)
        whole = CampaignLookup.build(names)
        self.assertIn(self.heir, whole.entities)
        self.assertNotIn(self.heir, whole.for_viewer(False).entities)
        self.assertEqual(
            [m for m in find(whole.for_viewer(False), "Missing Heir", secrets=False)], []
        )

    async def test_a_link_from_the_story_plan_never_reaches_players(self) -> None:
        await self.memory.involve(GUILD_A, self.c, self.heir, self.mira, "villain", source="dm")
        with mock.patch.object(recall, "PLAN_SOURCES", frozenset({"dm"})):
            players = await RecallService(self.memory).recall(
                GUILD_A, self.c, seeds=["Mira"], audience=PLAYERS, opted_in={7}
            )
        self.assertEqual([i for i in players.items if i.kind == recall.FACT], [])

    async def test_a_thread_is_the_clusters_member_walk_costs_no_extra_step(self) -> None:
        self.assertIn("thread", recall.CLUSTER_TYPES)

    # ---- isolation and backups ----

    async def test_another_campaign_or_server_sees_none(self) -> None:
        other = (await self.campaigns.create(GUILD_A, "Elsewhere", 7)).id
        self.assertEqual(await self.memory.threads(GUILD_A, other), [])
        with self.assertRaises(MemoryRuleError):
            await self.memory.threads(GUILD_B, self.c)

    async def test_threads_their_states_and_links_come_back_from_a_backup(self) -> None:
        await self.memory.set_condition(
            GUILD_A, self.c, self.heir, GOING, source="dm", from_session_at=S1
        )
        await self.memory.check_thread(GUILD_A, self.c, self.heir, source="dm", from_session_at=S2)
        await self.memory.involve(GUILD_A, self.c, self.heir, self.mira, "quest giver", source="dm")
        raw = encode_backup(await self.campaigns.export(GUILD_A, self.c))
        restored = await self.campaigns.import_backup(GUILD_B, decode_backup(raw), DM)
        (back,) = await self.memory.threads(GUILD_B, restored.id)
        self.assertEqual(back.thread.type, "quest")
        self.assertEqual([s.condition for s in back.history], [OPEN, GOING, GOING])
        self.assertEqual([(i.who.name, i.role) for i in back.involved], [("Mira", "quest giver")])


class Screens(ConfirmedMemoryTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.mira = await self.add("Mira", type="character", role="npc")
        self.heir = (
            await self.memory.new_thread(
                GUILD_A, self.c, "The Missing Heir", "quest", OPEN, source="dm"
            )
        ).value.id

    def interaction(self, user: int = DM) -> Any:
        sent: list[str] = []

        async def reply(text: str = "", **kw: Any) -> None:
            sent.append(str(kw.get("content", text)))

        it = MagicMock()
        it.user = SimpleNamespace(id=user)
        it.guild = SimpleNamespace(id=GUILD_A)
        it.client = SimpleNamespace(
            memory=self.memory,
            campaigns=self.campaigns,
            lookup=None,
            settings=SimpleNamespace(thread_stalled_sessions=3),
            transcripts=None,
        )
        it.response.is_done = lambda: False
        it.response.defer = AsyncMock()
        it.response.send_message = reply
        it.response.edit_message = reply
        it.followup.send = reply
        it.edit_original_response = reply
        it.sent = sent
        return it

    async def test_the_thread_lines_say_state_involved_and_moved(self) -> None:
        await self.memory.involve(GUILD_A, self.c, self.heir, self.mira, "quest giver", source="dm")
        (info,) = await self.memory.threads(GUILD_A, self.c)
        lines = ui.thread_lines(info, {})
        self.assertEqual(lines[0], "🧵 **Not started**")
        self.assertIn("**Involved:** Mira (quest giver)", lines)

    async def test_new_thread_and_the_card_buttons_are_for_the_dm_only(self) -> None:
        player = self.interaction(99)
        view = ui.NewThread(self.c, "Hidden")
        view.kind, view.state = "mystery", "hinted"
        await view._create(player)
        self.assertEqual(len(await self.memory.threads(GUILD_A, self.c)), 1)
        got = await ui._dm_and_memory(self.interaction(99), self.c)
        self.assertIsNone(got)

    async def test_the_quiet_threads_list_answers_each_one(self) -> None:
        await self.memory.set_condition(
            GUILD_A, self.c, self.heir, GOING, source="dm", from_session_at=S1
        )
        for s in (S2, S3, S4):
            await self.memory.add_session_heard(GUILD_A, self.c, s, [Heard(self.mira, 7, 1)])
        review = ui.ThreadsReview(self.c)
        text = await review.current_text(self.interaction())
        assert text is not None
        self.assertIn("**The Missing Heir**: quiet for 3 sessions.", text)
        self.assertEqual(
            [getattr(b, "label", "") for b in review.children],
            ["1 · Still going", "1 · Finished", "1 · Dropped"],
        )
        self.now = S4 + 10  # the DM answers after Session 4
        it = self.interaction()
        await review._answer(self.heir, "still", it)
        (info,) = await self.memory.threads(GUILD_A, self.c, stalled_after=3)
        self.assertFalse(info.stalled)  # checked: it counts as touched
        self.assertEqual(th.checked(info.history)[-1], True)
        self.assertEqual(it.sent[-1], ui.NOTHING_LEFT)

    async def test_resolved_and_dropped_answers_set_the_state(self) -> None:
        for key, state in (("resolved", RESOLVED), ("dropped", ABANDONED)):
            made = (
                await self.memory.new_thread(GUILD_A, self.c, f"T {key}", "goal", OPEN, source="dm")
            ).value.id
            review = ui.ThreadsReview(self.c)
            await review._answer(made, key, self.interaction())
            found = {t.thread.id: t for t in await self.memory.threads(GUILD_A, self.c)}
            self.assertEqual(found[made].state.condition, state)  # type: ignore[union-attr]


class ThreadList(ConfirmedMemoryTest):
    """The 🧵 Threads button on the pre-session note (#1675): every live thread, in play first."""

    def interaction(self, user: int = DM) -> Any:
        return Screens.interaction(self, user)  # type: ignore[arg-type]

    async def test_the_list_puts_threads_in_play_first_and_marks_the_quiet_ones(self) -> None:
        self.now = S1 - 100
        for name, state in (("Hinted one", HINTED), ("Open one", OPEN), ("Active one", GOING)):
            await self.memory.new_thread(GUILD_A, self.c, name, "quest", state, source="dm")
        done = await self.memory.new_thread(GUILD_A, self.c, "Done one", "quest", OPEN, source="dm")
        await self.memory.set_condition(GUILD_A, self.c, done.value.id, RESOLVED, source="dm")
        who = await self.add("Mira", type="character", role="npc")
        for n in (1, 2, 3):
            await self.memory.add_session_heard(GUILD_A, self.c, S1 + n, [Heard(who, 7, 1)])
        review = ui.ThreadsReview(self.c, all_live=True)
        text = await review.current_text(self.interaction())
        assert text is not None
        order = [line.split("**")[1] for line in text.splitlines() if line[:1].isdigit()]
        self.assertEqual(order, ["Active one", "Open one", "Hinted one"])  # resolved is not listed
        self.assertEqual(  # all three have been quiet for three sessions
            sum("⏳" in line for line in text.splitlines() if line[:1].isdigit()), 3
        )

    async def test_a_long_list_pages_and_the_default_review_is_unchanged(self) -> None:
        for n in range(6):
            await self.memory.new_thread(GUILD_A, self.c, f"T{n}", "quest", OPEN, source="dm")
        review = ui.ThreadsReview(self.c, all_live=True)
        await review.current_text(self.interaction())
        self.assertEqual(len(review.page), 4)
        labels = [str(getattr(b, "label", "")) for b in review.children]
        self.assertIn("Next ▶", labels)
        self.assertNotIn("◀ Back", labels)
        review.offset = 4
        await review.current_text(self.interaction())
        self.assertEqual(len(review.page), 2)
        self.assertIn("◀ Back", [str(getattr(b, "label", "")) for b in review.children])
        quiet_only = ui.ThreadsReview(self.c)  # nothing is stalled yet
        self.assertIsNone(await quiet_only.current_text(self.interaction()))

    async def test_the_list_is_this_campaigns_and_the_dms(self) -> None:
        other = (await self.campaigns.create(GUILD_A, "Elsewhere", 7)).id
        await self.memory.new_thread(GUILD_A, other, "Theirs", "quest", OPEN, source="dm")
        await self.memory.new_thread(GUILD_A, self.c, "Ours", "quest", OPEN, source="dm")
        review = ui.ThreadsReview(self.c, all_live=True)
        text = await review.current_text(self.interaction())
        assert text is not None
        self.assertIn("Ours", text)
        self.assertNotIn("Theirs", text)
        it = self.interaction(99)  # a player pressing the button gets refused
        await ui.start_review(it, self.c, all_live=True)
        self.assertIn("DM", it.sent[-1])  # refused, no list

    async def test_fifty_threads_read_fast(self) -> None:
        for n in range(50):
            await self.memory.new_thread(GUILD_A, self.c, f"Thread {n}", "quest", OPEN, source="dm")
        found = await self.memory.threads(GUILD_A, self.c, stalled_after=3)
        self.assertEqual(len(found), 50)
        took = await speed.fastest_async(
            lambda: self.memory.threads(GUILD_A, self.c, stalled_after=3)
        )  # (the best of several: a read does not change anything, #2671)
        self.assertLess(took, 1.0)  # a session start has 3 seconds; this is one read


class Buttons(unittest.TestCase):
    def test_the_bot_registers_the_threads_button(self) -> None:
        import inspect

        from dmbot import bot

        self.assertIn("ThreadListButton", inspect.getsource(bot.DMBot.setup_hook))

    def test_the_threads_button_comes_back_from_its_id_after_a_restart(self) -> None:
        match = ui.ThreadListButton.__discord_ui_compiled_template__.fullmatch(
            "dmbot:threads:list:" + "c" * 32
        )
        assert match is not None
        self.assertEqual(match["campaign"], "c" * 32)
        self.assertEqual(ui.ThreadListButton("c" * 32).item.label, "🧵 Threads")


if __name__ == "__main__":
    unittest.main()
