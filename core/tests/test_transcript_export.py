"""Transcript downloads (#41, #125) and the unsaved-lines buffer, without Discord or a
database."""

import dataclasses
import unittest
from typing import ClassVar

from dmbot.transcript import export
from dmbot.transcript.models import (
    MAX_UNSAVED,
    NO_LINEAGE,
    SIDEBAR_ANSWER,
    SIDEBAR_QUESTION,
    SIDEBAR_TABLE_QUESTION,
    VIA_TYPED,
    VIA_VOICE,
    Line,
    Lineage,
    TranscriptBuffer,
    TranscriptSession,
)

START = 1_700_000_000  # 2023-11-14 22:13:20 UTC
MIA, DEE = 8, 9


def session(ended: int | None = START + 2 * 3600 + 14 * 60) -> TranscriptSession:
    return TranscriptSession("a" * 32, 1, "c", START, ended, 2, (MIA, DEE), number=7)


def line(seconds: float, user: int, text: str) -> Line:
    return Line(int(START * 1000 + seconds * 1000), user, text, text)


class Render(unittest.TestCase):
    def test_lines_show_time_since_start_and_the_speaker(self) -> None:
        text = export.render(
            "Rime of the Frostmaiden",
            session(),
            [line(5, MIA, "I cast  Shield."), line(2530, DEE, "Run!")],
            {MIA: "Mia", DEE: "Dee"},
        )
        head, body = text.split("\n\n", 1)
        self.assertIn("DMbot transcript: Rime of the Frostmaiden, session 7", head)
        self.assertIn("Started 2023-11-14 22:13 UTC, ran 2 h 14 min", head)
        self.assertIn("before it fixed any names, so some names may be misheard", head)
        self.assertIn("(person) {their character}", head)
        self.assertIn("Only people who agreed were recorded", head)
        self.assertEqual(
            body.splitlines(), ["[0:00:05] (Mia): I cast Shield.", "[0:42:10] (Dee): Run!"]
        )

    def test_a_dm_is_marked_and_nobody_else_can_look_like_one(self) -> None:
        # #2337: "(Name, DM)" for a DM of the campaign, on speech, typed chat and sidebar lines.
        lines = [
            line(5, MIA, "I cast Shield."),
            line(6, DEE, "Roll for it."),
            dataclasses.replace(
                line(7, DEE, "is it optional"), sidebar=SIDEBAR_QUESTION, lineage=Lineage(ref="x")
            ),
        ]
        text = export.render(
            "X", session(), lines, {MIA: "Mia, DM", DEE: "Dee"}, dms={DEE}, version=export.AS_HEARD
        )
        self.assertIn("(Mia DM): I cast Shield.", text)  # a player's own ", DM" does not pass
        self.assertIn("(Dee, DM): Roll for it.", text)
        self.assertIn("(Dee, DM) [DM Sidebar", text)

    def test_a_name_with_dm_in_it_never_looks_like_the_mark_even_when_told_apart(self) -> None:
        names = export.distinct_names({MIA: "Eve, DM", DEE: "Eve, DM"})  # two players, one name
        text = export.render(
            "X", session(), [line(5, MIA, "a"), line(6, DEE, "b")], names, version=export.AS_HEARD
        )
        self.assertNotIn(", DM", text.split("\n\n", 1)[1])

    def test_no_look_alike_of_the_dm_mark_passes(self) -> None:
        names = {
            MIA: "Bob\uff0c DM",  # full-width comma
            DEE: "Bob\u201a DM",  # a low quotation mark
            3: "Bob, \u1d05\u1d0d",  # small-capital letters
            4: "Bob, DM_",
            5: "Bob \u3001DM",
            6: "Bob\u02cf DM",  # a modifier letter
            7: "Bob\u0375 DM",  # the Greek numeral sign
            8: "Bob\u00b8 DM",  # a cedilla (which folds to a combining one)
        }
        lines = [line(i, user, "hi") for i, user in enumerate(names)]
        text = export.render("X", session(), lines, names, version=export.AS_HEARD)
        body = text.split("\n\n", 1)[1]
        self.assertNotIn(", DM", body)
        self.assertNotIn("\uff0c", body)
        self.assertNotIn("\u201a", body)
        for gone in ("\u02cf", "\u0375", "\u00b8", "\u0327"):
            self.assertNotIn(gone, body)
        for user, who in ((MIA, "Bob DM"), (DEE, "Bob DM")):  # (a real DM is the only mark)
            real = export.render("X", session(), [line(1, user, "hi")], names, dms={user})
            self.assertIn(f"({who}, DM)", real)

    def test_a_chosen_name_cannot_copy_the_duplicate_mark_or_collide_with_it(self) -> None:
        names = export.distinct_names({1: "Sam", 2: "Sam", 3: "Sam (2)", 4: "sam [3]"})
        self.assertEqual([str(v) for v in names.values()], ["Sam", "Sam (2)", "Sam 2", "sam 3"])
        self.assertEqual(len({v.casefold() for v in names.values()}), 4)  # four people, four labels
        text = export.render("X", session(), [line(1, 3, "hi")], names, version=export.AS_HEARD)
        self.assertIn("(Sam 2): hi", text)  # brackets in a chosen name are gone, not kept
        raw = export.render("X", session(), [line(1, 1, "hi")], {1: "Alice (2)"})
        self.assertIn("(Alice 2): hi", raw)  # a name that was not made by distinct_names

    def test_a_bracket_of_any_script_cannot_stand_in_for_the_duplicate_mark(self) -> None:
        looks = ["Sam \u27ee2\u27ef", "Sam\u2768 2\u2769", "Sam \u27e82\u27e9", "Sam \u30102\u3011"]
        names = export.distinct_names({1: "Sam", **{n + 2: raw for n, raw in enumerate(looks)}})
        for label in names.values():
            self.assertNotRegex(label, r"[\u27ee\u27ef\u2768\u2769\u27e8\u27e9\u3010\u3011]")
        # five people, five different labels, and only the real mark is a bracket
        self.assertEqual(len({v.casefold() for v in names.values()}), 5)
        self.assertEqual(sum("(" in v for v in names.values()), 3)  # "Sam 2 (2)" .. "Sam 2 (4)"

    def test_the_order_of_first_speaking_is_one_rule_for_every_version(self) -> None:
        lines = [
            dataclasses.replace(line(1, MIA, "typed"), typed=True),
            line(2, DEE, "said"),
            dataclasses.replace(
                line(3, 3, "asked"), sidebar=SIDEBAR_QUESTION, lineage=Lineage(ref="x")
            ),
            line(4, MIA, "said"),
        ]
        self.assertEqual(export.speaker_order(lines, export.AS_HEARD), [MIA, DEE, 3])
        self.assertEqual(
            export.speaker_order(lines, export.CLEANED), [MIA, DEE]
        )  # typed chat is in the cleaned version now (#2447); asking DMbot is not
        hidden = [dataclasses.replace(lines[0], topic="off_topic"), *lines[1:]]
        self.assertEqual(export.speaker_order(hidden, export.CLEANED), [DEE, MIA])

    def test_two_people_with_one_name_tell_apart_and_keep_the_mark(self) -> None:
        names = export.distinct_names({MIA: "Sam", DEE: "sam", 3: "Sam", 4: "Ann"})
        self.assertEqual(names, {MIA: "Sam", DEE: "sam (2)", 3: "Sam (3)", 4: "Ann"})
        text = export.render(
            "X", session(), [line(5, DEE, "hi")], names, dms={DEE}, version=export.AS_HEARD
        )
        self.assertIn("(sam (2), DM): hi", text)

    def test_the_how_to_read_line_explains_the_tags_in_plain_words(self) -> None:
        for words in (
            "{narrating} means the DM is describing the scene",
            "{table_talk} is out-of-character talk about the rules or running the game",
            '"roll initiative"',
            "{non-game_content} is chat that isn't about the game",
            "A person marked DM is the Dungeon Master",
        ):
            self.assertIn(words, export.HOW_TO_READ)

    def test_a_line_said_as_someone_shows_that_instead_of_the_character(self) -> None:
        # #53: the DM narrating or voicing an NPC; a topic tag still wins; brackets are stripped.
        lines = [
            dataclasses.replace(line(5, DEE, "The door creaks."), entity="narrating"),
            dataclasses.replace(line(6, DEE, "Welcome, [travellers]."), entity="Brother {Hiro}"),
            dataclasses.replace(line(7, DEE, "brb"), entity="narrating", topic="off_topic"),
        ]
        text = export.render("X", session(), lines, {DEE: "Dee"}, characters={DEE: "Cerric"})
        self.assertIn("[0:00:05] (Dee) {narrating}: The door creaks.\n", text)
        self.assertIn("[0:00:06] (Dee) {Brother Hiro}: Welcome, [travellers].\n", text)
        self.assertIn("[0:00:07] (Dee) {non-game_content}: brb\n", text)

    def test_an_entity_is_tidied_and_an_off_topic_run_hides_it(self) -> None:
        tidy = dataclasses.replace(line(5, DEE, "x"), entity="  Brother \n Hiro " + "z" * 200)
        self.assertEqual(tidy.entity, ("Brother Hiro " + "z" * 200)[:120].strip())
        self.assertEqual(dataclasses.replace(line(5, DEE, "x"), entity="  ").entity, "")
        hidden = dataclasses.replace(
            line(6, DEE, "my boss"), entity="Hiro", topic="off_topic", duration_ms=3000
        )
        cleaned = export.render("X", session(), [hidden], {DEE: "Dee"}, version=export.CLEANED)
        self.assertIn("(Dee) [3s of off-topic chat skipped]", cleaned)
        self.assertNotIn("Hiro", cleaned)

    def test_each_player_has_their_character(self) -> None:
        text = export.render(
            "X",
            session(),
            [line(5, MIA, "I cast Shield."), line(6, DEE, "Roll for it.")],
            {MIA: "Mia", DEE: "Dee"},
            characters={MIA: "Cerric"},
        )
        self.assertIn("[0:00:05] (Mia) {Cerric}: I cast Shield.\n", text)
        self.assertIn("[0:00:06] (Dee): Roll for it.\n", text)  # the DM plays no one

    def test_the_as_heard_file_has_what_was_heard(self) -> None:
        fixed = Line(START * 1000, MIA, "I saw Beleros", "I saw Belleros")
        text = export.render("X", session(), [fixed], {MIA: "Mia"})
        self.assertIn("(Mia): I saw Beleros\n", text)

    def test_the_cleaned_file_has_the_fixed_names(self) -> None:
        fixed = Line(START * 1000, MIA, "I saw Beleros", "I saw Belleros")
        text = export.render("X", session(), [fixed], {MIA: "Mia"}, version=export.CLEANED)
        self.assertIn("(Mia): I saw Belleros\n", text)
        self.assertIn("Cleaned: DMbot fixed the spelling of names it was sure about", text)
        self.assertNotIn("As heard:", text)
        self.assertEqual(
            export.file_name("X", session(), export.CLEANED), "x-session-7-cleaned.txt"
        )

    def test_the_cleaned_file_skips_off_topic_runs_and_says_how_long(self) -> None:
        # #52: one marker per run, with the total; the as-heard file keeps everything.
        lines = [
            line(1, MIA, "I search the chest"),
            dataclasses.replace(
                line(5, DEE, "my boss called"), duration_ms=40_000, topic="off_topic"
            ),
            dataclasses.replace(
                line(50, DEE, "he wants me Monday"), duration_ms=42_000, topic="off_topic"
            ),
            dataclasses.replace(line(95, MIA, "is it my turn?"), topic="table_talk"),
        ]
        names = {MIA: "Mia", DEE: "Dee"}
        cleaned = export.render("X", session(), lines, names, version=export.CLEANED)
        self.assertIn("[0:00:05] (Dee) [1m 22s of off-topic chat skipped]\n", cleaned)
        self.assertNotIn("boss", cleaned)
        self.assertIn(
            "(Mia) {table_talk}: is it my turn?", cleaned
        )  # table talk stays, tagged (#53)
        self.assertIn("Chat clearly not about the game is left out", cleaned)
        heard = export.render("X", session(), lines, names)
        self.assertIn("my boss called", heard)
        self.assertIn("he wants me Monday", heard)
        self.assertNotIn("skipped]", heard)
        self.assertIn("[0:00:05] (Dee) {non-game_content}: my boss called", heard)
        self.assertIn("[0:00:01] (Mia): I search the chest", heard)
        with_character = export.render(
            "X", session(), lines, names, characters={MIA: "Cerric"}, version=export.CLEANED
        )
        self.assertIn("(Mia) {Cerric}: I search the chest", with_character)
        self.assertIn("(Dee) [1m 22s of off-topic chat skipped]", with_character)
        self.assertIn("(Mia) {table_talk}: is it my turn?", with_character)  # not {Cerric}

    def test_the_header_says_which_speech_to_text_wrote_it_in_plain_words(self) -> None:
        engines = ("deepgram nova-3 api.deepgram.com",)
        text = export.render("X", dataclasses.replace(session(), engines=engines), [], {})
        self.assertIn("Speech to text: Deepgram, an online service (model nova-3)", text)
        self.assertNotIn("api.deepgram.com", text)  # the endpoint stays in the database
        self.assertEqual(
            export.written_by(
                ["deepgram nova-3 x", "whisper-local small local", "deepgram nova-3 x"]
            ),
            "Deepgram, an online service (model nova-3); then Whisper, on DMbot's own "
            "computer (model small); then Deepgram, an online service (model nova-3)",
        )
        self.assertNotIn("Speech to text", export.render("X", session(), [], {}))  # older

    def test_an_unknown_version_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            export.render("X", session(), [], {}, version="raw")

    def test_unknown_and_tricky_names_are_safe(self) -> None:
        text = export.render(
            "X", session(), [line(1, MIA, "hi"), line(2, DEE, "yo")], {DEE: "Dee‮\nevil"}
        )
        self.assertIn("[0:00:01] (Someone): hi", text)
        self.assertIn("[0:00:02] (Dee evil): yo", text)

    def test_a_running_session_says_where_it_ends(self) -> None:
        text = export.render("X", session(None), [line(65, MIA, "hi")], {MIA: "Mia"}, running=True)
        self.assertIn("still recording, so this file stops here, at 0:01:05.", text)
        self.assertNotIn(", ran ", text)

    def test_speech_before_the_start_never_shows_a_negative_time(self) -> None:
        self.assertIn(
            "[0:00:00] (Mia): early",
            export.render("X", session(), [line(-3, MIA, "early")], {MIA: "Mia"}),
        )

    def test_file_names_are_safe_everywhere(self) -> None:
        self.assertEqual(
            export.file_name("Rime of the Frostmaiden: Part 2!", session()),
            "rime-of-the-frostmaiden-part-2-session-7-as-heard.txt",
        )
        self.assertEqual(export.file_name("🐉", session()), "campaign-session-7-as-heard.txt")

    def test_durations_and_labels_in_plain_words(self) -> None:
        self.assertEqual(export.duration(30), "under a minute")
        self.assertEqual(export.duration(35 * 60), "35 min")
        self.assertEqual(export.duration(2 * 3600), "2 h")
        # Named by number: a UTC date alone can look like the wrong day.
        self.assertEqual(export.session_label(session()), "Session 7 · 2 h 14 min · Nov 14 (UTC)")
        self.assertIn("stopped early", export.session_label(session(None)))
        self.assertIn("recording now", export.session_label(session(None), running=True))


