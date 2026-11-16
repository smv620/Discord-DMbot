"""Public never becomes secret by itself (#4524, the owner's rule; Supervisor's ruling): without the
DM's own choice, DMbot never takes knowledge away from the players.

Each path the design note listed meets a public item: it stays public, and a 🤫 question is asked
once instead. The DM's own buttons (`dm_chose_secret=True`) still hide at once; Make it secret is
one ↩ Undo; Keep it known is remembered; a merge with a hidden entry stays public; a backstory
connection never ends a public one by itself; a question quoting someone who stops recording goes.
And the review's eight (#4524): a merge never unhides what only some player characters know; a
public name folded into a secret twin stays public; Make it secret takes off only whom it asked
about and keeps the DM's own rules; an Undo takes back a write's questions; a replace question waits
for the new connection; a guess is marked across a clock tick; a question cites its upload.
Postgres. Invented names."""

from __future__ import annotations

import ast
import pathlib
import unittest
from typing import Any

from dmbot.memory import backstory_keep, knowers, lore_knowledge, secret_questions
from dmbot.memory._changes import KNOWERS
from dmbot.memory.gate import ALIAS, Viewer
from dmbot.memory.models import CONFIRMED, DM, REJECTED, MemoryRuleError
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
        self.assertIsNone(written.batch)  # (nothing else changed: the write logged nothing)
        (q,) = await self.waiting()
        self.assertEqual((q.kind, q.item_id, q.change), ("narrow", self.tie.id, {"secret": True}))
        mine = await self.memory.update_relation(
            GUILD_A, self.c, self.tie.id, secret=True, source=DM, dm_chose_secret=True
        )
        self.assertTrue(mine.value.secret)  # (the DM's own 🤫 hides at once)

    async def test_an_undo_takes_back_the_questions_its_write_asked(self) -> None:
        other = (await self.relate(self.hero, "ally_of", self.bob, status=CONFIRMED)).value[0]
        written = await self.memory.update_relation(
            GUILD_A, self.c, other.id, secret=True, to_session_at=self.now, source=DM
        )  # (the end goes through, the secret is held: one batch with one question)
        assert written.batch is not None
        self.assertEqual(
            await self.memory.secret_questions_asked_in(GUILD_A, self.c, [written.batch]), 1
        )
        await self.memory.undo(GUILD_A, self.c, written.batch, source=DM)
        self.assertEqual(await self.waiting(), [])
        again = await self.memory.update_relation(
            GUILD_A, self.c, other.id, secret=True, to_session_at=self.now, source=DM
        )
        (q,) = await self.waiting()  # (asked again: the undone one is gone, not remembered)
        await self.memory.answer_secret_question(GUILD_A, self.c, q.id, False, source=DM)
        assert again.batch is not None
        await self.memory.undo(GUILD_A, self.c, again.batch, source=DM)
        await self.memory.update_relation(GUILD_A, self.c, other.id, secret=True, source=DM)
        self.assertEqual(await self.waiting(), [])  # (Keep it known outlives the Undo)

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
        held: list[str] = []
        await backstory_keep.end_replaced(
            self.memory, GUILD_A, self.c, [self.tie], 100, ended, held=held, hidden=True
        )
        tie = await self.memory.relation(GUILD_A, self.c, self.tie.id)
        assert tie is not None
        self.assertIsNone(tie.to_session_at)
        self.assertEqual((held, await self.waiting()), ([self.tie.id], []))  # (not asked yet)
        new = (
            await self.relate(self.bob, "enemy_of", self.hero, status=CONFIRMED, secret=True)
        ).value[0]
        await backstory_keep.ask_held(
            self.memory, GUILD_A, self.c, held, by=new.id, start=100, label="Ilsa's backstory",
            batch=None,
        )  # fmt: skip
        (q,) = await self.waiting()
        self.assertEqual(
            (q.kind, q.change, q.label),
            ("replace", {"end": True, "by": new.id, "at": 100}, "Ilsa's backstory"),
        )
        await self.memory.answer_secret_question(GUILD_A, self.c, q.id, True, source=DM)
        tie = await self.memory.relation(GUILD_A, self.c, self.tie.id)
        assert tie is not None
        self.assertEqual(tie.to_session_at, 100)  # (End the old one)
        await backstory_keep.end_replaced(
            self.memory, GUILD_A, self.c, [tie], 200, ended, held=held, hidden=True,
            dm_chose_secret=True,
        )  # fmt: skip
        self.assertEqual(await self.waiting(), [])  # (the DM's own hide: no question)

    async def test_an_undo_of_the_keep_takes_its_replace_question_back(self) -> None:
        kept = await self.relate(self.bob, "enemy_of", self.hero, status=CONFIRMED, secret=True)
        new = kept.value[0]
        await backstory_keep.ask_held(
            self.memory, GUILD_A, self.c, [self.tie.id], by=new.id, start=100, label="",
            batch=kept.batch,
        )  # fmt: skip
        self.assertEqual(len(await self.waiting()), 1)
        assert kept.batch is not None
        await self.memory.undo(GUILD_A, self.c, kept.batch, source=DM)
        self.assertEqual(await self.waiting(), [])

    async def test_end_the_old_one_does_nothing_once_the_new_one_is_gone(self) -> None:
        new = (
            await self.relate(self.bob, "enemy_of", self.hero, status=CONFIRMED, secret=True)
        ).value[0]
        await self.memory.ask_replace(GUILD_A, self.c, self.tie.id, by=new.id, end_at=100, label="")
        await self.memory.update_relation(GUILD_A, self.c, new.id, status=REJECTED, source=DM)
        (q,) = await self.waiting()
        made = await self.memory.answer_secret_question(GUILD_A, self.c, q.id, True, source=DM)
        tie = await self.memory.relation(GUILD_A, self.c, self.tie.id)
        assert tie is not None
        self.assertEqual((made.batch, tie.to_session_at), (None, None))
        self.assertEqual(await self.waiting(), [])  # (answered: nothing left to end)

    async def test_make_it_secret_takes_off_only_whom_it_asked_about(self) -> None:
        held = (
            await self.relate(self.bob, "enemy_of", self.hero, status=CONFIRMED, secret=True)
        ).value[0]
        spy = await self.add("Vex", type="character", status=CONFIRMED)
        await self.memory.set_statement_knowers(
            GUILD_A, self.c, "connection", held.id, {self.hero: 1.0, spy: 0.6}, source=DM,
            dm_chose_secret=True,
        )  # fmt: skip
        self.now += 5
        await self.memory.set_statement_knowers(
            GUILD_A, self.c, "connection", held.id, {spy: 0.6}, source=DM, dm_chose_secret=False
        )
        (q,) = await self.waiting()
        await self.memory.answer_secret_question(GUILD_A, self.c, q.id, True, source=DM)
        knows = await self.memory.statement_knowers(GUILD_A, self.c, "connection", [held.id])
        self.assertEqual(knows.get(held.id), [spy])
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute(
                "SELECT confidence FROM memory_knowers WHERE relation_id = %s", (held.id,)
            )
            self.assertEqual([r["confidence"] for r in await cur.fetchall()], [0.6])

    async def test_make_it_secret_keeps_the_dms_own_rules(self) -> None:
        async with self.memory._write(GUILD_A, self.c, DM) as w:
            await secret_questions.ask(
                w, secret_questions.NARROW, "memory_entities", self.hero,
                {"hidden_from_players": True},
            )  # fmt: skip
        (q,) = await self.waiting()
        with self.assertRaises(MemoryRuleError):  # (a player's character is never hidden)
            await self.memory.answer_secret_question(GUILD_A, self.c, q.id, True, source=DM)
        self.assertEqual([x.id for x in await self.waiting()], [q.id])  # (still waiting)

    async def test_a_merge_never_unhides_what_only_some_know(self) -> None:
        shady = await self.add("Shady", status=CONFIRMED)
        other = await self.add("Other", status=CONFIRMED)
        for e in (shady, other):
            await self.memory.set_hidden_from_players(
                GUILD_A, self.c, e, True, source=DM, dm_chose_secret=True
            )
        await self.memory.set_statement_knowers(
            GUILD_A, self.c, "entry", other, [self.hero], source=DM, dm_chose_secret=True
        )
        kept = await self.memory.merge(GUILD_A, self.c, shady, other, source=DM, dm_said_same=True)
        self.assertTrue(kept.value.hidden_from_players)
        self.assertEqual(await self.waiting(), [])
        knows = await self.memory.statement_knowers(GUILD_A, self.c, "entry", [shady])
        self.assertEqual(knows.get(shady), [self.hero])  # (Ilsa still knows)

    async def disguise(self) -> tuple[str, str]:
        """Mask has the DM's secret name "Shade"; the players know Bob by "Shade" too, and Ilsa
        knows that name of Bob's. Returns (Mask, Bob's "Shade")."""
        mask = await self.add("Mask", status=CONFIRMED)
        await self.memory.add_alias(
            GUILD_A, self.c, mask, "Shade", kind="nickname", source=DM, status=CONFIRMED,
            secret=True, dm_chose_secret=True,
        )  # fmt: skip
        shade = await self.memory.add_alias(
            GUILD_A, self.c, self.bob, "Shade", kind="nickname", source=DM, status=CONFIRMED
        )
        await self.memory.set_statement_knowers(
            GUILD_A, self.c, "alias", shade.value.id, [self.hero], source=DM,
            dm_chose_secret=True,
        )  # fmt: skip
        return mask, shade.value.id

    async def twin(self, mask: str) -> Any:
        names = await self.memory.aliases(GUILD_A, self.c, entity_id=mask, include_secret=True)
        return next(a for a in names if a.text == "Shade")

    async def hero_knows(self, alias_id: str) -> bool:
        seen = await self.memory.visible_to(GUILD_A, self.c, Viewer.character(self.hero))
        return seen.knows(ALIAS, alias_id)

    async def test_a_merge_keeps_the_dms_secret_name_and_asks_once(self) -> None:
        mask, _ = await self.disguise()
        await self.memory.merge(GUILD_A, self.c, mask, self.bob, source=DM, dm_said_same=True)
        shade = await self.twin(mask)
        self.assertTrue(shade.secret)  # (the DM's own secret: showing it would spoil who Mask is)
        (q,) = await self.waiting()
        self.assertEqual((q.kind, q.item_id, q.change), ("reveal", shade.id, {"secret": False}))

    async def test_who_knew_the_name_on_the_merged_entry_does_not_see_it_while_it_waits(
        self,
    ) -> None:
        mask, bobs = await self.disguise()
        self.assertTrue(await self.hero_knows(bobs))
        await self.memory.merge(GUILD_A, self.c, mask, self.bob, source=DM, dm_said_same=True)
        self.assertFalse(await self.hero_knows((await self.twin(mask)).id))

    async def test_let_them_know_shows_it_and_wakes_the_rows_and_undo_reverses_it(self) -> None:
        mask, _ = await self.disguise()
        await self.memory.merge(GUILD_A, self.c, mask, self.bob, source=DM, dm_said_same=True)
        (q,) = await self.waiting()
        made = await self.memory.answer_secret_question(GUILD_A, self.c, q.id, False, source=DM)
        shade = await self.twin(mask)
        self.assertFalse(shade.secret)
        self.assertTrue(await self.hero_knows(shade.id))
        assert made.batch is not None
        await self.memory.undo(GUILD_A, self.c, made.batch, source=DM)
        shade = await self.twin(mask)
        self.assertTrue(shade.secret)
        self.assertFalse(await self.hero_knows(shade.id))
        self.assertEqual([x.id for x in await self.waiting()], [q.id])  # (waiting again)

    async def test_keep_it_secret_stays_secret_and_is_never_asked_again(self) -> None:
        mask, _ = await self.disguise()
        await self.memory.merge(GUILD_A, self.c, mask, self.bob, source=DM, dm_said_same=True)
        (q,) = await self.waiting()
        kept = await self.memory.answer_secret_question(GUILD_A, self.c, q.id, True, source=DM)
        self.assertIsNone(kept.batch)  # (nothing changed: the secret was already kept)
        self.assertTrue((await self.twin(mask)).secret)
        other = await self.add("Shady", status=CONFIRMED)
        await self.memory.add_alias(
            GUILD_A, self.c, other, "Shade", kind="nickname", source=DM, status=CONFIRMED
        )
        await self.memory.merge(GUILD_A, self.c, mask, other, source=DM, dm_said_same=True)
        self.assertEqual(await self.waiting(), [])
        self.assertTrue((await self.twin(mask)).secret)

    async def test_a_guess_is_marked_across_a_clock_tick(self) -> None:
        made = (await self.relate(self.bob, "enemy_of", self.hero, status=CONFIRMED)).value[0]
        start = self.now
        await self.memory.set_statement_knowers(
            GUILD_A, self.c, "connection", made.id, [self.hero], source=DM, dm_chose_secret=True
        )
        self.now += 2  # (the keep and the mark are two writes: the clock may move between)
        changed = await self.memory.mark_guess(
            GUILD_A, self.c, "connection", [made.id], lore_knowledge.GUESS_CERTAINTY, source=DM,
            made_since=start,
        )  # fmt: skip
        self.assertEqual(changed, 1)
        unchanged = await self.memory.mark_guess(
            GUILD_A, self.c, "connection", [made.id], 0.1, source=DM, made_since=self.now
        )
        self.assertEqual(unchanged, 0)  # (a row known before the keep stays as sure as it was)

    async def test_a_question_cites_the_upload_that_asked_it(self) -> None:
        with secret_questions.evidence("Ilsa's backstory, 24 Oct"):
            await self.memory.update_relation(GUILD_A, self.c, self.tie.id, secret=True, source=DM)
        (q,) = await self.waiting()
        self.assertEqual(q.label, "Ilsa's backstory, 24 Oct")

    async def test_a_question_quoting_someone_who_stops_recording_goes(self) -> None:
        async with self.memory._write(GUILD_A, self.c, DM) as w:
            w.evidence = {"label": "the session of 14 Nov", "speaker_ids": [77], "line_starts": [9]}
            await secret_questions.ask(
                w, secret_questions.NARROW, "memory_relations", self.tie.id, {"secret": True}
            )
        async with self.db.guild(GUILD_A) as conn:
            self.assertEqual(await secret_questions.forget(conn, GUILD_A, 77), 1)
        self.assertEqual(await self.waiting(), [])


