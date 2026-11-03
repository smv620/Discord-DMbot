"""Learning who answers to which name from "a name, then an answer" (#1188): pure rules,
no Discord and no database. The names are made up."""

from __future__ import annotations

import unittest

from dmbot.memory.table_names import NameThenAnswer, called_names, is_answer

DM, MIA, BEN = 1, 2, 3


def run(*lines: tuple[int, int, str], agreed: set[int] | None = None) -> NameThenAnswer:
    tracker = NameThenAnswer()
    for speaker, at_ms, text in lines:
        tracker.feed(speaker, at_ms, text, consented=agreed is None or speaker in agreed)
    return tracker


class Names(unittest.TestCase):
    def test_a_name_at_the_start_or_end_next_to_a_comma(self) -> None:
        self.assertEqual(called_names("Mike, what do you do?"), ["Mike"])
        self.assertEqual(called_names("Okay, your turn, Cerric."), ["Cerric"])
        self.assertEqual(called_names("Mike Stone, you're up"), ["Mike Stone"])

    def test_ordinary_openers_are_not_names(self) -> None:
        for text in ("Okay, let's go", "So, we walk in", "Well, I think", "Yes, I do"):
            self.assertEqual(called_names(text), [], text)

    def test_the_dm_words_are_never_a_name_to_learn(self) -> None:
        self.assertEqual(called_names("DM, can I roll?"), [])
        self.assertEqual(called_names("Thanks, GM"), [])
        self.assertEqual(called_names("Hey, Dungeon Master"), [])
        self.assertEqual(called_names("Ok, Game Master"), [])

    def test_answers(self) -> None:
        for text in ("I run with my sword", "Ok, I'll do that", "sure", "Cast fireball"):
            self.assertTrue(is_answer(text), text)
        self.assertFalse(is_answer("The door creaks open"))


SAM, KIM = 10, 11  # two DMs; Mia and Ben are players


