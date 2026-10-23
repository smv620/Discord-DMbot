"""PlotBot's check of a recap, as the DM's recap page reads and answers it (#2122): DM only, the
issues of one text, the four answers, "not a problem" kept, Approve waiting for answers, and a
check that cannot run never holding the DM up. Invented fixtures; the tables are filled
directly, as the bot's check would."""

from __future__ import annotations

import time

import httpx

from dmbot.consent import TERMS_VERSION
from dmbot.recaps.approval import text_hash
from dmbot.web import sessions
from dmbot.web.app import create_app
from dmbot.web.payments import FakeProvider
from dmbot.web.plot import PLOT_WAIT_S
from tests import recap_fixtures as fx
from tests.test_recap_web import HEADERS, NOW, Base
from tests.test_web_api import ALICE, API, SITE, THURSDAY, FakeDiscord, settings


class PlotBase(Base):
    on = True

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.app = create_app(
            settings(recaps_on=True, plot_check_on=self.on), self.web, FakeDiscord(),
            payments=FakeProvider(b"h" * 32, SITE), clock=lambda: NOW,
        )  # fmt: skip
        token = await sessions.sign_in(self.web, ALICE, [THURSDAY], now=NOW, days=30)
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url=API, headers=HEADERS
        )
        self.client.cookies.set("__Host-dmbot_session", token, domain="api.dmbot.example")
        async with self.db.guild(THURSDAY.id) as conn:  # the speaker is recorded
            await conn.execute(
                "INSERT INTO consent (guild_id, user_id, granted_at, terms_version)"
                " VALUES (%s, %s, %s, %s)",
                (THURSDAY.id, fx.ALICE, NOW, TERMS_VERSION),
            )

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        await super().asyncTearDown()

    async def hash_of(self, campaign: str | None = None) -> str:
        job = await self.state(campaign or self.one.id)
        return text_hash(job.title, job.summary)

    async def checked(self, campaign: str | None = None, state: str = "done") -> None:
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute(
                "INSERT INTO plot_checks (guild_id, campaign_id, session_at, text_hash, state,"
                " created_at) VALUES (%s, %s, 100, %s, %s, %s)",
                (THURSDAY.id, campaign or self.one.id, await self.hash_of(campaign), state, NOW),
            )

    async def issue(
        self, name: str, *, campaign: str | None = None, sentence: int = 1, key: str | None = None,
        text_hash_: str | None = None,
    ) -> str:  # fmt: skip
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute(
                "INSERT INTO plot_issues (guild_id, campaign_id, id, session_at, text_hash, kind,"
                " level, sentence, sentence_text, reason, source_text, source_session, fine_key,"
                " created_at) VALUES (%s, %s, %s, 100, %s, 'dead_acts', 'probable', %s,"
                " 'Ulfgar spoke.', 'He died in an earlier session.', 'Ulfgar falls.', 'Session 4',"
                " %s, %s)",
                (
                    THURSDAY.id,
                    campaign or self.one.id,
                    name,
                    text_hash_ or await self.hash_of(campaign),
                    sentence,
                    key or f"key-{name}",
                    NOW,
                ),
            )
        return name

    async def plot(self, ref: str | None = None) -> httpx.Response:
        return await self.client.get(f"/me/recaps/{ref or self.ref}/plot")

    async def answer(self, issue: str, kind: str, note: str = "") -> httpx.Response:
        return await self.client.post(
            f"/me/recaps/{self.ref}/plot/{issue}/answer", json={"kind": kind, "note": note}
        )


