"""Promises the DM keeps (#1674): a fifth thread kind with its own words for the same states,
who made it and to whom (the DM's word), a due note, the same stalled rule and audience rules as
any thread. Needs the test database."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

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
)
from dmbot.memory.recall import DM_SCREEN, PLAYERS, RecallService
from dmbot.ui import threads as ui
from tests.test_memory_store import DM, GUILD_A, GUILD_B, ConfirmedMemoryTest

S1, S2, S3, S4 = (1_700_000_000 + n * 100_000 for n in (1, 2, 3, 4))


class Words(unittest.TestCase):
    def test_a_promise_says_the_same_states_in_its_own_words(self) -> None:
        self.assertEqual(
            th.words_for("promise"),
            {
                HINTED: "Made in passing",
                OPEN: "Open",
                GOING: "Due soon",
                RESOLVED: "Kept",
                ABANDONED: "Broken or let go",
            },
        )
        self.assertEqual(th.words_for("quest"), th.STATE_WORDS)  # the others are as they were
        self.assertEqual(set(th.PROMISE_WORDS), set(th.STATE_WORDS))  # no new state

    def test_the_history_uses_the_promise_words_and_checks_stay_checks(self) -> None:
        def row(c: str, s: int, n: int) -> Any:
            return SimpleNamespace(condition=c, from_session_at=s, id=f"{n:032x}")

        history = [row(OPEN, S1, 1), row(GOING, S2, 2), row(GOING, S3, 3), row(RESOLVED, S4, 4)]
        numbers = {S1: 1, S2: 2, S3: 3, S4: 4}
        self.assertEqual(
            th.history_lines(history, numbers, "promise"),
            [
                "Open (Session 1)",
                "Due soon (Session 2)",
                "checked, still going (Session 3)",
                "Kept (Session 4)",
            ],
        )

    def test_the_after_session_line_says_promise_where_it_names_one(self) -> None:
        self.assertEqual(th.stalled_line(1, 3, 1), "1 promise hasn't moved for 3 or more sessions.")
        self.assertEqual(
            th.stalled_line(2, 3, 2), "2 promises haven't moved for 3 or more sessions."
        )
        self.assertEqual(
            th.stalled_line(3, 3, 1),
            "3 threads haven't moved for 3 or more sessions (1 is a promise).",
        )
        self.assertEqual(th.stalled_line(1, 3), "1 thread hasn't moved for 3 or more sessions.")


class Promises(ConfirmedMemoryTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        for name in ("set_condition", "check_thread", "new_thread", "promise_party", "set_due"):
            self._tick_before(name)
        self.kael = await self.add("Kael", type="character", role="npc")
        self.village = await self.add("Dunhollow", type="place")
        self.guild = await self.add("The Harpers", type="faction")
        self.promise = (
            await self.memory.new_thread(
                GUILD_A, self.c, "Bring the healer back", "promise", OPEN, source="dm"
            )
        ).value.id

    def _tick_before(self, name: str) -> None:
        real = getattr(self.memory, name)

        async def ticked(*a: Any, **kw: Any) -> Any:
            self.now += 5
            return await real(*a, **kw)

        setattr(self.memory, name, ticked)

    async def info(self, after: int = 3) -> th.ThreadInfo:
        found = await self.memory.threads(GUILD_A, self.c, stalled_after=after)
        (one,) = [t for t in found if t.thread.id == self.promise]
        return one

    async def test_a_promise_is_a_thread_kind(self) -> None:
        onto = await self.memory.ontology(GUILD_A, self.c)
        self.assertTrue(onto.is_a("promise", "thread"))
        made = await self.info()
        self.assertEqual((made.thread.type, made.state.condition), ("promise", OPEN))  # type: ignore[union-attr]

    async def test_who_promised_and_to_whom_are_saved_and_shown(self) -> None:
        await self.memory.promise_party(
            GUILD_A, self.c, self.promise, self.kael, to=False, source="dm"
        )
        await self.memory.promise_party(
            GUILD_A, self.c, self.promise, self.village, to=True, source="dm"
        )
        await self.memory.promise_party(
            GUILD_A, self.c, self.promise, self.guild, to=True, source="dm"
        )
        got = await self.info()
        self.assertEqual([e.name for e in got.promised_by], ["Kael"])
        self.assertEqual(sorted(e.name for e in got.promised_to), ["Dunhollow", "The Harpers"])
        lines = ui.thread_lines(got, {})
        self.assertEqual(
            lines[0], "🤝 **Kael** promised **Dunhollow, The Harpers**: Bring the healer back"
        )
        self.assertEqual(lines[1], "🧵 **Open**")

    async def test_the_card_asks_for_who_when_nobody_is_named(self) -> None:
        lines = ui.thread_lines(await self.info(), {})
        self.assertEqual(lines[0], "🤝 **Promise:** Bring the healer back")
        self.assertIn("not set yet", lines[1])
        await self.memory.promise_party(
            GUILD_A, self.c, self.promise, self.kael, to=False, source="dm"
        )
        half = ui.thread_lines(await self.info(), {})
        self.assertIn("**Promised by:** Kael. **To:** not set yet.", half)

    async def test_only_the_dm_names_the_parties_and_only_for_a_promise(self) -> None:
        with self.assertRaises(MemoryRuleError):
            await self.memory.promise_party(
                GUILD_A, self.c, self.promise, self.kael, to=False, source="extractor"
            )
        quest = (
            await self.memory.new_thread(GUILD_A, self.c, "A quest", "quest", OPEN, source="dm")
        ).value.id
        with self.assertRaises(MemoryRuleError):
            await self.memory.promise_party(
                GUILD_A, self.c, quest, self.kael, to=False, source="dm"
            )
        with self.assertRaises(MemoryRuleError):  # who promised is a character or a group
            await self.memory.promise_party(
                GUILD_A, self.c, self.promise, self.village, to=False, source="dm"
            )

    async def test_a_wrong_kind_of_party_is_refused_both_ways(self) -> None:
        item = await self.add("The Moonblade", type="item")
        event = await self.add("The ambush", type="event")
        for who in (item, event):
            for to in (False, True):
                with self.assertRaises(MemoryRuleError):
                    await self.memory.promise_party(
                        GUILD_A, self.c, self.promise, who, to=to, source="dm"
                    )

    async def test_naming_the_same_party_twice_shows_it_once(self) -> None:
        for _ in range(2):
            await self.memory.promise_party(
                GUILD_A, self.c, self.promise, self.kael, to=False, source="dm"
            )
        self.assertEqual([e.name for e in (await self.info()).promised_by], ["Kael"])

    async def test_a_party_and_a_due_note_can_each_be_undone(self) -> None:
        fact = await self.memory.promise_party(
            GUILD_A, self.c, self.promise, self.kael, to=False, source="dm"
        )
        due = await self.memory.set_due(GUILD_A, self.c, self.promise, "soon", source="dm")
        assert fact.batch is not None and due.batch is not None
        await self.memory.undo(GUILD_A, self.c, due.batch)
        await self.memory.undo(GUILD_A, self.c, fact.batch)
        got = await self.info()
        self.assertEqual((got.promised_by, got.due), ((), ""))

    async def test_the_due_note_is_free_text_and_clears(self) -> None:
        await self.memory.set_due(
            GUILD_A, self.c, self.promise, "  by the   full moon ", source="dm"
        )
        got = await self.info()
        self.assertEqual(got.due, "by the full moon")
        self.assertIn("**Due:** by the full moon", ui.thread_lines(got, {}))
        await self.memory.set_due(GUILD_A, self.c, self.promise, "", source="dm")
        self.assertEqual((await self.info()).due, "")
        with self.assertRaises(MemoryRuleError):
            await self.memory.set_due(GUILD_A, self.c, self.promise, "x" * 500, source="dm")
        with self.assertRaises(MemoryRuleError):
            await self.memory.set_due(GUILD_A, self.c, self.promise, "soon", source="extractor")
        quest = (
            await self.memory.new_thread(GUILD_A, self.c, "A quest", "quest", OPEN, source="dm")
        ).value.id
        with self.assertRaises(MemoryRuleError):  # only a promise has a due note
            await self.memory.set_due(GUILD_A, self.c, quest, "soon", source="dm")

    async def test_a_promise_goes_stalled_like_a_thread_and_kept_ones_never_do(self) -> None:
        await self.memory.set_condition(
            GUILD_A, self.c, self.promise, GOING, source="dm", from_session_at=S1
        )
        for s in (S2, S3, S4):
            await self.memory.add_session_heard(GUILD_A, self.c, s, [Heard(self.kael, 7, 1)])
        got = await self.info(3)
        self.assertEqual((got.quiet_sessions, got.stalled), (3, True))
        await self.memory.set_condition(
            GUILD_A, self.c, self.promise, RESOLVED, source="dm", from_session_at=S4
        )
        self.assertFalse((await self.info(3)).stalled)  # kept

    async def test_the_way_back_from_kept_or_broken_is_the_dms_alone(self) -> None:
        await self.memory.set_condition(GUILD_A, self.c, self.promise, ABANDONED, source="dm")
        with self.assertRaises(MemoryRuleError):
            await self.memory.set_condition(GUILD_A, self.c, self.promise, OPEN, source="extractor")
        await self.memory.set_condition(GUILD_A, self.c, self.promise, OPEN, source="dm")

    async def test_a_promise_is_the_dms_alone_in_lookup_and_recall(self) -> None:
        await self.memory.promise_party(
            GUILD_A, self.c, self.promise, self.kael, to=False, source="dm"
        )
        service = RecallService(self.memory)
        dm = await service.recall(GUILD_A, self.c, seeds=["Kael"], audience=DM_SCREEN, opted_in={7})
        self.assertIn("Bring the healer back", " ".join(i.text for i in dm.items))
        for seeds in (["Kael"], ["Bring the healer back"], [self.promise]):
            got = await service.recall(GUILD_A, self.c, seeds=seeds, audience=PLAYERS, opted_in={7})
            self.assertNotIn("healer", " ".join(i.text for i in got.items), seeds)
        whole = CampaignLookup.build(await self.memory.lookup_data(GUILD_A, self.c))
        self.assertIn(self.promise, whole.entities)
        self.assertNotIn(self.promise, whole.for_viewer(False).entities)

    async def test_another_campaign_or_server_never_sees_it(self) -> None:
        other = (await self.campaigns.create(GUILD_A, "Elsewhere", 7)).id
        self.assertEqual(await self.memory.threads(GUILD_A, other), [])
        with self.assertRaises(MemoryRuleError):
            await self.memory.threads(GUILD_B, self.c)

    async def test_it_comes_back_from_a_backup_with_its_parties_and_due_note(self) -> None:
        from dmbot.campaigns.store import decode_backup, encode_backup

        await self.memory.promise_party(
            GUILD_A, self.c, self.promise, self.kael, to=False, source="dm"
        )
        await self.memory.set_due(GUILD_A, self.c, self.promise, "by the full moon", source="dm")
        raw = encode_backup(await self.campaigns.export(GUILD_A, self.c))
        restored = await self.campaigns.import_backup(GUILD_B, decode_backup(raw), DM)
        (back,) = await self.memory.threads(GUILD_B, restored.id)
        self.assertEqual(back.thread.type, "promise")
        self.assertEqual(
            ([e.name for e in back.promised_by], back.due), (["Kael"], "by the full moon")
        )


class Screens(ConfirmedMemoryTest):
    def interaction(self, user: int = DM) -> Any:
        sent: list[Any] = []

        async def reply(text: str = "", **kw: Any) -> None:
            sent.append((str(kw.get("content", text)), kw.get("view")))

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

    async def test_the_review_buttons_say_kept_and_broken_for_a_promise(self) -> None:
        promise = (
            await self.memory.new_thread(
                GUILD_A, self.c, "Bring it back", "promise", GOING, source="dm", from_session_at=S1
            )
        ).value.id
        kael = await self.add("Kael", type="character")
        for s in (S2, S3, S4):
            await self.memory.add_session_heard(GUILD_A, self.c, s, [Heard(kael, 7, 1)])
        review = ui.ThreadsReview(self.c)
        text = await review.current_text(self.interaction())
        assert text is not None  # three sessions went by without it moving
        self.assertIn("Bring it back", text)
        self.assertEqual(
            [getattr(b, "label", "") for b in review.children],
            ["1 · Still going", "1 · Kept", "1 · Broken"],
        )
        self.assertTrue(promise)

    async def test_only_the_dm_gets_the_promise_buttons_to_work(self) -> None:
        from dmbot.ui.name_card import NameCard

        promise = (
            await self.memory.new_thread(GUILD_A, self.c, "A promise", "promise", OPEN, source="dm")
        ).value.id
        view = NameCard(self.c, promise, thread=True, promise=True)
        labels = [
            str(getattr(b, "label", "")) for b in view.children if getattr(b, "row", None) == 3
        ]
        self.assertEqual(
            labels,
            ["🧵 Change state", "🤝 Who promised", "🤝 Promised to", "📅 Due", "📌 Link an event"],
        )
        it = self.interaction(99)
        await view._promise_by(it)  # a player: nothing opens
        it.response.send_modal.assert_not_called()

    async def test_add_a_promise_makes_an_open_promise_with_its_due_note(self) -> None:
        form = ui.NewPromiseForm(self.c)
        form.what._value = "Bring the healer back"
        form.due._value = "by the full moon"
        await form.on_submit(self.interaction())
        (made,) = await self.memory.threads(GUILD_A, self.c)
        assert made.state is not None
        self.assertEqual(
            (made.thread.type, made.state.condition, made.due),
            ("promise", OPEN, "by the full moon"),
        )

    async def test_a_player_cannot_add_a_promise(self) -> None:
        form = ui.NewPromiseForm(self.c)
        form.what._value = "Sneaky"
        form.due._value = ""
        await form.on_submit(self.interaction(99))
        self.assertEqual(await self.memory.threads(GUILD_A, self.c), [])

    async def test_the_change_state_buttons_use_the_promise_words(self) -> None:
        view = ui.ChangeState(self.c, "x" * 32, OPEN, "promise")
        self.assertEqual(
            [str(getattr(b, "label", "")) for b in view.children][:5],
            ["Made in passing", "Open", "Due soon", "Kept", "Broken or let go"],
        )
