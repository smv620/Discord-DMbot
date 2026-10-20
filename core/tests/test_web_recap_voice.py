"""A note a DM speaks on the recap page (#2113): queued by the website's doors with the clip
held in the request row, written down by the bot, the clip deleted with the outcome. Invented
fixtures only; the clips are generated tones, no real speech."""

from __future__ import annotations

import io
from collections import defaultdict
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import av
import numpy as np

from dmbot.bot import DMBot
from dmbot.recaps import requests as rq
from dmbot.recaps import voice
from dmbot.web import recap_requests as web
from dmbot.web import sessions
from tests.test_recap_web import BOB, NOW
from tests.test_transcript_cleaner import alias, entity, lookup
from tests.test_web_api import THURSDAY
from tests.test_web_recap_requests import WithRequests

CLIP = b"\x1a\x45\xdf\xa3 not really audio, the doors do not decode it"


def tone(fmt: str = "webm", seconds: float = 1.5) -> bytes:
    """A real encoded clip of a steady tone (Opus in webm or ogg), as a browser might send."""
    buf = io.BytesIO()
    out = av.open(buf, "w", format=fmt)
    stream = out.add_stream("libopus", rate=48000)
    cast(Any, stream).layout = "mono"
    wave = (np.sin(np.arange(int(48000 * seconds)) * 2 * np.pi * 440 / 48000) * 8000).astype(
        np.int16
    )
    frame = av.AudioFrame.from_ndarray(wave.reshape(1, -1), format="s16", layout="mono")
    frame.sample_rate = 48000
    for packet in stream.encode(frame):
        out.mux(packet)
    for packet in stream.encode(None):
        out.mux(packet)
    out.close()
    return buf.getvalue()


class WithClips(WithRequests):
    async def send_clip(self, data: bytes = CLIP, kind: str = "audio/webm;codecs=opus") -> str:
        got = await web.send_voice(self.web, self.session, self.ref, data, kind)
        return str(got["requestId"])

    async def clip_in_row(self, rid: str) -> bytes | None:
        async with self.db.guild(THURSDAY.id) as conn:
            cur = await conn.execute("SELECT clip FROM recap_requests WHERE id = %s", (rid,))
            row = await cur.fetchone()
        assert row is not None
        return None if row["clip"] is None else bytes(row["clip"])

    async def work(self, write: Any, gate: Any = None) -> tuple[str, str]:
        rid = await self.send_clip()
        request = await self.requests.claim_next(THURSDAY.id)
        assert request is not None and request.kind == "voice" and request.clip == CLIP
        outcome = await rq.run_voice(
            self.requests,
            THURSDAY.id,
            request,
            gate=gate or AsyncMock(return_value=None),
            write=write,
        )
        return rid, outcome


class TheDoors(WithClips):
    async def test_a_clip_is_queued_and_waits_without_being_shown_back(self) -> None:
        rid = await self.send_clip()
        self.assertRegex(rid, r"^[0-9a-f]{32}$")
        self.assertEqual(
            await web.read_voice(self.web, self.session, self.ref, rid), {"state": "waiting"}
        )
        self.assertEqual(await self.clip_in_row(rid), CLIP)

    async def test_the_refusals(self) -> None:
        self.assertEqual(await self.code(self.send_clip(CLIP, "audio/mpeg")), "bad_type")
        self.assertEqual(await self.code(self.send_clip(b"")), "bad_clip")
        self.assertEqual(await self.code(self.send_clip(b"x" * (2 * 1024 * 1024 + 1))), "too_big")
        for _ in range(3):
            await self.send_clip()
        self.assertEqual(await self.code(self.send_clip()), "too_soon")  # three in ten minutes
        await self.store.decide(THURSDAY.id, self.one.id, 100, "drop")
        self.assertEqual(await self.code(self.send_clip()), "not_editable")

    async def test_the_types_with_parameters_are_read_by_their_base(self) -> None:
        await self.send_clip(CLIP, "Audio/OGG; codecs=opus")

    async def test_a_person_who_is_not_a_dm_is_told_nothing(self) -> None:
        bob_token = await sessions.sign_in(self.web, cast(Any, BOB), [THURSDAY], now=NOW, days=30)
        bob = await sessions.find(self.web, bob_token, now=NOW)
        assert bob is not None
        self.assertEqual(
            await self.code(web.send_voice(self.web, bob, self.ref, CLIP, "audio/webm")),
            "not_found",
        )
        rid = await self.send_clip()
        self.assertEqual(await self.code(web.read_voice(self.web, bob, self.ref, rid)), "not_found")

    async def test_a_note_request_is_not_a_voice_request_and_the_reverse(self) -> None:
        note = await self.send()
        voice_id = await self.send_clip()
        self.assertEqual(
            await self.code(web.read_voice(self.web, self.session, self.ref, note)), "not_found"
        )
        # A clip's id answers no "rewrite" either: it carries no points to keep.
        request = await self.requests.claim_next(THURSDAY.id)
        assert request is not None
        await self.requests.finish(THURSDAY.id, request, answer={"text": "hello"})
        self.assertIn(
            await self.code(
                web.rewrite(
                    self.web, self.session, self.ref, {"requestId": voice_id, "corrections": ["c1"]}
                )
            ),
            ("bad_choice", "not_found"),
        )


