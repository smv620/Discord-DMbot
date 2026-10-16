"""A campaign's session transcripts on the website (#1501), run as the website's own database role:
anyone in the server may read them (not only a DM), a person outside it gets "not found", the
cleaned version leaves out what Discord's cleaned file leaves out, the as-heard version is the raw
transcript, the download is `export.render`'s own text, and the plan rule is the bot's."""

from __future__ import annotations

from typing import Any

import httpx
import psycopg

from dmbot.transcript import export
from dmbot.transcript.models import (
    SIDEBAR_ANSWER,
    SIDEBAR_QUESTION,
    VIA_VOICE,
    Line,
    Lineage,
    TranscriptSession,
)
from dmbot.transcript.store import TranscriptStore
from dmbot.web import sessions, transcripts
from dmbot.web.app import create_app
from dmbot.web.payments import FakeProvider
from dmbot.web.recaps import RecapError
from tests.test_recap_web import BOB, HEADERS, NOW, Base, callers
from tests.test_web_api import ALICE, API, QUILLON, THURSDAY, FakeDiscord, settings

START = 1_700_000_000
CAROL = type(BOB)(id=3003, name="Carol", email="carol@example.com")  # in the server, no DM at all


def second_session_lines(base: int) -> list[Line]:
    """Speech, off-topic chat, typed chat, a sidebar question and DMbot's answer (#935)."""
    return [
        Line(base + 1000, BOB.id, "Kazeth draws his sword", "Ka'zeth draws his sword"),
        Line(base + 2000, ALICE.id, "did you see the match", "did you see the match",
             duration_ms=5000, topic="off_topic"),
        Line(base + 3000, BOB.id, "we take a long rest", "we take a long rest", typed=True),
        Line(base + 4000, ALICE.id, "is the ferryman hostile", "is the ferryman hostile",
             sidebar=SIDEBAR_QUESTION, lineage=Lineage(ref="a1b2c3", via=VIA_VOICE)),
        Line(base + 5000, ALICE.id, "Probably not.", "Probably not.",
             sidebar=SIDEBAR_ANSWER, lineage=Lineage(reply_to="a1b2c3", model="m")),
    ]  # fmt: skip


async def read(web: Any, who: Any, ref: str, number: int, version: str, **kw: Any) -> Any:
    return await transcripts.read_session(
        web, who, ref, number, version, now=NOW, enforce_plans=kw.get("enforce_plans", False)
    )


