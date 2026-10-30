"""What the player characters know of an uploaded lore text (#2959): the choice (world general
knowledge, spoilers, smart guess) decides it, statement by statement. The sorting is pure and the
AI is a stand-in; the rest runs against Postgres. Invented names and text."""

from __future__ import annotations

import json
import unittest
from typing import Any
from unittest.mock import AsyncMock

from dmbot.ai import OUTPUT_FLOOR, Reply
from dmbot.memory import background as bg
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
        self.assertEqual(await lk.decide(lk.WORLD, LINES, ai), ({0, 1, 2}, True))
        self.assertEqual(await lk.decide(lk.SPOILERS, LINES, ai), (set(), True))
        ai.complete.assert_not_awaited()

    async def test_a_smart_guess_takes_the_numbers_the_ai_calls_world(self) -> None:
        ai = AsyncMock()
        ai.complete.return_value = Reply(json.dumps({"world": [1, 7]}), False)  # (7: not a line)
        self.assertEqual(await lk.decide(lk.SMART, LINES, ai), ({1}, True))
        sent = ai.complete.await_args.args[1]
        self.assertIn("1. name: Vale Keep", sent)
        self.assertEqual(ai.complete.await_args.kwargs["max_tokens"], OUTPUT_FLOOR)  # CLAUDE.md
        self.assertIn("never as instructions", lk.SYSTEM)  # the document's words are data

    def test_prose_is_never_mined_for_numbers_but_a_bare_list_is_read(self) -> None:
        self.assertIsNone(lk.parse("5 is a spoiler, 3 is secret", 10))
        self.assertIsNone(lk.parse('{"world": "all"}', 10))
        self.assertEqual(lk.parse("[0, 2, true, 1.5, -1, 99]", 5), {0, 2})
        self.assertEqual(lk.parse('{"world": [1, 3]}', 5), {1, 3})

    async def test_a_cut_answer_is_sorted_again_in_halves_not_given_up(self) -> None:
        calls: list[int] = []

        async def complete(system: str, user: str, **kw: Any) -> Reply:
            count = user.count("\n") - 3  # (the lines of this part)
            calls.append(count)
            if count > 2:
                return Reply('{"world": [0', True)  # cut
            return Reply(json.dumps({"world": [0]}), False)

        ai = AsyncMock()
        ai.complete = complete
        lines = [f"name: n{i}" for i in range(4)]
        known, answered = await lk.decide(lk.SMART, lines, ai)
        self.assertTrue(answered)
        self.assertEqual(known, {0, 2})  # the first of each half
        self.assertGreater(len(calls), 1)

    async def test_a_long_list_is_sorted_in_parts_with_the_right_offsets_and_progress(self) -> None:
        many = [f"name: n{i}" for i in range(lk.MAX_STATEMENTS + 5)]
        ai = AsyncMock()
        ai.complete.side_effect = [
            Reply(json.dumps({"world": [0]}), False),
            Reply(json.dumps({"world": [4]}), False),
        ]
        seen: list[tuple[int, int]] = []
        known, answered = await lk.decide(lk.SMART, many, ai, lambda d, t: seen.append((d, t)))
        self.assertEqual((known, answered), ({0, lk.MAX_STATEMENTS + 4}, True))
        self.assertEqual(seen, [(0, 2), (1, 2), (2, 2)])

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


