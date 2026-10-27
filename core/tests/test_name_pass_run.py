"""PlotBot's name pass on a finished session (#2628) against a real database: a misspelling of a
list name is fixed in the cleaned text only, with a note and Undo; anything unsure changes
nothing; a secret name is never asked about; another campaign is untouched; the session is marked
done whatever happens; and a recap's first step waits for it. Invented names."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

from dmbot.campaigns.store import CampaignStore
from dmbot.db import Database, drop_schema
from dmbot.memory.lookup import LookupCache
from dmbot.memory.models import KEEP
from dmbot.schema import MIGRATIONS
from dmbot.transcript.models import Line
from dmbot.transcript.store import TranscriptStore
from dmbot.ui import name_pass_undo
from dmbot.ui import names as ui
from tests.pg import TEST_URL, DatabaseTest
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
        self.assertEqual(
            text,
            "**Names fixed this session**\n"
            "Kethrandil: corrected 3 times\n"
            "  (3) Keirandal\n"
            "Not sure (asked the DM): 0\n"
            "New names (asked the DM): 0\n"
            "Left as heard (secret-name guard): 0",
        )
        self.assertIsInstance(view.children[0], name_pass_undo.UndoNamePassButton)
        self.assertEqual(view.children[0].item.label, "Undo Kethrandil")

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

    async def test_the_dm_is_told_what_was_not_sure_and_what_was_new(self) -> None:
        self.ai.complete.return_value = reply({"n": 1, "answer": "unsure", "name": ""})
        await self.run_pass()
        text = self.bot.post.await_args.args[1]  # type: ignore[attr-defined]
        self.assertIn("Not sure (asked the DM): 1\n  (3) Keirandal → Kethrandil?", text)
        self.ai.complete.return_value = reply({"n": 1, "answer": "new", "name": ""})
        await self.run_pass()
        self.assertIn("New names (asked the DM): 1", self.bot.post.await_args.args[1])  # type: ignore[attr-defined]

    async def test_a_word_plotbot_was_not_sure_of_becomes_a_name_question_even_heard_once(
        self,
    ) -> None:
        self.ai.complete.return_value = reply({"n": 1, "answer": "unsure", "name": ""})
        await self.run_pass()
        self.assertEqual(self.bot._name_pass_asks[self.session], [("Keirandal", 3)])
        await self.bot._propose_new_names(
            GUILD, self.campaign.id, ["Keirandal rode on."], {PLAYER}, self.session
        )
        names = [e.name for e in await self.memory.entities(GUILD, self.campaign.id)]
        self.assertIn("Keirandal", names)  # offered to the DM: the existing hold waits for it
        self.assertNotIn(self.session, self.bot._name_pass_asks)  # asked once

    async def test_a_new_name_plotbot_found_is_offered_even_heard_once_and_the_count_matches(
        self,
    ) -> None:
        """#2628 review: the report says "New names (asked the DM)" only for what is offered."""
        self.ai.complete.return_value = reply({"n": 1, "answer": "new", "name": ""})
        await self.run_pass()
        self.assertEqual(self.bot._name_pass_asks[self.session], [("Keirandal", 3)])
        self.assertIn("New names (asked the DM): 1", self.bot.post.await_args.args[1])  # type: ignore[attr-defined]
        await self.bot._propose_new_names(
            GUILD, self.campaign.id, ["Nothing else here."], {PLAYER}, self.session
        )
        names = [e.name for e in await self.memory.entities(GUILD, self.campaign.id)]
        self.assertIn("Keirandal", names)  # a word heard once is a name to check

    async def test_a_word_the_campaign_already_holds_is_not_counted_as_asked(self) -> None:
        await self.memory.add_entity(
            GUILD, self.campaign.id, type="concept", name="Keirandal", source="scan"
        )  # (already a suggestion: the scan would skip it, so the report must not count it)
        self.ai.complete.return_value = reply({"n": 1, "answer": "new", "name": ""})
        await self.run_pass()
        self.assertNotIn(self.session, self.bot._name_pass_asks)
        posted = self.bot.post.await_args  # type: ignore[attr-defined]
        if posted is not None:
            self.assertNotIn("New names (asked the DM): 1", posted.args[1])

    async def test_two_list_names_that_sound_alike_are_never_chosen_between(self) -> None:
        """#2628 review: with Kethrandel on the list too, "Keirandal" is not fixed to either,
        whatever PlotBot answers; the text is unchanged and the word is a name question."""
        await ui.save_name(self.memory, self.campaign, "Kethrandel", "npc", [], [])
        self.ai.complete.return_value = reply({"n": 1, "answer": "is", "name": "Kethrandil"})
        before = await self.texts()
        self.assertEqual(await self.run_pass(), 0)
        self.assertEqual(await self.texts(), before)
        self.assertEqual(self.bot._name_pass_asks[self.session], [("Keirandal", 3)])
        self.assertIn("Not sure (asked the DM): 1", self.bot.post.await_args.args[1])  # type: ignore[attr-defined]

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
        it.message = SimpleNamespace(content="Fixed.", id=1, edit=AsyncMock(), components=[])
        await button.callback(it)
        self.assertEqual(await self.texts(), before)
        self.assertIn("Put the spellings back in 3 lines", it.response.sent[0][0])
        self.bot.lookup.mark_all_stale()  # type: ignore[union-attr]
        lookup = await self.bot.lookup.get(GUILD, self.campaign.id)  # type: ignore[union-attr]
        self.assertTrue(lookup.keep_as_heard("Keirandal"))
        self.assertEqual(KEEP, "keep")

    async def test_the_report_is_kept_for_the_website_and_an_undo_marks_it(self) -> None:
        from dmbot.transcript.name_pass_log import NamePassLog

        log = NamePassLog.of(self.transcripts)
        self.assertIsNone(await log.report(GUILD, self.campaign.id, self.session))
        self.ai.complete.return_value = reply({"n": 1, "answer": "is", "name": "Kethrandil"})
        await self.run_pass()
        kept = await log.report(GUILD, self.campaign.id, self.session)
        assert kept is not None
        self.assertEqual((kept.unsure, kept.new_names, kept.guarded, kept.cut), (0, 0, 0, False))
        (fix,) = kept.fixes
        self.assertEqual((fix.name, fix.times, fix.undone_at), ("Kethrandil", 3, None))
        button = self.bot.post.await_args.args[2].children[0]  # type: ignore[attr-defined]
        it = self.it()
        it.message = SimpleNamespace(content="Fixed.", id=1, edit=AsyncMock(), components=[])
        await button.callback(it)
        after = await log.report(GUILD, self.campaign.id, self.session)
        assert after is not None
        self.assertIsNotNone(after.fixes[0].undone_at)
        # and another campaign never reads it
        other = (await self.campaigns.create(GUILD, "Other", DM)).id
        self.assertIsNone(await log.report(GUILD, other, self.session))

    async def test_the_counts_of_what_was_not_sure_are_kept_too(self) -> None:
        from dmbot.transcript.name_pass_log import NamePassLog

        self.ai.complete.return_value = reply({"n": 1, "answer": "unsure", "name": ""})
        await self.run_pass()
        kept = await NamePassLog.of(self.transcripts).report(GUILD, self.campaign.id, self.session)
        assert kept is not None
        self.assertEqual((kept.unsure, kept.fixes), (1, ()))

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
        await store.enqueue(GUILD, self.campaign.id, START + 99_999, dm_user_id=DM)
        gone = await store.get(GUILD, self.campaign.id, START + 99_999)
        assert gone is not None
        self.assertTrue(await self.bot.recap_names_ready(GUILD, self.campaign.id, gone))

    async def test_a_session_whose_scan_ends_early_is_still_marked_done(self) -> None:
        """#2628 review: no lines were heard, so the scan returns early; the recap must not wait."""
        table = SimpleNamespace(
            guild_id=GUILD,
            campaign_id=self.campaign.id,
            heard=[],
            transcript_session_id=self.session,
        )
        self.assertFalse(await self.done())
        await self.bot.suggest_names(table)  # type: ignore[arg-type]
        self.assertTrue(await self.done())
        before = (await self.transcripts.lines(GUILD, self.session))[0].text
        await self.transcripts.mark_names_repaired(GUILD, self.session, 5)  # a second mark
        self.assertEqual((await self.transcripts.lines(GUILD, self.session))[0].text, before)

    async def run_tool(self, *, dry_run: bool) -> Any:
        from dmbot.transcript import name_pass_run

        self.bot.lookup.mark_all_stale()  # type: ignore[union-attr]
        lookup = await self.bot.lookup.get(GUILD, self.campaign.id)  # type: ignore[union-attr]

        async def ask(_system: str, _question: str) -> str:
            return str(
                reply(
                    {"n": 1, "answer": "is", "name": "Kethrandil"},
                    {"n": 2, "answer": "is", "name": "Kethrandil"},
                ).text
            )

        return await name_pass_run.run(
            self.transcripts, lookup, ask, GUILD, self.campaign.id, self.session,
            people=(), agreed={PLAYER}, now=START, forget_before=0, dry_run=dry_run,
        )  # fmt: skip

    async def test_the_tool_groups_the_spellings_and_a_dry_run_writes_nothing(self) -> None:
        await self.transcripts.add_lines(
            GUILD,
            self.session,
            [Line(MS + 50_000, PLAYER, "Then Keirandel left.", "Then Keirandel left.")],
        )
        before = await self.texts()
        dry = await self.run_tool(dry_run=True)
        self.assertEqual(await self.texts(), before)  # nothing written
        (fixed,) = dry.fixed
        self.assertEqual((fixed.name, fixed.times), ("Kethrandil", 4))
        self.assertEqual(fixed.forms, (("Keirandal", 3), ("Keirandel", 1)))
        real = await self.run_tool(dry_run=False)
        self.assertEqual(real.fixed[0].forms, fixed.forms)  # the same numbers as the real run
        self.assertNotIn("Keirandal", " ".join(await self.texts()))
        from dmbot.devtools.name_pass.__main__ import output, parse_args

        lines = output(dry, dry_run=True)
        self.assertTrue(lines[0].startswith("DRY RUN: nothing was written."))
        self.assertIn("Kethrandil: corrected 4 times\n  (3) Keirandal\n  (1) Keirandel", lines[0])
        self.assertEqual(lines[-1], "Counts: 1 names, 4 fixes, 0 unsure, 0 new, 0 guarded")
        args = parse_args(["--guild", "1", "--session", "abc", "--dry-run"])
        self.assertEqual(
            (args.guild, args.session, args.dry_run, args.yes), (1, "abc", True, False)
        )

    async def test_the_tool_and_the_live_pass_count_unsure_and_new_alike(self) -> None:
        """#2655: `run` leaves out a word the campaign knows or a person at the table, as the live
        pass does, so dev1's tool prints the numbers a live session would."""
        from dmbot.memory.models import name_key
        from dmbot.transcript import name_pass_run

        self.bot.lookup.mark_all_stale()  # type: ignore[union-attr]
        lookup = await self.bot.lookup.get(GUILD, self.campaign.id)  # type: ignore[union-attr]

        async def new_word(_system: str, _question: str) -> str:
            return str(reply({"n": 1, "answer": "new", "name": ""}).text)

        async def count(**kw: Any) -> tuple[int, int]:
            report = await name_pass_run.run(
                self.transcripts, lookup, new_word, GUILD, self.campaign.id, self.session,
                agreed={PLAYER}, now=START, forget_before=0, dry_run=True, **kw,
            )  # fmt: skip
            return len(report.unsure), len(report.new)

        self.assertEqual(await count(), (0, 1))
        self.assertEqual(await count(known={name_key("Keirandal")}), (0, 0))
        self.assertEqual(await count(people={name_key("Keirandal")}), (0, 0))
        # and the live pass over the same session says the same
        await self.memory.add_entity(
            GUILD, self.campaign.id, type="concept", name="Keirandal", source="scan"
        )
        await self.run_pass()
        posted = self.bot.post.await_args  # type: ignore[attr-defined]
        if posted is not None:
            self.assertNotIn("New names (asked the DM): 1", posted.args[1])

    async def test_a_line_of_a_person_who_stopped_agreeing_is_not_written_and_undo_is_exact(
        self,
    ) -> None:
        """#2655: the pass writes only the lines it read."""
        gone = "Keirandal was not at this table."
        await self.transcripts.add_lines(GUILD, self.session, [Line(MS + 80_000, 999, gone, gone)])
        before = await self.texts()
        self.ai.complete.return_value = reply({"n": 1, "answer": "is", "name": "Kethrandil"})
        self.assertEqual(await self.run_pass(), 3)  # the three lines of the person who agrees
        after = await self.texts()
        self.assertEqual(after[-1], gone)  # untouched
        self.assertNotIn("Keirandal", " ".join(after[:3]))
        from dmbot.transcript import name_pass as np

        batch = np.batch_of(self.session, await self._entity())
        await self.transcripts.undo_respell(GUILD, self.campaign.id, batch)
        self.assertEqual(await self.texts(), before)

    async def test_an_imported_sessions_off_topic_line_is_not_written_either(self) -> None:
        off = "Keirandal pass the chips."
        await self.transcripts.add_lines(
            GUILD, self.session, [Line(MS + 70_000, PLAYER, off, off, topic="off_topic")]
        )
        self.ai.complete.return_value = reply({"n": 1, "answer": "is", "name": "Kethrandil"})
        self.bot.lookup.mark_all_stale()  # type: ignore[union-attr]
        await self.bot.repair_names(
            GUILD, self.campaign.id, self.session, {PLAYER}, {PLAYER}, game_only=True
        )
        self.assertEqual((await self.texts())[-1], off)

    async def test_a_line_with_two_spellings_of_one_name_gets_both_fixed_and_undone(self) -> None:
        """#2649: the second spelling shares the first's batch, so a line already changed for it
        must still be a candidate; Undo puts the later change back first."""
        both = "Keirandal and Keirandel rode out."
        await self.transcripts.add_lines(
            GUILD, self.session, [Line(MS + 60_000, PLAYER, both, both)]
        )
        before = await self.texts()
        real = await self.run_tool(dry_run=False)
        self.assertEqual((await self.texts())[-1], "Kethrandil and Kethrandil rode out.")
        self.assertEqual(real.fixed[0].times, 5)
        from dmbot.transcript import name_pass as np

        batch = np.batch_of(self.session, real_entity := (await self._entity()))
        self.assertTrue(real_entity)
        await self.transcripts.undo_respell(GUILD, self.campaign.id, batch)
        self.assertEqual(await self.texts(), before)

    async def test_an_imported_sessions_off_topic_lines_are_not_read(self) -> None:
        """#2649: with game_only the pass reads what the filter kept as game talk, as the import's
        name scan does; a live session reads every spoken line."""
        from dmbot.transcript import name_pass_run

        off = "Keirandal pass the chips."
        await self.transcripts.add_lines(
            GUILD,
            self.session,
            [Line(MS + 70_000, PLAYER, off, off, topic="off_topic")],
        )
        live = await name_pass_run.pass_lines(self.transcripts, GUILD, self.session, {PLAYER})
        imported = await name_pass_run.pass_lines(
            self.transcripts, GUILD, self.session, {PLAYER}, True
        )
        self.assertIn(off, live)
        self.assertNotIn(off, imported)
        self.assertEqual(len(live), len(imported) + 1)
        self.assertEqual(
            await name_pass_run.pass_lines(self.transcripts, GUILD, self.session, {999}), []
        )  # (a person who stopped agreeing is not read)

    async def test_a_cut_answer_is_logged_by_count_and_marked_on_the_report(self) -> None:
        from dmbot.transcript import name_pass_run

        self.bot.lookup.mark_all_stale()  # type: ignore[union-attr]
        lookup = await self.bot.lookup.get(GUILD, self.campaign.id)  # type: ignore[union-attr]

        async def ask(_system: str, _question: str) -> Any:
            return SimpleNamespace(text="{", cut=True)

        with self.assertLogs("dmbot.transcript.name_pass_run", "WARNING") as logged:
            report = await name_pass_run.run(
                self.transcripts, lookup, ask, GUILD, self.campaign.id, self.session,
                people=(), agreed={PLAYER}, now=START, forget_before=0,
            )  # fmt: skip
        self.assertTrue(report.cut)
        self.assertEqual(report.fixed, ())
        self.assertIn("cut off", logged.output[0])
        self.assertNotIn("Keirandal", logged.output[0])  # counts only
        from dmbot.transcript import name_pass as np

        self.assertTrue(np.counts_line(report).endswith(", answer cut off"))
        self.assertIn(np.CUT_LINE, np.report_text(report))  # the DM is told why so many are unsure
        self.assertFalse(report.quiet)
        self.assertIn(np.CUT_LINE, np.import_text([report]))

    async def test_an_import_posts_one_report_for_all_its_sessions(self) -> None:
        from dmbot.transcript import name_pass as np

        first = np.Report((np.Fixed("Kethrandil", (("Keirandal", 3),), 11),), guarded=1)
        second = np.Report(
            (np.Fixed("Kethrandil", (("Keirandel", 1), ("Keirandal", 2)), 22),
             np.Fixed("Mirelle", (("Mirel", 1),), 33)),
        )  # fmt: skip
        text = np.import_text([first, second])
        self.assertEqual(
            text,
            "**Names fixed in your imported sessions** (2 had something)\n"
            "Kethrandil: corrected 6 times in 2 sessions\n"
            "  (5) Keirandal\n"
            "  (1) Keirandel\n"
            "Mirelle: corrected 1 time\n"
            "  (1) Mirel\n"
            "Not sure (asked the DM): 0\n"
            "New names (asked the DM): 0\n"
            "Left as heard (secret-name guard): 1",
        )
        views = name_pass_undo.undo_views_import(self.campaign.id, [first.fixed, second.fixed])
        self.assertEqual(
            [cast(Any, b).item.label for b in views[0].children],
            ["Undo Kethrandil #1", "Undo Kethrandil #2", "Undo Mirelle"],
        )

    async def test_every_fix_of_a_big_import_keeps_its_undo(self) -> None:
        """#2653: a message holds 25 buttons, so 60 fixes are three views, none lost."""
        from dmbot.transcript import name_pass as np

        reports = [
            [np.Fixed(f"Name{n}", (("Heard", 1),), n * 100 + k) for n in range(3)]
            for k in range(20)
        ]
        views = name_pass_undo.undo_views_import(self.campaign.id, reports)
        self.assertEqual([len(v.children) for v in views], [25, 25, 10])
        batches = {cast(Any, b).item.custom_id for v in views for b in v.children}
        self.assertEqual(len(batches), 60)

    async def test_an_import_holds_its_reports_and_posts_once_at_the_end(self) -> None:
        """#2653: nothing is posted after the first imported session, one report after the last
        (the quiet one left out); a live session's report still posts straight away."""
        from dmbot.bot import DMBot

        await ui.save_name(self.memory, self.campaign, "Drenwick", "npc", [], [])
        self.ai.complete.side_effect = [
            reply({"n": 1, "answer": "is", "name": "Kethrandil"}),
            reply({"n": 1, "answer": "is", "name": "Drenwick"}),
            reply({"n": 1, "answer": "is", "name": "Drenwick"}),  # the live session below
        ]
        second = await self.transcripts.open_session(GUILD, self.campaign.id, START + 1000)
        await self.transcripts.end_session(GUILD, second, START + 1100)
        text = "We rode with Drenwik."
        await self.transcripts.add_lines(
            GUILD, second, [Line(MS + 2_000_000 + i, PLAYER, text, text) for i in range(3)]
        )
        quiet = await self.transcripts.open_session(GUILD, self.campaign.id, START + 2000)
        await self.transcripts.end_session(GUILD, quiet, START + 2100)
        await self.transcripts.add_lines(
            GUILD, quiet, [Line(MS + 3_000_000, PLAYER, "Nothing here.", "Nothing here.")]
        )
        held = self.bot._import_reports.setdefault((GUILD, self.campaign.id), [])
        store = SimpleNamespace(pending_scans=AsyncMock(side_effect=[1, 1, 0]))
        tell = DMBot._tell_import_news.__get__(self.bot)
        for sid in (self.session, quiet, second):
            self.bot.lookup.mark_all_stale()  # type: ignore[union-attr]
            await self.bot.repair_names(
                GUILD, self.campaign.id, sid, {PLAYER}, {PLAYER}, held, game_only=True
            )
            await tell(GUILD, store)
            if sid != second:
                self.bot.post.assert_not_awaited()  # type: ignore[attr-defined]
        self.assertEqual(len(held), 2)  # the quiet session was left out
        self.bot.post.assert_awaited_once()  # type: ignore[attr-defined]
        posted = self.bot.post.await_args.args  # type: ignore[attr-defined]
        from dmbot.transcript import name_pass as np

        self.assertEqual(posted[1], np.import_text(held))
        self.assertNotIn((GUILD, self.campaign.id), self.bot._import_reports)
        self.bot.post.reset_mock()  # type: ignore[attr-defined]
        live = await self.transcripts.open_session(GUILD, self.campaign.id, START + 4000)
        await self.transcripts.end_session(GUILD, live, START + 4100)
        await self.transcripts.add_lines(GUILD, live, [Line(MS + 4_000_000, PLAYER, text, text)])
        await self.bot.repair_names(GUILD, self.campaign.id, live, {PLAYER}, {PLAYER})
        self.bot.post.assert_awaited_once()  # type: ignore[attr-defined]

    async def _entity(self) -> str:
        lookup = await self.bot.lookup.get(GUILD, self.campaign.id)  # type: ignore[union-attr]
        return next(n.entity_id for n in lookup.names if n.text == "Kethrandil")


