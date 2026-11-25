"""The small counts read of a running upload or import (#3534, part 3): DM only, one keyed row,
counts and times and never a word, nothing of another campaign, bad kinds and ids refused."""

from __future__ import annotations

from typing import Any

from dmbot.web import lore, sessions, wait_counts
from dmbot.web.recaps import RecapError
from tests.test_recap_web import BOB, NOW
from tests.test_web_api import THURSDAY
from tests.test_web_report import GUILD, Reading

WORDS = "Mira was raised by the Ashen Crown."
IMPORT = "a" * 32


class TheCounts(Reading):
    async def upload(self) -> str:
        got = await lore.add(
            self.web,
            self.session,
            self.cref,
            {"source": "paste", "text": WORDS, "choice": "world", "speaker": "", "rights": True},
        )
        return str(got["upload"]["id"])

    async def import_row(self, sid: str, at: int, campaign: str | None = None) -> None:
        await self.sql(
            "INSERT INTO transcript_sessions (id, guild_id, campaign_id, started_at)"
            " VALUES (%s, %s, %s, %s)",
            sid, GUILD, campaign or self.one.id, at,
            )  # fmt: skip
        await self.sql(
            "INSERT INTO session_imports (guild_id, campaign_id, session_id, import_id,"
            " file_name, imported_by, imported_at, state, step_done, step_total, words_done,"
            " calls_made) VALUES (%s, %s, %s, %s, 'one.txt', %s, %s, 'working', 3, 10, 400, 7)",
            GUILD, campaign or self.one.id, sid, IMPORT, self.session.user_id, at,
            )  # fmt: skip

    async def counts_refused(self, who: Any, ref: str, kind: object, ident: object) -> str:
        with self.assertRaises(RecapError) as err:
            await wait_counts.read(self.web, who, ref, kind, ident)
        return err.exception.code

    async def test_a_running_upload_gives_its_stage_and_counts_and_no_words(self) -> None:
        up = await self.upload()
        await self.sql(
            "UPDATE lore_uploads SET state = 'working', stage = 'finding_names', done = 2,"
            " total = 5, words_done = 120, calls_made = 3, names_found = 4, links_found = 1"
        )
        got = await wait_counts.read(self.web, self.session, self.cref, "lore", up)
        self.assertEqual(
            (got["kind"], got["state"], got["stage"], got["done"], got["total"]),
            ("lore", "working", "finding_names", 2, 5),
        )
        self.assertEqual(
            (got["wordsDone"], got["callsMade"], got["namesFound"], got["linksFound"]),
            (120, 3, 4, 1),
        )
        for key in ("createdAt", "updatedAt", "serverNow"):
            self.assertGreater(got[key], 1_000_000_000)
        self.assertNotIn("Ashen", repr(got))
        self.assertNotIn("text", got)

    async def test_an_import_gives_one_entry_a_session_with_counts_only(self) -> None:
        await self.import_row("s1", 1_700_000_000)
        await self.import_row("s2", 1_700_000_100)
        got = await wait_counts.read(self.web, self.session, self.cref, "import", IMPORT)
        self.assertEqual(got["kind"], "import")
        self.assertEqual(len(got["sessions"]), 2)
        first = got["sessions"][0]
        self.assertEqual(
            (first["state"], first["stepDone"], first["stepTotal"], first["wordsDone"]),
            ("working", 3, 10, 400),
        )
        self.assertEqual(first["callsMade"], 7)
        self.assertGreater(got["serverNow"], 1_000_000_000)

    async def test_another_campaigns_job_is_never_read(self) -> None:
        up = await self.upload()
        await self.import_row("s3", 1_700_000_000, self.two.id)
        two_ref = f"{GUILD}.{self.two.id}"
        self.assertEqual(await self.counts_refused(self.session, two_ref, "lore", up), "not_found")
        self.assertEqual(
            await self.counts_refused(self.session, two_ref, "import", IMPORT), "not_found"
        )

    async def test_a_player_and_bad_kinds_or_ids_are_turned_away(self) -> None:
        up = await self.upload()
        who: Any = BOB
        bob = await sessions.find(
            self.web, await sessions.sign_in(self.web, who, [THURSDAY], now=NOW, days=30), now=NOW
        )
        assert bob is not None
        self.assertEqual(await self.counts_refused(bob, self.cref, "lore", up), "not_found")
        for kind, ident in (
            ("names", up),
            (None, up),
            ("lore", "short"),
            ("lore", "A" * 32),
            ("lore", 5),
            ("lore", "b" * 32),
            ("import", "b" * 32),
        ):
            with self.subTest(kind=kind, ident=ident):
                self.assertEqual(
                    await self.counts_refused(self.session, self.cref, kind, ident), "not_found"
                )
