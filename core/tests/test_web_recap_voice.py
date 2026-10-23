"""A note a DM speaks on the recap page (#2113): queued by the website's doors with the clip
held in the request row, written down by the bot, the clip deleted with the outcome. Invented
fixtures only; the clips are generated tones, no real speech."""

from __future__ import annotations

import io
import time
import unittest
from collections import defaultdict
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import av
import numpy as np

from dmbot.bot import DMBot
from dmbot.consent import ConsentStore
from dmbot.recaps import requests as rq
from dmbot.recaps import voice
from dmbot.web import recap_requests as web
from dmbot.web import sessions
from tests.test_recap_web import BOB, NOW
from tests.test_transcript_cleaner import alias, entity, lookup
from tests.test_web_api import ALICE, THURSDAY
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
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        # The DM has agreed to be recorded (the door asks before it stores a clip).
        await ConsentStore(self.db).grant(THURSDAY.id, ALICE.id)

    async def send_clip(self, data: bytes = CLIP, kind: str = "audio/webm;codecs=opus") -> str:
        got = await web.send_voice(self.web, self.session, self.ref, data, kind, outside=None)
        return str(got["requestId"])

    async def clip_in_row(self, rid: str) -> bytes | None:
        async with self.db.guild(THURSDAY.id) as conn:
            cur = await conn.execute("SELECT clip FROM recap_requests WHERE id = %s", (rid,))
            row = await cur.fetchone()
        assert row is not None
        return None if row["clip"] is None else bytes(row["clip"])

    async def work(self, write: Any, gate: Any = None, meter: Any = None) -> tuple[str, str]:
        rid = await self.send_clip()
        request = await self.requests.claim_next(THURSDAY.id)
        assert request is not None and request.kind == "voice" and request.clip == CLIP
        outcome = await rq.run_voice(
            self.requests,
            THURSDAY.id,
            request,
            gate=gate or AsyncMock(return_value=None),
            write=write,
            meter=meter or AsyncMock(),
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

    async def test_a_dm_who_has_not_agreed_to_be_recorded_stores_nothing(self) -> None:
        await ConsentStore(self.db).revoke(THURSDAY.id, ALICE.id)
        self.assertEqual(await self.code(self.send_clip()), "no_consent")
        async with self.db.guild(THURSDAY.id) as conn:
            cur = await conn.execute("SELECT count(*) AS n FROM recap_requests")
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual(row["n"], 0)  # not even the clip's row

    async def test_consent_for_another_company_does_not_count(self) -> None:
        await ConsentStore(self.db).grant(THURSDAY.id, ALICE.id, outside_to="cloud")
        got = await web.send_voice(
            self.web, self.session, self.ref, CLIP, "audio/webm", outside="cloud"
        )
        self.assertRegex(got["requestId"], r"^[0-9a-f]{32}$")  # agreed to the company in use
        self.assertEqual(
            await self.code(
                web.send_voice(
                    self.web, self.session, self.ref, CLIP, "audio/webm", outside="deepgram"
                )
            ),
            "no_consent",
        )

    async def test_consent_that_names_no_company_does_not_count_while_an_engine_is_set(
        self,
    ) -> None:
        # (the setUp consent has outside_to NULL: given before an outside engine was in use)
        self.assertEqual(
            await self.code(
                web.send_voice(
                    self.web, self.session, self.ref, CLIP, "audio/webm", outside="deepgram"
                )
            ),
            "no_consent",
        )
        got = await web.send_voice(
            self.web, self.session, self.ref, CLIP, "audio/webm", outside=None
        )  # with no outside engine in use, the wording version is all that is asked
        self.assertRegex(got["requestId"], r"^[0-9a-f]{32}$")

    async def test_consent_under_older_wording_does_not_count(self) -> None:
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute(
                "UPDATE consent SET terms_version = terms_version - 1 WHERE user_id = %s",
                (ALICE.id,),
            )
        self.assertEqual(await self.code(self.send_clip()), "no_consent")

    async def test_a_server_the_bot_has_left_keeps_no_clips(self) -> None:
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute(
                "INSERT INTO installs (guild_id, installed_at, via, left_at)"
                " VALUES (%s, 1, 'link', 2)",
                (THURSDAY.id,),
            )
        self.assertEqual(await self.code(self.send_clip()), "not_allowed")

    async def test_clips_do_not_count_against_the_typed_notes_either(self) -> None:
        for _ in range(3):
            await self.send_clip()
        await self.send()  # three clips waiting do not stop a typed note

    async def test_failed_clips_do_not_count_against_the_limit(self) -> None:
        for _ in range(3):
            rid = await self.send_clip()
            request = await self.requests.claim_next(THURSDAY.id)
            assert request is not None
            await self.requests.finish(THURSDAY.id, request, answer=None, error=voice.BAD_CLIP)
            assert rid
        await self.send_clip()  # three misclicks did not lock the DM out
        for _ in range(2):
            await self.send_clip()
        self.assertEqual(await self.code(self.send_clip()), "too_soon")  # three held: now it does

    async def test_typed_notes_do_not_count_against_the_clips(self) -> None:
        for _ in range(3):
            await self.send()
        await self.send_clip()

    async def test_the_types_with_parameters_are_read_by_their_base(self) -> None:
        rid = await self.send_clip(CLIP, "Audio/OGG; codecs=opus")
        request = await self.requests.claim_next(THURSDAY.id)
        assert request is not None and request.id == rid
        self.assertEqual((request.kind, request.clip_type), ("voice", "audio/ogg"))

    async def test_a_person_who_is_not_a_dm_is_told_nothing(self) -> None:
        bob_token = await sessions.sign_in(self.web, cast(Any, BOB), [THURSDAY], now=NOW, days=30)
        bob = await sessions.find(self.web, bob_token, now=NOW)
        assert bob is not None
        self.assertEqual(
            await self.code(
                web.send_voice(self.web, bob, self.ref, CLIP, "audio/webm", outside=None)
            ),
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
        write = AsyncMock(return_value=("Mira did not draw her blade.", 90.0))
        meter = AsyncMock()
        rid, outcome = await self.work(write, meter=meter)
        meter.assert_awaited_once()
        assert meter.await_args is not None
        self.assertEqual(meter.await_args.args[1], 90.0)
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
            (AsyncMock(return_value=("   ", 3.0)), voice.NO_WORDS),
        ]
        for write, expected in cases:
            async with self.db.guild(THURSDAY.id) as conn:  # earlier ones are out of the limit
                await conn.execute("UPDATE recap_requests SET created_at = created_at - 700")
            rid, outcome = await self.work(write)
            self.assertEqual(outcome, expected)
            self.assertIsNone(await self.clip_in_row(rid), expected)
            got = await web.read_voice(self.web, self.session, self.ref, rid)
            self.assertEqual(got, {"state": "failed", "error": expected})

    async def test_a_retaken_claim_is_not_billed_and_changes_nothing(self) -> None:
        import dataclasses

        rid = await self.send_clip()
        request = await self.requests.claim_next(THURSDAY.id)
        assert request is not None
        stale = dataclasses.replace(request, claimed_at=request.claimed_at + 1)  # not the claim
        meter = AsyncMock()
        outcome = await rq.run_voice(
            self.requests,
            THURSDAY.id,
            stale,
            gate=AsyncMock(return_value=None),
            write=AsyncMock(return_value=("words", 30.0)),
            meter=meter,
        )
        self.assertEqual(outcome, "done")  # (what this worker saw)
        meter.assert_not_awaited()  # the row was not its to finish, so no minutes either
        self.assertEqual(await self.clip_in_row(rid), CLIP)
        self.assertEqual(
            await web.read_voice(self.web, self.session, self.ref, rid), {"state": "working"}
        )

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
        await self.requests.sweep_clips()
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
            pcm = voice.decode(tone(fmt), f"audio/{fmt}")
            self.assertAlmostEqual(voice.seconds(pcm), 1.5, delta=0.1)

    def test_only_the_named_containers_are_read(self) -> None:
        for data, kind in ((tone("webm"), "audio/ogg"), (tone("ogg"), "audio/webm"),
                           (tone("webm"), "audio/mpeg"), (tone("webm"), "")):  # fmt: skip
            with self.assertRaises(voice.ClipError) as err:
                voice.decode(data, kind)
            self.assertEqual(err.exception.code, voice.BAD_CLIP, kind)

    def test_a_clip_that_takes_too_long_to_read_is_refused(self) -> None:
        from unittest import mock

        with (
            mock.patch.object(voice, "DECODE_BUDGET_S", -1.0),
            self.assertRaises(voice.ClipError) as err,
        ):
            voice.decode(tone("webm", 2.0))
        self.assertEqual(err.exception.code, voice.BAD_CLIP)

    def test_what_is_not_audio_or_too_short_or_too_long_is_refused(self) -> None:
        for bad in (b"", CLIP, b"\x00" * 5000):
            with self.assertRaises(voice.ClipError) as err:
                voice.decode(bad, "audio/webm")
            self.assertEqual(err.exception.code, voice.BAD_CLIP)
        with self.assertRaises(voice.ClipError) as err:
            voice.decode(tone("webm", 0.1), "audio/webm")
        self.assertEqual(err.exception.code, voice.BAD_CLIP)
        with self.assertRaises(voice.ClipError) as err:
            voice.decode(tone("webm", 130), "audio/webm")
        self.assertEqual(err.exception.code, voice.TOO_LONG)

    def test_the_meter_rounds_up_to_a_whole_minute_and_never_less_than_one(self) -> None:
        got = [voice.meter_minutes(s) for s in (0.5, 59, 60, 60.5, 62, 62.5, 90, 120, 122, 122.5)]
        self.assertEqual(got, [1, 1, 1, 1, 1, 2, 2, 2, 2, 3])  # never a minute it did not use

    def test_nothing_is_left_on_disk(self) -> None:
        import os
        import tempfile

        before = set(os.listdir(tempfile.gettempdir()))
        voice.decode(tone("webm", 1.0), "audio/webm")
        self.assertEqual(set(os.listdir(tempfile.gettempdir())) - before, set())


KNOWN = "m" * 32


def agreed(*answers: bool, cache_loaded: bool = False) -> Any:
    """A consent store stand-in. `consenting` is the real question (it loads the server's rows
    when they are not loaded yet); `has_consent` is the cache-only one, which says no until the
    cache is loaded (after a restart). The answers are given one per question, the last one
    repeating."""
    queue = list(answers) or [True]

    async def consenting(guild: int) -> frozenset[int]:
        answer = queue.pop(0) if len(queue) > 1 else queue[0]
        return frozenset({7}) if answer else frozenset()

    return SimpleNamespace(consenting=consenting, has_consent=lambda g, u: cache_loaded)


def fake_bot(*, consent: bool = True, heard: str = "we met Cara Dinval today") -> Any:
    names = lookup(more=(entity(KNOWN, "Caradinval"),), more_aliases=(alias(KNOWN, "Caradinval"),))
    transcribe = AsyncMock(return_value=heard)
    bot = SimpleNamespace(
        consent=agreed(consent),
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
        return rq.Request("a" * 32, "c" * 32, 100, 7, "", 1, "voice", data, "audio/webm")

    async def test_the_note_is_written_with_the_campaigns_names_and_fixed(self) -> None:
        bot = fake_bot()
        got = await DMBot.recap_voice_text(bot, THURSDAY.id, self.request(tone("webm", 1.5)))
        text, seconds = got
        self.assertEqual(text, "we met Caradinval today")  # a split name made whole
        self.assertAlmostEqual(seconds, 1.5, delta=0.1)
        hints = bot.transcribe.await_args.args[1]
        self.assertEqual(hints, ["Caradinval"])  # the name hints a live session would send

    async def test_after_a_restart_a_consenting_dm_is_not_refused(self) -> None:
        bot = fake_bot()  # the cache-only has_consent says no: nothing is loaded yet
        self.assertFalse(bot.consent.has_consent(THURSDAY.id, 7))
        text, _ = await DMBot.recap_voice_text(bot, THURSDAY.id, self.request(tone("webm", 1.5)))
        self.assertTrue(text)

    async def test_without_agreement_nothing_is_decoded_or_sent(self) -> None:
        bot = fake_bot(consent=False)
        with self.assertRaises(voice.ClipError) as err:
            await DMBot.recap_voice_text(bot, THURSDAY.id, self.request(tone("webm", 1.5)))
        self.assertEqual(err.exception.code, voice.NO_CONSENT)
        bot.transcribe.assert_not_awaited()

    async def test_consent_withdrawn_while_it_was_written_down_keeps_nothing(self) -> None:
        bot = fake_bot()
        bot.consent = agreed(True, True, True, False)  # stopped while it was written down
        with self.assertRaises(voice.ClipError) as err:
            await DMBot.recap_voice_text(bot, THURSDAY.id, self.request(tone("webm", 1.5)))
        self.assertEqual(err.exception.code, voice.NO_CONSENT)

    async def test_consent_withdrawn_while_the_names_load_sends_no_audio(self) -> None:
        state = {"agreed": True}
        bot = fake_bot()

        async def consenting(guild: int) -> frozenset[int]:
            return frozenset({7}) if state["agreed"] else frozenset()

        bot.consent = SimpleNamespace(consenting=consenting, has_consent=lambda g, u: False)

        async def hints(*_: Any) -> Any:
            state["agreed"] = False  # "Stop recording me" pressed during the wait
            return ["Caradinval"], None, []

        bot._recap_voice_hints = hints
        with self.assertRaises(voice.ClipError) as err:
            await DMBot.recap_voice_text(bot, THURSDAY.id, self.request(tone("webm", 1.5)))
        self.assertEqual(err.exception.code, voice.NO_CONSENT)
        bot.transcribe.assert_not_awaited()  # nothing was sent out

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


class TheHints(WithClips):
    async def hints(self, found: Any) -> Any:
        bot = SimpleNamespace(
            _hint_people=AsyncMock(return_value=(["Mia"], ["Dee"])),
            lookup=SimpleNamespace(get_within=AsyncMock(return_value=found)),
        )
        return await DMBot._recap_voice_hints(cast(Any, bot), THURSDAY.id, "c" * 32)

    async def test_a_secret_name_is_never_a_hint(self) -> None:
        hints, found, people = await self.hints(lookup())  # Belleros has a secret alias
        self.assertIsNotNone(found)
        self.assertTrue(any("Belleros" in h for h in hints))
        self.assertFalse(any("hooded" in h.lower() for h in hints))
        self.assertEqual(people, ["Mia", "Dee"])

    async def test_names_that_will_not_load_leave_people_only_and_no_fixing(self) -> None:
        hints, found, _people = await self.hints(None)
        self.assertEqual((hints, found), (["Mia", "Dee"], None))


class TheBackups(WithClips):
    def test_the_requests_are_left_out_of_backups(self) -> None:
        import pathlib

        root = pathlib.Path(__file__).resolve().parents[1] / "src" / "dmbot"
        files = [p for p in root.rglob("*.py") if "backup" in p.name]
        self.assertTrue(files)  # the backup code is found
        for path in files:
            self.assertNotIn("recap_requests", path.read_text(), path.name)


class TheLoop(WithClips):
    """A voice request goes to the voice path and never to the AI sort; clips are swept every
    round."""

    async def test_each_loop_claims_only_its_own_kind(self) -> None:
        await self.send_clip()
        self.assertIsNone(await self.requests.claim_next(THURSDAY.id, voice=False))
        got = await self.requests.claim_next(THURSDAY.id, voice=True)
        assert got is not None and got.kind == "voice"
        await self.send()
        self.assertIsNone(await self.requests.claim_next(THURSDAY.id, voice=True))
        typed = await self.requests.claim_next(THURSDAY.id, voice=False)
        assert typed is not None and typed.kind == "instructions"

    def bot(self, store: Any, guilds: Any = None) -> Any:
        bot: Any = SimpleNamespace(
            recap_requests=store,
            recap_feedback_ai=object(),
            recaps=object(),
            settings=SimpleNamespace(recaps_on=True),
            guilds=guilds if guilds is not None else [SimpleNamespace(id=THURSDAY.id)],
            _clip_sweep_rounds=0,
            _voice_tasks={},
            _voice_next=0,
            _voice_gate=MagicMock(),
            recap_voice_text=MagicMock(),
            _meter_voice=MagicMock(),
        )
        bot._run_voice_request = lambda gid, req: DMBot._run_voice_request(cast(Any, bot), gid, req)
        bot._voice_done = lambda gid, task: DMBot._voice_done(cast(Any, bot), gid, task)
        return bot

    async def test_a_stale_working_voice_row_is_retaken_only_by_the_voice_loop(self) -> None:
        await self.send_clip()
        first = await self.requests.claim_next(THURSDAY.id, voice=True)
        assert first is not None
        self.assertIsNone(await self.requests.claim_next(THURSDAY.id, voice=True))  # held
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute("UPDATE recap_requests SET claimed_at = 1")  # long ago
        self.assertIsNone(await self.requests.claim_next(THURSDAY.id, voice=False))
        again = await self.requests.claim_next(THURSDAY.id, voice=True)
        assert again is not None and again.id == first.id

    async def test_closing_the_bot_cancels_a_clip_being_written_down(self) -> None:
        import asyncio
        from unittest import mock

        from discord.ext import commands

        bot = object.__new__(DMBot)
        stub: Any = bot
        for name, value in {
            "_closing": False,
            "ai_watch": SimpleNamespace(wait=AsyncMock()),
            "plan_reads": {},
            "_eleven_http": None,
            "pipeline": SimpleNamespace(
                stop_waiting=MagicMock(), transcriber=SimpleNamespace(close=AsyncMock())
            ),
            "tables": {},
            "_finishing": [],
            "_lookups": {},
            "sidebar": SimpleNamespace(close=AsyncMock()),
            "_background": [],
            "_asking": [],
            "_loops": [],
            "ai_client": None,
            "ears": SimpleNamespace(send=AsyncMock(), stop=AsyncMock()),
        }.items():
            setattr(stub, name, value)
        clip = asyncio.create_task(asyncio.sleep(60))
        stub._voice_tasks = {THURSDAY.id: clip}
        with mock.patch.object(commands.AutoShardedBot, "close", AsyncMock()):
            await asyncio.wait_for(DMBot.close(bot), 5)
        self.assertTrue(clip.cancelled())

    async def test_the_typed_loop_claims_only_typed_notes_and_never_runs_a_clip(self) -> None:
        from unittest import mock

        typed = rq.Request("a" * 32, "c" * 32, 100, 7, "fix it", 1, "instructions", b"", "")
        claims = [typed, None]
        seen: list[Any] = []

        async def claim(gid: int, *, voice: bool | None = None) -> Any:
            seen.append(voice)
            return claims.pop(0)

        store = SimpleNamespace(claim_next=claim, sweep_clips=AsyncMock())
        bot = self.bot(store)
        with (
            mock.patch("dmbot.recaps.discord.recap_requests.run_voice", AsyncMock()) as v,
            mock.patch(
                "dmbot.recaps.discord.recap_requests.run_one", AsyncMock(return_value="done")
            ) as one,
        ):
            await DMBot.run_recap_requests(cast(Any, bot))
        self.assertEqual(seen, [False, False])  # typed only
        one.assert_awaited_once()
        v.assert_not_awaited()
        store.sweep_clips.assert_awaited_once_with()  # every server, in the same round

    async def test_a_voice_request_is_written_down_not_sorted_and_in_a_task_of_its_own(
        self,
    ) -> None:
        import asyncio
        from unittest import mock

        voice_request = rq.Request("a" * 32, "c" * 32, 100, 7, "", 1, "voice", CLIP, "audio/webm")
        claims = [voice_request]
        seen: list[Any] = []

        async def claim(gid: int, *, voice: bool | None = None) -> Any:
            seen.append(voice)
            return claims.pop(0) if claims else None

        store = SimpleNamespace(claim_next=claim, sweep_clips=AsyncMock())
        bot = self.bot(store)
        release = asyncio.Event()

        async def slow(*a: Any, **k: Any) -> str:
            await release.wait()
            return "done"

        with (
            mock.patch("dmbot.recaps.discord.recap_requests.run_voice", slow),
            mock.patch("dmbot.recaps.discord.recap_requests.run_one", AsyncMock()) as sorted_,
        ):
            await asyncio.wait_for(DMBot.run_recap_voice(cast(Any, bot)), 2)
            self.assertEqual(seen, [True])  # voice only
            self.assertEqual(list(bot._voice_tasks), [THURSDAY.id])
            # a slow clip does not hold up the round: a typed note is worked while it runs
            typed = rq.Request("b" * 32, "c" * 32, 100, 7, "fix", 2, "instructions", b"", "")
            run_one = AsyncMock(return_value="done")
            with mock.patch("dmbot.recaps.discord.recap_requests.run_one", run_one):
                typed_claims = [typed, None]

                async def claim_typed(gid: int, *, voice: bool | None = None) -> Any:
                    return typed_claims.pop(0)

                store.claim_next = claim_typed
                await asyncio.wait_for(DMBot.run_recap_requests(cast(Any, bot)), 2)
            run_one.assert_awaited_once()
            self.assertFalse(bot._voice_tasks[THURSDAY.id].done())  # the clip is still being done
            # and the same server gets no second clip while one runs
            claims.append(voice_request)
            store.claim_next = claim
            await asyncio.wait_for(DMBot.run_recap_voice(cast(Any, bot)), 2)
            self.assertEqual(len(claims), 1)  # not claimed
            release.set()
            await asyncio.sleep(0.05)
            self.assertEqual(bot._voice_tasks, {})
        sorted_.assert_not_awaited()  # never the AI sort

    async def test_servers_take_turns_and_a_failing_clip_frees_its_slot(self) -> None:
        import asyncio
        from unittest import mock

        guilds = [SimpleNamespace(id=2000 + n) for n in range(5)]
        order: list[int] = []

        async def claim(gid: int, *, voice: bool | None = None) -> Any:
            order.append(gid)
            return rq.Request(f"{gid:032x}", "c" * 32, 100, 7, "", 1, "voice", CLIP, "audio/webm")

        bot = self.bot(SimpleNamespace(claim_next=claim, sweep_clips=AsyncMock()), guilds)

        async def boom(*a: Any, **k: Any) -> str:
            raise RuntimeError("decoder fell over")

        with mock.patch("dmbot.recaps.discord.recap_requests.run_voice", boom):
            await asyncio.wait_for(DMBot.run_recap_voice(cast(Any, bot)), 2)
            await asyncio.sleep(0.05)
            self.assertEqual(bot._voice_tasks, {})  # the failure freed every slot
            await DMBot.run_recap_voice(cast(Any, bot))  # the next round goes on from the next one
        self.assertEqual(order[:3], [2000, 2001, 2002])
        self.assertEqual(order[3:6], [2003, 2004, 2000])

    async def test_only_a_few_clips_are_written_down_at_once_across_servers(self) -> None:
        import asyncio
        from unittest import mock

        from dmbot.recaps.discord import RECAP_VOICE_AT_ONCE

        guilds = [SimpleNamespace(id=1000 + n) for n in range(RECAP_VOICE_AT_ONCE + 2)]
        made = []

        async def claim(gid: int, *, voice: bool | None = None) -> Any:
            made.append(gid)
            return rq.Request(f"{gid:032x}", "c" * 32, 100, 7, "", 1, "voice", CLIP, "audio/webm")

        bot = self.bot(SimpleNamespace(claim_next=claim, sweep_clips=AsyncMock()), guilds)
        release = asyncio.Event()

        async def slow(*a: Any, **k: Any) -> str:
            await release.wait()
            return "done"

        with mock.patch("dmbot.recaps.discord.recap_requests.run_voice", slow):
            await asyncio.wait_for(DMBot.run_recap_voice(cast(Any, bot)), 2)
            self.assertEqual(len(bot._voice_tasks), RECAP_VOICE_AT_ONCE)
            self.assertEqual(len(made), RECAP_VOICE_AT_ONCE)  # the rest were not even claimed
            release.set()
            await asyncio.sleep(0.05)
        self.assertEqual(bot._voice_tasks, {})

    async def test_a_clip_in_a_server_the_bot_is_not_in_is_cleared_too(self) -> None:
        from dmbot.campaigns.store import CampaignStore

        elsewhere = 987_654_321
        other = await CampaignStore(self.db).create(elsewhere, "Elsewhere", 5)
        async with self.db.guild(elsewhere) as conn:
            await conn.execute(
                "INSERT INTO recap_requests (guild_id, campaign_id, id, session_at, user_id, kind,"
                " body, clip, clip_type, created_at) VALUES (%s, %s, %s, 1, 5, 'voice', '',"
                " %s, 'audio/webm', %s)",
                (elsewhere, other.id, "e" * 32, CLIP, int(time.time()) - 700),
            )
            await conn.execute(
                "INSERT INTO recap_requests (guild_id, campaign_id, id, session_at, user_id, kind,"
                " body, clip, clip_type, created_at) VALUES (%s, %s, %s, 1, 5, 'voice', '',"
                " %s, 'audio/webm', %s)",
                (elsewhere, other.id, "f" * 32, CLIP, int(time.time())),
            )
        self.assertEqual(await self.requests.sweep_clips(), 1)  # only the old one
        async with self.db.guild(elsewhere) as conn:
            cur = await conn.execute("SELECT id, clip IS NULL AS gone FROM recap_requests")
            rows = {r["id"]: r["gone"] for r in await cur.fetchall()}
        self.assertEqual(rows, {"e" * 32: True, "f" * 32: False})

    async def test_the_sweep_function_is_the_bots_not_the_websites_and_only_empties_clips(
        self,
    ) -> None:
        import psycopg

        rid = await self.send_clip()
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute(
                "UPDATE recap_requests SET created_at = created_at - 700 WHERE id = %s", (rid,)
            )
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            async with self.web.unscoped() as conn:
                await conn.execute("SELECT dmbot_sweep_old_clips()")
        self.assertEqual(await self.requests.sweep_clips(), 1)
        self.assertEqual(await self.requests.sweep_clips(), 0)  # nothing left to empty
        async with self.db.guild(THURSDAY.id) as conn:
            cur = await conn.execute(
                "SELECT state, kind, body, clip_type FROM recap_requests WHERE id = %s", (rid,)
            )
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual(
            (row["state"], row["kind"], row["body"], row["clip_type"]),
            ("waiting", "voice", "", "audio/webm"),  # nothing else about the request changed
        )
        self.assertIsNone(await self.clip_in_row(rid))
        # The marker the function sets is gone once it ends: a later statement sees nothing.
        async with self.db.unscoped() as conn:
            cur = await conn.execute("SELECT dmbot_cleanup() AS c")
            marker = await cur.fetchone()
        assert marker is not None
        self.assertFalse(marker["c"])

    async def test_the_sweep_clears_only_old_clips(self) -> None:
        old, fresh = await self.send_clip(), None
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute(
                "UPDATE recap_requests SET created_at = created_at - 601 WHERE id = %s", (old,)
            )
        fresh = await self.send_clip()
        await self.requests.sweep_clips()
        self.assertIsNone(await self.clip_in_row(old))
        self.assertEqual(await self.clip_in_row(fresh), CLIP)


class TheRoutes(WithClips):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        import httpx

        from dmbot.web.app import create_app
        from dmbot.web.payments import FakeProvider
        from tests.test_recap_web import HEADERS
        from tests.test_web_api import API, SITE, FakeDiscord, settings
        from tests.test_web_recap_requests import fx_alice

        app = create_app(
            settings(), self.web, FakeDiscord(), payments=FakeProvider(b"h" * 32, SITE),
            clock=lambda: NOW,
        )  # fmt: skip
        token = await sessions.sign_in(
            self.web, cast(Any, fx_alice()), [THURSDAY], now=NOW, days=30
        )
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=API, headers=HEADERS
        )
        self.client.cookies.set("__Host-dmbot_session", token, domain="api.dmbot.example")
        self.url = f"/me/recaps/{self.ref}/voice"

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        await super().asyncTearDown()

    async def test_a_clip_is_accepted_with_202_and_polled(self) -> None:
        got = await self.client.post(
            self.url, content=CLIP, headers={"content-type": "audio/webm;codecs=opus"}
        )
        self.assertEqual(got.status_code, 202, got.text)
        poll = await self.client.get(f"{self.url}/{got.json()['requestId']}")
        self.assertEqual((poll.status_code, poll.json()), (200, {"state": "waiting"}))

    async def test_the_content_type_decides(self) -> None:
        for kind in ("audio/mpeg", "application/json", ""):
            got = await self.client.post(self.url, content=CLIP, headers={"content-type": kind})
            self.assertEqual((got.status_code, got.json()["error"]), (415, "bad_type"), kind)

    async def test_the_size_is_capped_by_its_declared_length_and_while_streaming(self) -> None:
        big = b"x" * (voice.MAX_CLIP_BYTES + 1)
        declared = await self.client.post(
            self.url, content=big, headers={"content-type": "audio/ogg"}
        )
        self.assertEqual((declared.status_code, declared.json()["error"]), (413, "too_big"))

        async def chunks() -> Any:
            for _ in range(3):
                yield b"x" * (1024 * 1024)  # no length up front

        streamed = await self.client.post(
            self.url, content=chunks(), headers={"content-type": "audio/ogg"}
        )
        self.assertEqual((streamed.status_code, streamed.json()["error"]), (413, "too_big"))

    async def test_an_empty_body_a_dm_who_has_not_agreed_and_too_many(self) -> None:
        empty = await self.client.post(
            self.url, content=b"", headers={"content-type": "audio/webm"}
        )
        self.assertEqual((empty.status_code, empty.json()["error"]), (422, "bad_clip"))
        await ConsentStore(self.db).revoke(THURSDAY.id, ALICE.id)
        no = await self.client.post(self.url, content=CLIP, headers={"content-type": "audio/webm"})
        self.assertEqual((no.status_code, no.json()["error"]), (403, "no_consent"))
        await ConsentStore(self.db).grant(THURSDAY.id, ALICE.id)
        for _ in range(3):
            got = await self.client.post(
                self.url, content=CLIP, headers={"content-type": "audio/webm"}
            )
            self.assertEqual(got.status_code, 202)
        fourth = await self.client.post(
            self.url, content=CLIP, headers={"content-type": "audio/webm"}
        )
        self.assertEqual((fourth.status_code, fourth.json()["error"]), (429, "too_soon"))

    async def test_the_route_passes_the_engine_in_use_to_the_consent_check(self) -> None:
        import dataclasses

        import httpx

        from dmbot.web.app import create_app
        from dmbot.web.payments import FakeProvider
        from tests.test_recap_web import HEADERS
        from tests.test_web_api import API, SITE, FakeDiscord, settings
        from tests.test_web_recap_requests import fx_alice

        app = create_app(
            dataclasses.replace(settings(), outside_engine="deepgram"), self.web, FakeDiscord(),
            payments=FakeProvider(b"h" * 32, SITE), clock=lambda: NOW,
        )  # fmt: skip
        token = await sessions.sign_in(
            self.web, cast(Any, fx_alice()), [THURSDAY], now=NOW, days=30
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=API, headers=HEADERS
        ) as client:
            client.cookies.set("__Host-dmbot_session", token, domain="api.dmbot.example")
            asked = await client.post(
                self.url, content=CLIP, headers={"content-type": "audio/webm"}
            )
            # The setUp consent names no company, and the engine in use is Deepgram.
            self.assertEqual((asked.status_code, asked.json()["error"]), (403, "no_consent"))
            await ConsentStore(self.db).grant(THURSDAY.id, ALICE.id, outside_to="deepgram")
            ok = await client.post(self.url, content=CLIP, headers={"content-type": "audio/webm"})
            self.assertEqual(ok.status_code, 202, ok.text)


class TheDeepgramTimeout(unittest.TestCase):
    def test_a_live_line_keeps_the_short_limit_and_a_long_clip_gets_time(self) -> None:
        from dmbot.transcription import deepgram

        self.assertEqual(deepgram.request_timeout_s(3.0), deepgram.REQUEST_TIMEOUT_S)
        self.assertEqual(deepgram.request_timeout_s(20.0), deepgram.REQUEST_TIMEOUT_S)
        self.assertGreater(deepgram.request_timeout_s(60.0), 20)
        self.assertGreaterEqual(deepgram.request_timeout_s(120.0), 50)
        self.assertEqual(deepgram.request_timeout_s(10_000.0), deepgram.LONG_REQUEST_MAX_S)
        # inside the time the whole job is given (decode, hints, request, name fixing)
        from dmbot.recaps.discord import VOICE_TRANSCRIBE_S
        from dmbot.recaps.requests import VOICE_TIMEOUT_S

        self.assertLess(deepgram.LONG_REQUEST_MAX_S, VOICE_TRANSCRIBE_S)
        self.assertLess(VOICE_TRANSCRIBE_S, VOICE_TIMEOUT_S)


class TheSweepRounds(WithClips):
    def bot(self, *, recaps_on: bool) -> Any:
        store = SimpleNamespace(claim_next=AsyncMock(return_value=None), sweep_clips=AsyncMock())
        return SimpleNamespace(
            recap_requests=store,
            recap_feedback_ai=None,
            recaps=object(),
            settings=SimpleNamespace(recaps_on=recaps_on),
            guilds=[SimpleNamespace(id=THURSDAY.id)],
            _clip_sweep_rounds=0,
        )

    async def test_clips_are_swept_about_once_a_minute_even_with_recaps_off(self) -> None:
        from dmbot.recaps.discord import CLIP_SWEEP_EVERY

        bot = self.bot(recaps_on=False)
        for _ in range(CLIP_SWEEP_EVERY):
            await DMBot.run_recap_requests(bot)
        self.assertEqual(bot.recap_requests.sweep_clips.await_count, 1)  # the first round only
        await DMBot.run_recap_requests(bot)
        self.assertEqual(bot.recap_requests.sweep_clips.await_count, 2)  # and one a minute on


class TheDeepgramSession(unittest.IsolatedAsyncioTestCase):
    async def test_every_request_carries_a_timeout_a_long_clip_a_longer_one(self) -> None:
        import aiohttp

        from dmbot.audio.segmenter import Utterance
        from dmbot.transcription.config import TranscriptionSettings
        from dmbot.transcription.deepgram import DeepgramTranscriber, request_timeout_s

        seen: list[aiohttp.ClientTimeout] = []

        class Response:
            status = 200

            async def json(self, content_type: str | None = None) -> Any:
                return {"results": {"channels": [{"alternatives": [{"transcript": "hi"}]}]}}

            async def __aenter__(self) -> Response:
                return self

            async def __aexit__(self, *_: Any) -> None:
                return None

        class Session:
            closed = False
            timeout = aiohttp.ClientTimeout(total=4, connect=2)

            def post(self, url: str, **kwargs: Any) -> Response:
                seen.append(kwargs["timeout"])
                return Response()

        engine = DeepgramTranscriber(
            TranscriptionSettings(engine="deepgram", deepgram_api_key="dg-test"),
            cast(Any, Session()),
        )
        short = Utterance(1, 2, 0, 0, bytes(32_000 * 3))  # a real 3 second line
        long = Utterance(1, 2, 0, 0, bytes(32_000 * 90))  # 90 seconds of 16 kHz audio
        await engine.transcribe(short, [])
        await engine.transcribe(long, [])
        self.assertEqual(seen[0].total, 4)  # a live line keeps the session's limit
        self.assertEqual(seen[1].total, request_timeout_s(90.0))  # a long clip is given time
        self.assertGreater(request_timeout_s(90.0), 30)
