"""A lore connection that says an item changed hands (#4115 slice 2; Supervisor's ruling,
2026-11-10): the reader keeps who had it before and how it moved, the card says it in brackets,
and keeping it ends what they held, as history with the reason. A theft gives the thief `carries`
only and the owner keeps owning; "found" never ends; turning it down changes nothing; a hidden
keep never ends what the players see. Invented names and text."""

from __future__ import annotations

import json
import unittest
from collections import Counter
from typing import Any
from unittest.mock import AsyncMock

from psycopg import errors

from dmbot.memory import background as bg
from dmbot.memory import backstory_keep as keep
from dmbot.memory.models import CONFIRMED, END_REASONS, REJECTED, NewName
from dmbot.memory.ontology import FALLBACK_PREDICATE
from dmbot.ui import name_background as ui_bg
from dmbot.ui.name_lists import Upload, _ai_busy, _ai_reads
from tests import lore_fake
from tests.test_background_connections import name
from tests.test_memory_names import DM, GUILD, NamesTest

TEXT = "Claude sold the Frost Brand to Belleros after the war."
QUOTE = "Claude sold the Frost Brand to Belleros"
NAMES = [
    name("Claude", "person", "Claude sold"),
    name("Belleros", "person", "to Belleros"),
    name("Frost Brand", "item", "the Frost Brand"),
]


def handed(how: str, change: str, holder: str = "Claude", quote: str = QUOTE) -> dict[str, Any]:
    return {"a": "Belleros", "how": how, "b": "Frost Brand", "quote": quote, "from": holder,
            "change": change}  # fmt: skip


def reply(*links: dict[str, Any]) -> str:
    return json.dumps({"names": NAMES, "connections": list(links)})


THREE = [
    bg.Found(n, k, (), (), "q")
    for n, k in (("Belleros", "person"), ("Claude", "person"), ("Frost Brand", "item"))
]


class TheReader(unittest.TestCase):
    def test_the_change_words_are_the_end_reasons(self) -> None:
        self.assertEqual(bg.HAND_OVERS, END_REASONS)

    def test_a_sale_keeps_who_sold_it(self) -> None:
        (got,) = bg.read_connections(reply(handed("owns", "sold")), TEXT)
        self.assertEqual((got.a, got.how, got.b, got.from_holder, got.change),
                         ("Belleros", "owns", "Frost Brand", "Claude", "sold"))  # fmt: skip

    def test_a_theft_is_held_not_owned(self) -> None:
        (got,) = bg.read_connections(reply(handed("owns", "stolen")), TEXT)
        self.assertEqual((got.how, got.change), ("carries", "stolen"))

    def test_a_one_before_not_in_the_quote_leaves_a_plain_connection(self) -> None:
        lost: Counter[str] = Counter()
        (got,) = bg.read_connections(reply(handed("owns", "sold", "Mirelle")), TEXT, dropped=lost)
        self.assertEqual((got.from_holder, got.change), ("", ""))
        self.assertEqual(lost["hand_over_dropped"], 1)

    def test_a_pronoun_line_is_held_to_the_rules_when_kept(self) -> None:
        # (reviewer: a line the who-is-it check settled never went through `_handed`)
        names = THREE
        stole = bg.Connection("Belleros", "owns", "Frost Brand", "He stole the Frost Brand from "
                              "Claude", from_holder="Claude", change="stolen")  # fmt: skip
        lost: Counter[str] = Counter()
        (kept,) = bg.keep_connections([[stole]], names, {}, dropped=lost)
        self.assertEqual((kept.how, kept.change, lost["hand_over_dropped"]), ("owns", "", 1))
        absent = bg.Connection("Belleros", "owns", "Frost Brand", "He bought the Frost Brand",
                               from_holder="Claude", change="sold")  # fmt: skip
        (kept,) = bg.keep_connections([[absent]], names, {})
        self.assertEqual(kept.change, "")

    def test_a_plain_twin_read_first_takes_the_hand_over(self) -> None:
        names = THREE
        plain = bg.Connection("Belleros", "owns", "Frost Brand", "Belleros owns the Frost Brand")
        sold = bg.Connection("Belleros", "owns", "Frost Brand", QUOTE, from_holder="Claude",
                             change="sold")  # fmt: skip
        (kept,) = bg.keep_connections([[plain, sold]], names, {})
        self.assertEqual((kept.from_holder, kept.change), ("Claude", "sold"))

    def test_a_change_on_another_kind_is_ignored(self) -> None:
        self.assertEqual(bg.hand_over_of("knows", "Claude", "sold"), ("knows", "", ""))
        self.assertEqual(bg.hand_over_of("owns", "Claude", "burnt"), ("owns", "", ""))
        self.assertEqual(bg.hand_over_of("carries", "Claude", "gave"), ("owns", "Claude", "gave"))


