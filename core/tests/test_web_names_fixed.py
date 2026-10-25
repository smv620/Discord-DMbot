"""The recap page's "Names fixed this session" (#2684): the DM's read of what PlotBot's name pass
did, with the spellings as heard for the DM only. Invented names throughout."""

from __future__ import annotations

from typing import Any, cast

from dmbot.transcript import name_pass as np
from dmbot.transcript.models import Line
from dmbot.transcript.name_pass_log import NamePassLog
from dmbot.transcript.store import TranscriptStore
from dmbot.web import names_fixed as web
from dmbot.web import sessions
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
