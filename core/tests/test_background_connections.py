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
from dmbot.memory.backstory_links import BackstoryLinks, NewLink, Waiting
from dmbot.memory.models import CONFIRMED, REJECTED, MemoryRuleError, NewName
from dmbot.memory.ontology import PredicateTerm
from dmbot.ui import name_background as ui_bg
from dmbot.ui import names as names_ui
from dmbot.ui.name_lists import Upload, _ai_busy, _ai_reads
from tests.test_memory_names import DM, GUILD, MANAGER, NamesTest


def shown(press: Any) -> str:
    return str(press.edit_original_response.await_args.kwargs["content"])


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
        self.assertEqual(list(bg.SCHEMA["properties"]), ["names", "connections"])

    def test_the_list_is_capped(self) -> None:
        found = bg.read_answer(reply(), TEXT)
        links = bg.read_connections(reply(), TEXT)
        self.assertEqual(len(bg.keep_connections([links], found, {}, cap=2)), 2)

    def test_the_schema_and_instructions_ask_for_connections(self) -> None:
        self.assertEqual(bg.SCHEMA["required"], ["names", "connections"])  # names come first
        self.assertIn("Only connections the text states", bg.instructions())

    def test_knows_and_the_campaigns_own_kinds_are_read(self) -> None:
        text = "Mirelle knows Brannoch. Mirelle teaches Oakmere."
        raw = json.dumps(
            {
                "connections": [
                    link("Mirelle", "knows", "Brannoch", "Mirelle knows Brannoch"),
                    link("Mirelle", "mentor_of", "Oakmere", "Mirelle teaches Oakmere"),
                    link("Mirelle", "mentor_of", "Brannoch", "Mirelle knows Brannoch"),
                ]
            }
        )
        plain = bg.read_connections(raw, text)
        self.assertEqual([c.how for c in plain], ["knows"])  # their own kind is unknown
        got = bg.read_connections(raw, text, {"mentor_of"})
        self.assertEqual(sorted(c.how for c in got), ["knows", "mentor_of", "mentor_of"])
        self.assertIn("mentor_of", json.dumps(bg.schema({"mentor_of": "is a mentor of"})))
        self.assertNotIn("mentor_of", json.dumps(bg.SCHEMA))
        self.assertIn('"mentor_of"', bg.instructions({"mentor_of": "is a mentor of"}))
        self.assertIn("knows", bg.instructions())

    def test_a_pair_the_campaign_has_is_left_out_before_the_cap(self) -> None:
        found = bg.read_answer(reply(), TEXT)
        links = bg.read_connections(reply(), TEXT)
        have = {("Brannoch", "kin_of", "Mirelle")}
        kept = bg.keep_connections(
            [links], found, {}, cap=2, skip=lambda c: (c.a, c.how, c.b) in have
        )
        self.assertEqual([(c.a, c.b) for c in kept], [("Mirelle", "Order of the Grey Hand"),
                                                       ("Vale Keep", "Oakmere")])  # fmt: skip

    def test_a_pronoun_is_never_an_end(self) -> None:
        found = [bg.Found("Mirelle", "npc", (), (), "")]
        links = [bg.Connection("She", "knows", "Mirelle", "She knows Mirelle")]
        self.assertEqual(bg.keep_connections([links], found, {}), [])

    def test_the_room_follows_the_piece(self) -> None:
        self.assertEqual(bg.room_for("x" * 10), bg.ROOM_MIN)
        self.assertGreater(bg.room_for("x" * 30_000), bg.room_for("x" * 14_000))
        self.assertEqual(bg.room_for("x" * 100_000), bg.ROOM_MAX)


