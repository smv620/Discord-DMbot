"""The recap's narration and music (#1383): the provider's calls through a fake transport, the file
store and its signed links, the renderer with a fake mixer, and the audio worker against a real
database: the off path, the plan, consent, secrets, the cap, the retry and the dictionary."""

from __future__ import annotations

import asyncio
import base64
import json
import math
import os
import tempfile
import unittest
import wave
from collections.abc import Collection, Mapping
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

from dmbot.campaigns.store import CampaignStore
from dmbot.recaps import pipeline as pl
from dmbot.recaps.approval import text_hash
from dmbot.recaps.audio import names as nm
from dmbot.recaps.audio import script as sc
from dmbot.recaps.audio.eleven import (
    SPEECH_RATE,
    ElevenLabs,
    Locator,
    ProviderError,
)
from dmbot.recaps.audio.files import FILE_NAMES, KEEP_DAYS, MIX, MUSIC, VOICE, AudioFiles
from dmbot.recaps.audio.render import (
    CLOSING_GAP_SECONDS,
    GAP_SECONDS,
    HEAD_SECONDS,
    TAIL_SECONDS,
    Renderer,
)
from dmbot.recaps.audio.worker import (
    NO_MIXER,
    NOT_INCLUDED,
    OVER_CAP,
    SECRET,
    STOPPED,
    AudioInputs,
    AudioWorker,
)
from dmbot.recaps.store import RecapStore
from tests import recap_fixtures as fx
from tests.pg import DatabaseTest

NOW = 1_800_000_000
SECRET_NAME = "Vaelithor"
SUMMARY = (
    "《Tense》 Mira crept into Greywater Keep ((pause)) and the torches guttered.\n\n"
    "《Hopeful》 At dawn the rain stopped, and the road opened."
)


def reply(text: str, seconds: float = 2.0) -> bytes:
    """What the voice call answers: PCM of a known length, with a time for every character."""
    count = len(text)
    pcm = b"\x01\x00" * round(seconds * SPEECH_RATE)
    step = seconds / max(1, count)
    align = {
        "characters": list(text),
        "character_start_times_seconds": [i * step for i in range(count)],
        "character_end_times_seconds": [(i + 1) * step for i in range(count)],
    }
    return json.dumps({"audio_base64": base64.b64encode(pcm).decode(), "alignment": align}).encode()