class Transcripts(Base):
    async def give_plan(self, user_id: int, plan: str) -> None:
        async with self.db.plan_writer(user_id) as conn:
            await conn.execute("DELETE FROM entitlements WHERE user_id = %s", (user_id,))
            await conn.execute(
                "INSERT INTO entitlements (user_id, plan, status, hours_cap, extra_hours,"
                " campaign_cap, period_start, period_end, grace_ends_at, lapsed_at,"
                " plan_changed_at, provider, provider_subscription_id, last_event_at, updated_at)"
                " VALUES (%s, %s, 'active', 18, 0, 1, %s, %s, NULL, NULL, 0, 'fake',"
                " 'sub_1', 0, 0)",
                (user_id, plan, NOW - 100, NOW + 86400),
            )

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.cref = f"{THURSDAY.id}.{self.one.id}"
        self.oref = f"{THURSDAY.id}.{self.two.id}"
        self.store_t = TranscriptStore(self.db)
        # Session 1 (older): a few lines. Session 2: speech, off-topic chat, typed chat, a sidebar
        # question and DMbot's answer. Another campaign's session too.
        first = await self.store_t.open_session(THURSDAY.id, self.one.id, START)
        await self.store_t.add_lines(
            THURSDAY.id,
            first,
            [Line(START * 1000 + 1000, ALICE.id, "we cross the river", "we cross the river")],
        )
        await self.store_t.end_session(THURSDAY.id, first, START + 3600)
        self.sid = await self.store_t.open_session(
            THURSDAY.id, self.one.id, START + 86_400, "deepgram nova-3 api.example"
        )
        await self.store_t.add_lines(
            THURSDAY.id, self.sid, second_session_lines((START + 86_400) * 1000)
        )
        await self.store_t.end_session(THURSDAY.id, self.sid, START + 90_000)
        other = await self.store_t.open_session(THURSDAY.id, self.two.id, START)
        await self.store_t.add_lines(
            THURSDAY.id, other, [Line(START * 1000 + 1000, BOB.id, "elsewhere", "elsewhere")]
        )
        self.bob = await self.session_of(BOB)
        self.carol = await self.session_of(CAROL)

    async def session_of(self, user: Any, guilds: Any = None) -> Any:
        token = await sessions.sign_in(
            self.web, user, guilds if guilds is not None else [THURSDAY], now=NOW, days=30
        )
        found = await sessions.find(self.web, token, now=NOW)
        assert found is not None
        return found

    async def listed(self, who: Any, ref: str | None = None, **kw: Any) -> Any:
        found = await transcripts.list_sessions(
            self.web, who, ref or self.cref, now=NOW, enforce_plans=kw.get("enforce_plans", False)
        )
        return found["sessions"]

    # ---- who ---------------------------------------------------------------------------

    async def test_anyone_in_the_server_sees_the_list_not_only_a_dm(self) -> None:
        for who in (
            self.session,
            self.bob,
            self.carol,
        ):  # Alice DMs it, Bob DMs another, Carol none
            got = await self.listed(who)
            self.assertEqual([(s["number"], s["lines"]) for s in got], [(2, 3), (1, 1)])
        self.assertEqual((await self.listed(self.carol))[0]["startedAt"], START + 86_400)
        self.assertEqual((await self.listed(self.carol))[0]["endedAt"], START + 90_000)

    async def test_the_list_is_only_this_campaigns_sessions(self) -> None:
        got = await self.listed(self.bob, self.oref)
        self.assertEqual([(s["number"], s["lines"]) for s in got], [(1, 1)])

    async def test_someone_outside_the_server_or_a_missing_campaign_gets_not_found(self) -> None:
        elsewhere = await self.session_of(CAROL, [QUILLON])  # signed in, but not in this server
        for who, ref in (
            (elsewhere, self.cref),
            (self.carol, f"{THURSDAY.id}.nosuchcampaign"),
            (self.carol, "nonsense"),
            (self.carol, f"999.{self.one.id}"),
        ):
            with self.subTest(ref=ref), self.assertRaises(RecapError) as caught:
                await self.listed(who, ref)
            self.assertEqual(caught.exception.code, "not_found")
        with self.assertRaises(RecapError) as caught:
            await read(self.web, elsewhere, self.cref, 2, "heard")
        self.assertEqual(caught.exception.code, "not_found")

    async def test_a_session_of_another_campaign_is_not_reachable_through_this_one(self) -> None:
        with self.assertRaises(RecapError) as caught:
            await read(self.web, self.carol, self.oref, 2, "heard")  # "Two" has only Session 1
        self.assertEqual(caught.exception.code, "not_found")

    async def test_an_unknown_session_or_version_is_not_found(self) -> None:
        for number, version in ((9, "heard"), (0, "heard"), (2, "raw"), (2, "as-heard")):
            with self.subTest(number=number, version=version), self.assertRaises(RecapError):
                await read(self.web, self.carol, self.cref, number, version)

    async def test_the_campaign_list_has_every_campaign_with_a_transcript_in_the_persons_servers(
        self,
    ) -> None:
        got = await transcripts.list_campaigns(self.web, self.carol, now=NOW, enforce_plans=False)
        self.assertEqual(
            [(c["ref"], c["name"], c["sessions"]) for c in got],
            [(self.cref, "One", 2), (self.oref, "Two", 1)],
        )
        self.assertEqual({c["serverName"] for c in got}, {THURSDAY.name})
        elsewhere = await self.session_of(CAROL, [QUILLON])  # a server with no campaigns here
        self.assertEqual(
            await transcripts.list_campaigns(self.web, elsewhere, now=NOW, enforce_plans=False), []
        )
        # A campaign with no saved lines is not listed.
        await self.campaigns.create(THURSDAY.id, "Empty", ALICE.id)
        again = await transcripts.list_campaigns(self.web, self.carol, now=NOW, enforce_plans=False)
        self.assertEqual([c["name"] for c in again], ["One", "Two"])

    async def test_the_campaign_list_follows_the_owners_plan_too(self) -> None:
        # Alice owns "One", Bob "Two". Only Bob's plan has transcripts.
        await self.give_plan(BOB.id, "table")
        got = await transcripts.list_campaigns(self.web, self.carol, now=NOW, enforce_plans=True)
        self.assertEqual([c["name"] for c in got], ["Two"])
        await self.give_plan(ALICE.id, "guild")
        got = await transcripts.list_campaigns(self.web, self.carol, now=NOW, enforce_plans=True)
        self.assertEqual([c["name"] for c in got], ["One", "Two"])

    # ---- what is shown -----------------------------------------------------------------

    def expected(self, version: str) -> str:
        """What export.render makes of the same rows: the website adds nothing of its own."""
        started = START + 86_400
        lines = second_session_lines(started * 1000)
        stored = TranscriptSession(
            "x", THURSDAY.id, self.one.id, started, START + 90_000, 5, (), 2,
            ("deepgram nova-3 api.example",),
        )  # fmt: skip
        names = transcripts.speaker_names(lines)
        return export.render("One", stored, lines, names, version=version)

    async def test_the_heard_version_is_the_raw_transcript_as_export_makes_it(self) -> None:
        shown = await read(self.web, self.carol, self.cref, 2, "heard")
        text = self.expected(export.AS_HEARD)
        head, _, body = text.partition("\n\n")
        self.assertEqual(shown["lines"], body.rstrip("\n").split("\n"))
        self.assertEqual(shown["notes"][:-1], head.split("\n"))
        joined = "\n".join(shown["lines"])
        self.assertIn("[DM Sidebar", joined)  # unredacted, the sidebar included
        self.assertIn("is the ferryman hostile", joined)
        self.assertIn("(typed)", joined)
        self.assertIn("did you see the match", joined)
        self.assertIn("Kazeth draws his sword", joined)  # as heard: names not fixed

    async def test_the_cleaned_version_leaves_out_what_discords_cleaned_file_leaves_out(
        self,
    ) -> None:
        shown = await read(self.web, self.carol, self.cref, 2, "cleaned")
        _head, _, body = self.expected(export.CLEANED).partition("\n\n")
        self.assertEqual(shown["lines"], body.rstrip("\n").split("\n"))
        joined = "\n".join(shown["lines"])
        self.assertIn("Ka'zeth draws his sword", joined)  # names fixed
        for hidden in ("DM Sidebar", "is the ferryman hostile", "Probably not", "(typed)",
                       "did you see the match", "long rest"):  # fmt: skip
            self.assertNotIn(hidden, joined)
        self.assertIn("skipped", joined)  # the off-topic run is a note with how long it lasted

    async def test_it_says_the_names_are_numbered_and_shows_nobodys_discord_id(self) -> None:
        shown = await read(self.web, self.carol, self.cref, 2, "heard")
        self.assertEqual(shown["notes"][-1], transcripts.NAMES_NOTE)
        everything = repr(shown)
        for private in (str(ALICE.id), str(BOB.id), "Belleros", "Bob"):
            self.assertNotIn(private, everything)
        self.assertIn("(Speaker 1)", "\n".join(shown["lines"]))
        self.assertEqual(
            transcripts.speaker_names(
                [Line(2, 7, "b", "b"), Line(1, 9, "a", "a"), Line(3, 7, "c", "c")]
            ),
            {9: "Speaker 1", 7: "Speaker 2"},
        )

    # ---- the plan rule -----------------------------------------------------------------

    async def test_the_owners_plan_decides_when_plans_are_enforced(self) -> None:
        # Alice owns "One". No plan row: no transcripts, for a player as for her.
        for who in (self.carol, self.session):
            with self.subTest(), self.assertRaises(RecapError) as caught:
                await self.listed(who, enforce_plans=True)
            self.assertEqual(caught.exception.code, "not_in_plan")
        with self.assertRaises(RecapError):
            await read(self.web, self.carol, self.cref, 2, "heard", enforce_plans=True)
        await self.give_plan(ALICE.id, "try-it")  # Try It has no copies or transcripts
        with self.assertRaises(RecapError):
            await self.listed(self.carol, enforce_plans=True)
        await self.give_plan(ALICE.id, "table")
        self.assertEqual(len(await self.listed(self.carol, enforce_plans=True)), 2)
        # The reader's own plan is nothing to do with it.
        await self.give_plan(ALICE.id, "try-it")
        await self.give_plan(CAROL.id, "guild")
        with self.assertRaises(RecapError):
            await self.listed(self.carol, enforce_plans=True)

    async def test_with_plans_not_enforced_every_member_may_read(self) -> None:
        self.assertEqual(len(await self.listed(self.carol)), 2)

    # ---- the doors and the role --------------------------------------------------------

    async def test_the_websites_role_cannot_read_the_transcript_tables(self) -> None:
        async with self.web.user(CAROL.id, session=self.carol.id_hash) as conn:
            await conn.execute("SELECT set_config('dmbot.guild_id', %s, true)", (str(THURSDAY.id),))
            for table in ("transcript_lines", "transcript_sessions"):
                with self.subTest(table), self.assertRaises(psycopg.errors.InsufficientPrivilege):
                    async with conn.transaction():
                        await conn.execute(f"SELECT * FROM {table}")

    async def test_the_doors_answer_nothing_without_the_server_set(self) -> None:
        async with self.web.user(CAROL.id, session=self.carol.id_hash) as conn:
            cur = await conn.execute(
                "SELECT dmbot_web_transcript_sessions(%s, %s) AS s,"
                " dmbot_web_transcript_lines(%s, %s, %s) AS l,"
                " dmbot_web_transcript_campaigns(%s) AS c",
                (THURSDAY.id, self.one.id, THURSDAY.id, self.one.id, self.sid, THURSDAY.id),
            )
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual((row["s"], row["l"], row["c"]), (None, None, None))

    async def test_each_door_has_one_caller(self) -> None:
        for door in (
            "dmbot_web_transcript_sessions",
            "dmbot_web_transcript_lines",
            "dmbot_web_transcript_campaigns",
        ):
            self.assertEqual(callers(door), {"schema.py", "web/transcripts.py"}, door)