class AFoldKeepsTheStrongerRow(MemoryTest):
    """#4524, Supervisor's review: a merge or a fold never takes knowledge away or lowers it. Of two
    rows for one character, the awake one beats a sleeping one, then the surer one."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.hero = await self.add(
            "Ilsa", type="character", role="player_character", played_by=PLAYER,
            status=CONFIRMED,
        )  # fmt: skip
        self.bob = await self.add("Bob", status=CONFIRMED)
        self.ann = await self.add("Ann", status=CONFIRMED)

    async def two(self, twin: float, folded: float, *, twin_asleep: bool = False) -> float:
        """Ilsa knows the twin and the folded copy at these certainties; fold; how sure she is."""
        made = [
            (await self.relate(self.bob, p, self.ann, status=CONFIRMED, secret=True)).value[0]
            for p in ("ally_of", "enemy_of")
        ]
        for rel, sure in zip(made, (twin, folded), strict=True):
            await self.memory.set_statement_knowers(
                GUILD_A, self.c, "connection", rel.id, {self.hero: sure}, source=DM,
                dm_chose_secret=True,
            )  # fmt: skip
        async with self.memory._write(GUILD_A, self.c, DM, dm_chose_secret=True) as w:
            if twin_asleep:
                await w.update_where(
                    KNOWERS, {"hidden_by": [self.bob]}, " AND relation_id = %s", [made[0].id]
                )
            await knowers.move_rows(w, "relation_id", made[1].id, made[0].id)
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute(
                "SELECT confidence, hidden_by FROM memory_knowers WHERE relation_id = %s",
                (made[0].id,),
            )
            (row,) = await cur.fetchall()
        self.assertEqual(row["hidden_by"], [])  # (the kept row is the awake one)
        return float(row["confidence"])

    async def test_the_surer_folded_row_is_kept(self) -> None:
        self.assertEqual(await self.two(0.6, 1.0), 1.0)

    async def test_the_surer_twin_row_is_kept(self) -> None:
        self.assertEqual(await self.two(1.0, 0.6), 1.0)

    async def test_an_awake_folded_row_beats_a_sleeping_twin(self) -> None:
        self.assertEqual(await self.two(1.0, 0.6, twin_asleep=True), 0.6)

    async def test_a_merge_keeps_the_surer_row_of_an_entry(self) -> None:
        for e, sure in ((self.bob, 0.6), (self.ann, 1.0)):
            await self.memory.set_statement_knowers(
                GUILD_A, self.c, "entry", e, {self.hero: sure}, source=DM, dm_chose_secret=True
            )
        await self.memory.merge(GUILD_A, self.c, self.bob, self.ann, source=DM, dm_said_same=True)
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute(
                "SELECT confidence FROM memory_knowers WHERE about_entity_id = %s", (self.bob,)
            )
            self.assertEqual([float(r["confidence"]) for r in await cur.fetchall()], [1.0])


class EveryCallerThatChoseSecret(unittest.TestCase):
    """Ruling A: every caller that may pass `dm_chose_secret` as anything but False is listed here,
    so a new one shows up in review. Each must be the DM's own 👁️ / 🤫 / Who knows button, the
    card's own hide, or Make it secret. Passing the caller's own argument on is not a choice."""

    def test_the_list_of_callers(self) -> None:
        found: set[str] = set()
        for path in (SRC / "dmbot").rglob("*.py"):
            for fn in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if not isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef):
                    continue
                for node in ast.walk(fn):
                    if isinstance(node, ast.Call) and any(
                        k.arg == "dm_chose_secret" and not _passed_on_or_false(k.value)
                        for k in node.keywords
                    ):
                        found.add(f"{path.relative_to(SRC).as_posix()}:{fn.name}")
        self.assertEqual(
            found,
            {
                "dmbot/memory/backstory_keep.py:keep_all",  # the card's Players: hide
                "dmbot/memory/link_edits.py:set_secret",  # 🤫 on a connection
                "dmbot/memory/link_edits.py:_set_flags",  # Who knows this connection
                "dmbot/memory/name_edits.py:_set_flags",  # Who knows this name
                # (another Who knows answer on a 🌍 item takes common knowledge back, #4537)
                "dmbot/memory/store.py:remove_common_knowledge",
                "dmbot/memory/store.py:_apply_secret",  # Make it secret / End the old one
                "dmbot/memory/web_actions.py:_do",  # the website's hide and secret buttons
                "dmbot/ui/clue_audience.py:_remove",  # a clue's audience: take one off
                "dmbot/ui/clue_audience.py:_switch",  # a clue's audience: only some know
                "dmbot/ui/name_background.py:_answer",  # the card's 🤫 or "not said yet"
                "dmbot/ui/name_card.py:_flip_hidden",  # 👁️ Hide from players
                "dmbot/ui/name_card.py:on_submit",  # a secret spelling the DM types
                "dmbot/ui/names.py:save_name",  # a secret spelling the DM types
            },
        )


def _passed_on_or_false(value: ast.expr) -> bool:
    """False, or the caller's own `dm_chose_secret` (or `w.dm_chose_secret`) passed on."""
    if isinstance(value, ast.Constant):
        return value.value is False
    if isinstance(value, ast.Name):
        return value.id == "dm_chose_secret"
    return isinstance(value, ast.Attribute) and value.attr == "dm_chose_secret"
