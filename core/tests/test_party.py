"""The players who play a session together know each other and form The party (#3036): by rule,
for the player characters whose players took part, never for an NPC, one connection a pair, and
nothing the DM removed comes back. Invented names."""

from __future__ import annotations

import time
from typing import Any

from dmbot.memory import party
from dmbot.memory.models import CONFIRMED, REJECTED
from tests.test_memory_names import GUILD, NamesTest

PLAYERS = {"Mara": 101, "Tavin": 102, "Brann": 103, "Ilse": 104}


class TheParty(NamesTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.ids: dict[str, str] = {}
        for name, user in PLAYERS.items():
            made = await self.memory.add_entity(
                GUILD, self.campaign.id, name=name, type="character", role="player_character",
                played_by=user, status=CONFIRMED, source="dm",
            )  # fmt: skip
            self.ids[name] = made.value.id
        npc = await self.memory.add_entity(
            GUILD, self.campaign.id, name="Rook", type="character", role="npc",
            status=CONFIRMED, source="dm",
        )  # fmt: skip
        self.ids["Rook"] = npc.value.id

    async def link(self, *users: int) -> party.PartyLinked:
        return await self.memory.link_party(GUILD, self.campaign.id, set(users))

    async def edges(self, predicate: str) -> list[tuple[str, str, str]]:
        out = await self.memory.relations(GUILD, self.campaign.id, include_secret=True)
        return [(r.subject_id, r.predicate, r.object_id) for r in out if r.predicate == predicate]

    async def the_party(self) -> list[str]:
        every = await self.memory.entities(GUILD, self.campaign.id)
        return [e.id for e in every if e.source == "party"]

    async def test_four_characters_make_six_pairs_and_four_members(self) -> None:
        done = await self.link(*PLAYERS.values())
        self.assertEqual(
            (done.characters, done.pairs, done.members, done.made_party), (4, 6, 4, True)
        )
        self.assertEqual(len(await self.edges("knows")), 6)
        (faction,) = await self.the_party()
        members = await self.edges("member_of")
        self.assertEqual({o for _, _, o in members}, {faction})
        self.assertEqual(len(members), 4)

    async def test_a_player_who_did_not_take_part_is_a_member_but_knows_nobody_yet(self) -> None:
        done = await self.link(101, 102, 103)  # (Ilse was away)
        self.assertEqual((done.characters, done.pairs, done.members), (3, 3, 4))
        touched = {x for a, _, b in await self.edges("knows") for x in (a, b)}
        self.assertNotIn(self.ids["Ilse"], touched)
        self.assertIn(self.ids["Ilse"], {s for s, _, _ in await self.edges("member_of")})

    async def test_no_session_players_still_adds_every_assigned_character(self) -> None:
        done = await self.link()
        self.assertEqual((done.pairs, done.members), (0, 4))
        self.assertEqual(len(await self.edges("knows")), 0)

    async def test_a_character_with_no_player_is_not_a_member(self) -> None:
        await self.link(*PLAYERS.values())
        self.assertNotIn(self.ids["Rook"], {s for s, _, _ in await self.edges("member_of")})

    async def second_for_mara(self, name: str = "Mara the Second") -> str:
        made = await self.memory.add_entity(
            GUILD, self.campaign.id, name=name, type="character",
            role="player_character", played_by=101, status=CONFIRMED, source="dm",
        )  # fmt: skip
        return made.value.id

    async def test_a_player_with_two_characters_has_both_in_and_both_took_part(self) -> None:
        """#3998, the owner (2026-11-10): a player may play several characters at once. Both are
        members, both took part and know the others, and none leaves for being older."""
        await self.link(*PLAYERS.values())
        self.memory._clock = lambda: time.time() + 60  # (a later second: "newer" would matter)
        second = await self.second_for_mara()
        done = await self.link(*PLAYERS.values())
        self.assertEqual((done.characters, done.members), (5, 1))  # (Mara's two took part)
        members = {s for s, _, _ in await self.edges("member_of")}
        self.assertTrue({self.ids["Mara"], second} <= members)
        knows = await self.edges("knows")
        for other in ("Tavin", "Brann", "Ilse", "Mara"):  # (Mara: the same player's other one)
            pair = {second, self.ids[other]}
            self.assertTrue(any({a, b} == pair for a, _, b in knows))
        out = await self.memory.relations(GUILD, self.campaign.id, include_secret=True)
        ties = [r for r in out if r.predicate == "member_of" and r.subject_id == self.ids["Mara"]]
        self.assertEqual([r.to_session_at for r in ties], [None])  # (still in, not history)
        third = await self.second_for_mara("Mara the Third")
        await self.link(101)
        members = {s for s, _, _ in await self.edges("member_of")}
        self.assertTrue({self.ids["Mara"], second, third} <= members)

    async def test_the_dm_taking_one_of_a_players_characters_out_sticks(self) -> None:
        """#3998: a character leaves only when the DM says so, across a new session and a start
        fill; the player's other character stays in."""
        await self.link(*PLAYERS.values())
        second = await self.second_for_mara()
        await self.link(101)
        out = await self.memory.relations(GUILD, self.campaign.id, include_secret=True)
        tie = next(r for r in out if r.predicate == "member_of" and r.subject_id == second)
        await self.memory.update_relation(
            GUILD, self.campaign.id, tie.id, source="dm", status=REJECTED
        )
        await self.link(*PLAYERS.values())
        await self.memory.make_party(GUILD, self.campaign.id)
        await self.memory.party_on_assignment(GUILD, self.campaign.id)
        members = {s for s, _, _ in await self.edges("member_of")}
        self.assertNotIn(second, members)
        self.assertIn(self.ids["Mara"], members)

    async def test_the_fill_counts_memberships_ended_beside_another_character(self) -> None:
        """#3998: what the old "only the newest" rule closed is counted, never reopened."""
        await self.link(*PLAYERS.values())
        await self.second_for_mara()
        await self.link(101)
        out = await self.memory.relations(GUILD, self.campaign.id, include_secret=True)
        tie = next(
            r for r in out if r.predicate == "member_of" and r.subject_id == self.ids["Mara"]
        )
        await self.memory.update_relation(
            GUILD, self.campaign.id, tie.id, source="dm", to_session_at=int(time.time()) + 5
        )
        filled = await self.memory.make_party(GUILD, self.campaign.id)
        self.assertEqual(filled.ended, 1)
        await self.link(101)  # (an ended membership is "had": never re-added by the rule)
        out = await self.memory.relations(GUILD, self.campaign.id, include_secret=True)
        mine = [r for r in out if r.predicate == "member_of" and r.subject_id == self.ids["Mara"]]
        self.assertEqual(len(mine), 1)
        self.assertIsNotNone(mine[0].to_session_at)  # (still ended: never reopened)

    async def test_an_ended_membership_with_no_other_character_is_not_counted(self) -> None:
        await self.link(*PLAYERS.values())
        out = await self.memory.relations(GUILD, self.campaign.id, include_secret=True)
        tie = next(
            r for r in out if r.predicate == "member_of" and r.subject_id == self.ids["Tavin"]
        )
        await self.memory.update_relation(
            GUILD, self.campaign.id, tie.id, source="dm", to_session_at=int(time.time()) + 5
        )
        self.assertEqual((await self.memory.make_party(GUILD, self.campaign.id)).ended, 0)

    async def test_a_character_the_dm_took_out_makes_no_new_played_together_links(self) -> None:
        """#3998, the reviewer: one player may play several, so a retired character is told apart
        only by the DM taking it out of the party; it then meets nobody new."""
        await self.link(101, 102)
        out = await self.memory.relations(GUILD, self.campaign.id, include_secret=True)
        tie = next(
            r for r in out if r.predicate == "member_of" and r.subject_id == self.ids["Mara"]
        )
        await self.memory.update_relation(
            GUILD, self.campaign.id, tie.id, source="dm", status=REJECTED
        )
        done = await self.link(*PLAYERS.values())
        self.assertEqual(done.characters, 3)
        knows = await self.edges("knows")
        self.assertEqual(len([1 for a, _, b in knows if self.ids["Mara"] in (a, b)]), 1)  # (Tavin)
        self.assertTrue(any(self.ids["Ilse"] in (a, b) for a, _, b in knows))

    async def test_a_member_the_dm_removed_who_did_not_speak_stays_removed(self) -> None:
        await self.link()
        out = await self.memory.relations(GUILD, self.campaign.id, include_secret=True)
        tie = next(
            r for r in out if r.predicate == "member_of" and r.subject_id == self.ids["Ilse"]
        )
        await self.memory.update_relation(
            GUILD, self.campaign.id, tie.id, source="dm", status=REJECTED
        )
        again = await self.link(101)
        self.assertEqual(again.members, 0)
        out = await self.memory.relations(GUILD, self.campaign.id, include_secret=True)
        live = [r for r in out if r.subject_id == self.ids["Ilse"] and r.status == CONFIRMED]
        self.assertEqual(live, [])

    async def test_an_npc_never_gets_one(self) -> None:
        await self.link(*PLAYERS.values())
        every = {x for a, _, b in await self.edges("knows") for x in (a, b)}
        every |= {s for s, _, _ in await self.edges("member_of")}
        self.assertNotIn(self.ids["Rook"], every)

    async def test_one_player_alone_knows_nobody_but_every_assigned_character_is_in(self) -> None:
        done = await self.link(101)
        self.assertEqual((done.characters, done.pairs, done.members), (1, 0, 4))

    async def test_a_pair_that_already_knows_each_other_gets_nothing_more(self) -> None:
        await self.memory.add_relation(
            GUILD,
            self.campaign.id,
            self.ids["Tavin"],
            "knows",
            self.ids["Mara"],
            source="dm",
            confidence=1.0,
            status=CONFIRMED,
        )  # fmt: skip (the other way round counts)
        done = await self.link(101, 102)
        self.assertEqual(done.pairs, 0)
        self.assertEqual(len(await self.edges("knows")), 1)

    async def test_running_it_twice_changes_nothing(self) -> None:
        await self.link(*PLAYERS.values())
        before = await self.memory.version(GUILD, self.campaign.id)
        again = await self.link(*PLAYERS.values())
        self.assertEqual((again.pairs, again.members, again.made_party), (0, 0, False))
        self.assertEqual(len(await self.edges("knows")), 6)
        self.assertEqual(len(await self.the_party()), 1)
        _ = before

    async def test_a_connection_the_dm_removed_is_not_added_again(self) -> None:
        await self.link(*PLAYERS.values())
        (gone,) = [
            r
            for r in await self.memory.relations(GUILD, self.campaign.id)
            if r.predicate == "knows"
            and {r.subject_id, r.object_id} == {self.ids["Mara"], self.ids["Tavin"]}
        ]
        await self.memory.update_relation(
            GUILD, self.campaign.id, gone.id, source="dm", status=REJECTED
        )
        again = await self.link(*PLAYERS.values())
        self.assertEqual(again.pairs, 0)
        shown = {frozenset((a, b)) for a, _, b in await self.edges("knows")}
        self.assertNotIn(frozenset((self.ids["Mara"], self.ids["Tavin"])), shown)

    async def test_a_party_the_dm_removed_is_not_made_again_or_filled(self) -> None:
        await self.link(*PLAYERS.values())
        (faction,) = await self.the_party()
        await self.memory.set_entity_status(
            GUILD, self.campaign.id, faction, source="dm", status=REJECTED
        )
        done = await self.link(*PLAYERS.values())
        self.assertEqual((done.members, done.made_party), (0, False))
        self.assertEqual(
            [
                e.status
                for e in await self.memory.entities(GUILD, self.campaign.id, statuses=[REJECTED])
                if e.source == "party"
            ],
            [REJECTED],
        )
        self.assertEqual(await self.the_party(), [])  # (no live one, none made again)

    async def test_the_party_keeps_the_dms_name_for_it(self) -> None:
        await self.link(101, 102)
        (faction,) = await self.the_party()
        await self.memory.rename_entity(
            GUILD, self.campaign.id, faction, "The Lanterns", source="dm"
        )
        await self.link(*PLAYERS.values())
        self.assertEqual(await self.the_party(), [faction])  # not a second one
        names = {e.id: e.name for e in await self.memory.entities(GUILD, self.campaign.id)}
        self.assertEqual(names[faction], "The Lanterns")

    async def test_it_is_one_change_and_one_undo_takes_it_all_back(self) -> None:
        before = await self.memory.version(GUILD, self.campaign.id)
        await self.link(*PLAYERS.values())
        (batch,) = {
            r["batch"] for r in await self._log_since(before) if r["table_name"] != "memory_knowers"
        }
        await self.memory.undo(GUILD, self.campaign.id, batch, source="dm")
        self.assertEqual(await self.edges("knows"), [])
        self.assertEqual(await self.the_party(), [])

    async def _log_since(self, version: int) -> list[dict[str, Any]]:
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                "SELECT batch, table_name FROM memory_changes WHERE guild_id = %s"
                " AND campaign_id = %s AND version > %s",
                (GUILD, self.campaign.id, version),
            )
            return [dict(r) for r in await cur.fetchall()]

    async def test_the_connections_are_shown_to_the_players(self) -> None:
        await self.link(101, 102)
        rows = await self.memory.relations(GUILD, self.campaign.id)  # (the public reader)
        self.assertEqual(len([r for r in rows if r.predicate == "knows"]), 1)
        self.assertEqual(len([r for r in rows if r.predicate == "member_of"]), 4)
