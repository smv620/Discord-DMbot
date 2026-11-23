"""The three lines at the top of a character's card, the pure part (#5050). Invented names."""

from __future__ import annotations

import re
import unittest
from typing import ClassVar
from unittest import IsolatedAsyncioTestCase

from dmbot.memory import card_lines as cl
from dmbot.memory import heard
from dmbot.recaps.beats import Beat
from dmbot.transcript.models import Line

DM, PLAYER, OTHER = 1, 2, 3


def line(n: int, user: int, text: str, **kw: object) -> Line:
    return Line(n * 1000, user, text, text, **kw)  # type: ignore[arg-type]


def beat(what: str, lines: tuple[int, ...], where: str = "") -> Beat:
    return Beat(1, what, (), lines, "event", where=where)


class TheSharedFilter(unittest.TestCase):
    LINES: ClassVar[list[Line]] = [
        line(1, DM, "Orla by the ford."),
        line(2, DM, "Orla", sidebar="question"),
        line(3, PLAYER, "  "),
        line(4, OTHER, "Orla again."),
        line(5, PLAYER, "Orla said it."),
    ]

    def test_the_card_and_the_count_look_at_the_same_lines(self) -> None:
        seen = [n for n, _ in heard.countable(self.LINES, {DM, PLAYER})]
        self.assertEqual(seen, [1, 5])  # (sidebar, blank, a speaker who did not agree: out)
        counted = heard.count_lines(self.LINES, {DM, PLAYER}, lambda t: {"orla"} & {"orla"})
        self.assertEqual(counted, {("orla", DM): 1, ("orla", PLAYER): 1})


def named(*names: str):  # type: ignore[no-untyped-def]
    """A stand-in matcher: the ids of the names that appear in the line."""
    return lambda text: {n.lower() for n in names if n in text}


class TheScan(unittest.TestCase):
    def test_one_pass_notes_every_character_asked_about(self) -> None:
        lines = [
            line(1, DM, "Orla and Rook at the ford."),
            line(2, PLAYER, "Rook alone."),
            line(3, DM, "Orla again."),
        ]
        got = cl.scan_session(lines, {DM, PLAYER}, named("Orla", "Rook"), {"orla", "rook"})
        self.assertEqual(got["orla"], cl.Seen(1, 3))
        self.assertEqual(got["rook"], cl.Seen(1, 2))

    def test_a_character_not_asked_about_is_not_noted(self) -> None:
        got = cl.scan_session([line(1, DM, "Orla.")], {DM}, named("Orla"), {"rook"})
        self.assertEqual(got, {})

    def test_the_recap_read_aloud_never_makes_a_first_but_does_make_a_last(self) -> None:
        lines = [line(1, DM, "Last time Orla met Rook."), line(2, DM, "Rook is here.")]
        got = cl.scan_session(lines, {DM}, named("Orla", "Rook"), {"orla", "rook"}, retold={1})
        self.assertEqual(got["orla"], cl.Seen(None, 1))  # only the recap said her
        self.assertEqual(got["rook"], cl.Seen(2, 2))

    def test_the_counts_own_filter_applies(self) -> None:
        lines = [
            line(1, DM, "Orla?", sidebar="question"),
            line(2, OTHER, "Orla!"),
            line(3, PLAYER, "  "),
            line(4, PLAYER, "Orla."),
        ]
        got = cl.scan_session(lines, {DM, PLAYER}, named("Orla"), {"orla"})
        self.assertEqual(got["orla"], cl.Seen(4, 4))


def sync(sessions: dict[int, dict[str, cl.Seen]], asked: list[int] | None = None):  # type: ignore[no-untyped-def]
    """A session pass over a table of results, noting which sessions were asked for."""

    async def scan(i: int) -> dict[str, cl.Seen]:
        if asked is not None:
            asked.append(i)
        return sessions.get(i, {})

    return scan


class TheSpans(IsolatedAsyncioTestCase):
    async def test_first_from_the_oldest_and_last_from_the_newest(self) -> None:
        sessions = {
            0: {"orla": cl.Seen(None, 2)},  # (only the recap said her)
            1: {"orla": cl.Seen(5, 9), "rook": cl.Seen(1, 1)},
            2: {"orla": cl.Seen(3, 3)},
        }
        got = await cl.spans(3, sync(sessions), {"orla", "rook", "nobody"})
        self.assertEqual(got["orla"], cl.Span(cl.Where(1, 5), cl.Where(2, 3)))
        self.assertEqual(got["rook"], cl.Span(cl.Where(1, 1), cl.Where(1, 1)))
        self.assertEqual(got["nobody"], cl.Span(None, None))  # (never, at the table)

    async def test_a_name_only_a_recap_said_has_a_last_and_no_first(self) -> None:
        got = await cl.spans(1, sync({0: {"orla": cl.Seen(None, 4)}}), {"orla"})
        self.assertEqual(got["orla"], cl.Span(None, cl.Where(0, 4)))

    async def test_each_session_is_read_at_most_once_for_the_whole_batch(self) -> None:
        asked: list[int] = []
        sessions = {0: {"orla": cl.Seen(1, 1)}, 3: {"orla": cl.Seen(1, 1)}}
        await cl.spans(4, sync(sessions, asked), {"orla", "rook"})
        self.assertEqual(sorted(asked), [0, 1, 2, 3])
        self.assertEqual(len(asked), len(set(asked)))  # (no session twice)

    async def test_the_search_stops_when_every_character_has_its_first_and_last(self) -> None:
        asked: list[int] = []

        async def scan(i: int) -> dict[str, cl.Seen]:
            asked.append(i)
            return {"orla": cl.Seen(1, 1)}

        got = await cl.spans(100, scan, {"orla"})
        self.assertEqual(got["orla"], cl.Span(cl.Where(0, 1), cl.Where(99, 1)))
        self.assertEqual(sorted(asked), [0, 99])  # (a long campaign, two reads)

    async def test_no_sessions_means_no_span(self) -> None:
        self.assertEqual(await cl.spans(0, sync({}), {"orla"}), {"orla": cl.Span(None, None)})