class Buffer(unittest.TestCase):
    def test_lines_from_someone_who_stopped_are_never_taken(self) -> None:
        buffer = TranscriptBuffer()
        buffer.add(line(1, MIA, "keep"))
        buffer.add(line(2, DEE, "drop"))
        self.assertEqual([w.heard for w in buffer.take(lambda uid: uid == MIA)], ["keep"])
        self.assertEqual(len(buffer), 0)  # Dee's line is gone, not kept for later

    def test_drop_speaker(self) -> None:
        buffer = TranscriptBuffer()
        buffer.add(line(1, MIA, "keep"))
        buffer.add(line(2, DEE, "drop"))
        buffer.drop_speaker(DEE)
        self.assertEqual([w.heard for w in buffer.take(lambda _: True)], ["keep"])

    def test_a_failed_save_is_retried_in_order(self) -> None:
        buffer = TranscriptBuffer()
        buffer.add(line(1, MIA, "first"))
        batch = buffer.take(lambda _: True)
        buffer.add(line(2, MIA, "second"))
        buffer.put_back(batch)
        self.assertEqual([w.heard for w in buffer.take(lambda _: True)], ["first", "second"])

    def test_waiting_lines_are_capped_and_blank_ones_ignored(self) -> None:
        buffer = TranscriptBuffer()
        buffer.add(line(0, MIA, "  "))
        self.assertEqual(len(buffer), 0)
        for i in range(MAX_UNSAVED + 3):
            buffer.add(line(i, MIA, f"line {i}"))
        self.assertEqual((len(buffer), buffer.dropped), (MAX_UNSAVED, 3))


