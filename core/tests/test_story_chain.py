"""Where the story is, in a lore upload (#4173 slice 3; Supervisor's rulings on #4173, 2026-11-13):
each place's parent assumed from the nearest bigger place still open when the text first names it,
waiting as a link that is never a question, kept by itself once both places are kept, marked
"(assumed)" on the DM's card only, never flagged, replaced by a stated parent, and confirmed by the
DM's ✅ That's right. Invented names only."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any

from dmbot.memory import story_chain
from dmbot.memory.backstory_keep import keep_assumed
from dmbot.memory.backstory_links import BackstoryLinks, NewLink
from dmbot.memory.gate import CONNECTION
from dmbot.memory.lookup import LookupCache
from dmbot.memory.models import CONFIRMED, PROPOSED, REJECTED, MemoryRuleError
from dmbot.memory.models import DM as BY_DM
from dmbot.ui import name_card
from tests.test_memory_names import DM as NAMES_DM
from tests.test_memory_names import GUILD as NAMES_GUILD
from tests.test_memory_names import MANAGER, PLAYER, NamesTest
from tests.test_memory_store import DM, GUILD_A, GUILD_B, MemoryTest

P = story_chain.Place


def chain(text: str, *places: P, stated: tuple[str, ...] = ()) -> list[tuple[str, str]]:
    return story_chain.assumed_parents(text, places, set(stated))


class TheChain(unittest.TestCase):
    def test_each_place_sits_in_the_nearest_bigger_open_place(self) -> None:
        text = (
            "Icewind Dale is cold.\n\nIn Kelvenmoor the boats come in.\n\n"
            "The Gull's Rest stands by the docks. Its back room smells of fish."
        )
        got = chain(
            text,
            P("dale", "region", ("Icewind Dale",)),
            P("town", "settlement", ("Kelvenmoor",)),
            P("inn", "building", ("The Gull's Rest", "Gull's Rest")),
            P("room", "room", ("back room",)),
        )
        self.assertEqual(got, [("town", "dale"), ("inn", "town"), ("room", "inn")])

    def test_the_order_is_the_texts_not_the_listing(self) -> None:
        text = "Kelvenmoor sits in the dale. Later, Brindleford."
        places = (
            P("other", "settlement", ("Brindleford",)),
            P("town", "settlement", ("Kelvenmoor",)),
            P("inn", "building", ("Gull's Rest",)),
        )
        text += " Gull's Rest is busy."
        # (the inn is named after Brindleford: it is in the town open then, not the first one)
        self.assertEqual(chain(text, *places), [("inn", "other")])

    def test_a_new_place_closes_its_own_size_and_smaller(self) -> None:
        text = "In Kelvenmoor, the Gull's Rest. Then Brindleford. The Lamp is there."
        got = chain(
            text,
            P("town", "settlement", ("Kelvenmoor",)),
            P("inn", "building", ("Gull's Rest",)),
            P("other", "settlement", ("Brindleford",)),
            P("lamp", "building", ("The Lamp",)),
        )
        self.assertEqual(got, [("inn", "town"), ("lamp", "other")])

    def test_a_stated_place_gets_none_but_opens_its_size(self) -> None:
        text = "Kelvenmoor. The Gull's Rest. Its back room."
        got = chain(
            text,
            P("town", "settlement", ("Kelvenmoor",)),
            P("inn", "building", ("Gull's Rest",)),
            P("room", "room", ("back room",)),
            stated=("inn",),
        )
        self.assertEqual(got, [("room", "inn")])

    def test_a_list_opens_nothing_and_assumes_nothing(self) -> None:
        text = (
            "Icewind Dale.\n\nThe towns are Kelvenmoor, Brindleford and Easthaven.\n\n"
            "The Gull's Rest is busy."
        )
        got = chain(
            text,
            P("dale", "region", ("Icewind Dale",)),
            P("a", "settlement", ("Kelvenmoor",)),
            P("b", "settlement", ("Brindleford",)),
            P("c", "settlement", ("Easthaven",)),
            P("inn", "building", ("Gull's Rest",)),
        )
        # (none of the three towns is assumed in the dale, and the inn is in no town: only the dale)
        self.assertEqual(got, [("inn", "dale")])

    def test_no_size_no_part(self) -> None:
        text = "Kelvenmoor. The Gull's Rest."
        got = chain(text, P("town", "", ("Kelvenmoor",)), P("inn", "building", ("Gull's Rest",)))
        self.assertEqual(got, [])

    def test_nothing_bigger_open_is_no_guess(self) -> None:
        self.assertEqual(chain("The Gull's Rest.", P("inn", "building", ("Gull's Rest",))), [])


class Stored(MemoryTest):
    async def place(self, name: str, level: str) -> str:
        made = await self.add(name, type="place", status=CONFIRMED)
        await self.memory.set_place_level(GUILD_A, self.c, made, level, source=BY_DM)
        return made

    async def assume(self, child: str, parent: str) -> str:
        written = await self.memory.add_relation(
            GUILD_A, self.c, child, "located_in", parent,
            source=BY_DM, confidence=1.0, status=CONFIRMED, assumed=True,
        )  # fmt: skip
        return str(written.value[0].id)

    async def row(self, relation_id: str) -> Any:
        """The connection as stored, a rejected one too."""
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute(
                "SELECT status, assumed, unrevealed, to_session_at FROM memory_relations"
                " WHERE id = %s",
                (relation_id,),
            )
            row = await cur.fetchone()
        assert row is not None
        return SimpleNamespace(**row)

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.town = await self.place("Kelvenmoor", "settlement")
        self.other = await self.place("Brindleford", "settlement")
        self.inn = await self.place("Gull's Rest", "building")


class AnAssumedParent(Stored):
    async def test_kept_held_back_and_marked(self) -> None:
        r = await self.row(await self.assume(self.inn, self.town))
        self.assertEqual((r.assumed, r.unrevealed, r.status), (True, True, CONFIRMED))

    async def test_a_stated_parent_replaces_it_with_one_undo(self) -> None:
        guess = await self.assume(self.inn, self.town)
        said = await self.memory.add_relation(
            GUILD_A, self.c, self.inn, "located_in", self.other,
            source=BY_DM, confidence=1.0, status=CONFIRMED,
        )  # fmt: skip
        self.assertEqual(said.value[1], [])  # (no flag)
        self.assertEqual((await self.row(guess)).status, REJECTED)
        self.assertIsNone((await self.row(guess)).to_session_at)  # (no "used to be in" line)
        flags = await self.memory.flags(GUILD_A, self.c)
        self.assertEqual(flags, [])
        assert said.batch is not None
        await self.memory.undo(GUILD_A, self.c, said.batch)
        self.assertEqual((await self.row(guess)).status, CONFIRMED)

    async def test_the_same_parent_said_keeps_it_and_clears_the_mark(self) -> None:
        guess = await self.assume(self.inn, self.town)
        await self.memory.add_relation(
            GUILD_A, self.c, self.inn, "located_in", self.town,
            source=BY_DM, confidence=1.0, status=CONFIRMED,
        )  # fmt: skip
        r = await self.row(guess)
        self.assertEqual((r.assumed, r.status), (False, CONFIRMED))

    async def test_never_over_a_stated_parent(self) -> None:
        await self.memory.add_relation(
            GUILD_A, self.c, self.inn, "located_in", self.other,
            source=BY_DM, confidence=1.0, status=CONFIRMED,
        )  # fmt: skip
        with self.assertRaises(MemoryRuleError):
            await self.assume(self.inn, self.town)

    async def test_never_flags_and_is_never_a_rival(self) -> None:
        guess = await self.assume(self.inn, self.town)
        await self.memory.add_relation(
            GUILD_A, self.c, self.inn, "located_in", self.other,
            source="background", confidence=0.9, status=PROPOSED,
        )  # fmt: skip
        self.assertEqual(await self.memory.flags(GUILD_A, self.c), [])
        self.assertEqual((await self.row(guess)).status, CONFIRMED)  # (a proposal replaces nothing)

    async def test_only_a_place_in_a_place(self) -> None:
        npc = await self.add("Tolly", type="npc", status=CONFIRMED)
        with self.assertRaises(ValueError):
            await self.memory.add_relation(
                GUILD_A, self.c, npc, "located_in", self.town,
                source=BY_DM, confidence=1.0, status=CONFIRMED, assumed=True,
            )  # fmt: skip

    async def test_thats_right_clears_the_mark_and_only_the_dm(self) -> None:
        guess = await self.assume(self.inn, self.town)
        with self.assertRaises(MemoryRuleError):
            await self.memory.confirm_assumed(GUILD_A, self.c, guess, source="background")
        done = await self.memory.confirm_assumed(GUILD_A, self.c, guess, source=BY_DM)
        self.assertFalse(done.value.assumed)
        self.assertTrue(done.value.unrevealed)  # (then the usual rule: held back until said)
        assert done.batch is not None
        await self.memory.undo(GUILD_A, self.c, done.batch)
        self.assertTrue((await self.row(guess)).assumed)

    async def test_the_dms_reveal_says_its_right(self) -> None:
        guess = await self.assume(self.inn, self.town)
        for e in (self.inn, self.town):
            await self.memory.set_hidden_from_players(GUILD_A, self.c, e, False, source=BY_DM)
        await self.memory.update_relation(GUILD_A, self.c, guess, source=BY_DM, unrevealed=False)
        r = await self.row(guess)
        self.assertEqual((r.assumed, r.unrevealed), (False, False))

    async def test_no_character_learns_it_on_the_way(self) -> None:
        guess = await self.assume(self.inn, self.town)
        hero = await self.add("Bram", type="character", status=CONFIRMED, role="player_character")
        with self.assertRaises(MemoryRuleError):
            await self.memory.learn_statement(
                GUILD_A, self.c, CONNECTION, guess, hero, source=BY_DM
            )
        # the DM's own "who knows" list is their word on it: the mark goes
        await self.memory.set_statement_knowers(
            GUILD_A, self.c, CONNECTION, guess, [hero], source=BY_DM
        )
        self.assertFalse((await self.row(guess)).assumed)

    async def test_a_backup_carries_the_mark_and_an_old_one_reads_stated(self) -> None:
        from dmbot.campaigns.store import decode_backup, encode_backup

        await self.assume(self.inn, self.town)
        raw = encode_backup(await self.campaigns.export(GUILD_A, self.c))
        restored = await self.campaigns.import_backup(GUILD_B, decode_backup(raw), DM)
        copy = await self.snapshot(GUILD_B, restored.id)
        self.assertEqual([r["assumed"] for r in copy["memory_relations"]], [True])
        data = decode_backup(raw)
        assert isinstance(data, dict)
        for row in data["sections"]["memory"]:
            row.pop("assumed", None)  # (written before 0462)
        older = await self.campaigns.import_backup(GUILD_B, data, DM)
        copy = await self.snapshot(GUILD_B, older.id)
        self.assertEqual([r["assumed"] for r in copy["memory_relations"]], [False])


class KeptByItself(Stored):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.links = BackstoryLinks(self.db)

    async def wait(self, child: str, parent: str) -> None:
        await self.links.add(
            GUILD_A, self.c, [NewLink(child, "located_in", parent, "", assumed=True)]
        )

    async def test_never_a_question_and_kept_once_both_are_kept(self) -> None:
        cellar = await self.add("Cellar", type="place", status=PROPOSED)
        await self.wait(cellar, self.inn)
        await self.wait(self.inn, self.town)
        self.assertEqual(await self.links.waiting(GUILD_A, self.c), [])
        self.assertEqual(await self.links.count(GUILD_A, self.c), 0)
        self.assertEqual(await keep_assumed(self.memory, self.links, GUILD_A, self.c), 1)
        rows = await self.memory.relations(GUILD_A, self.c, include_secret=True)
        self.assertEqual(
            [(r.subject_id, r.object_id, r.assumed) for r in rows],
            [(self.inn, self.town, True)],
        )
        # (the cellar's waits on its name)
        self.assertEqual(len(await self.links.waiting(GUILD_A, self.c, assumed=True)), 1)

    async def test_a_turned_down_place_takes_it_away(self) -> None:
        cellar = await self.add("Cellar", type="place", status=PROPOSED)
        await self.wait(cellar, self.inn)
        await self.memory.set_entity_status(GUILD_A, self.c, cellar, REJECTED, source=BY_DM)
        await keep_assumed(self.memory, self.links, GUILD_A, self.c)
        self.assertEqual(await self.links.waiting(GUILD_A, self.c, assumed=True), [])

    async def test_a_parent_said_meanwhile_drops_the_guess(self) -> None:
        await self.memory.add_relation(
            GUILD_A, self.c, self.inn, "located_in", self.other,
            source=BY_DM, confidence=1.0, status=CONFIRMED,
        )  # fmt: skip
        await self.wait(self.inn, self.town)
        self.assertEqual(await keep_assumed(self.memory, self.links, GUILD_A, self.c), 0)
        self.assertEqual(await self.links.waiting(GUILD_A, self.c, assumed=True), [])

    async def test_a_text_that_states_it_makes_the_waiting_one_a_question(self) -> None:
        await self.wait(self.inn, self.town)
        await self.links.add(
            GUILD_A, self.c, [NewLink(self.inn, "located_in", self.town, "It's in Kelvenmoor.")]
        )
        self.assertEqual(len(await self.links.waiting(GUILD_A, self.c)), 1)


class OnTheCard(NamesTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.bot.lookup = LookupCache(self.memory)
        g, c = NAMES_GUILD, self.campaign.id
        ids = []
        for name, level in (("Kelvenmoor", "settlement"), ("Gull's Rest", "building")):
            made = await self.memory.add_entity(
                g, c, name=name, type="place", source=BY_DM, status=CONFIRMED
            )
            await self.memory.set_place_level(g, c, made.value.id, level, source=BY_DM)
            await self.memory.set_hidden_from_players(g, c, made.value.id, False, source=BY_DM)
            ids.append(made.value.id)
        self.town, self.inn = ids
        written = await self.memory.add_relation(
            g, c, self.inn, "located_in", self.town,
            source=BY_DM, confidence=1.0, status=CONFIRMED, assumed=True,
        )  # fmt: skip
        self.guess = written.value[0].id

    async def card(self, user: int = NAMES_DM, entity: str | None = None) -> tuple[str, Any]:
        self.bot.lookup.mark_all_stale()  # type: ignore[union-attr]
        it = self.it(user)
        await name_card.show_card(it, self.campaign.id, entity or self.inn)
        sent = it.response.sent[0]
        return str(sent[0]), sent[1].get("view")

    def labels(self, view: Any) -> list[str]:
        return [str(getattr(c, "label", "")) for c in view.children] if view else []

    async def test_the_dm_sees_the_mark_and_says_its_right(self) -> None:
        text, view = await self.card()
        self.assertIn("**Kelvenmoor** (assumed)", text)
        self.assertIn(name_card.RIGHT_BUTTON, self.labels(view))
        town_text, town_view = await self.card(entity=self.town)
        self.assertIn("**Gull's Rest** inside it (assumed)", town_text)  # (the other side too)
        self.assertNotIn(name_card.RIGHT_BUTTON, self.labels(town_view))  # (on the place's own)
        it = self.it()
        await view._assumed_right(it)
        self.assertIn("📍 Noted: **Gull's Rest** is in **Kelvenmoor**.", it.response.edited[-1][0])
        text, view = await self.card()
        self.assertNotIn("(assumed)", text)
        self.assertNotIn(name_card.RIGHT_BUTTON, self.labels(view))

    async def test_a_player_never_sees_it(self) -> None:
        text, _ = await self.card(PLAYER)
        self.assertNotIn("Kelvenmoor", text)
        self.assertNotIn("assumed", text)

    async def test_a_player_cannot_say_its_right(self) -> None:
        it = self.it(PLAYER)
        await name_card.confirm_assumed(it, self.campaign.id, self.inn, self.guess)
        self.assertIn("Only", it.response.sent[-1][0])  # (refused before anything changes)
        manager = self.it(MANAGER)  # (a manager who is not a DM may open the card, not say this)
        await name_card.confirm_assumed(manager, self.campaign.id, self.inn, self.guess)
        self.assertEqual(manager.response.sent[-1][0], name_card.ONLY_DM_RIGHT)
        rows = await self.memory.relations(NAMES_GUILD, self.campaign.id, include_secret=True)
        self.assertTrue(next(r for r in rows if r.id == self.guess).assumed)


class TheReader(NamesTest):
    """The lore reader's own queue (`queue_links`): the chain is made from this upload's words."""

    async def asyncSetUp(self) -> None:
        from dmbot.memory.models import NewName

        await super().asyncSetUp()
        self.store = BackstoryLinks(self.db)
        self.bot.backstory_links = self.store
        self.bot.lookup = LookupCache(self.memory)
        await self.memory.add_names(
            NAMES_GUILD, self.campaign.id,
            [NewName("Kelvenmoor", "place", CONFIRMED, place_level="settlement"),
             NewName("Gull's Rest", "place", CONFIRMED, place_level="building"),
             NewName("Brindleford", "place", CONFIRMED, place_level="settlement")],
            source="dm",
        )  # fmt: skip
        self.ids = {e.name: e.id for e in await self.memory.entities(NAMES_GUILD, self.campaign.id)}

    async def queue(self, text: str, links: list[Any] | None = None) -> list[tuple[str, str]]:
        from dmbot.memory import background as bg
        from dmbot.ui import name_background as ui_bg

        found = [bg.Found(n, "place", (), (), "") for n in ("Kelvenmoor", "Gull's Rest")]
        self.queued = await ui_bg.queue_links(
            self.bot, self.memory, self.campaign, found, [links or []], set(), keys=set(),
            text=text,
        )  # fmt: skip
        names = {v: k for k, v in self.ids.items()}
        waiting = await self.store.waiting(NAMES_GUILD, self.campaign.id, assumed=True)
        return [(names[w.subject_id], names[w.object_id]) for w in waiting]

    async def test_an_upload_assumes_and_asks_nothing(self) -> None:
        got = await self.queue("Kelvenmoor is a fishing town. The Gull's Rest is by the water.")
        self.assertEqual(self.queued, 0)  # (nothing counted as a connection to ask about)
        self.assertEqual(got, [("Gull's Rest", "Kelvenmoor")])
        self.assertEqual(await self.store.waiting(NAMES_GUILD, self.campaign.id), [])
        self.assertEqual(await self.store.count(NAMES_GUILD, self.campaign.id), 0)

    async def test_a_place_the_upload_never_names_is_no_parent(self) -> None:
        # (Brindleford is in the campaign, from another file, and not in this upload's words)
        self.assertEqual(await self.queue("The Gull's Rest is by the water."), [])

    async def test_a_parent_the_read_states_is_not_assumed(self) -> None:
        from dmbot.memory import background as bg

        text = "Kelvenmoor is a fishing town. The Gull's Rest is in Brindleford."
        stated = [bg.Connection("Gull's Rest", "member_of", "Brindleford", text)]
        self.assertEqual(await self.queue(text, stated), [])


class AfterTheReview(Stored):
    """The reviewer's cases on slice 3."""

    async def test_never_brings_back_a_parent_the_dm_removed(self) -> None:
        said = await self.memory.add_relation(
            GUILD_A, self.c, self.inn, "located_in", self.town,
            source=BY_DM, confidence=1.0, status=CONFIRMED,
        )  # fmt: skip
        await self.memory.update_relation(
            GUILD_A, self.c, said.value[0].id, source=BY_DM, status=REJECTED
        )
        links = BackstoryLinks(self.db)
        await links.add(
            GUILD_A, self.c, [NewLink(self.inn, "located_in", self.town, "", assumed=True)]
        )
        self.assertEqual(await keep_assumed(self.memory, links, GUILD_A, self.c), 0)
        self.assertEqual((await self.row(said.value[0].id)).status, REJECTED)

    async def test_one_guess_per_place(self) -> None:
        await self.assume(self.inn, self.town)
        with self.assertRaises(MemoryRuleError):
            await self.assume(self.inn, self.other)

    async def test_a_player_reads_nothing_up_through_a_guess(self) -> None:
        from dmbot.memory import gate
        from dmbot.memory import map_facts as mf

        pc = await self.add("Testa", type="player_character", played_by=9, status=CONFIRMED)
        dale = await self.place("Icewind Dale", "region")
        await self.memory.set_map_fact(GUILD_A, self.c, dale, mf.CLIMATE, "cold", source=BY_DM)
        await self.memory.show_map_facts(GUILD_A, self.c, dale, True, source=BY_DM)
        await self.assume(self.town, dale)
        dm = await self.memory.map_facts_up(GUILD_A, self.c, self.town, gate.Viewer.the_dm())
        self.assertEqual(dm[mf.CLIMATE].from_id, dale)
        player = await self.memory.map_facts_up(
            GUILD_A, self.c, self.town, gate.Viewer.player([pc]), shown={self.town, dale}
        )
        self.assertNotIn(mf.CLIMATE, player)  # (no "(same as Icewind Dale)" from a guess)

    async def test_undo_of_the_dms_reveal_puts_the_guess_back(self) -> None:
        guess = await self.assume(self.inn, self.town)
        for e in (self.inn, self.town):
            await self.memory.set_hidden_from_players(GUILD_A, self.c, e, False, source=BY_DM)
        shown = await self.memory.update_relation(
            GUILD_A, self.c, guess, source=BY_DM, unrevealed=False
        )
        assert shown.batch is not None
        await self.memory.undo_connection(GUILD_A, self.c, shown.batch, within=3600, secrets=True)
        r = await self.row(guess)
        self.assertEqual((r.assumed, r.unrevealed), (True, True))

    async def test_a_stated_parent_kept_later_replaces_the_guess(self) -> None:
        guess = await self.assume(self.inn, self.town)
        proposed = await self.memory.add_relation(
            GUILD_A, self.c, self.inn, "located_in", self.other,
            source="background", confidence=0.9, status=PROPOSED,
        )  # fmt: skip
        self.assertEqual((await self.row(guess)).status, CONFIRMED)
        await self.memory.update_relation(
            GUILD_A, self.c, proposed.value[0].id, source=BY_DM, status=CONFIRMED
        )
        self.assertEqual((await self.row(guess)).status, REJECTED)

    async def test_a_proposal_of_the_same_parent_keeps_the_mark(self) -> None:
        guess = await self.assume(self.inn, self.town)
        await self.memory.add_relation(
            GUILD_A, self.c, self.inn, "located_in", self.town,
            source="background", confidence=0.9, status=PROPOSED,
        )  # fmt: skip
        self.assertTrue((await self.row(guess)).assumed)

    async def test_a_merge_with_a_stated_parent_drops_the_guess(self) -> None:
        guess = await self.assume(self.inn, self.town)
        twin = await self.place("The Gull", "building")
        await self.memory.add_relation(
            GUILD_A, self.c, twin, "located_in", self.other,
            source=BY_DM, confidence=1.0, status=CONFIRMED,
        )  # fmt: skip
        await self.memory.merge(GUILD_A, self.c, self.inn, twin, source=BY_DM, dm_said_same=True)
        self.assertEqual((await self.row(guess)).status, REJECTED)

    async def test_a_stated_fact_folded_into_its_guess_is_the_dms(self) -> None:
        guess = await self.assume(self.inn, self.town)
        twin = await self.place("The Gull", "building")
        await self.memory.add_relation(
            GUILD_A, self.c, twin, "located_in", self.town,
            source=BY_DM, confidence=1.0, status=CONFIRMED,
        )  # fmt: skip
        await self.memory.merge(GUILD_A, self.c, self.inn, twin, source=BY_DM, dm_said_same=True)
        r = await self.row(guess)
        self.assertEqual((r.assumed, r.unrevealed, r.status), (False, False, CONFIRMED))


class TheQueueAfterTheReview(Stored):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.links = BackstoryLinks(self.db)

    async def test_a_stated_text_takes_its_own_quote_and_secrecy(self) -> None:
        await self.links.add(
            GUILD_A, self.c, [NewLink(self.inn, "located_in", self.town, "", assumed=True)]
        )
        await self.links.add(
            GUILD_A, self.c,
            [NewLink(self.inn, "located_in", self.town, "It's in Kelvenmoor.", secret=True)],
        )  # fmt: skip
        (w,) = await self.links.waiting(GUILD_A, self.c)
        self.assertEqual((w.quote, w.secret, w.assumed), ("It's in Kelvenmoor.", True, False))

    async def test_a_kept_guess_restated_is_asked_and_keeping_it_clears_the_mark(self) -> None:
        guess = await self.assume(self.inn, self.town)
        await self.links.add(
            GUILD_A, self.c, [NewLink(self.inn, "located_in", self.town, "It's in Kelvenmoor.")]
        )
        (w,) = await self.links.waiting(GUILD_A, self.c)
        from dmbot.memory.backstory_keep import keep_all

        await keep_all(self.memory, self.links, GUILD_A, self.c, [w])
        self.assertFalse((await self.row(guess)).assumed)
