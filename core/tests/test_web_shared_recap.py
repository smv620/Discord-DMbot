"""A posted recap's public, read-only link (#2118)."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, cast

import httpx
import psycopg

from dmbot.consent import TERMS_VERSION
from dmbot.recaps.approval import text_hash
from dmbot.recaps.audio.files import KINDS, AudioFiles
from dmbot.transcript.models import Line
from dmbot.transcript.store import TranscriptStore
from dmbot.web import sessions
from dmbot.web.app import create_app
from dmbot.web.payments import FakeProvider
from tests import recap_fixtures as fx
from tests.test_recap_web import BOB, HEADERS, NOW, Base, callers
from tests.test_web_api import ALICE, API, SITE, THURSDAY, FakeDiscord, settings


class Shared(Base):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.app = create_app(
            settings(recap_audio_dir=self.dir.name), self.web, FakeDiscord(),
            payments=FakeProvider(b"h" * 32, SITE), clock=lambda: NOW,
        )  # fmt: skip
        token = await sessions.sign_in(self.web, ALICE, [THURSDAY], now=NOW, days=30)
        self.dm = self.client_for(token)
        bob = await sessions.sign_in(self.web, cast(Any, BOB), [THURSDAY], now=NOW, days=30)
        self.bob = self.client_for(bob)
        self.public = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url=API, headers={}
        )  # no cookie, no header: anyone
        self.files = AudioFiles(Path(self.dir.name))
        async with self.db.guild(THURSDAY.id) as conn:  # the speaker is recorded (consent given)
            await conn.execute(
                "INSERT INTO consent (guild_id, user_id, granted_at, terms_version)"
                " VALUES (%s, %s, %s, %s)",
                (THURSDAY.id, fx.ALICE, NOW, TERMS_VERSION),
            )

    def client_for(self, token: str) -> httpx.AsyncClient:
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url=API, headers=HEADERS
        )
        client.cookies.set("__Host-dmbot_session", token, domain="api.dmbot.example")
        return client

    async def asyncTearDown(self) -> None:
        for client in (self.dm, self.bob, self.public):
            await client.aclose()
        await super().asyncTearDown()

    async def post_it(self) -> None:
        await self.store.decide(THURSDAY.id, self.one.id, 100, "post")
        async with self.db.guild(THURSDAY.id) as conn:  # the bot has sent it
            await conn.execute("UPDATE recap_jobs SET status = 'posted', decided_at = 777")

    async def share(self, action: str, client: httpx.AsyncClient | None = None) -> httpx.Response:
        return await (client or self.dm).post(
            f"/me/recaps/{self.ref}/share", json={"action": action}
        )

    async def token(self) -> str:
        got = await self.share("on")
        assert got.status_code == 200, got.text
        return str(got.json()["path"]).removeprefix("/r/")

    async def make_secret(self, text: str = "Mira") -> None:
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute(
                "INSERT INTO memory_entities (guild_id, campaign_id, id, type, name, description,"
                " status, source, created_at) VALUES (%s, %s, %s, 'npc', %s, '', 'confirmed',"
                " 'dm', 0)",
                (THURSDAY.id, self.one.id, "e" * 32, text),
            )
            await conn.execute(
                "INSERT INTO memory_aliases (guild_id, campaign_id, id, entity_id, text, key,"
                " kind, secret, status, sound_codes, source, created_at) VALUES (%s, %s, %s, %s,"
                " %s, %s, 'full', true, 'confirmed', '{}', 'dm', 0)",
                (THURSDAY.id, self.one.id, "a" * 32, "e" * 32, text, text.lower()),
            )


class TheLink(Shared):
    async def test_a_draft_has_no_link(self) -> None:
        got = await self.share("on")
        self.assertEqual((got.status_code, got.json()["error"]), (409, "not_posted"))
        view = (await self.dm.get(f"/me/recaps/{self.ref}")).json()
        self.assertEqual(view["share"], {"on": False, "path": None})

    async def test_a_posted_recap_gets_a_link_that_works_with_no_sign_in(self) -> None:
        await self.post_it()
        token = await self.token()
        self.assertGreaterEqual(len(token), 43)  # 256 bits
        got = await self.public.get(f"/recaps/{token}")
        self.assertEqual(got.status_code, 200, got.text)
        body = got.json()
        self.assertEqual(body["title"], "The Silver Lock")
        self.assertEqual(body["postedAt"], 777)
        self.assertNotIn("《", body["text"])
        self.assertEqual(
            set(body),
            {"title", "text", "postedAt", "audio", "sessionAt", "sessionNumber", "sessionSeconds"},
        )  # the table version only
        # The signed-out door knows only the day; the number and length wait for its migration.
        self.assertEqual(body["sessionAt"], 100)
        self.assertIsNone(body["sessionNumber"])
        self.assertIsNone(body["sessionSeconds"])
        self.assertNotIn("Mira drew her blade.", got.text)  # not the housekeeping list
        self.assertEqual(got.headers["x-robots-tag"], "noindex, nofollow")
        self.assertIn("no-store", got.headers["cache-control"])

    async def test_the_shared_page_says_which_session_it_was_and_how_long_it_ran(self) -> None:
        """(#2418) The signed-out door gives the transcripts' own number and the session's length
        and nothing else of the transcript; another campaign's session at the same time is not
        mixed in, and a session still listening has no length."""
        transcripts = TranscriptStore(self.db)

        async def played(campaign_id: str, start: int, end: int | None) -> str:
            sid = await transcripts.open_session(THURSDAY.id, campaign_id, start)
            await transcripts.add_lines(
                THURSDAY.id, sid, [Line(start * 1000 + 1000, ALICE.id, "we go", "we go")]
            )
            if end is not None:
                await transcripts.end_session(THURSDAY.id, sid, end)
            return sid

        # An earlier session whose transcript retention has deleted still counts, as on the
        # transcripts page ("Session 1" stays 1), so this recap's number does not slip.
        gone = await played(self.one.id, 20, 40)
        await transcripts.delete_session_transcript(THURSDAY.id, self.one.id, gone, 10**6, 300)
        await played(self.one.id, 50, 90)  # Session 2 of "One"
        await played(self.one.id, 100, 100 + 2 * 3600 + 35 * 60)  # Session 3: this recap's
        await played(self.two.id, 100, 130)  # another campaign's, at the same time
        await self.post_it()
        token = await self.token()
        body = (await self.public.get(f"/recaps/{token}")).json()
        self.assertEqual((body["sessionNumber"], body["sessionSeconds"]), (3, 9300))
        self.assertEqual(
            set(body),
            {"title", "text", "postedAt", "audio", "sessionAt", "sessionNumber", "sessionSeconds"},
        )  # no line, speaker or id of the transcript
        self.assertNotIn("we go", (await self.public.get(f"/recaps/{token}")).text)
        # Still listening: the number is known and the length is not.
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute(
                "UPDATE transcript_sessions SET ended_at = NULL WHERE campaign_id = %s"
                " AND started_at = 100",
                (self.one.id,),
            )
        body = (await self.public.get(f"/recaps/{token}")).json()
        self.assertEqual((body["sessionNumber"], body["sessionSeconds"]), (3, None))

    async def test_asking_again_for_the_link_gives_the_same_one(self) -> None:
        await self.post_it()
        first = await self.token()
        self.assertEqual(await self.token(), first)
        view = (await self.dm.get(f"/me/recaps/{self.ref}")).json()
        self.assertEqual(view["share"], {"on": True, "path": f"/r/{first}"})

    async def test_off_and_new_end_the_old_link(self) -> None:
        await self.post_it()
        old = await self.token()
        new = (await self.share("new")).json()["path"].removeprefix("/r/")
        self.assertNotEqual(new, old)
        self.assertEqual((await self.public.get(f"/recaps/{old}")).status_code, 404)
        self.assertEqual((await self.public.get(f"/recaps/{new}")).status_code, 200)
        off = await self.share("off")
        self.assertEqual(off.json(), {"on": False, "path": None})
        self.assertEqual((await self.public.get(f"/recaps/{new}")).status_code, 404)

    async def test_every_failure_answers_alike(self) -> None:
        await self.post_it()
        token = await self.token()
        await self.share("off")
        answers = []
        for t in (token, "x" * 43, "short", "A" * 43, "é" * 43):
            got = await self.public.get(f"/recaps/{t}")
            answers.append((got.status_code, got.text))
        self.assertEqual(len(set(answers)), 1)
        self.assertEqual(answers[0][0], 404)
        self.assertEqual(answers[0][1], '{"error":"not_found"}')

    async def test_a_secret_added_later_makes_the_read_return_nothing(self) -> None:
        await self.post_it()
        token = await self.token()
        self.assertEqual((await self.public.get(f"/recaps/{token}")).status_code, 200)
        await self.make_secret("Mira")
        got = await self.public.get(f"/recaps/{token}")
        self.assertEqual((got.status_code, got.text), (404, '{"error":"not_found"}'))

    async def test_a_name_the_players_dont_know_yet_makes_the_read_return_nothing(self) -> None:
        """#2321: the DM's "Players don't know yet" entry is secret to the public page too, though
        none of its names is marked secret."""
        await self.post_it()
        token = await self.token()
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute(
                "INSERT INTO memory_entities (guild_id, campaign_id, id, type, name, description,"
                " status, source, created_at, hidden_from_players) VALUES (%s, %s, %s, 'npc', %s,"
                " '', 'confirmed', 'dm', 0, true)",
                (THURSDAY.id, self.one.id, "f" * 32, "Mira"),
            )
            await conn.execute(
                "INSERT INTO memory_aliases (guild_id, campaign_id, id, entity_id, text, key,"
                " kind, secret, status, sound_codes, source, created_at) VALUES (%s, %s, %s, %s,"
                " %s, %s, 'full', false, 'confirmed', '{}', 'dm', 0)",
                (THURSDAY.id, self.one.id, "b" * 32, "f" * 32, "Mira", "mira"),
            )
        got = await self.public.get(f"/recaps/{token}")
        self.assertEqual((got.status_code, got.text), (404, '{"error":"not_found"}'))

    async def test_a_recap_dropped_after_posting_cannot_be_read(self) -> None:
        await self.post_it()
        token = await self.token()
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute("UPDATE recap_jobs SET status = 'draft'")
        self.assertEqual((await self.public.get(f"/recaps/{token}")).status_code, 404)

    async def test_only_a_dm_of_the_campaign_may_use_the_controls(self) -> None:
        await self.post_it()
        for action in ("on", "off", "new"):
            got = await self.share(action, self.bob)  # Bob DMs the other campaign
            self.assertEqual(got.status_code, 404, action)
        self.assertEqual(
            (
                await self.public.post(
                    f"/me/recaps/{self.ref}/share", json={"action": "on"}, headers=HEADERS
                )
            ).status_code,
            401,
        )

    async def test_on_and_new_run_the_checks_post_makes(self) -> None:
        await self.post_it()
        await self.make_secret("Mira")
        for action in ("on", "new"):
            got = await self.share(action)
            self.assertEqual((got.status_code, got.json()["error"]), (409, "names_secret"), action)
        self.assertEqual(
            (await self.share("off")).status_code, 200
        )  # turning off is always allowed

    async def test_a_player_who_stopped_being_recorded_stops_the_sharing(self) -> None:
        await self.post_it()
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute("DELETE FROM consent WHERE user_id = %s", (fx.ALICE,))
        got = await self.share("on")
        self.assertEqual((got.status_code, got.json()["error"]), (409, "stopped_recording"))

    async def test_a_bad_action_is_refused(self) -> None:
        await self.post_it()
        got = await self.share("sideways")
        self.assertEqual((got.status_code, got.json()["error"]), (422, "bad_action"))

    async def test_one_token_finds_one_recap(self) -> None:
        await self.post_it()
        mine = await self.token()
        # Bob's recap in the other campaign is posted and shared too.
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute(
                "UPDATE recap_jobs SET status = 'posted', decided_at = 5, title = 'The Other Lock'"
                " WHERE campaign_id = %s",
                (self.two.id,),
            )
        theirs = await self.share("on", self.bob)
        self.assertEqual(theirs.status_code, 404)  # Bob's session names his own recap's ref
        other_ref = self.other
        got = await self.bob.post(f"/me/recaps/{other_ref}/share", json={"action": "on"})
        other = got.json()["path"].removeprefix("/r/")
        self.assertNotEqual(other, mine)
        self.assertEqual(
            (await self.public.get(f"/recaps/{mine}")).json()["title"], "The Silver Lock"
        )
        self.assertEqual(
            (await self.public.get(f"/recaps/{other}")).json()["title"], "The Other Lock"
        )
        body = (await self.public.get(f"/recaps/{mine}")).text
        self.assertNotIn(self.one.id, body)
        self.assertNotIn(str(THURSDAY.id), body)

    async def test_the_web_role_cannot_read_recaps_by_setting_the_settings_itself(self) -> None:
        await self.post_it()
        token = await self.token()
        async with self.web.anonymous() as conn:
            await conn.execute("SELECT set_config('dmbot.recap_share_tok', %s, true)", (token,))
            await conn.execute(
                "SELECT set_config('dmbot.recap_share_guild', %s, true)", (str(THURSDAY.id),)
            )
            await conn.execute(
                "SELECT set_config('dmbot.recap_share_campaign', %s, true)", (self.one.id,)
            )
            cur = await conn.execute("SELECT count(*) AS n FROM recap_jobs")
            self.assertEqual((await cur.fetchone() or {})["n"], 0)  # (the rules hide every row)
        for table in ("memory_aliases", "memory_entities"):
            async with self.web.anonymous() as conn:  # and it has no right on the names at all
                with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                    await conn.execute(f"SELECT count(*) FROM {table}")

    async def test_a_name_limited_to_some_players_is_a_secret_too(self) -> None:
        await self.post_it()
        token = await self.token()
        await self.make_secret("Mira")
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute("UPDATE memory_aliases SET secret = false")
            await conn.execute("UPDATE memory_entities SET audience_limited = true")
        self.assertEqual((await self.public.get(f"/recaps/{token}")).status_code, 404)

    async def test_a_secret_inside_a_longer_confirmed_name_is_that_name(self) -> None:
        await self.post_it()
        token = await self.token()
        await self.make_secret("Greywater")  # the recap says "Greywater Keep"
        async with self.db.guild(THURSDAY.id) as conn:  # ... which the DM has confirmed
            await conn.execute(
                "INSERT INTO memory_entities (guild_id, campaign_id, id, type, name, description,"
                " status, source, created_at) VALUES (%s, %s, %s, 'place', 'Greywater Keep', '',"
                " 'confirmed', 'dm', 0)",
                (THURSDAY.id, self.one.id, "f" * 32),
            )
            await conn.execute(
                "INSERT INTO memory_aliases (guild_id, campaign_id, id, entity_id, text, key,"
                " kind, secret, status, sound_codes, source, created_at) VALUES (%s, %s, %s, %s,"
                " 'Greywater Keep', 'greywater keep', 'full', false, 'confirmed', '{}', 'dm', 0)",
                (THURSDAY.id, self.one.id, "b" * 32, "f" * 32),
            )
        got = await self.public.get(f"/recaps/{token}")
        self.assertEqual(got.status_code, 200)  # (the confirmed place is not the secret)

    async def test_the_public_read_is_limited_per_address(self) -> None:
        await self.post_it()
        token = await self.token()
        codes = [(await self.public.get(f"/recaps/{token}")).status_code for _ in range(32)]
        self.assertEqual(codes[:30], [200] * 30)
        self.assertEqual(codes[30:], [429, 429])

    async def test_the_doors_have_one_caller_each(self) -> None:
        self.assertEqual(callers("dmbot_recap_shared"), {"schema.py", "web/shared_recaps.py"})
        self.assertEqual(callers("dmbot_recap_share("), {"schema.py", "web/recaps.py"})


class TheAudio(Shared):
    async def make_audio(self, *, choice: str = "both", current: bool = True) -> None:
        job = await self.state(self.one.id)
        digest = text_hash(job.title, job.summary) if current else "0" * 64
        async with self.db.guild(THURSDAY.id) as conn:  # (set after Post too: the store won't)
            await conn.execute(
                "UPDATE recap_jobs SET audio = 'ready', audio_text_hash = %s, audio_choice = %s"
                " WHERE campaign_id = %s",
                (digest, choice, self.one.id),
            )
        for kind in KINDS:
            path = self.files.path(THURSDAY.id, self.one.id, 100, kind)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(f"audio-{kind}".encode())

    async def test_the_audio_is_served_by_the_token_alone(self) -> None:
        await self.post_it()
        await self.make_audio()
        token = await self.token()
        audio: dict[str, Any] = (await self.public.get(f"/recaps/{token}")).json()["audio"]
        self.assertEqual(set(audio), {"voice", "music"})
        for kind, url in audio.items():
            got = await self.public.get(url.removeprefix(API))
            self.assertEqual((got.status_code, got.content), (200, f"audio-{kind}".encode()))
            self.assertEqual(got.headers["content-type"], "audio/mpeg")
            self.assertEqual(got.headers["x-robots-tag"], "noindex, nofollow")
        self.assertNotIn(self.one.id, str(audio))  # nothing about the campaign in the address

    async def test_the_audio_stops_with_the_link_and_with_a_secret(self) -> None:
        await self.post_it()
        await self.make_audio()
        token = await self.token()
        kind = KINDS[0]
        self.assertEqual((await self.public.get(f"/recaps/{token}/audio/{kind}")).status_code, 200)
        await self.make_secret("Mira")
        self.assertEqual((await self.public.get(f"/recaps/{token}/audio/{kind}")).status_code, 404)

    async def test_the_audio_stops_when_the_link_is_turned_off(self) -> None:
        await self.post_it()
        await self.make_audio()
        token = await self.token()
        await self.share("off")
        self.assertEqual(
            (await self.public.get(f"/recaps/{token}/audio/{KINDS[0]}")).status_code, 404
        )

    async def test_audio_that_is_not_of_this_text_or_not_chosen_is_not_offered(self) -> None:
        await self.post_it()
        token = await self.token()
        for kw in ({"current": False}, {"choice": "text"}):
            await self.make_audio(**kw)
            self.assertEqual((await self.public.get(f"/recaps/{token}")).json()["audio"], {}, kw)
            self.assertEqual(
                (await self.public.get(f"/recaps/{token}/audio/{KINDS[0]}")).status_code, 404, kw
            )

    async def test_the_mix_is_never_public_and_audio_off_withdraws_the_rest(self) -> None:
        await self.post_it()
        await self.make_audio()
        token = await self.token()
        audio = (await self.public.get(f"/recaps/{token}")).json()["audio"]
        self.assertEqual(set(audio), {"voice", "music"})
        self.assertEqual((await self.public.get(f"/recaps/{token}/audio/mix")).status_code, 404)
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute("UPDATE campaigns SET recaps_audio = false")
        self.assertEqual((await self.public.get(f"/recaps/{token}")).json()["audio"], {})
        self.assertEqual((await self.public.get(f"/recaps/{token}/audio/voice")).status_code, 404)

    async def test_the_share_door_refuses_a_missing_token(self) -> None:
        await self.post_it()
        async with self.web.user(self.session.user_id, session=self.session.id_hash) as conn:
            await conn.execute("SELECT set_config('dmbot.guild_id', %s, true)", (str(THURSDAY.id),))
            cur = await conn.execute(
                "SELECT dmbot_recap_share(%s, %s, 100, 'on', NULL) AS r", (THURSDAY.id, self.one.id)
            )
            self.assertIsNone((await cur.fetchone() or {})["r"])

    async def test_an_unknown_kind_or_token_is_not_found(self) -> None:
        await self.post_it()
        await self.make_audio()
        token = await self.token()
        for path in (f"/recaps/{token}/audio/evil", f"/recaps/{'A' * 43}/audio/{KINDS[0]}"):
            self.assertEqual((await self.public.get(path)).status_code, 404, path)


if __name__ == "__main__":
    import unittest

    unittest.main()
