"""📋 More lists (#4103): one button on the Names home for 👣 Met but not named and 🤫 Private
scenes, each shown only while its switch is on, the button hidden while both are off. No Discord
and no database."""

from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

from dmbot.ui import more_lists as ml
from dmbot.ui.names import NamesHome

CID = "c" * 32


def labels(view: Any) -> list[str]:
    return [str(getattr(item, "label", "")) for item in view.children]


class Buttons(unittest.TestCase):
    def test_the_counts(self) -> None:
        self.assertFalse(ml.Counts().shown)
        self.assertTrue(ml.Counts(met=0).shown)
        self.assertEqual(ml.Counts(met=2, scenes=3).total, 5)
        self.assertEqual(ml.button_label(ml.Counts(met=0, scenes=0)), "📋 More lists")
        self.assertEqual(ml.button_label(ml.Counts(met=2, scenes=150)), "📋 More lists (99+)")
        self.assertEqual(ml.scenes_label(4), "🤫 Private scenes (4)")

    def test_each_list_shows_only_while_its_switch_is_on(self) -> None:
        async def build(counts: ml.Counts) -> list[str]:
            return labels(ml.MoreLists(CID, counts))

        both = asyncio.run(build(ml.Counts(met=1, scenes=2)))
        self.assertEqual(both, ["👣 Met but not named (1)", "🤫 Private scenes (2)",
                                "↩️ Back to Names"])  # fmt: skip
        self.assertEqual(asyncio.run(build(ml.Counts(scenes=0))),
                         ["🤫 Private scenes (0)", "↩️ Back to Names"])  # fmt: skip

    def test_the_names_home_hides_it_while_both_switches_are_off(self) -> None:
        async def home(more: ml.Counts | None) -> list[str]:
            return labels(NamesHome(CID, 0, more=more))

        self.assertFalse(any("More lists" in x for x in asyncio.run(home(None))))
        self.assertFalse(any("More lists" in x for x in asyncio.run(home(ml.Counts()))))
        self.assertIn("📋 More lists (3)", asyncio.run(home(ml.Counts(met=3))))
        self.assertFalse(any("Met but not named" in x for x in asyncio.run(home(ml.Counts(met=3)))))


class Counting(unittest.IsolatedAsyncioTestCase):
    async def counted(self, met_on: bool, scenes_on: bool) -> ml.Counts:
        settings = SimpleNamespace(met_unnamed_on=met_on, private_scenes_on=scenes_on)
        bot = SimpleNamespace(
            settings=settings, consent=SimpleNamespace(consenting=AsyncMock(return_value={7}))
        )
        met = SimpleNamespace(count=AsyncMock(return_value=2))
        scenes = SimpleNamespace(listed=AsyncMock(return_value=[object()] * 3))
        with (
            patch.object(ml, "_bot", return_value=bot),
            patch.object(ml, "_memory", return_value=SimpleNamespace(_db=None)),
            patch("dmbot.memory.met_store.MetStore", return_value=met),
            patch("dmbot.memory.private_scene_store.PrivateScenes", return_value=scenes),
        ):
            return await ml.count(SimpleNamespace(), SimpleNamespace(guild_id=1, id=CID))  # type: ignore[arg-type]

    async def test_both_off_counts_nothing(self) -> None:
        self.assertEqual(await self.counted(False, False), ml.Counts())

    async def test_each_on_list_is_counted(self) -> None:
        self.assertEqual(await self.counted(True, True), ml.Counts(2, 3))
        self.assertEqual(await self.counted(False, True), ml.Counts(None, 3))

    async def test_someone_not_a_dm_sees_no_list(self) -> None:
        said: list[str] = []

        async def tell(_: Any, text: str) -> None:
            said.append(text)

        with (
            patch.object(ml, "_answer_first", AsyncMock()),
            patch.object(ml, "_memory", return_value=SimpleNamespace(_db=None)),
            patch.object(ml, "_campaign_for", AsyncMock(return_value=SimpleNamespace(
                guild_id=1, id=CID))),
            patch.object(ml, "sees_secrets", return_value=False),
            patch.object(ml, "count", AsyncMock()) as counted,
            patch.object(ml, "_tell", tell),
        ):  # fmt: skip
            await ml.show(SimpleNamespace(user=SimpleNamespace(id=9)), CID)  # type: ignore[arg-type]
        self.assertEqual(said, [ml.DM_ONLY])
        counted.assert_not_awaited()
