"""The recap page's "Names fixed this session" (#2684): the DM's read of what PlotBot's name pass
did, with the spellings as heard for the DM only. Invented names throughout."""

from __future__ import annotations

from typing import Any, cast

from dmbot.memory import web_actions as wa
from dmbot.memory.store import MemoryStore
from dmbot.transcript import name_pass as np
from dmbot.transcript.models import Line
from dmbot.transcript.name_pass_log import NamePassLog, undo_fix, undo_for_web
from dmbot.transcript.store import TranscriptStore
from dmbot.web import names_fixed as web
from dmbot.web import relationships, sessions
from dmbot.web.recaps import RecapError
from tests.test_recap_web import BOB, NOW, Base
from tests.test_web_api import ALICE, THURSDAY

GUILD = THURSDAY.id
AT = 100  # the recap's session time (the Base's one recap)


class Fixed(Base):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.transcripts = TranscriptStore(self.db)
        self.log = NamePassLog.of(self.transcripts)

    async def session_with_fixes(self, campaign: str | None = None) -> str:
        cid = campaign or self.one.id
        sid = await self.transcripts.open_session(GUILD, cid, AT)
        await self.transcripts.end_session(GUILD, sid, AT + 50)
        await self.transcripts.add_lines(
            GUILD,
            sid,
            [
                Line(AT * 1000 + 1000, 8, "Keirandal drew his bow.", "Keirandal drew his bow."),
                Line(AT * 1000 + 2000, 8, "We followed Kethrandel.", "We followed Kethrandel."),
            ],
        )
        batch = np.batch_of(sid, "e1")
        await self.transcripts.respell_names(
            GUILD, cid, batch, "Keirandal", "Kethrandil", [], now=AT + 10, forget_before=0,
            session_id=sid,
        )  # fmt: skip
        await self.transcripts.respell_names(
            GUILD, cid, batch, "Kethrandel", "Kethrandil", [], now=AT + 10, forget_before=0,
            session_id=sid,
        )  # fmt: skip
        report = np.Report(
            fixed=(np.Fixed("Kethrandil", (("Keirandal", 1), ("Kethrandel", 1)), batch),),
            unsure=(object(),) * 2,  # type: ignore[arg-type]
            new=(object(),),  # type: ignore[arg-type]
            guarded=3,
        )
        await self.log.record(GUILD, cid, sid, report, now=AT + 20)
        return sid

    async def read(self, who: Any = None) -> dict[str, Any]:
        return await web.read_names_fixed(self.web, who or self.session, self.ref)

    async def bob(self) -> Any:
        token = await sessions.sign_in(self.web, cast(Any, BOB), [THURSDAY], now=NOW, days=30)
        got = await sessions.find(self.web, token, now=NOW)
        assert got is not None
        return got

    async def test_a_session_the_pass_never_ran_in_reads_empty(self) -> None:
        self.assertEqual(await self.read(), {"fixed": [], "unsure": 0, "new": 0, "guarded": 0})

    async def test_the_dm_reads_the_counts_each_fix_and_the_spellings_as_heard(self) -> None:
        sid = await self.session_with_fixes()
        got = await self.read()
        self.assertEqual((got["unsure"], got["new"], got["guarded"]), (2, 1, 3))
        (fix,) = got["fixed"]
        self.assertEqual(
            (fix["id"], fix["name"], fix["times"], fix["undone"]),
            (str(np.batch_of(sid, "e1")), "Kethrandil", 2, False),
        )
        self.assertEqual(
            sorted((f["heard"], f["times"]) for f in fix["forms"]),
            [("Keirandal", 1), ("Kethrandel", 1)],
        )

    async def test_an_undone_fix_is_marked_and_has_no_spellings_left(self) -> None:
        sid = await self.session_with_fixes()
        batch = np.batch_of(sid, "e1")
        await self.transcripts.undo_respell(GUILD, self.one.id, batch)
        await self.log.mark_undone(GUILD, self.one.id, batch, now=AT + 30)
        (fix,) = (await self.read())["fixed"]
        self.assertEqual((fix["undone"], fix["forms"]), (True, []))

    async def test_a_person_who_is_not_a_dm_is_told_not_found(self) -> None:
        await self.session_with_fixes()
        with self.assertRaises(RecapError) as err:
            await self.read(await self.bob())
        self.assertEqual(err.exception.code, "not_found")

    async def test_another_campaigns_fixes_never_show(self) -> None:
        await self.session_with_fixes(campaign=self.two.id)
        self.assertEqual((await self.read())["fixed"], [])

    async def test_the_door_refuses_a_campaign_or_server_the_caller_is_not_a_dm_of(self) -> None:
        async with self.web.user(ALICE.id, session=self.session.id_hash) as conn:
            await conn.execute("SELECT set_config('dmbot.guild_id', %s, true)", (str(GUILD),))
            for guild, campaign in ((GUILD, self.two.id), (999, self.one.id)):
                cur = await conn.execute(
                    "SELECT dmbot_recap_names_fixed(%s, %s, %s) AS r", (guild, campaign, AT)
                )
                self.assertIsNone((await cur.fetchone() or {})["r"])

    async def test_the_website_role_has_no_right_on_the_tables(self) -> None:
        from psycopg import errors

        for table in ("name_pass_reports", "name_pass_fixes"):
            with self.subTest(table=table), self.assertRaises(errors.InsufficientPrivilege):
                async with self.web.user(ALICE.id, session=self.session.id_hash) as conn:
                    await conn.execute(f"SELECT 1 FROM {table}")


