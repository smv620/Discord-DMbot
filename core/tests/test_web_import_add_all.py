"""The website's "Add all" for the names an import found (#5276, the import side of #3207): each
session's scan keeps the ids it proposed by kind, the import's progress read says how many of each
still wait (a DM's alone), the DM queues `confirm-kinds` with the import's id, the bot adds them
with one Undo per kind, and nothing of another import or campaign is touched. Invented names."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any
from unittest import mock

from dmbot import bot as botmod
from dmbot.bot import DMBot
from dmbot.memory import web_actions as wa
from dmbot.memory.models import PROPOSED
from dmbot.transcript.import_job import ImportStore
from dmbot.web import all_names
from dmbot.web.recaps import RecapError
from tests.test_web_relationships import Web

IMPORT = "a" * 32
OTHER = "b" * 32


class Importing(Web):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.store = ImportStore(self.db)
        self.actions = wa.ActionStore(self.db)
        self.ids: dict[str, list[str]] = {"place": [], "npc": []}
        for kind, names in {"place": ["Rivermouth", "The Grey Pier"], "npc": ["Old Hob"]}.items():
            for name in names:
                made = await self.memory.add_entity(
                    self.g, self.one.id, type="place" if kind == "place" else "character",
                    name=name, source="scan", role="npc" if kind == "npc" else None,
                )  # fmt: skip
                self.ids[kind].append(made.value.id)
        self.first = await self.session_row("s1" * 4, IMPORT, 100)
        self.second = await self.session_row("s2" * 4, IMPORT, 200)
        await self.session_row("s3" * 4, OTHER, 300)

    async def session_row(self, sid: str, import_id: str, at: int) -> str:
        await self.sql(
            "INSERT INTO transcript_sessions (id, guild_id, campaign_id, started_at)"
            " VALUES (%s, %s, %s, %s)",
            sid, self.g, self.one.id, at,
        )  # fmt: skip
        await self.sql(
            "INSERT INTO session_imports (guild_id, campaign_id, session_id, import_id,"
            " file_name, imported_by, imported_at, state) VALUES (%s, %s, %s, %s, 'x.txt', %s, %s,"
            " 'done')",
            self.g, self.one.id, sid, import_id, self.session.user_id, at,
        )  # fmt: skip
        return sid

    async def waiting(self, import_id: str = IMPORT, who: Any = None) -> dict[str, int]:
        from dmbot.web import imports

        got = await imports.progress(
            self.web, who or self.session, self.cref, import_id, outside=None
        )
        return dict(got["waiting"])

    async def queue(self, body: dict[str, Any], who: Any = None) -> str:
        return await all_names.queue_confirm(self.web, who or self.session, self.cref, body)

    async def bot(self) -> list[wa.Outcome]:
        done = []
        while (action := await self.actions.claim_next(self.g)) is not None:
            outcome = await wa.apply(
                self.memory, self.g, {self.session.user_id}, action, actions=self.actions
            )
            await self.actions.finish(
                self.g, action, error=outcome.error, result_id=outcome.result_id,
                result_counts=outcome.counts, result_batches=outcome.batches,
            )  # fmt: skip
            done.append(outcome)
        return done


class KeepingWhatTheScanFound(Importing):
    async def test_the_ids_are_kept_by_kind_and_a_second_scan_replaces_them(self) -> None:
        await self.store.keep_found(self.g, self.first, self.ids)
        await self.store.keep_found(self.g, self.first, {"place": self.ids["place"][:1]})
        async with self.db.guild(self.g) as conn:
            cur = await conn.execute(
                "SELECT found FROM session_imports WHERE session_id = %s", (self.first,)
            )
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual(row["found"], {"place": self.ids["place"][:1]})


class TheCounts(Importing):
    async def test_the_progress_read_says_how_many_of_each_kind_wait_across_the_sessions(
        self,
    ) -> None:
        await self.store.keep_found(self.g, self.first, {"place": self.ids["place"]})
        await self.store.keep_found(self.g, self.second, {"npc": self.ids["npc"]})
        self.assertEqual(await self.waiting(), {"place": 2, "npc": 1})

    async def test_a_name_answered_since_is_not_counted_and_ideas_never_are(self) -> None:
        await self.store.keep_found(
            self.g, self.first, {"place": self.ids["place"], "concept": self.ids["npc"]}
        )
        await self.memory.confirm_kinds(
            self.g, self.one.id, self.ids["place"][:1], "place", source="dm"
        )
        self.assertEqual(await self.waiting(), {"place": 1})

    async def test_another_imports_names_are_not_counted(self) -> None:
        await self.store.keep_found(self.g, self.first, {"place": self.ids["place"]})
        self.assertEqual(await self.waiting(OTHER), {})

    async def test_a_player_never_gets_the_counts(self) -> None:
        await self.store.keep_found(self.g, self.first, {"place": self.ids["place"]})
        with self.assertRaises(RecapError):
            await self.waiting(who=self.player)


class AddingThem(Importing):
    async def test_add_all_for_an_import_adds_its_names_with_one_undo_per_kind(self) -> None:
        await self.store.keep_found(self.g, self.first, {"place": self.ids["place"]})
        await self.store.keep_found(self.g, self.second, {"npc": self.ids["npc"]})
        await self.queue({"action": "confirm-kinds", "import": IMPORT, "kinds": "all"})
        (outcome,) = await self.bot()
        self.assertFalse(outcome.error)
        self.assertEqual(len(outcome.batches), 2)
        self.assertEqual(await self.waiting(), {})
        for entity_id in self.ids["place"]:
            got = await self.memory.entity(self.g, self.one.id, entity_id)
            assert got is not None
            self.assertNotEqual(got.status, PROPOSED)
        answered = (
            await self.sql_rows("SELECT id FROM name_actions WHERE action = 'confirm-kinds'")
        )[0]["id"]
        await self.queue({"action": "confirm-undo", "answer": answered})
        (undone,) = await self.bot()
        self.assertFalse(undone.error)
        self.assertEqual(await self.waiting(), {"place": 2, "npc": 1})

    async def test_one_kind_only_leaves_the_others_waiting(self) -> None:
        await self.store.keep_found(self.g, self.first, {"place": self.ids["place"]})
        await self.store.keep_found(self.g, self.second, {"npc": self.ids["npc"]})
        await self.queue({"action": "confirm-kinds", "import": IMPORT, "kinds": ["place"]})
        await self.bot()
        self.assertEqual(await self.waiting(), {"npc": 1})

    async def test_an_import_that_is_not_here_or_not_finished_is_not_found(self) -> None:
        body = {"action": "confirm-kinds", "kinds": "all"}
        with self.assertRaises(RecapError) as missing:
            await self.queue({**body, "import": "c" * 32})
        self.assertEqual(missing.exception.code, "not_found")
        await self.sql("UPDATE session_imports SET state = 'working' WHERE import_id = %s", IMPORT)
        with self.assertRaises(RecapError) as working:
            await self.queue({**body, "import": IMPORT})
        self.assertEqual(working.exception.code, "not_found")

    async def test_a_player_cannot_queue_it(self) -> None:
        with self.assertRaises(RecapError):
            await self.queue(
                {"action": "confirm-kinds", "import": IMPORT, "kinds": "all"}, who=self.player
            )

    async def sql_rows(self, text: str) -> list[dict[str, Any]]:
        async with self.db.guild(self.g) as conn:
            cur = await conn.execute(text)
            return [dict(r) for r in await cur.fetchall()]


class TheBotsWrite(unittest.IsolatedAsyncioTestCase):
    def me(self, entities: list[Any]) -> Any:
        me: Any = SimpleNamespace(
            memory=SimpleNamespace(entities=mock.AsyncMock(return_value=entities)),
            campaigns=SimpleNamespace(get=mock.AsyncMock(return_value=SimpleNamespace(id="c"))),
        )
        return me

    def ready(self) -> Any:
        return SimpleNamespace(campaign_id="c" * 32, session_id="s" * 8)

    async def test_only_the_names_this_scan_proposed_are_grouped_and_kept_as_ids(self) -> None:
        mine = SimpleNamespace(id="a" * 32, name="Rivermouth")
        older = SimpleNamespace(id="b" * 32, name="Someone Older")
        me = self.me([mine, older])
        store = SimpleNamespace(keep_found=mock.AsyncMock())
        grouped = mock.AsyncMock(return_value=({"place": ["a" * 32]}, 0))
        with mock.patch.object(botmod, "add_all_groups", grouped):
            await DMBot._keep_import_found(me, 1, self.ready(), ["Rivermouth"], store)
        self.assertEqual(grouped.call_args.args[2], [mine])  # (not the older waiting name)
        store.keep_found.assert_awaited_once_with(1, "s" * 8, {"place": ["a" * 32]})

    async def test_nothing_proposed_keeps_nothing_and_a_failure_never_stops_the_scan(self) -> None:
        store = SimpleNamespace(keep_found=mock.AsyncMock(side_effect=RuntimeError("down")))
        await DMBot._keep_import_found(self.me([]), 1, self.ready(), [], store)
        store.keep_found.assert_not_awaited()
        me = self.me([SimpleNamespace(id="a" * 32, name="Rivermouth")])
        with (
            mock.patch.object(botmod, "add_all_groups", mock.AsyncMock(return_value=({}, 0))),
            self.assertLogs("dmbot.bot", level="WARNING") as logs,
        ):
            await DMBot._keep_import_found(me, 1, self.ready(), ["Rivermouth"], store)  # (no raise)
        self.assertNotIn("down", " ".join(logs.output))
