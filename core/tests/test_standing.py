"""Standing the DM sets (#1655): the pure rule (the five steps, the DM always wins, direct evidence
outweighing prejudice, two rungs at most, no loops, the timing budget), the dated impressions and
attitudes (history kept), the party, who may set them, isolation, backups, recall never reading
them, and the 🤝 Standing screen."""

from __future__ import annotations

import time
import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from dmbot.campaigns.models import CampaignError
from dmbot.campaigns.store import decode_backup, encode_backup
from dmbot.memory import standing as st
from dmbot.memory.models import MemoryRuleError
from dmbot.memory.recall import DM_SCREEN, PLAYERS, RecallService
from dmbot.memory.standing import Index, Link, Node, Row
from dmbot.ui import name_card
from dmbot.ui import standing as ui
from tests.test_memory_store import DM, GUILD_A, GUILD_B, ConfirmedMemoryTest


def node(i: str, kind: str = "character", **kw: Any) -> Node:
    return Node(i, i.title(), kind, **kw)


def world() -> Index:
    """Gate guard (NPC) lives in Bryn Shander, which is in Ten-Towns; the watch is a group the guard
    belongs to. Kesh (a tabaxi) is a player character; the party is Kesh's group."""
    nodes = [
        node("guard"), node("watch", "faction"), node("bryn", "place"), node("towns", "place"),
        node("kesh", player_character=True), node("bryn_band", "faction"),
        node("party", "faction", party=True), node("tabaxi", "people"),
        node("sam", player_character=True),
    ]  # fmt: skip
    links = [
        Link("guard", st.MEMBER_OF, "watch"),
        Link("guard", st.LOCATED_IN, "bryn"),
        Link("bryn", st.LOCATED_IN, "towns"),
        Link("kesh", st.OF_PEOPLE, "tabaxi"),
    ]
    return Index(nodes, links, [])


def with_rows(*rows: Row) -> Index:
    base = world()
    return Index(base.nodes.values(), [Link(s, p, o) for s, p, o in LINKS], rows)


LINKS = [
    ("guard", st.MEMBER_OF, "watch"),
    ("guard", st.LOCATED_IN, "bryn"),
    ("bryn", st.LOCATED_IN, "towns"),
    ("kesh", st.OF_PEOPLE, "tabaxi"),
]


def imp(a: str, b: str, step: str, weight: int = st.DM_WEIGHT, source: str = "dm") -> Row:
    return Row(a, b, st.SCORE_OF[step], weight, source)


def att(a: str, b: str, step: str, weight: int = st.DM_WEIGHT, source: str = "dm") -> Row:
    return Row(a, b, st.SCORE_OF[step], weight, source, attitude=True)


class Steps(unittest.TestCase):
    def test_the_five_steps_and_their_cut_offs(self) -> None:
        self.assertEqual(
            [st.step_of(s) for s in (-100, -61, -60, -21, -20, 19, 20, 59, 60, 100)],
            ["Hostile", "Hostile", "Unfriendly", "Unfriendly", "Wary", "Wary", "Friendly",
             "Friendly", "Devoted", "Devoted"],
        )  # fmt: skip
        for step, score in st.SCORE_OF.items():
            self.assertEqual(st.step_of(score), step)