class Undoing(Fixed):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.memory = MemoryStore(self.db)
        self.actions = wa.ActionStore(self.db)
        self.cref = f"{GUILD}.{self.one.id}"

    async def run_bot(self, dms: Any = None) -> list[wa.Outcome]:
        async def undo(cid: str, batch: int) -> int | None:
            return await undo_for_web(self.transcripts, self.memory, GUILD, cid, batch, now=AT + 99)

        waiting = wa.Waiting(cast(Any, None), cast(Any, None), cast(Any, None), name_pass_undo=undo)
        done = []
        while (action := await self.actions.claim_next(GUILD)) is not None:
            out = await wa.apply(
                self.memory, GUILD, {ALICE.id} if dms is None else dms, action, waiting
            )
            await self.actions.finish(
                GUILD, action, error=out.error, result_id=out.result_id,
                result_other=out.result_other,
            )  # fmt: skip
            done.append(out)
        return done

    async def poll(self, answer: str) -> dict[str, Any]:
        return await relationships.read_answer(self.web, self.session, self.cref, answer)

    async def texts(self, sid: str) -> list[str]:
        return [x.text for x in await self.transcripts.lines(GUILD, sid)]

    async def test_the_undo_is_queued_then_puts_the_lines_back_and_shows_in_the_read(self) -> None:
        sid = await self.session_with_fixes()
        fix = str(np.batch_of(sid, "e1"))
        self.assertIn("Kethrandil", " ".join(await self.texts(sid)))
        answer = await web.queue_undo(self.web, self.session, self.ref, fix)
        early = await self.poll(answer)
        self.assertEqual(
            (early["state"], early["waits"]["stage"], early["putBack"]),
            ("waiting", "queued", None),
        )
        (outcome,) = await self.run_bot()
        self.assertEqual(outcome.error, "")
        done = await self.poll(answer)
        self.assertEqual((done["state"], done["error"], done["putBack"]), ("done", "", 2))
        self.assertEqual(
            await self.texts(sid), ["Keirandal drew his bow.", "We followed Kethrandel."]
        )
        (shown,) = (await self.read())["fixed"]
        self.assertEqual((shown["undone"], shown["forms"]), (True, []))

    async def test_an_undo_already_made_is_done_with_nothing_put_back(self) -> None:
        sid = await self.session_with_fixes()
        fix = str(np.batch_of(sid, "e1"))
        await web.queue_undo(self.web, self.session, self.ref, fix)
        await self.run_bot()
        before = await self.texts(sid)
        again = await web.queue_undo(self.web, self.session, self.ref, fix)
        (outcome,) = await self.run_bot()
        self.assertEqual(outcome.error, "")  # the wish is met: it is undone
        done = await self.poll(again)
        self.assertEqual((done["state"], done["error"], done["putBack"]), ("done", "", 0))
        self.assertEqual(await self.texts(sid), before)  # and nothing was changed

    async def test_a_retaken_undo_after_the_lines_went_back_is_done_with_zero(self) -> None:
        # the bot put the lines back and marked the fix, then died before it could finish the
        # action; the retry finds nothing to put back, and the record says undone
        sid = await self.session_with_fixes()
        batch = np.batch_of(sid, "e1")
        await undo_fix(self.transcripts, self.memory, GUILD, self.one.id, batch, now=AT + 50)
        answer = await web.queue_undo(self.web, self.session, self.ref, str(batch))
        await self.run_bot()
        done = await self.poll(answer)
        self.assertEqual((done["state"], done["error"], done["putBack"]), ("done", "", 0))

    async def test_a_fix_the_lines_of_which_changed_and_was_never_undone_is_gone(self) -> None:
        sid = await self.session_with_fixes()
        batch = np.batch_of(sid, "e1")
        async with self.db.guild(GUILD) as conn:  # no fix of this number was ever recorded
            await conn.execute(
                "UPDATE name_pass_fixes SET undone_at = NULL WHERE batch = %s", (batch,)
            )
            await conn.execute("DELETE FROM transcript_respells WHERE batch = %s", (batch,))
        answer = await web.queue_undo(self.web, self.session, self.ref, str(batch))
        await self.run_bot()
        self.assertEqual((await self.poll(answer))["error"], wa.GONE)

    async def test_the_route_returns_the_answer_and_the_path_to_poll(self) -> None:
        import httpx

        from dmbot.web.app import create_app
        from dmbot.web.payments import FakeProvider
        from tests.test_recap_web import HEADERS
        from tests.test_web_api import FakeDiscord, settings

        sid = await self.session_with_fixes()
        app = create_app(
            settings(), self.web, FakeDiscord(), payments=FakeProvider(b"h" * 32, "https://x"),
            clock=lambda: NOW,
        )  # fmt: skip
        token = await sessions.sign_in(self.web, ALICE, [THURSDAY], now=NOW, days=30)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="https://web-api.internal.test",
            headers=HEADERS,
        ) as client:
            client.cookies.set("__Host-dmbot_session", token, domain="web-api.internal.test")
            posted = await client.post(
                f"/me/recaps/{self.ref}/names-fixed/{np.batch_of(sid, 'e1')}/undo"
            )
            self.assertEqual(posted.status_code, 202)
            body = posted.json()
            self.assertEqual(
                body["poll"], f"/me/campaigns/{self.cref}/web/answers/{body['answer']}"
            )
            await self.run_bot()
            polled = await client.get(body["poll"])
            self.assertEqual((polled.json()["state"], polled.json()["putBack"]), ("done", 2))

    async def test_a_fix_whose_lines_all_changed_is_still_marked_undone(self) -> None:
        sid = await self.session_with_fixes()
        batch = np.batch_of(sid, "e1")
        async with self.db.guild(GUILD) as conn:  # every line was edited by hand since
            await conn.execute(
                "UPDATE transcript_lines SET text = 'Edited by hand.' WHERE session_id = %s", (sid,)
            )
        answer = await web.queue_undo(self.web, self.session, self.ref, str(batch))
        await self.run_bot()
        done = await self.poll(answer)  # nothing put back, and its record is spent: done, 0
        self.assertEqual((done["state"], done["error"], done["putBack"]), ("done", "", 0))
        (shown,) = (await self.read())["fixed"]
        self.assertTrue(shown["undone"])  # but its record is spent: not shown as standing
        kept = {c.heard for c in await self.memory.corrections(GUILD, self.one.id)}
        self.assertNotIn("Keirandal", kept)  # no keep-as-heard rule for lines never put back

    async def test_a_number_that_is_not_plain_digits_is_not_found(self) -> None:
        await self.session_with_fixes()
        for odd in ("\u00b2", "\u0663", "1 ", "", "1" * 13, 5):
            with self.assertRaises(RecapError, msg=repr(odd)):
                await web.queue_undo(self.web, self.session, self.ref, odd)

    def test_the_door_and_the_bot_name_the_same_actions(self) -> None:
        from dmbot import schema

        self.assertEqual(schema.NAMES_FIXED_ACTIONS, wa.NAMES_FIXED_ACTIONS)

    async def test_the_undo_keeps_the_old_spellings_as_heard(self) -> None:
        sid = await self.session_with_fixes()
        await web.queue_undo(self.web, self.session, self.ref, str(np.batch_of(sid, "e1")))
        await self.run_bot()
        kept = {c.heard for c in await self.memory.corrections(GUILD, self.one.id)}
        self.assertIn("Keirandal", kept)

    async def test_a_player_or_a_stranger_fix_cannot_be_queued(self) -> None:
        sid = await self.session_with_fixes()
        fix = str(np.batch_of(sid, "e1"))
        with self.assertRaises(RecapError):
            await web.queue_undo(self.web, await self.bob(), self.ref, fix)
        for bad in ("12", "abc", 5, None, "9" * 13):
            with self.subTest(bad), self.assertRaises(RecapError):
                await web.queue_undo(self.web, self.session, self.ref, bad)
        self.assertEqual(await self.run_bot(), [])  # nothing was queued

    async def test_another_campaigns_fix_cannot_be_undone_from_this_one(self) -> None:
        sid = await self.session_with_fixes(campaign=self.two.id)
        with self.assertRaises(RecapError):
            await web.queue_undo(self.web, self.session, self.ref, str(np.batch_of(sid, "e1")))

    async def test_a_sender_who_is_no_longer_a_dm_is_refused_by_the_bot(self) -> None:
        sid = await self.session_with_fixes()
        await web.queue_undo(self.web, self.session, self.ref, str(np.batch_of(sid, "e1")))
        (outcome,) = await self.run_bot(dms={999})
        self.assertEqual(outcome.error, wa.NOT_ALLOWED)
        self.assertIn("Kethrandil", " ".join(await self.texts(sid)))  # still fixed