class DownloadButtons(unittest.TestCase):
    """The buttons sent when a session ends (#296): their IDs work after a restart."""

    def test_three_choices_and_old_buttons_still_work(self) -> None:
        from dmbot.ui import transcripts as ui

        view = ui.download_view(1, "a" * 32)
        every = [item.custom_id for item in view.children]  # type: ignore[attr-defined]
        ids = every[:3]  # the downloads; the fourth is a player's suggested fix (#1491)
        self.assertEqual(
            ids,
            [f"dmbot:transcript:1:{'a' * 32}:{c}" for c in ("cleaned", "heard", "both")],
        )
        self.assertEqual(every[3:], [f"dmbot:fixline:1:{'a' * 32}"])
        template = ui.DownloadButton.__discord_ui_compiled_template__
        for custom_id in [*ids, f"dmbot:transcript:1:{'a' * 32}"]:
            with self.subTest(custom_id=custom_id):
                self.assertIsNotNone(template.fullmatch(custom_id))
                self.assertLessEqual(len(custom_id), 100)
        old = ui.DownloadButton(1, "a" * 32)  # sent before there was a choice
        self.assertEqual(old.versions, (export.AS_HEARD,))
        both = ui.DownloadButton(1, "a" * 32, "both")
        self.assertEqual(both.versions, (export.CLEANED, export.AS_HEARD))


