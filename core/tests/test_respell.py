"""A fixed spelling carries through the cleaned transcripts of past sessions (#2170): the rules
(pure), the table (a real Postgres), the card's Fix spelling and its Undo. Invented names and
numbers; no real speech."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from dmbot.transcript.models import Line
from dmbot.transcript.respell import contains, done_note, put_back, respell, undone_note
from dmbot.transcript.store import TranscriptStore
from dmbot.ui import name_card
from dmbot.ui import names as ui
from tests.test_memory_names import DM, GUILD, NamesTest

PLAYER = 8
START = 1_700_000_000
MS = START * 1000


class TheRules(unittest.TestCase):
    def test_whole_names_in_any_case_become_the_new_spelling(self) -> None:
        got = respell("Branok's axe, branok  said hi", "Branok", "Brannoch")
        assert got is not None
        self.assertEqual(got.text, "Brannoch's axe, Brannoch  said hi")
        self.assertEqual(got.offsets, (0, 16))
        self.assertEqual(got.was, ("Branok", "branok"))

    def test_part_of_a_longer_word_or_longer_name_is_left_alone(self) -> None:
        self.assertIsNone(respell("Branoki and Sbranok", "Branok", "Brannoch"))
        got = respell("Branok Hall, then Branok", "Branok", "Brannoch", ["Branok Hall"])
        assert got is not None
        self.assertEqual(got.text, "Branok Hall, then Brannoch")

    def test_a_name_in_two_words_matches_across_any_spaces(self) -> None:
        got = respell("Old  Branok came", "Old Branok", "Old Brannoch")
        assert got is not None
        self.assertEqual(got.text, "Old Brannoch came")

    def test_nothing_to_change_is_none(self) -> None:
        self.assertIsNone(respell("nothing here", "Branok", "Brannoch"))
        self.assertIsNone(respell("Brannoch is here", "Branok", "Brannoch"))

    def test_put_back_restores_the_text_as_it_was(self) -> None:
        text = "Branok's axe, branok  said hi. Branok Hall."
        got = respell(text, "Branok", "Brannoch", ["Branok Hall"])
        assert got is not None
        self.assertEqual(put_back(got.text, got.offsets, got.was, "Brannoch"), text)

    def test_a_line_changed_since_is_not_put_back(self) -> None:
        got = respell("Branok waits", "Branok", "Brannoch")
        assert got is not None
        self.assertIsNone(put_back("Someone waits", got.offsets, got.was, "Brannoch"))

    def test_curly_apostrophes_and_dashes_match_their_look_alikes(self) -> None:
        got = respell("Ka\u2019zeth and Ka\u2013Zeth", "Ka'zeth", "Kazeth")
        assert got is not None
        self.assertEqual(got.text, "Kazeth and Ka\u2013Zeth")
        self.assertTrue(contains("Ka-Zeth", "Ka\u2010Zeth"))

    def test_a_line_the_new_spelling_would_complete_a_hidden_name_in_is_left_alone(self) -> None:
        self.assertIsNone(
            respell("Branok Hall burns", "Branok", "Brannoch", hidden=["Brannoch Hall"])
        )
        got = respell("Branok Hall burns", "Branok", "Brannoch", hidden=["Brannoch Keep"])
        assert got is not None  # another hidden name is not completed
        already = respell(
            "Brannoch Hall and Branok", "Branok", "Brannoch", hidden=["Brannoch Hall"]
        )
        assert already is not None  # the line said it before: nothing new is revealed

    def test_the_notes_are_plain_and_count(self) -> None:
        self.assertEqual(
            done_note("Branok", "Brannoch", 12, 3),
            "Changed Branok to Brannoch in 12 lines of 3 past sessions.",
        )
        self.assertEqual(
            done_note("Branok", "Brannoch", 1, 1),
            "Changed Branok to Brannoch in 1 line of 1 past session.",
        )
        self.assertEqual(
            undone_note("Branok", 2, 1), "Put Branok back in 2 lines of 1 past session."
        )


class ThePastTranscripts(NamesTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        from dmbot.memory.lookup import LookupCache

        self.bot.lookup = LookupCache(self.memory)
        self.transcripts = TranscriptStore(self.db)
        self.bot.transcripts = self.transcripts
        self.branok = await ui.save_name(self.memory, self.campaign, "Branok", "npc", [], [])
        await ui.save_name(self.memory, self.campaign, "Branok Hall", "place", [], [])
        self.session = await self.transcripts.open_session(GUILD, self.campaign.id, START)
        await self.transcripts.end_session(GUILD, self.session, START + 100)  # a past session
        await self.transcripts.add_lines(
            GUILD,
            self.session,
            [
                Line(MS + 1000, PLAYER, "branok waits at the gate", "Branok waits at the gate"),
                Line(MS + 2000, PLAYER, "branok hall burns", "Branok Hall burns"),
                Line(MS + 3000, PLAYER, "branok's axe and Branok", "Branok's axe and Branok"),
                Line(MS + 4000, PLAYER, "nothing here", "nothing here"),
                Line(MS + 5000, PLAYER, "Branok typed", "Branok typed", typed=True),
            ],
        )

    async def texts(self) -> list[str]:
        return [x.text for x in await self.transcripts.lines(GUILD, self.session)]

    async def heard(self) -> list[str]:
        return [x.heard for x in await self.transcripts.lines(GUILD, self.session)]

    async def fix(self, new: str = "Brannoch") -> Any:
        self.bot.lookup.mark_all_stale()  # type: ignore[union-attr]
        form = name_card.FixSpellingForm(self.campaign.id, self.branok.id, "Branok")
        form.name = SimpleNamespace(value=new)  # type: ignore[assignment]
        it = self.it()
        await form.on_submit(it)
        return it

    async def test_fixing_a_spelling_changes_the_cleaned_lines_and_tells_the_dm(self) -> None:
        heard = await self.heard()
        it = await self.fix()
        self.assertEqual(
            await self.texts(),
            [
                "Brannoch waits at the gate",
                "Branok Hall burns",  # a longer name: left as it was
                "Brannoch's axe and Brannoch",
                "nothing here",
                "Branok typed",  # typed lines are only in the as-heard transcript
            ],
        )
        self.assertEqual(await self.heard(), heard)  # what was heard never changes
        text = it.response.edited[0][0]
        self.assertIn("Changed Branok to Brannoch in 2 lines of 1 past session.", text)

    async def test_the_card_also_says_what_happened_to_the_recaps(self) -> None:
        from dmbot.recaps.store import RecapStore

        self.bot.recaps = RecapStore(self.db)
        await self.bot.recaps.enqueue(GUILD, self.campaign.id, 100, dm_user_id=DM)
        async with self.db.guild(GUILD) as conn:
            await conn.execute(
                "UPDATE recap_jobs SET state = 'ready', title = 'Branok', summary = 'Branok runs.'"
            )
        it = await self.fix()
        text = it.response.edited[0][0]
        self.assertIn("Also changed Branok to Brannoch in 1 recap that is waiting.", text)
        job = await self.bot.recaps.get(GUILD, self.campaign.id, 100)
        assert job is not None
        self.assertEqual(job.summary, "Brannoch runs.")

    async def test_one_undo_takes_back_the_name_and_the_lines(self) -> None:
        before = await self.texts()
        it = await self.fix()
        view = it.response.edited[0][1]
        (undo,) = [c for c in view.children if isinstance(c, name_card.UndoSpellingButton)]
        again = self.it()
        await undo.callback(again)
        self.assertEqual(await self.texts(), before)
        entity = await self.memory.entity(GUILD, self.campaign.id, self.branok.id)
        assert entity is not None
        self.assertEqual(entity.name, "Branok")
        self.assertIn("Put Branok back in 2 lines of 1 past session.", again.response.edited[0][0])

    async def test_only_a_dm_may_press_undo(self) -> None:
        it = await self.fix()
        view = it.response.edited[0][1]
        (undo,) = [c for c in view.children if isinstance(c, name_card.UndoSpellingButton)]
        other = self.it(99)
        await undo.callback(other)
        self.assertIn("Brannoch waits", (await self.texts())[0])

    async def test_a_line_changed_since_stays_when_undoing(self) -> None:
        it = await self.fix()
        await self.transcripts.relabel_line(GUILD, self.session, PLAYER, MS + 1000, "Someone waits")
        view = it.response.edited[0][1]
        (undo,) = [c for c in view.children if isinstance(c, name_card.UndoSpellingButton)]
        await undo.callback(self.it())
        texts = await self.texts()
        self.assertEqual(texts[0], "Someone waits")
        self.assertEqual(texts[2], "Branok's axe and Branok")

    async def test_another_campaigns_lines_are_not_touched(self) -> None:
        other = await self.campaigns.create(GUILD, "Sunken City", DM)
        elsewhere = await self.transcripts.open_session(GUILD, other.id, START + 10)
        await self.transcripts.end_session(GUILD, elsewhere, START + 110)
        await self.transcripts.add_lines(
            GUILD, elsewhere, [Line(MS + 9000, PLAYER, "branok sails", "Branok sails")]
        )
        await self.fix()
        (line,) = await self.transcripts.lines(GUILD, elsewhere)
        self.assertEqual(line.text, "Branok sails")

    async def test_a_name_that_clashes_is_not_kept_so_nothing_is_rewritten(self) -> None:
        rival = await ui.save_name(self.memory, self.campaign, "Branoch", "npc", [], [])
        await self.memory.add_alias(
            GUILD,
            self.campaign.id,
            rival.id,
            "Branok",
            kind="nickname",
            source="dm",
            status="confirmed",
        )  # another entry is also called that: the old spelling is not kept
        before = await self.texts()
        await self.fix("Brannoch")
        self.assertEqual(await self.texts(), before)

    async def test_a_hyphenated_longer_name_is_not_rewritten_inside(self) -> None:
        await ui.save_name(self.memory, self.campaign, "Branok-Keep", "place", [], [])
        await self.transcripts.add_lines(
            GUILD, self.session, [Line(MS + 6000, PLAYER, "x", "Branok-Keep and Branok")]
        )
        await self.fix()
        texts = await self.texts()
        self.assertIn("Branok-Keep and Brannoch", texts)

    async def test_two_lines_with_the_same_start_both_come_back(self) -> None:
        await self.transcripts.add_lines(
            GUILD,
            self.session,
            [
                Line(MS + 7000, PLAYER, "branok a", "Branok a"),
                Line(MS + 7000, PLAYER, "branok b", "Branok b"),
            ],
        )
        it = await self.fix()
        self.assertIn("Brannoch a", await self.texts())
        (undo,) = [
            c for c in it.response.edited[0][1].children
            if isinstance(c, name_card.UndoSpellingButton)
        ]  # fmt: skip
        await undo.callback(self.it())
        texts = await self.texts()
        self.assertIn("Branok a", texts)
        self.assertIn("Branok b", texts)

    async def test_a_failed_transcript_undo_can_be_pressed_again(self) -> None:
        it = await self.fix()
        (undo,) = [
            c for c in it.response.edited[0][1].children
            if isinstance(c, name_card.UndoSpellingButton)
        ]  # fmt: skip
        with (
            patch.object(self.transcripts, "undo_respell", side_effect=RuntimeError("database")),
            self.assertLogs("dmbot.ui.name_card", "ERROR"),
        ):
            await undo.callback(self.it())
        self.assertIn("Brannoch waits", (await self.texts())[0])  # the name is back, lines not
        retry = self.it()
        await undo.callback(retry)
        self.assertEqual((await self.texts())[0], "Branok waits at the gate")
        self.assertIn("Spelling put back", retry.response.edited[0][0])
        self.assertIn("Put Branok back", retry.response.edited[0][0])
        self.assertNotIn("changed again", retry.response.edited[0][0])

    async def test_an_entry_only_some_people_know_is_never_written_into_the_transcript(
        self,
    ) -> None:
        add = self.memory.add_entity
        cid = self.campaign.id
        clue = (
            await add(
                GUILD, cid, type="clue", name="Branok's deed", source="dm", status="confirmed"
            )
        ).value
        mira = (
            await add(GUILD, cid, type="character", name="Mira", source="dm", status="confirmed")
        ).value
        await self.memory.add_knower(GUILD, cid, clue.id, mira.id, source="dm", from_session_at=100)
        await self.memory.set_audience_limited(GUILD, cid, clue.id, True, source="dm")
        await self.transcripts.add_lines(
            GUILD, self.session, [Line(MS + 8000, PLAYER, "x", "Brannoch's deed and Branok's deed")]
        )
        before = await self.texts()
        form = name_card.FixSpellingForm(self.campaign.id, clue.id, "Branok's deed")
        form.name = SimpleNamespace(value="Brannoch's deed")  # type: ignore[assignment]
        it = self.it()
        await form.on_submit(it)
        self.assertEqual(await self.texts(), before)
        self.assertNotIn("Changed", it.response.edited[0][0])

    async def test_a_dm_only_entry_is_never_written_into_the_transcript(self) -> None:
        thread = await ui.save_name(self.memory, self.campaign, "Branok's quest", "thread", [], [])
        await self.transcripts.add_lines(
            GUILD,
            self.session,
            [Line(MS + 8000, PLAYER, "x", "Brannoch's quest and Branok's quest")],
        )
        self.branok = thread
        before = await self.texts()
        form = name_card.FixSpellingForm(self.campaign.id, thread.id, "Branok's quest")
        form.name = SimpleNamespace(value="Brannoch's quest")  # type: ignore[assignment]
        await form.on_submit(self.it())
        self.assertEqual(await self.texts(), before)

    async def test_a_line_the_fix_would_make_say_a_secret_name_is_left_as_it_was(self) -> None:
        keeper = await ui.save_name(self.memory, self.campaign, "Mirelle", "npc", [], [])
        await self.memory.add_alias(
            GUILD, self.campaign.id, keeper.id, "Brannoch Hall", kind="title", source="dm",
            status="confirmed", secret=True,
        )  # fmt: skip
        await self.fix()
        texts = await self.texts()
        self.assertEqual(texts[1], "Branok Hall burns")  # "Brannoch Hall" is a secret name
        self.assertEqual(texts[0], "Brannoch waits at the gate")

    async def test_the_session_being_played_is_not_touched(self) -> None:
        live = await self.transcripts.open_session(GUILD, self.campaign.id, START + 500)
        await self.transcripts.add_lines(
            GUILD, live, [Line(MS + 500_000, PLAYER, "branok now", "Branok now")]
        )
        await self.fix()
        (line,) = await self.transcripts.lines(GUILD, live)
        self.assertEqual(line.text, "Branok now")

    async def test_a_big_fix_is_finished_in_runs_each_with_its_own_undo(self) -> None:
        many = [Line(MS + 20_000 + i, PLAYER, "x", f"Branok {i}") for i in range(7)]
        await self.transcripts.add_lines(GUILD, self.session, many)
        with patch("dmbot.transcript.store.MAX_RESPELLED", 5):
            it = await self.fix()
            view = it.response.edited[0][1]
            self.assertIn("more lines may still change", it.response.edited[0][0])
            (finish,) = [
                c for c in view.children if isinstance(c, name_card.FinishPastButton)
            ]  # fmt: skip
            again = self.it()
            await finish.callback(again)
            self.assertIn("Changed Branok to Brannoch", again.response.edited[0][0])
            texts = await self.texts()
            self.assertTrue(all("Branok " not in t or "Hall" in t for t in texts[5:]))
            (undo_part,) = [
                c for c in again.response.edited[0][1].children
                if isinstance(c, name_card.UndoRunButton)
            ]  # fmt: skip
            await undo_part.callback(self.it())
            self.assertIn("Branok 6", await self.texts())  # only the second run went back
            self.assertIn("Brannoch waits at the gate", await self.texts())

    async def test_an_old_finish_does_nothing_after_the_name_changed_again(self) -> None:
        many = [Line(MS + 20_000 + i, PLAYER, "x", f"Branok {i}") for i in range(7)]
        await self.transcripts.add_lines(GUILD, self.session, many)
        with patch("dmbot.transcript.store.MAX_RESPELLED", 5):
            it = await self.fix()
        (finish,) = [
            c for c in it.response.edited[0][1].children
            if isinstance(c, name_card.FinishPastButton)
        ]  # fmt: skip
        self.bot.lookup.mark_all_stale()  # type: ignore[union-attr]
        again = name_card.FixSpellingForm(self.campaign.id, self.branok.id, "Brannoch")
        again.name = SimpleNamespace(value="Brannok")  # type: ignore[assignment]
        await again.on_submit(self.it())
        before = await self.texts()
        press = self.it()
        await finish.callback(press)
        self.assertEqual(await self.texts(), before)
        self.assertIn("out of date", press.followup.send.await_args.args[0])

    def test_the_buttons_are_registered_so_they_work_after_a_restart(self) -> None:
        import inspect

        from dmbot.bot import DMBot

        source = inspect.getsource(DMBot)
        for name in ("UndoSpellingButton", "FinishPastButton", "UndoRunButton"):
            self.assertRegex(source, rf"add_dynamic_items\([^)]*{name}")

    def test_the_card_draws_with_both_buttons_for_every_kind(self) -> None:
        flags: list[dict[str, Any]] = [
            {},
            {"item": True},
            {"thread": True},
            {"thread": True, "promise": True},
            {"clue": True, "standing": True, "deed": True, "led": True, "book": True},
            {"sheet": True, "others": True, "led": True, "book": True, "item": True},
        ]
        for extra in flags:
            with self.subTest(extra=extra):
                card = name_card.NameCard(
                    "c" * 32, "e" * 32, undo_spelling=3, finish_past=3, **extra
                )  # raises if a row overflows
                self.assertTrue(
                    any(isinstance(c, name_card.FinishPastButton) for c in card.children)
                )

    async def test_a_curly_apostrophe_in_a_stored_line_is_found(self) -> None:
        kaz = await ui.save_name(self.memory, self.campaign, "Ka'zeth", "npc", [], [])
        await self.transcripts.add_lines(
            GUILD, self.session, [Line(MS + 9000, PLAYER, "x", "Ka\u2019zeth rides out")]
        )
        form = name_card.FixSpellingForm(self.campaign.id, kaz.id, "Ka'zeth")
        form.name = SimpleNamespace(value="Kazett")  # type: ignore[assignment]
        await form.on_submit(self.it())
        self.assertIn("Kazett rides out", await self.texts())

    async def test_only_a_dm_may_finish(self) -> None:
        many = [Line(MS + 20_000 + i, PLAYER, "x", f"Branok {i}") for i in range(7)]
        await self.transcripts.add_lines(GUILD, self.session, many)
        with patch("dmbot.transcript.store.MAX_RESPELLED", 5):
            it = await self.fix()
        (finish,) = [
            c for c in it.response.edited[0][1].children
            if isinstance(c, name_card.FinishPastButton)
        ]  # fmt: skip
        before = await self.texts()
        await finish.callback(self.it(99))
        self.assertEqual(await self.texts(), before)

    async def test_deleting_a_person_forgets_where_their_lines_were_respelled(self) -> None:
        await self.fix()
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute("SELECT count(*) AS n FROM transcript_respells")
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual(row["n"], 2)
        await self.transcripts.delete_person(GUILD, PLAYER)
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute("SELECT count(*) AS n FROM transcript_respells")
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual(row["n"], 0)

    async def test_the_places_kept_hold_positions_and_the_name_never_the_words(self) -> None:
        await self.fix()
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute("SELECT * FROM transcript_respells")
            rows = await cur.fetchall()
        for row in rows:
            self.assertEqual(row["new_text"], "Brannoch")
            self.assertTrue(set(row["was"]) <= {"Branok", "branok"})
            self.assertNotIn("gate", str(dict(row)))