class Fake:
    """A pretend voice company: remembers every request, answers by path."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, Mapping[str, Any] | None]] = []
        self.fail: dict[str, int] = {}  # a path word -> the status to answer
        self.seconds = 2.0

    async def __call__(
        self, method: str, path: str, body: Mapping[str, Any] | None
    ) -> tuple[int, bytes]:
        self.calls.append((method, path, body))
        for word, status in self.fail.items():
            if word in path:
                return status, b"{}"
        if "text-to-speech" in path:
            assert body is not None
            return 200, reply(str(body["text"]), self.seconds)
        if "/v1/music" in path:
            return 200, b"mp3-music"
        if "sound-generation" in path:
            return 200, b"mp3-ambience"
        if "pronunciation-dictionaries" in path:
            return 200, json.dumps({"id": "dict1", "version_id": "v1"}).encode()
        return 404, b"{}"

    def paths(self, word: str) -> list[tuple[str, Mapping[str, Any] | None]]:
        return [(p, b) for _, p, b in self.calls if word in p]

    def everything_sent(self) -> str:
        return json.dumps([b for _, _, b in self.calls], ensure_ascii=False)


class TheCalls(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.fake = Fake()
        self.eleven = ElevenLabs(
            self.fake, voice_id="voice123", speech_model="eleven_multilingual_v2"
        )

    async def test_speech_asks_for_timestamps_pcm_the_neighbours_a_seed_and_the_dictionary(
        self,
    ) -> None:
        got = await self.eleven.speech(
            "[whispers] Hello there.",
            previous_text="Before.",
            next_text="After.",
            seed=2**40 + 5,
            dictionary=Locator("d1", "v9"),
        )
        ((path, body),) = self.fake.paths("text-to-speech")
        self.assertIn("/v1/text-to-speech/voice123/with-timestamps", path)
        self.assertIn("output_format=pcm_24000", path)
        assert body is not None
        self.assertEqual(body["text"], "[whispers] Hello there.")
        self.assertEqual((body["previous_text"], body["next_text"]), ("Before.", "After."))
        self.assertEqual(body["seed"], 5)  # inside the provider's range
        self.assertEqual(body["model_id"], "eleven_multilingual_v2")
        self.assertEqual(
            body["pronunciation_dictionary_locators"],
            [{"pronunciation_dictionary_id": "d1", "version_id": "v9"}],
        )
        self.assertAlmostEqual(got.seconds, 2.0, places=2)
        self.assertEqual(len(got.starts), len("[whispers] Hello there."))
        self.assertEqual(
            self.eleven.credits, len("[whispers] Hello there.")
        )  # a credit a character

    async def test_the_tag_model_gets_the_dictionary_but_no_stitching(self) -> None:
        v3 = ElevenLabs(self.fake, voice_id="v", speech_model="eleven_v3")
        await v3.speech(
            "[whispers] Hi.", previous_text="a", next_text="b", seed=1, dictionary=Locator("d", "v")
        )
        ((_, body),) = self.fake.paths("text-to-speech")
        assert body is not None
        for field in ("previous_text", "next_text"):
            self.assertNotIn(field, body)
        self.assertEqual(  # alias rules work with every model
            body["pronunciation_dictionary_locators"],
            [{"pronunciation_dictionary_id": "d", "version_id": "v"}],
        )

    async def test_a_plan_request_has_no_force_instrumental_and_ambience_names_its_model(
        self,
    ) -> None:
        await self.eleven.music(
            {"composition_plan": {"sections": []}, "force_instrumental": True}, seconds=60
        )
        await self.eleven.ambience({"text": "rain", "duration_seconds": 25.0, "loop": True})
        ((_, music_body),) = self.fake.paths("/v1/music")
        ((_, sound_body),) = self.fake.paths("sound-generation")
        assert music_body is not None and sound_body is not None
        self.assertNotIn("force_instrumental", music_body)
        self.assertEqual(sound_body["model_id"], "eleven_text_to_sound_v2")
        self.assertIs(sound_body["loop"], True)

    async def test_music_ambience_and_the_dictionary_have_their_shapes_and_prices(self) -> None:
        await self.eleven.music(
            {"composition_plan": {"sections": []}, "force_instrumental": True}, seconds=120
        )
        ((_, music_body),) = self.fake.paths("/v1/music")
        assert music_body is not None
        self.assertEqual(music_body["model_id"], "music_v1")
        self.assertIn("composition_plan", music_body)
        self.assertEqual(self.eleven.credits, 1800)  # 900 a minute
        await self.eleven.ambience({"text": "rain", "duration_seconds": 25.0, "loop": True})
        self.assertEqual(self.eleven.credits, 2000)  # and 200 a sound
        locator = await self.eleven.dictionary("names", [nm.Rule("Ka'zeth", "Kah-zeth")])
        self.assertEqual(locator, Locator("dict1", "v1"))
        ((_, dict_body),) = self.fake.paths("pronunciation-dictionaries")
        assert dict_body is not None
        self.assertEqual(
            dict_body["rules"],
            [{"string_to_replace": "Ka'zeth", "type": "alias", "alias": "Kah-zeth"}],
        )

    async def test_errors_say_how_they_failed_and_never_what_was_sent(self) -> None:
        for status, kind, permanent in (
            (401, "auth", True),
            (402, "plan", True),
            (422, "refused", True),
            (429, "busy", False),
            (503, "busy", False),
        ):
            self.fake.fail = {"text-to-speech": status}
            with self.assertRaises(ProviderError) as caught:
                await self.eleven.speech(f"A line that names {SECRET_NAME}.", seed=1)
            self.assertEqual((caught.exception.kind, caught.exception.permanent), (kind, permanent))
            self.assertNotIn(SECRET_NAME, str(caught.exception))
        self.assertEqual(self.eleven.credits, 0)  # a refused call is not charged

    async def test_a_reply_that_is_not_what_was_asked_for_is_an_error(self) -> None:
        async def odd(*_: Any) -> tuple[int, bytes]:
            return 200, b'{"audio_base64": "!!", "alignment": {}}'

        with self.assertRaises(ProviderError) as caught:
            await ElevenLabs(odd, voice_id="v").speech("x", seed=1)
        self.assertEqual(caught.exception.kind, "reply")


class TheNamedFailure(unittest.IsolatedAsyncioTestCase):
    """#2042: a failed call says its kind, its HTTP status and which call it was, in fixed words
    and a number: never the text that was sent or anything the company answered."""

    async def asyncSetUp(self) -> None:
        self.fake = Fake()
        self.eleven = ElevenLabs(self.fake, voice_id="v", speech_model="eleven_multilingual_v2")

    async def failing(self, word: str, status: int, call: Any) -> ProviderError:
        self.fake.fail = {word: status}
        with self.assertRaises(ProviderError) as caught:
            await call()
        return caught.exception

    async def test_each_call_names_itself_with_its_kind_and_status(self) -> None:
        text = f"A line that names {SECRET_NAME}."
        cases = [
            ("text-to-speech", 401, "auth", "speech", lambda: self.eleven.speech(text, seed=1)),
            ("/v1/music", 402, "plan", "music",
             lambda: self.eleven.music({"prompt": text}, seconds=5)),
            ("sound-generation", 422, "refused", "ambience",
             lambda: self.eleven.ambience({"text": text})),
            ("pronunciation-dictionaries", 403, "auth", "pronunciation dictionary",
             lambda: self.eleven.dictionary("n", [])),
        ]  # fmt: skip
        for word, status, kind, step, call in cases:
            error = await self.failing(word, status, call)
            self.assertEqual((error.kind, error.status, error.step), (kind, status, step))
            self.assertEqual(error.describe(), f"{kind}, HTTP {status}, during {step}")
            self.assertNotIn(SECRET_NAME, error.describe() + str(error))

    async def test_a_network_failure_has_no_status_but_still_a_step(self) -> None:
        async def down(*_: Any) -> tuple[int, bytes]:
            raise ProviderError("network")

        with self.assertRaises(ProviderError) as caught:
            await ElevenLabs(down, voice_id="v").music({"prompt": "x"}, seconds=5)
        self.assertEqual(caught.exception.describe(), "network, no HTTP reply, during music")

    async def test_an_error_that_already_has_a_step_keeps_it(self) -> None:
        error = ProviderError("auth", 401, "speech")
        self.assertEqual(error.describe(), "auth, HTTP 401, during speech")
        self.assertEqual(
            ProviderError("reply").describe(), "reply, no HTTP reply, during an unnamed call"
        )

    async def test_every_kind_has_a_line_saying_what_to_check(self) -> None:
        from dmbot.devtools.recap_run.__main__ import audio_failure
        from dmbot.recaps.audio.eleven import CHECKS

        for kind in ("auth", "plan", "refused", "busy", "network", "reply"):
            self.assertIn(kind, CHECKS)
        auth = audio_failure(ProviderError("auth", 401, "speech"))
        self.assertEqual(
            auth[0], "audio: failed (auth, HTTP 401, during speech); the text above is unchanged"
        )
        self.assertIn("ELEVENLABS_API_KEY", auth[1])
        self.assertIn("paid plan", audio_failure(ProviderError("plan", 402, "music"))[1])
        self.assertIn("voice id", audio_failure(ProviderError("refused", 422, "speech"))[1])
        self.assertNotIn("voice id", audio_failure(ProviderError("refused", 422, "music"))[1])
        self.assertNotIn(
            "voice id", audio_failure(ProviderError("refused", 400, "pronunciation dictionary"))[1]
        )
        # anything else: only its type, never its message
        other = audio_failure(RuntimeError(f"secret {SECRET_NAME}"))
        self.assertEqual(other, ["audio: failed (RuntimeError); the text above is unchanged"])


class TheRealTransport(unittest.IsolatedAsyncioTestCase):
    """`HttpTransport` against a small local server that answers in pieces, as the real one does."""

    async def serve(self, size: int, status: int = 200) -> tuple[Any, str]:
        from aiohttp import web

        async def handler(request: Any) -> Any:
            if status != 200:
                return web.Response(status=status, text="the text you sent")
            response = web.StreamResponse()
            await response.prepare(request)
            for _ in range(size // 100_000):
                await response.write(b"x" * 100_000)
                await asyncio.sleep(0)
            await response.write_eof()
            return response

        app = web.Application()
        app.router.add_post("/{tail:.*}", handler)
        runner = web.AppRunner(app)
        await runner.setup()
        self.addAsyncCleanup(runner.cleanup)
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = cast(Any, site)._server.sockets[0].getsockname()[1]
        return runner, f"http://127.0.0.1:{port}"

    async def test_the_whole_body_comes_back_not_what_had_arrived(self) -> None:
        from dmbot.recaps.audio.eleven import HttpTransport

        _, base = await self.serve(2_000_000)
        http = HttpTransport("secret-key", base)
        self.addAsyncCleanup(http.close)
        status, body = await http("POST", "/v1/music", {"a": 1})
        self.assertEqual((status, len(body)), (200, 2_000_000))

    async def test_a_body_over_the_limit_is_refused_and_an_error_body_is_never_kept(self) -> None:
        from unittest.mock import patch as patch_

        from dmbot.recaps.audio.eleven import HttpTransport

        _, base = await self.serve(2_000_000)
        http = HttpTransport("secret-key", base)
        self.addAsyncCleanup(http.close)
        with (
            patch_("dmbot.recaps.audio.eleven.MAX_AUDIO_BYTES", 1_000_000),
            self.assertRaises(ProviderError) as caught,
        ):
            await http("POST", "/v1/music", {})
        self.assertEqual(caught.exception.kind, "reply")
        _, bad = await self.serve(0, status=422)
        other = HttpTransport("secret-key", bad)
        self.addAsyncCleanup(other.close)
        self.assertEqual(await other("POST", "/x", {}), (422, b""))  # never the echoed text

    async def test_a_server_that_cannot_be_reached_is_a_network_error(self) -> None:
        from dmbot.recaps.audio.eleven import HttpTransport

        http = HttpTransport("secret-key", "http://127.0.0.1:9")
        self.addAsyncCleanup(http.close)
        with self.assertRaises(ProviderError) as caught:
            await http("POST", "/x", {})
        self.assertEqual(caught.exception.kind, "network")
        self.assertNotIn("secret-key", repr(caught.exception))


class TheFiles(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.now = float(NOW)
        self.files = AudioFiles(Path(self.dir.name), clock=lambda: self.now)

    def test_only_checked_names_become_paths(self) -> None:
        good = self.files.path(1, "abc-12_x", 100, VOICE)
        self.assertEqual(good.name, "recap-voice.mp3")
        self.assertTrue(str(good).startswith(self.dir.name))
        for guild, campaign, session, kind in (
            (1, "../etc", 100, VOICE),
            (1, "a/b", 100, VOICE),
            (1, "", 100, VOICE),
            (0, "abc", 100, VOICE),
            (1, "abc", 0, VOICE),
            (1, "abc", 100, "../../x"),
        ):
            with self.assertRaises(ValueError):
                self.files.path(guild, campaign, session, kind)

    def test_campaigns_and_servers_have_their_own_folders(self) -> None:
        a = self.files.recap_dir(1, "camp", 100)
        self.assertNotEqual(a, self.files.recap_dir(2, "camp", 100))
        self.assertNotEqual(a, self.files.recap_dir(1, "other", 100))
        self.assertNotEqual(
            self.files.ambience_path(1, "camp", "rain"),
            self.files.ambience_path(1, "other", "rain"),
        )

    def test_a_link_works_until_it_runs_out_and_for_that_file_only(self) -> None:
        self.files.path(1, "camp", 100, VOICE).parent.mkdir(parents=True)
        self.files.path(1, "camp", 100, VOICE).write_bytes(b"x")
        expires, mac = self.files.sign(b"k" * 32, 1, "camp", 100, VOICE)
        ok = self.files.verify(b"k" * 32, 1, "camp", 100, VOICE, expires, mac)
        self.assertEqual(ok, self.files.path(1, "camp", 100, VOICE))
        self.assertIsNone(self.files.verify(b"j" * 32, 1, "camp", 100, VOICE, expires, mac))
        self.assertIsNone(self.files.verify(b"k" * 32, 1, "camp", 100, MUSIC, expires, mac))
        self.assertIsNone(self.files.verify(b"k" * 32, 2, "camp", 100, VOICE, expires, mac))
        self.assertIsNone(self.files.verify(b"k" * 32, 1, "camp", 100, VOICE, expires + 1, mac))
        self.assertIsNone(self.files.verify(b"k" * 32, 1, "../x", 100, VOICE, expires, mac))
        self.now += 16 * 60
        self.assertIsNone(self.files.verify(b"k" * 32, 1, "camp", 100, VOICE, expires, mac))

    def test_audio_goes_after_180_days_and_with_its_campaign(self) -> None:
        for session in (100, 200):
            folder = self.files.recap_dir(1, "camp", session)
            folder.mkdir(parents=True)
            (folder / FILE_NAMES[VOICE]).write_bytes(b"x")
        for session in (100, 200):
            os.utime(self.files.recap_dir(1, "camp", session), (NOW, NOW))  # made at NOW
        self.now += (KEEP_DAYS - 1) * 86400
        self.assertEqual(self.files.sweep(), 0)
        self.now += 2 * 86400
        self.assertEqual(self.files.sweep(), 2)
        folder = self.files.recap_dir(1, "camp", 300)
        folder.mkdir(parents=True)
        self.files.forget_campaign(1, "camp")
        self.assertFalse(folder.exists())


def _touch_outputs(argv: list[str]) -> None:
    for i, a in enumerate(argv):
        if a == "-map":
            Path(argv[i + 2]).write_bytes(b"mp3")


def _seconds_of(path: str) -> float:
    with wave.open(path) as w:
        return float(w.getnframes() / w.getframerate())


class Mixer:
    """A pretend ffmpeg: notes the command, the voice file's length, and makes the outputs."""

    def __init__(self, code: int = 0) -> None:
        self.runs: list[list[str]] = []
        self.voice_seconds = 0.0
        self.code = code

    async def __call__(self, argv: list[str]) -> int:
        self.runs.append(argv)
        self.voice_seconds = _seconds_of(argv[argv.index("-i") + 1])
        if self.code == 0:
            _touch_outputs(argv)
        return self.code