class TheBot(WithClips):
    async def test_the_words_come_back_and_the_clip_is_gone_in_the_same_change(self) -> None:
        write = AsyncMock(return_value="Mira did not draw her blade.")
        rid, outcome = await self.work(write)
        self.assertEqual(outcome, "done")
        self.assertIsNone(await self.clip_in_row(rid))
        self.assertEqual(
            await web.read_voice(self.web, self.session, self.ref, rid),
            {"state": "done", "text": "Mira did not draw her blade."},
        )

    async def test_every_way_it_ends_deletes_the_clip(self) -> None:
        cases = [
            (AsyncMock(side_effect=voice.ClipError(voice.TOO_LONG)), voice.TOO_LONG),
            (AsyncMock(side_effect=voice.ClipError(voice.BAD_CLIP)), voice.BAD_CLIP),
            (AsyncMock(side_effect=voice.ClipError(voice.NO_CONSENT)), voice.NO_CONSENT),
            (AsyncMock(side_effect=RuntimeError("Mira's words")), voice.TRANSCRIBE_FAILED),
            (AsyncMock(return_value="   "), voice.NO_WORDS),
        ]
        for write, expected in cases:
            async with self.db.guild(THURSDAY.id) as conn:  # earlier ones are out of the limit
                await conn.execute("UPDATE recap_requests SET created_at = created_at - 700")
            rid, outcome = await self.work(write)
            self.assertEqual(outcome, expected)
            self.assertIsNone(await self.clip_in_row(rid), expected)
            got = await web.read_voice(self.web, self.session, self.ref, rid)
            self.assertEqual(got, {"state": "failed", "error": expected})

    async def test_a_gate_that_says_no_transcribes_nothing_and_deletes_the_clip(self) -> None:
        write = AsyncMock()
        rid, outcome = await self.work(write, gate=AsyncMock(return_value=rq.NOT_ALLOWED))
        self.assertEqual(outcome, rq.NOT_ALLOWED)
        write.assert_not_awaited()
        self.assertIsNone(await self.clip_in_row(rid))

    async def test_the_words_and_the_audio_are_never_logged(self) -> None:
        with self.assertLogs("dmbot.recaps.requests", level="WARNING") as logs:
            await self.work(AsyncMock(side_effect=RuntimeError("Mira did not draw her blade.")))
        self.assertNotIn("Mira", " ".join(logs.output))

    async def test_a_clip_older_than_ten_minutes_is_cleared_whatever_happened(self) -> None:
        rid = await self.send_clip()
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute(
                "UPDATE recap_requests SET created_at = created_at - 601 WHERE id = %s", (rid,)
            )
        await self.requests.sweep(THURSDAY.id)
        self.assertIsNone(await self.clip_in_row(rid))  # the row stays for its hour; the audio
        # does not
        async with self.db.guild(THURSDAY.id) as conn:
            cur = await conn.execute(
                "SELECT count(*) AS n FROM recap_requests WHERE id = %s", (rid,)
            )
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual(row["n"], 1)

    async def test_a_new_clip_clears_the_old_ones_in_the_door_too(self) -> None:
        old = await self.send_clip()
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute(
                "UPDATE recap_requests SET created_at = created_at - 601 WHERE id = %s", (old,)
            )
        await self.send_clip()
        self.assertIsNone(await self.clip_in_row(old))


class TheClip(WithClips):
    def test_a_browsers_clip_is_read_in_memory(self) -> None:
        for fmt in ("webm", "ogg"):
            pcm = voice.decode(tone(fmt))
            self.assertAlmostEqual(voice.seconds(pcm), 1.5, delta=0.1)

    def test_what_is_not_audio_or_too_short_or_too_long_is_refused(self) -> None:
        for bad in (b"", CLIP, b"\x00" * 5000):
            with self.assertRaises(voice.ClipError) as err:
                voice.decode(bad)
            self.assertEqual(err.exception.code, voice.BAD_CLIP)
        with self.assertRaises(voice.ClipError) as err:
            voice.decode(tone("webm", 0.1))
        self.assertEqual(err.exception.code, voice.BAD_CLIP)
        with self.assertRaises(voice.ClipError) as err:
            voice.decode(tone("webm", 130))
        self.assertEqual(err.exception.code, voice.TOO_LONG)

    def test_the_meter_rounds_up_to_a_whole_minute_and_never_less_than_one(self) -> None:
        got = [voice.meter_minutes(s) for s in (0.5, 59, 60, 61, 90, 120, 121)]
        self.assertEqual(got, [1, 1, 1, 2, 2, 2, 3])

    def test_nothing_is_left_on_disk(self) -> None:
        import os
        import tempfile

        before = set(os.listdir(tempfile.gettempdir()))
        voice.decode(tone("webm", 1.0))
        self.assertEqual(set(os.listdir(tempfile.gettempdir())) - before, set())


