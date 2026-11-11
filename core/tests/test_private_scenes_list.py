"""🤫 Private scenes, the DM's list (#2996 rule 5, slice 2): its words, Discord's limits, the DM
check on every press, and "Not private". No Discord and no database: the store and the Discord
helpers are stood in for. Invented names."""

from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

from dmbot.memory.private_scene_store import Kept
from dmbot.ui import private_scenes_list as psl

WREN, ASH = "w" * 32, "a" * 32
CID = "c" * 32
SCENE = Kept("s" * 32, ASH, WREN, 1_700_000_000, 1_700_000_650_000, 1_700_001_370_000, 9)
NAMES = {WREN: "Wren", ASH: "Ash"}


class Words(unittest.TestCase):
    def test_the_words(self) -> None:
        self.assertEqual(psl.when(SCENE), "14 Nov 2023 · 10 min in · about 12 min, 9 lines spoken")
        self.assertEqual(psl.pair(NAMES, SCENE), "Ash and Wren")
        self.assertIn("1 private scene:", psl.notice(1))
        self.assertIn("3 private scenes", psl.notice(3))
        text = psl.choice_text("Ash and Wren", SCENE)
        self.assertIn("Not private", text)
        self.assertIn("won't add it back", text)
        self.assertEqual(psl.pair(NAMES, SCENE, " & "), "Ash & Wren")

    def test_the_notice_names_nobody(self) -> None:
        for n in (1, 2):
            self.assertNotIn("Wren", psl.notice(n))
            self.assertIn("never the words", psl.notice(n))

    def test_the_menu_keeps_to_discords_limits(self) -> None:
        async def build() -> psl.SceneList:
            scenes = [Kept(f"{n:032x}", ASH, WREN, 1_700_000_000, 0, 60_000, 2) for n in range(30)]
            return psl.SceneList(CID, scenes, {WREN: "W" * 150, ASH: "A" * 150})

        options = asyncio.run(build()).pick.options
        self.assertEqual(len(options), psl.SHOWN_MAX)
        self.assertTrue(all(len(o.label) <= 100 and len(o.description or "") <= 100
                            for o in options))  # fmt: skip


class Presses(unittest.IsolatedAsyncioTestCase):
    """Each press reads the campaign and checks the DM again, then the store."""

    async def press_not_private(self, is_dm: bool, done: bool = True) -> tuple[Any, list[str]]:
        store = SimpleNamespace(
            not_private=AsyncMock(return_value=done), listed=AsyncMock(return_value=[])
        )
        told: list[str] = []
        shown: list[str] = []
        campaign = SimpleNamespace(guild_id=1, id=CID)

        async def tell(_: Any, text: str) -> None:
            told.append(text)

        async def replace(_: Any, text: str, view: Any) -> None:
            shown.append(text)

        bot = SimpleNamespace(consent=SimpleNamespace(consenting=AsyncMock(return_value={7})))
        with (
            patch.object(psl, "_memory", return_value=SimpleNamespace(_db=None)),
            patch.object(psl, "_campaign_for", AsyncMock(return_value=campaign)),
            patch.object(psl, "sees_secrets", return_value=is_dm),
            patch.object(psl, "PrivateScenes", return_value=store),
            patch.object(psl, "_answer_first", AsyncMock()),
            patch.object(psl, "_bot", return_value=bot),
            patch.object(psl, "_tell", tell),
            patch.object(psl, "_replace", replace),
        ):
            view = psl.SceneActions(CID, SCENE, "Ash and Wren")
            await view._not_private(SimpleNamespace(user=SimpleNamespace(id=7)))  # type: ignore[arg-type]
        return store, told + shown

    async def test_a_dm_marks_it_not_private(self) -> None:
        store, said = await self.press_not_private(True)
        store.not_private.assert_awaited_once()
        self.assertEqual(store.not_private.await_args.args[:3], (1, CID, SCENE.id))
        self.assertTrue(any("marked not private" in s and "Ash and Wren" in s for s in said))

    async def test_someone_no_longer_a_dm_changes_nothing(self) -> None:
        store, said = await self.press_not_private(False)
        store.not_private.assert_not_awaited()
        self.assertEqual(said, [psl.DM_ONLY])

    async def test_one_already_gone_says_so(self) -> None:
        _, said = await self.press_not_private(True, done=False)
        self.assertTrue(any("already gone" in s for s in said))


