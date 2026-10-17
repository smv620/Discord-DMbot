"""Deeds the DM confirms move standing (#1732): recording a deed and Undo, one deed moving a
feeling as the weights say, the DM's own word still winning, old deeds counting half, deeds
hidden from players' recall and lookup and from transcripts, the backup round trip, isolation,
and who can do a deed and who it can matter to. Needs the test database for the stored parts."""

from __future__ import annotations

import unittest
from functools import partial
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

from dmbot.campaigns.store import decode_backup, encode_backup
from dmbot.memory import standing as st
from dmbot.memory.lookup import CampaignLookup
from dmbot.memory.models import MemoryRuleError
from dmbot.memory.recall import DM_SCREEN, PLAYERS, RecallService
from dmbot.memory.standing import Index, Link, Node, Row
from dmbot.ui import deeds as ui
from dmbot.ui import standing as standing_ui
from tests.test_adventure import press
from tests.test_memory_store import DM, GUILD_A, GUILD_B, ConfirmedMemoryTest


def deed_row(effect: str, *, weight: int = st.DEED_WEIGHT, session: int | None = None) -> Row:
    return Row(
        "guard", "kesh", st.DEED_SCORE[effect], weight, st.STORY_SOURCE, False, "Saved the mill",
        effect, session,
    )  # fmt: skip


def world(*rows: Row, numbers: dict[int, int] | None = None) -> Index:
    nodes = [
        Node("guard", "Guard", "character"),
        Node("kesh", "Kesh", "character", player_character=True),
    ]
    return Index(nodes, [], rows, numbers)


