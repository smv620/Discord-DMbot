"""🔗 Look-alikes and 🔗 Merge several… (#2426): the group message, the one confirmation that names
every name, Discord's limits (25 options, 5 rows, 25-letter labels), and who may press what."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from dmbot.memory.lookalikes import Name
from dmbot.memory.models import DM, MemoryRuleError
from dmbot.ui import look_alikes as la
from dmbot.ui import name_dropdown as dd
from dmbot.ui.name_card import SAME
from dmbot.ui.names import NamesHome, Reviews

A, B, C = ("a" * 32, "b" * 32, "c" * 32)
KINDS = {A: "NPC", B: "NPC", C: "place"}
NAMES = [Name(A, "Belleros", 9), Name(B, "Beleros", 2), Name(C, "the Belleros", 0)]


def labels(view: discord.ui.View) -> list[str]:
    return [str(c.label) for c in view.children if isinstance(c, discord.ui.Button)]


def selects(view: discord.ui.View) -> list[discord.ui.Select[Any]]:
    return [c for c in view.children if isinstance(c, discord.ui.Select)]


class TheGroupMessage(unittest.TestCase):
    def test_every_name_is_listed_with_its_kind_and_how_often_it_was_heard(self) -> None:
        text = la.group_text(NAMES, KINDS, number=2, of=5)
        self.assertIn("DMbot thinks these may be one name", text)
        self.assertIn("(2 of 5)", text)
        self.assertIn("**Belleros** (NPC, heard 9 times)", text)
        self.assertIn("**the Belleros** (place)", text)
        self.assertIn("I'd keep **Belleros**, the one heard most", text)

    def test_a_group_of_a_kind_of_one_needs_no_number(self) -> None:
        self.assertNotIn(" of ", la.group_text(NAMES, KINDS, number=1, of=1).splitlines()[0])

    def test_names_past_the_menu_are_counted_not_dropped_silently(self) -> None:
        self.assertIn("…and 4 more not shown", la.group_text(NAMES, KINDS, number=1, of=1, extra=4))

    def test_the_confirmation_names_all_of_them_and_says_it_can_be_undone(self) -> None:
        text = la.confirm_text(NAMES[0], NAMES[1:], KINDS)
        for name in ("Belleros", "Beleros", "the Belleros"):
            self.assertIn(f"**{name}**", text)
        self.assertIn("you can undo it", text)
        self.assertIn("Nothing is deleted", text)

    def test_different_kinds_are_said_once_in_that_same_confirmation(self) -> None:
        text = la.confirm_text(NAMES[0], NAMES[1:], KINDS)
        self.assertIn("**the Belleros** is a place; it becomes an NPC's other name.", text)
        same = la.confirm_text(NAMES[0], NAMES[1:2], KINDS)
        self.assertNotIn("same kind", same)

    def test_a_name_with_markdown_in_it_cannot_change_the_message(self) -> None:
        text = la.confirm_text(Name("a", "**Bel**", 0), [Name("b", "_x_", 0)], {})
        self.assertIn("\\*\\*Bel\\*\\*", text)

    def test_the_button_says_how_many_wait(self) -> None:
        self.assertEqual(la.label(0), "🔗 Look-alikes")
        self.assertEqual(la.label(3), "🔗 Look-alikes (3)")
        self.assertEqual(la.label(250), "🔗 Look-alikes (99+)")


class TheGroupView(unittest.TestCase):
    def view(self, count: int = 3, groups: int = 1) -> la.LookAlikeGroup:
        names = [Name(f"{i:032x}", f"Name {i:03}", count - i) for i in range(count)]
        return la.LookAlikeGroup("c1", [names] * groups, 0, {})

    def test_every_name_is_ticked_and_the_keep_menu_has_the_same_names_most_heard_first(
        self,
    ) -> None:
        view = self.view()
        ticked, keep = selects(view)
        self.assertTrue(all(o.default for o in ticked.options))
        self.assertEqual([o.default for o in keep.options], [True, False, False])  # most heard
        self.assertEqual([o.label for o in keep.options], ["Name 000", "Name 001", "Name 002"])
        self.assertEqual(keep.placeholder, "Keep which name? ▾")

    def test_a_menu_holds_25_so_a_big_group_shows_the_first_25(self) -> None:
        view = self.view(40)
        self.assertEqual({len(s.options) for s in selects(view)}, {25})
        self.assertEqual(selects(view)[0].max_values, 25)

    def test_the_rows_fit_and_the_labels_fit_a_phone(self) -> None:
        view = self.view(30, groups=3)
        self.assertEqual(len(view.children), 6)  # two menus, four buttons
        rows = {c.row for c in view.children if c.row is not None}
        self.assertLessEqual(max(rows), 4)
        self.assertTrue(all(len(x) <= 25 for x in labels(view)), labels(view))

    def test_next_group_shows_only_when_there_is_another(self) -> None:
        self.assertNotIn("Next group ▸", labels(self.view(3, groups=1)))
        self.assertIn("Next group ▸", labels(self.view(3, groups=2)))


class TheCardsOtherRoute(unittest.TestCase):
    def test_merge_several_sits_under_same_as_not_under_connect(self) -> None:
        same = dd.NameDropdown(
            "c1", "me", SAME, "Jack", cast(Any, SimpleNamespace(entities={})), [],
            secret=False, mode=dd.CLOSEST, page=0, last=0,
        )  # fmt: skip
        connect = dd.NameDropdown(
            "c1", "me", "member_of", "Jack", cast(Any, SimpleNamespace(entities={})), [],
            secret=False, mode=dd.CLOSEST, page=0, last=0,
        )  # fmt: skip
        self.assertIn(la.MERGE_SEVERAL, labels(same))
        self.assertNotIn(la.MERGE_SEVERAL, labels(connect))

    def test_the_list_holds_25_and_has_a_search(self) -> None:
        options = [(f"{i:032x}", f"Name {i:03}") for i in range(25)]
        view = la.MergeSeveral("c1", "me", "Jack", options)
        self.assertEqual(len(selects(view)[0].options), 25)
        self.assertEqual(selects(view)[0].max_values, 25)
        self.assertIn("🔍 Search", labels(view))
        self.assertIn("🔗 Merge", labels(view))

    def test_with_no_names_there_is_nothing_to_merge(self) -> None:
        self.assertNotIn("🔗 Merge", labels(la.MergeSeveral("c1", "me", "Jack", [])))


class TheLimits(unittest.TestCase):
    def test_a_group_message_stays_under_discords_2000_letters(self) -> None:
        names = [Name(f"{i:032x}", f"{'Long' * 25} {i}", 99) for i in range(25)]
        kinds = {n.id: "NPC" for n in names}
        text = la.group_text(names, kinds, number=3, of=9, extra=7)
        self.assertLessEqual(len(text), 2000)
        self.assertIn("not shown", text)
        self.assertIn("None are the same", text)

    def test_the_confirmation_stays_under_2000_letters_with_25_long_names(self) -> None:
        names = [Name(f"{i:032x}", f"{'Odd' * 33} {i}", 0) for i in range(26)]
        kinds = {n.id: ("place" if i % 2 else "NPC") for i, n in enumerate(names)}
        text = la.confirm_text(names[0], names[1:], kinds)
        self.assertLessEqual(len(text), 2000)
        self.assertIn("is a place; it becomes an NPC's other name", text)


class ThePanelButton(unittest.TestCase):
    def test_only_a_dm_gets_it_and_the_panel_still_fits(self) -> None:
        none = NamesHome("c1", 0, None, None)
        self.assertFalse([x for x in labels(none) if "Look-alikes" in x])
        dm = NamesHome("c1", 3, [("e1", "Belleros", "npc")], None, look_alikes=4)
        self.assertIn("🔗 Look-alikes (4)", labels(dm))
        row = next(c for c in dm.children if getattr(c, "label", "") == "🔗 Look-alikes (4)").row
        self.assertEqual(row, 1)
        self.assertLessEqual(len(dm.children), 25)
        all_reviews = Reviews(heard=1, people=1, claims=1, said=1)
        full = NamesHome(
            "c1", 3, [("e1", "Belleros", "npc")], all_reviews, connections=2, look_alikes=4
        )
        self.assertLessEqual(len(full.children), 25)


def interaction() -> Any:
    answered = SimpleNamespace(is_done=lambda: True)
    return SimpleNamespace(
        response=answered,
        user=SimpleNamespace(id=7),
        followup=SimpleNamespace(send=AsyncMock()),
        type=discord.InteractionType.component,
    )


class TheMerge(unittest.IsolatedAsyncioTestCase):
    """The confirmation's button: one `merge_many`, one Undo, then the groups asked for again."""

    async def run_yes(
        self, memory: Any, *, dm: bool = True, left: list[Name] | None = None, show: Any = None
    ) -> tuple[Any, Any, Any]:
        campaign = SimpleNamespace(guild_id=1, id="c" * 32)
        view = la.ConfirmMerge("c" * 32, [NAMES], 0, KINDS, NAMES[0], NAMES[1:], left)
        reply, tell = AsyncMock(), AsyncMock()
        with (
            patch.object(la, "_campaign_for", AsyncMock(return_value=campaign)),
            patch.object(la, "_memory", MagicMock(return_value=memory)),
            patch.object(la, "sees_secrets", MagicMock(return_value=dm)),
            patch.object(la, "_replace", reply),
            patch.object(la, "_tell", tell),
            patch.object(la, "_answer_first", AsyncMock()),
            patch.object(la, "changed", MagicMock()),
            patch.object(la, "_show_group", show or AsyncMock()),
        ):
            asked = interaction()
            await view._yes(asked)
        return asked, reply, tell

    def memory(self, **kw: Any) -> Any:
        return SimpleNamespace(
            merge_many=AsyncMock(return_value=SimpleNamespace(batch=42)),
            look_alikes=AsyncMock(return_value=[]),
            not_the_same=AsyncMock(return_value=1),
            keep_days=30,
            **kw,
        )

    async def test_one_merge_many_call_and_one_undo_for_all_of_it(self) -> None:
        memory = self.memory()
        show = AsyncMock()
        asked, _, _ = await self.run_yes(memory, show=show)
        memory.merge_many.assert_awaited_once_with(1, "c" * 32, A, [B, C], source=DM)
        sent = asked.followup.send.await_args
        self.assertIn("Undo", sent.args[0])
        undo = sent.kwargs["view"].children[0]
        self.assertEqual((undo.campaign_id, undo.entity_id, undo.batch), ("c" * 32, B, 42))
        self.assertTrue(sent.kwargs["ephemeral"])
        assert show.await_args is not None
        self.assertIn(
            "2 names are now other names for **Belleros**", show.await_args.kwargs["note"]
        )

    async def test_the_undo_is_sent_before_the_next_group_is_drawn(self) -> None:
        """If drawing the next message fails, the merge must still have its Undo."""
        memory = self.memory()
        seen: list[bool] = []

        async def failing(*a: Any, **k: Any) -> None:
            seen.append(asked.followup.send.await_count == 1)
            raise RuntimeError("the edit failed")

        asked = interaction()
        with self.assertRaises(RuntimeError):
            await self.run_yes_with(memory, asked, failing)
        self.assertEqual(seen, [True])

    async def run_yes_with(self, memory: Any, asked: Any, show: Any) -> None:
        campaign = SimpleNamespace(guild_id=1, id="c" * 32)
        view = la.ConfirmMerge("c" * 32, [NAMES], 0, KINDS, NAMES[0], NAMES[1:])
        with (
            patch.object(la, "_campaign_for", AsyncMock(return_value=campaign)),
            patch.object(la, "_memory", MagicMock(return_value=memory)),
            patch.object(la, "sees_secrets", MagicMock(return_value=True)),
            patch.object(la, "_replace", AsyncMock()),
            patch.object(la, "_tell", AsyncMock()),
            patch.object(la, "_answer_first", AsyncMock()),
            patch.object(la, "changed", MagicMock()),
            patch.object(la, "_show_group", show),
        ):
            await view._yes(asked)

    async def test_a_name_the_dm_unticked_is_remembered_as_not_the_same_as_the_kept_one(
        self,
    ) -> None:
        memory = self.memory()
        extra = Name("d" * 32, "Belloros", 0)
        await self.run_yes(memory, left=[extra])
        memory.not_the_same.assert_awaited_once_with(1, "c" * 32, [A, "d" * 32], user_id=7)

    async def test_none_are_the_same_remembers_every_name_shown_ticked_or_not(self) -> None:
        memory = self.memory()
        campaign = SimpleNamespace(guild_id=1, id="c" * 32)
        view = la.LookAlikeGroup("c" * 32, [NAMES], 0, KINDS)
        view.ticked = {A, B}  # the DM unticked the third: it is still one of "none"
        with (
            patch.object(la, "_campaign_for", AsyncMock(return_value=campaign)),
            patch.object(la, "_memory", MagicMock(return_value=memory)),
            patch.object(la, "sees_secrets", MagicMock(return_value=True)),
            patch.object(la, "_answer_first", AsyncMock()),
            patch.object(la, "_show_group", AsyncMock()),
        ):
            await view._apart(interaction())
        ids = memory.not_the_same.await_args.args[2]
        self.assertEqual(sorted(ids), sorted([A, B, C]))
        self.assertIn("None are the same", labels(view))

    async def test_a_name_that_is_not_the_dms_to_merge_changes_nothing(self) -> None:
        memory = self.memory()
        _, _, tell = await self.run_yes(memory, dm=False)
        memory.merge_many.assert_not_awaited()
        self.assertIn("Only the DM", tell.await_args.args[1])

    async def test_a_refused_merge_says_nothing_was_changed(self) -> None:
        memory = self.memory()
        memory.merge_many.side_effect = MemoryRuleError(
            "Those are two different players' characters."
        )
        asked, reply, _ = await self.run_yes(memory)
        said = " ".join(str(c.args[1]) for c in reply.await_args_list)
        self.assertIn("Nothing was changed", said)
        asked.followup.send.assert_not_awaited()  # no Undo for what did not happen

    async def test_a_broken_merge_says_so_and_offers_no_undo(self) -> None:
        memory = self.memory()
        memory.merge_many.side_effect = RuntimeError("boom")
        asked, reply, _ = await self.run_yes(memory)
        self.assertIn(
            "nothing was changed", " ".join(str(c.args[1]) for c in reply.await_args_list)
        )
        asked.followup.send.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