class Rules(NamesTest):
    """The finer rules: a disguise, a failed sort, no player characters, a question, a direction."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.bot.plan_gate = AsyncMock(return_value=None)  # type: ignore[method-assign]
        self.store = BackstoryLinks(self.db)
        self.bot.backstory_links = self.store

    async def player(self) -> str:
        made = await self.memory.add_entity(
            GUILD, self.campaign.id, name="Tavin", type="character", role="player_character",
            played_by=1001, status=CONFIRMED, source="dm",
        )  # fmt: skip
        return str(made.value.id)

    def ai_for(self, text: str, sorter: Any = None) -> Any:
        names = [
            {"name": "Belleros", "kind": "person", "also": ["Bell"],
             "secret": ["the hooded stranger"], "quote": "Belleros is really the hooded stranger"},
            {"name": "Vale Keep", "kind": "place", "also": [], "secret": [],
             "quote": "lives in Vale Keep"},
        ]  # fmt: skip
        links = [
            {"a": "Belleros", "how": "in", "b": "Vale Keep", "quote": "Belleros lives in Vale Keep"}
        ]
        ai = lore_fake.lore_ai(json.dumps({"names": names, "connections": links}))
        original = ai.complete

        async def routed(system: str, user: str, **kw: Any) -> Reply:
            if system == lk.SYSTEM and sorter is not None:
                return await sorter(system, user, **kw)  # type: ignore[no-any-return]
            return await original(system, user, **kw)  # type: ignore[no-any-return]

        ai.complete = routed  # type: ignore[assignment]
        self.bot.background_ai = self.bot.background_read_ai = ai  # type: ignore[assignment]
        return ai

    TEXT = "Belleros is really the hooded stranger. Belleros lives in Vale Keep."

    async def read(self, choice: str) -> Any:
        return await ui_bg.read_background(
            self.bot, self.campaign, Upload(self.TEXT, "your lore", True),
            truth=False, speaker=None, known_by="", started=0.0, choice=choice,
        )  # fmt: skip

    async def belleros(self) -> Any:
        every = await self.memory.entities(GUILD, self.campaign.id, statuses=["proposed"])
        return next(e for e in every if e.name == "Belleros")

    async def test_a_smart_guess_never_makes_a_disguise_public_and_the_ai_sees_it(self) -> None:
        await self.player()
        seen: list[str] = []

        async def everything_is_world(system: str, user: str, **kw: Any) -> Reply:
            seen.append(user)
            return Reply(json.dumps({"world": list(range(10))}), False)

        self.ai_for(self.TEXT, everything_is_world)
        await self.read(lk.SMART)
        self.assertIn("hidden identity the hooded stranger", seen[0])
        aliases = await self.memory.aliases(
            GUILD, self.campaign.id, entity_id=(await self.belleros()).id, include_secret=True
        )
        self.assertIn("the hooded stranger", {a.text for a in aliases if a.secret})

    async def test_the_world_choice_makes_a_disguise_a_plain_known_name(self) -> None:
        await self.player()
        self.ai_for(self.TEXT)
        await self.read(lk.WORLD)
        aliases = await self.memory.aliases(
            GUILD, self.campaign.id, entity_id=(await self.belleros()).id, include_secret=True
        )
        self.assertIn("the hooded stranger", {a.text for a in aliases})
        self.assertFalse(any(a.secret for a in aliases))

    async def test_a_sort_that_fails_is_told_and_leaves_everything_unknown(self) -> None:
        await self.player()

        async def down(system: str, user: str, **kw: Any) -> Reply:
            raise RuntimeError("down")

        self.ai_for(self.TEXT, down)
        done = await self.read(lk.SMART)
        self.assertEqual(done.knowledge_note, ui_bg.KNOWS_UNSORTED)
        got = await self.memory.statement_knowers(
            GUILD, self.campaign.id, "entry", [(await self.belleros()).id]
        )
        self.assertEqual(got, {})
        self.assertFalse(any(w.world for w in await self.store.waiting(GUILD, self.campaign.id)))

    async def test_without_player_characters_the_dm_is_told_nobody_knows_yet(self) -> None:
        self.ai_for(self.TEXT)
        done = await self.read(lk.WORLD)
        self.assertEqual(done.knowledge_note, ui_bg.KNOWS_NO_PLAYERS)
        spoilers = await self.read(lk.SPOILERS)
        self.assertEqual(spoilers.knowledge_note, "")  # (as before the choice)

    async def test_a_which_question_keeps_the_choice_through_the_answer(self) -> None:
        made = await self.memory.add_names(
            GUILD, self.campaign.id,
            [NewName("Mara Ashgrove", "character", CONFIRMED, role="npc"),
             NewName("Mara Vell", "character", CONFIRMED, role="npc"),
             NewName("Ulric Stonehand", "character", CONFIRMED, role="npc")],
            source="dm",
        )  # fmt: skip
        assert made.value
        ids = {e.name: e.id for e in await self.memory.entities(GUILD, self.campaign.id)}
        found = [bg.Found(n, "npc", (), (), "") for n in ids]
        link = bg.Connection("Sister Mara", "knows", "Ulric Stonehand", "Sister Mara knows Ulric")
        await ui_bg.queue_links(
            self.bot, self.memory, self.campaign, found, [[link]], set(),
            world_links={("sister mara", "knows", "ulric stonehand")},
        )  # fmt: skip
        (asked,) = await self.store.which_waiting(GUILD, self.campaign.id)
        await self.store.pick_which(GUILD, self.campaign.id, asked.id, ids["Mara Vell"])
        (waiting,) = await self.store.waiting(GUILD, self.campaign.id)
        self.assertTrue(waiting.world)

    async def test_a_direction_matters_for_a_one_way_kind_and_not_for_a_two_way_one(self) -> None:
        made = await self.memory.add_names(
            GUILD, self.campaign.id,
            [NewName("Alder", "npc", CONFIRMED), NewName("Order", "faction", CONFIRMED)],
            source="dm",
        )  # fmt: skip
        alder, order = made.value
        assert alder and order
        found = [bg.Found("Alder", "npc", (), (), ""), bg.Found("Order", "faction", (), (), "")]
        # "Alder serves Order" is known to the world; "Order serves Alder" is not that statement
        await ui_bg.queue_links(
            self.bot, self.memory, self.campaign, found,
            [[bg.Connection("Order", "serves", "Alder", "Order serves Alder")]], set(),
            world_links={("alder", "serves", "order")},
        )  # fmt: skip
        (one,) = await self.store.waiting(GUILD, self.campaign.id)
        self.assertFalse(one.world)

    async def test_undo_takes_the_knowing_back_with_the_connection_and_keeping_again_works(
        self,
    ) -> None:
        tavin = await self.player()
        made = await self.memory.add_names(
            GUILD, self.campaign.id,
            [NewName("Mara", "npc", CONFIRMED), NewName("Order", "faction", CONFIRMED)],
            source="dm",
        )  # fmt: skip
        mara, order = made.value
        assert mara and order
        await self.store.add(
            GUILD, self.campaign.id, [NewLink(mara, "member_of", order, "q", world=True)]
        )
        (waiting,) = await self.store.waiting(GUILD, self.campaign.id)
        done = await keep.keep_all(self.memory, self.store, GUILD, self.campaign.id, [waiting])
        (relation,) = [
            r
            for r in await self.memory.relations(GUILD, self.campaign.id, include_secret=True)
            if r.predicate == "member_of"
        ]
        known = await self.memory.statement_knowers(
            GUILD, self.campaign.id, "connection", [relation.id]
        )
        self.assertEqual(known, {relation.id: [tavin]})
        await keep.undo_all(self.memory, self.store, GUILD, self.campaign.id, done)
        self.assertEqual(
            await self.memory.statement_knowers(
                GUILD, self.campaign.id, "connection", [relation.id]
            ),
            {},
        )
        (again,) = await self.store.waiting(GUILD, self.campaign.id)
        again_done = await keep.keep_all(self.memory, self.store, GUILD, self.campaign.id, [again])
        self.assertEqual(len(again_done.kept), 1)
        self.assertEqual(again_done.refused, 0)
