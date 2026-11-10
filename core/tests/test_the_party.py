"""The party is the first entry of every campaign (#3954): one entry with the `party` flag, made
with each new campaign, adopted once for an existing campaign from a group it already uses for
the party, joined by its player characters, and never a knower. Invented names only."""

from __future__ import annotations

from dmbot.bot import fill_parties
from dmbot.memory import gate, knowers
from dmbot.memory import party as party_mod
from dmbot.memory._changes import KNOWERS
from dmbot.memory.models import CONFIRMED, NewName, name_key
from tests.test_memory_names import DM, GUILD, NamesTest


class TheParty(NamesTest):
    async def parties(self, campaign_id: str | None = None) -> list[dict[str, object]]:
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                "SELECT id, name, status, source FROM memory_entities WHERE guild_id = %s"
                " AND campaign_id = %s AND party",
                (GUILD, campaign_id or self.campaign.id),
            )
            return list(await cur.fetchall())

    async def group(self, name: str) -> str:
        made = await self.memory.add_names(
            GUILD, self.campaign.id, [NewName(name, "faction", CONFIRMED)], source="dm"
        )
        (gid,) = made.value
        assert gid
        return gid

    async def pc(self, name: str, player: int | None) -> str:
        made = await self.memory.add_names(
            GUILD, self.campaign.id, [NewName(name, "npc", CONFIRMED)], source="dm"
        )
        (pid,) = made.value
        assert pid
        await self.memory.confirm_entity(
            GUILD, self.campaign.id, pid, "player_character", source="dm", played_by=player
        )
        return pid

    async def members(self, party_id: str) -> set[str]:
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                "SELECT subject_id FROM memory_relations WHERE guild_id = %s AND campaign_id = %s"
                " AND predicate = 'member_of' AND object_id = %s AND status = 'confirmed'"
                " AND to_session_at IS NULL",
                (GUILD, self.campaign.id, party_id),
            )
            return {str(r["subject_id"]) for r in await cur.fetchall()}

    async def test_a_new_campaign_has_one_party_when_the_hook_is_on(self) -> None:
        self.campaigns.on_create(self.memory.make_party)
        made = await self.campaigns.create(GUILD, "Sunken City", DM)
        (party,) = await self.parties(made.id)
        self.assertEqual((party["name"], party["status"]), ("The party", CONFIRMED))
        again = await self.memory.make_party(GUILD, made.id)
        self.assertEqual((again.step, again.party_id), ("had", party["id"]))

    async def test_a_player_character_joins_when_it_gets_its_player(self) -> None:
        filled = await self.memory.make_party(GUILD, self.campaign.id)
        assert filled.party_id is not None
        wren = await self.pc("Wren", 77)
        # (what the confirm and assign screens run after a character gets its player, #3363)
        await self.memory.party_on_assignment(GUILD, self.campaign.id)
        self.assertEqual(await self.members(filled.party_id), {wren})

    async def test_a_player_character_still_waiting_for_its_player_is_a_member(self) -> None:
        """#3995: every confirmed player character is in the party, a "pending" one too (the
        wizard's step 3 is optional); getting its player later changes nothing."""
        filled = await self.memory.make_party(GUILD, self.campaign.id)
        assert filled.party_id is not None
        wren = await self.pc("Wren", None)
        ash = await self.pc("Ash", 78)
        npc = await self.group("Grey Wolves")  # (a group is never a member)
        await self.memory.add_names(
            GUILD, self.campaign.id, [NewName("Old Marn", "npc", CONFIRMED)], source="dm"
        )  # (an NPC never joins)
        await self.memory.party_on_assignment(GUILD, self.campaign.id)
        self.assertEqual(await self.members(filled.party_id), {wren, ash})
        self.assertNotIn(npc, await self.members(filled.party_id))
        await self.memory.confirm_entity(
            GUILD, self.campaign.id, wren, "player_character", source="dm", played_by=79
        )
        await self.memory.party_on_assignment(GUILD, self.campaign.id)
        self.assertEqual(await self.members(filled.party_id), {wren, ash})

    async def test_a_character_the_dm_took_out_of_the_party_is_not_added_back(self) -> None:
        filled = await self.memory.make_party(GUILD, self.campaign.id)
        assert filled.party_id is not None
        wren = await self.pc("Wren", None)
        await self.memory.party_on_assignment(GUILD, self.campaign.id)
        (tie,) = [r for r in await self.memory.relations(GUILD, self.campaign.id)
                  if r.subject_id == wren and r.predicate == "member_of"]  # fmt: skip
        await self.memory.update_relation(
            GUILD, self.campaign.id, tie.id, source="dm", status="rejected"
        )
        await self.memory.party_on_assignment(GUILD, self.campaign.id)
        self.assertEqual(await self.members(filled.party_id), set())

    async def test_the_fill_flags_the_party_the_sessions_made(self) -> None:
        linked = await self.group("The party")
        async with self.db.guild(GUILD) as conn:
            await conn.execute(
                "UPDATE memory_entities SET source = 'party' WHERE guild_id = %s AND id = %s",
                (GUILD, linked),
            )
        filled = await self.memory.make_party(GUILD, self.campaign.id)
        self.assertEqual((filled.step, filled.party_id), ("linked", linked))

    async def test_the_fill_adopts_a_group_of_exactly_its_player_characters(self) -> None:
        crew = await self.group("Frost Crew")
        await self.group("Other Lot")
        a, b = await self.pc("Wren", 77), await self.pc("Torvin", 78)
        for who in (a, b):
            await self.memory.add_relation(
                GUILD, self.campaign.id, who, "member_of", crew, source="dm", status=CONFIRMED,
                confidence=1.0,
            )  # fmt: skip
        filled = await self.memory.make_party(GUILD, self.campaign.id)
        self.assertEqual((filled.step, filled.party_id), ("members", crew))
        self.assertEqual(len(await self.parties()), 1)

    async def test_the_fill_adopts_a_group_named_like_the_party(self) -> None:
        named = await self.group("The Adventurers")
        async with self.db.guild(GUILD) as conn:  # (shown to the players already)
            await conn.execute(
                "UPDATE memory_entities SET hidden_from_players = FALSE WHERE guild_id = %s"
                " AND id = %s",
                (GUILD, named),
            )
        filled = await self.memory.make_party(GUILD, self.campaign.id)
        self.assertEqual((filled.step, filled.party_id), ("named", named))

    async def test_a_rejected_party_stays_gone(self) -> None:
        filled = await self.memory.make_party(GUILD, self.campaign.id)
        async with self.db.guild(GUILD) as conn:
            await conn.execute(
                "UPDATE memory_entities SET status = 'rejected' WHERE guild_id = %s AND id = %s",
                (GUILD, filled.party_id),
            )
        again = await self.memory.make_party(GUILD, self.campaign.id)
        self.assertEqual((again.step, again.party_id), ("rejected", None))

    async def test_the_party_is_never_a_knower(self) -> None:
        filled = await self.memory.make_party(GUILD, self.campaign.id)
        assert filled.party_id is not None
        wren = await self.pc("Wren", 77)
        await self.memory.party_on_assignment(GUILD, self.campaign.id)
        self.assertIn(wren, await self.members(filled.party_id))  # (a member: the gate's case)
        secret = await self.group("Shadow Court")
        async with self.memory._write(GUILD, self.campaign.id, "dm") as w:
            with self.assertRaisesRegex(Exception, "The party can't know something"):
                await knowers.set_statement_knowers(w, "entry", secret, [filled.party_id])
        # a row on the party however it got there (an old one) never reaches a member
        async with self.memory._write(GUILD, self.campaign.id, "dm") as w:
            view = await knowers._view_for(w, filled.party_id, False)
            await w.insert(
                KNOWERS,
                {"id": "f" * 32, "perspective_id": view, "about_entity_id": secret,
                 "state_id": None, "relation_id": None, "alias_id": None,
                 "created_at": w.now, "confidence": 1.0},
            )  # fmt: skip
        async with self.memory._read(GUILD, self.campaign.id) as scope:
            data = await gate.load(scope)
        self.assertNotIn(filled.party_id, data.groups.get(wren, frozenset()))
        seen = gate.visible(gate.Viewer.character(wren), data)
        self.assertNotIn(secret, seen.entries)  # (the hidden statement stays hidden to Wren)
        self.assertEqual((await self.memory.make_party(GUILD, self.campaign.id)).knower_rows, 1)

    async def test_the_party_s_name_is_known_so_lore_never_offers_it(self) -> None:
        await self.memory.make_party(GUILD, self.campaign.id)
        self.assertIn(name_key("The party"), await self.memory.known_keys(GUILD, self.campaign.id))

    async def test_the_start_fill_counts_each_campaign_s_step(self) -> None:
        await self.campaigns.create(GUILD, "Sunken City", DM)
        steps = await fill_parties(self.campaigns, self.memory.make_party)
        self.assertEqual(steps["made"], 2)
        again = await fill_parties(self.campaigns, self.memory.make_party)
        self.assertEqual(dict(again), {"had": 2})

    async def test_a_hidden_group_or_one_with_an_npc_is_never_adopted(self) -> None:
        """The reviewer's cases: a flagged entry is always shown (a trigger), so adopting the DM's
        hidden group would show it; a group with an NPC is the DM's own faction."""
        hidden = await self.group("The Group")
        async with self.db.guild(GUILD) as conn:
            await conn.execute(
                "UPDATE memory_entities SET hidden_from_players = TRUE WHERE guild_id = %s"
                " AND id = %s",
                (GUILD, hidden),
            )
        crew = await self.group("The Adventurers")
        async with self.db.guild(GUILD) as conn:  # (shown, so only its NPC member stops it)
            await conn.execute(
                "UPDATE memory_entities SET hidden_from_players = FALSE WHERE guild_id = %s"
                " AND id = %s",
                (GUILD, crew),
            )
        npc = await self.group("Old Mott")  # (any confirmed entry as a member will do)
        await self.memory.add_relation(
            GUILD, self.campaign.id, npc, "member_of", crew, source="dm", status=CONFIRMED,
            confidence=1.0,
        )  # fmt: skip
        filled = await self.memory.make_party(GUILD, self.campaign.id)
        self.assertEqual(filled.step, "made")
        self.assertNotIn(filled.party_id, (hidden, crew))

    async def test_the_dm_s_press_brings_back_a_rejected_linked_party(self) -> None:
        linked = await self.group("The party")
        async with self.db.guild(GUILD) as conn:
            await conn.execute(
                "UPDATE memory_entities SET source = 'party', status = 'rejected'"
                " WHERE guild_id = %s AND id = %s",
                (GUILD, linked),
            )
        self.assertEqual((await self.memory.make_party(GUILD, self.campaign.id)).step, "rejected")
        party = await self.memory.ensure_party(GUILD, self.campaign.id, source="dm")
        self.assertEqual((party.id, party.status, party.party), (linked, CONFIRMED, True))

    async def test_the_start_fill_never_stops_on_a_failing_server(self) -> None:
        class Broken:
            async def guilds_with_campaigns(self) -> list[int]:
                return [GUILD, GUILD + 1]

            async def campaign_ids(self, guild_id: int) -> set[str]:
                if guild_id != GUILD:
                    raise RuntimeError("down")
                return {"c" * 32}

        async def make(guild_id: int, campaign_id: str) -> object:
            raise RuntimeError("down")

        steps = await fill_parties(Broken(), make)  # type: ignore[arg-type]
        self.assertEqual(steps["failed"], 2)

    async def test_a_campaign_with_both_parties_is_counted_and_the_flagged_one_wins(self) -> None:
        flagged = (await self.memory.ensure_party(GUILD, self.campaign.id, source="dm")).id
        linked = await self.group("The Old Party")
        async with self.db.guild(GUILD) as conn:
            await conn.execute(
                "UPDATE memory_entities SET source = 'party' WHERE guild_id = %s AND id = %s",
                (GUILD, linked),
            )
        filled = await self.memory.make_party(GUILD, self.campaign.id)
        self.assertEqual((filled.step, filled.party_id, filled.both), ("had", flagged, True))

    def test_the_names_the_fill_knows(self) -> None:
        self.assertIn("the adventurers", party_mod.PARTY_NAMES)
        self.assertNotIn("the heroes", party_mod.PARTY_NAMES)