class DmAddressed(unittest.TestCase):
    """DM words and a DM's own name resolve to the DM who is here (#1547). Made-up names."""

    def tracker(self, dms: dict[int, str]) -> NameThenAnswer:
        t = NameThenAnswer()
        t.set_dms(dms)
        return t

    def test_a_dm_word_means_the_one_dm_who_has_spoken(self) -> None:
        t = self.tracker({SAM: "Sam"})
        t.feed(SAM, 0, "Okay, what do you do?", consented=True)
        t.feed(MIA, 2000, "DM, can I roll?", consented=True)
        self.assertEqual(t.dm_addressed(), {SAM: 1})

    def test_every_dm_word_counts(self) -> None:
        t = self.tracker({SAM: "Sam"})
        t.feed(SAM, 0, "Go ahead.", consented=True)
        for at, text in enumerate(("DM, hi", "Hey, GM", "Dungeon Master, wait", "Ok, Game Master")):
            t.feed(MIA, 1000 * (at + 1), text, consented=True)
        self.assertEqual(t.dm_addressed(), {SAM: 4})

    def test_a_dms_own_name_means_that_dm_and_is_never_learned(self) -> None:
        t = self.tracker({SAM: "Sam"})
        t.feed(MIA, 0, "Sam, what do I see?", consented=True)
        t.feed(SAM, 3000, "I describe the room", consented=True)
        t.feed(MIA, 20_000, "Sam, is it dark?", consented=True)
        t.feed(SAM, 22_000, "Yes, it is", consented=True)
        self.assertEqual(t.dm_addressed(), {SAM: 1})  # the first call came before Sam had spoken
        self.assertEqual(t.answered(), [])  # not a nickname to ask the DM about

    def test_with_two_dms_the_one_who_spoke_last_is_meant(self) -> None:
        t = self.tracker({SAM: "Sam", KIM: "Kim"})
        t.feed(SAM, 0, "Welcome.", consented=True)
        t.feed(KIM, 4000, "And the door opens.", consented=True)
        t.feed(MIA, 6000, "DM, what is that?", consented=True)
        self.assertEqual(t.dm_addressed(), {KIM: 1})
        t.feed(SAM, 7000, "A statue.", consented=True)
        t.feed(MIA, 8000, "GM, can I touch it?", consented=True)
        self.assertEqual(t.dm_addressed(), {KIM: 1, SAM: 1})

    def test_with_two_dms_and_neither_recent_it_means_no_one(self) -> None:
        t = self.tracker({SAM: "Sam", KIM: "Kim"})
        t.feed(SAM, 0, "Welcome.", consented=True)
        t.feed(KIM, 1000, "Sit down.", consented=True)
        t.feed(MIA, 60_000, "DM, can I roll?", consented=True)
        self.assertEqual(t.dm_addressed(), {})

    def test_no_dm_has_spoken_means_no_one(self) -> None:
        t = self.tracker({SAM: "Sam"})
        t.feed(MIA, 0, "DM, can I roll?", consented=True)
        self.assertEqual(t.dm_addressed(), {})

    def test_a_dm_calling_for_the_dm_is_not_counted_for_themselves(self) -> None:
        t = self.tracker({SAM: "Sam"})
        t.feed(SAM, 0, "Sam, you are rambling", consented=True)
        self.assertEqual(t.dm_addressed(), {})

    def test_a_line_from_someone_who_did_not_agree_is_never_read(self) -> None:
        t = self.tracker({SAM: "Sam"})
        t.feed(SAM, 0, "Go on.", consented=True)
        t.feed(MIA, 1000, "DM, can I roll?", consented=False)
        self.assertEqual(t.dm_addressed(), {})

    def test_stop_recording_me_forgets_a_dm_too(self) -> None:
        t = self.tracker({SAM: "Sam"})
        t.feed(SAM, 0, "Go on.", consented=True)
        t.feed(MIA, 1000, "DM, can I roll?", consented=True)
        t.drop_speaker(SAM)
        self.assertEqual(t.dm_addressed(), {})
        t.feed(MIA, 2000, "DM, hello?", consented=True)
        self.assertEqual(t.dm_addressed(), {})  # and they no longer count as here

    def test_a_dms_name_does_not_count_after_they_stopped_recording_or_before_they_spoke(
        self,
    ) -> None:
        t = self.tracker({SAM: "Sam"})
        t.feed(MIA, 0, "Sam, can I roll?", consented=True)  # Sam has not spoken: not counted
        self.assertEqual(t.dm_addressed(), {})
        t.feed(SAM, 1000, "Go on.", consented=True)
        t.drop_speaker(SAM)
        t.feed(MIA, 2000, "Sam, can I roll?", consented=True)
        self.assertEqual(t.dm_addressed(), {})

    def test_a_single_dm_who_spoke_long_ago_is_still_here_and_a_tie_goes_to_the_lower_id(
        self,
    ) -> None:
        one = self.tracker({SAM: "Sam"})
        one.feed(SAM, 0, "Go on.", consented=True)
        self.assertEqual(one.session_dm(MIA, 3_600_000), SAM)
        two = self.tracker({SAM: "Sam", KIM: "Kim"})
        two.feed(SAM, 5000, "Hm.", consented=True)
        two.feed(KIM, 5000, "Hm.", consented=True)
        self.assertEqual(two.session_dm(MIA, 6000), SAM)

    def test_two_dms_with_the_same_name_are_not_told_apart_by_it(self) -> None:
        t = self.tracker({SAM: "Alex", KIM: "alex"})
        t.feed(SAM, 0, "Hm.", consented=True)
        t.feed(KIM, 1000, "Hm.", consented=True)
        t.feed(MIA, 2000, "Alex, can I roll?", consented=True)
        self.assertEqual(t.dm_addressed(), {})
        self.assertEqual(
            t.answered(), []
        )  # (it is not learned as a nickname either: one DM answered)

    def test_ordinary_names_are_learned_as_before(self) -> None:
        t = self.tracker({SAM: "Sam"})
        for at, who, text in (
            (0, SAM, "Mike, what do you do?"),
            (2000, MIA, "I run"),
            (20_000, SAM, "Mike, you're up"),
            (22_000, MIA, "ok, I'll go"),
        ):
            t.feed(who, at, text, consented=True)
        self.assertEqual([(a.name, a.speaker) for a in t.answered()], [("Mike", MIA)])


