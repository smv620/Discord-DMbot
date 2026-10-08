"""Lines left out as off-topic, which the DM can put back (#677): the pure parts."""

from __future__ import annotations

import unittest

from dmbot.dm_screen.left_out import left_out_view
from dmbot.transcript import left_out
from dmbot.transcript.stream import EDIT_WINDOW_S, TranscriptStream, escape
from dmbot.transcript.topics import Waiting

MIA, DEE = 1, 2


def w(speaker: int, at_s: int, text: str = "my boss called again") -> Waiting:
    return Waiting(speaker, at_s * 1000, text, 3.0)


def when(started_ms: int) -> str:
    return f"0:00:{started_ms // 1000:02d}"


class RunsTest(unittest.TestCase):
    def test_one_persons_lines_in_a_row_are_one_run(self) -> None:
        notes = left_out.LeftOut()
        (run,) = notes.add([w(DEE, 1), w(DEE, 2)])
        self.assertEqual([x.started_ms for x in run.lines], [1000, 2000])
        self.assertEqual((run.number, run.speaker), (1, DEE))

    def test_anyone_else_speaking_in_between_ends_a_run(self) -> None:
        notes = left_out.LeftOut()
        between = w(MIA, 2, "we sneak past")
        runs = notes.add([w(DEE, 1), w(DEE, 3)], [w(DEE, 1), between, w(DEE, 3)])
        self.assertEqual([len(r.lines) for r in runs], [1, 1])

    def test_their_own_kept_line_ends_a_run_too(self) -> None:
        kept = w(DEE, 2, "I attack the goblin")
        runs = left_out.LeftOut().add([w(DEE, 1), w(DEE, 3)], [w(DEE, 1), kept, w(DEE, 3)])
        self.assertEqual(len(runs), 2)
        self.assertTrue(all(kept not in r.lines for r in runs))

    def test_two_people_are_two_runs_in_the_order_heard(self) -> None:
        runs = left_out.LeftOut().add([w(MIA, 2), w(DEE, 1)])
        self.assertEqual([r.speaker for r in runs], [DEE, MIA])

    def test_numbers_never_change_meaning_and_old_runs_go(self) -> None:
        notes = left_out.LeftOut()
        for at in range(left_out.SHOWN + 3):
            notes.add([w(DEE, at)])
        self.assertEqual(len(notes.runs), left_out.SHOWN)
        self.assertEqual(notes.shown()[0].number, 4)  # the oldest three are let go
        self.assertEqual(notes.shown()[-1].number, left_out.SHOWN + 3)

    def test_someone_who_stops_loses_their_runs(self) -> None:
        notes = left_out.LeftOut()
        notes.add([w(DEE, 1), w(MIA, 2)])
        self.assertTrue(notes.drop_speaker(DEE))
        self.assertEqual([r.speaker for r in notes.runs], [MIA])
        self.assertFalse(notes.drop_speaker(DEE))

    def test_find_by_id_only(self) -> None:
        notes = left_out.LeftOut()
        (run,) = notes.add([w(DEE, 1)])
        self.assertIs(notes.find(run.id), run)
        self.assertIsNone(notes.find("00000000"))


