"""Connections between names, read from a DM's own background (#2380): the checks that keep a link
the text does not state out (pure), then the whole path with a fake AI against Postgres: each
connection is offered after the names, kept only when the DM presses, as the DM's word, and never
without both names answered first. Invented names and text throughout."""

from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

from dmbot.ai import Reply
from dmbot.memory import background as bg
from dmbot.memory.models import CONFIRMED, MemoryRuleError
from dmbot.ui import name_background as ui_bg
from dmbot.ui.name_lists import Upload, _ai_busy, _ai_reads
from tests.test_memory_names import DM, GUILD, MANAGER, NamesTest

TEXT = (
    "Mirelle the Pale serves the Order of the Grey Hand. Brannoch is the brother of Mirelle, "
    "and Vale Keep sits in the hills near Oakmere."
)


def name(n: str, kind: str, quote: str, also: tuple[str, ...] = ()) -> dict[str, Any]:
    return {"name": n, "kind": kind, "also": list(also), "secret": [], "quote": quote}


def link(a: str, how: str, b: str, quote: str) -> dict[str, str]:
    return {"a": a, "how": how, "b": b, "quote": quote}


NAMES = [
    name("Mirelle", "person", "Mirelle the Pale serves", ("Mirelle the Pale",)),
    name("Order of the Grey Hand", "group", "the Order of the Grey Hand"),
    name("Brannoch", "person", "Brannoch is the brother of Mirelle"),
    name("Vale Keep", "place", "Vale Keep sits in the hills"),
    name("Oakmere", "place", "near Oakmere"),
]
LINKS = [
    link("Mirelle the Pale", "works for", "Order of the Grey Hand",
         "Mirelle the Pale serves the Order of the Grey Hand"),
    link("Brannoch", "family", "Mirelle", "Brannoch is the brother of Mirelle"),
    link("Vale Keep", "in", "Oakmere", "Vale Keep sits in the hills near Oakmere"),
    link("Brannoch", "owns", "Zorgul", "Brannoch owns Zorgul"),  # not in the text
    link("Brannoch", "enemy", "Oakmere", "Brannoch is the brother of Mirelle"),  # no Oakmere in it
    link("Brannoch", "enemy", "Mirelle", "Brannoch hates Mirelle"),  # a quote the text lacks
    link("Brannoch", "family", "Brannoch", "Brannoch is the brother of Mirelle"),  # itself
]  # fmt: skip


def reply(links: list[dict[str, str]] = LINKS) -> str:
    return json.dumps({"names": NAMES, "connections": links})


