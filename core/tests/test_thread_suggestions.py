"""The lore reader's story-thread suggestions (#4453, quests slice 1; Supervisor's ruling on #4453):
waiting until the DM keeps or turns one down; ✅ Keep writes the thread, its state and the chosen
people in one change, and ↩ Undo puts the suggestion back; a turned-down one never comes back; a
person merged since is followed and one gone is skipped; nothing of it is an entry until kept, so
the names count never sees it. Invented names. Needs Postgres."""

from __future__ import annotations

from dmbot.memory import thread_read as tr
from dmbot.memory.models import DM, MemoryRuleError, name_key
from dmbot.memory.thread_suggestions import GONE, ThreadSuggestionStore, unkeep_in
from tests.test_memory_store import GUILD_A, MemoryTest


class Suggestions(MemoryTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.store = ThreadSuggestionStore(self.db)
        self.pell = await self.add("Old Pell", status="confirmed")
        self.troll = await self.add("Gnashwater", status="confirmed")
        self.ilsa = await self.add("Ilsa Rookwind", status="confirmed")

    def job(self, name: str = "Old Pell's job: Gnashwater", kind: str = "quest") -> tr.Suggestion:
        return tr.Suggestion(
            kind, name, "drive the troll off", "Old Pell offers thirty gold",
            ((tr.GIVER, self.pell), (tr.TARGET, self.troll)), tr.STATE[kind],
        )  # fmt: skip

    async def keep(self, parts: list[tuple[str, str]] | None = None) -> int:
        (s,) = await self.store.waiting(GUILD_A, self.c)
        kept = await self.memory.keep_thread(
            GUILD_A, self.c, s.name, s.kind, s.state, aim=s.aim,
            parts=list(s.parts) if parts is None else parts, source=DM, suggestion_id=s.id,
        )  # fmt: skip
        assert kept.batch is not None
        return kept.batch

    async def test_waiting_kept_with_its_state_and_people_and_undo_puts_it_back(self) -> None:
        names_before = len(await self.memory.entities(GUILD_A, self.c, statuses=["proposed"]))
        self.assertEqual(await self.store.offer(GUILD_A, self.c, [self.job()], "Notes", 5), 1)
        self.assertEqual(await self.store.count(GUILD_A, self.c), 1)
        # (a suggestion is no entry: the names count never sees it)
        self.assertEqual(
            len(await self.memory.entities(GUILD_A, self.c, statuses=["proposed"])), names_before
        )
        batch = await self.keep()
        (t,) = await self.memory.threads(GUILD_A, self.c)
        self.assertEqual((t.thread.name, t.thread.type), ("Old Pell's job: Gnashwater", "quest"))
        self.assertEqual(t.thread.description, "drive the troll off")
        assert t.state is not None
        self.assertEqual(t.state.condition, "active")
        self.assertEqual(
            sorted((i.who.id, i.role) for i in t.involved),
            sorted([(self.pell, tr.GIVER), (self.troll, tr.TARGET)]),
        )
        self.assertEqual(await self.store.count(GUILD_A, self.c), 0)
        # (the card's ↩ Undo: the thread goes and the suggestion waits, in one write)
        await self.memory.undo(GUILD_A, self.c, batch, after=unkeep_in(batch))
        self.assertEqual(await self.memory.threads(GUILD_A, self.c), [])
        self.assertEqual(await self.store.count(GUILD_A, self.c), 1)

    async def test_a_hook_is_not_started_and_people_left_out_stay_out(self) -> None:
        await self.store.offer(GUILD_A, self.c, [self.job("Pell's offer", "hook")], "", 5)
        await self.keep(parts=[(tr.GIVER, self.pell)])
        (t,) = await self.memory.threads(GUILD_A, self.c)
        assert t.state is not None
        self.assertEqual(t.state.condition, "open")
        self.assertEqual([i.who.id for i in t.involved], [self.pell])

    async def test_a_kept_thread_says_where_it_came_from_and_unkeeping_forgets_it(self) -> None:
        """(#4554: the entry's own change links it, so a kept quest can say where it was read)"""
        await self.store.offer(GUILD_A, self.c, [self.job()], "Winter plot", 5)
        batch = await self.keep()
        (t,) = await self.memory.threads(GUILD_A, self.c)
        got = await self.store.kept_from(GUILD_A, self.c, t.thread.id)
        assert got is not None
        self.assertEqual((got.upload, got.session_id), ("Winter plot", None))
        self.assertIsNone(await self.store.kept_from(GUILD_A, self.c, self.pell))
        self.assertTrue(await self.store.unkeep(GUILD_A, self.c, batch))  # (↩ on the card)
        self.assertIsNone(await self.store.kept_from(GUILD_A, self.c, t.thread.id))

    async def test_turned_down_never_comes_back_and_a_second_answer_is_refused(self) -> None:
        await self.store.offer(GUILD_A, self.c, [self.job()], "", 5)
        (s,) = await self.store.waiting(GUILD_A, self.c)
        self.assertTrue(await self.store.turn_down(GUILD_A, self.c, s.id))
        self.assertFalse(await self.store.turn_down(GUILD_A, self.c, s.id))
        self.assertEqual(await self.store.offer(GUILD_A, self.c, [self.job()], "", 6), 0)
        self.assertIn((name_key("Old Pell's job: Gnashwater"), frozenset({self.pell, self.troll})),
                      await self.store.known(GUILD_A, self.c))  # fmt: skip
        with self.assertRaisesRegex(MemoryRuleError, GONE):
            await self.memory.keep_thread(
                GUILD_A, self.c, s.name, s.kind, s.state, aim="", parts=[], source=DM,
                suggestion_id=s.id,
            )  # fmt: skip
        self.assertEqual(await self.memory.threads(GUILD_A, self.c), [])  # (nothing half-kept)
        self.assertTrue(await self.store.unturn(GUILD_A, self.c, s.id))
        self.assertEqual(await self.store.count(GUILD_A, self.c), 1)

    async def test_a_person_merged_since_is_followed_and_one_gone_is_skipped(self) -> None:
        await self.store.offer(GUILD_A, self.c, [self.job()], "", 5)
        pell2 = await self.add("Pell the Ferryman", status="confirmed")
        await self.memory.merge(GUILD_A, self.c, pell2, self.pell, source=DM, dm_said_same=True)
        await self.memory.set_entity_status(GUILD_A, self.c, self.troll, "rejected", source=DM)
        await self.keep()
        (t,) = await self.memory.threads(GUILD_A, self.c)
        self.assertEqual([(i.who.id, i.role) for i in t.involved], [(pell2, tr.GIVER)])

    async def test_a_name_the_dm_hasnt_kept_is_left_out(self) -> None:
        """(the reviewer's point on #4453: keeping a thread never confirms a connection to a waiting
        name; the card says it is left out)"""
        waiting = await self.add("Tobin", status="proposed")
        await self.store.offer(GUILD_A, self.c, [self.job()], "", 5)
        await self.keep(parts=[(tr.GIVER, self.pell), (tr.MISSING, waiting)])
        (t,) = await self.memory.threads(GUILD_A, self.c)
        self.assertEqual([i.who.id for i in t.involved], [self.pell])

    async def test_only_the_dm_keeps(self) -> None:
        with self.assertRaises(MemoryRuleError):
            await self.memory.keep_thread(
                GUILD_A, self.c, "x", "quest", "active", aim="", parts=[], source="background"
            )


class FromTheUpload(MemoryTest):
    """`store_thread_suggestions`, the upload's step, with the AI's answer written here: the code
    checks it, keeps the party out and leaves suggestions waiting, never entries."""

    async def test_a_lore_upload_leaves_suggestions_waiting(self) -> None:
        import json
        from types import SimpleNamespace

        from dmbot.ai import Reply
        from dmbot.ui.name_background import store_thread_suggestions

        await self.add("Old Pell", status="confirmed")
        await self.add("Gnashwater", status="confirmed")
        text = "Old Pell offers thirty gold to whoever drives the troll Gnashwater off."
        answer = {
            "threads": [
                {"shape": "ask", "aim": "drive the troll off", "giver": "Old Pell",
                 "target": "Gnashwater", "taker": "the party", "missing": [], "in_danger": [],
                 "quote": "Old Pell offers thirty gold"},
                {"shape": "ask", "aim": "made up", "giver": "Old Pell", "target": "",
                 "taker": "", "missing": [], "in_danger": [], "quote": "not in the text"},
            ]
        }  # fmt: skip

        class FakeAI:
            async def complete(self, *_: object, **__: object) -> Reply:
                return Reply(json.dumps(answer), False)

        campaign = await self.campaigns.get(GUILD_A, self.c)
        assert campaign is not None
        bot = SimpleNamespace(memory=self.memory)
        made, short = await store_thread_suggestions(bot, campaign, text, "Notes", FakeAI())
        self.assertEqual((made, short), (1, ""))
        (s,) = await ThreadSuggestionStore(self.db).waiting(GUILD_A, self.c)
        self.assertEqual(
            (s.name, s.kind, s.upload), ("Old Pell's job: Gnashwater", "quest", "Notes")
        )
        self.assertEqual(await self.memory.threads(GUILD_A, self.c), [])  # (never added by itself)
        again, _ = await store_thread_suggestions(bot, campaign, text, "Notes", FakeAI())
        self.assertEqual(again, 0)  # (the same thread is never suggested twice)

    async def test_out_of_time_out_of_funds_and_a_cut_answer_never_fail_the_upload(self) -> None:
        import asyncio
        import time
        from types import SimpleNamespace

        from dmbot.ai import AIOutOfFunds, Reply
        from dmbot.ui.name_background import store_thread_suggestions, thread_suggestions_line

        campaign = await self.campaigns.get(GUILD_A, self.c)
        assert campaign is not None
        bot = SimpleNamespace(memory=self.memory)

        class Slow:
            async def complete(self, *_: object, **__: object) -> Reply:
                await asyncio.sleep(5)
                return Reply('{"threads": []}', False)

        class Broke:
            async def complete(self, *_: object, **__: object) -> Reply:
                raise AIOutOfFunds("no credits")

        class Cut:
            async def complete(self, *_: object, **__: object) -> Reply:
                return Reply('{"threads": [{"shape": "ask"', True)

        late = time.monotonic() + 1  # (less than the step's least time: not even started)
        got = await store_thread_suggestions(bot, campaign, "x", "", Slow(), until=late)
        self.assertEqual(got, (0, "time"))
        broke = await store_thread_suggestions(bot, campaign, "x", "", Broke())
        self.assertEqual(broke, (0, "funds"))  # (only this step ends: the upload is done)
        cut = await store_thread_suggestions(bot, campaign, "x", "", Cut())
        self.assertEqual(cut, (0, "stopped"))  # (said as stopped early, never "none found")
        self.assertIn("stopped early", thread_suggestions_line(0, "stopped"))
        self.assertEqual(thread_suggestions_line(0, ""), "")
        self.assertIn("2 story threads to check", thread_suggestions_line(2, ""))
