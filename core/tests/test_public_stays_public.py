"""Public never becomes secret by itself (#4524, the owner's rule; Supervisor's ruling): without the
DM's own choice, DMbot never takes knowledge away from the players.

Each path the design note listed meets a public item: it stays public, and a 🤫 question is asked
once instead. The DM's own buttons (`dm_chose_secret=True`) still hide at once; Make it secret is
one ↩ Undo; Keep it known is remembered; a merge with a hidden entry stays public; a backstory
connection never ends a public one by itself; a question quoting someone who stops recording goes.
Postgres. Invented names."""

from __future__ import annotations

import ast
import pathlib
import unittest

from dmbot.memory import backstory_keep, secret_questions
from dmbot.memory.models import CONFIRMED, DM
from tests.test_memory_store import GUILD_A, MemoryTest

PLAYER = 4242
SRC = pathlib.Path(__file__).resolve().parents[1] / "src"


class ThePublicStaysPublic(MemoryTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.hero = await self.add(
            "Ilsa", type="character", role="player_character", played_by=PLAYER,
            status=CONFIRMED,
        )  # fmt: skip
        self.bob = await self.add("Bob", status=CONFIRMED)
        self.tie = (await self.relate(self.bob, "ally_of", self.hero, status=CONFIRMED)).value[0]

    async def waiting(self) -> list[secret_questions.Question]:
        return await self.memory.secret_questions_waiting(GUILD_A, self.c)

    async def test_a_public_connection_is_not_made_secret_by_itself(self) -> None:
        written = await self.memory.update_relation(
            GUILD_A, self.c, self.tie.id, secret=True, source=DM
        )
        self.assertFalse(written.value.secret)
        (q,) = await self.waiting()
        self.assertEqual((q.kind, q.item_id, q.change), ("narrow", self.tie.id, {"secret": True}))
        asked = await self.memory.secret_questions_asked_in(GUILD_A, self.c, [written.batch])
        self.assertEqual(asked, 1)
        mine = await self.memory.update_relation(
            GUILD_A, self.c, self.tie.id, secret=True, source=DM, dm_chose_secret=True
        )
        self.assertTrue(mine.value.secret)  # (the DM's own 🤫 hides at once)

    async def test_a_public_name_is_not_made_secret_by_itself(self) -> None:
        name = await self.memory.add_alias(
            GUILD_A, self.c, self.bob, "Bobby", kind="nickname", source=DM, status=CONFIRMED
        )
        again = await self.memory.add_alias(
            GUILD_A, self.c, self.bob, "Bobby", kind="nickname", source=DM, secret=True
        )
        self.assertFalse(again.value.secret)
        self.assertEqual([q.item_id for q in await self.waiting()], [name.value.id])

    async def test_the_dms_own_hide_still_hides(self) -> None:
        hidden = await self.memory.set_hidden_from_players(
            GUILD_A, self.c, self.bob, True, source=DM, dm_chose_secret=True
        )
        self.assertTrue(hidden.value.hidden_from_players)
        self.assertEqual(await self.waiting(), [])

    async def test_a_merge_with_a_hidden_entry_stays_public_and_asks(self) -> None:
        shady = await self.add("Shady Bob", status=CONFIRMED)
        await self.memory.set_hidden_from_players(
            GUILD_A, self.c, shady, True, source=DM, dm_chose_secret=True
        )
        kept = await self.memory.merge(
            GUILD_A, self.c, shady, self.bob, source=DM, dm_said_same=True
        )
        self.assertFalse(kept.value.hidden_from_players)  # (the public one wins, ruling B)
        (q,) = await self.waiting()
        self.assertEqual((q.item_id, q.change), (shady, {"hidden_from_players": True}))

    async def test_a_player_character_who_knows_keeps_knowing(self) -> None:
        held = (
            await self.relate(self.bob, "enemy_of", self.hero, status=CONFIRMED, secret=True)
        ).value[0]
        await self.memory.set_statement_knowers(
            GUILD_A, self.c, "connection", held.id, [self.hero], source=DM, dm_chose_secret=True
        )
        self.now += 5  # (a later write: the row was known before it)
        await self.memory.set_statement_knowers(
            GUILD_A, self.c, "connection", held.id, [], source=DM, dm_chose_secret=False
        )
        knows = await self.memory.statement_knowers(GUILD_A, self.c, "connection", [held.id])
        self.assertEqual(knows.get(held.id), [self.hero])
        (q,) = await self.waiting()
        self.assertEqual(q.change, {"knowers_off": [self.hero]})
        await self.memory.set_statement_knowers(
            GUILD_A, self.c, "connection", held.id, [], source=DM, dm_chose_secret=True
        )
        knows = await self.memory.statement_knowers(GUILD_A, self.c, "connection", [held.id])
        self.assertFalse(knows.get(held.id))

    async def test_make_it_secret_is_one_undo_and_keep_it_known_is_remembered(self) -> None:
        await self.memory.update_relation(GUILD_A, self.c, self.tie.id, secret=True, source=DM)
        (q,) = await self.waiting()
        made = await self.memory.answer_secret_question(GUILD_A, self.c, q.id, True, source=DM)
        tie = await self.memory.relation(GUILD_A, self.c, self.tie.id)
        assert tie is not None and made.batch is not None
        self.assertTrue(tie.secret)
        await self.memory.undo(GUILD_A, self.c, made.batch, source=DM)
        tie = await self.memory.relation(GUILD_A, self.c, self.tie.id)
        assert tie is not None
        self.assertFalse(tie.secret)
        (q,) = await self.waiting()  # (it waits again)
        await self.memory.answer_secret_question(GUILD_A, self.c, q.id, False, source=DM)
        again = await self.memory.update_relation(
            GUILD_A, self.c, self.tie.id, secret=True, source=DM
        )
        self.assertFalse(again.value.secret)
        self.assertEqual(await self.waiting(), [])  # (Keep it known: never asked twice)
        asked = await self.memory.secret_questions_asked_in(GUILD_A, self.c, [again.batch])
        self.assertEqual(asked, 0)

    async def test_a_backstory_connection_never_ends_a_public_one_by_itself(self) -> None:
        ended: list[int | None] = []
        await backstory_keep.end_replaced(
            self.memory, GUILD_A, self.c, [self.tie], 100, ended, hidden=True
        )
        tie = await self.memory.relation(GUILD_A, self.c, self.tie.id)
        assert tie is not None
        self.assertIsNone(tie.to_session_at)
        (q,) = await self.waiting()
        self.assertEqual((q.kind, q.change), ("replace", {"end": True, "at": 100}))
        await self.memory.answer_secret_question(GUILD_A, self.c, q.id, True, source=DM)
        tie = await self.memory.relation(GUILD_A, self.c, self.tie.id)
        assert tie is not None
        self.assertEqual(tie.to_session_at, 100)  # (End the old one)
        await backstory_keep.end_replaced(
            self.memory, GUILD_A, self.c, [tie], 200, ended, hidden=True, dm_chose_secret=True
        )  # (the DM's own hide on the card ends it as before: no question)
        self.assertEqual(await self.waiting(), [])

    async def test_a_question_quoting_someone_who_stops_recording_goes(self) -> None:
        async with self.memory._write(GUILD_A, self.c, DM) as w:
            w.evidence = {"label": "the session of 14 Nov", "speaker_ids": [77], "line_starts": [9]}
            await secret_questions.ask(
                w, secret_questions.NARROW, "memory_relations", self.tie.id, {"secret": True}
            )
        async with self.db.guild(GUILD_A) as conn:
            self.assertEqual(await secret_questions.forget(conn, GUILD_A, 77), 1)
        self.assertEqual(await self.waiting(), [])


class EveryCallerThatChoseSecret(unittest.TestCase):
    """Ruling A: every caller passing `dm_chose_secret=True` is listed here, so a new one shows up
    in review. Each must be the DM's own 👁️ / 🤫 / Who knows button or Make it secret."""

    def test_the_list_of_callers(self) -> None:
        found: set[str] = set()
        for path in (SRC / "dmbot").rglob("*.py"):
            for fn in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if not isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef):
                    continue
                for node in ast.walk(fn):
                    if isinstance(node, ast.Call) and any(
                        k.arg == "dm_chose_secret"
                        and isinstance(k.value, ast.Constant)
                        and k.value.value is True
                        for k in node.keywords
                    ):
                        found.add(f"{path.relative_to(SRC).as_posix()}:{fn.name}")
        self.assertEqual(
            found,
            {
                "dmbot/memory/link_edits.py:set_secret",  # 🤫 on a connection
                "dmbot/memory/link_edits.py:set_who_knows",  # Who knows this connection
                "dmbot/memory/name_edits.py:set_who_knows",  # Who knows this name
                "dmbot/memory/web_actions.py:_do",  # the website's hide and secret buttons
                "dmbot/ui/clue_audience.py:_remove",  # a clue's audience: take one off
                "dmbot/ui/clue_audience.py:_switch",  # a clue's audience: only some know
                "dmbot/ui/name_card.py:_flip_hidden",  # 👁️ Hide from players
                "dmbot/ui/name_card.py:on_submit",  # a secret spelling the DM types
                "dmbot/ui/names.py:save_name",  # a secret spelling the DM types
            },
        )