class TheChecks(unittest.TestCase):
    def test_only_connections_the_text_states_are_read(self) -> None:
        got = bg.read_connections(reply(), TEXT)
        self.assertEqual([(c.a, c.b) for c in got],
                         [("Mirelle the Pale", "Order of the Grey Hand"),
                          ("Brannoch", "Mirelle"), ("Vale Keep", "Oakmere")])  # fmt: skip

    def test_a_kind_dmbot_does_not_know_is_dropped(self) -> None:
        odd = [link("Brannoch", "adores", "Mirelle", "Brannoch is the brother of Mirelle")]
        self.assertEqual(bg.read_connections(reply(odd), TEXT), [])

    def test_a_garbled_answer_gives_nothing(self) -> None:
        self.assertEqual(bg.read_connections("not json", TEXT), [])
        self.assertEqual(bg.read_connections('{"connections": 5}', TEXT), [])

    def test_the_ends_must_be_names_dmbot_will_have(self) -> None:
        found = bg.read_answer(reply(), TEXT)
        links = bg.read_connections(reply(), TEXT)
        kept = bg.keep_connections([links], found, {})
        self.assertEqual(len(kept), 3)
        # "Mirelle the Pale" is Mirelle's other spelling: the end becomes the name itself.
        self.assertEqual(kept[0].a, "Mirelle")
        alone = bg.keep_connections([links], [f for f in found if f.name != "Oakmere"], {})
        self.assertEqual(len(alone), 2)  # an end that is no name at all is not offered

    def test_a_name_the_campaign_has_can_be_an_end(self) -> None:
        found = [f for f in bg.read_answer(reply(), TEXT) if f.name != "Oakmere"]
        kept = bg.keep_connections(
            [bg.read_connections(reply(), TEXT)], found, {"oakmere": "Oakmere"}
        )
        self.assertEqual(len(kept), 3)

    def test_the_same_pair_is_offered_once_either_way_round(self) -> None:
        both = [
            link("Brannoch", "family", "Mirelle", "Brannoch is the brother of Mirelle"),
            link("Mirelle", "family", "Brannoch", "Brannoch is the brother of Mirelle"),
        ]
        found = bg.read_answer(reply(), TEXT)
        self.assertEqual(len(bg.keep_connections([bg.read_connections(reply(both), TEXT)],
                                                 found, {})), 1)  # fmt: skip

    def test_a_shared_spelling_is_no_end_and_a_public_name_is_not_taken_over(
        self,
    ) -> None:
        found = [
            bg.Found("Mirelle", "npc", ("Vex",), (), ""),
            bg.Found("Brannoch", "npc", ("Vex",), (), ""),
            bg.Found("Vex", "npc", (), (), ""),
        ]
        links = [bg.Connection("Vex", "ally_of", "Brannoch", "Vex and Brannoch")]
        (kept,) = bg.keep_connections([links], found, {})
        # "Vex" is the third name's own: the link is not guessed away to Mirelle or Brannoch
        self.assertEqual((kept.a, kept.b), ("Vex", "Brannoch"))
        shared = [bg.Connection("Shade", "owns", "Oakmere", "Shade and Oakmere")]
        both = [bg.Found("Mirelle", "npc", ("Shade",), (), ""),
                bg.Found("Brannoch", "npc", ("Shade",), (), ""),
                bg.Found("Oakmere", "place", (), (), "")]  # fmt: skip
        self.assertEqual(bg.keep_connections([shared], both, {}), [])  # which one? not guessed
        (none,) = [bg.keep_connections([[bg.Connection("Vex", "owns", "Vex", "q")]], found, {})]
        self.assertEqual(none, [])

    def test_a_connection_found_through_a_secret_spelling_can_only_be_kept_as_secret(self) -> None:
        found = [bg.Found("Belleros", "npc", (), ("the hooded stranger",), ""),
                 bg.Found("Waterdeep", "place", (), (), "")]  # fmt: skip
        (secret,) = bg.keep_connections(
            [[bg.Connection("the hooded stranger", "located_in", "Waterdeep", "q")]], found, {}
        )
        (public,) = bg.keep_connections(
            [[bg.Connection("Belleros", "located_in", "Waterdeep", "q")]], found, {}
        )
        self.assertEqual((secret.a, secret.secret, public.secret), ("Belleros", True, False))

    def test_the_names_come_before_the_connections_in_the_answer(self) -> None:
        self.assertEqual(list(bg.SCHEMA["properties"]), ["names", "connections"])  # type: ignore[call-overload]

    def test_the_list_is_capped(self) -> None:
        found = bg.read_answer(reply(), TEXT)
        links = bg.read_connections(reply(), TEXT)
        self.assertEqual(len(bg.keep_connections([links], found, {}, cap=2)), 2)

    def test_the_schema_and_instructions_ask_for_connections(self) -> None:
        self.assertEqual(bg.SCHEMA["required"], ["names", "connections"])  # names come first
        self.assertIn("Only connections the text states", bg.instructions())


