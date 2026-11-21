"""A lore connection that says an item changed hands (#4115 slice 2; Supervisor's ruling,
2026-11-10): the reader keeps who had it before and how it moved, the card says it in brackets,
and keeping it ends what they held, as history with the reason. A theft gives the thief `carries`
only and the owner keeps owning; "found" never ends; turning it down changes nothing; a hidden
keep never ends what the players see. Invented names and text."""

from __future__ import annotations

import json
import unittest
from collections import Counter
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

from psycopg import errors

from dmbot.memory import background as bg
from dmbot.memory import backstory_keep as keep
from dmbot.memory.models import CONFIRMED, END_REASONS, REJECTED, MemoryRuleError, NewName, Relation
from dmbot.memory.models import DM as DM_SOURCE
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
        self.assertIn("Keeping it also means **Claude** no longer has it.", view.text())
        await self.press(view, "✅ Add it")
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
        await self.press(view, "✅ Add it")
        self.assertIsNone((await self.row(owns)).to_session_at)
        got = await self.row(carries)
        self.assertEqual((got.to_session_at is not None, got.end_reason), (True, "stolen"))

    async def test_turning_it_down_changes_nothing(self) -> None:
        owns = await self.tie(self.claude, "owns")
        await self.read(handed("owns", "sold"))
        await self.press(await self.card(), "❌ Leave it out")
        self.assertIsNone((await self.row(owns)).to_session_at)

    async def test_a_hidden_keep_never_ends_what_the_players_see(self) -> None:
        owns = await self.tie(self.claude, "owns", unrevealed=False)  # (said at the table)
        await self.read(handed("owns", "sold"))
        view = await self.card()
        self.assertIn("so to them **Claude** still has it", view.text())
        await self.press(view, "✅ Add it")
        self.assertIsNone((await self.row(owns)).to_session_at)

    async def keep_on_the_website(self, *, secret: bool = False) -> str:
        """The website's single ✅ Keep on the waiting hand-over (#4122): the bot's own answer."""
        from dmbot.memory import web_actions as wa
        from dmbot.memory.claims import ClaimStore

        onto = await self.memory.ontology(GUILD, self.campaign.id)
        symmetric = {k for k, p in onto.predicates.items() if p.symmetric}
        (link,) = [
            x for x in await self.store.waiting(GUILD, self.campaign.id, symmetric=symmetric)
            if x.hand_over
        ]  # fmt: skip

        async def everyone() -> set[int]:
            return {DM}

        action = wa.Action(
            "a" * 32, self.campaign.id, "", DM, "backstory-keep", target_id=link.id, flag=secret
        )
        waiting = wa.Waiting(claims=ClaimStore(self.memory), backstory=self.store,
                             opted_in=everyone)  # fmt: skip
        return (await wa.apply(self.memory, GUILD, {DM}, action, waiting)).error

    async def test_the_websites_keep_ends_the_sellers_part_as_discord_does(self) -> None:
        owns = await self.tie(self.claude, "owns")
        carries = await self.tie(self.claude, "carries")
        await self.read(handed("owns", "sold"))
        self.assertEqual(await self.keep_on_the_website(), "")
        for tie in (owns, carries):
            got = await self.row(tie)
            self.assertEqual((got.to_session_at is not None, got.end_reason), (True, "sold"))
        self.assertIsNone((await self.row(self.found)).to_session_at)  # "found" stays

    async def test_the_websites_hidden_keep_never_ends_what_the_players_see(self) -> None:
        owns = await self.tie(self.claude, "owns", unrevealed=False)  # (said at the table)
        await self.read(handed("owns", "sold"))
        self.assertEqual(await self.keep_on_the_website(secret=True), "")
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


SAMPLE = (
    "Claude Ambere carries a unique wolf figurine inlaid with black gemstones. Later, while the"
    " others rested, Claude sold a wolf figurine etched with conjuration runes to Belleros for 75"
    " gold. Belleros Stormgrace keeps the trading post."
)
SOLD = "Claude sold a wolf figurine etched with conjuration runes to Belleros for 75 gold"