class TheCard(NamesTest):
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
            [NewName("Claude", "npc", CONFIRMED), NewName("Belleros", "npc", CONFIRMED),
             NewName("Frost Brand", "item", CONFIRMED)],
            source="dm",
        )  # fmt: skip
        self.claude, self.bel, self.brand = made.value
        assert self.claude and self.bel and self.brand
        # "Claude found it" is something that happened: a hand-over never ends it (the owner)
        self.found = await self.tie(self.claude, FALLBACK_PREDICATE, detail="found")

    async def asyncTearDown(self) -> None:
        _ai_busy.clear()
        _ai_reads.clear()
        await super().asyncTearDown()

    async def tie(
        self, who: str | None, pred: str, *, detail: str = "", unrevealed: bool = True
    ) -> str:
        """A tie the lore gave, not said at the table yet (as a kept lore connection is)."""
        assert who and self.brand
        made = await self.memory.add_relation(
            GUILD, self.campaign.id, who, pred, self.brand, source="dm", confidence=1.0,
            status=CONFIRMED, detail=detail, unrevealed=unrevealed,
        )  # fmt: skip
        return str(made.value[0].id)

    async def read(self, *links: dict[str, Any]) -> None:
        ai = lore_fake.lore_ai(reply(*links))
        self.bot.background_ai = ai  # type: ignore[assignment]
        self.bot.background_read_ai = ai  # type: ignore[assignment]
        it = self.it()
        await ui_bg.offer(it, self.campaign.id, Upload(TEXT, "your background"))
        await it.response.sent[0][1]["view"]._read(self.it())

    async def card(self) -> Any:
        it = self.it(DM)
        self.assertTrue(await ui_bg.offer_ready(it, self.campaign.id))
        return it.followup.send.await_args.kwargs["view"]

    async def row(self, relation_id: str) -> Any:
        return next(
            r
            for r in await self.memory.relations(GUILD, self.campaign.id, include_secret=True)
            if r.id == relation_id
        )

    async def press(self, view: Any, label: str) -> None:
        await next(b for b in view.children if b.label == label).callback(self.it())

    async def test_keeping_a_sale_ends_the_sellers_part_with_the_reason(self) -> None:
        owns = await self.tie(self.claude, "owns")
        await self.read(handed("owns", "sold"))
        view = await self.card()
        self.assertIn("**Belleros** owns **Frost Brand** (Claude sold it)", view.text())
        self.assertIn("✅ Keep also means **Claude** no longer has it.", view.text())
        await self.press(view, "✅ Keep")
        got = await self.row(owns)
        self.assertIsNotNone(got.to_session_at)
        self.assertEqual(got.end_reason, "sold")
        self.assertIsNone((await self.row(self.found)).to_session_at)  # "found" stays
        # (no double asking: the DM's keep was the answer)
        self.assertEqual(
            await self.memory.hand_over_waiting(GUILD, self.campaign.id, source="dm"), []
        )

    async def test_a_theft_ends_the_holding_and_the_owner_still_owns_it(self) -> None:
        owns = await self.tie(self.claude, "owns")
        carries = await self.tie(self.claude, "carries")
        await self.read(handed("owns", "stolen"))
        view = await self.card()
        self.assertIn("**Belleros** carries **Frost Brand** (taken from Claude)", view.text())
        self.assertIn("no longer has it with them. Who owns it doesn't change.", view.text())
        await self.press(view, "✅ Keep")
        self.assertIsNone((await self.row(owns)).to_session_at)
        got = await self.row(carries)
        self.assertEqual((got.to_session_at is not None, got.end_reason), (True, "stolen"))

    async def test_turning_it_down_changes_nothing(self) -> None:
        owns = await self.tie(self.claude, "owns")
        await self.read(handed("owns", "sold"))
        await self.press(await self.card(), "❌ No")
        self.assertIsNone((await self.row(owns)).to_session_at)

    async def test_a_hidden_keep_never_ends_what_the_players_see(self) -> None:
        owns = await self.tie(self.claude, "owns", unrevealed=False)  # (said at the table)
        await self.read(handed("owns", "sold"))
        view = await self.card()
        self.assertIn("so to them **Claude** still has it", view.text())
        await self.press(view, "✅ Keep")
        self.assertIsNone((await self.row(owns)).to_session_at)

    async def test_keep_all_ends_it_and_undo_brings_it_back(self) -> None:
        owns = await self.tie(self.claude, "owns")
        await self.read(handed("owns", "sold"))
        waiting = await self.store.waiting(GUILD, self.campaign.id)
        self.assertEqual([(w.hand_over_from, w.hand_over) for w in waiting],
                         [(self.claude, "sold")])  # fmt: skip
        # Keep all never keeps it unseen (it ends what someone else holds): its own press does
        done = await keep.keep_all(self.memory, self.store, GUILD, self.campaign.id, waiting)
        self.assertEqual(done.kept, [])
        self.assertIsNone((await self.row(owns)).to_session_at)
        done = await keep.keep_all(
            self.memory, self.store, GUILD, self.campaign.id, waiting, one_press=True
        )
        self.assertEqual((await self.row(owns)).end_reason, "sold")
        await keep.undo_all(self.memory, self.store, GUILD, self.campaign.id, done)
        got = await self.row(owns)
        self.assertEqual((got.to_session_at, got.end_reason), (None, None))
        back = await self.store.waiting(GUILD, self.campaign.id)  # (back with its hand-over)
        self.assertEqual([(w.hand_over_from, w.hand_over) for w in back], [(self.claude, "sold")])

    async def test_an_upload_that_adds_by_itself_leaves_a_hand_over_waiting(self) -> None:
        self.campaign = await self.campaigns.set_ask_before_adding(GUILD, self.campaign.id, False)
        owns = await self.tie(self.claude, "owns")
        await self.read(handed("owns", "sold"))
        self.assertIsNone((await self.row(owns)).to_session_at)
        waiting = await self.store.waiting(GUILD, self.campaign.id)
        self.assertEqual([w.hand_over for w in waiting], ["sold"])

    async def test_the_one_before_follows_a_merge(self) -> None:
        await self.read(handed("owns", "sold"))
        made = await self.memory.add_names(
            GUILD, self.campaign.id, [NewName("Old Claude", "npc", CONFIRMED)], source="dm"
        )
        (kept,) = made.value
        assert kept and self.claude
        await self.memory.merge(GUILD, self.campaign.id, kept, self.claude, source="dm",
                                dm_said_same=True)  # fmt: skip
        (w,) = await self.store.waiting(GUILD, self.campaign.id)
        self.assertEqual((w.hand_over_from, w.hand_over_name, w.hand_over),
                         (kept, "Old Claude", "sold"))  # fmt: skip

    async def test_a_turned_down_or_merged_in_one_before_leaves_it_plain(self) -> None:
        await self.read(handed("owns", "sold"))
        assert self.claude and self.bel
        await self.memory.merge(GUILD, self.campaign.id, self.bel, self.claude, source="dm",
                                dm_said_same=True)  # fmt: skip
        self.assertEqual([w.hand_over for w in await self.store.waiting(GUILD, self.campaign.id)],
                         [""])  # fmt: skip

    async def test_a_turned_down_one_before_leaves_it_plain(self) -> None:
        await self.read(handed("owns", "sold"))
        assert self.claude
        await self.memory.set_entity_status(GUILD, self.campaign.id, self.claude, REJECTED,
                                            source="dm")  # fmt: skip
        self.assertEqual([w.hand_over for w in await self.store.waiting(GUILD, self.campaign.id)],
                         [""])  # fmt: skip

    async def test_the_table_takes_a_hand_over_whole_and_on_owns_or_carries_only(self) -> None:
        await self.read(handed("owns", "sold"))
        for change in ("hand_over_from = ''", "predicate = 'knows'"):
            with self.subTest(change), self.assertRaises(errors.CheckViolation):
                async with self.db.guild(GUILD) as conn:
                    await conn.execute(
                        f"UPDATE backstory_links SET {change} WHERE campaign_id = %s",
                        (self.campaign.id,),
                    )