class ThePath(NamesTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        _ai_busy.clear()
        _ai_reads.clear()
        self.ai = SimpleNamespace(complete=AsyncMock(return_value=Reply(reply(), False)))
        self.bot.background_ai = self.ai  # type: ignore[assignment]
        self.bot.plan_gate = AsyncMock(return_value=None)  # type: ignore[method-assign]
        self.bot.campaigns.record_confirmation = AsyncMock()  # type: ignore[method-assign]

    async def asyncTearDown(self) -> None:
        _ai_busy.clear()
        _ai_reads.clear()
        await super().asyncTearDown()

    async def read(self) -> Any:
        it = self.it()
        await ui_bg.offer(it, self.campaign.id, Upload(TEXT, "your background"))
        press = self.it()
        await it.response.sent[0][1]["view"]._read(press)
        return press

    def offered(self, press: Any) -> Any:
        sent = press.followup.send.await_args
        return sent.kwargs["view"] if sent is not None else None

    async def relations(self) -> list[Any]:
        return await self.memory.relations(GUILD, self.campaign.id, include_secret=True)

    async def confirm_all(self) -> None:
        waiting = await self.memory.entities(GUILD, self.campaign.id, statuses=["proposed"])
        for kind in {e.type for e in waiting}:
            ids = [e.id for e in waiting if e.type == kind]
            await self.memory.confirm_kinds(GUILD, self.campaign.id, ids, kind, source="dm")

    async def test_the_connections_are_offered_and_nothing_is_stored_yet(self) -> None:
        press = await self.read()
        view = self.offered(press)
        self.assertIsNotNone(view)
        self.assertEqual(len(view.links), 3)
        self.assertEqual(await self.relations(), [])
        self.assertIn("Mirelle", view.text())
        self.assertIn("> Mirelle the Pale serves", view.text())

    async def test_keep_stores_the_dms_word_once_both_names_are_answered(self) -> None:
        view = self.offered(await self.read())
        await self.confirm_all()
        keep = next(b for b in view.children if b.label == "✅ Keep")
        await keep.callback(self.it())
        (rel,) = await self.relations()
        self.assertEqual((rel.predicate, rel.status, rel.secret), ("serves", CONFIRMED, False))
        self.assertEqual(len(view.links), 2)

    async def test_keep_as_secret_stores_a_secret_connection(self) -> None:
        view = self.offered(await self.read())
        await self.confirm_all()
        secret = next(b for b in view.children if b.label == "🤫 Keep as secret")
        it = self.it()
        await secret.callback(it)
        (rel,) = await self.relations()
        self.assertTrue(rel.secret)
        # Players never see it: the public read leaves it out.
        self.assertEqual(await self.memory.relations(GUILD, self.campaign.id), [])

    async def test_a_name_still_waiting_blocks_keep_and_the_connection_stays(self) -> None:
        view = self.offered(await self.read())
        keep = next(b for b in view.children if b.label == "✅ Keep")
        it = self.it()
        await keep.callback(it)
        self.assertEqual(await self.relations(), [])
        self.assertEqual(len(view.links), 3)
        self.assertIn("isn't added yet", it.followup.send.await_args.args[0])

    async def test_no_drops_it_and_skip_ends_it_without_storing(self) -> None:
        view = self.offered(await self.read())
        await next(b for b in view.children if b.label == "❌ No").callback(self.it())
        self.assertEqual(len(view.links), 2)
        await next(b for b in view.children if b.label == "⏭️ Skip remaining").callback(self.it())
        self.assertEqual(view.links, [])
        self.assertEqual(await self.relations(), [])

    async def test_only_a_dm_may_answer(self) -> None:
        view = self.offered(await self.read())
        await self.confirm_all()
        await next(b for b in view.children if b.label == "✅ Keep").callback(self.it(MANAGER))
        self.assertEqual(await self.relations(), [])
        self.assertEqual(len(view.links), 3)

    async def test_the_card_counts_what_is_left_and_the_end_says_what_was_left_out(
        self,
    ) -> None:
        view = self.offered(await self.read())
        self.assertIn("(3 left)", view.text())
        await next(b for b in view.children if b.label == "❌ No").callback(self.it())
        self.assertIn("(2 left)", view.text())
        await next(b for b in view.children if b.label == "⏭️ Skip remaining").callback(self.it())
        self.assertEqual(view.text(), "📜 Done. Kept 0 connections. Left out 3.")

    async def test_a_refused_connection_says_what_to_do_not_why(self) -> None:
        view = self.offered(await self.read())
        await self.confirm_all()
        it = self.it()
        with patch.object(self.memory, "add_relation", side_effect=MemoryRuleError("raw words")):
            await next(b for b in view.children if b.label == "✅ Keep").callback(it)
        said = it.followup.send.await_args.args[0]
        self.assertNotIn("raw words", said)
        self.assertIn("❌ No", said)

    async def test_plain_keep_is_off_for_a_connection_that_uses_a_secret_name(self) -> None:
        view = ui_bg.ConnectionOffer(
            self.campaign.id, [bg.Connection("Belleros", "located_in", "Waterdeep", "q", True)]
        )
        self.assertTrue(view.keep.disabled)
        self.assertIn("only be kept as secret", view.text())
        it = self.it()
        with patch.object(self.memory, "lookup_data", wraps=self.memory.lookup_data) as looked:
            await view.keep.callback(it)  # even if it is pressed, it is not even tried
        looked.assert_not_called()
        self.assertEqual(await self.relations(), [])
        self.assertEqual(len(view.links), 1)

    async def test_a_name_still_waiting_is_named_in_the_message(self) -> None:
        view = self.offered(await self.read())
        it = self.it()
        await next(b for b in view.children if b.label == "✅ Keep").callback(it)
        self.assertIn("Mirelle", it.followup.send.await_args.args[0])

    async def test_no_connections_means_no_second_message(self) -> None:
        self.ai.complete.return_value = Reply(reply([]), False)
        press = await self.read()
        self.assertIsNone(self.offered(press))

    async def test_the_log_has_counts_only(self) -> None:
        with self.assertLogs("dmbot.ui.name_background", "INFO") as logs:
            await self.read()
        joined = "\n".join(logs.output)
        self.assertIn("3 connections", joined)
        self.assertNotIn("Mirelle", joined)
        _ = DM