class ThePage(PlotBase):
    async def test_the_issues_come_with_their_sentence_source_and_level(self) -> None:
        await self.checked()
        await self.issue("a1")
        got = (await self.plot()).json()
        self.assertEqual((got["state"], got["approveBlocked"]), ("ready", True))
        [one] = got["issues"]
        self.assertEqual(
            (one["kind"], one["level"], one["sentence"], one["sentenceText"]),
            ("dead_acts", "probable", 1, "Ulfgar spoke."),
        )
        self.assertEqual(one["source"], {"text": "Ulfgar falls.", "session": "Session 4"})
        self.assertIsNone(one["answer"])
        self.assertEqual(got["textHash"], await self.hash_of())

    async def test_a_check_with_no_issue_blocks_nothing(self) -> None:
        await self.checked()
        got = (await self.plot()).json()
        self.assertEqual((got["state"], got["approveBlocked"], got["issues"]), ("ready", False, []))

    async def test_a_text_that_is_being_checked_shows_it_and_blocks_nothing(self) -> None:
        got = (await self.plot()).json()  # no check yet, and the recap is fresh
        self.assertEqual((got["state"], got["approveBlocked"]), ("checking", False))
        await self.checked(state="running")
        # (the pretend row is for the same text) still checking
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute("DELETE FROM plot_checks")
        await self.checked(state="running")
        self.assertEqual((await self.plot()).json()["state"], "checking")

    async def test_a_text_that_waited_too_long_or_whose_check_failed_never_blocks(self) -> None:
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute(
                "UPDATE recap_jobs SET updated_at = %s", (int(time.time()) - PLOT_WAIT_S - 60,)
            )
        got = (await self.plot()).json()
        self.assertEqual((got["state"], got["approveBlocked"]), ("unavailable", False))
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute("UPDATE recap_jobs SET updated_at = %s", (int(time.time()),))
        await self.checked(state="failed")
        got = (await self.plot()).json()
        self.assertEqual(
            (got["state"], got["approveBlocked"], got["issues"]), ("unavailable", False, [])
        )

    async def test_issues_of_an_older_text_are_not_shown_for_the_new_one(self) -> None:
        await self.checked()
        await self.issue("old", text_hash_="f" * 64)
        got = (await self.plot()).json()
        self.assertEqual(got["issues"], [])

    async def test_the_versions_the_dm_accepted_are_listed(self) -> None:
        await self.checked()
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute(
                "INSERT INTO recap_accepted VALUES (%s, %s, 100, 1, %s, 'T', 'S', %s, 7)",
                (THURSDAY.id, self.one.id, await self.hash_of(), ALICE.id),
            )
        got = (await self.plot()).json()
        self.assertEqual(
            got["versions"], [{"version": 1, "acceptedBy": str(ALICE.id), "acceptedAt": 7}]
        )
        self.assertEqual(got["version"], 1)  # this text is that accepted version

    async def test_only_a_dm_of_the_campaign_reads_or_answers(self) -> None:
        await self.checked(self.two.id)
        await self.issue("b1", campaign=self.two.id)
        theirs = await self.plot(self.other)
        self.assertEqual((theirs.status_code, theirs.json()["error"]), (404, "not_found"))
        sent = await self.client.post(
            f"/me/recaps/{self.other}/plot/b1/answer", json={"kind": "leave"}
        )
        self.assertEqual(sent.status_code, 404)

    async def test_the_website_cannot_read_the_tables_itself(self) -> None:
        import psycopg.errors as pg_errors

        for table in ("plot_checks", "plot_issues", "plot_fine"):
            async with self.web.user(ALICE.id, session=self.session.id_hash) as conn:
                with self.assertRaises(pg_errors.InsufficientPrivilege):
                    await conn.execute(f"SELECT * FROM {table}")

    async def test_nothing_of_it_is_in_the_recap_itself(self) -> None:
        await self.checked()
        await self.issue("a1")
        got = (await self.client.get(f"/me/recaps/{self.ref}")).text
        self.assertNotIn("He died in an earlier session.", got)
        listed = (await self.client.get("/me/recaps")).text
        self.assertNotIn("He died in an earlier session.", listed)


