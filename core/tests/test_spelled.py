"""A name spelled out at the table (#2168): the parser's table of spoken spellings, when a spelling
counts, the live watcher, the hint, and the end-of-session suggestion. Invented names."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

from dmbot.bot import SPELLED_MAX, DMBot
from dmbot.memory.models import MemoryRuleError
from dmbot.memory.scene import HintParts, SceneTracker, prepare, scene_hints
from dmbot.transcript.spelled import (
    SpellingWatcher,
    capitalised_words,
    is_ask,
    parse,
    said_spelling,
)
from tests.test_memory_scene import NOW, lookup


class TheParser(unittest.TestCase):
    def test_a_table_of_spoken_spellings(self) -> None:
        table = [
            ("it's spelled B-R-A-N-N-O-C-H", "Brannoch"),
            ("Uh, it's K-A-E-L", "Kael"),
            ("yeah it's B R A N", "Bran"),
            ("B R A N N O C H", "Brannoch"),
            ("b, r, a, n, n, o, c, h", "Brannoch"),
            ("B. R. A. N. N. O. C. H.", "Brannoch"),
            ("bee are ay double n oh see aitch", "Brannoch"),
            ("B R A double N O C H", "Brannoch"),
            ("K-A-E-L", "Kael"),
            ("kay ay ee el", "Kael"),
            ("capital K A E L", "Kael"),
            ("K A E L apostrophe T A R", "Kael'tar"),
            ("M I R dash E L L E", "Mir-elle"),
            ("Z E double T A", "Zetta"),
        ]
        for said, word in table:
            with self.subTest(said=said):
                got = parse(said, asked=True)
                self.assertEqual([g.word for g in got], [word])

    def test_stutters_and_capital_i_and_a_are_not_letters(self) -> None:
        for said in ("Then Gary said I I I think so", "We told Gary A B C", "a a a a"):
            with self.subTest(said=said):
                self.assertEqual(SpellingWatcher().note(1, said, 0.0), [])
                self.assertEqual(parse(said), [])

    def test_double_you_is_w(self) -> None:
        for said in ("double you A Y N E", "double u a y n e", "W A Y N E"):
            with self.subTest(said=said):
                self.assertEqual([g.word for g in parse(said, asked=True)], ["Wayne"])

    def test_an_answer_is_mostly_letters_not_a_sentence_with_a_few(self) -> None:
        self.assertEqual(parse("No idea, I see a bee ex", asked=True), [])

    def test_ordinary_sentences_are_never_a_spelling(self) -> None:
        for said in (
            "are you a man or what",
            "I see you are on a quest",
            "oh be quiet you",
            "we need a plan B",
            "he got an A in it",
            "Is he an N.P.C. or a P.C.?",
            "That would be a T.P.K. for sure",
            "an N P C, a B B E G",
        ):
            with self.subTest(said=said):
                self.assertEqual(parse(said), [])

    def test_a_run_of_ambiguous_letter_words_needs_an_ask(self) -> None:
        # Two clear letters and an ambiguous one: believed only after an ask.
        self.assertEqual(parse("K R you"), [])
        self.assertEqual(parse("K R you", asked=True), [])  # "you" at the end is a word
        self.assertEqual(parse("B R"), [])  # too short

    def test_a_named_spelling_is_found(self) -> None:
        self.assertEqual(said_spelling("It's Brannoch"), "Brannoch")  # capital lead-ins too
        self.assertEqual(said_spelling("That's spelled Brannoch"), "Brannoch")
        self.assertEqual(said_spelling("that's Brannoch with two Ns"), "Brannoch")
        self.assertEqual(said_spelling("it's spelled Brannoch"), "Brannoch")
        self.assertEqual(said_spelling("it is Kael-Tar with a K"), "Kael-Tar")
        self.assertIsNone(said_spelling("that's fine"))

    def test_a_with_a_letter_hint_is_applied_or_the_spelling_is_refused(self) -> None:
        self.assertEqual(said_spelling("that's Branoch with two Ns"), "Brannoch")
        self.assertEqual(said_spelling("Cael with a K"), "Kael")
        self.assertEqual(said_spelling("it's Cael with a K"), "Kael")
        self.assertEqual(said_spelling("it's Brannoch with two Ns"), "Brannoch")  # already so
        self.assertIsNone(said_spelling("that's Zorgul with a K"))  # nothing to swap: never guess
        self.assertIsNone(said_spelling("that's Borgul with two Ns"))  # no N to double
        for said in (
            "It's Phil with an F",  # "ph" is one sound: which letter is meant is a guess
            "Cecil with a K",  # two Cs
            "Sasha with a Z",  # two Ss
            "Gigi with a J",  # two Gs
        ):
            with self.subTest(said=said):
                self.assertIsNone(said_spelling(said))

    def test_capitalised_lead_ins_count(self) -> None:
        self.assertEqual(said_spelling("It's Brannoch"), "Brannoch")
        self.assertEqual(said_spelling("That's Branoch with two Ns"), "Brannoch")
        self.assertEqual(said_spelling("It's spelled Brannoch"), "Brannoch")

    def test_chat_acronyms_and_number_words_are_not_names(self) -> None:
        for said in ("B R B", "L O L", "T B H", "W T F", "F I V E", "O M G", "G G"):
            with self.subTest(said=said):
                self.assertEqual(parse(said, asked=True), [])

    def test_asks(self) -> None:
        for said in (
            "how is that spelled?",
            "how do you spell that",
            "how do you spell his name",
            "can you spell it for me",
            "spell that",
            "wait how's that spelled",
            "what's the spelling",
        ):
            with self.subTest(said=said):
                self.assertTrue(is_ask(said))
        self.assertFalse(is_ask("the wizard casts a spell"))

    def test_capitalised_words_skip_the_start_of_a_sentence(self) -> None:
        self.assertEqual(capitalised_words("We met Brannoch in Vale"), ["Brannoch", "Vale"])


class TheWatcher(unittest.TestCase):
    def test_an_answer_after_an_ask_counts(self) -> None:
        w = SpellingWatcher()
        self.assertEqual(w.note(1, "how do you spell that?", 0.0), [])
        (hit,) = w.note(2, "B-R-A-N-N-O-C-H", 8.0)
        self.assertEqual((hit.word, hit.user_id), ("Brannoch", 2))

    def test_an_answer_too_long_after_the_ask_does_not(self) -> None:
        w = SpellingWatcher()
        w.note(1, "how do you spell that?", 0.0)
        self.assertEqual(w.note(2, "B R A N N O C H", 30.0), [])

    def test_a_spelling_on_its_own_counts_straight_after_a_capitalised_word(self) -> None:
        w = SpellingWatcher()
        w.note(1, "We were sent by Brannoch", 0.0)
        (hit,) = w.note(1, "B R A N N O C H", 4.0)
        self.assertEqual(hit.word, "Brannoch")
        same = SpellingWatcher().note(1, "It was Brannoch. B R A N N O C H", 0.0)
        self.assertEqual([h.word for h in same], ["Brannoch"])

    def test_a_spelling_out_of_the_blue_does_not(self) -> None:
        self.assertEqual(SpellingWatcher().note(1, "the A B C of it", 0.0), [])
        w = SpellingWatcher()
        w.note(1, "so we go on", 0.0)
        self.assertEqual(w.note(1, "B R A N N O C H", 3.0), [])

    def test_a_named_spelling_after_an_ask(self) -> None:
        w = SpellingWatcher()
        w.note(1, "how is that spelled", 0.0)
        (hit,) = w.note(2, "it's Brannoch with two Ns", 5.0)
        self.assertEqual(hit.word, "Brannoch")

    def test_the_hit_carries_the_nearby_words(self) -> None:
        w = SpellingWatcher()
        w.note(1, "ask Brenoch about it", 0.0)
        w.note(1, "how do you spell that", 2.0)
        (hit,) = w.note(2, "B R A N N O C H", 5.0)
        self.assertIn("Brenoch", hit.near)

    def test_an_answer_with_a_lead_in_counts(self) -> None:
        w = SpellingWatcher()
        w.note(1, "how do you spell that", 0.0)
        (hit,) = w.note(2, "Uh, it's K-A-E-L", 4.0)
        self.assertEqual(hit.word, "Kael")

    def test_a_spelling_after_a_capitalised_line_counts(self) -> None:
        w = SpellingWatcher()
        w.note(1, "We were sent by Brannoch", 0.0)
        (hit,) = w.note(1, "It's B R A N N O C H", 3.0)
        self.assertEqual(hit.word, "Brannoch")

    def test_forgetting_a_speaker_removes_their_words_and_their_ask(self) -> None:
        w = SpellingWatcher()
        w.note(1, "We were sent by Brannoch", 0.0)
        w.note(1, "how do you spell that", 2.0)
        w.forget(1)
        self.assertEqual(w.note(2, "B R A N N O C H", 4.0), [])  # their ask no longer counts
        w = SpellingWatcher()
        w.note(1, "We were sent by Brannoch", 0.0)
        w.forget(1)
        self.assertEqual(w.note(1, "B R A N N O C H", 3.0), [])  # nor their capitalised line
        w = SpellingWatcher()
        w.note(1, "ask Brenoch about it", 0.0)
        w.forget(1)
        w.note(2, "how do you spell that", 2.0)
        (hit,) = w.note(2, "B R A N N O C H", 4.0)
        self.assertNotIn("Brenoch", hit.near)  # nothing near comes from the one who stopped

    def test_another_speakers_ask_stays_when_someone_else_stops(self) -> None:
        w = SpellingWatcher()
        w.note(1, "how do you spell that", 0.0)
        w.forget(3)
        self.assertEqual(len(w.note(2, "B R A N N O C H", 2.0)), 1)

    def test_an_answered_ask_is_used_once(self) -> None:
        w = SpellingWatcher()
        w.note(1, "how do you spell that", 0.0)
        w.note(2, "B R A N N O C H", 3.0)
        self.assertEqual(w.note(2, "K A E L", 6.0), [])  # no ask, no capitalised word


def table_bot(*, consenting: set[int] | None = None, waiting: list[Any] | None = None) -> Any:
    memory = SimpleNamespace(
        known_keys=AsyncMock(return_value=["vale"]),
        add_entity=AsyncMock(),
        entities=AsyncMock(return_value=waiting or []),
        set_note=AsyncMock(),
    )
    bot = SimpleNamespace(
        memory=memory,
        consent=SimpleNamespace(consenting=AsyncMock(return_value=consenting or {2})),
    )
    for name in ("_note_spelling", "_record_spelled", "suggest_spelled"):
        setattr(bot, name, getattr(DMBot, name).__get__(bot))
    return bot


def a_table(secret: frozenset[str] = frozenset()) -> Any:
    return SimpleNamespace(
        spelling=SpellingWatcher(),
        spelled=[],
        spelled_hints=(),
        held_spelled={},
        held_pronounced={},
        hint_parts=SimpleNamespace(secret=secret),
        guild_id=1,
        campaign_id="c" * 32,
    )


class TheGate(unittest.IsolatedAsyncioTestCase):
    """With the off-topic filter on, a spelled line waits for its label (#1748): game talk gives
    its hint once labelled, off-topic talk gives nothing."""

    async def label(self, topic: str, *, consenting: bool = True) -> Any:
        from dmbot import bot as bot_module
        from dmbot.transcript.topics import Waiting

        fake = table_bot()
        fake.consent.has_consent = lambda _g, _u: consenting
        fake.topic_ai = object()
        fake._track = lambda *_a, **_k: None
        fake._save_topic = AsyncMock()
        fake._hide_lines = AsyncMock()
        for name in ("_label_topics", "_record_spelled"):
            setattr(fake, name, getattr(DMBot, name).__get__(fake))
        table = a_table()
        table.held_spelled = {}
        table.held = {(2, 100): "B R A N N O C H"}
        table.dm_user_ids = set()
        table.topic_calls = 0
        table.topic_tokens = [0, 0]
        table.topic_failures = 0
        table.heard = []
        table.vocabulary = SimpleNamespace(note=lambda *_a: None)
        fake._note_spelling(table, 1, "how do you spell that")
        fake._note_spelling(table, 2, "B R A N N O C H", held_key=(2, 100))
        self.assertEqual((table.spelled, table.spelled_hints), ([], ()))  # held with the line
        reply = SimpleNamespace(input_tokens=0, output_tokens=0)
        with patch.object(bot_module, "classify", AsyncMock(return_value=([topic], reply))):
            await fake._label_topics(table, [Waiting(2, 100, "B R A N N O C H")])
        return table

    async def test_game_talk_gives_its_hint_once_labelled(self) -> None:
        table = await self.label("game")
        self.assertEqual(table.spelled_hints, ("Brannoch",))

    async def test_off_topic_talk_gives_nothing(self) -> None:
        table = await self.label("off_topic")
        self.assertEqual((table.spelled, table.spelled_hints, table.held_spelled), ([], (), {}))

    async def test_someone_who_stopped_agreeing_gives_nothing(self) -> None:
        table = await self.label("game", consenting=False)
        self.assertEqual((table.spelled, table.spelled_hints), ([], ()))


class TheBot(unittest.IsolatedAsyncioTestCase):
    def say(self, bot: Any, table: Any, lines: list[tuple[int, str]]) -> None:
        for uid, text in lines:
            bot._note_spelling(table, uid, text)

    def test_a_spelled_name_is_kept_hinted_and_attached_to_the_word_that_sounds_like_it(
        self,
    ) -> None:
        bot, table = table_bot(), a_table()
        self.say(
            bot, table, [(1, "ask Brenoch"), (1, "how do you spell that"), (2, "B R A N O C H")]
        )
        self.assertEqual(table.spelled, [("Branoch", 2, "Brenoch")])
        self.assertEqual(table.spelled_hints, ("Branoch",))

    def test_a_secret_name_is_never_hinted_or_kept(self) -> None:
        bot, table = table_bot(), a_table(frozenset({"brannoch"}))
        self.say(bot, table, [(1, "how do you spell that"), (2, "B R A N N O C H")])
        self.assertEqual((table.spelled, table.spelled_hints), ([], ()))

    def test_the_same_spelling_is_kept_once_and_the_list_is_bounded(self) -> None:
        bot, table = table_bot(), a_table()
        for _ in range(3):
            self.say(bot, table, [(1, "how do you spell that"), (2, "B R A N N O C H")])
        self.assertEqual(len(table.spelled), 1)
        for i in range(SPELLED_MAX + 5):
            word = "-".join("KAEL"[j % 4] for j in range(3)) + "-" + "".join(chr(97 + i % 26) * 2)
            self.say(bot, table, [(1, "how do you spell that"), (2, word.replace("-", " "))])
        self.assertLessEqual(len(table.spelled), SPELLED_MAX)

    async def test_the_end_of_session_suggests_what_was_spelled_marked_as_such(self) -> None:
        bot, table = table_bot(), a_table()
        self.say(bot, table, [(1, "how do you spell that"), (2, "B R A N N O C H")])
        await bot.suggest_spelled(table)
        kwargs = bot.memory.add_entity.await_args.kwargs
        self.assertEqual(
            (kwargs["name"], kwargs["description"]), ("Brannoch", "Spelled out at the table")
        )
        self.assertEqual(kwargs["source"], "scan")  # a suggestion only: nothing is confirmed
        self.assertEqual(table.spelled, [])

    async def test_someone_who_stopped_agreeing_is_not_suggested(self) -> None:
        bot, table = table_bot(consenting={9}), a_table()
        self.say(bot, table, [(1, "how do you spell that"), (2, "B R A N N O C H")])
        await bot.suggest_spelled(table)
        bot.memory.add_entity.assert_not_awaited()

    async def test_a_spelling_said_next_to_a_pending_suggestion_goes_to_that_suggestion(
        self,
    ) -> None:
        pending = SimpleNamespace(id="e" * 32, name="Brenoch", description="Heard 3 times")
        bot, table = table_bot(waiting=[pending]), a_table()
        said = [(1, "ask Brenoch"), (1, "how do you spell that"), (2, "B R A N N O C H")]
        self.say(bot, table, said)
        await bot.suggest_spelled(table)
        bot.memory.add_entity.assert_not_awaited()  # not a second entry
        bot.memory.set_note.assert_awaited_once()
        self.assertEqual(
            bot.memory.set_note.await_args.args[3], "Spelled out at the table: Brannoch"
        )

    async def test_two_spellings_near_one_suggestion_give_it_one_note(self) -> None:
        pending = SimpleNamespace(id="e" * 32, name="Brenoch", description="Heard 3 times")
        bot, table = table_bot(waiting=[pending]), a_table()
        said = [(1, "ask Brenoch"), (1, "how do you spell that"), (2, "B R A N N O C H")]
        said += [(1, "ask Brenoch"), (1, "how do you spell that"), (2, "B R A N O C H")]
        self.say(bot, table, said)
        self.assertEqual(len(table.spelled), 2)
        await bot.suggest_spelled(table)
        bot.memory.set_note.assert_awaited_once()  # the first spelling stays
        bot.memory.add_entity.assert_not_awaited()

    async def test_a_secret_name_spelled_out_is_never_suggested(self) -> None:
        bot, table = table_bot(), a_table(frozenset({"brannoch"}))
        self.say(bot, table, [(1, "how do you spell that"), (2, "B R A N N O C H")])
        await bot.suggest_spelled(table)
        bot.memory.add_entity.assert_not_awaited()

    async def test_a_name_dmbot_already_has_is_not_suggested(self) -> None:
        bot, table = table_bot(), a_table()
        self.say(bot, table, [(1, "how do you spell that"), (2, "V A L E")])
        await bot.suggest_spelled(table)
        bot.memory.add_entity.assert_not_awaited()

    async def test_a_refusal_skips_that_name_only(self) -> None:
        bot, table = table_bot(), a_table()
        bot.memory.add_entity = AsyncMock(side_effect=[MemoryRuleError("no"), None])
        self.say(bot, table, [(1, "how do you spell that"), (2, "B R A N N O C H")])
        self.say(bot, table, [(1, "how do you spell that"), (2, "K A E L")])
        await bot.suggest_spelled(table)
        self.assertEqual(bot.memory.add_entity.await_count, 2)


class TheHint(unittest.TestCase):
    def test_a_spelled_word_is_a_hint_but_not_a_secret_name(self) -> None:
        names = lookup()
        parts: HintParts = prepare(names, NOW)
        hints = scene_hints(
            names,
            parts,
            SceneTracker(),
            0.0,
            spelled=["Brannoch", "Belleros", "the hooded stranger"],
        )
        self.assertIn("Brannoch", hints)
        self.assertNotIn("the hooded stranger", hints)  # a secret name is never sent

    def test_the_hint_comes_after_the_scene_not_before_the_characters(self) -> None:
        names = lookup()
        hints = scene_hints(names, prepare(names, NOW), SceneTracker(), 0.0, spelled=["Brannoch"])
        self.assertLess(hints.index("Cerric"), hints.index("Brannoch"))