class Backfill(DatabaseTest):
    """Migration 0227 runs with no server set, so its backfill must open the table it updates
    (CLAUDE.md, "A migration that changes rows"): sessions from before it are marked done, so no
    old recap waits for a pass that will never run."""

    async def test_sessions_from_before_0227_are_marked_done_and_the_opening_is_gone(self) -> None:
        before = [m for m in MIGRATIONS if m[0] < "0227"]
        await self.db.close()  # start again from a schema as it was before 0227
        await drop_schema(TEST_URL, self.schema)
        self.db = await Database.open(TEST_URL, schema=self.schema, migrations=before)
        campaigns = CampaignStore(self.db)
        campaign = await campaigns.create(GUILD, "Old", DM)
        transcripts = TranscriptStore(self.db)
        old = await transcripts.open_session(GUILD, campaign.id, START)
        await transcripts.end_session(GUILD, old, START + 100)
        live = await transcripts.open_session(GUILD, campaign.id, START + 500)  # never ended
        await self.db.migrate()
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                "SELECT id, names_repaired_at FROM transcript_sessions ORDER BY started_at"
            )
            rows = {r["id"]: int(r["names_repaired_at"]) for r in await cur.fetchall()}
        self.assertEqual(set(rows), {old, live})
        self.assertTrue(all(at > 0 for at in rows.values()), rows)
        self.assertTrue(await transcripts.names_repaired(GUILD, campaign.id, START))
        self.assertTrue(await transcripts.names_repaired(GUILD, campaign.id, START + 500))
        async with self.db.unscoped() as conn:  # the opening is gone again
            cur = await conn.execute(
                "SELECT count(*) AS n FROM pg_policies WHERE policyname = 'migrate_backfill'"
            )
            self.assertEqual((await cur.fetchone() or {})["n"], 0)
        # a session made after the migration starts undone, as it should
        fresh = await transcripts.open_session(GUILD, campaign.id, START + 900)
        self.assertEqual(fresh is not None, True)
        self.assertFalse(await transcripts.names_repaired(GUILD, campaign.id, START + 900))
