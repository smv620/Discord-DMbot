"""Editing a recap after listening (#2630): when the change is small only the voice is made again
(the changed paragraphs; the music is kept), and a bigger change makes everything again, with a
fixed code for why. Progress is counts. Invented names throughout."""

from __future__ import annotations

import re
import unittest
from typing import Any

from dmbot.recaps.audio import parts
from dmbot.recaps.audio import script as sc
from dmbot.recaps.audio.files import AudioFiles  # noqa: F401  (the base builds one)
from tests import recap_fixtures as fx
from tests.test_recap_audio_step import SUMMARY, Base

SMALL = SUMMARY.replace("guttered", "burned")  # one word, one paragraph


def build(summary: str) -> sc.Script:
    return sc.build("The Silver Lock", 4, summary)


def manifest_for(summary: str) -> dict[str, Any]:
    script = build(summary)
    n = len(script.paragraphs)
    return parts.manifest_of(
        script, mode="both", seed=100, seconds=[2.0] * n, cue_at=[[] for _ in range(n)]
    )


class Classifying(unittest.TestCase):
    def classify(self, summary: str, *, mode: str = "both") -> parts.Change:
        return parts.classify(manifest_for(SUMMARY), build(summary), mode=mode, seed=100)

    def test_one_word_in_one_paragraph_is_a_voice_change_of_that_paragraph(self) -> None:
        got = self.classify(SMALL)
        self.assertEqual((got.kind, got.changed, got.total), ("voice", (1,), 3))

    def test_the_same_words_change_nothing(self) -> None:
        self.assertEqual(self.classify(SUMMARY.replace("\n\n", "\n\n\n")).kind, "same")

    def test_an_added_or_removed_paragraph_is_everything(self) -> None:
        added = self.classify(SUMMARY + "\n\nThe door closed.")
        self.assertEqual((added.kind, added.why), ("all", "paragraphs"))
        removed = self.classify(SUMMARY.split("\n\n")[0])
        self.assertEqual((removed.kind, removed.why), ("all", "paragraphs"))

    def test_a_changed_mood_or_pacing_cue_is_everything(self) -> None:
        for edited in (
            SUMMARY.replace("《Hopeful》", "《Eerie》"),
            SUMMARY.replace("((pause))", ""),
        ):
            got = self.classify(edited)
            self.assertEqual((got.kind, got.why), ("all", "moods"), edited)

    def test_more_than_a_tenth_of_the_spoken_text_is_everything(self) -> None:
        long = SUMMARY.replace("and the road opened", "and the long grey road opened " * 4)
        got = self.classify(long)
        self.assertEqual((got.kind, got.why), ("all", "size"))
        small = SUMMARY.replace("and the road opened", "and the road opened wide")
        self.assertEqual(self.classify(small).kind, "voice")  # (under a tenth: still small)

    def test_nothing_kept_or_another_choice_is_everything_and_says_so(self) -> None:
        none = parts.classify(None, build(SUMMARY), mode="both", seed=100)
        self.assertEqual((none.kind, none.why), ("all", "no_parts"))
        other = self.classify(SMALL, mode="voice")
        self.assertEqual((other.kind, other.why), ("all", "no_parts"))

    def test_a_manifest_holds_hashes_and_counts_never_the_words(self) -> None:
        text = str(manifest_for(SUMMARY))
        for word in ("Mira", "torches", "Greywater", "road"):
            self.assertNotIn(word, text)


