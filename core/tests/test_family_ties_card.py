"""👪 Family ties (#3463): the Discord card for a fact kept because a name could be two entries.
Real Postgres, fake Discord; invented names. The calls are the ones the website queues."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from dmbot.memory.claims import ClaimStore
from dmbot.memory.models import DM as DM_SOURCE
from dmbot.ui import family_ties as ft
from tests.test_memory_claims import said
from tests.test_recap_web import Base
from tests.test_web_api import THURSDAY


class TheCard(Base):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        from dmbot.memory.store import MemoryStore

        self.memory = MemoryStore(self.db)
        self.claims = ClaimStore(self.memory)
        self.g, self.c = THURSDAY.id, self.one.id

    async def kept(self) -> str:
        for name, type_, note in (
            ("Tavin", "player_character", ""),
            ("Mira", "character", "the ferrywoman"),
            ("Mira", "character", "a smuggler's daughter"),
        ):
            await self.memory.add_entity(
                self.g,
                self.c,
                type=type_,
                name=name,
                description=note,
                source=DM_SOURCE,
                status="confirmed",
            )
        await self.claims.propose(
            self.g, self.c, [said("Tavin", "is the sister of", "Mira")], {"1": 7},
            opted_in={7}, prompt_version="p1", model="m1", session_started_at=500,
        )  # fmt: skip
        async with self.db.guild(self.g) as conn:
            cur = await conn.execute("SELECT id FROM memory_claims WHERE reason <> ''")
            (row,) = await cur.fetchall()
        return str(row["id"])

    def interaction(self) -> Any:
        bot = MagicMock()
        bot.consent.consenting = AsyncMock(return_value={7})
        campaign = SimpleNamespace(
            id=self.c, guild_id=self.g, name="One", dm_user_ids=frozenset({7})
        )
        it = MagicMock(client=bot, user=MagicMock(id=7))
        it.response.defer = AsyncMock()
        it.response.is_done = MagicMock(return_value=True)
        it.edit_original_response = AsyncMock()
        it.followup.send = AsyncMock()
        it.data = {}
        self._campaign = campaign
        return it

    def patches(self) -> Any:
        from contextlib import ExitStack

        stack = ExitStack()
        stack.enter_context(patch.object(ft, "_memory", lambda _it: self.memory))
        stack.enter_context(
            patch.object(ft, "_campaign_for", AsyncMock(return_value=self._campaign))
        )
        stack.enter_context(patch.object(ft, "_tell", AsyncMock()))
        return stack

    async def test_the_card_shows_the_question_in_the_websites_words_with_a_menu(self) -> None:
        await self.kept()
        it = self.interaction()
        view = ft.FamilyTies(self.c, self.g)
        with self.patches():
            text, _ = (await view._load(it)) or ("", [])
        for words in (
            "Which one?",
            ft.WHY,
            ft.PICK_HELP,
            ft.NEITHER,
        ):
            self.assertIn(words, text)
        menus = [i for i in view.children if isinstance(i, discord.ui.Select)]
        (menu,) = menus
        self.assertEqual(menu.placeholder, "Which Mira does this mean?")  # the question
        self.assertEqual(
            sorted(o.description for o in menu.options),
            ["a smuggler's daughter", "the ferrywoman"],
        )
        self.assertEqual(
            [i.label for i in view.children if isinstance(i, discord.ui.Button)], [ft.NEITHER]
        )

    async def test_a_pick_records_the_choice_and_offers_undo_then_undo_puts_it_back(self) -> None:
        claim = await self.kept()
        it = self.interaction()
        view = ft.FamilyTies(self.c, self.g)
        with self.patches():
            await view._load(it)
            (choice,) = await self.claims.which_one(self.g, self.c, claim)
            entry = choice.entries[0].id
            await view._decide(it, claim, "pick", side=choice.side, entry=entry)
            picked = await self.claims.get(self.g, self.c, claim)
            assert picked is not None
            self.assertEqual((picked.object_picked, picked.reason), (entry, ""))
            self.assertEqual(await self.claims.unplaced_count(self.g, self.c), 0)
            sent = it.edit_original_response.await_args.kwargs["content"]
            self.assertIn(ft.NOTHING_LEFT, sent)  # (nothing else is waiting)
            self.assertIn(ft.PICKED, sent)
            view.undo_of = claim
            await view._decide(it, claim, "undo")
        again = await self.claims.get(self.g, self.c, claim)
        assert again is not None
        self.assertEqual((again.object_picked, again.reason != ""), (None, True))
        self.assertIn(ft.UNDONE, it.edit_original_response.await_args.kwargs["content"])

    async def test_none_of_these_forgets_the_fact_for_good(self) -> None:
        claim = await self.kept()
        it = self.interaction()
        view = ft.FamilyTies(self.c, self.g)
        with self.patches():
            await view._load(it)
            await view._decide(it, claim, "neither")
        self.assertEqual(await self.claims.unplaced_count(self.g, self.c), 0)
        got = await self.claims.get(self.g, self.c, claim)
        assert got is not None
        self.assertEqual(got.status, "rejected")
        self.assertIn(ft.NEITHER_DONE, it.edit_original_response.await_args.kwargs["content"])

    async def test_a_pick_that_is_not_a_choice_says_it_is_gone_and_changes_nothing(self) -> None:
        claim = await self.kept()
        it = self.interaction()
        view = ft.FamilyTies(self.c, self.g)
        with self.patches():
            await view._load(it)
            await view._decide(it, claim, "pick", side="object", entry="f" * 32)
        self.assertEqual(await self.claims.unplaced_count(self.g, self.c), 1)
        self.assertIn(ft.GONE_NOW, it.edit_original_response.await_args.kwargs["content"])

    async def test_nothing_waiting_says_so(self) -> None:
        it = self.interaction()
        view = ft.FamilyTies(self.c, self.g)
        with self.patches():
            self.assertIsNone(await view._load(it))

    async def test_another_campaigns_kept_fact_is_never_shown(self) -> None:
        await self.kept()
        it = self.interaction()
        view = ft.FamilyTies(self.two.id, self.g)
        with self.patches():
            self.assertIsNone(await view._load(it))

    async def test_the_button_answers_first_and_refuses_a_stranger(self) -> None:
        it = self.interaction()
        order: list[str] = []
        it.response.defer = AsyncMock(side_effect=lambda **_: order.append("defer"))

        async def refused(*_a: object, **_k: object) -> None:
            order.append("check")

        with (
            patch.object(ft, "_memory", lambda _it: self.memory),
            patch.object(ft, "_campaign_for", refused),
            patch.object(ft, "_tell", AsyncMock()),
        ):
            await ft.start_family_ties(it, self.c)
        self.assertEqual(order, ["defer", "check"])

    async def test_a_server_manager_who_is_not_a_dm_sees_and_changes_nothing(self) -> None:
        claim = await self.kept()
        it = self.interaction()
        it.user.id = 99  # may run the names screens (the campaign check passes) but is no DM
        view = ft.FamilyTies(self.c, self.g)
        with self.patches():
            await ft.start_family_ties(it, self.c)
            await view._decide(it, claim, "neither")
        self.assertEqual(await self.claims.unplaced_count(self.g, self.c), 1)
        it.followup.send.assert_not_called()

    async def test_a_kept_fact_naming_a_proposed_entry_is_still_on_the_card_with_the_count(
        self,
    ) -> None:
        claim = await self.kept()
        async with self.db.guild(self.g) as conn:  # one Mira is not confirmed yet
            await conn.execute(
                "UPDATE memory_entities SET status = 'proposed' WHERE name = 'Mira'"
                " AND description LIKE 'a smuggler%'"
            )
        self.assertEqual(await self.claims.unplaced_count(self.g, self.c), 1)
        listed = await self.claims.unplaced(self.g, self.c, opted_in={7})
        self.assertEqual([c.id for c in listed], [claim])
        self.assertEqual(await self.claims.unplaced(self.g, self.c, opted_in=set()), [])

    async def test_a_name_with_one_choice_is_no_question_and_makes_no_menu(self) -> None:
        await self.kept()
        async with self.db.guild(self.g) as conn:
            await conn.execute(
                "UPDATE memory_entities SET status = 'rejected'"
                " WHERE description LIKE 'a smuggler%'"
            )
        view = ft.FamilyTies(self.c, self.g)
        it = self.interaction()
        with self.patches():
            self.assertIsNone(await view._load(it))