class TheScene(unittest.TestCase):
    BEATS: ClassVar[tuple[Beat, ...]] = (
        beat("The party reached the Northlook inn", (3, 4, 5), "Bryn Shander"),
        beat("An argument over the ship's price", (20, 21)),
    )

    def test_the_beat_that_cites_the_line_is_the_scene(self) -> None:
        self.assertEqual(cl.scene_near(self.BEATS, 4), self.BEATS[0])
        self.assertEqual(cl.scene_near(self.BEATS, 21), self.BEATS[1])

    def test_a_line_between_scenes_belongs_to_the_nearer_and_a_tie_to_the_earlier(self) -> None:
        self.assertEqual(cl.scene_near(self.BEATS, 8), self.BEATS[0])
        self.assertEqual(cl.scene_near(self.BEATS, 18), self.BEATS[1])
        self.assertEqual(cl.scene_near(self.BEATS, 12), self.BEATS[0])  # (7 and 8 away: 12-5=7)

    def test_no_beats_is_no_scene(self) -> None:
        self.assertIsNone(cl.scene_near((), 3))
        self.assertIsNone(cl.scene_near((beat("x", ()),), 3))


class TheWords(unittest.TestCase):
    FIRST = 1_700_000_000  # 14 Nov 2023

    def test_a_scene_line_says_the_session_the_day_and_what_was_happening(self) -> None:
        text = cl.when_line(
            cl.FIRST_MET,
            3,
            self.FIRST,
            cl.scene_words(beat("The party reached the Northlook inn.", (1,), "Bryn Shander")),
        )
        self.assertEqual(
            text,
            "First met: Session 3, 14 Nov. The party reached the Northlook inn in Bryn Shander.",
        )

    def test_the_place_is_not_said_twice(self) -> None:
        text = cl.when_line(
            cl.LAST_MENTIONED,
            1,
            self.FIRST,
            cl.scene_words(beat("They reached Bryn Shander", (1,), "Bryn Shander")),
        )
        self.assertEqual(text, "Last mentioned: Session 1, 14 Nov. They reached Bryn Shander.")

    def test_a_scene_the_job_wrote_is_used_as_it_was_kept(self) -> None:
        # (a session with no recap beat: the background job's own clause, stored on the row)
        text = cl.when_line(cl.FIRST_MET, 4, self.FIRST, "when the party argued over the price")
        self.assertEqual(
            text, "First met: Session 4, 14 Nov. When the party argued over the price."
        )

    def test_until_the_job_has_written_a_scene_the_line_says_so_and_is_never_the_date_alone(
        self,
    ) -> None:
        for none in ("", "   ", cl.scene_words(None)):
            self.assertEqual(
                cl.when_line(cl.LAST_MENTIONED, 7, self.FIRST, none),
                "Last mentioned: Session 7, 14 Nov (DMbot is still writing what happened).",
            )
        self.assertEqual(
            cl.when_line(cl.FIRST_MET, 7, self.FIRST),
            "First met: Session 7, 14 Nov (DMbot is still writing what happened).",
        )

    def test_the_lore_line_names_the_document_and_the_heading(self) -> None:
        self.assertEqual(
            cl.lore_line("Icewind Dale notes", "Bryn Shander"),
            "First met: in your lore, *Icewind Dale notes*, under 'Bryn Shander'.",
        )
        self.assertEqual(cl.lore_line("Notes"), "First met: in your lore, *Notes*.")

    def test_the_who_line_is_the_sentence_or_says_why_there_is_none(self) -> None:
        self.assertEqual(cl.who_line("A halfling innkeeper.\n", "Shafina"), "A halfling innkeeper.")
        self.assertEqual(cl.who_line(None, "Shafina"), "DMbot doesn't know much about Shafina yet.")
        self.assertEqual(cl.who_line("", "Shafina", pending=True), cl.STILL_WRITING)

    def test_a_hidden_entry_never_reads_as_an_unknown_one(self) -> None:
        # (a secret NPC: the job kept no sentence because the players may not see the entry)
        self.assertEqual(
            cl.who_line(None, "Veiled", hidden=True),
            "DMbot writes this line only from what your players may know.",
        )
        self.assertNotIn("know much", cl.who_line(None, "Veiled", hidden=True))
        # (a sentence is still shown when there is one; hidden only explains an empty line)
        self.assertEqual(cl.who_line("A rogue.", "Veiled", hidden=True), "A rogue.")

    def test_a_long_sentence_is_cut_at_a_word_and_never_refused(self) -> None:
        long = "word " * 100
        cut = cl.cut_sentence(long)
        self.assertLessEqual(len(cut), cl.WHO_MAX)
        self.assertTrue(cut.endswith("…") and not cut.endswith(" …"))
        self.assertEqual(cl.cut_sentence("short"), "short")

    def test_the_words_are_plain(self) -> None:
        lines = [
            cl.when_line(cl.FIRST_MET, 1, self.FIRST, "They met in the inn"),
            cl.when_line(cl.FIRST_MET, 1, self.FIRST),
            cl.lore_line("Notes", "Inn"),
            cl.NEVER_AT_THE_TABLE,
            cl.STILL_WRITING,
            cl.who_line(None, "Shafina"),
            cl.who_line(None, "Shafina", hidden=True),
        ]
        for text in lines:
            self.assertIsNone(re.search(r"\b(node|entity|graph)\b", text, re.I), text)
