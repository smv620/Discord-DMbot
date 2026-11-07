"""The lore and import pages' live windows on what DMbot asked the AI (#3710), the website's half: a
DM or game master reads the cut prompts and replies of one job of their campaign, nobody else does,
an import is read through every session it made, and the route is 404 while its switch is off.
Real Postgres; invented words."""

from __future__ import annotations

from typing import Any

from dmbot.transcript.store import TranscriptStore
from dmbot.web import ai_feed_read
from dmbot.web.recaps import RecapError
from tests.test_web_relationships import Web

LORE = "a" * 32
IMPORT = "b" * 32


class Feed(Web):
    async def put(
        self,
        kind: str,
        job: str,
        n: int,
        at: int,
        *,
        prompt: str = "Read part 1.",
        reply: str | None = "Found a ferry.",
        state: str = "done",
        campaign: str | None = None,
        lost: int = 0,
    ) -> None:
        async with self.db.guild(self.g) as conn:
            await conn.execute(
                "INSERT INTO ai_feed (guild_id, campaign_id, kind, job_id, n, at, service,"
                " model_label, step, prompt, reply, state, lost)"
                " VALUES (%s, %s, %s, %s, %s, %s, 'Anthropic', 'smaller model', 'part 1',"
                " %s, %s, %s, %s)",
                (self.g, campaign or self.one.id, kind, job, n, at, prompt, reply, state, lost),
            )

    async def read(
        self, kind: str = "lore", job: str = LORE, after: int = 0, who: Any = None
    ) -> Any:
        return await ai_feed_read.read_feed(
            self.web, who or self.session, self.cref, kind, job, after
        )

    async def refusal(self, call: Any) -> str:
        with self.assertRaises(RecapError) as caught:
            await call
        return str(caught.exception.code)

    async def test_a_dm_reads_the_calls_of_a_lore_upload_in_order(self) -> None:
        await self.put("lore", LORE, 0, 100, prompt="", reply=None, lost=2)  # bookkeeping
        await self.put("lore", LORE, 2, 120, prompt="Read part 2.")
        await self.put("lore", LORE, 1, 110)
        got = await self.read()
        self.assertEqual([e["n"] for e in got["entries"]], [1, 2])  # n = 0 is never a call
        self.assertEqual(got["lost"], 2)
        first = got["entries"][0]
        self.assertEqual(
            (first["service"], first["model"], first["step"], first["state"]),
            ("Anthropic", "smaller model", "part 1", "done"),
        )
        self.assertEqual((first["prompt"], first["reply"]), ("Read part 1.", "Found a ferry."))

    async def test_only_what_is_new_comes_back_and_the_calls_still_waiting_always_do(self) -> None:
        await self.put("lore", LORE, 1, 100)
        await self.put("lore", LORE, 2, 110, state="waiting", reply=None)
        await self.put("lore", LORE, 3, 120)
        got = await self.read(after=120)
        self.assertEqual([e["n"] for e in got["entries"]], [2, 3])  # the waiting one, then the new
        self.assertIsNone(got["entries"][0]["reply"])

    async def test_an_import_is_read_through_every_session_it_made(self) -> None:
        transcripts = TranscriptStore(self.db)
        sessions = [
            await transcripts.open_session(self.g, self.one.id, 1_700_000_000 + i) for i in range(2)
        ]
        async with self.db.guild(self.g) as conn:
            for s in sessions:
                await conn.execute(
                    "INSERT INTO session_imports (guild_id, campaign_id, session_id, import_id,"
                    " file_name, imported_by, imported_at) VALUES (%s, %s, %s, %s, 'a.txt', 1, 1)",
                    (self.g, self.one.id, s, IMPORT),
                )
        await self.put("import", sessions[0], 1, 100)
        await self.put("import", sessions[1], 1, 105, prompt="Read the second file.")
        await self.put("import", "c" * 32, 1, 110)  # another import's session
        got = await self.read("import", IMPORT)
        self.assertEqual(
            [(e["job"], e["prompt"]) for e in got["entries"]],
            [(sessions[0], "Read part 1."), (sessions[1], "Read the second file.")],
        )

    async def test_a_player_is_refused_and_a_dm_of_another_campaign_reads_nothing(self) -> None:
        await self.put("lore", LORE, 1, 100)
        self.assertEqual(await self.refusal(self.read(who=self.player)), "not_allowed")
        await self.put("lore", LORE, 1, 100, campaign=self.two.id)
        got = await self.read()
        self.assertEqual(len(got["entries"]), 1)  # this campaign's, never the other's

    async def test_the_kind_the_job_and_the_cursor_must_be_shaped_right(self) -> None:
        for call in (
            self.read(kind="recap"),
            self.read(job="../x"),
            self.read(job="A" * 32),
            self.read(after=-1),
            self.read(after=10**12),
        ):
            self.assertEqual(await self.refusal(call), "not_found")

    async def test_a_job_with_no_rows_is_an_empty_list_not_an_error(self) -> None:
        got = await self.read()
        self.assertEqual((got["entries"], got["lost"]), ([], 0))

    async def test_a_call_that_finishes_later_is_read_again_from_the_oldest_waiting_time(
        self,
    ) -> None:
        await self.put("lore", LORE, 1, 100, state="waiting", reply=None)
        await self.put("lore", LORE, 2, 110)
        first = await self.read(after=0)
        self.assertEqual([e["state"] for e in first["entries"]], ["waiting", "done"])
        # The call finishes; the writer does not move its time. The page asks from the oldest waiting
        # call's time (100), not from the newest (110), so the finished call comes back once.
        async with self.db.guild(self.g) as conn:
            await conn.execute(
                "UPDATE ai_feed SET state = 'done', reply = 'Found it late.' WHERE n = 1"
            )
        again = await self.read(after=100)
        self.assertEqual(
            [(e["n"], e["state"], e["reply"]) for e in again["entries"]],
            [(1, "done", "Found it late."), (2, "done", "Found a ferry.")],
        )

    async def test_another_campaigns_import_with_the_same_id_reads_nothing_here(self) -> None:
        transcripts = TranscriptStore(self.db)
        theirs = await transcripts.open_session(self.g, self.two.id, 1_700_000_000)
        async with self.db.guild(self.g) as conn:
            await conn.execute(
                "INSERT INTO session_imports (guild_id, campaign_id, session_id, import_id,"
                " file_name, imported_by, imported_at) VALUES (%s, %s, %s, %s, 'a.txt', 1, 1)",
                (self.g, self.two.id, theirs, IMPORT),
            )
        await self.put("import", theirs, 1, 100, campaign=self.two.id)
        got = await self.read("import", IMPORT)
        self.assertEqual(got["entries"], [])
