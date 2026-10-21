"""Importing older sessions through the website's doors (#2105). Invented fixtures only."""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
import unittest.mock
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import httpx

from dmbot.consent import TERMS_VERSION, ConsentStore
from dmbot.db import Database
from dmbot.transcript import import_link
from dmbot.transcript.store import TranscriptStore
from dmbot.web import imports
from dmbot.web import sessions as web_sessions
from dmbot.web.app import create_app
from dmbot.web.payments import FakeProvider
from dmbot.web.recaps import RecapError
from tests.test_recap_web import BOB, HEADERS, NOW, Base
from tests.test_web_api import ALICE, API, SITE, THURSDAY, FakeDiscord, settings

MIRA, TOMAS, NOBODY = 7001, 7002, 7003
CRAIG = """Craig Recording of Tuesday
Started at 18:00

Mira (mira_v): We open the door and listen.
{non-game context}
Tomas (tomas#1): I check the lock, it is rusty.
Mira (mira_v): Good, we go in and the stairs go down for a long time.
"""
SECOND = "[0:00:05] Mira: Back in the hall again.\n[0:01:00] Tomas: We rest here.\n"
MAPPING = {"Mira (mira_v)": str(MIRA), "Tomas (tomas#1)": str(TOMAS)}


def files(*pairs: tuple[str, str]) -> list[dict[str, str]]:
    return [{"name": n, "text": t} for n, t in pairs]


class WithImports(Base):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        consent = ConsentStore(self.db)
        await consent.grant(THURSDAY.id, MIRA)  # Tomas has not agreed
        async with self.db.guild(THURSDAY.id) as conn:  # both are the campaign's people
            for n, who in enumerate((MIRA, TOMAS)):
                await conn.execute(
                    "INSERT INTO memory_entities (guild_id, campaign_id, id, type, name,"
                    " description, status, source, created_at, played_by)"
                    " VALUES (%s, %s, %s, 'person', %s, '', 'confirmed', 'dm', 0, %s)",
                    (THURSDAY.id, self.one.id, f"{n:032x}", f"Player {n}", who),
                )
        self.transcripts = TranscriptStore(self.db)
        self.kw: dict[str, Any] = {"outside": None, "now": NOW, "enforce_plans": False}

    async def sessions(self) -> int:
        return len(await self.transcripts.sessions(THURSDAY.id, self.one.id))

    async def preview(self, *pairs: tuple[str, str]) -> dict[str, Any]:
        return await imports.preview(self.web, self.session, self.ref_c, files(*pairs), **self.kw)

    @property
    def ref_c(self) -> str:
        return f"{THURSDAY.id}.{self.one.id}"

    async def confirm(self, plan: dict[str, Any], *pairs: tuple[str, str]) -> dict[str, Any]:
        return await imports.confirm(
            self.web, self.session, self.ref_c, files(*pairs), plan, **self.kw
        )