class TheSampleSentence(NamesTest):
    """The sample lore's own case (#4170, invented text): the seller is stored by the whole name,
    "Claude Ambere", and the lore names only "Claude". The hand-over still finds the seller, and
    keeping it ends the carry the figurine's card showed."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        _ai_busy.clear()
        _ai_reads.clear()
        self.bot.plan_gate = AsyncMock(return_value=None)  # type: ignore[method-assign]
        self.bot.campaigns.record_confirmation = AsyncMock()  # type: ignore[method-assign]
        from dmbot.memory.backstory_links import BackstoryLinks

        self.bot.backstory_links = BackstoryLinks(self.db)
        made = await self.memory.add_names(
            GUILD, self.campaign.id,
            [NewName("Claude Ambere", "npc", CONFIRMED),
             NewName("Belleros Stormgrace", "npc", CONFIRMED),
             NewName("Wolf figurine", "item", CONFIRMED)],
            source="dm",
        )  # fmt: skip
        self.claude, self.bel, self.figurine = made.value
        assert self.claude and self.bel and self.figurine
        carried = await self.memory.add_relation(
            GUILD, self.campaign.id, self.claude, "carries", self.figurine, source="dm",
            confidence=1.0, status=CONFIRMED, unrevealed=True,
        )  # fmt: skip
        self.carries = str(carried.value[0].id)

    async def asyncTearDown(self) -> None:
        _ai_busy.clear()
        _ai_reads.clear()
        await super().asyncTearDown()

    async def test_the_first_name_finds_the_seller_and_keep_ends_the_carry(self) -> None:
        names = [
            name("Claude Ambere", "person", "Claude Ambere"),
            name("Belleros Stormgrace", "person", "Belleros Stormgrace keeps"),
            name("Wolf figurine", "item", "a unique wolf figurine"),
        ]
        sale = {"a": "Belleros Stormgrace", "how": "owns", "b": "Wolf figurine", "quote": SOLD,
                "from": "Claude", "change": "sold"}  # fmt: skip
        ai = lore_fake.lore_ai(json.dumps({"names": names, "connections": [sale]}))
        self.bot.background_ai = ai  # type: ignore[assignment]
        self.bot.background_read_ai = ai  # type: ignore[assignment]
        it = self.it()
        await ui_bg.offer(it, self.campaign.id, Upload(SAMPLE, "your background"))
        await it.response.sent[0][1]["view"]._read(self.it())
        dm = self.it(DM)
        self.assertTrue(await ui_bg.offer_ready(dm, self.campaign.id))
        view = dm.followup.send.await_args.kwargs["view"]
        self.assertIn("(Claude Ambere sold it)", view.text())
        await next(b for b in view.children if b.label == "✅ Add it").callback(self.it())
        row = next(
            r
            for r in await self.memory.relations(GUILD, self.campaign.id, include_secret=True)
            if r.id == self.carries
        )
        self.assertIsNotNone(row.to_session_at)  # (the card no longer shows Claude carrying it)
        self.assertEqual(row.end_reason, "sold")


class TheOrderOfKeeping(NamesTest):
    """Both lines from one upload, the sale kept before the carry (#4170; Supervisor's ruling,
    2026-11-21): the carry is kept already ended, with the sale's reason, on Discord's card and on
    a one-press keep alike; an Undo of the sale opens it again, and a redo ends it again."""

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
            [NewName("Claude Ambere", "npc", CONFIRMED),
             NewName("Belleros Stormgrace", "npc", CONFIRMED),
             NewName("Wolf figurine", "item", CONFIRMED)],
            source="dm",
        )  # fmt: skip
        claude, bel, figurine = made.value
        assert claude and bel and figurine
        self.claude, self.bel, self.figurine = claude, bel, figurine  # (typed str)
        names = [
            name("Claude Ambere", "person", "Claude Ambere"),
            name("Belleros Stormgrace", "person", "Belleros Stormgrace keeps"),
            name("Wolf figurine", "item", "a unique wolf figurine"),
        ]
        carry = {"a": "Claude Ambere", "how": "carries", "b": "Wolf figurine",
                 "quote": "Claude Ambere carries a unique wolf figurine"}  # fmt: skip
        sale = {"a": "Belleros Stormgrace", "how": "owns", "b": "Wolf figurine", "quote": SOLD,
                "from": "Claude", "change": "sold"}  # fmt: skip
        ai = lore_fake.lore_ai(json.dumps({"names": names, "connections": [carry, sale]}))
        self.bot.background_ai = ai  # type: ignore[assignment]
        self.bot.background_read_ai = ai  # type: ignore[assignment]
        it = self.it()
        await ui_bg.offer(it, self.campaign.id, Upload(SAMPLE, "your background"))
        await it.response.sent[0][1]["view"]._read(self.it())

    async def asyncTearDown(self) -> None:
        _ai_busy.clear()
        _ai_reads.clear()
        await super().asyncTearDown()

    async def rows(self) -> dict[str, Any]:
        rels = await self.memory.relations(GUILD, self.campaign.id, include_secret=True)
        return {r.predicate: r for r in rels if r.object_id == self.figurine}

    async def keep_one(self, sale_first: bool = True) -> list[int | None]:
        waiting = await self.store.waiting(GUILD, self.campaign.id)
        self.assertEqual(sorted(w.predicate for w in waiting), ["carries", "owns"])
        batches = []
        for w in sorted(
            waiting, key=lambda w: not w.hand_over if sale_first else bool(w.hand_over)
        ):
            done = await keep.keep_all(
                self.memory, self.store, GUILD, self.campaign.id, [w], one_press=True
            )
            batches.append(done.kept[0][1])
        return batches

    async def test_the_carry_kept_after_the_sale_is_kept_ended(self) -> None:
        await self.keep_one()
        got = await self.rows()
        self.assertEqual((got["owns"].hand_over, got["owns"].hand_over_from), ("sold", self.claude))
        self.assertIsNone(got["owns"].to_session_at)
        self.assertEqual(got["carries"].subject_id, self.claude)
        self.assertIsNotNone(got["carries"].to_session_at)  # (history: never shown as current)
        self.assertEqual(got["carries"].end_reason, "sold")

    async def test_the_other_order_is_as_before(self) -> None:
        await self.keep_one(sale_first=False)
        got = await self.rows()
        self.assertIsNotNone(got["carries"].to_session_at)  # (the sale ended it, #4115)
        self.assertEqual(got["carries"].end_reason, "sold")

    async def test_undo_of_the_sale_opens_the_carry_again_and_redo_ends_it(self) -> None:
        sale_batch, _carry = await self.keep_one()
        assert sale_batch is not None
        undone = await self.memory.undo(GUILD, self.campaign.id, sale_batch, source=DM_SOURCE)
        got = await self.rows()
        self.assertNotIn("owns", got)
        self.assertIsNone(got["carries"].to_session_at)  # (Supervisor's condition: symmetric)
        self.assertIsNone(got["carries"].end_reason)
        assert undone.batch is not None
        await self.memory.undo(GUILD, self.campaign.id, undone.batch, source=DM_SOURCE)  # (a redo)
        got = await self.rows()
        self.assertEqual(got["owns"].hand_over, "sold")
        self.assertEqual(got["carries"].end_reason, "sold")
        self.assertIsNotNone(got["carries"].to_session_at)

    async def test_discords_card_keeps_it_ended_too(self) -> None:
        # (the sale kept first by one press; the carry is then the only card, kept on Discord)
        waiting = await self.store.waiting(GUILD, self.campaign.id)
        sale = next(w for w in waiting if w.hand_over)
        await keep.keep_all(
            self.memory, self.store, GUILD, self.campaign.id, [sale], one_press=True
        )
        dm = self.it(DM)
        self.assertTrue(await ui_bg.offer_ready(dm, self.campaign.id))
        view = dm.followup.send.await_args.kwargs["view"]
        self.assertIn("**Claude Ambere** carries **Wolf figurine**", view.text())
        await next(b for b in view.children if b.label == "✅ Add it").callback(self.it())
        got = await self.rows()
        self.assertEqual(got["owns"].hand_over_from, self.claude)
        self.assertEqual(got["carries"].end_reason, "sold")
        self.assertIsNotNone(got["carries"].to_session_at)

    async def test_a_merge_of_the_seller_keeps_the_sale_theirs(self) -> None:
        # ("Claude" and "Claude Ambere" are one person: the kept sale follows the merge, so the
        # carry kept after it is still ended, and an Undo of the sale still opens it again)
        waiting = await self.store.waiting(GUILD, self.campaign.id)
        sale = next(w for w in waiting if w.hand_over)
        done = await keep.keep_all(
            self.memory, self.store, GUILD, self.campaign.id, [sale], one_press=True
        )
        other = (
            await self.memory.add_names(
                GUILD, self.campaign.id, [NewName("Old Clod", "npc", CONFIRMED)], source="dm"
            )
        ).value[0]
        assert other is not None
        await self.memory.merge(
            GUILD, self.campaign.id, other, self.claude, source=DM_SOURCE, dm_said_same=True
        )
        self.assertEqual((await self.rows())["owns"].hand_over_from, other)
        await self.memory.add_relation(
            GUILD, self.campaign.id, other, "carries", self.figurine, source=DM_SOURCE,
            confidence=1.0, status=CONFIRMED,
        )  # fmt: skip
        rels = await self.memory.relations(GUILD, self.campaign.id, include_secret=True)
        carry = next(r for r in rels if r.predicate == "carries")
        link = cast(Any, SimpleNamespace(
            hand_over="", predicate="carries", subject_id=other, object_id=self.figurine
        ))  # fmt: skip
        self.assertIsNotNone(keep.handed_away(link, rels))
        self.assertIsNone(carry.end_reason)  # (a plain add, not a keep: only the keep ends it)
        assert done.kept[0][1] is not None

    async def test_a_sale_ended_by_a_later_sale_still_took_it(self) -> None:
        # (Claude sold it to Belleros, who sold it on: Belleros's owning has ended, and Claude's
        # carry, kept last, is still ended)
        waiting = await self.store.waiting(GUILD, self.campaign.id)
        sale = next(w for w in waiting if w.hand_over)
        carry = next(w for w in waiting if not w.hand_over)
        await keep.keep_all(
            self.memory, self.store, GUILD, self.campaign.id, [sale], one_press=True
        )
        owns = (await self.rows())["owns"]
        await self.memory.update_relation(
            GUILD, self.campaign.id, owns.id, source=DM_SOURCE, to_session_at=owns.created_at + 5,
            end_reason="sold",
        )  # fmt: skip
        await keep.keep_all(
            self.memory, self.store, GUILD, self.campaign.id, [carry], one_press=True
        )
        self.assertEqual((await self.rows())["carries"].end_reason, "sold")

    async def test_a_half_hand_over_is_refused_everywhere(self) -> None:
        cases: tuple[dict[str, Any], ...] = (
            {"hand_over": "sold"},
            {"hand_over_from": self.claude},
            {"hand_over": "moved", "hand_over_from": self.claude},
            {"end_reason": "sold"},  # (an end reason with no end)
        )
        for kwargs in cases:
            with self.subTest(kwargs), self.assertRaises(ValueError):
                await self.memory.add_relation(
                    GUILD, self.campaign.id, self.bel, "owns", self.figurine, source=DM_SOURCE,
                    confidence=1.0, status=CONFIRMED, **kwargs,
                )  # fmt: skip
        with self.assertRaises(MemoryRuleError):  # (only owning or holding moves by hand)
            await self.memory.add_relation(
                GUILD, self.campaign.id, self.bel, "knows", self.claude, source=DM_SOURCE,
                confidence=1.0, status=CONFIRMED, hand_over="sold", hand_over_from=self.claude,
            )  # fmt: skip
        await self.memory.add_relation(
            GUILD, self.campaign.id, self.bel, "knows", self.claude, source=DM_SOURCE,
            confidence=1.0, status=CONFIRMED,
        )  # fmt: skip
        async with self.db.guild(GUILD) as conn:  # (and the database says the same)
            with self.assertRaises(errors.CheckViolation):
                await conn.execute(
                    "UPDATE memory_relations SET hand_over = 'sold' WHERE campaign_id = %s",
                    (self.campaign.id,),
                )

    async def test_a_sale_the_players_dont_know_never_ends_a_carry_they_see(self) -> None:
        # (the sale is kept held back, as lore is; the carry shown to the players: ending it would
        # tell them the item moved, so it stays, and the DM is asked "Did this change?")
        from dmbot.memory import secret_questions

        waiting = await self.store.waiting(GUILD, self.campaign.id)
        sale = next(w for w in waiting if w.hand_over)
        carry = next(w for w in waiting if not w.hand_over)
        await keep.keep_all(
            self.memory, self.store, GUILD, self.campaign.id, [sale], one_press=True
        )
        self.assertTrue((await self.rows())["owns"].unrevealed)
        await keep.keep_all(
            self.memory, self.store, GUILD, self.campaign.id, [carry], one_press=True,
            players="show",
        )  # fmt: skip
        got = await self.rows()
        self.assertIsNone(got["carries"].to_session_at)
        self.assertFalse(got["carries"].unrevealed)
        async with self.db.guild(GUILD) as conn:
            asked = await secret_questions.waiting(conn, GUILD, self.campaign.id)
        self.assertIn(got["carries"].id, [q.item_id for q in asked])


class TheLateEnd(unittest.TestCase):
    """`handed_away` and `late_end` (#4170), pure: which sale took the item, and when the tie kept
    after it ends."""

    def sale(self, how: str, *, hidden: bool = False, at: int | None = None) -> Relation:
        return Relation(
            "1" * 32, "b" * 32, "owns", "f" * 32, "", 1.0, CONFIRMED, DM_SOURCE, (), at, None,
            None, None, False, 5, unrevealed=hidden, hand_over=how, hand_over_from="c" * 32,
        )  # fmt: skip

    def link(self, predicate: str) -> Any:
        return SimpleNamespace(
            hand_over="", predicate=predicate, subject_id="c" * 32, object_id="f" * 32
        )

    def test_a_theft_takes_only_the_carry(self) -> None:
        self.assertIsNotNone(keep.handed_away(self.link("owns"), [self.sale("sold")]))
        self.assertIsNone(keep.handed_away(self.link("owns"), [self.sale("stolen")]))
        self.assertIsNotNone(keep.handed_away(self.link("carries"), [self.sale("stolen")]))
        self.assertIsNone(keep.handed_away(self.link("knows"), [self.sale("sold")]))

    def test_the_end_is_the_sales_start_and_never_before_the_tie_starts(self) -> None:
        self.assertEqual(
            keep.late_end(
                self.link("carries"), [self.sale("sold", at=40)], hidden=True, start=None
            ),
            (40, "sold", None),
        )
        at, why, _ = keep.late_end(
            self.link("carries"), [self.sale("sold", at=40)], hidden=True, start=90
        )
        self.assertEqual((at, why), (91, "sold"))  # (after it starts: the span check holds)

    def test_a_hidden_sale_asks_about_a_shown_tie(self) -> None:
        sale = self.sale("sold", hidden=True)
        self.assertEqual(
            keep.late_end(self.link("carries"), [sale], hidden=False, start=None), (5, None, sale)
        )
        self.assertEqual(
            keep.late_end(self.link("carries"), [sale], hidden=True, start=None), (5, "sold", None)
        )

    def test_the_backup_reads_the_two_columns(self) -> None:
        from dmbot.memory import backup

        self.assertTrue(backup._valid("hand_over_from", ""))
        self.assertTrue(backup._valid("hand_over_from", "c" * 32))
        self.assertFalse(backup._valid("hand_over_from", "Claude"))
        self.assertTrue(backup._valid("hand_over", "sold"))
        self.assertFalse(backup._valid("hand_over", "moved"))
