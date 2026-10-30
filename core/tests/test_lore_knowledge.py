"""What the player characters know of an uploaded lore text (#2959): the choice (world general
knowledge, spoilers, smart guess) decides it, statement by statement. The sorting is pure and the
AI is a stand-in; the rest runs against Postgres. Invented names and text."""

from __future__ import annotations

import json
import unittest
from typing import Any
from unittest.mock import AsyncMock

from dmbot.ai import Reply
from dmbot.memory import backstory_keep as keep
from dmbot.memory import lore_knowledge as lk
from dmbot.memory.backstory_links import BackstoryLinks, NewLink, NewWhich
from dmbot.memory.models import CONFIRMED, NewName
from dmbot.ui import name_background as ui_bg
from dmbot.ui.name_lists import Upload
from tests import lore_fake
from tests.test_background_connections import TEXT
from tests.test_memory_names import GUILD, NamesTest

LINES = [
    lk.line("name", "Mirelle", "Mirelle the Pale serves"),
    lk.line("name", "Vale Keep", "Vale Keep sits in the hills"),
    lk.line("connection", "Mirelle — works for — Order", "serves the Order of the Grey Hand"),
]


class Sorting(unittest.IsolatedAsyncioTestCase):
    def test_a_wrong_or_missing_word_is_the_old_behaviour(self) -> None:
        self.assertEqual(lk.normalize(None), lk.SPOILERS)
        self.assertEqual(lk.normalize("everything"), lk.SPOILERS)
        self.assertEqual(lk.normalize("world"), lk.WORLD)

    async def test_world_is_all_spoilers_none_and_neither_asks_the_ai(self) -> None:
        ai = AsyncMock()
        self.assertEqual(await lk.decide(lk.WORLD, LINES, ai), ({0, 1, 2}, False))
        self.assertEqual(await lk.decide(lk.SPOILERS, LINES, ai), (set(), False))
        ai.complete.assert_not_awaited()

    async def test_a_smart_guess_takes_the_numbers_the_ai_calls_world(self) -> None:
        ai = AsyncMock()
        ai.complete.return_value = Reply(json.dumps({"world": [1, 7]}), False)  # (7: not a line)
        self.assertEqual(await lk.decide(lk.SMART, LINES, ai), ({1}, True))
        sent = ai.complete.await_args.args[1]
        self.assertIn("1. name: Vale Keep", sent)

    async def test_a_smart_guess_that_fails_is_all_spoilers_never_all_world(self) -> None:
        for reply in (
            Reply("not json at all", False),
            Reply('{"world": [0', True),  # cut
            Reply("{}", False, refused=True),
        ):
            ai = AsyncMock()
            ai.complete.return_value = reply
            known, answered = await lk.decide(lk.SMART, LINES, ai)
            self.assertEqual((known, answered), (set(), False), reply)
        down = AsyncMock()
        down.complete.side_effect = RuntimeError("down")
        self.assertEqual(await lk.decide(lk.SMART, LINES, down), (set(), False))
        self.assertEqual(await lk.decide(lk.SMART, LINES, None), (set(), False))