class Rule(unittest.TestCase):
    def test_nothing_set_is_no_opinion_yet_and_not_wary(self) -> None:
        got = world().standing("guard", "kesh")
        self.assertEqual((got.step, got.score, got.shown), (None, None, st.NO_OPINION))
        self.assertEqual(got.reasons, ())

    def test_a_direct_dm_set_impression_decides_and_the_rest_are_only_reasons(self) -> None:
        index = with_rows(
            imp("guard", "kesh", "Friendly"),
            att("bryn", "tabaxi", "Hostile"),
            imp("watch", "party", "Hostile"),
        )
        got = index.standing("guard", "kesh")
        self.assertEqual((got.step, got.decided_by_dm), ("Friendly", True))
        self.assertEqual(len(got.reasons), 3)
        self.assertIn("You set it.", got.reasons)
        self.assertTrue(any("Bryn is Hostile toward Tabaxi" in r for r in got.reasons))

    def test_a_group_with_both_an_impression_and_an_attitude_for_a_pair_counts_both(self) -> None:
        nodes = [node("g", "faction"), node("t", "people"), node("v")]
        rows = [imp("g", "t", "Devoted", source="deed"), att("g", "t", "Hostile", source="deed")]
        got = Index(nodes, [], rows).standing("g", "t")
        self.assertEqual((got.score, len(got.reasons)), (0.0, 2))

    def test_a_dm_set_word_toward_a_group_the_subject_is_in_does_not_dilute_to_decide(self) -> None:
        # Only the exact pair, or the viewer's own attitude toward a people, decides outright.
        index = with_rows(imp("guard", "party", "Hostile"), imp("watch", "kesh", "Devoted"))
        got = index.standing("guard", "kesh")
        self.assertFalse(got.decided_by_dm)
        self.assertEqual(len(got.reasons), 2)

    def test_the_viewers_own_attitude_toward_a_people_decides_too(self) -> None:
        nodes = list(world().nodes.values())
        links = [Link(s, p, o) for s, p, o in LINKS]
        rows = [att("bryn", "tabaxi", "Unfriendly"), imp("watch", "party", "Devoted")]
        got = Index(nodes, links, rows).standing("bryn", "kesh")
        # (Bryn is the viewer: its own word toward the tabaxi decides; the watch's is not Bryn's.)
        self.assertEqual((got.step, got.decided_by_dm), ("Unfriendly", True))

    def test_prejudice_from_a_rung_up_counts_when_nothing_is_direct(self) -> None:
        got = with_rows(att("bryn", "tabaxi", "Unfriendly")).standing("guard", "kesh")
        self.assertEqual((got.step, got.decided_by_dm), ("Unfriendly", False))
        self.assertIn("Bryn is Unfriendly toward Tabaxi (you set it).", got.reasons)

    def test_the_party_counts_for_each_player_character(self) -> None:
        got = with_rows(imp("watch", "party", "Friendly")).standing("guard", "kesh")
        self.assertEqual(got.step, "Friendly")
        self.assertEqual(
            with_rows(imp("watch", "party", "Friendly")).standing("guard", "sam").step, "Friendly"
        )

    def test_each_rung_up_counts_half_as_much(self) -> None:
        # Close and far disagree: the nearer one weighs more, so the mean leans to it.
        index = with_rows(
            imp("watch", "kesh", "Devoted", source="deed"),  # one rung up the judging ladder
            imp("towns", "tabaxi", "Hostile", source="deed"),  # two up and one up
        )
        got = index.standing("guard", "kesh")
        near = 100 * 0.5**1  # (1, 0)
        far = 100 * 0.5**3  # (2, 1)
        expect = (80 * near + -80 * far) / (near + far)
        self.assertAlmostEqual(got.score or 0, expect)
        self.assertEqual(got.step, "Friendly")  # 48: leaning to the nearer, Devoted needs 60

    def test_direct_experience_outweighs_prejudice_as_the_evidence_grows(self) -> None:
        prejudice = att("bryn", "tabaxi", "Hostile", weight=100, source="deed")
        weak = imp("guard", "kesh", "Friendly", weight=5, source="deed")
        strong = imp("guard", "kesh", "Friendly", weight=100, source="deed")
        low = with_rows(prejudice, weak).standing("guard", "kesh")
        high = with_rows(prejudice, strong).standing("guard", "kesh")
        self.assertLess(low.score or 0, 0)  # prejudice still wins on thin evidence
        self.assertGreater(high.score or 0, 0)  # the guard's own experience decides
        self.assertFalse(high.decided_by_dm)

    def test_no_more_than_two_rungs_up_either_ladder(self) -> None:
        nodes = [node(c, "place") for c in "abcd"] + [node("v"), node("s", player_character=True)]
        links = [Link("v", st.LOCATED_IN, "a"), Link("a", st.LOCATED_IN, "b"),
                 Link("b", st.LOCATED_IN, "c"), Link("c", st.LOCATED_IN, "d")]  # fmt: skip
        index = Index(nodes, links, [imp("b", "s", "Hostile")])
        self.assertEqual(index.standing("v", "s").step, "Hostile")  # b is two up
        only_c = Index(nodes, links, [imp("c", "s", "Hostile"), imp("d", "s", "Hostile")])
        self.assertIsNone(only_c.standing("v", "s").step)  # c is three up: too far

    def test_a_loop_in_the_places_does_not_hang_or_count_twice(self) -> None:
        nodes = [
            node("a", "place"),
            node("b", "place"),
            node("v"),
            node("s", player_character=True),
        ]
        links = [Link("v", st.LOCATED_IN, "a"), Link("a", st.LOCATED_IN, "b"),
                 Link("b", st.LOCATED_IN, "a")]  # fmt: skip
        got = Index(nodes, links, [imp("a", "s", "Friendly")]).standing("v", "s")
        self.assertEqual((got.step, len(got.reasons)), ("Friendly", 1))

    def test_a_viewer_does_not_judge_itself_and_unknown_names_say_nothing(self) -> None:
        index = with_rows(imp("guard", "kesh", "Friendly"))
        self.assertIsNone(index.standing("guard", "guard").step)
        self.assertIsNone(index.standing("nobody", "kesh").step)

    def test_thirty_pairs_are_worked_out_well_inside_five_milliseconds(self) -> None:
        nodes = [node(f"n{i}") for i in range(3000)] + [
            node(f"g{i}", "faction") for i in range(300)
        ]
        nodes += [node(f"p{i}", "place") for i in range(100)]
        nodes += [node(f"c{i}", player_character=True) for i in range(6)]
        nodes += [node("party", "faction", party=True), node("tabaxi", "people")]
        links = [Link(f"n{i}", st.MEMBER_OF, f"g{i % 300}") for i in range(3000)]
        links += [Link(f"n{i}", st.LOCATED_IN, f"p{i % 100}") for i in range(3000)]
        links += [Link(f"c{i}", st.OF_PEOPLE, "tabaxi") for i in range(6)]
        rows = [imp(f"g{i}", f"c{i % 6}", "Wary") for i in range(300)]
        rows += [att(f"p{i}", "tabaxi", "Unfriendly") for i in range(100)]
        rows += [imp(f"n{i}", "party", "Friendly") for i in range(0, 3000, 3)]
        index = Index(nodes, links, rows)
        pairs = [(f"n{i}", f"c{j}") for i in range(5) for j in range(6)]
        best = min(self.timed(index, pairs) for _ in range(5))
        self.assertLess(best, 5.0, f"30 pairs took {best:.2f} ms")

    @staticmethod
    def timed(index: Index, pairs: list[tuple[str, str]]) -> float:
        start = time.perf_counter()
        for viewer, subject in pairs:
            index.standing(viewer, subject)
        return (time.perf_counter() - start) * 1000


