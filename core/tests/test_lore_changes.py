"""A lore read that finds a different connection between two names the campaign already connects
(#3021): the same kind the same way round is "already known", a kind that cannot hold beside the
old one is offered as a change (the old one is ended, never deleted), a kind that can is offered
as new, and the reply always says what became of the connections. Invented names and text."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, patch

from dmbot.memory import backstory_keep as keep
from dmbot.memory.models import CONFIRMED, MemoryRuleError, NewName
from dmbot.ui import name_background as ui_bg
from dmbot.ui.name_lists import Upload, _ai_busy, _ai_reads
from tests import lore_fake
from tests.test_background_connections import link, name, shown
from tests.test_memory_names import DM, GUILD, NamesTest

TEXT = "Belleros and Cerric were close once. Now Belleros is the sworn enemy of Cerric."
NAMES = [
    name("Belleros", "person", "Belleros and Cerric"),
    name("Cerric", "person", "Cerric were close"),
]


def reply(how: str) -> dict[str, Any]:
    return {
        "names": NAMES,
        "connections": [link("Belleros", how, "Cerric", "Belleros is the sworn enemy of Cerric")],
    }


class TheChange(NamesTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        _ai_busy.clear()
        _ai_reads.clear()
        self.bot.plan_gate = AsyncMock(return_value=None)  # type: ignore[method-assign]
        self.bot.campaigns.record_confirmation = AsyncMock()  # type: ignore[method-assign]
        from dmbot.memory.backstory_links import BackstoryLinks

        self.store = BackstoryLinks(self.db)
        self.bot.backstory_links = self.store
        made = await self.memory.add_names(
            GUILD, self.campaign.id,
            [NewName("Belleros", "npc", CONFIRMED), NewName("Cerric", "npc", CONFIRMED)],
            source="dm",
        )  # fmt: skip
        self.bel, self.cer = made.value
        assert self.bel and self.cer
        self.old = await self.memory.add_relation(
            GUILD, self.campaign.id, self.bel, "ally_of", self.cer, source="dm", confidence=1.0,
            status=CONFIRMED,
        )  # fmt: skip

    async def asyncTearDown(self) -> None:
        _ai_busy.clear()
        _ai_reads.clear()
        await super().asyncTearDown()

    async def read(self, how: str) -> Any:
        ai = lore_fake.lore_ai(json.dumps(reply(how)))
        self.bot.background_ai = ai  # type: ignore[assignment]
        self.bot.background_read_ai = ai  # type: ignore[assignment]
        it = self.it()
        await ui_bg.offer(it, self.campaign.id, Upload(TEXT, "your background"))
        press = self.it()
        await it.response.sent[0][1]["view"]._read(press)
        return press

    async def relations(self) -> list[Any]:
        return await self.memory.relations(GUILD, self.campaign.id, include_secret=True)

    async def card(self) -> Any:
        it = self.it(DM)
        self.assertTrue(await ui_bg.offer_ready(it, self.campaign.id))
        return it.followup.send.await_args.kwargs["view"]

    async def test_a_kind_that_cannot_hold_beside_the_old_one_is_offered_as_a_change(self) -> None:
        press = await self.read("enemy")
        said = shown(press)
        self.assertNotIn("Nothing was added", said)
        self.assertIn("1 change ready to check", said)
        labels = [
            getattr(b, "label", "")
            for b in press.edit_original_response.await_args.kwargs["view"].children
        ]
        self.assertIn("📜 Check the connections", labels)
        view = await self.card()
        self.assertIn("Change this?", view.text())
        self.assertIn("Now: **Belleros** ally of **Cerric**", view.text())
        self.assertIn("Your text: **Belleros** is an enemy of **Cerric**", view.text())
        # nothing has changed until the DM says yes
        (only,) = await self.relations()
        self.assertIsNone(only.to_session_at)
        await next(b for b in view.children if b.label == "✅ Change it").callback(self.it())
        by_kind = {r.predicate: r for r in await self.relations()}
        self.assertIsNotNone(by_kind["ally_of"].to_session_at)  # ended, not deleted
        self.assertIsNone(by_kind["enemy_of"].to_session_at)  # live
        self.assertEqual(await self.store.waiting(GUILD, self.campaign.id), [])

    async def test_a_kind_that_can_hold_beside_the_old_one_is_offered_as_new(self) -> None:
        press = await self.read("works for")
        said = shown(press)
        self.assertIn("1 ready to check", said)
        self.assertNotIn("change", said)
        view = await self.card()
        self.assertIn("Keep this?", view.text())
        await next(b for b in view.children if b.label == "✅ Keep").callback(self.it())
        live = [r.predicate for r in await self.relations() if r.to_session_at is None]
        self.assertCountEqual(live, ["ally_of", "serves"])

    async def test_the_same_kind_is_already_known_and_a_rerun_queues_nothing(self) -> None:
        said = shown(await self.read("friend"))  # (the connection the campaign already holds)
        self.assertIn("1 already known", said)
        self.assertEqual(await self.store.waiting(GUILD, self.campaign.id), [])
        await self.read("enemy")
        again = shown(await self.read("enemy"))
        self.assertIn("1 already known", again)  # (waiting from the first run)
        self.assertEqual(len(await self.store.waiting(GUILD, self.campaign.id)), 1)

    async def test_keep_all_ends_the_old_one_and_undo_brings_it_back(self) -> None:
        await self.read("enemy")
        waiting = await self.store.waiting(GUILD, self.campaign.id)
        done = await keep.keep_all(self.memory, self.store, GUILD, self.campaign.id, waiting)
        by_kind = {r.predicate: r for r in await self.relations()}
        self.assertIsNotNone(by_kind["ally_of"].to_session_at)
        self.assertEqual(
            await keep.undo_all(self.memory, self.store, GUILD, self.campaign.id, done), 1
        )
        live = [r.predicate for r in await self.relations() if r.to_session_at is None]
        self.assertEqual(live, ["ally_of"])

    async def test_a_refused_change_leaves_the_old_connection_as_it_was(self) -> None:
        await self.read("enemy")
        view = await self.card()
        with patch.object(self.memory, "add_relation", AsyncMock(side_effect=MemoryRuleError("x"))):
            await next(b for b in view.children if b.label == "✅ Change it").callback(self.it())
        (only,) = await self.relations()
        self.assertEqual((only.predicate, only.to_session_at), ("ally_of", None))
        self.assertEqual(len(await self.store.waiting(GUILD, self.campaign.id)), 1)

    async def test_after_a_change_the_old_kind_can_be_offered_back(self) -> None:
        await self.read("enemy")
        view = await self.card()
        await next(b for b in view.children if b.label == "✅ Change it").callback(self.it())
        said = shown(await self.read("friend"))  # (the ended friendship is not "already known")
        self.assertIn("1 change ready to check", said)