class MessageTest(unittest.TestCase):
    def test_header_line_and_after_a_press(self) -> None:
        notes = left_out.LeftOut()
        first, second = notes.add([w(DEE, 4), w(MIA, 9, "pass the chips")])
        names = {DEE: "Dee", MIA: "Mia"}
        text, shown = left_out.message_text(notes.runs, names, when)
        self.assertTrue(
            text.startswith("🙈 **Left out as off-topic** (tap Put it back if it was game talk)\n")
        )
        self.assertIn("1. [0:00:04] Dee: my boss called again\n", text)
        self.assertEqual(shown, [first, second])
        first.put_back = True
        text, _ = left_out.message_text(notes.runs, names, when)
        self.assertIn("1. Put back: [0:00:04] Dee: my boss called again", text)
        self.assertIn("2. [0:00:09] Mia: pass the chips", text)

    def test_only_the_first_words_and_never_formatting(self) -> None:
        notes = left_out.LeftOut()
        notes.add([w(DEE, 1, "**bold** @everyone " + "word " * 30), w(DEE, 2)])
        text, _ = left_out.message_text(notes.runs, {}, when, escape)
        line = text.splitlines()[1]
        self.assertIn("Someone (2 lines): \\*\\*bold\\*\\*", line)  # no name: "Someone"
        self.assertNotIn("@everyone", line)
        self.assertTrue(line.endswith("…"))
        self.assertLess(len(line), left_out.WORDS_MAX + 40)

    def test_a_run_of_several_lines_says_theres_more(self) -> None:
        notes = left_out.LeftOut()
        notes.add([w(DEE, 1, "short"), w(DEE, 2)])
        text, _ = left_out.message_text(notes.runs, {DEE: "Dee"}, when)
        self.assertIn("Dee (2 lines): short…", text)

    def test_nothing_left(self) -> None:
        text, shown = left_out.message_text([], {}, when)
        self.assertIn("Nothing left out right now", text)
        self.assertEqual(shown, [])

    def test_a_long_list_still_fits_discord(self) -> None:
        notes = left_out.LeftOut()
        for at in range(left_out.SHOWN):
            notes.add([w(DEE, at, "x" * 500)])
        name = {DEE: "N" * 300}
        text, shown = left_out.message_text(notes.runs, name, when)
        self.assertLessEqual(len(text), left_out.MESSAGE_MAX)
        self.assertEqual(shown, notes.runs[-len(shown) :])  # the newest whole lines
        self.assertEqual(len(text.splitlines()), len(shown) + 1)

    def test_one_button_per_run_not_put_back(self) -> None:
        notes = left_out.LeftOut()
        first, _ = notes.add([w(DEE, 1), w(MIA, 2)])
        view = left_out_view(5, notes.runs)
        assert view is not None
        self.assertEqual([b.item.label for b in view.children], ["Put it back 1", "Put it back 2"])  # type: ignore[attr-defined]
        self.assertTrue(view.children[0].item.custom_id.startswith("dmbot:putback:5:"))  # type: ignore[attr-defined]
        first.put_back = True
        view = left_out_view(5, notes.runs)
        assert view is not None
        self.assertEqual(len(view.children), 1)
        notes.runs[1].put_back = True
        self.assertIsNone(left_out_view(5, notes.runs))

    def test_what_a_press_says(self) -> None:
        done = left_out.done_text(3, "Mia", in_channel=True)
        self.assertTrue(done.startswith("↩️ Put back 3 (Mia): "))
        self.assertIn("and the live channel.", done)
        self.assertTrue(left_out.done_text(3, "Mia", in_channel=None).endswith("transcript."))
        late = left_out.done_text(3, "Mia", in_channel=False)
        self.assertIn('still says "skipped" there. That\'s expected.', late)

    def test_once_the_session_ended_it_says_so(self) -> None:
        notes = left_out.LeftOut()
        notes.add([w(DEE, 1)])
        text, shown = left_out.message_text(notes.runs, {}, when, ended=True)
        self.assertTrue(text.endswith(left_out.ENDED))
        self.assertEqual(shown, notes.runs)
        self.assertTrue(left_out.message_text([], {}, when, ended=True)[0].endswith(left_out.ENDED))


class ButtonTest(unittest.IsolatedAsyncioTestCase):
    def test_its_id_matches_what_a_press_is_read_back_with(self) -> None:
        from dmbot.dm_screen.left_out import PutBackButton

        (run,) = left_out.LeftOut().add([w(DEE, 1)])
        button = PutBackButton(5, run.id)
        template = PutBackButton.__discord_ui_compiled_template__
        match = template.fullmatch(button.item.custom_id or "")
        assert match is not None
        self.assertEqual((match["guild"], match["run"]), ("5", run.id))

    async def test_a_press_from_another_server_is_closed(self) -> None:
        from unittest.mock import AsyncMock, MagicMock

        from dmbot.dm_screen.left_out import PutBackButton

        bot = MagicMock(put_back=AsyncMock())
        interaction = MagicMock(guild_id=6, client=bot)
        interaction.response.send_message = AsyncMock()
        await PutBackButton(5, "0123abcd").callback(interaction)
        bot.put_back.assert_not_awaited()
        interaction.response.send_message.assert_awaited_once_with(left_out.EXPIRED, ephemeral=True)


class CanChangeTest(unittest.TestCase):
    def test_waiting_recent_or_too_late(self) -> None:
        stream = TranscriptStream()
        stream.add(DEE, "Dee", "my boss called again", 1000)
        self.assertTrue(stream.can_change(DEE, 1000, now=0.0))  # still waiting
        self.assertFalse(stream.can_change(DEE, 2000, now=0.0))  # never heard
        built = stream.next_message(lambda _: True)
        assert built is not None
        stream.posted(built[1], object(), now=10.0)  # type: ignore[arg-type]
        self.assertTrue(stream.can_change(DEE, 1000, now=10.0 + EDIT_WINDOW_S))
        self.assertFalse(stream.can_change(DEE, 1000, now=10.0 + EDIT_WINDOW_S + 1))


if __name__ == "__main__":
    unittest.main()