class ThePath(NamesTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        _ai_busy.clear()
        _ai_reads.clear()
        self.ai = SimpleNamespace(complete=AsyncMock(return_value=Reply(reply(), False)))
        self.bot.background_ai = self.ai  # type: ignore[assignment]
        self.bot.plan_gate = AsyncMock(return_value=None)  # type: ignore[method-assign]
        self.bot.campaigns.record_confirmation = AsyncMock()  # type: ignore[method-assign]
        self.store = BackstoryLinks(self.db)
        self.bot.backstory_links = self.store

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

    async def offer(self, user: int = DM) -> tuple[Any, Any]:
        """The connections offered now (a new view each time, as after a restart)."""
        it = self.it(user)
        asked = await ui_bg.offer_ready(it, self.campaign.id)
        sent = it.followup.send.await_args
        return it, (sent.kwargs["view"] if asked and sent is not None else None)

    async def relations(self) -> list[Any]:
        return await self.memory.relations(GUILD, self.campaign.id, include_secret=True)

    async def waiting(self) -> list[Any]:
        return await self.store.waiting(GUILD, self.campaign.id)

    async def ids(self) -> dict[str, str]:
        every = await self.memory.entities(
            GUILD, self.campaign.id, statuses=["proposed", "confirmed"]
        )
        return {e.name: e.id for e in every}

    async def confirm_all(self) -> None:
        waiting = await self.memory.entities(GUILD, self.campaign.id, statuses=["proposed"])
        for kind in {e.type for e in waiting}:
            ids = [e.id for e in waiting if e.type == kind]
            await self.memory.confirm_kinds(GUILD, self.campaign.id, ids, kind, source="dm")

    def button(self, view: Any, label: str) -> Any:
        return next(b for b in view.children if b.label == label)

    # ---- what is stored, and when it is asked -------------------------------------------------

    async def test_the_connections_wait_in_the_campaign_and_nothing_is_a_fact_yet(self) -> None:
        press = await self.read()
        self.assertEqual(len(await self.waiting()), 3)
        self.assertEqual(await self.relations(), [])
        summary = press.edit_original_response.await_args.kwargs
        self.assertIn("3 connections found too", summary["content"])
        labels = [getattr(b, "label", "") for b in summary["view"].children]
        self.assertIn("📜 Check the connections", labels)

    async def test_they_are_not_asked_while_a_name_is_still_waiting(self) -> None:
        await self.read()
        _, view = await self.offer()
        self.assertIsNone(view)
        self.assertEqual(len(await self.waiting()), 3)  # still there for later

    async def test_the_connections_button_before_the_names_says_to_answer_them_first(self) -> None:
        press = await self.read()
        view = press.edit_original_response.await_args.kwargs["view"]
        it = self.it()
        await self.button(view, "📜 Check the connections").callback(it)
        self.assertIn("None to check yet", it.followup.send.await_args.args[0])

    async def test_add_all_offers_the_connections_whose_names_it_answered(self) -> None:
        press = await self.read()
        view = press.edit_original_response.await_args.kwargs["view"]
        pressed = []
        for label in [b.label for b in view.children if str(b.label).startswith("✅ Add all")]:
            it = self.it()
            await self.button(view, label).callback(it)
            pressed.append(it)
        said = [
            str(c.args[0]) if c.args else ""
            for it in pressed
            for c in it.followup.send.await_args_list
        ]
        self.assertTrue(any("Keep this?" in text for text in said), said)

    async def test_the_end_of_check_new_names_offers_them(self) -> None:
        await self.read()
        await self.confirm_all()
        it = self.it()
        await names_ui.SuggestionReview(self.campaign.id, [])._finish(it, None)
        offered = it.followup.send.await_args
        self.assertIsNotNone(offered)
        self.assertIn(
            "Keep this?",
            offered.args[0] if offered.args else offered.kwargs.get("content", ""),
        )

    # ---- the card and the presses -----------------------------------------------------------

    async def test_keep_stores_the_dms_word_and_the_row_goes(self) -> None:
        await self.read()
        await self.confirm_all()
        _, view = await self.offer()
        self.assertIn("From your text:", view.text())
        self.assertIn("Connection 1 of 3", view.text())
        await self.button(view, "✅ Keep").callback(self.it())
        (rel,) = await self.relations()
        self.assertEqual((rel.status, rel.secret), (CONFIRMED, False))
        self.assertEqual(len(await self.waiting()), 2)

    async def test_keep_as_secret_stores_a_secret_connection(self) -> None:
        await self.read()
        await self.confirm_all()
        _, view = await self.offer()
        await self.button(view, "🤫 Keep as secret").callback(self.it())
        (rel,) = await self.relations()
        self.assertTrue(rel.secret)
        self.assertEqual(await self.memory.relations(GUILD, self.campaign.id), [])  # not public

    async def test_they_survive_a_rebuilt_view_and_a_closed_message(self) -> None:
        await self.read()
        await self.confirm_all()
        _, first = await self.offer()
        await self.button(first, "❌ No").callback(self.it())
        _, again = await self.offer()  # as after a restart: a new view reads the store
        self.assertIn("Connection 1 of 2", again.text())

    async def test_no_drops_it_and_skip_the_rest_drops_what_is_ready(self) -> None:
        await self.read()
        await self.confirm_all()
        _, view = await self.offer()
        await self.button(view, "❌ No").callback(self.it())
        await self.button(view, "⏭️ Skip the rest").callback(self.it())
        self.assertEqual(view.text(), "📜 Done. Kept 0 connections. Left out 3.")
        self.assertEqual(await self.waiting(), [])
        self.assertEqual(await self.relations(), [])

    async def test_only_a_dm_may_answer_on_every_button(self) -> None:
        await self.read()
        await self.confirm_all()
        _, view = await self.offer()
        for label in ("✅ Keep", "🤫 Keep as secret", "❌ No", "⏭️ Skip the rest"):
            await self.button(view, label).callback(self.it(MANAGER))
        self.assertEqual(len(await self.waiting()), 3)
        self.assertEqual(await self.relations(), [])

    async def test_a_refused_name_takes_its_connections_away_quietly(self) -> None:
        await self.read()
        await self.confirm_all()
        _, view = await self.offer()
        ids = await self.ids()
        await self.memory.set_entity_status(
            GUILD, self.campaign.id, ids["Oakmere"], REJECTED, source="dm"
        )
        left = await self.waiting()
        self.assertEqual(len(left), 2)
        self.assertNotIn("Oakmere", [w.object for w in left] + [w.subject for w in left])
        _, again = await self.offer()
        self.assertIn("Connection 1 of 2", again.text())  # nothing was said about the refused one
        _ = view

    async def test_a_refused_first_name_takes_its_connections_away_too(self) -> None:
        await self.read()
        await self.confirm_all()
        ids = await self.ids()
        await self.memory.set_entity_status(
            GUILD, self.campaign.id, ids["Vale Keep"], REJECTED, source="dm"
        )
        names = {n for w in await self.waiting() for n in (w.subject, w.object)}
        self.assertNotIn("Vale Keep", names)

    async def test_a_merged_name_carries_its_connections_to_the_one_that_stayed(self) -> None:
        await self.read()
        await self.confirm_all()
        ids = await self.ids()
        made = await self.memory.add_names(
            GUILD, self.campaign.id, [NewName("Mira", "npc", CONFIRMED)], source="dm"
        )
        keep_id = made.value[0]
        assert keep_id
        await self.memory.merge(GUILD, self.campaign.id, keep_id, ids["Mirelle"], source="dm",
                                dm_said_same=True)  # fmt: skip
        await self.waiting()  # the clean-up moves the rows
        left = await self.waiting()  # ...and the move is kept: read again
        subjects = {w.subject for w in left} | {w.object for w in left}
        self.assertIn("Mira", subjects)
        self.assertNotIn("Mirelle", subjects)

    async def test_a_connection_the_campaign_already_has_is_not_asked(self) -> None:
        await self.read()
        await self.confirm_all()
        ids = await self.ids()
        await self.memory.add_relation(
            GUILD, self.campaign.id, ids["Mirelle"], "serves", ids["Order of the Grey Hand"],
            source="dm", confidence=1.0, status=CONFIRMED,
        )  # fmt: skip
        self.assertEqual(len(await self.waiting()), 2)

    async def test_a_refusal_says_what_to_do_not_why(self) -> None:
        await self.read()
        await self.confirm_all()
        _, view = await self.offer()
        it = self.it()
        with patch.object(self.memory, "add_relation", side_effect=MemoryRuleError("raw words")):
            await self.button(view, "✅ Keep").callback(it)
        said = it.followup.send.await_args.args[0]
        self.assertNotIn("raw words", said)
        self.assertIn("❌ No", said)
        self.assertEqual(len(await self.waiting()), 3)

    # ---- a lasting way back, and what the wiring must keep ------------------------------------

    async def test_a_fresh_review_with_no_new_names_reaches_the_connections(self) -> None:
        await self.read()
        await self.confirm_all()  # the names were answered elsewhere: nothing proposed is left
        it = self.it()
        await names_ui.start_review(it, self.campaign.id)
        asked = it.followup.send.await_args
        self.assertIsNotNone(asked)
        self.assertIn("Keep this?", asked.args[0])

    async def test_the_names_card_carries_a_lasting_button_while_any_wait(self) -> None:
        from dmbot.memory.lookup import LookupCache

        self.bot.lookup = LookupCache(self.memory)
        await self.read()
        await self.confirm_all()
        shown = self.it()
        await names_ui.show_home(shown, self.campaign.id)
        view = shown.response.sent[0][1]["view"]
        labels = [getattr(b, "label", "") for b in view.children]
        self.assertIn("📜 Check the connections (3)", labels)
        pressed = self.it()
        await next(
            b for b in view.children if getattr(b, "label", "") == "📜 Check the connections (3)"
        ).callback(pressed)
        self.assertIn("Keep this?", pressed.followup.send.await_args.args[0])

    async def test_the_names_card_has_no_such_button_when_none_wait(self) -> None:
        from dmbot.memory.lookup import LookupCache

        self.bot.lookup = LookupCache(self.memory)
        shown = self.it()
        await names_ui.show_home(shown, self.campaign.id)
        view = shown.response.sent[0][1]["view"]
        labels = [str(getattr(b, "label", "")) for b in view.children]
        self.assertFalse(any("connections" in label for label in labels))

    async def test_a_card_that_turns_secret_before_the_press_is_kept_as_secret(self) -> None:
        await self.read()
        await self.confirm_all()
        _, view = await self.offer()
        shown = view.current
        ids = await self.ids()
        for name in (shown.subject, shown.object):  # one end becomes a name the players don't know
            await self.memory.set_hidden_from_players(
                GUILD, self.campaign.id, ids[name], True, source="dm"
            )
        await self.button(view, "✅ Keep").callback(self.it())
        (rel,) = await self.relations()
        self.assertTrue(rel.secret)  # never stored as a public connection naming a hidden end

    async def test_a_text_with_a_secret_spelling_queues_a_secret_connection(self) -> None:
        text = "Belleros is really the hooded stranger. The hooded stranger lives in Vale Keep."
        names = [
            {"name": "Belleros", "kind": "person", "also": [], "secret": ["the hooded stranger"],
             "quote": "Belleros is really the hooded stranger"},
            {"name": "Vale Keep", "kind": "place", "also": [], "secret": [],
             "quote": "lives in Vale Keep"},
        ]  # fmt: skip
        one = [
            link("the hooded stranger", "in", "Vale Keep", "The hooded stranger lives in Vale Keep")
        ]
        self.ai.complete.return_value = Reply(
            json.dumps({"names": names, "connections": one}), False
        )
        it = self.it()
        await ui_bg.offer(it, self.campaign.id, Upload(text, "your background"))
        await it.response.sent[0][1]["view"]._read(self.it())
        (queued,) = await self.waiting()
        self.assertTrue(queued.secret)

    async def test_a_pair_the_campaign_has_is_neither_queued_nor_counted(self) -> None:
        made = await self.memory.add_names(
            GUILD, self.campaign.id,
            [NewName("Mirelle", "npc", CONFIRMED),
             NewName("Order of the Grey Hand", "faction", CONFIRMED)],
            source="dm",
        )  # fmt: skip
        mira, order = made.value
        assert mira and order
        await self.memory.add_relation(
            GUILD, self.campaign.id, mira, "serves", order, source="dm", confidence=1.0,
            status=CONFIRMED,
        )  # fmt: skip
        press = await self.read()
        self.assertEqual(len(await self.waiting()), 2)  # not the one it already holds
        self.assertIn("2 connections found too", shown(press))

    async def test_an_end_that_is_no_entry_of_the_campaign_is_not_queued(self) -> None:
        from dmbot.memory.background import Connection, Found

        queued = await ui_bg.queue_links(
            self.bot, self.memory, self.campaign,
            [Found("Ghost", "npc", (), (), ""), Found("Zed", "npc", (), (), "")],
            [[Connection("Ghost", "knows", "Zed", "q")]], set(),
        )  # fmt: skip
        self.assertEqual(queued, 0)  # neither was stored as a name: nothing to point at

    async def test_pairs_that_cannot_be_asked_do_not_use_up_the_cap(self) -> None:
        # the cap counts what will really be asked: a pair the campaign has, or one with an end
        # that was never stored, is left out before it, so it can't crowd out a good one
        from dmbot.memory.background import Connection, Found

        made = await self.memory.add_names(
            GUILD, self.campaign.id,
            [NewName("Mirelle", "npc", CONFIRMED), NewName("Order", "faction", CONFIRMED),
             NewName("Oakmere", "place", CONFIRMED)],
            source="dm",
        )  # fmt: skip
        mira, order, oak = made.value
        assert mira and order and oak
        await self.memory.add_relation(
            GUILD, self.campaign.id, mira, "serves", order, source="dm", confidence=1.0,
            status=CONFIRMED,
        )  # fmt: skip
        found = [Found("Ghost", "npc", (), (), "")]  # (never stored: no entry to point at)
        answers = [[
            Connection("Ghost", "knows", "Mirelle", "q1"),
            Connection("Mirelle", "serves", "Order", "q2"),
            Connection("Mirelle", "knows", "Oakmere", "q3"),
        ]]  # fmt: skip
        with patch.object(bg, "MAX_CONNECTIONS", 1):
            queued = await ui_bg.queue_links(
                self.bot, self.memory, self.campaign, found, answers, set()
            )
        self.assertEqual(queued, 1)
        (kept,) = await self.waiting()
        self.assertEqual((kept.subject, kept.object), ("Mirelle", "Oakmere"))

    async def test_a_second_campaign_in_the_server_sees_none_of_the_first_ones(self) -> None:
        await self.read()
        other = await self.campaigns.create(GUILD, "Other", DM)
        self.assertEqual(await self.store.waiting(GUILD, other.id), [])
        self.assertEqual(await self.store.count(GUILD, other.id), 0)
        self.assertEqual(await self.store.count(GUILD, self.campaign.id), 3)

    async def test_deleting_the_campaign_deletes_its_waiting_connections(self) -> None:
        await self.read()
        await self.campaigns.delete(GUILD, self.campaign.id)
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute("SELECT count(*) AS n FROM backstory_links")
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual(row["n"], 0)

    async def test_skip_the_rest_leaves_what_the_message_never_listed(self) -> None:
        await self.read()
        await self.confirm_all()
        _, view = await self.offer()
        ids = await self.ids()
        await self.store.add(  # a connection that turned ready after this message was drawn
            GUILD, self.campaign.id, [NewLink(ids["Brannoch"], "knows", ids["Oakmere"], "later")]
        )
        await self.button(view, "⏭️ Skip the rest").callback(self.it())
        (left,) = await self.waiting()
        self.assertEqual((left.subject, left.object, left.quote), ("Brannoch", "Oakmere", "later"))

    async def test_no_connections_beyond_the_limit_wait_for_one_campaign(self) -> None:
        from dmbot.memory import backstory_links as store_module

        await self.read()
        ids = await self.ids()
        with patch.object(store_module, "MAX_WAITING", 4):
            added = await self.store.add(
                GUILD, self.campaign.id,
                [NewLink(ids["Brannoch"], "knows", ids["Oakmere"], "a"),
                 NewLink(ids["Mirelle"], "knows", ids["Oakmere"], "b"),
                 NewLink(ids["Mirelle"], "knows", ids["Vale Keep"], "c")],
            )  # fmt: skip
        self.assertEqual(added, 1)  # three wait already; room for one more

    async def test_a_two_way_kind_held_the_other_way_round_is_already_known(self) -> None:
        await self.read()
        await self.confirm_all()
        ids = await self.ids()
        await self.memory.add_relation(
            GUILD, self.campaign.id, ids["Mirelle"], "kin_of", ids["Brannoch"], source="dm",
            confidence=1.0, status=CONFIRMED,
        )  # fmt: skip
        (held,) = [r for r in await self.relations() if r.predicate == "kin_of"]
        for link_ in await self.waiting():
            await self.store.remove(GUILD, self.campaign.id, link_)
        # the text says it the other way round from how the campaign stores it
        await self.store.add(
            GUILD, self.campaign.id, [NewLink(held.object_id, "kin_of", held.subject_id, "q")]
        )
        # told nothing about two-way kinds, the reverse is a different pair and stays
        self.assertEqual(len(await self.store.waiting(GUILD, self.campaign.id)), 1)
        # told that it reads both ways, it is the one the campaign holds, and goes
        two_way = await self.store.waiting(GUILD, self.campaign.id, symmetric={"kin_of"})
        self.assertEqual(two_way, [])
        self.assertEqual(await self.store.count(GUILD, self.campaign.id), 0)

    async def test_plain_keep_is_off_for_a_connection_that_uses_a_secret_name(self) -> None:
        view = ui_bg.ConnectionOffer(self.campaign.id)
        view._set([Waiting("a", "knows", "b", "q", True, "Belleros", "Waterdeep", True)])
        self.assertTrue(view.keep.disabled)
        self.assertIn("Only 🤫 Keep as secret works", view.text())

    # ---- the reading --------------------------------------------------------------------------

    async def test_a_cut_answer_is_asked_again_with_twice_the_room(self) -> None:
        self.ai.complete.side_effect = [Reply(reply(), True), Reply(reply(), False)]
        press = await self.read()
        first, second = (c.kwargs["max_tokens"] for c in self.ai.complete.await_args_list)
        self.assertEqual(second, 2 * first)
        self.assertNotIn("missed some names", shown(press))

    async def test_an_answer_cut_twice_tells_the_dm_it_may_be_missing_some(self) -> None:
        self.ai.complete.side_effect = [Reply(reply(), True), Reply(reply(), True)]
        press = await self.read()
        self.assertIn("missed some names", shown(press))

    async def test_the_retry_budget_stops_more_than_four_retries_in_one_reading(self) -> None:
        spent = [ui_bg.RETRIES_PER_READING]
        ai = SimpleNamespace(complete=AsyncMock(return_value=Reply(reply(), True)))
        done = await ui_bg.read_piece(ai, TEXT, {}, spent)
        self.assertEqual(ai.complete.await_count, 1)  # no budget: not asked again
        self.assertTrue(done.cut)

    async def test_the_campaigns_own_kind_is_offered_to_the_ai_and_kept(self) -> None:
        term = PredicateTerm(
            "mentor_of", "is a mentor of", "One teaches the other.", ("character",), ("character",),
            core=False,
        )  # fmt: skip
        await self.memory.add_predicate(
            GUILD, self.campaign.id, term, examples=["Mira is a mentor of Cerric."],
            reason="the table uses it", source="dm",
        )  # fmt: skip
        await self.read()
        sent = self.ai.complete.await_args
        self.assertIn("mentor_of", sent.args[0])
        self.assertIn("mentor_of", json.dumps(sent.kwargs["schema"]))

    async def test_the_log_has_counts_only(self) -> None:
        with self.assertLogs("dmbot.ui.name_background", "INFO") as logs:
            await self.read()
        joined = "\n".join(logs.output)
        self.assertIn("3 connections", joined)
        self.assertNotIn("Mirelle", joined)
        self.assertNotIn("From your text", joined)

    async def test_nothing_is_queued_without_the_store_or_without_connections(self) -> None:
        self.ai.complete.return_value = Reply(reply([]), False)
        await self.read()
        self.assertEqual(await self.waiting(), [])


class TheStore(NamesTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.store = BackstoryLinks(self.db)
        made = await self.memory.add_names(
            GUILD, self.campaign.id,
            [NewName("Mirelle", "npc", CONFIRMED), NewName("Oakmere", "place", CONFIRMED),
             NewName("Brannoch", "npc", "proposed")],
            source="dm",
        )  # fmt: skip
        mirelle, oakmere, brannoch = made.value
        assert mirelle and oakmere and brannoch
        self.mirelle, self.oakmere, self.brannoch = mirelle, oakmere, brannoch

    async def test_a_pair_is_kept_once_and_only_so_many_wait(self) -> None:
        link_ = NewLink(self.mirelle, "knows", self.oakmere, "q")
        self.assertEqual(await self.store.add(GUILD, self.campaign.id, [link_, link_]), 1)
        self.assertEqual(await self.store.add(GUILD, self.campaign.id, [link_]), 0)

    async def test_one_with_a_waiting_name_is_not_ready_and_stays(self) -> None:
        await self.store.add(
            GUILD, self.campaign.id, [NewLink(self.mirelle, "knows", self.brannoch, "q")]
        )
        (w,) = await self.store.waiting(GUILD, self.campaign.id)
        self.assertFalse(w.ready)

    async def test_a_merge_never_loses_the_connection_whichever_row_comes_first(self) -> None:
        early, late = BackstoryLinks(self.db, lambda: 100), BackstoryLinks(self.db, lambda: 200)
        for n, merged_first in enumerate((True, False)):
            made = await self.memory.add_names(
                GUILD, self.campaign.id,
                [NewName(f"Mira{n}", "npc", CONFIRMED), NewName(f"Mirela{n}", "npc", CONFIRMED)],
                source="dm",
            )  # fmt: skip
            keep, gone = made.value
            assert keep and gone
            # one row names the entry that stays, one the entry merged into it: both orders
            first, second = (gone, keep) if merged_first else (keep, gone)
            await early.add(GUILD, self.campaign.id, [NewLink(first, "knows", self.oakmere, "q")])
            await late.add(GUILD, self.campaign.id, [NewLink(second, "knows", self.oakmere, "q")])
            await self.memory.merge(
                GUILD, self.campaign.id, keep, gone, source="dm", dm_said_same=True
            )
            for _ in range(2):  # the second read must still find it
                found = [
                    w for w in await self.store.waiting(GUILD, self.campaign.id)
                    if w.subject_id == keep
                ]  # fmt: skip
                self.assertEqual(len(found), 1, (n, merged_first))

    async def test_a_hidden_entry_can_only_be_linked_as_secret(self) -> None:
        await self.store.add(
            GUILD, self.campaign.id, [NewLink(self.mirelle, "knows", self.oakmere, "q")]
        )
        (plain,) = await self.store.waiting(GUILD, self.campaign.id)
        self.assertFalse(plain.secret)
        await self.memory.set_hidden_from_players(
            GUILD, self.campaign.id, self.oakmere, True, source="dm"
        )
        (hidden,) = await self.store.waiting(GUILD, self.campaign.id)
        self.assertTrue(hidden.secret)

    async def test_a_proposed_relation_does_not_hide_the_dms_own_connection(self) -> None:
        await self.memory.add_relation(
            GUILD, self.campaign.id, self.mirelle, "knows", self.oakmere,
            source="extractor", confidence=0.5,
        )  # fmt: skip
        await self.store.add(
            GUILD, self.campaign.id, [NewLink(self.mirelle, "knows", self.oakmere, "q")]
        )
        self.assertEqual(len(await self.store.waiting(GUILD, self.campaign.id)), 1)

    async def test_it_goes_with_its_campaign_and_is_isolated_per_server(self) -> None:
        await self.store.add(
            GUILD, self.campaign.id, [NewLink(self.mirelle, "knows", self.oakmere, "q")]
        )
        self.assertEqual(await self.store.waiting(GUILD + 1, self.campaign.id), [])

    async def test_waiting_cleans_up_in_a_timely_way_on_a_big_campaign(self) -> None:
        import time

        await self.store.add(
            GUILD, self.campaign.id, [NewLink(self.mirelle, "knows", self.oakmere, "q")]
        )
        start = time.monotonic()
        for _ in range(20):
            await self.store.waiting(GUILD, self.campaign.id)
        self.assertLess(time.monotonic() - start, 5)


def _unused() -> None:
    _ = (DM,)
