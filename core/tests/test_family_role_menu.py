"""The family role menu on the name card's Connect (#3102): "is family of" asks which role first,
saves nothing until a role or "Just family" is picked, Cancel saves nothing, the role is stored
with the direction its words read (a one-way role gets a direction, a shared one does not), and the
other way round swaps which name holds the role. The store is faked; its own tests cover writes.
Invented names only."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

from dmbot.memory.models import Written
from dmbot.ui import name_card as nc

CAMPAIGN = SimpleNamespace(id="c" * 32, guild_id=5)
MIRA, ALDRIC = "m" * 32, "a" * 32


def fake_interaction() -> Any:
    return SimpleNamespace(
        user=SimpleNamespace(id=1), response=SimpleNamespace(is_done=lambda: False)
    )


def _kwargs(mock: Any) -> dict[str, Any]:
    await_args = mock.await_args
    assert await_args is not None
    return dict(await_args.kwargs)


class TheRoleMenu(unittest.IsolatedAsyncioTestCase):
    def test_every_family_role_is_one_of_the_stored_roles(self) -> None:
        from dmbot.memory import kin

        for role in nc.FAMILY_ROLES:
            self.assertIn(role, kin.ROLES)

    def test_the_menu_holds_the_roles_and_just_family_and_says_how_each_reads(self) -> None:
        menu = nc.FamilyRole(CAMPAIGN.id, MIRA, ALDRIC, "kin_of", ("Mira", "Aldric"),
                             secret=False, forced=False)  # fmt: skip
        values = [o.value for o in menu.role.options]
        self.assertEqual(len(values), len(nc.FAMILY_ROLES) + 1)
        self.assertLessEqual(len(values), 25)
        sister = next(o for o in menu.role.options if o.value == "sister")
        self.assertEqual(sister.description, "Mira is the sister of Aldric")
        self.assertEqual(sister.label, "Sister")
        self.assertIn(nc.JUST_FAMILY, values)
        for role in ("grandparent", "grandson", "granddaughter", "grandchild"):
            self.assertIn(role, values)

    def test_the_other_way_round_puts_the_other_name_first(self) -> None:
        menu = nc.FamilyRole(CAMPAIGN.id, MIRA, ALDRIC, "kin_of" + nc.BACK, ("Mira", "Aldric"),
                             secret=False, forced=False)  # fmt: skip
        sister = next(o for o in menu.role.options if o.value == "sister")
        self.assertEqual(sister.description, "Aldric is the sister of Mira")

    async def test_is_family_of_asks_the_role_and_saves_nothing_yet(self) -> None:
        found = (CAMPAIGN, "Mira")
        other = (CAMPAIGN, "Aldric")
        connect = AsyncMock()
        replace = AsyncMock()
        with (
            patch.object(nc, "_answer_first", AsyncMock()),
            patch.object(nc, "_current", AsyncMock(side_effect=[found, other])),
            patch.object(nc, "_replace", replace),
            patch.object(nc, "connect", connect),
        ):
            await nc.choose_other(
                fake_interaction(), MagicMock(), ALDRIC, CAMPAIGN.id, MIRA, "kin_of",
                secret=False,
            )  # fmt: skip
        connect.assert_not_awaited()
        await_args = replace.await_args
        assert await_args is not None
        menu = await_args.args[2]
        self.assertIsInstance(menu, nc.FamilyRole)
        self.assertEqual((menu.entity_id, menu.other_id, menu.how), (MIRA, ALDRIC, "kin_of"))
        self.assertEqual((menu.secret, menu.forced), (False, False))

    async def test_a_picked_role_passes_the_role_on_to_connect(self) -> None:
        menu = nc.FamilyRole(CAMPAIGN.id, MIRA, ALDRIC, "kin_of", ("Mira", "Aldric"),
                             secret=False, forced=False)  # fmt: skip
        connect = AsyncMock()
        with patch.object(nc, "connect", connect):
            cast(Any, menu).role = SimpleNamespace(values=["sister"])
            await menu._picked(fake_interaction())
        connect.assert_awaited_once()
        self.assertEqual(_kwargs(connect)["detail"], "sister")

    async def test_just_family_saves_with_no_role(self) -> None:
        menu = nc.FamilyRole(CAMPAIGN.id, MIRA, ALDRIC, "kin_of", ("Mira", "Aldric"),
                             secret=False, forced=False)  # fmt: skip
        connect = AsyncMock()
        with patch.object(nc, "connect", connect):
            cast(Any, menu).role = SimpleNamespace(values=[nc.JUST_FAMILY])
            await menu._picked(fake_interaction())
        self.assertEqual(_kwargs(connect)["detail"], "")

    async def test_cancel_saves_nothing_and_goes_back_to_the_card(self) -> None:
        menu = nc.FamilyRole(CAMPAIGN.id, MIRA, ALDRIC, "kin_of", ("Mira", "Aldric"),
                             secret=False, forced=False)  # fmt: skip
        show = AsyncMock()
        with patch.object(nc, "show_card", show), patch.object(nc, "connect", AsyncMock()) as c:
            await menu._cancel(fake_interaction())
        c.assert_not_awaited()
        show.assert_awaited_once()


class TheSave(unittest.IsolatedAsyncioTestCase):
    async def save(self, how: str, detail: str) -> tuple[tuple[Any, ...], dict[str, Any]]:
        """What connect hands the store: the positional (guild, campaign, subject, predicate,
        object) and the keyword arguments. The saved note is in `self.note`."""
        memory = MagicMock()
        memory.add_relation = AsyncMock(
            return_value=Written((SimpleNamespace(secret=False), []), None)
        )
        with (
            patch.object(nc, "_answer_first", AsyncMock()),
            patch.object(
                nc, "_current", AsyncMock(side_effect=[(CAMPAIGN, "Mira"), (CAMPAIGN, "Aldric")])
            ),
            patch.object(nc, "_memory", return_value=memory),
            patch.object(nc, "sees_secrets", return_value=True),
            patch.object(nc, "changed"),
            patch.object(nc, "_tell", AsyncMock()),
            patch.object(nc, "show_card", show := AsyncMock()),
        ):
            await nc.connect(fake_interaction(), CAMPAIGN.id, MIRA, how, ALDRIC, detail=detail)
        show_args = show.await_args
        assert show_args is not None
        self.note = show_args.kwargs.get("note") or ""
        await_args = memory.add_relation.await_args
        assert await_args is not None
        return await_args.args, await_args.kwargs

    async def test_a_one_way_role_is_stored_with_its_direction(self) -> None:
        args, kwargs = await self.save("kin_of", "sister")
        self.assertEqual((kwargs["detail"], kwargs["directional"]), ("sister", True))
        self.assertEqual((args[2], args[4]), (MIRA, ALDRIC))  # Mira holds the role

    async def test_a_shared_role_is_stored_without_a_direction(self) -> None:
        _, kwargs = await self.save("kin_of", "cousin")
        self.assertEqual((kwargs["detail"], kwargs["directional"]), ("cousin", False))

    async def test_just_family_stores_no_role(self) -> None:
        _, kwargs = await self.save("kin_of", "")
        self.assertEqual((kwargs["detail"], kwargs["directional"]), ("", False))

    async def test_the_other_way_round_swaps_who_holds_the_role(self) -> None:
        args, kwargs = await self.save("kin_of" + nc.BACK, "sister")
        self.assertEqual((kwargs["detail"], kwargs["directional"]), ("sister", True))
        self.assertEqual((args[2], args[4]), (ALDRIC, MIRA))  # Aldric holds the role

    async def test_the_saved_note_reads_with_the_role_in_the_order_it_was_saved(self) -> None:
        await self.save("kin_of", "sister")
        self.assertIn("is the sister of", self.note)
        self.assertIn("Mira", self.note.split("is the sister of")[0])  # Mira holds the role

    async def test_the_saved_note_reads_the_other_way_round_for_the_other_way_round(self) -> None:
        await self.save("kin_of" + nc.BACK, "sister")
        self.assertIn("is the sister of", self.note)
        self.assertIn("Aldric", self.note.split("is the sister of")[0])  # Aldric holds the role


class TheSecretPassThrough(unittest.IsolatedAsyncioTestCase):
    async def test_a_forced_secret_role_passes_secret_and_forced_to_connect(self) -> None:
        menu = nc.FamilyRole(CAMPAIGN.id, MIRA, ALDRIC, "kin_of", ("Mira", "Aldric"),
                             secret=True, forced=True)  # fmt: skip
        connect = AsyncMock()
        with patch.object(nc, "connect", connect):
            cast(Any, menu).role = SimpleNamespace(values=["sister"])
            await menu._picked(fake_interaction())
        kwargs = _kwargs(connect)
        self.assertEqual(
            (kwargs["secret"], kwargs["forced"], kwargs["detail"]), (True, True, "sister")
        )

    async def test_just_family_says_it_is_family_with_no_role(self) -> None:
        menu = nc.FamilyRole(CAMPAIGN.id, MIRA, ALDRIC, "kin_of", ("Mira", "Aldric"),
                             secret=False, forced=False)  # fmt: skip
        just = next(o for o in menu.role.options if o.value == nc.JUST_FAMILY)
        self.assertEqual(just.description, "Mira and Aldric are family. No role.")