class TheMessage(unittest.TestCase):
    def test_a_full_list_keeps_its_lines_and_fits(self) -> None:
        long = {WREN: "W" * 60, ASH: "A" * 60}
        scenes = [Kept(f"{n:032x}", ASH, WREN, 1_700_000_000, 1_700_000_100_000,
                       1_700_000_700_000, 9) for n in range(psl.SHOWN_MAX)]  # fmt: skip
        text = psl.list_text("A note.\n\n", scenes, long, 40)
        self.assertLessEqual(len(text), psl.MESSAGE_LIMIT)
        self.assertIn("\n• **", text)
        self.assertTrue(text.rstrip().endswith("pick them in the menu below."))

    def test_a_short_list_says_how_many_older_ones_are_not_shown(self) -> None:
        text = psl.list_text("", [SCENE], NAMES, 30)
        self.assertIn("• **Ash and Wren** · ", text)
        self.assertTrue(text.endswith(f"Only the newest {psl.SHOWN_MAX} show."))


class TheRoots(unittest.TestCase):
    def test_a_merge_chain_ends_at_the_name_it_became(self) -> None:
        from dmbot.memory.private_scene_store import _root

        self.assertEqual(_root({"a": "b", "b": "c"}, "a"), "c")
        self.assertEqual(_root({"a": "b", "b": "a"}, "a"), "b")  # (a loop stops)
        self.assertEqual(_root({}, "a"), "a")


class Opening(unittest.IsolatedAsyncioTestCase):
    """The press on the channel's button: the DM check comes before any read."""

    async def open_it(self, is_dm: bool) -> tuple[Any, Any, list[str]]:
        store = SimpleNamespace(
            listed=AsyncMock(return_value=[SCENE]),
            roots=AsyncMock(return_value={WREN: WREN, ASH: "m" * 32}),
        )
        memory = SimpleNamespace(
            _db=None,
            entity=AsyncMock(
                side_effect=lambda g, c, i: None if i == WREN else SimpleNamespace(name="Ashe")
            ),
        )
        consenting = AsyncMock(return_value={7, 8})
        said: list[str] = []

        async def tell(_: Any, text: str) -> None:
            said.append(text)

        async def replace(_: Any, text: str, view: Any) -> None:
            said.append(text)

        with (
            patch.object(psl, "_memory", return_value=memory),
            patch.object(psl, "_campaign_for", AsyncMock(return_value=SimpleNamespace(
                guild_id=1, id=CID))),
            patch.object(psl, "sees_secrets", return_value=is_dm),
            patch.object(psl, "PrivateScenes", return_value=store),
            patch.object(psl, "_answer_first", AsyncMock()),
            patch.object(psl, "_bot", return_value=SimpleNamespace(
                consent=SimpleNamespace(consenting=consenting))),
            patch.object(psl, "_tell", tell),
            patch.object(psl, "_replace", replace),
        ):  # fmt: skip
            await psl.show(SimpleNamespace(user=SimpleNamespace(id=7)), CID)  # type: ignore[arg-type]
        return store, consenting, said

    async def test_someone_not_a_dm_sees_nothing_and_nothing_is_read(self) -> None:
        store, consenting, said = await self.open_it(False)
        self.assertEqual(said, [psl.DM_ONLY])
        store.listed.assert_not_awaited()
        consenting.assert_not_awaited()

    async def test_a_dm_sees_it_read_with_whos_recorded_now(self) -> None:
        store, _, said = await self.open_it(True)
        self.assertEqual(store.listed.await_args.args, (1, CID, {7, 8}))
        # (Ash was merged into "Ashe"; Wren is gone: "a character")
        self.assertIn("• **Ashe and a character** · ", said[0])
