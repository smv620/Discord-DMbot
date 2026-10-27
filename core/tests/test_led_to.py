"""`led_to` (#1679): the DM says a clue, an event or a thread led to a thread or an event; no loops,
the chain shown on the cards, DM only. Needs the test database."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from dmbot.memory import threads as th
from dmbot.memory.lookup import CampaignLookup
from dmbot.memory.models import OPEN, MemoryRuleError
from dmbot.memory.recall import DM_SCREEN, PLAYERS, RecallService
from dmbot.memory.store import LED_TO_LOOP, UNDO_LED_TO_LOOP
from dmbot.ui import threads as ui
from tests import speed
from tests.test_memory_store import DM, GUILD_A, GUILD_B, ConfirmedMemoryTest


class Pure(unittest.TestCase):
    def test_a_link_to_itself_or_a_loop_is_refused(self) -> None:
        self.assertTrue(th.would_loop([], "a", "a"))
        self.assertFalse(th.would_loop([], "a", "b"))
        chain = [("b", "c"), ("c", "d")]
        self.assertTrue(th.would_loop(chain, "d", "b"))  # b→c→d already, d→b closes it
        self.assertFalse(th.would_loop(chain, "a", "b"))
        self.assertFalse(th.would_loop(chain, "d", "e"))

    def test_a_loop_through_a_node_is_found_however_long(self) -> None:
        self.assertFalse(th.loops_through([], "a"))
        self.assertFalse(th.loops_through([("a", "b"), ("b", "c")], "a"))
        self.assertTrue(th.loops_through([("a", "b"), ("b", "c"), ("c", "a")], "a"))
        self.assertTrue(th.loops_through([("b", "c"), ("c", "b"), ("a", "b")], "b"))
        self.assertFalse(th.loops_through([("b", "c"), ("c", "b"), ("a", "b")], "a"))  # not on it
        long = [(f"n{i}", f"n{i + 1}") for i in range(40)] + [("n40", "n0")]
        self.assertTrue(th.loops_through(long, "n0"))

    def test_a_chain_longer_than_the_limit_is_refused(self) -> None:
        long = [(f"n{i}", f"n{i + 1}") for i in range(30)]
        self.assertTrue(th.would_loop(long, "x", "n0", depth=10))  # too deep to read on a card
        for links, refused in ((9, False), (10, True), (11, True)):  # links already below `end`
            below = [(f"n{i}", f"n{i + 1}") for i in range(links)]
            self.assertIs(th.would_loop(below, "x", "n0", depth=10), refused, links)
        short = [(f"n{i}", f"n{i + 1}") for i in range(3)]
        self.assertFalse(th.would_loop(short, "x", "n0", depth=10))

    def test_a_card_line_shows_five_names_then_how_many_more(self) -> None:
        self.assertIsNone(th.names_line("Led to", []))
        self.assertEqual(th.names_line("Led to", ["A", "B"]), "**Led to:** A, B")
        seven = [str(n) for n in range(7)]
        self.assertEqual(
            th.names_line("Came from", seven), "**Came from:** 0, 1, 2, 3, 4 (+2 more)"
        )


class Chain(ConfirmedMemoryTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.map = await self.add("The torn map", type="clue")
        self.ambush = await self.add("The ambush", type="event")
        self.heir = await self.thread("The Missing Heir", "quest")
        self.tower = await self.thread("The tower", "mystery")

    async def thread(self, name: str, kind: str) -> str:
        made = await self.memory.new_thread(GUILD_A, self.c, name, kind, OPEN, source="dm")
        return made.value.id

    async def link(self, a: str, b: str, **kw: Any) -> Any:
        return await self.memory.led_to(GUILD_A, self.c, a, b, source=kw.pop("source", "dm"))

    async def info(self, thread: str) -> th.ThreadInfo:
        (one,) = [t for t in await self.memory.threads(GUILD_A, self.c) if t.thread.id == thread]
        return one

    async def test_a_clue_an_event_and_a_thread_can_lead_to_a_thread_or_an_event(self) -> None:
        await self.link(self.map, self.heir)
        await self.link(self.ambush, self.heir)
        await self.link(self.heir, self.tower)
        ruins = await self.add("The ruins", type="event")
        await self.link(self.heir, ruins)  # a thread can lead to an event too
        heir = await self.info(self.heir)
        self.assertEqual(sorted(e.name for e in heir.came_from), ["The ambush", "The torn map"])
        self.assertEqual(sorted(e.name for e in heir.led_to), ["The ruins", "The tower"])
        tower = await self.info(self.tower)
        self.assertEqual([e.name for e in tower.came_from], ["The Missing Heir"])
        self.assertEqual(tower.led_to, ())

    async def test_only_the_dm_and_only_between_the_right_kinds(self) -> None:
        with self.assertRaises(MemoryRuleError):
            await self.link(self.map, self.heir, source="extractor")
        person = await self.add("Mira", type="character", role="npc")
        for a, b in ((person, self.heir), (self.map, person), (self.heir, self.map)):
            with self.assertRaises(MemoryRuleError):
                await self.link(a, b)

    async def test_an_unconfirmed_end_is_told_to_confirm_first_by_both_paths(self) -> None:
        from dmbot.memory.store import LED_TO_CONFIRM, LED_TO_KINDS

        waiting = await self.add("The unconfirmed lead", type="event", status="proposed")
        person = await self.add("Mira", type="character", role="npc")
        for a, b in ((waiting, self.heir), (self.map, waiting)):
            with self.assertRaises(MemoryRuleError) as button:
                await self.link(a, b)
            with self.assertRaises(MemoryRuleError) as connect:
                await self.memory.add_relation(
                    GUILD_A, self.c, a, "led_to", b, confidence=0.5, source="dm"
                )
            self.assertEqual(str(button.exception), LED_TO_CONFIRM)
            self.assertEqual(str(connect.exception), LED_TO_CONFIRM)
        with self.assertRaises(MemoryRuleError):  # an extractor's try is told the same
            await self.memory.add_relation(
                GUILD_A, self.c, waiting, "led_to", self.heir, confidence=0.5, source="extractor"
            )
        self.assertEqual(await self.memory.chain(GUILD_A, self.c, self.heir), ([], []))  # no row
        with self.assertRaises(MemoryRuleError) as kinds:  # confirmed but the wrong kind
            await self.link(person, self.heir)
        self.assertEqual(str(kinds.exception), LED_TO_KINDS)

    async def test_each_refusal_has_its_own_words_on_both_paths(self) -> None:
        """The three messages (#1701): a wrong kind (the button refuses; the generic path flags
        a wrong range instead, by design), an unconfirmed end, and a loop (both refuse)."""
        from dmbot.memory.store import LED_TO_CONFIRM, LED_TO_KINDS, LED_TO_LOOP

        person = await self.add("Mira", type="character", role="npc")
        with self.assertRaises(MemoryRuleError) as kinds:
            await self.link(person, self.heir)
        self.assertEqual(str(kinds.exception), LED_TO_KINDS)
        written = await self.memory.add_relation(  # the generic path: kept, with a flag
            GUILD_A, self.c, person, "led_to", self.heir, confidence=0.5, source="dm"
        )
        self.assertTrue(written.value[1])  # (flags)
        await self.link(self.heir, self.tower)
        with self.assertRaises(MemoryRuleError) as button:
            await self.link(self.tower, self.heir)
        with self.assertRaises(MemoryRuleError) as connect:
            await self.memory.add_relation(
                GUILD_A, self.c, self.tower, "led_to", self.heir, confidence=0.5, source="dm"
            )
        self.assertEqual((str(button.exception), str(connect.exception)), (LED_TO_LOOP,) * 2)
        self.assertNotEqual(LED_TO_CONFIRM, LED_TO_LOOP)

    async def rejected_then_looped(self) -> tuple[str, str]:
        """heir→tower made and turned down, then tower→heir made: confirming the first would
        close a loop. Returns the first link's id and the second's."""
        first = (await self.link(self.heir, self.tower)).value
        await self.memory.update_relation(GUILD_A, self.c, first.id, source="dm", status="rejected")
        second = (await self.link(self.tower, self.heir)).value
        return first.id, second.id

    async def test_confirming_a_turned_down_link_that_would_close_a_loop_is_refused(self) -> None:
        from dmbot.memory.store import LED_TO_LOOP

        first, _ = await self.rejected_then_looped()
        with self.assertRaises(MemoryRuleError) as refused:
            await self.memory.update_relation(
                GUILD_A, self.c, first, source="dm", status="confirmed"
            )
        self.assertEqual(str(refused.exception), LED_TO_LOOP)
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute("SELECT status FROM memory_relations WHERE id = %s", (first,))
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual(row["status"], "rejected")  # nothing changed
        self.assertEqual(
            [e.name for e in (await self.info(self.heir)).led_to], []
        )  # and no loop on the cards

    async def test_confirming_a_turned_down_link_with_no_loop_still_works(self) -> None:
        first = (await self.link(self.heir, self.tower)).value
        await self.memory.update_relation(GUILD_A, self.c, first.id, source="dm", status="rejected")
        again = await self.memory.update_relation(
            GUILD_A, self.c, first.id, source="dm", status="confirmed"
        )
        self.assertEqual(again.value.status, "confirmed")
        self.assertEqual([e.name for e in (await self.info(self.heir)).led_to], ["The tower"])

    async def test_confirming_a_link_to_an_end_that_is_not_confirmed_is_refused(self) -> None:
        from dmbot.memory.store import LED_TO_CONFIRM

        first = (await self.link(self.heir, self.tower)).value
        await self.memory.update_relation(GUILD_A, self.c, first.id, source="dm", status="rejected")
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "UPDATE memory_entities SET status = 'proposed' WHERE id = %s", (self.tower,)
            )
        with self.assertRaises(MemoryRuleError) as refused:
            await self.memory.update_relation(
                GUILD_A, self.c, first.id, source="dm", status="confirmed"
            )
        self.assertEqual(str(refused.exception), LED_TO_CONFIRM)

    async def test_a_merge_that_would_make_a_loop_is_refused_and_changes_nothing(self) -> None:
        from dmbot.memory.store import MERGE_LED_TO_LOOP

        a = await self.thread("The old rumour", "mystery")
        b = await self.thread("The rumour again", "mystery")
        await self.link(a, self.tower)  # a led to the tower
        await self.link(
            self.tower, b
        )  # and the tower led to b: a and b are one thread? no loop yet
        with self.assertRaises(MemoryRuleError) as refused:
            await self.memory.merge(GUILD_A, self.c, b, a, source="dm", dm_said_same=True)
        self.assertEqual(str(refused.exception), MERGE_LED_TO_LOOP)
        self.assertEqual((await self.memory.entity(GUILD_A, self.c, a)).status, "confirmed")  # type: ignore[union-attr]
        self.assertEqual([e.name for e in (await self.info(a)).led_to], ["The tower"])

    async def test_a_merge_with_no_loop_moves_the_links_as_before(self) -> None:
        a = await self.thread("The old rumour", "mystery")
        b = await self.thread("The rumour again", "mystery")
        await self.link(a, self.tower)
        await self.memory.merge(GUILD_A, self.c, b, a, source="dm", dm_said_same=True)
        self.assertEqual([e.name for e in (await self.info(b)).led_to], ["The tower"])

    async def test_a_loop_that_was_there_before_does_not_block_a_merge(self) -> None:
        """A backup from before the checks may hold one (restore trusts it, #2673): a merge that
        touches it must still work. Here both threads have live links, so the graph is read and it
        is the old loop that lets the merge through."""
        first, _ = await self.rejected_then_looped()
        async with self.db.guild(GUILD_A) as conn:  # as a restored file would have it
            await conn.execute(
                "UPDATE memory_relations SET status = 'confirmed' WHERE id = %s", (first,)
            )
        other = await self.thread("Another rumour", "mystery")
        await self.link(other, self.tower)  # other → tower, as heir → tower is
        # heir is on the old loop; joining `other` into it keeps that loop, which is not new
        await self.memory.merge(GUILD_A, self.c, self.heir, other, source="dm", dm_said_same=True)
        merged = await self.memory.entity(GUILD_A, self.c, other)
        assert merged is not None
        self.assertEqual(merged.status, "merged")

    async def test_a_many_merge_that_would_make_a_loop_writes_none_of_it(self) -> None:
        from dmbot.memory.store import MERGE_LED_TO_LOOP

        a = await self.thread("The old rumour", "mystery")
        b = await self.thread("The rumour again", "mystery")
        c = await self.thread("A third rumour", "mystery")
        await self.link(a, self.tower)
        await self.link(self.tower, b)
        with self.assertRaises(MemoryRuleError) as refused:
            await self.memory.merge_many(GUILD_A, self.c, b, [c, a], source="dm")
        self.assertEqual(str(refused.exception), MERGE_LED_TO_LOOP)
        for name in (a, b, c):  # the merge of c, which came first, was not kept either
            self.assertEqual((await self.memory.entity(GUILD_A, self.c, name)).status, "confirmed")  # type: ignore[union-attr]

    async def test_an_undo_that_would_bring_back_a_loop_is_refused(self) -> None:
        first = (await self.link(self.heir, self.tower)).value
        removed = await self.memory.update_relation(
            GUILD_A, self.c, first.id, source="dm", status="rejected"
        )
        assert removed.batch is not None
        await self.link(self.tower, self.heir)  # allowed: heir → tower is no longer live
        with self.assertRaises(MemoryRuleError) as refused:
            await self.memory.undo(GUILD_A, self.c, removed.batch)
        self.assertEqual(str(refused.exception), UNDO_LED_TO_LOOP)
        self.assertEqual([e.name for e in (await self.info(self.heir)).led_to], [])

    async def test_keeping_a_waiting_guess_that_would_close_a_loop_is_refused(self) -> None:
        first = (await self.link(self.heir, self.tower)).value
        await self.memory.update_relation(GUILD_A, self.c, first.id, source="dm", status="rejected")
        back = (await self.link(self.tower, self.heir)).value
        async with self.db.guild(GUILD_A) as conn:  # the extractor's guess, waiting for the DM
            await conn.execute(
                "UPDATE memory_relations SET status = 'proposed', secret = true,"
                " source = 'extractor' WHERE id = %s",
                (back.id,),
            )
        await self.memory.update_relation(
            GUILD_A, self.c, first.id, source="dm", status="confirmed"
        )  # heir → tower is live again; the guess is not
        with self.assertRaises(MemoryRuleError) as refused:
            await self.memory.update_relation(
                GUILD_A, self.c, back.id, source="dm", status="confirmed"
            )
        self.assertEqual(str(refused.exception), LED_TO_LOOP)
        self.assertEqual([e.name for e in (await self.info(self.tower)).led_to], [])

    async def test_an_undo_with_no_loop_still_brings_the_link_back(self) -> None:
        first = (await self.link(self.heir, self.tower)).value
        removed = await self.memory.update_relation(
            GUILD_A, self.c, first.id, source="dm", status="rejected"
        )
        assert removed.batch is not None
        await self.memory.undo(GUILD_A, self.c, removed.batch)
        self.assertEqual([e.name for e in (await self.info(self.heir)).led_to], ["The tower"])

    async def test_a_link_to_itself_or_a_loop_is_refused(self) -> None:
        with self.assertRaises(MemoryRuleError):
            await self.link(self.heir, self.heir)
        await self.link(self.heir, self.tower)
        with self.assertRaises(MemoryRuleError):
            await self.link(self.tower, self.heir)
        await self.link(self.tower, self.ambush)
        with self.assertRaises(MemoryRuleError):  # a longer way round
            await self.link(self.ambush, self.heir)

    async def test_the_other_ways_in_are_secret_and_loop_checked_too(self) -> None:
        rel = (
            await self.memory.add_relation(
                GUILD_A, self.c, self.map, "led_to", self.heir, confidence=0.5, source="extractor"
            )
        ).value[0]
        self.assertTrue(rel.secret)
        with self.assertRaises(MemoryRuleError):
            await self.memory.update_relation(GUILD_A, self.c, rel.id, source="dm", secret=False)
        await self.link(self.heir, self.tower)
        with self.assertRaises(MemoryRuleError):
            await self.memory.add_relation(
                GUILD_A, self.c, self.tower, "led_to", self.heir, confidence=0.5, source="dm"
            )

    async def test_an_ended_link_does_not_count_for_the_loop_check(self) -> None:
        made = await self.link(self.heir, self.tower)
        await self.memory.update_relation(
            GUILD_A, self.c, made.value.id, source="dm", to_session_at=1
        )
        await self.link(self.tower, self.heir)

    async def test_another_campaigns_entry_cannot_be_linked(self) -> None:
        other = (await self.campaigns.create(GUILD_A, "Elsewhere", 7)).id
        theirs = (
            await self.memory.new_thread(GUILD_A, other, "Theirs", "quest", OPEN, source="dm")
        ).value.id
        with self.assertRaises(MemoryRuleError):
            await self.link(self.heir, theirs)
        with self.assertRaises(MemoryRuleError):
            await self.memory.led_to(GUILD_B, self.c, self.heir, self.tower, source="dm")

    async def test_a_chain_is_not_a_thread_moving(self) -> None:
        before = (await self.info(self.heir)).last_touch
        self.now += 100_000
        await self.link(self.map, self.heir)
        self.assertEqual((await self.info(self.heir)).last_touch, before)

    async def test_a_clues_and_an_events_chain_is_read_in_one_call(self) -> None:
        await self.link(self.map, self.heir)
        await self.link(self.map, self.ambush)
        came, went = await self.memory.chain(GUILD_A, self.c, self.map)
        self.assertEqual([e.name for e in came], [])
        self.assertEqual([e.name for e in went], ["The ambush", "The Missing Heir"])
        came, went = await self.memory.chain(GUILD_A, self.c, self.ambush)
        self.assertEqual(([e.name for e in came], went), (["The torn map"], []))

    async def test_the_links_are_the_dms_alone(self) -> None:
        await self.link(self.map, self.heir)
        await self.link(self.ambush, self.tower)
        service = RecallService(self.memory)
        for seeds in (["The torn map"], ["The ambush"], ["The Missing Heir"], [self.tower]):
            got = await service.recall(GUILD_A, self.c, seeds=seeds, audience=PLAYERS, opted_in={7})
            self.assertNotIn("led to", " ".join(i.text for i in got.items).lower(), seeds)
            dm = await service.recall(
                GUILD_A, self.c, seeds=["The torn map"], audience=DM_SCREEN, opted_in={7}
            )
        self.assertIn("led to", " ".join(i.text for i in dm.items).lower())
        whole = CampaignLookup.build(await self.memory.lookup_data(GUILD_A, self.c))
        self.assertNotIn(self.heir, whole.for_viewer(False).entities)

    async def test_the_links_come_back_from_a_backup(self) -> None:
        from dmbot.campaigns.store import decode_backup, encode_backup

        await self.link(self.map, self.heir)
        raw = encode_backup(await self.campaigns.export(GUILD_A, self.c))
        restored = await self.campaigns.import_backup(GUILD_B, decode_backup(raw), DM)
        back = {t.thread.name: t for t in await self.memory.threads(GUILD_B, restored.id)}
        self.assertEqual([e.name for e in back["The Missing Heir"].came_from], ["The torn map"])

    async def test_fifty_threads_with_links_read_in_the_same_single_read(self) -> None:

        previous = self.heir
        for n in range(49):
            nxt = await self.thread(f"Thread {n}", "quest")
            await self.link(previous, nxt)
            previous = nxt
        found = await self.memory.threads(GUILD_A, self.c)
        self.assertEqual(len(found), 51)
        took = await speed.fastest_async(lambda: self.memory.threads(GUILD_A, self.c))
        self.assertLess(took, speed.allowed(1.0))


class Cards(ConfirmedMemoryTest):
    def interaction(self, user: int = DM) -> Any:
        it = MagicMock()
        it.user = SimpleNamespace(id=user)
        it.guild = SimpleNamespace(id=GUILD_A)
        it.client = SimpleNamespace(
            memory=self.memory, campaigns=self.campaigns, lookup=None,
            settings=SimpleNamespace(thread_stalled_sessions=3), transcripts=None,
        )  # fmt: skip
        it.response.is_done = lambda: False
        it.response.defer = AsyncMock()
        it.response.send_modal = AsyncMock()
        it.response.send_message = AsyncMock()
        it.followup.send = AsyncMock()
        return it

    async def test_a_thread_card_shows_came_from_and_led_to_with_the_cap(self) -> None:
        heir = (
            await self.memory.new_thread(GUILD_A, self.c, "Heir", "quest", OPEN, source="dm")
        ).value.id
        for n in range(7):
            clue = await self.add(f"Clue {n}", type="clue")
            await self.memory.led_to(GUILD_A, self.c, clue, heir, source="dm")
        tower = (
            await self.memory.new_thread(GUILD_A, self.c, "Tower", "mystery", OPEN, source="dm")
        ).value.id
        await self.memory.led_to(GUILD_A, self.c, heir, tower, source="dm")
        (info,) = [t for t in await self.memory.threads(GUILD_A, self.c) if t.thread.id == heir]
        lines = ui.thread_lines(info, {})
        came = next(x for x in lines if x.startswith("**Came from:**"))
        self.assertTrue(came.endswith("(+2 more)"))
        self.assertIn("**Led to:** Tower", lines)

    async def test_the_led_to_button_is_the_dms_and_opens_the_form(self) -> None:
        from dmbot.ui.name_card import NameCard

        clue = await self.add("A clue", type="clue")
        view = NameCard(self.c, clue, clue=True, led=True)
        self.assertIn("➡ Led to…", [str(getattr(b, "label", "")) for b in view.children])
        player = self.interaction(99)
        await view._led_to(player)
        player.response.send_modal.assert_not_called()
        dm = self.interaction()
        await view._led_to(dm)
        dm.response.send_modal.assert_awaited_once()
        self.assertIsInstance(dm.response.send_modal.await_args.args[0], ui.LedToForm)