DM = 7


def sidebar(seconds: float, kind: str, text: str, lineage: Lineage = NO_LINEAGE) -> Line:
    when = int(START * 1000 + seconds * 1000)
    return Line(when, DM, text, text, sidebar=kind, lineage=lineage)


ASKED = Lineage(ref="a1b2c3", via=VIA_VOICE, stt="deepgram nova-3 api.deepgram.com")
REPLIED = Lineage(
    reply_to="a1b2c3",
    model="claude-haiku-4-5",
    prompt="sidebar-1",
    sources=("SRD 5.2.1 p. 241", "house rule 3"),
)


class TypedInTheCleanedVersion(unittest.TestCase):
    """#2447, item 6: typed chat is in both versions, tagged (typed); typing clearly not about the
    game is left out of the cleaned one, as talk like it is."""

    def render(self, version: str, lines: list[Line]) -> list[str]:
        text = export.render("X", session(), lines, {MIA: "Mia", DEE: "Dee"}, version=version)
        return text.split("\n\n", 1)[1].splitlines()

    def test_typed_game_chat_is_in_both_versions_under_the_writers_name(self) -> None:
        lines = [
            dataclasses.replace(line(5, MIA, "we take a long rest"), typed=True),
            line(9, DEE, "Run!"),
        ]
        want = ["[0:00:05] (Mia) (typed): we take a long rest", "[0:00:09] (Dee): Run!"]
        self.assertEqual(self.render(export.AS_HEARD, lines), want)
        self.assertEqual(self.render(export.CLEANED, lines), want)

    def test_off_topic_typing_is_hidden_from_the_cleaned_version_only(self) -> None:
        lines = [
            dataclasses.replace(line(5, MIA, "my cat"), typed=True, topic="off_topic"),
            line(9, DEE, "Run!"),
        ]
        self.assertIn("(typed): my cat", "\n".join(self.render(export.AS_HEARD, lines)))
        cleaned = "\n".join(self.render(export.CLEANED, lines))
        self.assertNotIn("my cat", cleaned)
        self.assertIn("Run!", cleaned)

    def test_the_note_is_there_even_when_every_typed_line_is_hidden(self) -> None:
        lines = [dataclasses.replace(line(5, MIA, "my cat"), typed=True, topic="off_topic")]
        head = export.render("X", session(), lines, {MIA: "Mia"}, version=export.CLEANED)
        self.assertIn("(typed) lines were typed", head.split("\n\n", 1)[0])
        self.assertNotIn("my cat", head.split("\n\n", 1)[1])


