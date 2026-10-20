"""Importing older sessions through the website's doors (#2105). Invented fixtures only."""

from __future__ import annotations

import json
import time
from typing import Any

from dmbot.consent import TERMS_VERSION, ConsentStore
from dmbot.transcript.store import TranscriptStore
from dmbot.web import imports
from dmbot.web.recaps import RecapError
from tests.test_recap_web import BOB, NOW, Base
from tests.test_web_api import THURSDAY

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
        self.assertEqual(done["sessions"], [{"file": "a.txt", "state": "waiting"}])
        (stored,) = await self.transcripts.sessions(THURSDAY.id, self.one.id)
        lines = await self.transcripts.lines(THURSDAY.id, stored.id)
        self.assertEqual({line.user_id for line in lines}, {MIRA})
        self.assertEqual(len(lines), 2)
        self.assertEqual(stored.speakers, (MIRA,))

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


class TheGuards(WithImports):
    async def test_a_person_who_is_not_a_dm_of_the_campaign_is_refused_everywhere(self) -> None:
        from dmbot.web import sessions as web_sessions

        token = await web_sessions.sign_in(self.web, BOB, [THURSDAY], now=NOW, days=30)
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

    async def test_the_members_door_has_one_caller(self) -> None:
        from tests.test_recap_web import callers

        for door in ("dmbot_import_store", "dmbot_import_agreed", "dmbot_import_members"):
            self.assertEqual(callers(door), {"schema.py", "web/imports.py"}, door)


if __name__ == "__main__":
    import unittest

    unittest.main()