class Remaking(Base):
    async def first(self) -> None:
        job = await self.worker().run(1, self.one.id, 100)
        assert job is not None
        self.assertEqual(job.audio, "ready")
        self.fake.calls.clear()

    async def edit(self, summary: str) -> None:
        """The DM edits the text, approves it again and presses Make it again."""
        await self.store.update(1, self.one.id, 100, summary=summary)
        await self.store.set_audio(1, self.one.id, 100, audio="off")
        self.assertTrue(await self.approve("both"))

    async def how(self) -> tuple[str, str]:
        async with self.db.guild(1) as conn:
            cur = await conn.execute("SELECT audio_how, audio_why FROM recap_jobs")
            row = await cur.fetchone()
        assert row is not None
        return row["audio_how"], row["audio_why"]

    async def test_the_first_making_is_new_and_keeps_what_a_small_change_needs(self) -> None:
        await self.first()
        self.assertEqual(await self.how(), ("new", ""))
        folder = self.files.recap_dir(1, self.one.id, 100)
        manifest = parts.read_manifest(folder)
        assert manifest is not None
        for number in range(len(manifest["paragraphs"])):
            self.assertTrue(parts.speech_path(folder, number).is_file())
        self.assertTrue((folder / parts.WORK / parts.MUSIC_PIECE).is_file())

    async def test_one_word_changed_remakes_one_voice_call_and_no_music_call(self) -> None:
        await self.first()
        await self.edit(SMALL)
        job = await self.worker().run(1, self.one.id, 100)
        assert job is not None
        self.assertEqual((job.audio, job.audio_text_hash != ""), ("ready", True))
        self.assertEqual(len(self.fake.paths("text-to-speech")), 1)
        self.assertEqual(self.fake.paths("/v1/music"), [])
        self.assertEqual(self.fake.paths("sound-generation"), [])
        self.assertEqual(await self.how(), ("voice", ""))
        self.assertEqual(sorted(self.files.present(1, self.one.id, 100)), ["mix", "music", "voice"])
        sent = [b["text"] for _, b in self.fake.paths("text-to-speech") if b]
        self.assertIn("burned", sent[0])  # exactly the changed paragraph
        # and the mix was made again over the voice
        self.assertEqual(len(self.mixer.runs), 2)

    def tempo_of(self, run: int) -> float:
        graph = self.mixer.runs[run][self.mixer.runs[run].index("-filter_complex") + 1]
        found = re.search(r"atempo=([0-9.]+),", graph)
        return float(found.group(1)) if found else 1.0

    async def test_the_kept_music_is_fitted_to_a_longer_or_shorter_voice_not_cut_or_padded(
        self,
    ) -> None:
        """#2629's ending is composed for the first voice's length: a remake plays the kept piece at
        old/new speed so the ending still lands at the end, and the closing keeps its music."""
        await self.first()
        folder = self.files.recap_dir(1, self.one.id, 100)
        manifest = parts.read_manifest(folder)
        assert manifest is not None
        composed = float(manifest["musicSeconds"])
        self.assertGreater(composed, 0)
        self.assertEqual(self.tempo_of(0), 1.0)  # the first making needs no fitting
        await self.edit(SMALL)
        self.fake.seconds = 3.0  # the changed paragraph is longer than before
        await self.worker().run(1, self.one.id, 100)
        longer = self.mixer.voice_seconds
        self.assertGreater(longer, composed - 1)
        self.assertAlmostEqual(self.tempo_of(1), composed / longer, places=3)
        self.assertLess(self.tempo_of(1), 1.0)  # slowed to last as long as the new voice
        # and the fit is against the composed piece, not the last voice: a shorter one next
        await self.edit(SMALL.replace("rain stopped", "rain eased"))
        self.fake.seconds = 1.0
        await self.worker().run(1, self.one.id, 100)
        self.assertAlmostEqual(self.tempo_of(2), composed / self.mixer.voice_seconds, places=3)
        self.assertGreater(self.tempo_of(2), 1.0)
        self.assertEqual(self.fake.paths("/v1/music"), [])  # still no new music

    async def test_it_costs_only_the_changed_words(self) -> None:
        await self.first()
        spent = (await self.job()).audio_credits
        await self.edit(SMALL)
        before = (await self.job()).audio_credits
        job = await self.worker().run(1, self.one.id, 100)
        assert job is not None
        self.assertLess(job.audio_credits - before, spent / 3)  # (no music, one paragraph)
        self.assertGreater(job.audio_credits, before)

    async def test_a_second_small_change_builds_on_the_first(self) -> None:
        await self.first()
        await self.edit(SMALL)
        await self.worker().run(1, self.one.id, 100)
        self.fake.calls.clear()
        await self.edit(SMALL.replace("rain stopped", "rain eased"))
        await self.worker().run(1, self.one.id, 100)
        self.assertEqual(len(self.fake.paths("text-to-speech")), 1)
        self.assertEqual(await self.how(), ("voice", ""))

    async def test_an_added_paragraph_makes_all_the_audio_again_and_says_why(self) -> None:
        await self.first()
        await self.edit(SUMMARY + "\n\nThe door closed.")
        job = await self.worker().run(1, self.one.id, 100)
        assert job is not None
        self.assertEqual(job.audio, "ready")
        self.assertEqual(len(self.fake.paths("text-to-speech")), 4)  # the title and 3 paragraphs
        self.assertEqual(len(self.fake.paths("/v1/music")), 1)
        self.assertEqual(await self.how(), ("all", "paragraphs"))

    async def test_a_bigger_change_and_a_changed_mood_make_everything_again(self) -> None:
        await self.first()
        await self.edit(SUMMARY.replace("《Hopeful》", "《Eerie》"))
        await self.worker().run(1, self.one.id, 100)
        self.assertEqual(await self.how(), ("all", "moods"))
        self.assertEqual(len(self.fake.paths("/v1/music")), 1)

    async def test_words_the_voice_never_reads_differently_make_nothing(self) -> None:
        await self.first()
        await self.edit(SUMMARY.replace("\n\n", "\n\n\n"))
        job = await self.worker().run(1, self.one.id, 100)
        assert job is not None
        self.assertEqual(job.audio, "ready")
        self.assertEqual(self.fake.calls, [])
        self.assertEqual(await self.how(), ("same", ""))

    async def test_make_it_again_on_the_same_text_is_a_new_take_of_everything(self) -> None:
        await self.first()
        await self.store.set_audio(1, self.one.id, 100, audio="queued", tries=0, next_at=0)
        await self.worker().run(1, self.one.id, 100)
        self.assertEqual(len(self.fake.paths("/v1/music")), 1)
        self.assertEqual(await self.how(), ("all", ""))

    async def test_saved_speech_that_is_gone_starts_over_whole(self) -> None:
        await self.first()
        folder = self.files.recap_dir(1, self.one.id, 100)
        parts.speech_path(folder, 0).unlink()
        await self.edit(SMALL)
        job = await self.worker().run(1, self.one.id, 100)
        assert job is not None
        self.assertEqual(job.audio, "ready")
        self.assertEqual(len(self.fake.paths("/v1/music")), 1)
        self.assertEqual(await self.how(), ("all", "no_parts"))

    async def test_consent_and_secrets_are_checked_again_for_a_voice_remake(self) -> None:
        await self.first()
        await self.edit(SMALL)
        self.agreed = {fx.DM}  # a player left
        job = await self.worker().run(1, self.one.id, 100)
        assert job is not None
        self.assertEqual(job.audio, "failed")
        self.assertEqual(self.fake.calls, [])

    async def test_progress_counts_the_parts_being_remade(self) -> None:
        await self.first()
        await self.edit(SMALL)
        seen: list[tuple[str, int, int]] = []
        real = self.store.set_audio_progress

        async def spy(g: int, c: str, at: int, *, stage: str, done: int, total: int) -> None:
            seen.append((stage, done, total))
            await real(g, c, at, stage=stage, done=done, total=total)

        self.store.set_audio_progress = spy  # type: ignore[method-assign,assignment]
        await self.worker().run(1, self.one.id, 100)
        self.assertEqual(seen, [("voice", 0, 1), ("voice", 1, 1), ("mix", 0, 1)])
        async with self.db.guild(1) as conn:  # it ends with the making
            cur = await conn.execute("SELECT audio_stage, audio_done, audio_total FROM recap_jobs")
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual((row["audio_stage"], row["audio_done"], row["audio_total"]), ("", 0, 0))

    async def test_a_whole_making_counts_every_paragraph_then_the_music(self) -> None:
        seen: list[tuple[str, int, int]] = []
        real = self.store.set_audio_progress

        async def spy(g: int, c: str, at: int, *, stage: str, done: int, total: int) -> None:
            seen.append((stage, done, total))
            await real(g, c, at, stage=stage, done=done, total=total)

        self.store.set_audio_progress = spy  # type: ignore[method-assign,assignment]
        await self.worker().run(1, self.one.id, 100)
        self.assertEqual(
            seen,
            [("voice", 0, 3), ("voice", 1, 3), ("voice", 2, 3), ("voice", 3, 3),
             ("music", 0, 1), ("mix", 0, 1)],
        )  # fmt: skip