class TheRoutes(Base):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.store_t = TranscriptStore(self.db)
        sid = await self.store_t.open_session(THURSDAY.id, self.one.id, START)
        await self.store_t.add_lines(
            THURSDAY.id,
            sid,
            [
                Line(START * 1000 + 1000, ALICE.id, "Kazeth draws", "Ka'zeth draws"),
                Line(
                    START * 1000 + 2000,
                    ALICE.id,
                    "off topic",
                    "off topic",
                    topic="off_topic",
                    duration_ms=4000,
                ),
            ],
        )
        await self.store_t.end_session(THURSDAY.id, sid, START + 600)
        self.cref = f"{THURSDAY.id}.{self.one.id}"
        app = create_app(
            settings(enforce_plans=False),
            self.web,
            FakeDiscord(),
            payments=FakeProvider(b"h" * 32, "https://s.example"),
            clock=lambda: NOW,
        )
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=API, follow_redirects=False
        )
        self.addAsyncCleanup(self.client.aclose)

    async def sign_in(self, user: Any, guilds: Any) -> None:
        token = await sessions.sign_in(self.web, user, guilds, now=NOW, days=30)
        self.client.cookies.set("__Host-dmbot_session", token, domain="api.dmbot.example")

    async def test_the_list_the_read_view_and_the_download_agree(self) -> None:
        await self.sign_in(CAROL, [THURSDAY])
        listed = await self.client.get(f"/me/campaigns/{self.cref}/transcripts", headers=HEADERS)
        self.assertEqual(listed.status_code, 200, listed.text)
        self.assertEqual([s["number"] for s in listed.json()["sessions"]], [1])
        self.assertEqual(listed.json()["name"], "One")
        mine = await self.client.get("/me/transcripts", headers=HEADERS)
        self.assertEqual(
            [(c["ref"], c["sessions"]) for c in mine.json()["campaigns"]], [(self.cref, 1)]
        )
        read_view = await self.client.get(
            f"/me/campaigns/{self.cref}/transcripts/1", params={"version": "heard"}, headers=HEADERS
        )
        self.assertEqual(read_view.status_code, 200)
        file = await self.client.get(
            f"/me/campaigns/{self.cref}/transcripts/1/download",
            params={"version": "heard"},
            headers=HEADERS,
        )
        self.assertEqual(file.status_code, 200)
        self.assertEqual(file.headers["content-type"], "text/plain; charset=utf-8")
        self.assertEqual(
            file.headers["content-disposition"],
            'attachment; filename="one-session-1-as-heard.txt"',
        )
        self.assertEqual(file.headers["cache-control"], "no-store")  # every signed-in answer
        body = read_view.json()
        # The download is the read view's text, joined as export.render wrote it: nothing added.
        self.assertEqual(file.text, "\n".join([*body["notes"][:-1], "", *body["lines"]]) + "\n")
        cleaned = await self.client.get(
            f"/me/campaigns/{self.cref}/transcripts/1/download", headers=HEADERS
        )  # the default version is the cleaned one
        self.assertIn("-cleaned.txt", cleaned.headers["content-disposition"])
        self.assertNotIn("off topic", cleaned.text)

    async def test_someone_outside_the_server_and_bad_requests_get_404(self) -> None:
        await self.sign_in(CAROL, [QUILLON])
        for path in (
            f"/me/campaigns/{self.cref}/transcripts",
            f"/me/campaigns/{self.cref}/transcripts/1",
            f"/me/campaigns/{self.cref}/transcripts/1/download",
        ):
            with self.subTest(path):
                self.assertEqual((await self.client.get(path, headers=HEADERS)).status_code, 404)
        # The list of campaigns is simply empty for someone in no server that has one.
        self.assertEqual((await self.client.get("/me/transcripts")).json(), {"campaigns": []})
        await self.sign_in(CAROL, [THURSDAY])
        for path in (
            f"/me/campaigns/{self.cref}/transcripts/7",
            f"/me/campaigns/{self.cref}/transcripts/1?version=raw",
            f"/me/campaigns/{self.cref}/transcripts/1/download?version=raw",
        ):
            with self.subTest(path):
                self.assertEqual((await self.client.get(path, headers=HEADERS)).status_code, 404)
        self.assertEqual(
            (await self.client.get(f"/me/campaigns/{self.cref}/transcripts")).status_code, 200
        )