class DmSidebarLines(unittest.TestCase):
    """The DM's question and DMbot's answer (#935, #933): in the as-heard file for everyone
    who can read transcripts, with where each came from; never in the cleaned one."""

    lines: ClassVar[list[Line]] = [
        line(5, MIA, "We ride at dawn."),
        sidebar(10, SIDEBAR_QUESTION, "find if you need line of sight for fireball", ASKED),
        sidebar(12, SIDEBAR_ANSWER, "No: a point you choose. (SRD 5.2.1, sure)", REPLIED),
        line(20, DEE, "Run!"),
    ]
    names: ClassVar[dict[int, str]] = {MIA: "Mia", DEE: "Dee", DM: "Sam"}

    def render(self, version: str, lines: list[Line] | None = None) -> str:
        return export.render("X", session(), lines or self.lines, self.names, version=version)

    def test_the_as_heard_file_has_them_in_time_order_with_where_they_came_from(self) -> None:
        text = self.render(export.AS_HEARD)
        self.assertEqual(
            text.split("\n\n", 1)[1].splitlines(),
            [
                "[0:00:05] (Mia): We ride at dawn.",
                "[0:00:10] (Sam) [DM Sidebar id=a1b2c3 via=voice-memo stt=deepgram/nova-3]: "
                "find if you need line of sight for fireball",
                "[0:00:12] (DMbot) [DM Sidebar reply-to=a1b2c3 model=claude-haiku-4-5 "
                "prompt=sidebar-1 sources=SRD-5.2.1-p.-241;house-rule-3]: "
                "No: a point you choose. (SRD 5.2.1, sure)",
                "[0:00:20] (Dee): Run!",
            ],
        )
        self.assertIn("questions to DMbot", text)
        self.assertNotIn("api.deepgram.com", text)  # the company's host stays private

    def test_the_cleaned_file_never_has_them(self) -> None:
        text = self.render(export.CLEANED)
        for word in ("Sidebar", "fireball", "DMbot)", "a1b2c3"):
            self.assertNotIn(word, text)
        self.assertIn("Run!", text)

    def test_a_typed_question_and_an_unknown_origin_show_only_what_is_known(self) -> None:
        typed = sidebar(
            10, SIDEBAR_QUESTION, "check flanking", Lineage(ref="ff0011", via=VIA_TYPED)
        )
        bare = sidebar(11, SIDEBAR_ANSWER, "Optional.")
        body = self.render(export.AS_HEARD, [typed, bare]).split("\n\n", 1)[1].splitlines()
        self.assertEqual(
            body,
            [
                "[0:00:10] (Sam) [DM Sidebar id=ff0011 via=typed]: check flanking",
                "[0:00:11] (DMbot) [DM Sidebar]: Optional.",
            ],
        )

    def test_a_name_cant_pass_for_dmbot_or_forge_a_tag(self) -> None:
        mean = {**self.names, DM: "DMbot [DM Sidebar]"}
        text = export.render("X", session(), self.lines, mean, version=export.AS_HEARD)
        self.assertIn("(DMbot DM Sidebar) [DM Sidebar id=a1b2c3", text)
        forged = Lineage(ref="x] [DM Sidebar", via=VIA_TYPED)
        line_ = sidebar(10, SIDEBAR_QUESTION, "hi", forged)
        self.assertIn(
            "[DM Sidebar id=x-DM-Sidebar via=typed]", self.render(export.AS_HEARD, [line_])
        )

    def test_the_header_says_nothing_about_them_when_there_are_none(self) -> None:
        text = export.render("X", session(), [line(5, MIA, "hi")], {MIA: "Mia"})
        self.assertNotIn("Sidebar", text)