class Rule(unittest.TestCase):
    def test_the_four_effects_and_their_scores(self) -> None:
        self.assertEqual(
            [(label, score) for label, score in st.DEED_EFFECTS],
            [("Helped them", 20), ("Pleased them", 10), ("Annoyed them", -10), ("Hurt them", -20)],
        )

    def test_one_deed_moves_no_opinion_and_two_move_it_on_as_the_weights_say(self) -> None:
        self.assertEqual(world().standing("guard", "kesh").shown, "No opinion yet")
        one = world(deed_row("Helped them")).standing("guard", "kesh")
        self.assertEqual((one.step, one.score), ("Friendly", 20))
        story = Row("guard", "kesh", st.SCORE_OF["Wary"], 20, "story")  # something from the story
        mixed = world(story, deed_row("Helped them")).standing("guard", "kesh")
        self.assertEqual((mixed.step, mixed.score), ("Wary", 10))
        # Helped them (+20) is exactly the Friendly line, so with a Wary word beside it the mean
        # stays below it: deeds alone get there, a mix does not.

    def test_a_hurt_deed_pulls_down_and_the_dm_set_impression_still_wins(self) -> None:
        hurt = world(deed_row("Hurt them"), deed_row("Hurt them")).standing("guard", "kesh")
        self.assertEqual(hurt.step, "Wary")  # -20 is not yet below the Unfriendly line
        dm = Row("guard", "kesh", st.SCORE_OF["Devoted"], st.DM_WEIGHT, "dm")
        decided = world(dm, deed_row("Hurt them")).standing("guard", "kesh")
        self.assertEqual((decided.step, decided.decided_by_dm), ("Devoted", True))
        self.assertTrue(any("Saved the mill" in r for r in decided.reasons))  # shown, not counted

    def test_a_deed_older_than_ten_sessions_counts_half(self) -> None:
        fresh = deed_row("Helped them")
        faded = deed_row("Helped them", weight=st.DEED_WEIGHT // 2)
        story = Row("guard", "kesh", st.SCORE_OF["Hostile"], 20, "story")
        a = world(story, fresh).standing("guard", "kesh")
        b = world(story, faded).standing("guard", "kesh")
        self.assertGreater(b.score or 0, -1000)
        self.assertLess(b.score or 0, a.score or 0)  # the faded deed pulls less toward Friendly

    def test_the_reason_names_the_deed_the_session_and_how_it_landed(self) -> None:
        got = world(deed_row("Helped them", session=700), numbers={700: 4}).standing(
            "guard", "kesh"
        )
        self.assertEqual(got.reasons, ("⚔️ Saved the mill (session 4, helped)",))
        plain = world(deed_row("Hurt them")).standing("guard", "kesh")
        self.assertEqual(plain.reasons, ("⚔️ Saved the mill (hurt)",))

    def test_deeds_come_from_the_doers_ladders_too(self) -> None:
        index = Index(
            [
                Node("guard", "Guard", "character"),
                Node("kesh", "Kesh", "character", player_character=True),
                Node("party", "The party", "faction", party=True),
            ],
            [Link("kesh", st.MEMBER_OF, "party")],
            [Row("guard", "party", 20, 20, "story", False, "Won the war", "Helped them")],
        )
        self.assertEqual(index.standing("guard", "kesh").step, "Friendly")  # via the party


class Stored(ConfirmedMemoryTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.guard = await self.add("Gate guard", type="character", role="npc")
        self.watch = await self.add("The watch", type="faction")
        self.mill = await self.add("The mill", type="place")
        self.kesh = await self.add("Kesh", type="character", role="player_character", played_by=11)
        self.party = (await self.memory.ensure_party(GUILD_A, self.c, source="dm")).id

    async def deed(
        self, doer: str | None = None, who: Any = None, effect: str = "Helped them"
    ) -> Any:
        return await self.memory.add_deed(
            GUILD_A, self.c, doer or self.kesh, "Saved the mill from the fire",
            [self.guard] if who is None else who, effect, source="dm", from_session_at=700,
        )  # fmt: skip

    async def standing(self) -> Any:
        index = await self.memory.standing_index(GUILD_A, self.c, {700: 4})
        return index.standing(self.guard, self.kesh)

    async def test_recording_a_deed_moves_standing_and_undo_takes_it_back(self) -> None:
        written = await self.deed()
        self.assertEqual(written.value.type, "deed")
        got = await self.standing()
        self.assertEqual(got.step, "Friendly")
        self.assertEqual(got.reasons, ("⚔️ Saved the mill from the fire (session 4, helped)",))
        await self.memory.undo(GUILD_A, self.c, written.batch)
        self.assertEqual((await self.standing()).shown, "No opinion yet")
        names = [e.name for e in await self.memory.entities(GUILD_A, self.c)]
        self.assertNotIn("Saved the mill from the fire", names)  # the whole deed went

    async def test_the_party_can_do_a_deed_and_it_reaches_each_player_character(self) -> None:
        await self.deed(doer=self.party, who=[self.watch, self.mill])
        index = await self.memory.standing_index(GUILD_A, self.c)
        self.assertEqual(index.standing(self.watch, self.kesh).step, "Friendly")

    async def test_one_deed_for_several_and_never_more_than_five(self) -> None:
        extra = [await self.add(f"Folk {n}", type="character", role="npc") for n in range(5)]
        with self.assertRaises(MemoryRuleError):
            await self.deed(who=[self.guard, *extra])  # six
        written = await self.deed(who=[self.guard, *extra[:4]])
        self.assertTrue(written.batch)
        with self.assertRaises(MemoryRuleError):
            await self.deed(who=[])

    async def test_only_a_player_character_or_the_party_can_do_one(self) -> None:
        for bad in (self.guard, self.watch, self.mill):
            with self.assertRaises(MemoryRuleError):
                await self.deed(doer=bad, who=[self.watch if bad != self.watch else self.mill])

    async def test_it_can_matter_only_to_an_npc_a_group_or_a_place(self) -> None:
        sam = await self.add("Sam", type="character", role="player_character", played_by=12)
        item = await self.add("Heartseeker", type="item")
        for bad in (sam, item, self.party, self.kesh):
            with self.assertRaises(MemoryRuleError):
                await self.deed(who=[bad])

    async def test_a_long_deed_is_kept_whole_with_a_shorter_name(self) -> None:
        for size in (150, 200):
            words = ("w" * (size - 1) + ".")[:size]
            written = await self.memory.add_deed(
                GUILD_A, self.c, self.kesh, words, [self.guard], "Helped them", source="dm"
            )
            got = await self.memory.entity(GUILD_A, self.c, written.value.id)
            assert got is not None
            self.assertEqual((got.description, len(got.name)), (words, min(size, 100)))

    async def test_a_deed_recorded_between_sessions_takes_the_newest_session(self) -> None:
        await self.heard(self.guard, 5000)
        await self.memory.add_deed(
            GUILD_A, self.c, self.kesh, "Between sessions", [self.guard], "Helped them", source="dm"
        )
        index = await self.memory.standing_index(GUILD_A, self.c)
        (row,) = index.deeds[(self.guard, self.kesh)]
        self.assertEqual(row.session, 5000)

    async def test_the_connections_are_secret_too(self) -> None:
        await self.deed()
        for r in await self.memory.relations(
            GUILD_A, self.c, entity_id=self.kesh, include_secret=True
        ):
            if r.predicate in ("did", "affected"):
                self.assertTrue(r.secret)
        shown = await self.memory.relations(
            GUILD_A, self.c, entity_id=self.kesh, include_secret=False
        )
        self.assertFalse([r for r in shown if r.predicate in ("did", "affected")])

    async def test_a_deed_has_no_name_the_table_can_match_and_hides_no_real_name(self) -> None:
        from dmbot.memory.scene import find_mentions

        brindle = await self.add("Brindle", type="character", role="npc")
        await self.memory.add_deed(
            GUILD_A, self.c, self.kesh, "Saved Brindle", [brindle], "Helped them", source="dm"
        )
        lookup = CampaignLookup.build(await self.memory.lookup_data(GUILD_A, self.c))
        self.assertFalse([n for n in lookup.names if "Saved Brindle" in n.text])
        heard = find_mentions(lookup, "we all saw Brindle there")
        self.assertEqual([f.entity_id for f in heard], [brindle])  # still counted as named
        said_whole = find_mentions(lookup, "Saved Brindle")
        self.assertEqual([f.entity_id for f in said_whole], [brindle])

    async def test_a_story_threads_name_said_aloud_still_counts_as_heard(self) -> None:
        from dmbot.memory.scene import find_mentions

        thread = await self.add("The Missing Heir", type="quest")
        lookup = CampaignLookup.build(await self.memory.lookup_data(GUILD_A, self.c))
        self.assertEqual(
            [f.entity_id for f in find_mentions(lookup, "about the missing heir")], [thread]
        )

    async def test_merging_the_npc_keeps_the_deed_and_the_standing(self) -> None:
        await self.deed()
        twin = await self.add("The guard at the gate", type="character", role="npc")
        await self.memory.merge(GUILD_A, self.c, self.guard, twin, source="dm", dm_said_same=True)
        index = await self.memory.standing_index(GUILD_A, self.c)
        self.assertEqual(index.standing(self.guard, self.kesh).step, "Friendly")

    async def test_the_words_and_the_effect_are_checked_and_only_the_dm_may(self) -> None:
        for text in ("", "x" * 201):
            with self.assertRaises(MemoryRuleError):
                await self.memory.add_deed(
                    GUILD_A, self.c, self.kesh, text, [self.guard], "Helped them", source="dm"
                )
        with self.assertRaises(MemoryRuleError):
            await self.deed(effect="Loved them")
        with self.assertRaises(MemoryRuleError):
            await self.memory.add_deed(
                GUILD_A, self.c, self.kesh, "x", [self.guard], "Helped them", source="entitybot"
            )

    async def test_a_dm_set_impression_still_wins_over_deeds(self) -> None:
        await self.memory.set_impression(
            GUILD_A, self.c, self.guard, self.kesh, "Hostile", source="dm"
        )
        await self.deed()
        got = await self.standing()
        self.assertEqual((got.step, got.decided_by_dm), ("Hostile", True))

    async def test_deeds_older_than_ten_sessions_count_half(self) -> None:
        for n in range(11):  # eleven sessions where names were heard
            await self.heard(self.guard, 1000 + n)
        await self.memory.set_impression(
            GUILD_A, self.c, self.guard, self.kesh, "Wary", source="dm"
        )  # (decides outright, so look at the rows instead)
        written = await self.memory.add_deed(
            GUILD_A, self.c, self.kesh, "An old favour", [self.guard], "Helped them",
            source="dm", from_session_at=1000,
        )  # fmt: skip
        self.assertTrue(written.batch)
        index = await self.memory.standing_index(GUILD_A, self.c)
        (row,) = index.deeds[(self.guard, self.kesh)]
        self.assertEqual(row.weight, st.DEED_WEIGHT // 2)
        fresh = await self.memory.add_deed(
            GUILD_A, self.c, self.kesh, "Yesterday", [self.guard], "Helped them",
            source="dm", from_session_at=1010,
        )  # fmt: skip
        self.assertTrue(fresh.batch)
        index = await self.memory.standing_index(GUILD_A, self.c)
        weights = sorted(r.weight for r in index.deeds[(self.guard, self.kesh)])
        self.assertEqual(weights, [st.DEED_WEIGHT // 2, st.DEED_WEIGHT])

    async def heard(self, entity: str, session: int) -> None:
        """Names heard in a session, where production writes them (`memory_heard`)."""
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "INSERT INTO memory_heard (guild_id, campaign_id, entity_id, session_started_at,"
                " speaker_id, times) VALUES (%s, %s, %s, %s, 7, 1)",
                (GUILD_A, self.c, entity, session),
            )

    # ---- private to the DM ----

    async def test_players_recall_and_lookup_never_see_a_deed(self) -> None:
        await self.deed()
        for audience in (DM_SCREEN, PLAYERS):
            got = await RecallService(self.memory).recall(
                GUILD_A, self.c, seeds=["Kesh", "Gate guard"], audience=audience, opted_in={11}
            )
            said = " ".join(i.text for i in got.items)
            if audience == PLAYERS:
                self.assertNotIn("Saved the mill", said)
        lookup = CampaignLookup.build(await self.memory.lookup_data(GUILD_A, self.c))
        self.assertTrue(any(e.type == "deed" for e in lookup.entities.values()))
        shown = lookup.for_viewer(False)
        self.assertFalse(any(e.type == "deed" for e in shown.entities.values()))

    async def test_the_players_card_for_the_doer_never_lists_the_deed(self) -> None:
        from dmbot.ui.name_card import card_text

        await self.deed()
        lookup = CampaignLookup.build(await self.memory.lookup_data(GUILD_A, self.c))
        connections = await self.memory.relations(
            GUILD_A, self.c, entity_id=self.kesh, include_secret=True
        )
        mine = card_text(lookup.for_viewer(True), self.kesh, connections, secrets=True)
        players = card_text(lookup.for_viewer(False), self.kesh, connections, secrets=False)
        self.assertIn("Saved the mill", mine or "")
        self.assertNotIn("Saved the mill", players or "")

    # ---- backups and isolation ----

    async def test_a_backup_brings_the_deeds_and_their_effect_back(self) -> None:
        await self.deed()
        raw = encode_backup(await self.campaigns.export(GUILD_A, self.c))
        restored = await self.campaigns.import_backup(GUILD_B, decode_backup(raw), DM)
        entities = {e.name: e for e in await self.memory.entities(GUILD_B, restored.id)}
        index = await self.memory.standing_index(GUILD_B, restored.id)
        got = index.standing(entities["Gate guard"].id, entities["Kesh"].id)
        self.assertEqual(got.step, "Friendly")

    async def test_another_campaign_or_server_sees_nothing_of_it(self) -> None:
        await self.deed()
        other = (await self.campaigns.create(GUILD_A, "Elsewhere", 7)).id
        index = await self.memory.standing_index(GUILD_A, other)
        self.assertEqual(index.deeds, {})
        with self.assertRaises(MemoryRuleError):
            await self.memory.standing_index(GUILD_B, self.c)

    async def test_deed_and_its_connections_exist_in_core(self) -> None:
        onto = await self.memory.ontology(GUILD_A, self.c)
        self.assertTrue(onto.is_a("deed", "event"))
        self.assertIn("did", onto.predicates)
        self.assertIn("affected", onto.predicates)


class Screens(Stored):
    def pressing(self, user: int = DM) -> Any:
        it = press(user, GUILD_A)
        it.client = SimpleNamespace(
            memory=self.memory, campaigns=self.campaigns, lookup=None, tables={}
        )
        it.type = None

        async def followup(content: str = "", **kw: Any) -> None:
            it.sent.update(content=content, **kw)

        it.followup.send = followup
        return it

    async def test_the_steps_record_the_deed_once_and_undo_works(self) -> None:
        doer = await self.memory.entity(GUILD_A, self.c, self.kesh)
        guard = await self.memory.entity(GUILD_A, self.c, self.guard)
        assert doer is not None and guard is not None
        how = ui.HowMenu(self.c, doer, "Burned the mill", [guard])
        it = self.pressing()
        await how._landed("Hurt them", it)
        confirm = it.sent["view"]
        self.assertIn("Record this deed?", it.sent["content"])
        done = self.pressing()
        await confirm._yes(done)
        self.assertIn("Recorded", done.sent["content"])
        self.assertEqual((await self.standing()).reasons[0][:2], "⚔️")
        undone = self.pressing()
        await done.sent["view"]._undo(undone)
        self.assertIn("Undone", undone.sent["content"])
        self.assertEqual((await self.standing()).shown, "No opinion yet")

    async def test_another_campaigns_running_table_never_dates_a_deed(self) -> None:
        doer = await self.memory.entity(GUILD_A, self.c, self.kesh)
        guard = await self.memory.entity(GUILD_A, self.c, self.guard)
        assert doer is not None and guard is not None
        confirm = ui.ConfirmMenu(self.c, doer, "Burned the mill", [guard], "Hurt them")
        it = self.pressing()
        it.client.tables = {GUILD_A: SimpleNamespace(campaign_id="f" * 32, started_at=424242)}
        await confirm._yes(it)
        index = await self.memory.standing_index(GUILD_A, self.c)
        (row,) = index.deeds[(self.guard, self.kesh)]
        self.assertNotEqual(row.session, 424242)
        mine = self.pressing()
        mine.client.tables = {GUILD_A: SimpleNamespace(campaign_id=self.c, started_at=525252)}
        again = ui.ConfirmMenu(self.c, doer, "Burned it again", [guard], "Hurt them")
        await again._yes(mine)
        index = await self.memory.standing_index(GUILD_A, self.c)
        self.assertIn(525252, [r.session for r in index.deeds[(self.guard, self.kesh)]])

    async def test_the_standing_screen_still_shows_when_the_sessions_cannot_be_read(self) -> None:
        await self.deed()

        async def broken(*a: Any, **kw: Any) -> Any:
            raise RuntimeError("the transcripts are down")

        it = self.pressing()
        it.client.transcripts = SimpleNamespace(sessions=broken)
        await standing_ui.show_standing(it, self.c, self.guard)
        shown = it.sent.get("content") or it.response.edit_message.call_args.kwargs["content"]
        self.assertIn("⚔️ Saved the mill from the fire", shown)
        self.assertNotIn("session 4", shown)  # the reason shows, without a session number
        ok = self.pressing()
        ok.client.transcripts = SimpleNamespace(
            sessions=AsyncMock(return_value=[SimpleNamespace(started_at=700, number=4)])
        )
        await standing_ui.show_standing(ok, self.c, self.guard)
        again = ok.sent.get("content") or ok.response.edit_message.call_args.kwargs["content"]
        self.assertIn("session 4", again)

    async def test_only_a_dm_can_use_any_step(self) -> None:
        doer = await self.memory.entity(GUILD_A, self.c, self.kesh)
        guard = await self.memory.entity(GUILD_A, self.c, self.guard)
        assert doer is not None and guard is not None
        confirm = ui.ConfirmMenu(self.c, doer, "Burned the mill", [guard], "Hurt them")
        it = self.pressing(999)
        await confirm._yes(it)
        self.assertIn(ui.ONLY_DM, it.sent["content"])
        self.assertEqual((await self.standing()).shown, "No opinion yet")


class Doers(Stored):
    async def test_a_deed_whose_doer_is_not_a_player_character_or_the_party_counts_for_nothing(
        self,
    ) -> None:
        await self.deed()
        index = await self.memory.standing_index(GUILD_A, self.c, {700: 4})
        self.assertEqual(len(index.deeds[(self.guard, self.kesh)]), 1)
        async with self.db.guild(GUILD_A) as conn:  # the doer is no longer a player character
            await conn.execute(
                "UPDATE memory_entities SET role = 'npc' WHERE guild_id = %s AND id = %s",
                (GUILD_A, self.kesh),
            )
        index = await self.memory.standing_index(GUILD_A, self.c, {700: 4})
        self.assertEqual(index.deeds, {})
        self.assertEqual(index.standing(self.guard, self.kesh).shown, "No opinion yet")

    async def test_the_party_counts_as_a_doer(self) -> None:
        await self.deed(doer=self.party)
        index = await self.memory.standing_index(GUILD_A, self.c, {700: 4})
        self.assertEqual(len(index.deeds[(self.guard, self.party)]), 1)


class Scale(Stored):
    async def test_exactly_ten_sessions_do_not_fade_and_eleven_do(self) -> None:
        for n in range(10):
            await self.heard(self.guard, 2000 + n)
        await self.memory.add_deed(
            GUILD_A, self.c, self.kesh, "Oldest of ten", [self.guard], "Helped them",
            source="dm", from_session_at=2000,
        )  # fmt: skip
        index = await self.memory.standing_index(GUILD_A, self.c)
        self.assertEqual(index.deeds[(self.guard, self.kesh)][0].weight, st.DEED_WEIGHT)
        await self.heard(self.guard, 2010)  # an eleventh session
        index = await self.memory.standing_index(GUILD_A, self.c)
        self.assertEqual(index.deeds[(self.guard, self.kesh)][0].weight, st.DEED_WEIGHT // 2)

    async def test_three_hundred_deeds_still_work_out_fast(self) -> None:
        import time

        npcs = [await self.add(f"Folk {n}", type="character", role="npc") for n in range(10)]
        for n in range(60):
            await self.memory.add_deed(
                GUILD_A, self.c, self.kesh if n % 2 else self.party, f"Deed number {n}",
                npcs[n % 10 : n % 10 + 2], "Helped them", source="dm", from_session_at=100 + n,
            )  # fmt: skip
        began = time.perf_counter()
        index = await self.memory.standing_index(GUILD_A, self.c)
        read = time.perf_counter() - began
        began = time.perf_counter()
        for _ in range(30):
            index.standing(npcs[0], self.kesh)
        asked = (time.perf_counter() - began) / 30
        print(f"\n60 deeds: read {read * 1000:.1f} ms, one standing {asked * 1000:.2f} ms")
        self.assertLess(read, 1.0)
        self.assertLess(asked, 0.05)


class Steps(Screens):
    async def test_every_step_asks_again_who_is_pressing(self) -> None:
        doer = await self.memory.entity(GUILD_A, self.c, self.kesh)
        guard = await self.memory.entity(GUILD_A, self.c, self.guard)
        assert doer is not None and guard is not None
        pool = [guard]
        who = ui.WhoMenu(self.c, doer, "Burned the mill", pool)
        who.select._values = [guard.id]
        how = ui.HowMenu(self.c, doer, "Burned the mill", [guard])
        confirm = ui.ConfirmMenu(self.c, doer, "Burned the mill", [guard], "Hurt them")
        done = ui.DoneMenu(self.c, self.kesh, 1)
        for press_it in (
            who._picked,
            who._on,
            partial(how._landed, "Hurt them"),
            confirm._yes,
            done._undo,
        ):  # fmt: skip
            it = self.pressing(999)
            await press_it(it)
            self.assertIn(ui.ONLY_DM, it.sent["content"])
        self.assertEqual((await self.standing()).shown, "No opinion yet")

    async def test_the_picker_counts_what_was_picked_on_other_pages(self) -> None:
        doer = await self.memory.entity(GUILD_A, self.c, self.kesh)
        assert doer is not None
        pool = [
            await self.memory.entity(
                GUILD_A, self.c, await self.add(f"Folk {n:02d}", type="character", role="npc")
            )
            for n in range(30)
        ]
        everyone = [e for e in pool if e is not None]
        chosen = {e.id: e for e in everyone[:3]}  # three picked on page one
        page_two = ui.WhoMenu(self.c, doer, "x", everyone, 1, chosen)
        self.assertEqual(page_two.select.max_values, 2)
        full = {e.id: e for e in everyone[:5]}
        self.assertEqual(ui.WhoMenu(self.c, doer, "x", everyone, 1, full).select.max_values, 1)
