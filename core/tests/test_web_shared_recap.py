"""A posted recap's public, read-only link (#2118)."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import httpx

from dmbot.recaps.audio.files import KINDS, AudioFiles
from dmbot.web import sessions
from dmbot.web.app import create_app
from dmbot.web.payments import FakeProvider
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
        bob = await sessions.sign_in(self.web, BOB, [THURSDAY], now=NOW, days=30)
        self.bob = self.client_for(bob)
        self.public = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url=API, headers={}
        )  # no cookie, no header: anyone
        self.files = AudioFiles(Path(self.dir.name))

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
            set(body), {"title", "text", "postedAt", "audio"}
        )  # the table version only
        self.assertNotIn("Mira drew her blade.", got.text)  # not the housekeeping list
        self.assertEqual(got.headers["x-robots-tag"], "noindex, nofollow")
        self.assertIn("no-store", got.headers["cache-control"])

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

    async def test_a_bad_action_is_refused(self) -> None:
        await self.post_it()
        got = await self.share("sideways")
        self.assertEqual((got.status_code, got.json()["error"]), (422, "bad_action"))

    async def test_one_token_finds_one_recap(self) -> None:
        await self.post_it()
        mine = await self.token()
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute("UPDATE recap_jobs SET status = 'posted', decided_at = 5")
        other = (await self.share("on", self.dm)).json()["path"]
        self.assertEqual(other, f"/r/{mine}")
        body = (await self.public.get(f"/recaps/{mine}")).json()
        self.assertNotIn("campaign", body)
        self.assertNotIn("guild", body)

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
    async def make_audio(self) -> None:
        for kind in KINDS:
            path = self.files.path(THURSDAY.id, self.one.id, 100, kind)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(f"audio-{kind}".encode())

    async def test_the_audio_is_served_by_the_token_alone(self) -> None:
        await self.post_it()
        await self.make_audio()
        token = await self.token()
        audio: dict[str, Any] = (await self.public.get(f"/recaps/{token}")).json()["audio"]
        self.assertEqual(set(audio), set(KINDS))
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
        await self.make_secret("Mira")
        self.assertEqual((await self.public.get(f"/recaps/{token}/audio/{kind}")).status_code, 404)

    async def test_an_unknown_kind_or_token_is_not_found(self) -> None:
        await self.post_it()
        await self.make_audio()
        token = await self.token()
        for path in (f"/recaps/{token}/audio/evil", f"/recaps/{'A' * 43}/audio/{KINDS[0]}"):
            self.assertEqual((await self.public.get(path)).status_code, 404, path)


if __name__ == "__main__":
    import unittest

    unittest.main()
