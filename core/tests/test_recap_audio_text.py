"""The recap audio's provider-free parts (#1413): script and cue map, pronunciation rules, the
music and ambience requests and the mix plan. No network, no database."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest

from dmbot.recaps.audio import mix, music, names, script
from dmbot.recaps.audio.script import EMOTION, MAX_CHARS, PERFORMANCE_KIND

SUMMARY = (
    "《Tense》 The party crept into the cave ((pause)) and the torches guttered, "
    "((slow)) one by one.\n\n"
    "《whisper》 Mira bent low ((hushed)) and spoke of the old door.\n\n"
    "* * *\n\n"
    "《Hopeful》 At dawn the rain stopped. Garrick laughed, ((long pause)) and the road opened."
)
SECRET = "Vaelithor"


def built() -> script.Script:
    return script.build("The Broken Door", 12, SUMMARY)


class Narration(unittest.TestCase):
    def test_the_title_is_read_first_with_its_day_and_never_the_housekeeping(self) -> None:
        s = built()
        self.assertEqual(s.paragraphs[0].spoken, "Session Summary: The Broken Door, Day 12")
        self.assertEqual(script.title_line("Quiet", None), "Session Summary: Quiet")
        # Housekeeping is not an input of `build`, so no bullet can be in the script.
        self.assertNotIn("Mechanical", s.spoken)

    def test_no_cue_is_ever_in_the_spoken_or_the_sent_text(self) -> None:
        s = built()
        for p in s.paragraphs:
            for text in (p.spoken, p.tagged):
                for mark in ("《", "》", "((", "))"):
                    self.assertNotIn(mark, text)
        self.assertNotIn("Tense", s.spoken)
        self.assertNotIn("hushed", s.spoken)

    def test_cues_become_the_voices_tags_where_one_fits(self) -> None:
        s = built()
        tagged = " ".join(p.tagged for p in s.paragraphs)
        self.assertIn("[short pause]", tagged)  # ((pause))
        self.assertIn("[whispers]", tagged)  # 《whisper》 and ((hushed))
        self.assertIn("[long pause]", tagged)  # ((long pause)) and the scene break
        self.assertNotIn("[tense]", tagged)  # no tag fits: only the music follows it
        self.assertNotIn("slow", tagged)

    def test_the_scene_break_ends_its_paragraph_with_a_long_pause(self) -> None:
        s = built()
        mira = next(p for p in s.paragraphs if p.spoken.startswith("Mira"))
        self.assertTrue(mira.tagged.endswith("[long pause]"))
        self.assertNotIn("* * *", s.spoken)

    def test_every_cues_offset_points_at_the_words_it_came_before(self) -> None:
        s = built()
        by_name = {c.name: c for c in s.cues}
        self.assertTrue(s.spoken[by_name["tense"].offset :].startswith("The party crept"))
        self.assertTrue(s.spoken[by_name["whisper"].offset :].startswith("Mira bent"))
        self.assertTrue(s.spoken[by_name["hopeful"].offset :].startswith("At dawn"))
        pause = by_name["pause"]
        self.assertTrue(s.spoken[pause.offset :].startswith("and the torches"))
        self.assertEqual(pause.kind, PERFORMANCE_KIND)
        # And in the sent text, where the voice's timestamps count the tag too.
        para = next(p for p in s.paragraphs if p.spoken.startswith("The party"))
        cue = next(c for c in para.cues if c.name == "pause")
        self.assertTrue(para.tagged[cue.tagged_offset :].startswith("[short pause] and"))

    def test_taking_a_cue_out_leaves_no_double_space_or_space_before_punctuation(self) -> None:
        s = script.build(
            "T", None, "《Calm》 It was quiet ((pause)), very quiet. ((slow)) Then it ended."
        )
        spoken = s.paragraphs[1].spoken
        self.assertEqual(spoken, "It was quiet, very quiet. Then it ended.")
        for cue in s.paragraphs[1].cues:
            self.assertLessEqual(cue.offset - s.paragraphs[1].start, len(spoken))

    def test_neighbours_are_given_and_the_first_and_last_have_one_side(self) -> None:
        s = built()
        self.assertEqual(s.paragraphs[0].previous_text, "")
        self.assertEqual(s.paragraphs[-1].next_text, "")
        self.assertEqual(s.paragraphs[1].previous_text, s.paragraphs[0].spoken)
        self.assertTrue(s.paragraphs[1].next_text.startswith("Mira"))

    def test_a_long_paragraph_is_split_under_the_request_limit_at_sentence_ends(self) -> None:
        sentence = "The wind rose over the hills and the party walked on. "
        s = script.build("Long", None, "《Calm》 " + sentence * 200)
        self.assertGreater(len(s.paragraphs), 2)
        for p in s.paragraphs:
            self.assertLessEqual(len(p.tagged), MAX_CHARS)
        self.assertTrue(all(p.spoken.endswith(".") for p in s.paragraphs[1:]))
        joined = " ".join(p.spoken for p in s.paragraphs[1:])
        self.assertEqual(joined.count("walked on"), 200)

    def test_emotion_cues_are_listed_in_order(self) -> None:
        self.assertEqual([c.name for c in built().emotions], ["tense", "whisper", "hopeful"])
        self.assertTrue(all(c.kind == EMOTION for c in built().emotions))


class Edges(unittest.TestCase):
    def test_a_cue_at_the_end_of_a_paragraph_sits_at_its_end_not_past_it(self) -> None:
        s = script.build("T", None, "《Calm》 Next. ((pause))")
        para = s.paragraphs[1]
        cue = next(c for c in para.cues if c.name == "pause")
        self.assertEqual(cue.offset - para.start, len(para.spoken))
        self.assertEqual(cue.tagged_offset, para.tagged.index("[short pause]"))

    def test_a_scene_break_at_the_length_limit_still_fits_one_request(self) -> None:
        s = script.build("T", None, "w" * (MAX_CHARS - 20) + "\n\n***\n\nnext")
        for p in s.paragraphs:
            self.assertLessEqual(len(p.tagged), MAX_CHARS)
        self.assertTrue(s.paragraphs[1].tagged.endswith("[long pause]"))

    def test_the_split_path_keeps_the_tags_and_the_cues_in_the_part_they_fall_in(self) -> None:
        sentence = "The wind rose over the hills ((pause)) and the party walked on. "
        text = "《Calm》 " + sentence * 100 + "《Hopeful》 Then dawn came."
        s = script.build("T", None, text)
        self.assertGreater(len(s.paragraphs), 2)
        for p in s.paragraphs:
            self.assertLessEqual(len(p.tagged), MAX_CHARS)
            for cue in p.cues:
                self.assertTrue(cue.tag == "" or cue.tag in p.tagged)
        self.assertEqual(len([c for c in s.cues if c.name == "pause"]), 100)
        self.assertEqual([c.name for c in s.emotions], ["calm", "hopeful"])
        hopeful = next(c for c in s.emotions if c.name == "hopeful")
        self.assertTrue(s.spoken[hopeful.offset :].startswith("Then dawn"))

    def test_a_secret_name_in_the_text_is_refused_before_anything_is_built(self) -> None:
        with self.assertRaises(script.SecretInScript):
            script.build("T", None, f"《Calm》 {SECRET}'s keep fell.", secret_names=[SECRET])
        with self.assertRaises(script.SecretInScript):
            script.build(f"The {SECRET}-gate", None, "《Calm》 Quiet.", secret_names=[SECRET])
        script.build("T", None, "《Calm》 Quiet.", secret_names=[SECRET])  # absent: fine


class SpacedCues(unittest.TestCase):
    def test_spaces_inside_a_pacing_cue_are_folded(self) -> None:
        tagged = script.build(
            "T", None, "《Calm》 One ((long   pause)) two ((Short\tPause)) three."
        )
        text = " ".join(p.tagged for p in tagged.paragraphs)
        self.assertIn("[long pause]", text)
        self.assertIn("[short pause]", text)


class Pronunciation(unittest.TestCase):
    def test_a_rule_for_each_confirmed_name_with_a_spelling_and_none_for_the_rest(self) -> None:
        got = names.rules(
            [
                names.SpokenName("Ka'zeth", said_as="Kah-zeth"),
                names.SpokenName("Garrick"),  # no spelling: the voice says it fine
                names.SpokenName("Drizzt Quillen", said_as="Drizzt Quillen"),  # the same
            ]
        )
        self.assertEqual([(r.string_to_replace, r.alias) for r in got], [("Ka'zeth", "Kah-zeth")])

    def test_secret_unconfirmed_and_person_names_never_get_a_rule(self) -> None:
        got = names.rules(
            [
                names.SpokenName(SECRET, said_as="Vay-lith-or", secret=True),
                names.SpokenName("Maybe", said_as="May-bee", confirmed=False),
                names.SpokenName("Sam Player", said_as="Sam", person=True),
                names.SpokenName("Okay", said_as="Oh-kay"),
            ]
        )
        self.assertEqual([r.string_to_replace for r in got], ["Okay"])

    def test_a_name_that_holds_a_secret_name_is_left_out_too(self) -> None:
        got = names.rules(
            [
                names.SpokenName(SECRET, secret=True),
                names.SpokenName("The Tomb of Vaelithor", said_as="The Tomb of Vay-lith-or"),
                names.SpokenName("Hall", said_as="Haul the vaelithor"),  # in the spelling
                names.SpokenName("Vaelithor's Keep", said_as="Vay Keep"),  # a possessive
                names.SpokenName("Vaelithor-Keep", said_as="Vay Keep"),  # hyphen-joined
                names.SpokenName("Vaelithors", said_as="Vay"),  # a plural
                names.SpokenName("Keep", said_as="Vaelithor's"),  # in the spelling
                names.SpokenName("Bree", said_as="Bree-ah"),
            ]
        )
        self.assertEqual([r.string_to_replace for r in got], ["Bree"])

    def test_the_request_has_the_alias_shape_and_no_secret_text(self) -> None:
        body = names.dictionary_request(
            "My campaign",
            [
                names.SpokenName(SECRET, said_as="x", secret=True),
                names.SpokenName("Ka'zeth", said_as="Kah-zeth"),
            ],
        )
        self.assertEqual(
            body["rules"],
            [{"string_to_replace": "Ka'zeth", "type": "alias", "alias": "Kah-zeth"}],
        )
        self.assertNotIn(SECRET, json.dumps(body))

    def test_duplicates_control_characters_and_the_cap(self) -> None:
        many = [names.SpokenName(f"Name{i}", said_as=f"Nay-{i}") for i in range(300)]
        self.assertEqual(len(names.rules(many)), names.MAX_RULES)
        twice = names.rules(
            [names.SpokenName("Ka'zeth", said_as="A"), names.SpokenName("KA'ZETH", said_as="B")]
        )
        self.assertEqual(len(twice), 1)
        clean = names.rules([names.SpokenName("Ka\x00zeth", said_as="Kah\nzeth")])
        self.assertEqual((clean[0].string_to_replace, clean[0].alias), ("Ka zeth", "Kah zeth"))


class Music(unittest.TestCase):
    def test_one_section_for_each_run_of_a_mood_adding_up_to_the_length(self) -> None:
        s = built()
        plan = music.composition_request(s)
        sections = plan["composition_plan"]["sections"]
        # The title is read before the first cue: a calm opening, then the three moods.
        self.assertEqual(
            [x["section_name"] for x in sections],
            ["Calm", "Tense", "Calm", "Hopeful", "Hopeful ending"],
        )  # "whisper" has no table entry: the default style
        total = sum(x["duration_ms"] for x in sections)
        self.assertEqual(total, music.total_seconds(len(s.spoken)) * 1000)
        self.assertGreaterEqual(total, 180_000)
        self.assertLessEqual(total, 600_000)
        self.assertTrue(all(x["duration_ms"] >= 3000 for x in sections))

    def test_the_same_mood_twice_in_a_row_is_one_section(self) -> None:
        s = script.build("T", None, "《Tense》 One two three.\n\n《tense》 Four five six.")
        names_ = [
            x["section_name"] for x in music.composition_request(s)["composition_plan"]["sections"]
        ]
        self.assertEqual(
            names_, ["Calm", "Tense", "Tense ending"]
        )  # the title, one run of Tense, its ending

    def test_text_before_the_first_cue_is_a_calm_opening(self) -> None:
        s = script.build("T", None, "Before any cue. 《Battle》 Then swords.")
        parts = music.sections(s)
        self.assertEqual(parts[0].emotion, "calm")

    def test_a_long_run_is_split_under_the_providers_longest_section(self) -> None:
        s = script.build("T", None, "《Tense》 " + "word " * 8000)
        parts = music.sections(s)
        self.assertTrue(all(3000 <= p.duration_ms <= music.MAX_SECTION_MS for p in parts))
        self.assertEqual(sum(p.duration_ms for p in parts), 600_000)
        self.assertEqual({p.emotion for p in parts}, {"calm", "tense"})

    def test_synonyms_are_one_run_and_too_many_moods_keep_the_first_and_the_last(self) -> None:
        s = script.build("T", None, "《Tense》 One two three.\n\n《tension》 Four five six.")
        self.assertEqual(
            [p.emotion for p in music.sections(s)], ["calm", "tense", "tense"]
        )  # the last is its ending
        moods = ["tense", "somber", "hopeful", "eerie", "urgent", "tender", "dread", "wonder",
                 "playful", "battle", "grim", "defiant"]  # fmt: skip
        text = "\n\n".join(f"《{m}》 " + "word " * (5 + i) for i, m in enumerate(moods))
        built_ = script.build("T", None, text)
        many = music.sections(built_)
        self.assertLessEqual(len(many), music.MAX_SECTIONS)
        self.assertEqual(many[-1].emotion, "defiant")  # the closing mood is kept
        self.assertEqual(
            sum(p.duration_ms for p in many), music.total_seconds(len(built_.spoken)) * 1000
        )

    def test_the_request_is_instrumental_with_a_positive_and_negative_list_for_every_mood(
        self,
    ) -> None:
        req = music.composition_request(built())
        self.assertIs(req["force_instrumental"], True)
        plan = req["composition_plan"]
        for section in plan["sections"]:
            self.assertTrue(section["positive_local_styles"])
            self.assertTrue(section["negative_local_styles"])
        self.assertIn("orchestral", plan["positive_global_styles"])

    def test_a_long_script_is_capped_at_ten_minutes_and_a_short_one_lifted_to_three(self) -> None:
        self.assertEqual(music.total_seconds(10), 180)
        self.assertEqual(music.total_seconds(10**6), 600)
        long = script.build("T", None, "《Calm》 " + "Words go on. " * 3000)
        total = sum(x.duration_ms for x in music.sections(long))
        self.assertEqual(total, 600_000)

    def test_every_table_entry_and_synonym_is_valid_and_names_nothing_protected(self) -> None:
        for emotion, (pos, neg) in music.STYLES.items():
            self.assertTrue(pos and neg, emotion)
        for synonym, target in music.SYNONYMS.items():
            self.assertIn(target, music.STYLES, synonym)
        every = " ".join(
            [
                *music.GLOBAL_POSITIVE,
                *music.GLOBAL_NEGATIVE,
                *(w for pos, neg in music.STYLES.values() for w in (*pos, *neg)),
                *(prompt for _, prompt in music.SCENES.values()),
            ]
        ).lower()
        for banned in music.DENY:
            self.assertNotIn(banned, every)

    def test_a_secret_name_is_in_none_of_the_music_or_ambience_requests(self) -> None:
        # Even if a model put it in a cue: only table words come out.
        s = script.build(
            "T", None, f"《{SECRET}》 {SECRET} walked into the tavern ((the {SECRET} cave))."
        )
        blob = json.dumps(
            [music.composition_request(s), [a.as_request() for a in music.ambience_requests(s)]]
        )
        self.assertNotIn(SECRET.lower(), blob.lower())

    def test_ambience_is_one_looping_prompt_for_each_scene_in_order_of_appearance(self) -> None:
        s = script.build(
            "T",
            None,
            "《Calm》 The tavern was loud. Later they hid in a cave while the rain fell, "
            "and the tavern again.",
        )
        got = music.ambience_requests(s)
        self.assertEqual([a.kind for a in got], ["tavern", "cave", "rain"])
        for a in got:
            req = a.as_request()
            self.assertIs(req["loop"], True)
            self.assertTrue(20 <= a.duration_seconds <= 30)
        self.assertEqual(
            music.ambience_requests(script.build("T", None, "《Calm》 Plain words.")), []
        )

    def test_a_word_that_only_starts_like_a_scene_word_is_not_one(self) -> None:
        text = "《Calm》 The campaign went on. The public warned of an inner war. Warm rainbow."
        kinds = [a.kind for a in music.ambience_requests(script.build("T", None, text))]
        self.assertEqual(kinds, ["battle"])  # only "war" itself

    def test_the_performance_cues_count_as_setting_words(self) -> None:
        s = script.build("T", None, "《Calm》 They rested. ((campfire)) The night went on.")
        self.assertEqual([a.kind for a in music.ambience_requests(s)], ["camp"])


class Mixing(unittest.TestCase):
    def test_the_graph_names_the_three_levels_the_duck_and_the_length(self) -> None:
        graph = mix.plan(215.5).filter_complex
        self.assertIn("loudnorm=I=-16:TP=-1", graph)
        self.assertIn("loudnorm=I=-32", graph)
        self.assertEqual(graph.count("aresample=44100"), 4)  # loudnorm's 192 kHz is brought down
        self.assertIn("loudnorm=I=-38", graph)
        self.assertIn("sidechaincompress", graph)
        self.assertIn("ratio=4", graph)
        self.assertEqual(graph.count("atrim=0:215.500"), 3)  # all three are the voice's length
        self.assertEqual(graph.count("apad=whole_dur=215.500"), 3)
        for label in ("[voice_out]", "[bed_out]", "[mix_out]"):
            self.assertIn(label, graph)

    def test_the_command_maps_the_two_stems_and_the_mix_and_loops_the_ambience(self) -> None:
        p = mix.plan(60)
        args = mix.command(
            p, voice="v.mp3", music="m.mp3", ambience="a.mp3",
            out_voice="voice.mp3", out_bed="bed.mp3", out_mix="mix.mp3",
        )  # fmt: skip
        self.assertEqual(args[:2], ["ffmpeg", "-y"])
        self.assertEqual(args.count("-i"), 3)
        self.assertEqual(
            args[args.index("a.mp3") - 3 : args.index("a.mp3")], ["-stream_loop", "-1", "-i"]
        )
        self.assertEqual(
            [args[i + 1] for i, a in enumerate(args) if a == "-map"],
            ["[voice_out]", "[bed_out]", "[mix_out]"],
        )

    def test_without_music_or_ambience_the_inputs_are_numbered_to_match(self) -> None:
        no_music = mix.plan(60, with_music=False).filter_complex
        self.assertIn("[1:a]loudnorm=I=-38", no_music)  # the ambience is now input 1
        self.assertNotIn("sidechaincompress", no_music)
        voice_only = mix.plan(60, with_music=False, with_ambience=False).filter_complex
        self.assertIn("anullsrc", voice_only)
        self.assertNotIn("[1:a]", voice_only)

    def test_the_mix_is_held_at_the_voices_ceiling(self) -> None:
        # The mix is levelled again to the voice's target with the same ceiling.
        self.assertTrue(mix.plan(10).filter_complex.count("loudnorm=I=-16:TP=-1") == 2)

    def test_tracks_that_do_not_match_the_plan_are_refused(self) -> None:
        p = mix.plan(10, with_music=False)
        with self.assertRaises(ValueError):
            mix.command(
                p, voice="v", music="m", ambience="a", out_voice="1", out_bed="2", out_mix="3"
            )

    @unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg is not installed")
    def test_the_graph_runs_in_every_combination_and_keeps_the_voices_length(self) -> None:
        for music_on in (True, False):
            for ambience_on in (True, False):
                with tempfile.TemporaryDirectory() as tmp:
                    p = mix.plan(3, with_music=music_on, with_ambience=ambience_on)
                    args = mix.command(
                        p, voice="", music="m" if music_on else None,
                        ambience="a" if ambience_on else None,
                        out_voice=f"{tmp}/v.wav", out_bed=f"{tmp}/b.wav", out_mix=f"{tmp}/x.wav",
                    )  # fmt: skip
                    lavfi = ["-f", "lavfi", "-i"]
                    inputs = [*lavfi, "sine=f=220:d=3"]
                    if music_on:
                        inputs += [*lavfi, "anoisesrc=d=2:c=pink:a=0.2"]
                    if ambience_on:
                        inputs += [*lavfi, "sine=f=110:d=1"]
                    cut = args.index("-filter_complex")
                    argv = [*args[:2], "-loglevel", "error", *inputs, *args[cut:]]
                    done = subprocess.run(argv, capture_output=True, text=True, check=False)
                    self.assertEqual(done.returncode, 0, done.stderr)
                    probe = subprocess.run(
                        ["ffprobe", "-v", "error", "-of", "default=nw=1",
                         "-show_entries", "stream=sample_rate:format=duration", f"{tmp}/x.wav"],
                        capture_output=True, text=True, check=False,
                    )  # fmt: skip
                    self.assertIn("sample_rate=44100", probe.stdout)
                    seconds = float(probe.stdout.split("duration=")[1].split()[0])
                    self.assertAlmostEqual(seconds, 3.0, delta=0.1)

    def test_the_music_and_the_ambience_close_with_a_fade_to_the_voices_end(self) -> None:
        graph = mix.plan(20).filter_complex
        self.assertEqual(graph.count("afade=t=out:st=18.500:d=1.500"), 2)  # music and ambience
        crescendo = mix.plan(20, ending="crescendo").filter_complex
        self.assertEqual(crescendo.count("afade=t=out:st=19.700:d=0.300"), 2)

    def test_the_last_music_section_is_an_ending_in_the_style_of_the_last_mood(self) -> None:
        for mood, how in (("somber", music.DIMINUENDO), ("battle", music.CRESCENDO)):
            built_ = script.build("T", None, f"Before. 《{mood}》 " + "word " * 60)
            last = music.sections(built_)[-1]
            self.assertTrue(last.ending)
            self.assertEqual(music.ending_of(built_), how)
            asked = music.composition_request(built_)["composition_plan"]
            self.assertTrue(asked["sections"][-1]["section_name"].endswith("ending"))
            self.assertIn("abrupt stop", asked["sections"][-1]["negative_local_styles"])

    @unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg is not installed")
    def test_the_bed_is_far_quieter_in_its_last_moments_than_in_its_loudest(self) -> None:
        def rms(raw: bytes) -> float:
            samples = [
                int.from_bytes(raw[i : i + 2], "little", signed=True) for i in range(0, len(raw), 2)
            ]
            return (sum(x * x for x in samples) / max(1, len(samples))) ** 0.5

        with tempfile.TemporaryDirectory() as tmp:
            p = mix.plan(6, with_ambience=False)
            args = mix.command(
                p, voice="", music="m", ambience=None,
                out_voice=f"{tmp}/v.wav", out_bed=f"{tmp}/b.wav", out_mix=f"{tmp}/x.wav",
            )  # fmt: skip
            lavfi = ["-f", "lavfi", "-i"]
            inputs = [*lavfi, "sine=f=220:d=6", *lavfi, "anoisesrc=d=8:c=pink:a=0.3"]
            cut = args.index("-filter_complex")
            argv = [*args[:2], "-loglevel", "error", *inputs, *args[cut:]]
            done = subprocess.run(argv, capture_output=True, text=True, check=False)
            self.assertEqual(done.returncode, 0, done.stderr)
            pcm = subprocess.run(
                ["ffmpeg", "-loglevel", "error", "-i", f"{tmp}/b.wav", "-f", "s16le",
                 "-ac", "1", "-ar", "8000", "-"],
                capture_output=True, check=True,
            ).stdout  # fmt: skip
            window = 800  # 0.1 s of samples at 8 kHz
            loud = max(
                rms(pcm[i : i + window * 2]) for i in range(0, len(pcm) - window * 2, window * 2)
            )
            self.assertLess(rms(pcm[-window * 2 :]), loud / 4)


if __name__ == "__main__":
    unittest.main()


class TheShortestRecap(unittest.TestCase):
    """A recap of a short session (180 words, #2013) still has music: at least one section, each
    at least the provider's shortest."""

    def test_a_180_word_recap_makes_at_least_one_music_section(self) -> None:
        summary = "《Tense》 " + " ".join(["calm"] * 90) + " ((slow)) " + " ".join(["calm"] * 90)
        built_script = script.build("A Short One", None, summary)
        plan = music.composition_request(built_script)["composition_plan"]["sections"]
        self.assertGreaterEqual(len(plan), 1)
        self.assertTrue(all(s["duration_ms"] >= 3000 for s in plan))