class Pairs(unittest.TestCase):
    TWICE = (
        (DM, 0, "Mike, what do you do?"),
        (MIA, 3000, "I run with my sword"),
        (DM, 20_000, "Mike, you're up"),
        (MIA, 22_000, "Ok, I'll do that"),
    )

    def test_twice_by_the_same_person_is_worth_asking_about(self) -> None:
        found = run(*self.TWICE).answered()
        self.assertEqual([(a.name, a.speaker, a.times) for a in found], [("Mike", MIA, 2)])

    def test_once_is_not(self) -> None:
        self.assertEqual(run(*self.TWICE[:2]).answered(), [])

    def test_a_third_speaker_in_between_breaks_it(self) -> None:
        tracker = run(
            (DM, 0, "Mike, what do you do?"),
            (BEN, 1000, "Heh"),
            (MIA, 2000, "I run with my sword"),
            (DM, 20_000, "Mike, you're up"),
            (BEN, 21_000, "Hm"),
            (MIA, 22_000, "I run"),
        )
        self.assertEqual(tracker.answered(), [])

    def test_more_than_ten_seconds_later_is_nothing(self) -> None:
        tracker = run(
            (DM, 0, "Mike, what do you do?"),
            (MIA, 10_500, "I run with my sword"),
            (DM, 30_000, "Mike, you're up"),
            (MIA, 41_000, "I run"),
        )
        self.assertEqual(tracker.answered(), [])

    def test_the_same_person_answering_themselves_is_nothing(self) -> None:
        tracker = run(
            (DM, 0, "Mike, up"), (DM, 1000, "I think"), (DM, 5000, "Mike, up"), (DM, 6000, "I")
        )
        self.assertEqual(tracker.answered(), [])

    def test_two_different_people_answering_never_add_up(self) -> None:
        tracker = run(
            (DM, 0, "Mike, what do you do?"),
            (MIA, 1000, "I run"),
            (DM, 20_000, "Mike, what do you do?"),
            (BEN, 21_000, "I run"),
        )
        self.assertEqual(tracker.answered(), [])

    def test_someone_who_did_not_agree_is_never_read_and_breaks_the_pair(self) -> None:
        tracker = run(*self.TWICE, agreed={DM})
        self.assertEqual(tracker.answered(), [])
        tracker = run(
            (DM, 0, "Mike, what do you do?"),
            (BEN, 500, "hmm"),
            (MIA, 1000, "I run"),
            agreed={DM, MIA},
        )
        self.assertEqual(tracker.answered(minimum=1), [])

    def test_stop_recording_me_drops_their_part(self) -> None:
        tracker = run(*self.TWICE)
        tracker.drop_speaker(MIA)
        self.assertEqual(tracker.answered(), [])
        self.assertEqual(
            [a.speaker for a in run(*self.TWICE).answered()], [MIA]
        )  # and the DM's side alone never named anyone

    def test_a_name_only_a_stopped_speaker_answered_is_forgotten_entirely(self) -> None:
        tracker = run(*self.TWICE)
        tracker.drop_speaker(MIA)
        self.assertEqual(tracker._display, {})

    def test_a_line_calling_two_names_counts_both(self) -> None:
        tracker = run(
            (DM, 0, "Mike, you are up, Vera"),
            (MIA, 1000, "I go"),
            (DM, 20_000, "Mike, you are up, Vera"),
            (MIA, 21_000, "I go"),
        )
        self.assertEqual({a.name for a in tracker.answered()}, {"Mike", "Vera"})


if __name__ == "__main__":
    unittest.main()
