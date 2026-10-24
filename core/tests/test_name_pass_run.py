"""PlotBot's name pass on a finished session (#2628) against a real database: a misspelling of a
list name is fixed in the cleaned text only, with a note and Undo; anything unsure changes
nothing; a secret name is never asked about; another campaign is untouched; the session is marked
done whatever happens; and a recap's first step waits for it. Invented names."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

from dmbot.memory.lookup import LookupCache
from dmbot.memory.models import KEEP
from dmbot.transcript.models import Line
from dmbot.transcript.store import TranscriptStore
from dmbot.ui import name_pass_undo
from dmbot.ui import names as ui
from tests.test_memory_names import DM, GUILD, NamesTest

PLAYER = 8
START = 1_700_000_000
MS = START * 1000
LINES = [
    "Keirandal drew his bow.",
    "Then Keirandal said no to the king.",
    "We followed Keirandal home.",
    "Nothing to see here.",
]


def reply(*items: dict[str, Any]) -> Any:
    return SimpleNamespace(text=json.dumps({"answers": list(items)}))


class ThePass(NamesTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.bot.lookup = LookupCache(self.memory)
        self.transcripts = TranscriptStore(self.db)
        self.bot.transcripts = self.transcripts
        self.bot.post = AsyncMock(return_value=True)  # type: ignore[method-assign]
        self.ai = SimpleNamespace(complete=AsyncMock(return_value=reply()))
        self.bot.name_pass_ai = self.ai  # type: ignore[assignment]
        await self.campaigns.set_dm_screen(GUILD, self.campaign.id, 3)
        await ui.save_name(self.memory, self.campaign, "Kethrandil", "player_character", [], [])
        self.session = await self.transcripts.open_session(GUILD, self.campaign.id, START)
        await self.transcripts.end_session(GUILD, self.session, START + 100)
        await self.transcripts.add_lines(
            GUILD,
            self.session,
            [Line(MS + 1000 * (i + 1), PLAYER, t, t) for i, t in enumerate(LINES)],
        )

    async def texts(self) -> list[str]:
        return [x.text for x in await self.transcripts.lines(GUILD, self.session)]

    async def heard(self) -> list[str]:
        return [x.heard for x in await self.transcripts.lines(GUILD, self.session)]

    async def run_pass(self) -> int:
        self.bot.lookup.mark_all_stale()  # type: ignore[union-attr]
        return await self.bot.repair_names(
            GUILD, self.campaign.id, self.session, {PLAYER}, {PLAYER}
        )

    async def done(self) -> bool:
        return await self.transcripts.names_repaired(GUILD, self.campaign.id, START)

    async def test_a_session_is_not_done_until_the_pass_has_run(self) -> None:
        self.assertFalse(await self.done())

    async def test_a_sure_answer_fixes_the_cleaned_text_only_and_says_so_with_undo(self) -> None:
        self.ai.complete.return_value = reply({"n": 1, "answer": "is", "name": "Kethrandil"})
        heard = await self.heard()
        self.assertEqual(await self.run_pass(), 3)
        self.assertEqual(
            (await self.texts())[:3],
            [
                "Kethrandil drew his bow.",
                "Then Kethrandil said no to the king.",
                "We followed Kethrandil home.",
            ],
        )
        self.assertEqual(await self.heard(), heard)  # what was heard never changes (#933)
        self.assertTrue(await self.done())
        text, view = self.bot.post.await_args.args[1], self.bot.post.await_args.args[2]  # type: ignore[attr-defined]
        self.assertEqual(text, "Fixed 3 spellings of Kethrandil (heard as Keirandal).")
        self.assertIsInstance(view.children[0], name_pass_undo.UndoNamePassButton)

    async def test_the_prompt_has_the_word_a_short_line_and_the_near_list_names_only(self) -> None:
        await ui.save_name(self.memory, self.campaign, "Drenwick", "npc", [], [])
        await self.run_pass()
        _system, question = self.ai.complete.await_args.args[:2]
        self.assertIn("Keirandal", question)
        self.assertIn("Kethrandil", question)
        self.assertNotIn("Drenwick", question)  # not near: not sent
        self.assertNotIn("Nothing to see here", question)  # no line without the word

    async def test_new_unsure_or_unlisted_answers_change_nothing_but_the_session_is_done(
        self,
    ) -> None:
        before = await self.texts()
        for item in (
            {"n": 1, "answer": "new", "name": ""},
            {"n": 1, "answer": "unsure", "name": "Kethrandil"},
            {"n": 1, "answer": "is", "name": "Somebody Else"},
        ):
            self.ai.complete.return_value = reply(item)
            self.assertEqual(await self.run_pass(), 0)
            self.assertEqual(await self.texts(), before)
        self.assertTrue(await self.done())
        self.bot.post.assert_not_awaited()  # type: ignore[attr-defined]

    async def test_a_failed_call_changes_nothing_and_does_not_hold_the_recap_back(self) -> None:
        self.ai.complete.side_effect = RuntimeError("the service is down")
        before = await self.texts()
        with self.assertLogs("dmbot.bot", "ERROR"):
            self.assertEqual(await self.run_pass(), 0)
        self.assertEqual(await self.texts(), before)
        self.assertTrue(await self.done())

    async def test_with_no_ai_the_pass_does_nothing_and_still_finishes(self) -> None:
        self.bot.name_pass_ai = None
        self.assertEqual(await self.run_pass(), 0)
        self.assertTrue(await self.done())

    async def test_a_secret_name_is_never_asked_about_or_written(self) -> None:
        await ui.save_name(self.memory, self.campaign, "Brother Halvard", "npc", [], ["Silas Vane"])
        # (a spoken "Silas Vain" near the secret: the ask must not happen at all)
        lines = ["Silas Vain waved.", "Silas Vain left.", "Then Silas Vain sat."]
        await self.transcripts.add_lines(
            GUILD,
            self.session,
            [Line(MS + 100_000 + i, PLAYER, t, t) for i, t in enumerate(lines)],
        )
        self.ai.complete.return_value = reply()
        await self.run_pass()
        question = self.ai.complete.await_args.args[1]
        self.assertNotIn("Silas", question)

    async def test_another_campaigns_lines_are_not_touched(self) -> None:
        other = await self.campaigns.create(GUILD, "Elsewhere", DM)
        away = await self.transcripts.open_session(GUILD, other.id, START + 500)
        await self.transcripts.end_session(GUILD, away, START + 600)
        await self.transcripts.add_lines(
            GUILD, away, [Line(MS + 501_000, PLAYER, LINES[0], LINES[0])]
        )
        self.ai.complete.return_value = reply({"n": 1, "answer": "is", "name": "Kethrandil"})
        await self.run_pass()
        got = await self.transcripts.lines(GUILD, away)
        self.assertEqual(got[0].text, LINES[0])

    async def test_undo_puts_the_spellings_back_and_keeps_them_as_heard(self) -> None:
        self.ai.complete.return_value = reply({"n": 1, "answer": "is", "name": "Kethrandil"})
        before = await self.texts()
        await self.run_pass()
        view = self.bot.post.await_args.args[2]  # type: ignore[attr-defined]
        button = view.children[0]
        it = self.it()
        it.message = SimpleNamespace(content="Fixed 3 spellings.", id=1)
        it.followup = SimpleNamespace(send=AsyncMock())
        await button.callback(it)
        self.assertEqual(await self.texts(), before)
        self.assertEqual(it.response.edited[0][1], None)  # the button is gone
        self.assertIn("Put the spellings back in 3 lines", it.followup.send.await_args.args[0])
        self.bot.lookup.mark_all_stale()  # type: ignore[union-attr]
        lookup = await self.bot.lookup.get(GUILD, self.campaign.id)  # type: ignore[union-attr]
        self.assertTrue(lookup.keep_as_heard("Keirandal"))
        self.assertEqual(KEEP, "keep")

    async def test_only_a_dm_may_press_undo(self) -> None:
        self.ai.complete.return_value = reply({"n": 1, "answer": "is", "name": "Kethrandil"})
        await self.run_pass()
        button = self.bot.post.await_args.args[2].children[0]  # type: ignore[attr-defined]
        changed = await self.texts()
        it = self.it(user_id=PLAYER)
        await button.callback(it)
        self.assertEqual(await self.texts(), changed)
        self.assertEqual(it.response.sent[0][0], name_pass_undo.NOT_YOURS)

    async def test_an_imported_session_gets_the_pass_before_its_name_scan(self) -> None:
        from dmbot.transcript.import_job import ImportStore, Ready

        self.bot.import_job = SimpleNamespace(store=ImportStore(self.db))  # type: ignore[assignment]
        self.bot.consent = SimpleNamespace(consenting=AsyncMock(return_value={PLAYER}))  # type: ignore[assignment]
        self.ai.complete.return_value = reply({"n": 1, "answer": "is", "name": "Kethrandil"})
        self.bot.lookup.mark_all_stale()  # type: ignore[union-attr]
        ready = Ready(self.campaign.id, self.session, START, False, "imp")
        await self.bot._scan_import(GUILD, ready)
        self.assertEqual((await self.texts())[0], "Kethrandil drew his bow.")
        self.assertTrue(await self.done())
        proposed = [
            e.name
            for e in await self.memory.entities(GUILD, self.campaign.id)
            if "Keirandal" in e.name
        ]
        self.assertEqual(proposed, [])  # the repaired spelling is not offered as a new name

    async def test_the_recap_job_waits_for_the_pass_and_then_goes_on(self) -> None:
        from dmbot.recaps.store import RecapStore

        store = RecapStore(self.db)
        self.bot.recaps = store
        await store.enqueue(GUILD, self.campaign.id, START, dm_user_id=DM)
        job = await store.get(GUILD, self.campaign.id, START)
        assert job is not None
        self.assertFalse(await self.bot.recap_names_ready(GUILD, self.campaign.id, job))
        await self.run_pass()
        self.assertTrue(await self.bot.recap_names_ready(GUILD, self.campaign.id, job))
        # a recap of a session with no transcript has nothing to wait for
        gone = await store.get(GUILD, self.campaign.id, START)
        assert gone is not None
        self.assertTrue(await self.bot.recap_names_ready(GUILD, self.campaign.id, gone))