class Answering(PlotBase):
    async def setup_issue(self) -> str:
        await self.checked()
        return await self.issue("a1")

    async def test_each_answer_closes_the_issue_and_unblocks_approve(self) -> None:
        for kind in ("fix", "changed", "fine", "leave"):
            async with self.db.guild(THURSDAY.id) as conn:
                await conn.execute("DELETE FROM plot_issues")
                await conn.execute("DELETE FROM plot_checks")
            issue = await self.setup_issue()
            got = await self.answer(issue, kind, "my reason")
            self.assertEqual(got.status_code, 200, got.text)
            self.assertEqual(got.json()["answer"], {"kind": kind, "note": "my reason"})
            after = (await self.plot()).json()
            self.assertEqual((after["approveBlocked"], len(after["issues"])), (False, 1), kind)

    async def test_leave_it_is_always_allowed_even_with_no_note(self) -> None:
        issue = await self.setup_issue()
        self.assertEqual((await self.answer(issue, "leave")).status_code, 200)

    async def test_not_a_problem_is_kept_so_it_is_not_raised_again(self) -> None:
        issue = await self.setup_issue()
        await self.answer(issue, "fine", "It is a different Ulfgar.")
        async with self.db.guild(THURSDAY.id) as conn:
            cur = await conn.execute("SELECT fine_key, note FROM plot_fine")
            rows = await cur.fetchall()
        self.assertEqual(
            [(r["fine_key"], r["note"]) for r in rows], [("key-a1", "It is a different Ulfgar.")]
        )
        await self.answer(issue, "leave")  # changed their mind: the keep goes
        async with self.db.guild(THURSDAY.id) as conn:
            cur = await conn.execute("SELECT count(*) AS n FROM plot_fine")
            self.assertEqual((await cur.fetchone() or {})["n"], 0)

    async def test_a_bad_answer_or_note_is_refused(self) -> None:
        issue = await self.setup_issue()
        for body in (
            {"kind": "maybe"},
            {"kind": "fine", "note": "x" * 501},
            {"note": "x"},
            {"kind": 3},
        ):
            got = await self.client.post(f"/me/recaps/{self.ref}/plot/{issue}/answer", json=body)
            self.assertEqual((got.status_code, got.json()["error"]), (422, "bad_answer"), body)
        self.assertEqual((await self.answer("nope", "leave")).status_code, 404)

    async def test_an_answer_after_the_text_changed_is_stale(self) -> None:
        issue = await self.setup_issue()
        await self.store.update(THURSDAY.id, self.one.id, 100, summary="A different recap.")
        got = await self.answer(issue, "leave")
        self.assertEqual((got.status_code, got.json()["error"]), (409, "stale"))

    async def test_approve_waits_for_the_answers_and_then_goes_ahead(self) -> None:
        issue = await self.setup_issue()
        blocked = await self.client.post(f"/me/recaps/{self.ref}/approve", json={"choice": "text"})
        self.assertEqual((blocked.status_code, blocked.json()["error"]), (409, "plot_open"))
        self.assertFalse((await self.state(self.one.id)).approved_hash)
        await self.answer(issue, "leave")
        ok = await self.client.post(f"/me/recaps/{self.ref}/approve", json={"choice": "text"})
        self.assertEqual(ok.status_code, 200, ok.text)

    async def test_approve_while_the_check_is_still_running_goes_ahead(self) -> None:
        await self.checked(state="running")
        ok = await self.client.post(f"/me/recaps/{self.ref}/approve", json={"choice": "text"})
        self.assertEqual(ok.status_code, 200, ok.text)


class Off(PlotBase):
    on = False

    async def test_with_the_switch_off_it_is_unavailable_and_approve_is_not_held(self) -> None:
        await self.checked()
        await self.issue("a1")
        got = (await self.plot()).json()
        self.assertEqual(
            (got["state"], got["approveBlocked"], got["issues"]), ("unavailable", False, [])
        )
        ok = await self.client.post(f"/me/recaps/{self.ref}/approve", json={"choice": "text"})
        self.assertEqual(ok.status_code, 200, ok.text)