class Stored(ConfirmedMemoryTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.guard = await self.add("Gate guard", type="character", role="npc")
        self.bryn = await self.add("Bryn Shander", type="place")
        self.watch = await self.add("The watch", type="faction")
        self.kesh = await self.add("Kesh", type="character", role="player_character", played_by=11)
        self.tabaxi = await self.add("Tabaxi", type="people")
        await self.relate(self.guard, "located_in", self.bryn, status="confirmed")
        await self.relate(self.kesh, "of_people", self.tabaxi, status="confirmed")

    async def set(self, viewer: str, subject: str, step: str, **kw: Any) -> Any:
        return await self.memory.set_impression(
            GUILD_A, self.c, viewer, subject, step, source="dm", **kw
        )

    async def test_people_and_of_people_exist_in_core(self) -> None:
        onto = await self.memory.ontology(GUILD_A, self.c)
        self.assertTrue(onto.is_a("people", "concept"))
        self.assertIn("of_people", onto.predicates)

    async def test_the_standing_is_worked_out_from_the_stored_story(self) -> None:
        await self.memory.set_attitude(
            GUILD_A, self.c, self.bryn, self.tabaxi, "Unfriendly", source="dm"
        )
        index = await self.memory.standing_index(GUILD_A, self.c)
        got = index.standing(self.guard, self.kesh)
        self.assertEqual(got.step, "Unfriendly")
        self.assertIn("Bryn Shander is Unfriendly toward Tabaxi (you set it).", got.reasons)
        await self.set(self.guard, self.kesh, "Friendly")
        self.assertEqual(
            (await self.memory.standing_index(GUILD_A, self.c))
            .standing(self.guard, self.kesh)
            .step,
            "Friendly",
        )

    async def test_a_new_word_closes_the_old_and_the_history_is_kept(self) -> None:
        await self.set(self.guard, self.kesh, "Wary", from_session_at=100)
        await self.set(self.guard, self.kesh, "Friendly", from_session_at=200)
        history = await self.memory.opinion_history(
            GUILD_A, self.c, self.guard, self.kesh, attitude=False
        )
        self.assertEqual([h.score for h in history], [0, 40])
        self.assertEqual([h.closed_at is None for h in history], [False, True])
        self.assertEqual([h.from_session_at for h in history], [100, 200])
        again = await self.set(self.guard, self.kesh, "Friendly")  # nothing new to say
        self.assertEqual(again.value.id, history[-1].id)
        self.assertEqual(
            len(
                await self.memory.opinion_history(
                    GUILD_A, self.c, self.guard, self.kesh, attitude=False
                )
            ),
            2,
        )

    async def test_only_the_dm_and_only_between_the_right_kinds(self) -> None:
        with self.assertRaises(MemoryRuleError):
            await self.memory.set_impression(
                GUILD_A, self.c, self.guard, self.kesh, "Friendly", source="extractor"
            )
        for bad in ("Cheerful", ""):
            with self.assertRaises(MemoryRuleError):
                await self.set(self.guard, self.kesh, bad)
        item = await self.add("Heartseeker", type="item")
        with self.assertRaises(MemoryRuleError):
            await self.set(self.guard, item, "Friendly")
        with self.assertRaises(MemoryRuleError):
            await self.set(self.guard, self.guard, "Friendly")
        with self.assertRaises(
            MemoryRuleError
        ):  # an attitude is a group's or place's, toward a people
            await self.memory.set_attitude(
                GUILD_A, self.c, self.guard, self.tabaxi, "Wary", source="dm"
            )
        with self.assertRaises(MemoryRuleError):
            await self.memory.set_attitude(
                GUILD_A, self.c, self.bryn, self.kesh, "Wary", source="dm"
            )

    async def test_the_party_is_made_once_and_its_members_are_the_player_characters(self) -> None:
        self.assertIsNone(await self.memory.party(GUILD_A, self.c))
        one = await self.memory.ensure_party(GUILD_A, self.c, source="dm")
        two = await self.memory.ensure_party(GUILD_A, self.c, source="dm")
        self.assertEqual((one.id, one.party, one.type), (two.id, True, "faction"))
        await self.set(self.watch, one.id, "Friendly")
        got = (await self.memory.standing_index(GUILD_A, self.c)).standing(self.watch, self.kesh)
        self.assertEqual(got.step, "Friendly")  # Kesh is in the party with no fact written
        written = await self.memory.relations(GUILD_A, self.c, entity_id=one.id)
        self.assertEqual(written, [])
        with self.assertRaises(MemoryRuleError):
            await self.memory.ensure_party(GUILD_A, self.c, source="player")

    async def test_the_party_goes_when_it_stops_being_a_group(self) -> None:
        party = await self.memory.ensure_party(GUILD_A, self.c, source="dm")
        written = await self.memory.set_entity_type(GUILD_A, self.c, party.id, "place", source="dm")
        self.assertFalse(written.value.party)
        self.assertIsNone(await self.memory.party(GUILD_A, self.c))

    async def test_a_forgotten_party_is_brought_back_not_made_twice(self) -> None:
        party = await self.memory.ensure_party(GUILD_A, self.c, source="dm")
        await self.memory.set_entity_status(GUILD_A, self.c, party.id, "rejected", source="dm")
        again = await self.memory.ensure_party(GUILD_A, self.c, source="dm")
        self.assertEqual((again.id, again.status, again.party), (party.id, "confirmed", True))

    async def test_a_backup_still_restores_after_a_group_became_something_else(self) -> None:
        await self.memory.set_attitude(
            GUILD_A, self.c, self.watch, self.tabaxi, "Wary", source="dm"
        )
        await self.memory.set_entity_type(GUILD_A, self.c, self.watch, "item", source="dm")
        raw = encode_backup(await self.campaigns.export(GUILD_A, self.c))
        restored = await self.campaigns.import_backup(GUILD_B, decode_backup(raw), DM)
        self.assertTrue(await self.memory.entities(GUILD_B, restored.id))

    async def test_three_words_for_one_pair_in_a_second_keep_their_order(self) -> None:
        for step in ("Wary", "Friendly", "Hostile"):
            await self.set(self.guard, self.kesh, step)
        history = await self.memory.opinion_history(
            GUILD_A, self.c, self.guard, self.kesh, attitude=False
        )
        self.assertEqual(history[-1].score, -80)
        self.assertIsNone(history[-1].closed_at)
        self.assertEqual(sum(h.closed_at is None for h in history), 1)

    async def test_a_people_is_found_or_made_by_name(self) -> None:
        again = await self.memory.find_or_add_people(GUILD_A, self.c, "  tabaxi ", source="dm")
        self.assertEqual(again.id, self.tabaxi)
        new = await self.memory.find_or_add_people(GUILD_A, self.c, "Outsiders", source="dm")
        self.assertEqual((new.type, new.status), ("people", "confirmed"))

    async def test_another_campaign_sees_nothing(self) -> None:
        await self.set(self.guard, self.kesh, "Friendly")
        other = (await self.campaigns.create(GUILD_A, "Elsewhere", 7)).id
        index = await self.memory.standing_index(GUILD_A, other)
        self.assertEqual((index.nodes, index.impressions), ({}, {}))
        with self.assertRaises(MemoryRuleError):
            await self.memory.standing_index(GUILD_B, self.c)

    async def test_merging_two_entries_keeps_the_standing_on_the_one_kept(self) -> None:
        twin = await self.add("The guard at the gate", type="character", role="npc")
        await self.set(twin, self.kesh, "Hostile")
        await self.memory.merge(GUILD_A, self.c, self.guard, twin, source="dm", dm_said_same=True)
        index = await self.memory.standing_index(GUILD_A, self.c)
        self.assertEqual(index.standing(self.guard, self.kesh).step, "Hostile")

    async def test_recall_never_reads_standing(self) -> None:
        await self.set(self.guard, self.kesh, "Devoted")
        for audience in (DM_SCREEN, PLAYERS):
            got = await RecallService(self.memory).recall(
                GUILD_A, self.c, seeds=["Gate guard", "Kesh"], audience=audience, opted_in={7}
            )
            self.assertNotIn("Devoted", " ".join(i.text for i in got.items))

    # ---- backups ----

    async def test_standing_the_party_and_the_history_come_back_from_a_backup(self) -> None:
        party = await self.memory.ensure_party(GUILD_A, self.c, source="dm")
        await self.set(self.guard, self.kesh, "Wary")
        await self.set(self.guard, self.kesh, "Friendly")
        await self.set(self.watch, party.id, "Devoted")
        await self.memory.set_attitude(
            GUILD_A, self.c, self.bryn, self.tabaxi, "Hostile", source="dm"
        )
        raw = encode_backup(await self.campaigns.export(GUILD_A, self.c))
        restored = await self.campaigns.import_backup(GUILD_B, decode_backup(raw), DM)
        entities = {e.name: e for e in await self.memory.entities(GUILD_B, restored.id)}
        self.assertTrue(entities["The party"].party)
        index = await self.memory.standing_index(GUILD_B, restored.id)
        got = index.standing(entities["Gate guard"].id, entities["Kesh"].id)
        self.assertEqual(got.step, "Friendly")
        history = await self.memory.opinion_history(
            GUILD_B, restored.id, entities["Gate guard"].id, entities["Kesh"].id, attitude=False
        )
        self.assertEqual(len(history), 2)
        bryn = index.standing(entities["Bryn Shander"].id, entities["Kesh"].id)
        self.assertEqual(bryn.step, "Hostile")

    async def test_an_older_backup_without_standing_restores_and_a_damaged_one_is_refused(
        self,
    ) -> None:
        await self.set(self.guard, self.kesh, "Wary")
        data = await self.campaigns.export(GUILD_A, self.c)
        older = [r for r in data["sections"]["memory"] if r.get("table") != "impression"]
        for row in older:
            if row.get("table") == "entity":
                row.pop("party", None)
        old = {**data, "sections": {**data["sections"], "memory": older}}
        restored = await self.campaigns.import_backup(GUILD_B, old, DM)
        names = {e.name for e in await self.memory.entities(GUILD_B, restored.id)}
        self.assertTrue({"Gate guard", "Kesh", "Tabaxi"} <= names)
        damaged = [dict(r) for r in data["sections"]["memory"]]
        for row in damaged:
            if row.get("table") == "impression":
                row["score"] = 1000
        bad = {**data, "sections": {**data["sections"], "memory": damaged}}
        with self.assertRaises(CampaignError):
            await self.campaigns.import_backup(GUILD_B, bad, DM)
        twice = [dict(r) for r in data["sections"]["memory"]]
        twice += [dict(r) for r in twice if r.get("table") == "impression"]
        for n, row in enumerate(twice):
            if row.get("table") == "impression" and n >= len(twice) - 1:
                row["id"] = "f" * 32
        with self.assertRaises(CampaignError):  # two current impressions for one pair
            await self.campaigns.import_backup(
                GUILD_B, {**data, "sections": {**data["sections"], "memory": twice}}, DM
            )


class Screen(ConfirmedMemoryTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.guard = await self.add("Gate guard", type="character", role="npc")
        self.kesh = await self.add("Kesh", type="character", role="player_character", played_by=11)
        self.bryn = await self.add("Bryn Shander", type="place")

    def interaction(self, user: int = DM) -> Any:
        sent: list[str] = []

        async def reply(text: str = "", **kw: Any) -> None:
            sent.append(str(kw.get("content", text)))

        it = MagicMock()
        it.user = SimpleNamespace(id=user)
        it.guild = SimpleNamespace(id=GUILD_A)
        it.client = SimpleNamespace(
            memory=self.memory, campaigns=self.campaigns, lookup=None, homebrew_changed=None
        )
        it.response.is_done = lambda: False
        it.response.defer = AsyncMock()
        it.response.send_message = reply
        it.response.edit_message = reply
        it.followup.send = reply
        it.edit_original_response = reply
        it.sent = sent
        return it

    async def test_the_screen_shows_the_step_the_reasons_and_the_reminder(self) -> None:
        await self.memory.set_impression(
            GUILD_A, self.c, self.guard, self.kesh, "Wary", source="dm"
        )
        it = self.interaction()
        await ui.show_standing(it, self.c, self.guard)
        text = it.sent[-1]
        self.assertIn("**Gate guard** → **Kesh**: Wary. You set it.", text)
        self.assertIn(st.REMINDER, text)

    async def test_nothing_set_says_no_opinion_yet(self) -> None:
        it = self.interaction()
        await ui.show_standing(it, self.c, self.guard)
        self.assertIn("**Gate guard** → **Kesh**: No opinion yet.", it.sent[-1])

    async def test_a_player_sees_and_changes_nothing(self) -> None:
        guard = await self.memory.entity(GUILD_A, self.c, self.guard)
        kesh = await self.memory.entity(GUILD_A, self.c, self.kesh)
        assert guard is not None and kesh is not None
        player = self.interaction(99)
        await ui.show_standing(player, self.c, self.guard)
        self.assertNotIn("Wary", " ".join(player.sent))
        step = ui.PickStep(self.c, guard, kesh, attitude=False)
        await step._save("Hostile", self.interaction(99))
        self.assertEqual((await self.memory.standing_index(GUILD_A, self.c)).impressions, {})
        menu = ui.StandingMenu(self.c, guard, [kesh], False)
        self.assertTrue(await menu._dm(self.interaction()))
        self.assertFalse(await menu._dm(self.interaction(99)))

    async def test_picking_a_step_saves_a_dated_impression(self) -> None:
        guard = await self.memory.entity(GUILD_A, self.c, self.guard)
        kesh = await self.memory.entity(GUILD_A, self.c, self.kesh)
        assert guard is not None and kesh is not None
        step = ui.PickStep(self.c, guard, kesh, attitude=False)
        it = self.interaction()
        await step._save("Hostile", it)
        self.assertIn("Saved: **Gate guard** is **Hostile** toward **Kesh**.", it.sent[-1])
        got = (await self.memory.standing_index(GUILD_A, self.c)).standing(self.guard, self.kesh)
        self.assertEqual(got.step, "Hostile")

    async def test_the_card_button_is_for_an_npc_a_group_or_a_place_never_a_player(self) -> None:
        def labels(card: Any) -> list[str]:
            return [str(getattr(c, "label", "")) for c in card.children]

        self.assertIn(
            name_card.STANDING_BUTTON, labels(name_card.NameCard(self.c, self.guard, standing=True))
        )
        self.assertNotIn(name_card.STANDING_BUTTON, labels(name_card.NameCard(self.c, self.kesh)))


if __name__ == "__main__":
    unittest.main()
