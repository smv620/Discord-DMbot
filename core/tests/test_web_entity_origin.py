"""Where an entry came from, on the website's "Where was it said?" (#4557): the DM's door gives the
upload an entry was read from, the lines a kept thread pointed at (only a speaker who agrees now)
and who added it; nobody else's does. `origins` is pure. Real Postgres for the door; invented
names."""

from __future__ import annotations

import unittest
from typing import Any

from dmbot.consent import TERMS_VERSION
from dmbot.memory import thread_read as tr
from dmbot.memory.models import DM
from dmbot.memory.thread_suggestions import ThreadSuggestionStore
from dmbot.transcript.models import Line as Heard
from dmbot.transcript.store import TranscriptStore
from dmbot.web import graph_cleanup
from dmbot.web.recaps import _scoped
from tests.agreed import agree
from tests.test_scenes import ALICE, DM_ID, Base

START = 1_700_000_000
LINE_MS = START * 1000 + 2_000


class TheShape(unittest.TestCase):
    def test_nothing_known_is_nothing(self) -> None:
        self.assertEqual(graph_cleanup.origins(None), [])
        self.assertEqual(graph_cleanup.origins({"source": "extractor", "uploads": []}), [])

    def test_a_name_the_dm_typed_says_so_only_when_nothing_else_does(self) -> None:
        self.assertEqual(graph_cleanup.origins({"source": DM, "uploads": []}), [{"kind": "yours"}])
        up = {"source": DM, "uploads": [{"label": "Notes", "at": 5}]}
        self.assertEqual(graph_cleanup.origins(up), [{"kind": "upload", "label": "Notes", "at": 5}])

    def test_a_thread_read_from_a_session_gives_its_lines_and_uploads_follow(self) -> None:
        got = graph_cleanup.origins(
            {
                "source": DM,
                "uploads": [{"label": "Notes", "at": 5}],
                "thread": {"session": 2, "at": 9, "lines": ["We  will\nfind him."], "upload": ""},
            }
        )
        self.assertEqual(
            got,
            [
                {"kind": "session", "session": 2, "at": 9, "lines": ["We will find him."]},
                {"kind": "upload", "label": "Notes", "at": 5},
            ],
        )

    def test_a_thread_read_from_an_upload_names_it_with_the_suggestions_own_day(self) -> None:
        got = graph_cleanup.origins(
            {
                "source": DM,
                "uploads": [],
                "thread": {"session": None, "upload": "Plot", "madeAt": 7},
            }
        )
        self.assertEqual(got, [{"kind": "upload", "label": "Plot", "at": 7}])

    def test_a_session_whose_lines_are_gone_has_none_to_show(self) -> None:
        got = graph_cleanup.origins(
            {"thread": {"session": 1, "at": 3, "lines": [], "removed": True, "upload": ""}}
        )
        self.assertEqual(got, [{"kind": "session", "session": 1, "at": 3, "lines": []}])

    def test_at_most_three_are_shown(self) -> None:
        ups = [{"label": f"U{n}", "at": n} for n in range(5)]
        self.assertEqual(len(graph_cleanup.origins({"uploads": ups})), 3)


class TheDoor(Base):
    async def origin(self, who: Any, campaign: str, entity: str) -> Any:
        async with self.web.user(who.user_id, session=who.id_hash) as conn:
            await _scoped(conn, self.g)
            cur = await conn.execute(
                "SELECT dmbot_web_entity_origin(%s, %s, %s, %s, %s) AS r",
                (self.g, campaign, entity, TERMS_VERSION, None),
            )
            found = await cur.fetchone()
            assert found is not None
            return found["r"]

    async def test_the_dm_reads_the_upload_and_nobody_else_does(self) -> None:
        await self.memory.record_name_sources(
            self.g, self.one.id, [self.mira], upload_key="u1", label="Harbour notes", headings={}
        )
        got = await self.origin(self.session, self.one.id, self.mira)
        self.assertEqual([u["label"] for u in got["uploads"]], ["Harbour notes"])
        self.assertIsNone(got["thread"])
        self.assertIsNone(await self.origin(self.player, self.one.id, self.mira))
        other = (await self.campaigns.create(self.g, "Elsewhere", 7)).id
        self.assertIsNone(await self.origin(self.session, other, self.mira))

    async def test_a_kept_threads_lines_are_read_now_and_only_for_those_who_agree(self) -> None:
        await agree(self.db, self.g, DM_ID, ALICE)
        sid = await TranscriptStore(self.db).open_session(self.g, self.one.id, START)
        await TranscriptStore(self.db).add_lines(
            self.g,
            sid,
            [Heard(LINE_MS, ALICE, "x", "We'll find him."), Heard(LINE_MS + 1, 99, "x", "Hidden.")],
        )
        store = ThreadSuggestionStore(self.db)
        job = tr.Suggestion(
            "quest", "Find Gnashwater", "find him", "",
            ((tr.TARGET, self.mira),), tr.STATE["quest"],
            line_starts=(LINE_MS, LINE_MS + 1), speaker_ids=(ALICE, 99),
        )  # fmt: skip
        await store.offer(
            self.g, self.one.id, [job], "the session", 5, session_id=sid, session_at=START
        )
        (w,) = await store.waiting(self.g, self.one.id)
        kept = await self.memory.keep_thread(
            self.g, self.one.id, w.name, w.kind, w.state, aim=w.aim, parts=list(w.parts),
            source=DM, suggestion_id=w.id,
        )  # fmt: skip
        (thread,) = await self.memory.threads(self.g, self.one.id)
        self.assertIsNotNone(kept.batch)
        got = await self.origin(self.session, self.one.id, thread.thread.id)
        self.assertEqual(got["thread"]["lines"], ["We'll find him."])  # (99 never agreed)
        self.assertEqual(got["thread"]["session"], 1)

    async def test_a_speaker_who_agreed_to_older_terms_is_not_read(self) -> None:
        await agree(self.db, self.g, DM_ID, ALICE)
        async with self.db.guild(self.g) as conn:  # (their consent is for an older wording)
            await conn.execute(
                "UPDATE consent SET terms_version = %s WHERE guild_id = %s AND user_id = %s",
                (TERMS_VERSION - 1, self.g, ALICE),
            )
        sid = await TranscriptStore(self.db).open_session(self.g, self.one.id, START)
        await TranscriptStore(self.db).add_lines(
            self.g, sid, [Heard(LINE_MS, ALICE, "x", "We'll find him.")]
        )
        store = ThreadSuggestionStore(self.db)
        job = tr.Suggestion(
            "quest", "Find Gnashwater", "find him", "",
            ((tr.TARGET, self.mira),), tr.STATE["quest"],
            line_starts=(LINE_MS,), speaker_ids=(ALICE,),
        )  # fmt: skip
        await store.offer(
            self.g, self.one.id, [job], "the session", 5, session_id=sid, session_at=START
        )
        (w,) = await store.waiting(self.g, self.one.id)
        await self.memory.keep_thread(
            self.g, self.one.id, w.name, w.kind, w.state, aim=w.aim, parts=list(w.parts),
            source=DM, suggestion_id=w.id,
        )  # fmt: skip
        (thread,) = await self.memory.threads(self.g, self.one.id)
        got = await self.origin(self.session, self.one.id, thread.thread.id)
        self.assertEqual(got["thread"]["lines"], [])
        self.assertTrue(got["thread"]["removed"])