class TheUpload(NamesTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        from unittest.mock import patch

        from tests.test_background_connections import reply

        self.bot.plan_gate = AsyncMock(return_value=None)  # type: ignore[method-assign]
        self.ai = lore_fake.lore_ai(reply())
        self.bot.background_ai = self.bot.background_read_ai = self.ai  # type: ignore[assignment]
        self.store = BackstoryLinks(self.db)
        self.bot.backstory_links = self.store
        made = await self.memory.add_entity(
            GUILD, self.campaign.id, name="Tavin", type="character", role="player_character",
            played_by=1001, status=CONFIRMED, source="dm",
        )  # fmt: skip
        self.tavin = made.value.id
        self.patch = patch  # (kept for the tests that need it)

    async def read(self, choice: str, ai: Any = None) -> Any:
        return await ui_bg.read_background(
            self.bot, self.campaign, Upload(TEXT, "your lore", True),
            truth=False, speaker=None, known_by="", started=0.0, choice=choice,
        )  # fmt: skip

    async def known(self, kind: str, ids: list[str]) -> dict[str, list[str]]:
        return await self.memory.statement_knowers(GUILD, self.campaign.id, kind, ids)

    async def proposed(self) -> dict[str, str]:
        every = await self.memory.entities(GUILD, self.campaign.id, statuses=["proposed"])
        return {e.name: e.id for e in every}

    async def test_spoilers_changes_nothing(self) -> None:
        await self.read(lk.SPOILERS)
        ids = await self.proposed()
        self.assertEqual(await self.known("entry", list(ids.values())), {})
        self.assertFalse(any(w.world for w in await self.store.waiting(GUILD, self.campaign.id)))

    async def test_world_general_knowledge_is_known_to_every_player_character(self) -> None:
        await self.read(lk.WORLD)
        ids = await self.proposed()
        got = await self.known("entry", list(ids.values()))
        self.assertEqual({k: v for k, v in got.items()}, {i: [self.tavin] for i in ids.values()})
        # an other name is public and known too (Mirelle the Pale is her other name)
        aliases = await self.memory.aliases(
            GUILD, self.campaign.id, entity_id=ids["Mirelle"], include_secret=True
        )
        self.assertTrue(aliases and not any(a.secret for a in aliases))
        said = await self.known("alias", [a.id for a in aliases])
        self.assertEqual(set(said), {a.id for a in aliases})
        waiting = await self.store.waiting(GUILD, self.campaign.id)
        self.assertTrue(waiting and all(w.world for w in waiting))

    async def test_a_kept_world_connection_is_known_and_a_secret_one_is_not(self) -> None:
        await self.read(lk.WORLD)
        made = await self.memory.entities(GUILD, self.campaign.id, statuses=["proposed"])
        for kind in {e.type for e in made}:
            await self.memory.confirm_kinds(
                GUILD, self.campaign.id, [e.id for e in made if e.type == kind], kind, source="dm"
            )
        ready = [w for w in await self.store.waiting(GUILD, self.campaign.id) if w.ready]
        self.assertGreaterEqual(len(ready), 2)
        first, second = ready[0], ready[1]
        await keep.keep_all(self.memory, self.store, GUILD, self.campaign.id, [first])
        mine = [
            r
            for r in await self.memory.relations(GUILD, self.campaign.id, include_secret=True)
            if (r.subject_id, r.object_id)
            in {(first.subject_id, first.object_id), (first.object_id, first.subject_id)}
        ]
        self.assertEqual(await self.known("connection", [mine[0].id]), {mine[0].id: [self.tavin]})
        secret = NewLink(
            second.subject_id, second.predicate, second.object_id, "q", True, world=True
        )
        await self.store.remove(GUILD, self.campaign.id, second)
        await self.store.add(GUILD, self.campaign.id, [secret])
        (again,) = [
            w
            for w in await self.store.waiting(GUILD, self.campaign.id)
            if w.subject_id == second.subject_id and w.predicate == second.predicate
        ]
        await keep.keep_all(self.memory, self.store, GUILD, self.campaign.id, [again])
        kept = [
            r
            for r in await self.memory.relations(GUILD, self.campaign.id, include_secret=True)
            if r.secret
        ]
        self.assertEqual(await self.known("connection", [k.id for k in kept]), {})

    async def test_a_smart_guess_sorts_statement_by_statement(self) -> None:
        # the stand-in AI calls only the Vale Keep name common knowledge
        async def answer(system: str, user: str, **kw: Any) -> Reply:
            numbers = [
                int(line.split(".")[0])
                for line in user.splitlines()
                if line[:1].isdigit() and "Vale Keep" in line and line.split(".")[0].isdigit()
            ]
            return Reply(json.dumps({"world": numbers}), False)

        original = self.ai.complete

        async def routed(system: str, user: str, **kw: Any) -> Reply:
            if system == lk.SYSTEM:
                return await answer(system, user, **kw)
            return await original(system, user, **kw)  # type: ignore[no-any-return]

        self.ai.complete = routed  # type: ignore[assignment]
        await self.read(lk.SMART)
        ids = await self.proposed()
        got = await self.known("entry", list(ids.values()))
        self.assertEqual(set(got), {ids["Vale Keep"]})  # the others are spoilers
        waiting = await self.store.waiting(GUILD, self.campaign.id)
        # (and of the connections, only the one that names Vale Keep: the rest stay spoilers)
        self.assertEqual({w.predicate for w in waiting if w.world}, {"located_in"})
        self.assertTrue(any(not w.world for w in waiting))

    async def test_a_which_question_and_an_undo_keep_the_choice(self) -> None:
        a = (
            await self.memory.add_names(
                GUILD,
                self.campaign.id,
                [NewName("Mara", "npc", CONFIRMED), NewName("Order", "faction", CONFIRMED)],
                source="dm",
            )
        ).value
        mara, order = a
        assert mara and order
        await self.store.add(
            GUILD, self.campaign.id, [NewLink(mara, "member_of", order, "q", world=True)]
        )
        (waiting,) = await self.store.waiting(GUILD, self.campaign.id)
        done = await keep.keep_all(self.memory, self.store, GUILD, self.campaign.id, [waiting])
        await keep.undo_all(self.memory, self.store, GUILD, self.campaign.id, done)
        (again,) = await self.store.waiting(GUILD, self.campaign.id)
        self.assertTrue(again.world)  # Undo puts it back as it was
        _ = NewWhich  # (the question rows carry the same column: see the store)