class SidebarKinds(unittest.TestCase):
    def test_a_made_up_kind_is_refused_before_it_can_fail_a_whole_save(self) -> None:
        with self.assertRaises(ValueError):
            Line(1, DM, "x", "x", sidebar="memo")
        with self.assertRaises(ValueError):
            Lineage(via="carrier-pigeon")


class TableQuestionLines(unittest.TestCase):
    """A player's question to DMbot, tagged apart from the DM's (#1187): raw transcript only."""

    asked = Lineage(ref="d4e5f6", via="chat")
    lines: ClassVar[list[Line]] = [
        Line(
            10_000 + int(START * 1000),
            MIA,
            "does fireball need line of sight",
            "does fireball need line of sight",
            sidebar=SIDEBAR_TABLE_QUESTION,
            lineage=Lineage(ref="d4e5f6", via="chat"),
        ),
        Line(
            12_000 + int(START * 1000),
            MIA,
            "No. A point you choose.",
            "No. A point you choose.",
            sidebar=SIDEBAR_ANSWER,
            lineage=Lineage(reply_to="d4e5f6", model="m", prompt="sidebar-7", sources=("SRD",)),
        ),
        line(20, DEE, "Run!"),
    ]
    names: ClassVar[dict[int, str]] = {MIA: "Mia", DEE: "Dee"}

    def test_the_raw_transcript_has_a_table_q_tag_and_the_asker_by_name(self) -> None:
        text = export.render("X", session(), self.lines, self.names, version=export.AS_HEARD)
        self.assertIn(
            "[0:00:10] (Mia) [Table Q id=d4e5f6 via=chat]: does fireball need line of sight", text
        )
        self.assertIn("(DMbot) [DM Sidebar reply-to=d4e5f6 model=m prompt=sidebar-7", text)

    def test_the_cleaned_transcript_never_has_them(self) -> None:
        text = export.render("X", session(), self.lines, self.names, version=export.CLEANED)
        for word in ("Table Q", "fireball", "DMbot)", "d4e5f6"):
            self.assertNotIn(word, text)
        self.assertIn("Run!", text)

    def test_a_player_chat_via_is_known_and_an_unknown_one_is_not(self) -> None:
        self.assertEqual(Lineage(via="chat").via, "chat")
        with self.assertRaises(ValueError):
            Lineage(via="smoke-signal")


class SidebarLinesInTheBuffer(unittest.TestCase):
    def test_an_undo_or_a_topic_never_changes_a_sidebar_line(self) -> None:
        buffer = TranscriptBuffer()
        question = sidebar(10, SIDEBAR_QUESTION, "find the rules for flanking")
        buffer.add(question)
        self.assertFalse(buffer.relabel(DM, question.started_ms, "something else"))
        self.assertFalse(buffer.set_topic(DM, question.started_ms, "off_topic"))
        (kept,) = buffer.take(lambda user: True)
        self.assertEqual((kept.text, kept.topic, kept.sidebar), (question.text, "game", "question"))

    def test_a_consent_stop_takes_the_answer_too(self) -> None:
        buffer = TranscriptBuffer()
        buffer.add(sidebar(10, SIDEBAR_QUESTION, "find the rules for flanking"))
        buffer.add(sidebar(12, SIDEBAR_ANSWER, "Flanking is optional. (SRD, sure)"))
        self.assertEqual(buffer.take(lambda user: user != DM), [])  # answers carry the DM's ID