class TheLinks(WithImports):
    """Google Doc links next to uploads (#2140): each becomes a file, then follows the same path."""

    DOC = "https://docs.google.com/document/d/" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6"

    def reader(self, texts: dict[str, str | import_link.DocError]) -> Any:
        async def read(link: object) -> str:
            got = texts[str(link)]
            if isinstance(got, Exception):
                raise got
            return got

        return unittest.mock.patch.object(import_link, "read_doc", read)

    async def test_a_shared_doc_previews_and_imports_like_a_file(self) -> None:
        with self.reader({self.DOC: CRAIG}):
            got = await imports.preview(
                self.web, self.session, self.ref_c, None, links=[self.DOC], **self.kw
            )
            (one,) = got["files"]
            self.assertEqual(
                (one["name"], one["format"], one["lineCount"]), ("Google Doc 1.txt", "plain", 3)
            )
            done = await imports.confirm(
                self.web, self.session, self.ref_c, None,
                {
                    "mapping": MAPPING,
                    "sessions": [{"file": "Google Doc 1.txt", "date": "2026-09-14"}],
                },
                links=[self.DOC], **self.kw,
            )  # fmt: skip
        self.assertEqual(
            done["sessions"],
            [{"file": "Google Doc 1.txt", "state": "waiting", "writeRecap": False}],
        )
        self.assertEqual(await self.sessions(), 1)

    async def test_a_doc_that_cannot_be_read_says_why_and_the_others_still_work(self) -> None:
        err = import_link.DocError
        other = self.DOC + "x"
        with self.reader({self.DOC: err("doc_not_shared"), other: CRAIG}):
            got = await imports.preview(
                self.web, self.session, self.ref_c, files(("a.txt", CRAIG)),
                links=[self.DOC, other], **self.kw,
            )  # fmt: skip
        self.assertEqual(
            [(f["name"], f["error"]) for f in got["files"]],
            [("a.txt", None), ("Google Doc 1.txt", "doc_not_shared"), ("Google Doc 2.txt", None)],
        )

    async def test_a_link_that_is_not_a_google_doc_is_marked(self) -> None:
        got = await imports.preview(
            self.web, self.session, self.ref_c, None, links=["https://example.com/x"], **self.kw
        )
        self.assertEqual(got["files"][0]["error"], "not_a_google_doc")

    async def test_links_and_files_count_together_toward_the_limit(self) -> None:
        many = [(f"{n}.txt", CRAIG) for n in range(imports.MAX_FILES)]
        with self.assertRaises(RecapError) as refused:
            await imports.preview(
                self.web, self.session, self.ref_c, files(*many), links=[self.DOC], **self.kw
            )
        self.assertEqual(refused.exception.code, "too_many_files")
        with self.assertRaises(RecapError):
            await imports.preview(
                self.web, self.session, self.ref_c, None, links=[self.DOC] * 21, **self.kw
            )

    async def test_nothing_at_all_is_refused_and_a_bad_links_value_too(self) -> None:
        for files_, links in ((None, None), ([], []), (None, "https://x"), (None, {"a": 1})):
            with self.assertRaises(RecapError):
                await imports.preview(
                    self.web, self.session, self.ref_c, files_, links=links, **self.kw
                )

    async def test_a_bad_request_fetches_nothing(self) -> None:
        fake = unittest.mock.AsyncMock(return_value=CRAIG)
        bad = (
            ("x", [self.DOC]),  # files is not a list
            ([{"name": "Google Doc 1.txt", "text": CRAIG}], [self.DOC]),  # a name taken
            ([{"name": "a.txt", "text": ""}, {"name": "a.txt", "text": ""}], [self.DOC]),
            ([{"text": "no name"}], [self.DOC]),
        )
        with unittest.mock.patch.object(import_link, "read_doc", fake):
            for files_, links in bad:
                with self.assertRaises(RecapError):
                    await imports.preview(
                        self.web, self.session, self.ref_c, files_, links=links, **self.kw
                    )
        fake.assert_not_awaited()

    async def test_the_same_doc_twice_is_read_once_and_the_repeat_is_refused(self) -> None:
        fake = unittest.mock.AsyncMock(return_value=CRAIG)
        spelled = self.DOC + "/edit?usp=sharing"  # another spelling of the same document
        with unittest.mock.patch.object(import_link, "read_doc", fake):
            got = await imports.preview(
                self.web, self.session, self.ref_c, None, links=[self.DOC, spelled], **self.kw
            )
        self.assertEqual(fake.await_count, 1)
        self.assertEqual(
            [(f["name"], f["error"]) for f in got["files"]],
            [("Google Doc 1.txt", None), ("Google Doc 2.txt", "repeated_link")],
        )

    async def test_a_cancelled_request_cancels_the_reads_still_running(self) -> None:
        started = asyncio.Event()
        cancelled: list[bool] = []

        async def slow(_link: object) -> str:
            started.set()
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                cancelled.append(True)
                raise
            return CRAIG

        with unittest.mock.patch.object(import_link, "read_doc", slow):
            task = asyncio.ensure_future(
                imports.preview(
                    self.web, self.session, self.ref_c, None, links=[self.DOC], **self.kw
                )
            )
            await started.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            await asyncio.sleep(0.05)
        self.assertEqual(cancelled, [True])

    async def test_a_slow_google_marks_the_unfinished_docs_unreachable(self) -> None:
        async def slow(_link: object) -> str:
            await asyncio.sleep(5)
            return CRAIG

        with (
            unittest.mock.patch.object(import_link, "read_doc", slow),
            unittest.mock.patch.object(imports, "LINKS_DEADLINE_S", 0.05),
        ):
            got = await imports.preview(
                self.web, self.session, self.ref_c, None, links=[self.DOC], **self.kw
            )
        self.assertEqual(got["files"][0]["error"], "doc_unreachable")

    async def test_a_non_dm_reads_no_doc(self) -> None:
        from dmbot.web import sessions as web_sessions

        token = await web_sessions.sign_in(self.web, cast(Any, BOB), [THURSDAY], now=NOW, days=30)
        bob = await web_sessions.find(self.web, token, now=NOW)
        assert bob is not None
        fake = unittest.mock.AsyncMock(return_value=CRAIG)
        with (
            unittest.mock.patch.object(import_link, "read_doc", fake),
            self.assertRaises(RecapError),
        ):
            await imports.preview(self.web, bob, self.ref_c, None, links=[self.DOC], **self.kw)
        fake.assert_not_awaited()


