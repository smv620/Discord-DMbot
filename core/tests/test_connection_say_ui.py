"""✍️ Say a connection in your own words, in Discord (#2940 slice B): the DM only, ten reads an hour,
the working line, the plain-sentence confirm step, and nothing written before Use this. The database
and the AI are fakes: the reading and the writing are tested in test_connection_said."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import discord

from dmbot.ai import Reply
from dmbot.memory import connection_said as said
from dmbot.memory import link_edits
from dmbot.memory.models import CONFIRMED, Relation
from dmbot.memory.ontology import FALLBACK_PREDICATE
from dmbot.ui import connection_say as ui
from tests.test_house_rules_ui import DM, PLAYER, UITest

MARA, TAVIN, BRIN = "a" * 32, "b" * 32, "c" * 32


def entity(name: str, kind: str = "character") -> Any:
    return SimpleNamespace(name=name, type=kind)


def lookup() -> Any:
    entities = {MARA: entity("Mara"), TAVIN: entity("Tavin"), BRIN: entity("Brin")}
    entries = [
        SimpleNamespace(entity_id=i, text=e.name, secret=False, key=e.name.casefold())
        for i, e in entities.items()
    ]
    return SimpleNamespace(
        entities=entities,
        names=tuple(entries),
        by_key={e.key: (e,) for e in entries},
    )


def old_link(secret: bool = False) -> Relation:
    return Relation(
        id="r" * 32, subject_id=TAVIN, predicate=FALLBACK_PREDICATE, object_id=MARA, detail="owes",
        confidence=1.0, status=CONFIRMED, source="dm", mention_ids=(), from_session_at=None,
        to_session_at=None, from_game_time=None, to_game_time=None, secret=secret, created_at=1,
    )  # fmt: skip


def proposal(kind: str = FALLBACK_PREDICATE, words: str = "owes") -> said.Proposal:
    return said.Proposal(MARA, TAVIN, kind, words, "Mara owes Tavin the favour")


class Saying(UITest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.ai = SimpleNamespace(complete=AsyncMock())
        self.bot.connection_said_ai = self.ai
        self.bot.lookup = None  # (the names are not cached in this test)
        self.memory = SimpleNamespace(relation=AsyncMock(return_value=old_link()))
        self.read = AsyncMock(return_value=said.Said((proposal(),), said.Who(said.ONLY_DM)))
        self.apply = AsyncMock(return_value=said.Applied((7,), ("n" * 32,)))
        self.patches: list[Any] = [
            patch.object(ui, "_memory", lambda it: self.memory),
            patch.object(ui, "_names", AsyncMock(return_value=lookup())),
            patch.object(ui, "show_card", AsyncMock()),
            patch.object(said, "read", self.read),
            patch.object(said, "apply", self.apply),
            patch.object(said, "undo", AsyncMock(return_value=True)),
        ]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    async def say(self, user: int = DM, **kw: Any) -> Any:
        it = self.it(user, discord.InteractionType.modal_submit)
        await ui.read_and_ask(
            it, "c1", MARA, TAVIN, MARA, "Mara owes Tavin the favour. Only Tavin knows.", **kw
        )
        return it

    @staticmethod
    def confirm(it: Any) -> tuple[str, Any]:
        text, view = (it.edited or it.response.edited)[-1]
        return str(text), view

    async def test_the_confirm_step_says_each_connection_as_one_plain_sentence(self) -> None:
        it = await self.say()
        text, view = self.confirm(it)
        self.assertIn("DMbot read this as:", text)
        self.assertIn("Mara “owes” Tavin", text)
        self.assertIn("only you, the DM", text)
        self.assertEqual(
            [str(b.label) for b in view.children if isinstance(b, discord.ui.Button)],
            ["✅ Use this", "✍️ Write it again", "Cancel"],
        )
        self.apply.assert_not_awaited()  # nothing is written by reading

    async def test_the_working_line_is_shown_before_the_read(self) -> None:
        it = await self.say()
        self.assertEqual(it.edited[0][0], ui.WORKING)

    async def test_a_player_is_refused_and_nothing_is_read(self) -> None:
        it = await self.say(PLAYER)
        self.read.assert_not_awaited()
        self.assertIn(ui.ONLY_DMS, str(it.followup.send.await_args_list))

    async def test_the_eleventh_read_in_an_hour_waits(self) -> None:
        for _ in range(said.READS_PER_HOUR):
            await self.say()
        self.assertEqual(self.read.await_count, said.READS_PER_HOUR)
        late = await self.say()
        self.assertEqual(self.read.await_count, said.READS_PER_HOUR)  # no more AI
        self.assertIn("Try again in", str(late.followup.send.await_args_list))

    async def test_a_plan_without_the_ai_is_told_so(self) -> None:
        self.bot.plan_allows = AsyncMock(return_value=False)
        it = await self.say()
        self.read.assert_not_awaited()
        self.assertIn(ui.NOT_ON_PLAN, str(it.followup.send.await_args_list))

    async def test_use_this_writes_once_as_the_dm_and_offers_undo(self) -> None:
        it = await self.say(replacing_id="r" * 32)
        _, view = self.confirm(it)
        press = self.it()
        await self.press(view, "✅ Use this").callback(press)
        self.apply.assert_awaited_once()
        kw = self.apply.await_args.kwargs  # type: ignore[union-attr]
        self.assertEqual((kw["who"], kw["replacing"].id), (link_edits.ONLY_THE_DM, "r" * 32))
        text, done = self.confirm(press)
        self.assertIn("Corrected", text)
        self.assertIn("Undo", next(str(b.label) for b in done.children))
        again = self.it()
        await self.press(view, "✅ Use this").callback(again)  # a second press writes nothing more
        self.apply.assert_awaited_once()

    async def test_cancel_writes_nothing(self) -> None:
        it = await self.say()
        _, view = self.confirm(it)
        await self.press(view, "Cancel").callback(self.it())
        self.apply.assert_not_awaited()

    async def test_write_it_again_opens_the_form_with_the_words(self) -> None:
        it = await self.say()
        _, view = self.confirm(it)
        press = self.it()
        await self.press(view, "✍️ Write it again").callback(press)
        form = press.response.modal
        self.assertEqual(form.text.default, "Mara owes Tavin the favour. Only Tavin knows.")

    async def test_a_hidden_connection_corrected_with_no_word_on_who_stays_hidden(self) -> None:
        self.memory.relation = AsyncMock(return_value=old_link(secret=True))
        self.read.return_value = said.Said((proposal(),), said.Who())
        it = await self.say(replacing_id="r" * 32)
        _, view = self.confirm(it)
        await self.press(view, "✅ Use this").callback(self.it())
        self.assertIsNone(self.apply.await_args.kwargs["who"])  # type: ignore[union-attr]  # as it was before

    async def test_a_problem_is_said_plainly_with_a_way_to_write_again(self) -> None:
        self.read.return_value = said.Said(problem=said.UNREADABLE)
        it = await self.say()
        text, view = self.confirm(it)
        self.assertIn(said.UNREADABLE, text)
        self.assertEqual(
            [str(b.label) for b in view.children if isinstance(b, discord.ui.Button)],
            ["✍️ Write it again", "Cancel"],
        )

    async def test_a_name_that_is_not_a_character_is_left_out_and_said(self) -> None:
        self.read.return_value = said.Said(
            (proposal(),), said.Who(said.SOME, ("Tavin", "the cook"))
        )
        it = await self.say()
        text, _ = self.confirm(it)
        self.assertIn("only Tavin", text)
        self.assertIn("“the cook”", text)

    async def test_the_sentence_is_built_from_the_fields_and_escapes_the_words(self) -> None:
        self.assertEqual(ui.sentence("Mara", "Tavin", "serves", ""), "Mara works for Tavin")
        self.assertEqual(
            ui.sentence("Mara", "Tavin", FALLBACK_PREDICATE, "owes"), "Mara “owes” Tavin"
        )
        self.assertIn("\\*", ui.sentence("Mara", "Tavin", FALLBACK_PREDICATE, "*bold*"))

    def test_the_limit_counts_an_hour_per_campaign(self) -> None:
        now = [0.0]
        limit = said.ReadLimit(lambda: now[0], per_hour=2)
        self.assertEqual([limit.take(1, "a"), limit.take(1, "a")], [0, 0])
        self.assertGreater(limit.take(1, "a"), 0)
        self.assertEqual(limit.take(1, "b"), 0)  # another campaign has its own
        now[0] = 3601.0
        self.assertEqual(limit.take(1, "a"), 0)  # an hour later

    async def test_a_read_that_was_never_asked_is_not_counted(self) -> None:
        limit = said.ReadLimit(per_hour=1)
        self.assertEqual(limit.take(1, "a"), 0)
        limit.give_back(1, "a")
        self.assertEqual(limit.take(1, "a"), 0)

    def test_a_cut_reply_is_a_reply_object(self) -> None:
        self.assertTrue(Reply("x", cut=True).cut)

    async def test_the_form_opens_for_a_dm_only(self) -> None:
        with patch.object(ui, "_campaign_for", AsyncMock(return_value=self.campaigns["c1"])):
            dm = self.it()
            await ui.open_form(dm, "c1", MARA, TAVIN, MARA)
            self.assertIsInstance(dm.response.modal, ui.SayForm)
            player = self.it(PLAYER)
            await ui.open_form(player, "c1", MARA, TAVIN, MARA)
        self.assertIsNone(player.response.modal)
        self.assertIn(ui.ONLY_DMS, str(player.response.sent))

    async def test_picking_the_other_name_for_connect_in_words_opens_the_form(self) -> None:
        from dmbot.ui import name_card

        opened = AsyncMock()
        view: Any = SimpleNamespace(stop=lambda: None)
        with patch.object(ui, "open_form", opened):
            await name_card.choose_other(
                self.it(), view, TAVIN, "c1", MARA, name_card.IN_WORDS, secret=False
            )
        opened.assert_awaited_once()
        self.assertEqual(opened.await_args.args[2:], (MARA, TAVIN, MARA))  # type: ignore[union-attr]