KNOWN = "m" * 32


def fake_bot(*, consent: bool = True, heard: str = "we met Cara Dinval today") -> Any:
    names = lookup(more=(entity(KNOWN, "Caradinval"),), more_aliases=(alias(KNOWN, "Caradinval"),))
    transcribe = AsyncMock(return_value=heard)
    bot = SimpleNamespace(
        consent=SimpleNamespace(has_consent=lambda g, u: consent),
        pipeline=SimpleNamespace(
            transcriber=SimpleNamespace(transcribe=transcribe), sent_s_in=defaultdict(float)
        ),
        settings=SimpleNamespace(transcription=SimpleNamespace(sends_audio_out=False)),
        _recap_voice_hints=AsyncMock(return_value=(["Caradinval"], names, ["Mia"])),
        _meter_voice=AsyncMock(),
    )
    bot.transcribe = transcribe
    return bot


class TheEngineSide(WithClips):
    def request(self, data: bytes) -> rq.Request:
        return rq.Request("a" * 32, "c" * 32, 100, 7, "", 1, "voice", data)

    async def test_the_note_is_written_with_the_campaigns_names_and_fixed(self) -> None:
        bot = fake_bot()
        got = await DMBot.recap_voice_text(bot, THURSDAY.id, self.request(tone("webm", 1.5)))
        self.assertEqual(got, "we met Caradinval today")  # a split name made whole
        hints = bot.transcribe.await_args.args[1]
        self.assertEqual(hints, ["Caradinval"])  # the name hints a live session would send
        bot._meter_voice.assert_awaited_once()

    async def test_without_agreement_nothing_is_decoded_or_sent(self) -> None:
        bot = fake_bot(consent=False)
        with self.assertRaises(voice.ClipError) as err:
            await DMBot.recap_voice_text(bot, THURSDAY.id, self.request(tone("webm", 1.5)))
        self.assertEqual(err.exception.code, voice.NO_CONSENT)
        bot.transcribe.assert_not_awaited()

    async def test_consent_withdrawn_while_it_was_written_down_keeps_nothing(self) -> None:
        answers = iter([True, True, False])
        bot = fake_bot()
        bot.consent = SimpleNamespace(has_consent=lambda g, u: next(answers, False))
        with self.assertRaises(voice.ClipError) as err:
            await DMBot.recap_voice_text(bot, THURSDAY.id, self.request(tone("webm", 1.5)))
        self.assertEqual(err.exception.code, voice.NO_CONSENT)
        bot._meter_voice.assert_not_awaited()  # not billed for words that were thrown away

    async def test_a_ninety_second_clip_adds_two_minutes_to_the_meter(self) -> None:
        added: list[dict[str, Any]] = []
        owned = SimpleNamespace(
            owner_user_id=5,
        )
        meter = SimpleNamespace(
            add=AsyncMock(side_effect=lambda **kw: added.append(kw)),
            add_unowned=AsyncMock(side_effect=lambda **kw: added.append({**kw, "unowned": True})),
        )
        bot = SimpleNamespace(
            meter=meter, campaigns=SimpleNamespace(get=AsyncMock(return_value=owned))
        )
        await DMBot._meter_voice(cast(Any, bot), THURSDAY.id, self.request(CLIP), 90.0)
        self.assertEqual((added[0]["minutes"], added[0]["session_started_at"]), (2, 100))
        self.assertEqual(added[0]["owner_user_id"], 5)
        owned.owner_user_id = None  # nobody's hours to spend yet: the campaign's own record only
        await DMBot._meter_voice(cast(Any, bot), THURSDAY.id, self.request(CLIP), 90.0)
        self.assertEqual((added[1]["minutes"], added[1]["unowned"]), (2, True))

    async def test_a_meter_that_fails_costs_nothing(self) -> None:
        bot = SimpleNamespace(
            meter=SimpleNamespace(add=AsyncMock(side_effect=RuntimeError("db"))),
            campaigns=SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(owner_user_id=5))),
        )
        with self.assertLogs("dmbot.bot", level="WARNING"):
            await DMBot._meter_voice(cast(Any, bot), THURSDAY.id, self.request(CLIP), 30.0)