class ThePreviewAndCheck(WithImports):
    async def test_the_preview_lists_speakers_and_stores_nothing(self) -> None:
        got = await self.preview(("tuesday-2026-09-14.txt", CRAIG))
        (one,) = got["files"]
        self.assertEqual(
            (one["format"], one["suggestedDate"], one["lineCount"]), ("plain", "2026-09-14", 3)
        )
        self.assertEqual(one["leftOut"], 1)
        self.assertEqual(
            [(s["label"], s["lines"]) for s in one["speakers"]],
            [("Mira (mira_v)", 2), ("Tomas (tomas#1)", 1)],
        )
        self.assertEqual(got["estimate"]["sessions"], 1)
        self.assertEqual(await self.sessions(), 0)

    async def test_a_file_that_cannot_be_used_says_why_without_its_words(self) -> None:
        got = await self.preview(
            ("notes.md", CRAIG), ("a.txt", "just prose\nwith no speakers\n"), ("b.txt", "   ")
        )
        self.assertEqual([f["error"] for f in got["files"]], ["not_txt", "unreadable", "empty"])
        self.assertNotIn("rusty", json.dumps(got))

    async def test_the_limits_are_enforced(self) -> None:
        with self.assertRaises(RecapError) as many:
            await self.preview(*[(f"{n}.txt", CRAIG) for n in range(imports.MAX_FILES + 1)])
        self.assertEqual(many.exception.code, "too_many_files")
        big = "Mira: " + "word " * (imports.MAX_BYTES // 5 + 10)
        got = await self.preview(("big.txt", big))
        self.assertEqual(got["files"][0]["error"], "too_big")

    async def test_the_check_shows_who_is_kept_and_who_is_dropped_and_stores_nothing(self) -> None:
        got = await imports.check(
            self.web, self.session, self.ref_c, files(("a.txt", CRAIG)), MAPPING, **self.kw
        )
        by = {s["label"]: s for s in got["files"][0]["speakers"]}
        self.assertEqual(by["Mira (mira_v)"]["kept"], 2)
        self.assertEqual(
            (by["Tomas (tomas#1)"]["kept"], by["Tomas (tomas#1)"]["dropped"]["not_agreed"]), (0, 1)
        )
        self.assertEqual(await self.sessions(), 0)

    async def test_a_label_remembered_from_before_is_suggested(self) -> None:
        await self.confirm(
            {"mapping": MAPPING, "sessions": [{"file": "a.txt", "date": "2026-09-14", "order": 1}]},
            ("a.txt", CRAIG),
        )
        got = await self.preview(("b.txt", SECOND.replace("Mira:", "Mira (mira_v):")))
        suggested = {s["label"]: s["suggested"] for s in got["files"][0]["speakers"]}
        self.assertEqual(
            suggested["Mira (mira_v)"], {"userId": str(MIRA), "name": "", "why": "remembered"}
        )


class TheStore(WithImports):
    async def test_only_members_who_agreed_are_stored(self) -> None:
        done = await self.confirm(
            {"mapping": MAPPING, "sessions": [{"file": "a.txt", "date": "2026-09-14", "order": 1}]},
            ("a.txt", CRAIG),
        )
        self.assertEqual(
            done["sessions"], [{"file": "a.txt", "state": "waiting", "writeRecap": False}]
        )
        (stored,) = await self.transcripts.sessions(THURSDAY.id, self.one.id)
        lines = await self.transcripts.lines(THURSDAY.id, stored.id)
        self.assertEqual({line.user_id for line in lines}, {MIRA})
        self.assertEqual(len(lines), 2)
        self.assertEqual(stored.speakers, (MIRA,))

    async def write_recap_of(self) -> dict[str, bool]:
        async with self.db.guild(THURSDAY.id) as conn:
            cur = await conn.execute("SELECT file_name, write_recap FROM session_imports")
            return {r["file_name"]: r["write_recap"] for r in await cur.fetchall()}

    async def test_write_recaps_is_kept_for_the_sessions_the_dm_chose_only(self) -> None:
        plan = {
            "mapping": MAPPING,
            "sessions": [
                {"file": "a.txt", "date": "2026-09-14", "order": 1, "writeRecap": True},
                {"file": "b.txt", "date": "2026-09-15", "order": 1},
                {"file": "c.txt", "date": "2026-09-16", "order": 1, "writeRecap": "yes"},
            ],
        }
        done = await self.confirm(plan, ("a.txt", CRAIG), ("b.txt", CRAIG), ("c.txt", CRAIG))
        self.assertEqual(
            {s["file"]: s["writeRecap"] for s in done["sessions"]},
            {"a.txt": True, "b.txt": False, "c.txt": False},
        )
        self.assertEqual(
            await self.write_recap_of(), {"a.txt": True, "b.txt": False, "c.txt": False}
        )  # (only a plain true counts)

    async def test_the_choice_door_is_for_a_dm_of_the_campaign_while_it_waits(self) -> None:
        done = await self.confirm(
            {"mapping": MAPPING, "sessions": [{"file": "a.txt", "date": "2026-09-14"}]},
            ("a.txt", CRAIG),
        )
        (stored,) = await self.transcripts.sessions(THURSDAY.id, self.one.id)
        from dmbot.web import sessions as web_sessions

        token = await web_sessions.sign_in(self.web, cast(Any, BOB), [THURSDAY], now=NOW, days=30)
        bob = await web_sessions.find(self.web, token, now=NOW)
        assert bob is not None
        async with self.web.user(BOB.id, session=bob.id_hash) as conn:
            await conn.execute("SELECT set_config('dmbot.guild_id', %s, true)", (str(THURSDAY.id),))
            cur = await conn.execute(
                "SELECT dmbot_import_write_recap(%s, %s, %s, TRUE) AS r",
                (THURSDAY.id, self.one.id, stored.id),
            )
            row = await cur.fetchone()
        assert row is not None
        self.assertFalse(row["r"])  # not a DM of this campaign
        self.assertEqual(await self.write_recap_of(), {"a.txt": False})
        self.assertEqual(done["sessions"][0]["writeRecap"], False)
        async with self.db.guild(THURSDAY.id) as conn:  # once it is being cleaned it is too late
            await conn.execute("UPDATE session_imports SET state = 'working'")
        async with self.web.user(ALICE.id, session=self.session.id_hash) as conn:
            await conn.execute("SELECT set_config('dmbot.guild_id', %s, true)", (str(THURSDAY.id),))
            cur = await conn.execute(
                "SELECT dmbot_import_write_recap(%s, %s, %s, TRUE) AS r",
                (THURSDAY.id, self.one.id, stored.id),
            )
            row = await cur.fetchone()
        assert row is not None
        self.assertFalse(row["r"])

    async def test_the_door_drops_a_line_of_anyone_not_recorded_whatever_it_is_sent(self) -> None:
        sent = [
            {"ms": 0, "user": MIRA, "text": "kept", "dur": 1000},
            {"ms": 1000, "user": TOMAS, "text": "not agreed", "dur": 1000},
            {"ms": 2000, "user": NOBODY, "text": "unknown", "dur": 1000},
        ]
        async with self.web.user(self.session.user_id, session=self.session.id_hash) as conn:
            await conn.execute("SELECT set_config('dmbot.guild_id', %s, true)", (str(THURSDAY.id),))
            cur = await conn.execute(
                "SELECT dmbot_import_store(%s, %s, %s, %s, %s, %s::jsonb, %s, NULL) AS r",
                (
                    THURSDAY.id,
                    self.one.id,
                    "a" * 32,
                    "x.txt",
                    1_700_000_000,
                    json.dumps(sent),
                    TERMS_VERSION,
                ),
            )
            got = (await cur.fetchone() or {})["r"]
        self.assertEqual((got["kept"], got["dropped"]), (1, 2))
        (stored,) = await self.transcripts.sessions(THURSDAY.id, self.one.id)
        self.assertEqual(
            [line.heard for line in await self.transcripts.lines(THURSDAY.id, stored.id)], ["kept"]
        )

    async def test_the_door_refuses_old_terms_whatever_the_caller_passes(self) -> None:
        async with self.web.user(self.session.user_id, session=self.session.id_hash) as conn:
            await conn.execute("SELECT set_config('dmbot.guild_id', %s, true)", (str(THURSDAY.id),))
            cur = await conn.execute(
                "SELECT dmbot_import_store(%s, %s, %s, %s, %s, %s::jsonb, 1, NULL) AS r",
                (THURSDAY.id, self.one.id, "a" * 32, "x.txt", 1_700_000_000,
                 json.dumps([{"ms": 0, "user": MIRA, "text": "hi", "dur": 1}])),
            )  # fmt: skip
            self.assertIsNone((await cur.fetchone() or {})["r"])
        self.assertEqual(await self.sessions(), 0)

    async def test_the_floor_in_the_door_is_the_bots_terms_or_older(self) -> None:
        from dmbot import schema

        self.assertLessEqual(schema.IMPORT_TERMS_FLOOR, TERMS_VERSION)
        self.assertIn(f"terms < {schema.IMPORT_TERMS_FLOOR} OR", schema.SESSION_IMPORTS)

    async def test_any_member_who_agreed_can_be_mapped_even_in_a_new_campaign(self) -> None:
        stranger = 8_000_000_000_000_000_001  # 19 digits, newer than the old 18
        await ConsentStore(self.db).grant(THURSDAY.id, stranger)
        done = await self.confirm(
            {
                "mapping": {"Mira (mira_v)": str(stranger)},
                "sessions": [{"file": "a.txt", "date": "2026-09-14"}],
            },
            ("a.txt", CRAIG),
        )
        self.assertEqual(done["sessions"][0]["state"], "waiting")
        (stored,) = await self.transcripts.sessions(THURSDAY.id, self.one.id)
        self.assertEqual(stored.speakers, (stranger,))
        got = await imports.preview(
            self.web, self.session, self.ref_c, files(("b.txt", CRAIG)), **self.kw
        )
        remembered = {s["label"]: s["suggested"] for s in got["files"][0]["speakers"]}
        self.assertEqual(remembered["Mira (mira_v)"]["userId"], str(stranger))  # remembered whole

    async def test_only_people_recorded_now_are_remembered_for_next_time(self) -> None:
        await self.confirm(
            {"mapping": MAPPING, "sessions": [{"file": "a.txt", "date": "2026-09-14"}]},
            ("a.txt", CRAIG),
        )
        async with self.db.guild(THURSDAY.id) as conn:
            cur = await conn.execute("SELECT user_id FROM import_speakers ORDER BY 1")
            self.assertEqual([r["user_id"] for r in await cur.fetchall()], [MIRA])  # not Tomas

    async def test_the_members_search_covers_the_servers_recorded_people_only(self) -> None:
        stranger, unrecorded, elsewhere = 9101, 9102, 9103
        await ConsentStore(self.db).grant(THURSDAY.id, stranger)
        await ConsentStore(self.db).grant(555, elsewhere)  # recorded, but on another server
        for who, name in ((stranger, "Zed Fenn"), (unrecorded, "Zed Gray"), (elsewhere, "Zed Ode")):
            user = SimpleNamespace(id=who, name=name, email=None)
            await web_sessions.sign_in(self.web, cast(Any, user), [THURSDAY], now=NOW, days=30)
        got = await imports.search_members(self.web, self.session, self.ref_c, "zed", **self.kw)
        self.assertEqual(
            [(m["userId"], m["agreed"]) for m in got["members"]], [(str(stranger), True)]
        )

    async def test_a_second_file_the_door_refuses_stores_nothing_at_all(self) -> None:
        await ConsentStore(self.db).grant(THURSDAY.id, TOMAS)
        original = imports._plans

        def then_tomas_stops(*args: Any, **kw: Any) -> Any:
            plans = original(*args, **kw)  # both files look fine here ...
            self.revoke = True
            return plans

        self.revoke = False
        with unittest.mock.patch.object(imports, "_plans", then_tomas_stops):
            real = Database.user
            seen = {"n": 0}

            @contextlib.asynccontextmanager
            async def user(db: Any, *a: Any, **kw: Any) -> Any:
                async with real(db, *a, **kw) as conn:
                    if self.revoke and seen["n"] == 0:
                        seen["n"] = 1
                        async with self.db.guild(THURSDAY.id) as other:  # ... until the store
                            await other.execute("DELETE FROM consent WHERE user_id = %s", (TOMAS,))
                    yield conn

            with (
                unittest.mock.patch.object(Database, "user", user),
                self.assertRaises(RecapError) as refused,
            ):
                await self.confirm(
                    {
                        "mapping": MAPPING,
                        "sessions": [
                            {"file": "a.txt", "date": "2026-09-14"},
                            {"file": "b.txt", "date": "2026-09-15"},
                        ],
                    },
                    ("a.txt", CRAIG),
                    ("b.txt", "Tomas (tomas#1): hello there friend.\n"),
                )
        self.assertEqual(refused.exception.code, "no_lines_kept")
        self.assertEqual(await self.sessions(), 0)

    async def test_session_dates_must_be_from_2000_to_2100(self) -> None:
        cases = {"1999-12-31": False, "2000-01-01": True, "2100-01-01": True, "2100-01-02": False}
        for day, ok in cases.items():
            if ok:
                self.assertIsInstance(imports._started_at(day, 0), int, day)
            else:
                with self.assertRaises(RecapError, msg=day):
                    imports._started_at(day, 0)

    async def test_a_new_session_is_stored_at_noon_utc_of_its_day(self) -> None:
        await self.confirm(
            {"mapping": MAPPING, "sessions": [{"file": "a.txt", "date": "2026-09-14", "order": 0}]},
            ("a.txt", CRAIG),
        )
        (stored,) = await self.transcripts.sessions(THURSDAY.id, self.one.id)
        self.assertEqual(stored.started_at % 86400, 12 * 3600)

    async def test_a_list_of_sessions_longer_than_the_files_or_repeating_one_is_refused(
        self,
    ) -> None:
        for sessions in (
            [{"file": "a.txt", "date": "2026-09-14"}] * 2,
            [{"file": "a.txt", "date": "2026-09-14"}, {"file": "b.txt", "date": "2026-09-15"}],
            [{"file": ["a.txt"], "date": "2026-09-14"}],
        ):
            with self.assertRaises(RecapError):
                await self.confirm({"mapping": MAPPING, "sessions": sessions}, ("a.txt", CRAIG))
        self.assertEqual(await self.sessions(), 0)

    async def test_two_uploaded_files_with_one_name_are_refused(self) -> None:
        with self.assertRaises(RecapError):
            await self.preview(("a.txt", CRAIG), ("a.txt", SECOND))

    async def test_a_file_with_too_many_lines_or_speakers_is_marked(self) -> None:
        many_lines = "Mira: hi there friend\n" * (imports.MAX_LINES + 1)
        many_speakers = "".join(
            f"Speaker{n}: hello there\n" for n in range(imports.MAX_SPEAKERS + 1)
        )
        got = await self.preview(("l.txt", many_lines), ("s.txt", many_speakers))
        self.assertEqual(
            [f["error"] for f in got["files"]], ["too_many_lines", "too_many_speakers"]
        )

    async def test_a_caller_without_a_plan_gets_no_parsing_on_preview(self) -> None:
        from unittest.mock import patch

        with patch.object(imports, "read_files") as parsed, self.assertRaises(RecapError):
            await imports.preview(
                self.web, self.session, self.ref_c, files(("a.txt", CRAIG)),
                outside=None, now=int(time.time()), enforce_plans=True,
            )  # fmt: skip
        parsed.assert_not_called()

    async def test_consent_under_old_terms_does_not_count(self) -> None:
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute("UPDATE consent SET terms_version = %s", (TERMS_VERSION - 1,))
        with self.assertRaises(RecapError) as refused:
            await self.confirm(
                {"mapping": MAPPING, "sessions": [{"file": "a.txt", "date": "2026-09-14"}]},
                ("a.txt", CRAIG),
            )
        self.assertEqual(refused.exception.code, "no_lines_kept")
        self.assertEqual(await self.sessions(), 0)

    async def test_a_file_with_no_lines_kept_stops_the_whole_import(self) -> None:
        only_tomas = "Tomas (tomas#1): hello there friend.\n"
        with self.assertRaises(RecapError):
            await self.confirm(
                {
                    "mapping": MAPPING,
                    "sessions": [
                        {"file": "a.txt", "date": "2026-09-14"},
                        {"file": "b.txt", "date": "2026-09-15"},
                    ],
                },
                ("a.txt", CRAIG),
                ("b.txt", only_tomas),
            )
        self.assertEqual(await self.sessions(), 0)

    async def test_two_files_on_one_day_keep_the_dms_order_and_never_join_a_session(self) -> None:
        plan = {
            "mapping": MAPPING,
            "sessions": [
                {"file": "a.txt", "date": "2026-09-14", "order": 1},
                {"file": "b.txt", "date": "2026-09-14", "order": 2},
            ],
        }
        await self.confirm(
            plan,
            ("a.txt", CRAIG),
            (
                "b.txt",
                SECOND.replace("Mira:", "Mira (mira_v):").replace("Tomas:", "Tomas (tomas#1):"),
            ),
        )
        stored = await self.transcripts.sessions(THURSDAY.id, self.one.id)
        self.assertEqual(len(stored), 2)
        self.assertEqual(
            sorted(s.started_at for s in stored), sorted({s.started_at for s in stored})
        )

    async def test_a_bad_date_or_mapping_is_refused(self) -> None:
        for plan in (
            {"mapping": MAPPING, "sessions": [{"file": "a.txt", "date": "soon"}]},
            {
                "mapping": {"Mira (mira_v)": "not a number"},
                "sessions": [{"file": "a.txt", "date": "2026-09-14"}],
            },
        ):
            with self.assertRaises(RecapError):
                await self.confirm(plan, ("a.txt", CRAIG))
        self.assertEqual(await self.sessions(), 0)


class TheRetention(WithImports):
    async def test_an_imported_session_is_kept_from_the_day_it_was_imported(self) -> None:
        await self.confirm(
            {"mapping": MAPPING, "sessions": [{"file": "a.txt", "date": "2024-03-02"}]},
            ("a.txt", CRAIG),
        )
        old = await self.transcripts.open_session(THURSDAY.id, self.one.id, 1_700_000_000)
        from dmbot.transcript.models import Line

        await self.transcripts.add_lines(
            THURSDAY.id, old, [Line(1_700_000_000_000, MIRA, "hello", "hello")]
        )
        await self.transcripts.end_session(THURSDAY.id, old, 1_700_000_100)
        cutoff = int(time.time()) - 30 * 86400  # "keep transcripts for 30 days"
        listed = await self.transcripts.expired_sessions(THURSDAY.id, self.one.id, cutoff)
        self.assertEqual(
            listed, [old]
        )  # the plain old session goes; the 2024 game just loaded stays


class TheOutsideEngine(WithImports):
    async def test_consent_given_for_another_outside_company_does_not_count(self) -> None:
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute("UPDATE consent SET outside_to = 'cloud'")
        kw = {**self.kw, "outside": "deepgram"}
        with self.assertRaises(RecapError) as refused:
            await imports.confirm(
                self.web, self.session, self.ref_c, files(("a.txt", CRAIG)),
                {"mapping": MAPPING, "sessions": [{"file": "a.txt", "date": "2026-09-14"}]},
                **kw,
            )  # fmt: skip
        self.assertEqual(refused.exception.code, "no_lines_kept")
        kw["outside"] = "cloud"
        done = await imports.confirm(
            self.web, self.session, self.ref_c, files(("a.txt", CRAIG)),
            {"mapping": MAPPING, "sessions": [{"file": "a.txt", "date": "2026-09-14"}]},
            **kw,
        )  # fmt: skip
        self.assertEqual(done["sessions"][0]["state"], "waiting")


class TheRoutes(WithImports):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        app = create_app(
            settings(imports_on=True), self.web, FakeDiscord(),
            payments=FakeProvider(b"h" * 32, SITE), clock=lambda: NOW,
        )  # fmt: skip
        self.clients = []
        for who in (ALICE, BOB):
            token = await web_sessions.sign_in(
                self.web, cast(Any, who), [THURSDAY], now=NOW, days=30
            )
            client = httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url=API, headers=HEADERS
            )
            client.cookies.set("__Host-dmbot_session", token, domain="api.dmbot.example")
            self.clients.append(client)
        self.dm, self.bob = self.clients

    async def asyncTearDown(self) -> None:
        for client in self.clients:
            await client.aclose()
        await super().asyncTearDown()

    async def test_preview_over_http(self) -> None:
        got = await self.dm.post(
            f"/me/campaigns/{self.ref_c}/imports/preview", json={"files": files(("a.txt", CRAIG))}
        )
        self.assertEqual(got.status_code, 200, got.text)
        self.assertEqual(got.json()["files"][0]["speakers"][0]["label"], "Mira (mira_v)")

    async def test_a_person_who_is_not_a_dm_is_refused_before_the_body_is_read(self) -> None:
        got = await self.bob.post(
            f"/me/campaigns/{self.ref_c}/imports/preview",
            content=b"{not even json",
            headers={"content-type": "application/json"},
        )
        self.assertEqual((got.status_code, got.json()["error"]), (403, "not_a_dm"))

    async def test_every_import_route_is_not_found_while_the_switch_is_off(self) -> None:
        off = create_app(
            settings(), self.web, FakeDiscord(), payments=FakeProvider(b"h" * 32, SITE),
            clock=lambda: NOW,
        )  # fmt: skip
        token = await web_sessions.sign_in(self.web, cast(Any, ALICE), [THURSDAY], now=NOW, days=30)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=off), base_url=API, headers=HEADERS
        ) as client:
            client.cookies.set("__Host-dmbot_session", token, domain="api.dmbot.example")
            base = f"/me/campaigns/{self.ref_c}/imports"
            for method, path in (
                ("POST", "/preview"), ("GET", "/members"), ("POST", "/check"), ("POST", ""),
                ("GET", "/" + "f" * 32),
            ):  # fmt: skip
                got = await client.request(method, base + path, json={"files": []})
                self.assertEqual((got.status_code, got.json()["error"]), (404, "not_found"), path)

    async def test_a_body_of_the_worst_escaped_legal_files_fits(self) -> None:
        worst = '\n"' * (imports.MAX_BYTES // 2)  # 2 MB that JSON writes at twice the size
        body = json.dumps({"files": [{"name": f"{n}.txt", "text": worst} for n in range(20)]})
        self.assertGreater(len(body), 20 * imports.MAX_BYTES)
        self.assertLess(len(body), imports.MAX_BODY_BYTES)
        got = await self.dm.post(
            f"/me/campaigns/{self.ref_c}/imports/preview",
            content=body,
            headers={"content-type": "application/json"},
        )
        self.assertEqual(got.status_code, 200, got.text[:200])

    async def test_a_body_over_the_limit_is_refused_with_413(self) -> None:
        got = await self.dm.post(
            f"/me/campaigns/{self.ref_c}/imports/preview",
            content=b"x" * (imports.MAX_BODY_BYTES + 10),
            headers={"content-type": "application/json"},
        )
        self.assertEqual((got.status_code, got.json()["error"]), (413, "too_big"))

    async def test_too_many_requests_get_too_soon(self) -> None:
        codes = []
        for _ in range(8):
            got = await self.dm.post(
                f"/me/campaigns/{self.ref_c}/imports/preview", json={"files": []}
            )
            codes.append((got.status_code, got.json().get("error")))
        self.assertEqual(codes[-1], (429, "too_soon"))

    async def test_the_error_codes_of_a_confirm(self) -> None:
        for body, status, code in (
            (
                {
                    "files": files(("a.txt", CRAIG)),
                    "plan": {"mapping": MAPPING, "sessions": [{"file": "a.txt", "date": "x"}]},
                },
                422,
                "bad_date",
            ),
            (
                {
                    "files": files(("a.txt", CRAIG)),
                    "plan": {"mapping": {"Mira (mira_v)": "nope"}, "sessions": []},
                },
                422,
                "bad_mapping",
            ),
            (
                {
                    "files": [],
                    "plan": {
                        "mapping": MAPPING,
                        "sessions": [{"file": "a.txt", "date": "2026-09-14"}],
                    },
                },
                422,
                "bad_files",
            ),
        ):
            got = await self.dm.post(f"/me/campaigns/{self.ref_c}/imports", json=body)
            self.assertEqual((got.status_code, got.json()["error"]), (status, code))

    async def test_a_confirm_over_http_is_accepted_and_can_be_followed(self) -> None:
        got = await self.dm.post(
            f"/me/campaigns/{self.ref_c}/imports",
            json={
                "files": files(("a.txt", CRAIG)),
                "plan": {"mapping": MAPPING, "sessions": [{"file": "a.txt", "date": "2026-09-14"}]},
            },
        )
        self.assertEqual(got.status_code, 202, got.text)
        seen = await self.dm.get(f"/me/campaigns/{self.ref_c}/imports/{got.json()['importId']}")
        self.assertEqual(seen.json()["sessions"][0]["state"], "waiting")


class TheProgress(WithImports):
    async def test_a_stored_session_is_waiting_until_it_is_cleaned(self) -> None:
        done = await self.confirm(
            {"mapping": MAPPING, "sessions": [{"file": "a.txt", "date": "2026-09-14"}]},
            ("a.txt", CRAIG),
        )
        got = await imports.progress(
            self.web, self.session, self.ref_c, done["importId"], outside=None
        )
        self.assertEqual(
            got["sessions"],
            [
                {
                    "file": "a.txt",
                    "date": "2026-09-14",
                    "state": "waiting",
                    "reason": None,
                    "recap": None,
                }
            ],
        )

    async def test_an_unknown_import_is_not_found(self) -> None:
        with self.assertRaises(RecapError):
            await imports.progress(self.web, self.session, self.ref_c, "f" * 32, outside=None)


class TheRecapFacts(WithImports):
    """Whether the import page may offer a recap tick (#2225): the bot's own gates, read first."""

    async def preview(self, **kw: Any) -> dict[str, Any]:  # type: ignore[override]
        return await imports.preview(
            self.web,
            self.session,
            self.ref_c,
            files(("a.txt", CRAIG)),
            **{**self.kw, "site_recaps_on": True, **kw},
        )

    async def test_it_is_possible_when_every_gate_is_open(self) -> None:
        got = await self.preview()
        self.assertEqual((got["recapsPossible"], "recapsWhy" in got), (True, False))

    async def test_switched_off_for_the_server(self) -> None:
        got = await self.preview(site_recaps_on=False)
        self.assertEqual((got["recapsPossible"], got["recapsWhy"]), (False, "off"))

    async def test_turned_off_by_the_dm_for_the_campaign(self) -> None:
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute(
                "UPDATE campaigns SET recaps_text = FALSE WHERE id = %s", (self.one.id,)
            )
        got = await self.preview()
        self.assertEqual((got["recapsPossible"], got["recapsWhy"]), (False, "campaign_off"))

    async def test_the_confirm_step_says_it_too(self) -> None:
        done = await imports.confirm(
            self.web,
            self.session,
            self.ref_c,
            files(("a.txt", CRAIG)),
            {"mapping": MAPPING, "sessions": [{"file": "a.txt", "date": "2026-09-14"}]},
            **{**self.kw, "site_recaps_on": False},
        )
        self.assertEqual((done["recapsPossible"], done["recapsWhy"]), (False, "off"))

    async def test_the_plan_decides_from_the_owner_and_costs_nothing(self) -> None:
        from dmbot.entitlements import Access

        campaign = imports.Campaign(THURSDAY.id, self.one.id, frozenset(), 99)
        free = Access("free", "Free access", 1, 1, False)
        paid = Access("paid", "Pro", None, None, True)
        for access, want in ((free, False), (paid, True), (None, False)):
            with unittest.mock.patch.object(
                imports, "plan_grant_access", AsyncMock(return_value=(None, None, access))
            ) as asked:
                got = await imports.recap_facts(
                    self.web,
                    self.session,
                    self.ref_c,
                    campaign,
                    site_recaps_on=True,
                    now=NOW,
                    enforce_plans=True,
                )
            self.assertEqual(got["recapsPossible"], want, access)
            if not want:
                self.assertEqual(got["recapsWhy"], "plan")
            asked.assert_awaited_once()  # (the owner's plan is read, nothing is spent)

    async def test_a_campaign_without_an_owner_has_no_plan(self) -> None:
        campaign = imports.Campaign(THURSDAY.id, self.one.id, frozenset(), None)
        got = await imports.recap_facts(
            self.web,
            self.session,
            self.ref_c,
            campaign,
            site_recaps_on=True,
            now=NOW,
            enforce_plans=True,
        )
        self.assertEqual((got["recapsPossible"], got["recapsWhy"]), (False, "plan"))

    async def test_the_reasons_come_in_the_bots_order(self) -> None:
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute(
                "UPDATE campaigns SET recaps_text = FALSE WHERE id = %s", (self.one.id,)
            )
        got = await self.preview(site_recaps_on=False)
        self.assertEqual(got["recapsWhy"], "off")  # the server's switch speaks first


class TheGuards(WithImports):
    async def test_a_person_who_is_not_a_dm_of_the_campaign_is_refused_everywhere(self) -> None:
        from dmbot.web import sessions as web_sessions

        token = await web_sessions.sign_in(self.web, cast(Any, BOB), [THURSDAY], now=NOW, days=30)
        bob = await web_sessions.find(self.web, token, now=NOW)
        assert bob is not None
        for call in (
            imports.preview(self.web, bob, self.ref_c, files(("a.txt", CRAIG)), **self.kw),
            imports.check(self.web, bob, self.ref_c, files(("a.txt", CRAIG)), MAPPING, **self.kw),
            imports.confirm(
                self.web,
                bob,
                self.ref_c,
                files(("a.txt", CRAIG)),
                {"mapping": MAPPING, "sessions": [{"file": "a.txt", "date": "2026-09-14"}]},
                **self.kw,
            ),
            imports.search_members(self.web, bob, self.ref_c, "", **self.kw),
        ):
            with self.assertRaises(RecapError) as refused:
                await call
            self.assertEqual(refused.exception.code, "not_a_dm")
        self.assertEqual(await self.sessions(), 0)

    async def test_a_campaign_without_a_paid_plan_is_refused(self) -> None:
        with self.assertRaises(RecapError) as refused:
            await imports.preview(
                self.web, self.session, self.ref_c, files(("a.txt", CRAIG)),
                outside=None, now=int(time.time()), enforce_plans=True,
            )  # fmt: skip
        self.assertIn(refused.exception.code, ("not_on_a_paid_plan", "try_it_campaign"))

    async def test_a_try_it_campaign_is_refused_with_its_own_code(self) -> None:
        async with self.db.plan_writer(self.session.user_id) as conn:
            await conn.execute(
                "DELETE FROM entitlements WHERE user_id = %s",
                (self.session.user_id,),
            )
            await conn.execute(
                "INSERT INTO entitlements (user_id, plan, status, hours_cap, extra_hours,"
                " campaign_cap, period_start, period_end, grace_ends_at, lapsed_at,"
                " plan_changed_at, provider, provider_subscription_id, last_event_at, updated_at)"
                " VALUES (%s, 'try-it', 'active', 3, 0, 1, %s, %s, NULL, NULL, 0, 'fake',"
                " 'sub_1', 0, 0)",
                (self.session.user_id, NOW - 100, NOW + 86400),
            )
        with self.assertRaises(RecapError) as refused:
            await imports.preview(
                self.web, self.session, self.ref_c, files(("a.txt", CRAIG)),
                outside=None, now=NOW, enforce_plans=True,
            )  # fmt: skip
        self.assertEqual(refused.exception.code, "try_it_campaign")

    async def test_the_members_door_has_one_caller(self) -> None:
        from tests.test_recap_web import callers

        for door in ("dmbot_import_store", "dmbot_import_agreed", "dmbot_import_members"):
            self.assertEqual(callers(door), {"schema.py", "web/imports.py"}, door)


if __name__ == "__main__":
    import unittest

    unittest.main()


class TheMemberDirectory(WithImports):
    """The server's recorded members who never signed in to the website (#2141)."""

    async def find(self, q: str) -> list[tuple[str, bool]]:
        got = await imports.search_members(self.web, self.session, self.ref_c, q, **self.kw)
        return [(m["name"], m["agreed"]) for m in got["members"]]

    async def test_a_recorded_member_who_never_signed_in_can_be_found_by_name_or_username(
        self,
    ) -> None:
        from dmbot.members import MemberDirectory

        who = 9201
        await ConsentStore(self.db).grant(THURSDAY.id, who)
        await MemberDirectory(self.db).remember(THURSDAY.id, who, "Quill Ardent", "quill_a")
        self.assertEqual(await self.find("quill"), [("Quill Ardent", True)])
        self.assertEqual(await self.find("_a"), [("Quill Ardent", True)])  # the username too
        self.assertEqual(await self.find("nobody"), [])

    async def test_a_member_who_withdrew_or_has_old_consent_is_not_offered(self) -> None:
        from dmbot.members import MemberDirectory

        who = 9202
        directory = MemberDirectory(self.db)
        await ConsentStore(self.db).grant(THURSDAY.id, who)
        await directory.remember(THURSDAY.id, who, "Quill Ardent", "quill_a")
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute("UPDATE consent SET terms_version = %s", (TERMS_VERSION - 1,))
            await conn.execute(
                "UPDATE consent SET terms_version = %s WHERE user_id = %s", (TERMS_VERSION, MIRA)
            )
        self.assertEqual(await self.find("quill"), [])  # a stale row is never offered
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute("UPDATE consent SET terms_version = %s", (TERMS_VERSION,))
        self.assertEqual(await self.find("quill"), [("Quill Ardent", True)])
        await directory.forget(THURSDAY.id, who)
        self.assertEqual(await self.find("quill"), [])

    async def test_another_servers_members_never_show(self) -> None:
        from dmbot.members import MemberDirectory

        who = 9203
        await ConsentStore(self.db).grant(555, who)
        await MemberDirectory(self.db).remember(555, who, "Quill Ardent", "quill_a")
        self.assertEqual(await self.find("quill"), [])
        async with self.db.guild(THURSDAY.id) as conn:  # and the table itself is per server
            cur = await conn.execute("SELECT count(*) AS n FROM member_directory")
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual(row["n"], 0)

    async def test_the_campaigns_own_person_comes_first_and_is_not_listed_twice(self) -> None:
        from dmbot.members import MemberDirectory

        await MemberDirectory(self.db).remember(THURSDAY.id, MIRA, "Mira Real", "mira_v")
        for q in ("", "mira_v", "real", "player 0"):  # whichever of her names is searched
            names = [n for n, _ in await self.find(q)]
            self.assertEqual(names.count("Player 0"), 1, q)  # MIRA is "Player 0" here
            self.assertNotIn("Mira Real", names, q)

    async def test_forgetting_one_member_also_drops_rows_of_people_with_no_consent(self) -> None:
        from dmbot.members import MemberDirectory

        directory = MemberDirectory(self.db)
        for who in (9301, 9302):
            await ConsentStore(self.db).grant(THURSDAY.id, who)
            await directory.remember(THURSDAY.id, who, f"Name {who}", f"u{who}")
        async with self.db.guild(THURSDAY.id) as conn:  # 9301 stopped on the website, bot down
            await conn.execute("DELETE FROM consent WHERE user_id = 9301")
        await directory.forget(THURSDAY.id, 9999)  # any stop in the server tidies the rest
        async with self.db.guild(THURSDAY.id) as conn:
            cur = await conn.execute("SELECT user_id FROM member_directory")
            self.assertEqual([r["user_id"] for r in await cur.fetchall()], [9302])

    async def test_nothing_is_kept_for_someone_with_no_consent_row(self) -> None:
        from dmbot.members import MemberDirectory

        await MemberDirectory(self.db).remember(THURSDAY.id, 9206, "Ghost", "ghost")
        async with self.db.guild(THURSDAY.id) as conn:
            cur = await conn.execute("SELECT count(*) AS n FROM member_directory")
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual(row["n"], 0)

    async def test_consent_for_another_outside_company_hides_the_member(self) -> None:
        from dmbot.members import MemberDirectory

        who = 9207
        await ConsentStore(self.db).grant(THURSDAY.id, who)
        await MemberDirectory(self.db).remember(THURSDAY.id, who, "Quill Ardent", "quill_a")
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute(
                "UPDATE consent SET outside_to = 'deepgram' WHERE user_id = %s", (who,)
            )
        kw = {**self.kw, "outside": "other-company"}
        got = await imports.search_members(self.web, self.session, self.ref_c, "quill", **kw)
        self.assertEqual(
            got["members"], [{"userId": str(who), "name": "Quill Ardent", "agreed": False}][:0]
        )

    async def test_empty_names_keep_no_row_and_a_name_is_cut_to_a_sane_length(self) -> None:
        from dmbot.members import MemberDirectory

        directory = MemberDirectory(self.db)
        for who in (9204, 9205):
            await ConsentStore(self.db).grant(THURSDAY.id, who)
        await directory.remember(THURSDAY.id, 9204, "", None)
        await directory.remember(THURSDAY.id, 9205, "x" * 500, "  ")
        async with self.db.guild(THURSDAY.id) as conn:
            cur = await conn.execute(
                "SELECT user_id, char_length(display_name) AS n FROM member_directory"
            )
            rows = await cur.fetchall()
        self.assertEqual([(r["user_id"], r["n"]) for r in rows], [(9205, 100)])
