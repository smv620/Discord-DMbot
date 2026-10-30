"""The players who play a session together know each other and form The party (#3036): by rule,
for the player characters whose players took part, never for an NPC, one connection a pair, and
nothing the DM removed comes back. Invented names."""

from __future__ import annotations

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

    async def test_a_player_who_did_not_take_part_gets_nothing(self) -> None:
        done = await self.link(101, 102, 103)  # (Ilse was away)
        self.assertEqual((done.characters, done.pairs, done.members), (3, 3, 3))
        touched = {x for a, _, b in await self.edges("knows") for x in (a, b)}
        self.assertNotIn(self.ids["Ilse"], touched)
        self.assertNotIn(self.ids["Ilse"], {s for s, _, _ in await self.edges("member_of")})

    async def test_an_npc_never_gets_one(self) -> None:
        await self.link(*PLAYERS.values())
        every = {x for a, _, b in await self.edges("knows") for x in (a, b)}
        every |= {s for s, _, _ in await self.edges("member_of")}
        self.assertNotIn(self.ids["Rook"], every)

    async def test_one_player_alone_is_in_the_party_but_knows_nobody(self) -> None:
        done = await self.link(101)
        self.assertEqual((done.characters, done.pairs, done.members), (1, 0, 1))

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
        self.assertEqual(len([r for r in rows if r.predicate == "member_of"]), 2)