class Rendering(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.files = AudioFiles(Path(self.dir.name))
        self.fake = Fake()
        self.eleven = ElevenLabs(self.fake, voice_id="v")
        self.mixer = Mixer()
        self.renderer = Renderer(self.eleven, self.files, runner=self.mixer)
        self.script = sc.build("The Silver Lock", 4, SUMMARY)
        self.into = Path(self.dir.name) / "out"

    async def render(self) -> Any:
        return await self.renderer.render(
            1, "camp", self.script, into=self.into, seed=7, dictionary=None
        )

    async def test_the_cues_are_never_read_and_each_paragraph_is_its_own_request(self) -> None:
        await self.render()
        texts = [b["text"] for _, b in self.fake.paths("text-to-speech") if b]
        self.assertEqual(len(texts), len(self.script.paragraphs))
        for text in texts:
            for mark in ("《", "》", "((", "))", "Tense", "Hopeful"):
                self.assertNotIn(mark, text)
        self.assertIn("[short pause]", " ".join(texts))

    async def test_the_voice_has_room_before_and_after_and_every_stem_is_its_length(self) -> None:
        made = await self.render()
        speech_seconds = 2.0 * len(self.script.paragraphs)
        want = (
            HEAD_SECONDS
            + speech_seconds
            + GAP_SECONDS
            * (len(self.script.paragraphs) - 1)  # none after the last: the tail follows
            + TAIL_SECONDS
        )
        self.assertAlmostEqual(made.seconds, want, places=2)
        self.assertAlmostEqual(self.mixer.voice_seconds, made.seconds, places=2)

    async def test_the_closing_line_follows_a_long_pause_with_the_music_playing_on(self) -> None:
        """#2627: the closing line is asked for after `[long pause]` and the voice track has at
        least 1.5 s of silence before it, where an ordinary paragraph gets 0.4 s."""
        closing = "And so we continue, the epic adventure of The Ember Road."
        self.script = sc.build("The Silver Lock", 4, f"{SUMMARY}\n\n{closing}")
        made = await self.render()
        texts = [b["text"] for _, b in self.fake.paths("text-to-speech") if b]
        self.assertTrue(texts[-1].startswith("[long pause] And so we continue"))
        self.assertFalse(any("[long pause]" in t for t in texts[:-1]))
        n = len(self.script.paragraphs)
        want = HEAD_SECONDS + 2.0 * n + GAP_SECONDS * (n - 2) + CLOSING_GAP_SECONDS + TAIL_SECONDS
        self.assertAlmostEqual(made.seconds, want, places=2)
        self.assertGreaterEqual(CLOSING_GAP_SECONDS, 1.5)

    async def test_the_music_sections_follow_the_voices_timestamps(self) -> None:
        made = await self.render()
        ((_, body),) = self.fake.paths("/v1/music")
        assert body is not None
        sections = body["composition_plan"]["sections"]
        total = sum(s["duration_ms"] for s in sections)
        # As long as the voice track (rounded up), not the 3-minute floor of an estimate.
        self.assertEqual(total, math.ceil(made.seconds) * 1000)
        self.assertLess(total, 180_000)
        # The company takes force_instrumental only with a prompt; a plan with no lyric lines is
        # instrumental, so the field is left out of a plan request.
        self.assertNotIn("force_instrumental", body)
        self.assertTrue(all(s["lines"] == [] for s in sections))
        # The first mood (Tense) starts when the voice reaches it, 2 s in; the lead-in is calm.
        self.assertEqual(sections[0]["section_name"], "Calm")
        self.assertGreaterEqual(sections[0]["duration_ms"], 3000)

    async def test_the_ambience_is_made_once_for_a_campaign_and_scene(self) -> None:
        script = sc.build("T", None, "《Calm》 They slept in the cave while the rain fell.")
        for _ in range(2):
            await self.renderer.render(1, "camp", script, into=self.into, seed=1, dictionary=None)
        self.assertEqual(len(self.fake.paths("sound-generation")), 1)
        await self.renderer.render(1, "other", script, into=self.into, seed=1, dictionary=None)
        self.assertEqual(len(self.fake.paths("sound-generation")), 2)  # another campaign's own

    async def test_it_mixes_with_the_plan_and_names_the_three_files(self) -> None:
        made = await self.render()
        (argv,) = self.mixer.runs
        self.assertEqual(argv[0], "ffmpeg")
        self.assertIn("sidechaincompress", argv[argv.index("-filter_complex") + 1])
        self.assertEqual(sorted(made.files), sorted([VOICE, MUSIC, MIX]))
        self.assertEqual(
            {p.name for p in self.into.iterdir()}, set(FILE_NAMES.values())
        )  # the working files are gone

    async def test_a_failed_mix_is_an_error_not_a_half_result(self) -> None:
        self.renderer = Renderer(self.eleven, self.files, runner=Mixer(code=1))
        from dmbot.recaps.audio.render import RenderError

        with self.assertRaises(RenderError):
            await self.render()

    async def test_no_secret_name_is_in_any_request(self) -> None:
        # The script refuses it first; and even a campaign-set name only ever reaches the
        # provider as the dictionary's rule text, which leaves secrets out.
        with self.assertRaises(sc.SecretInScript):
            sc.build("T", None, f"《Calm》 {SECRET_NAME} rose.", secret_names=[SECRET_NAME])
        await self.render()
        self.assertNotIn(SECRET_NAME, self.fake.everything_sent())


class Base(DatabaseTest):
    async def approve(self, choice: str) -> bool:
        """The DM approves the text as it is now (#2083), as the screens do."""
        job = await self.store.get(1, self.one.id, 100)
        assert job is not None
        return await self.store.approve(
            1, self.one.id, 100, fx.DM, choice, expected=text_hash(job.title, job.summary)
        )

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.files = AudioFiles(Path(self.dir.name))
        self.campaigns = CampaignStore(self.db)
        self.one = await self.campaigns.create(1, "One", fx.DM)
        self.now = float(NOW)
        self.store = RecapStore(self.db, clock=lambda: self.now)
        self.fake = Fake()
        self.mixer = Mixer()
        await self.store.enqueue(1, self.one.id, 100, dm_user_id=fx.DM)
        await self.store.update(
            1, self.one.id, 100, state="ready", title="The Silver Lock", summary=SUMMARY,
            housekeeping=["Mira drew her blade."], speakers=[fx.DM, fx.ALICE], game_day=4,
        )  # fmt: skip
        # The DM approved the text and picked both tracks (#2083): that queues the audio.
        self.assertTrue(await self.approve("both"))
        self.agreed: Collection[int] = {fx.DM, fx.ALICE, fx.BOB}
        self.secret: list[str] = []
        self.spoken: list[nm.SpokenName] = [nm.SpokenName("Greywater Keep", "Grey-water Keep")]
        self.allowed = True

    def worker(
        self, *, provider: bool = True, cap: int = 15000, model: str = "eleven_multilingual_v2"
    ) -> AudioWorker:
        eleven = ElevenLabs(self.fake, voice_id="v", speech_model=model) if provider else None

        async def load(gid: int, cid: str, job: Any) -> AudioInputs | None:
            return AudioInputs(self.secret, self.spoken)

        async def consenting(gid: int) -> Collection[int]:
            return self.agreed

        async def allowed(gid: int, cid: str, job: Any) -> bool:
            return self.allowed

        return AudioWorker(
            self.store, eleven, self.files, load=load, consenting=consenting, allowed=allowed,
            renderer=Renderer(eleven, self.files, runner=self.mixer) if eleven else None,
            max_credits=cap, clock=lambda: self.now, have_ffmpeg=lambda: True,
        )  # fmt: skip

    async def job(self) -> Any:
        job = await self.store.get(1, self.one.id, 100)
        assert job is not None
        return job


class Choices(Base):
    """The DM's approval and what they pick (#2083): voice-over, music, both or text only."""

    async def reapprove(self, choice: str) -> None:
        await self.store.set_audio(1, self.one.id, 100, audio="off")
        self.assertTrue(await self.approve(choice))

    async def test_both_makes_all_three_files_and_every_kind_of_call(self) -> None:
        await self.worker().run(1, self.one.id, 100)
        self.assertEqual(
            sorted(self.files.present(1, self.one.id, 100)), sorted([VOICE, MUSIC, MIX])
        )
        self.assertTrue(self.fake.paths("text-to-speech"))
        self.assertTrue(self.fake.paths("/v1/music"))

    async def test_voice_over_alone_makes_the_voice_and_its_mix_and_asks_for_no_music(self) -> None:
        await self.reapprove("voice")
        job = await self.worker().run(1, self.one.id, 100)
        assert job is not None
        self.assertEqual(job.audio, "ready")
        self.assertEqual(sorted(self.files.present(1, self.one.id, 100)), sorted([VOICE, MIX]))
        self.assertTrue(self.fake.paths("text-to-speech"))
        self.assertEqual(self.fake.paths("/v1/music"), [])
        self.assertEqual(self.fake.paths("sound-generation"), [])

    async def test_music_alone_asks_for_no_voice_and_is_as_long_as_the_voice_would_be(self) -> None:
        await self.reapprove("music")
        job = await self.worker().run(1, self.one.id, 100)
        assert job is not None
        self.assertEqual(job.audio, "ready")
        self.assertEqual(sorted(self.files.present(1, self.one.id, 100)), [MUSIC])
        self.assertEqual(self.fake.paths("text-to-speech"), [])
        ((_, body),) = self.fake.paths("/v1/music")
        assert body is not None
        sections = body["composition_plan"]["sections"]
        from dmbot.recaps.audio import music as music_module
        from dmbot.recaps.audio import script as script_module

        script = script_module.build(
            job.title, job.game_day, job.summary, secret_names=[], allowed_names=[]
        )
        self.assertEqual(
            sum(x["duration_ms"] for x in sections) // 1000,
            music_module.total_seconds(len(script.spoken)) + 4,  # the voice's head and 1.5 s tail
        )
        self.assertGreaterEqual(len(sections), 2)  # the text's moods share it

    async def test_a_posted_recap_gets_its_audio_made_again_for_the_page(self) -> None:
        """#2183: the audio of a posted recap is made again after its text changed (a spelling
        fix); the files replace the old ones, and the recap stays posted."""
        await self.worker().run(1, self.one.id, 100)
        async with self.db.guild(1) as conn:
            await conn.execute("UPDATE recap_jobs SET status = 'posted'")
        await self.store.set_audio(1, self.one.id, 100, audio="queued", tries=0, next_at=0)
        self.assertEqual(await self.store.audio_due(1), [(self.one.id, 100)])
        self.fake.calls.clear()
        job = await self.worker().run(1, self.one.id, 100)
        assert job is not None
        self.assertEqual((job.status, job.audio), ("posted", "ready"))
        self.assertTrue(self.fake.paths("text-to-speech"))  # it was really made again
        self.assertEqual(job.audio_text_hash, text_hash(job.title, job.summary))

    async def posted_and_queued_again(self) -> None:
        await self.worker().run(1, self.one.id, 100)
        async with self.db.guild(1) as conn:
            await conn.execute("UPDATE recap_jobs SET status = 'posted'")
        await self.store.set_audio(1, self.one.id, 100, audio="queued", tries=0, next_at=0)
        self.fake.calls.clear()

    async def test_a_recap_posted_while_its_audio_is_queued_still_gets_that_audio(self) -> None:
        """The audio was asked for before the post; posting does not drop it (#2183)."""
        self.assertEqual((await self.job()).audio, "queued")
        async with self.db.guild(1) as conn:
            await conn.execute("UPDATE recap_jobs SET status = 'posted'")
        self.assertEqual(await self.store.audio_due(1), [(self.one.id, 100)])
        job = await self.worker().run(1, self.one.id, 100)
        assert job is not None
        self.assertEqual((job.status, job.audio), ("posted", "ready"))
        self.assertTrue(self.fake.paths("text-to-speech"))

    async def test_a_failed_posted_remake_keeps_the_last_ready_hash_and_can_be_asked_again(
        self,
    ) -> None:
        await self.worker().run(1, self.one.id, 100)
        made_from = (await self.job()).audio_text_hash
        async with self.db.guild(1) as conn:  # a spelling fix changed the text of a posted recap
            await conn.execute(
                "UPDATE recap_jobs SET status = 'posted', summary = summary || ' More.',"
                " approved_hash = encode(sha256(convert_to(title || E'\\n' || summary"
                " || ' More.', 'UTF8')), 'hex')"
            )  # (and the DM approved the new text)
        await self.store.set_audio(1, self.one.id, 100, audio="queued", tries=0, next_at=0)
        self.fake.fail["text-to-speech"] = 401  # refused for good, as the worker sees it
        job = await self.worker().run(1, self.one.id, 100)
        assert job is not None
        self.assertEqual((job.status, job.audio), ("posted", "failed"))
        self.assertEqual(job.audio_text_hash, made_from)  # the last ready audio's hash
        self.assertNotEqual(job.audio_text_hash, text_hash(job.title, job.summary))

    async def test_a_posted_remake_over_the_months_allowance_fails_and_spends_nothing(self) -> None:
        await self.posted_and_queued_again()
        before = (await self.job()).audio_credits  # what the first making cost

        async def over(gid: int, cid: str, job: Any) -> str | None:
            return "This month's narrated recaps are used up."

        worker = self.worker()
        worker._allowance = over
        job = await worker.run(1, self.one.id, 100)
        assert job is not None
        self.assertEqual((job.status, job.audio), ("posted", "failed"))
        self.assertEqual(self.fake.calls, [])  # nothing was bought
        self.assertEqual(job.audio_credits, before)  # and nothing more

    async def test_a_posted_remake_with_the_dms_switch_off_makes_nothing(self) -> None:
        await self.posted_and_queued_again()

        async def off(gid: int, cid: str, job: Any) -> bool:
            return False

        worker = self.worker()
        worker._switched = off
        job = await worker.run(1, self.one.id, 100)
        assert job is not None
        self.assertEqual((job.status, job.audio), ("posted", "off"))
        self.assertEqual(self.fake.calls, [])

    async def test_text_only_queues_nothing_and_makes_nothing(self) -> None:
        await self.store.set_audio(1, self.one.id, 100, audio="off")
        job = await self.job()
        self.assertEqual(await self.store.audio_due(1), [])
        await self.reapprove("text")
        job = await self.job()
        self.assertEqual((job.audio, job.audio_choice, job.approved), ("off", "text", True))
        self.assertEqual(await self.store.audio_due(1), [])
        await self.worker().run(1, self.one.id, 100)
        self.assertEqual(self.fake.calls, [])

    async def test_without_approval_no_paid_call_is_made_even_if_audio_is_queued(self) -> None:
        async with self.db.guild(1) as conn:
            await conn.execute("UPDATE recap_jobs SET approved_hash = '', audio_choice = ''")
        job = await self.worker().run(1, self.one.id, 100)
        assert job is not None
        self.assertEqual(job.audio, "off")
        self.assertEqual(self.fake.calls, [])

    async def test_an_edit_ends_the_approval_and_the_audio_is_not_made(self) -> None:
        self.assertTrue((await self.job()).approved)
        await self.store.update(1, self.one.id, 100, summary="《Calm》 The DM rewrote it.")
        job = await self.job()
        self.assertFalse(job.approved)
        await self.worker().run(1, self.one.id, 100)
        self.assertEqual(self.fake.calls, [])
        self.assertEqual((await self.job()).audio, "off")

    async def test_approval_records_who_and_when_and_needs_a_ready_undecided_recap(
        self,
    ) -> None:
        job = await self.job()
        self.assertEqual((job.approved_by, job.approved_at), (fx.DM, NOW))
        await self.store.set_audio(1, self.one.id, 100, audio="off")
        await self.store.decide(1, self.one.id, 100, "drop")
        self.assertFalse(await self.approve("both"))

    async def test_a_second_approval_does_not_restart_audio_that_is_under_way(self) -> None:
        self.assertFalse(await self.approve("voice"))  # queued
        self.assertEqual((await self.job()).audio_choice, "both")

    async def test_nothing_is_approved_over_queued_audio_text_only_included(self) -> None:
        self.assertEqual((await self.job()).audio, "queued")
        for choice in ("text", "voice", "music", "both"):
            self.assertFalse(await self.approve(choice), choice)
        self.assertEqual((await self.job()).audio_choice, "both")

    async def test_work_stopped_by_an_edit_keeps_what_it_spent(self) -> None:
        worker = self.worker()
        original = worker._renderer.render  # type: ignore[union-attr]

        async def render_then_edit(*a: Any, **kw: Any) -> Any:
            made = await original(*a, **kw)
            await self.store.update(1, self.one.id, 100, summary="《Calm》 The DM rewrote this.")
            return made

        worker._renderer.render = render_then_edit  # type: ignore[union-attr,method-assign]
        await worker.run(1, self.one.id, 100)
        job = await self.job()
        self.assertEqual(job.audio, "off")
        self.assertGreater(job.audio_credits, 0)  # the voice and music already paid for count

    async def test_a_text_that_changed_since_it_was_checked_is_not_approved(self) -> None:
        job = await self.job()
        stale = text_hash(job.title, "an older text")
        await self.store.set_audio(1, self.one.id, 100, audio="off")
        self.assertFalse(
            await self.store.approve(1, self.one.id, 100, fx.DM, "both", expected=stale)
        )
        self.assertEqual((await self.job()).audio, "off")

    async def test_after_audio_was_made_an_edit_can_be_approved_again(self) -> None:
        await self.worker().run(1, self.one.id, 100)
        self.assertEqual((await self.job()).audio, "ready")
        self.assertFalse(await self.approve("voice"))  # the same text: already made
        await self.store.update(1, self.one.id, 100, summary="《Calm》 The DM rewrote it.")
        self.assertFalse((await self.job()).approved)
        self.assertTrue(await self.approve("voice"))  # the new text may be made again
        job = await self.job()
        self.assertEqual((job.audio, job.audio_choice, job.approved), ("queued", "voice", True))

    async def test_the_mode_made_is_the_one_approved_when_the_work_began(self) -> None:
        from dmbot.recaps.audio.render import kinds_for

        with self.assertRaises(KeyError):
            kinds_for("text")  # text only makes no files
        worker = self.worker()
        with self.assertRaises(ValueError):
            await worker._renderer.render(  # type: ignore[union-attr]
                1, self.one.id, sc.build("T", 1, SUMMARY, secret_names=[], allowed_names=[]),
                into=Path(self.dir.name) / "x", seed=1, dictionary=None, mode="text",
            )  # fmt: skip
        self.assertEqual(self.fake.calls, [])

    async def test_an_estimate_for_each_choice_counts_only_what_it_asks_for(self) -> None:
        from dmbot.recaps.audio.render import estimate_credits
        from dmbot.recaps.audio.script import build

        script = build("T", 1, SUMMARY, secret_names=[], allowed_names=[])
        both = estimate_credits(script, ambience_needed=True, mode="both")
        voice = estimate_credits(script, ambience_needed=True, mode="voice")
        music = estimate_credits(script, ambience_needed=True, mode="music")
        self.assertLess(voice, both)
        self.assertLess(music, both)
        self.assertEqual(voice + music, both)


class Working(Base):
    async def test_a_recap_gets_its_audio_and_the_files_are_kept_under_the_campaign(self) -> None:
        job = await self.worker().run(1, self.one.id, 100)
        assert job is not None
        self.assertEqual((job.state, job.audio, job.audio_error), ("ready", "ready", ""))
        self.assertGreater(job.audio_credits, 300)
        present = self.files.present(1, self.one.id, 100)
        self.assertEqual(sorted(present), sorted([VOICE, MUSIC, MIX]))
        self.assertEqual(job.summary, SUMMARY)  # the text is untouched

    async def test_with_no_key_the_audio_is_off_and_nothing_is_called(self) -> None:
        job = await self.worker(provider=False).run(1, self.one.id, 100)
        assert job is not None
        self.assertEqual((job.state, job.audio), ("ready", "off"))
        self.assertEqual(self.fake.calls, [])

    async def test_a_plan_without_spoken_recaps_gets_none_and_no_call_is_made(self) -> None:
        self.allowed = False
        job = await self.worker().run(1, self.one.id, 100)
        assert job is not None
        self.assertEqual((job.audio, job.audio_error), ("failed", NOT_INCLUDED))
        self.assertEqual(self.fake.calls, [])

    async def test_a_player_who_stopped_being_recorded_stops_it_before_any_call(self) -> None:
        self.agreed = {fx.DM}
        job = await self.worker().run(1, self.one.id, 100)
        assert job is not None
        self.assertEqual((job.audio, job.audio_error), ("failed", STOPPED))
        self.assertEqual(self.fake.calls, [])

    async def test_consent_is_read_again_after_the_long_work_and_nothing_is_kept(self) -> None:
        reads = 0

        async def consenting(gid: int) -> Collection[int]:
            nonlocal reads
            reads += 1
            return {fx.DM, fx.ALICE} if reads == 1 else {fx.DM}  # ALICE leaves during the work

        worker = self.worker()
        worker._consenting = consenting
        job = await worker.run(1, self.one.id, 100)
        assert job is not None
        self.assertEqual((job.audio, job.audio_error), ("failed", STOPPED))
        self.assertEqual(self.files.present(1, self.one.id, 100), {})
        # And it stopped at once: not one more paragraph was sent after she left.
        self.assertEqual(len(self.fake.paths("text-to-speech")), 0)

    async def test_a_secret_name_in_the_recap_stops_it_before_any_call(self) -> None:
        self.secret = [SECRET_NAME]
        await self.store.update(1, self.one.id, 100, summary=f"《Calm》 {SECRET_NAME}'s keep fell.")
        await self.store.set_audio(1, self.one.id, 100, audio="off")
        self.assertTrue(await self.approve("both"))  # this text
        job = await self.worker().run(1, self.one.id, 100)
        assert job is not None
        self.assertEqual((job.audio, job.audio_error), ("failed", SECRET))
        self.assertEqual(self.fake.calls, [])

    async def test_a_secret_made_during_the_work_stops_the_next_call_and_keeps_nothing(
        self,
    ) -> None:
        worker = self.worker()
        loads = 0
        original = worker._load

        async def load(gid: int, cid: str, job: Any) -> AudioInputs | None:
            nonlocal loads
            loads += 1
            if loads >= 4:  # partway through: the DM has since made a name secret
                # ("Greywater" would not do: it stands inside the confirmed "Greywater Keep")
                return AudioInputs(["Mira"], self.spoken)
            return await original(gid, cid, job)

        worker._load = load
        job = await worker.run(1, self.one.id, 100)
        assert job is not None
        self.assertEqual((job.audio, job.audio_error), ("failed", SECRET))
        self.assertEqual(self.files.present(1, self.one.id, 100), {})
        self.assertLess(len(self.fake.paths("text-to-speech")), 3)  # it did not go on

    async def test_a_recap_over_the_cap_is_not_made_and_costs_nothing(self) -> None:
        job = await self.worker(cap=1000).run(1, self.one.id, 100)
        assert job is not None
        self.assertEqual((job.audio, job.audio_error), ("failed", OVER_CAP))
        self.assertEqual(self.fake.calls, [])

    async def test_a_provider_error_keeps_the_text_and_tries_once_more_an_hour_later(self) -> None:
        self.fake.fail = {"/v1/music": 503}
        worker = self.worker()
        job = await worker.run(1, self.one.id, 100)
        assert job is not None
        self.assertEqual((job.state, job.audio, job.audio_tries), ("ready", "queued", 1))
        self.assertEqual(job.audio_next_at, NOW + 3600)
        self.assertEqual(self.files.present(1, self.one.id, 100), {})  # nothing half-made
        self.assertIsNone(await worker.run(1, self.one.id, 100))  # not before its time
        self.now += 3601
        job = await worker.run(1, self.one.id, 100)
        assert job is not None
        self.assertEqual((job.state, job.audio, job.audio_tries), ("ready", "failed", 2))
        self.assertEqual(job.summary, SUMMARY)

    async def test_the_retry_can_work(self) -> None:
        self.fake.fail = {"/v1/music": 503}
        worker = self.worker()
        await worker.run(1, self.one.id, 100)
        self.fake.fail = {}
        self.now += 3601
        job = await worker.run(1, self.one.id, 100)
        assert job is not None
        self.assertEqual(job.audio, "ready")

    async def test_a_refusal_that_would_fail_the_same_way_is_not_tried_again(self) -> None:
        self.fake.fail = {"text-to-speech": 401}
        job = await self.worker().run(1, self.one.id, 100)
        assert job is not None
        self.assertEqual((job.state, job.audio, job.audio_tries), ("ready", "failed", 1))

    async def test_the_dms_own_switch_turns_the_audio_off_quietly_and_the_plan_still_wins(
        self,
    ) -> None:
        async def off(gid: int, cid: str, job: Any) -> bool:
            return False

        worker = self.worker()
        worker._switched = off
        job = await worker.run(1, self.one.id, 100)
        assert job is not None
        self.assertEqual((job.state, job.audio, job.audio_error), ("ready", "off", ""))
        self.assertEqual(self.fake.calls, [])
        # On is only a yes to the switch: a plan without it is still refused.
        await self.store.set_audio(1, self.one.id, 100, audio="queued", tries=0)

        async def on(gid: int, cid: str, job: Any) -> bool:
            return True

        self.allowed = False
        worker = self.worker()
        worker._switched = on
        job = await worker.run(1, self.one.id, 100)
        assert job is not None
        self.assertEqual((job.audio, job.audio_error), ("failed", NOT_INCLUDED))

    async def test_no_mixer_on_the_server_fails_before_any_paid_call(self) -> None:
        worker = self.worker()
        worker._have_ffmpeg = lambda: False
        job = await worker.run(1, self.one.id, 100)
        assert job is not None
        self.assertEqual((job.audio, job.audio_error), ("failed", NO_MIXER))
        self.assertEqual(self.fake.calls, [])

    async def test_a_failed_provider_call_is_logged_with_its_kind_status_and_step(self) -> None:
        self.fake.fail = {"text-to-speech": 401}
        worker = self.worker()
        with self.assertLogs("dmbot.recaps.audio.worker", level="WARNING") as logged:
            job = await worker.run(1, self.one.id, 100)
        assert job is not None
        text = "\n".join(logged.output)
        self.assertIn("auth, HTTP 401, during speech", text)
        self.assertNotIn(SECRET_NAME, text)
        self.assertNotIn("xi-api-key", text)

    async def test_a_hung_provider_cannot_hold_the_job_past_its_lease(self) -> None:
        async def slow(*_: Any) -> tuple[int, bytes]:
            await asyncio.sleep(5)
            return 200, b""

        worker = self.worker()
        worker._provider._http = slow  # type: ignore[union-attr]
        with patch("dmbot.recaps.audio.worker.DEADLINE_S", 0.05):
            job = await worker.run(1, self.one.id, 100)
        assert job is not None
        self.assertEqual((job.state, job.audio, job.audio_tries), ("ready", "queued", 1))
        self.assertEqual(self.files.present(1, self.one.id, 100), {})
        self.assertFalse(self.files.recap_dir(1, self.one.id, 100).with_name("100.new").exists())

    async def test_a_try_is_counted_when_the_job_is_taken_so_a_crash_loop_cannot_bill_for_ever(
        self,
    ) -> None:
        for expected in (1, 2):  # two process deaths mid-work: the job is taken, nothing recorded
            self.assertTrue(await self.store.audio_lease(1, self.one.id, 100))
            await self.store.release(1, self.one.id, 100)
            self.assertEqual((await self.job()).audio_tries, expected)
        self.assertEqual(await self.store.audio_due(1), [])
        self.assertFalse(await self.store.audio_lease(1, self.one.id, 100))
        self.assertEqual(await self.store.fail_stuck_audio(1), 1)
        self.assertEqual((await self.job()).audio, "failed")

    async def test_credits_spent_on_a_failed_try_are_kept_and_count_toward_the_cap(self) -> None:
        self.fake.fail = {"/v1/music": 503}
        worker = self.worker()
        first = await worker.run(1, self.one.id, 100)
        assert first is not None
        self.assertGreater(first.audio_credits, 0)  # the voice was paid for, then music failed
        spent = first.audio_credits
        self.fake.fail = {}
        self.now += 3601
        second = await self.worker(cap=spent + 500).run(1, self.one.id, 100)
        assert second is not None
        self.assertEqual((second.audio, second.audio_error), ("failed", OVER_CAP))

    async def test_text_changed_while_the_audio_was_made_keeps_nothing(self) -> None:
        worker = self.worker()
        original = worker._renderer.render  # type: ignore[union-attr]

        async def render_then_edit(*a: Any, **kw: Any) -> Any:
            made = await original(*a, **kw)
            await self.store.update(1, self.one.id, 100, summary="《Calm》 The DM rewrote this.")
            return made

        worker._renderer.render = render_then_edit  # type: ignore[union-attr,method-assign]
        job = await worker.run(1, self.one.id, 100)
        assert job is not None
        # The edit ended the approval: nothing is kept and nothing is made again until the DM
        # approves the new text.
        self.assertEqual((job.audio, job.approved), ("off", False))
        self.assertEqual(self.files.present(1, self.one.id, 100), {})

    async def test_the_audio_is_marked_with_the_text_it_was_made_from(self) -> None:
        job = await self.worker().run(1, self.one.id, 100)
        assert job is not None
        self.assertEqual(job.audio_text_hash, text_hash(job.title, SUMMARY))
        self.assertNotEqual(job.audio_text_hash, text_hash(job.title, SUMMARY + " more"))

    async def test_a_recap_gone_during_the_work_is_not_recreated_on_disk(self) -> None:
        worker = self.worker()
        original = worker._renderer.render  # type: ignore[union-attr]

        async def render_then_drop(*a: Any, **kw: Any) -> Any:
            made = await original(*a, **kw)
            async with self.db.guild(1) as conn:
                await conn.execute("UPDATE recap_jobs SET status = 'dropped'")
            return made

        worker._renderer.render = render_then_drop  # type: ignore[union-attr,method-assign]
        await worker.run(1, self.one.id, 100)
        self.assertEqual(self.files.present(1, self.one.id, 100), {})

    async def test_a_failed_mix_is_a_retry_too(self) -> None:
        self.mixer.code = 1
        job = await self.worker().run(1, self.one.id, 100)
        assert job is not None
        self.assertEqual((job.audio, job.audio_tries), ("queued", 1))

    def made(self) -> list[str]:
        return [
            m for m, p, _ in self.fake.calls if "pronunciation-dictionaries" in p and m == "POST"
        ]

    async def test_the_dictionary_is_made_once_and_again_only_when_the_rules_change(self) -> None:
        worker = self.worker()
        await worker.run(1, self.one.id, 100)
        self.assertEqual(len(self.made()), 1)
        locators = [b for _, b in self.fake.paths("text-to-speech") if b]
        self.assertTrue(all("pronunciation_dictionary_locators" in b for b in locators))
        await self.store.set_audio(1, self.one.id, 100, audio="queued", tries=0)
        await worker.run(1, self.one.id, 100)
        self.assertEqual(len(self.made()), 1)  # kept
        self.spoken = [nm.SpokenName("Greywater Keep", "Grey-wah-ter Keep")]
        await self.store.set_audio(1, self.one.id, 100, audio="queued", tries=0)
        await worker.run(1, self.one.id, 100)
        self.assertEqual(len(self.made()), 2)
        # The older one is deleted at the company, and the name sent is opaque.
        self.assertIn(
            ("DELETE", "/v1/pronunciation-dictionaries/dict1"),
            [(m, p) for m, p, _ in self.fake.calls],
        )
        first = next(b for m, p, b in self.fake.calls if m == "POST" and "dictionaries" in p)
        assert first is not None
        self.assertNotIn(self.one.id, str(first["name"]))

    async def test_no_dictionary_is_made_when_no_name_has_a_spelling(self) -> None:
        self.spoken = [nm.SpokenName("Greywater Keep")]
        await self.worker().run(1, self.one.id, 100)
        self.assertEqual(self.made(), [])
        speech = [b for _, b in self.fake.paths("text-to-speech") if b]
        self.assertTrue(all("pronunciation_dictionary_locators" not in b for b in speech))

    async def test_a_secret_name_is_in_no_request_even_as_a_dictionary_rule(self) -> None:
        self.secret = [SECRET_NAME]
        self.spoken = [
            nm.SpokenName(SECRET_NAME, "Vay-lith-or", secret=True),
            nm.SpokenName(f"{SECRET_NAME}'s Keep", "Vay Keep"),
            nm.SpokenName("Greywater Keep", "Grey-water Keep"),
        ]
        await self.worker().run(1, self.one.id, 100)
        self.assertNotIn(SECRET_NAME, self.fake.everything_sent())
        self.assertNotIn("Vay", self.fake.everything_sent())
        self.assertEqual(len(self.made()), 1)

    async def test_the_audio_waits_for_nobody_and_runs_one_at_a_time_for_a_campaign(self) -> None:
        self.assertEqual(await self.store.audio_due(1), [(self.one.id, 100)])
        self.assertTrue(await self.store.audio_lease(1, self.one.id, 100))
        self.assertFalse(await self.store.audio_lease(1, self.one.id, 100))
        self.assertEqual(await self.store.audio_due(1), [])
        await self.store.release(1, self.one.id, 100)
        await self.store.update(1, self.one.id, 100, state="queued")  # Try again set it back
        self.assertEqual(await self.store.audio_due(1), [])

    async def test_a_new_recap_forgets_the_old_audio_state(self) -> None:
        await self.worker().run(1, self.one.id, 100)
        await self.store.reset_audio(1, self.one.id, 100)
        job = await self.job()
        self.assertEqual((job.audio, job.audio_credits, job.audio_tries), ("off", 0, 0))


class WhenTheTextIsReady(DatabaseTest):
    """No audio is queued when the text is ready (#2083): the DM approves it first."""

    async def test_the_text_is_ready_at_once_and_no_audio_is_queued(self) -> None:
        campaigns = CampaignStore(self.db)
        one = await campaigns.create(1, "One", fx.DM)
        store = RecapStore(self.db, clock=lambda: float(NOW))

        async def load(gid: int, cid: str, job: Any) -> pl.Inputs:
            return pl.Inputs(
                lines=fx.lines(), dm_ids={fx.DM}, allowed_names=list(fx.NAMES), secret_names=[],
                previous="", day=4, hours=3.5,
            )  # fmt: skip

        async def ok(gid: int, cid: str, job: Any) -> bool:
            return True

        async def told(gid: int, cid: str, job: Any) -> None:
            return None

        canned = fx.Canned(fx.BEATS_ANSWER, fx.PROSE_ANSWER, fx.GRADE_ANSWER, fx.FINISH_ANSWER)
        worker = pl.RecapWorker(
            store, canned, fx.Canned(), load=load, consenting=lambda gid: fx.everyone,
            allowed=ok, notify=told, clock=lambda: float(NOW),
        )  # fmt: skip
        await store.enqueue(1, one.id, 100, dm_user_id=fx.DM)
        job = await worker.run(1, one.id, 100)
        assert job is not None
        self.assertEqual(
            (job.state, job.audio, job.approved, job.audio_choice), ("ready", "off", False, "")
        )
        self.assertEqual(await store.audio_due(1), [])


if __name__ == "__main__":
    unittest.main()
