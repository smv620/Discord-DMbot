"""Who knows a hidden connection or an other name (#2935), data layer: the DM writes it, only
player characters of the campaign can hold it, no public reader sees it, it survives a backup,
and nothing crosses campaigns. Invented names."""

from __future__ import annotations

from typing import Any

from dmbot.campaigns.store import decode_backup, encode_backup
from dmbot.memory.models import CONFIRMED, MemoryRuleError, spelling_key
from tests.test_memory_store import DM as DM_USER
from tests.test_memory_store import GUILD_A, GUILD_B, MemoryTest

DM = "dm"  # (the memory source; DM_USER is the Discord user)
TAVIN_PLAYER, MARA_PLAYER = 1001, 1002


class WhoKnows(MemoryTest):
    async def pc(self, name: str, player: int, *, campaign: str | None = None) -> str:
        return await self.add(
            name, type="character", role="player_character", played_by=player,
            status=CONFIRMED, campaign=campaign,
        )  # fmt: skip

    async def hidden_link(self, a: str, b: str, *, how: str = "secret") -> str:
        made = await self.memory.add_relation(
            GUILD_A, self.c, a, "knows", b, source=DM, confidence=1.0, status=CONFIRMED,
            secret=how == "secret", unrevealed=how == "unrevealed",
        )  # fmt: skip
        return str(made.value[0].id)

    async def setup_table(self) -> dict[str, str]:
        ids = {
            "tavin": await self.pc("Tavin", TAVIN_PLAYER),
            "mara": await self.pc("Mara", MARA_PLAYER),
            "council": await self.add("Lantern Council", type="faction", status=CONFIRMED),
            "voss": await self.add("Voss", status=CONFIRMED),
        }
        return ids

    async def party(self) -> dict[str, float]:
        return {b.relation_id: b.confidence for b in await self.memory.party_view(GUILD_A, self.c)}

    # ---- a connection ----

    async def test_a_connection_known_by_some_characters_is_seen_by_those_and_not_the_party(
        self,
    ) -> None:
        ids = await self.setup_table()
        link = await self.hidden_link(ids["voss"], ids["council"])
        done = await self.memory.set_connection_knowers(
            GUILD_A, self.c, link, [ids["tavin"]], source=DM
        )
        self.assertEqual(done.value, [ids["tavin"]])
        self.assertEqual(
            await self.memory.connection_knowers(GUILD_A, self.c, [link]), {link: [ids["tavin"]]}
        )
        self.assertNotIn(
            link, await self.party()
        )  # The party holds nothing of it: public readers see none
        shown = [r.id for r in await self.memory.relations(GUILD_A, self.c)]
        self.assertNotIn(link, shown)  # (the plain reader still hides it)

    async def test_the_dm_can_change_who_knows_and_back_to_only_the_dm(self) -> None:
        ids = await self.setup_table()
        link = await self.hidden_link(ids["voss"], ids["council"], how="unrevealed")
        both = [ids["tavin"], ids["mara"]]
        await self.memory.set_connection_knowers(GUILD_A, self.c, link, both, source=DM)
        got = await self.memory.connection_knowers(GUILD_A, self.c, [link])
        self.assertEqual(sorted(got[link]), sorted(both))
        await self.memory.set_connection_knowers(GUILD_A, self.c, link, [ids["mara"]], source=DM)
        self.assertEqual(
            await self.memory.connection_knowers(GUILD_A, self.c, [link]), {link: [ids["mara"]]}
        )
        await self.memory.set_connection_knowers(GUILD_A, self.c, link, [], source=DM)
        self.assertEqual(await self.memory.connection_knowers(GUILD_A, self.c, [link]), {})

    async def test_only_the_dm_and_only_characters_of_this_campaign(self) -> None:
        ids = await self.setup_table()
        link = await self.hidden_link(ids["voss"], ids["council"])
        place = await self.add("Harbor", type="place", status=CONFIRMED)
        merged = await self.add("Voss twice", status=CONFIRMED)
        rejected = await self.add("Not a person", status=CONFIRMED)
        async with self.db.guild(GUILD_A) as conn:  # (the store only adds live entries)
            await conn.execute(
                "UPDATE memory_entities SET status = 'merged', merged_into = %s WHERE id = %s",
                (ids["voss"], merged),
            )
            await conn.execute(
                "UPDATE memory_entities SET status = 'rejected' WHERE id = %s", (rejected,)
            )
        for who in ([place], [merged], [rejected], ["f" * 32]):
            with self.subTest(who=who):
                with self.assertRaises(MemoryRuleError):
                    await self.memory.set_connection_knowers(GUILD_A, self.c, link, who, source=DM)
                with self.assertRaises(MemoryRuleError):
                    await self.memory.check_knowers(GUILD_A, self.c, who)
        with self.assertRaises(MemoryRuleError):  # a player (or the scan) cannot say who knows
            await self.memory.set_connection_knowers(
                GUILD_A, self.c, link, [ids["tavin"]], source="character"
            )
        self.assertEqual(await self.memory.connection_knowers(GUILD_A, self.c, [link]), {})

    async def test_an_npc_can_know_a_connection_and_an_other_name(self) -> None:
        ids = await self.setup_table()
        link = await self.hidden_link(ids["voss"], ids["council"])
        npc = await self.add("Brisa", status=CONFIRMED)
        await self.memory.check_knowers(GUILD_A, self.c, [ids["tavin"], npc])
        done = await self.memory.set_connection_knowers(
            GUILD_A, self.c, link, [ids["tavin"], npc], source=DM
        )
        self.assertEqual(done.value, [ids["tavin"], npc])
        # a player's read is unchanged: only the player characters are listed there
        self.assertEqual(
            await self.memory.connection_knowers(GUILD_A, self.c, [link]), {link: [ids["tavin"]]}
        )
        self.assertEqual(  # the gate's list holds both, and saving again adds no second row
            sorted(
                (await self.memory.statement_knowers(GUILD_A, self.c, "connection", [link]))[link]
            ),
            sorted([ids["tavin"], npc]),
        )
        rows = await self.count("memory_knowers")
        await self.memory.set_connection_knowers(
            GUILD_A, self.c, link, [ids["tavin"], npc], source=DM
        )
        self.assertEqual(await self.count("memory_knowers"), rows)
        alias = await self.secret_name(ids["voss"], "Shade")
        await self.memory.set_alias_knowers(GUILD_A, self.c, alias, [npc], source=DM)
        self.assertEqual(
            await self.memory.alias_knowers(GUILD_A, self.c, [alias]), {}
        )  # (the old reader lists player characters only)
        self.assertEqual(await self.count("memory_alias_knowers"), 1)

    async def test_an_npc_not_picked_any_more_stops_knowing(self) -> None:
        ids = await self.setup_table()
        link = await self.hidden_link(ids["voss"], ids["council"])
        alias = await self.secret_name(ids["voss"], "Shade")
        npc = await self.add("Brisa", status=CONFIRMED)
        for kind, thing, setter in (
            ("connection", link, self.memory.set_connection_knowers),
            ("alias", alias, self.memory.set_alias_knowers),
        ):
            with self.subTest(kind=kind):
                await setter(GUILD_A, self.c, thing, [ids["tavin"], npc], source=DM)
                await setter(GUILD_A, self.c, thing, [ids["tavin"]], source=DM)
                got = await self.memory.statement_knowers(GUILD_A, self.c, kind, [thing])
                self.assertEqual(got.get(thing), [ids["tavin"]])
                await setter(GUILD_A, self.c, thing, [npc], source=DM)
                await setter(GUILD_A, self.c, thing, [], source=DM)
                got = await self.memory.statement_knowers(GUILD_A, self.c, kind, [thing])
                self.assertEqual(got.get(thing, []), [])

    async def test_an_npcs_certainty_survives_a_save_that_still_picks_it(self) -> None:
        ids = await self.setup_table()
        link = await self.hidden_link(ids["voss"], ids["council"])
        npc = await self.add("Brisa", status=CONFIRMED)
        await self.memory.set_statement_knowers(
            GUILD_A, self.c, "connection", link, {npc: 0.4}, source=DM
        )
        await self.memory.set_connection_knowers(GUILD_A, self.c, link, [npc], source=DM)
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute(
                "SELECT confidence FROM memory_knowers WHERE relation_id = %s", (link,)
            )
            self.assertEqual([r["confidence"] for r in await cur.fetchall()], [0.4])

    async def test_an_npc_never_becomes_a_players_view(self) -> None:
        ids = await self.setup_table()
        link = await self.hidden_link(ids["voss"], ids["council"])
        npc = await self.add("Brisa", status=CONFIRMED)
        await self.memory.set_connection_knowers(GUILD_A, self.c, link, [npc], source=DM)
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute(
                "SELECT kind FROM memory_perspectives WHERE entity_id = %s", (npc,)
            )
            self.assertEqual([r["kind"] for r in await cur.fetchall()], ["npc"])

    async def test_at_most_sixty_knowers(self) -> None:
        ids = await self.setup_table()
        link = await self.hidden_link(ids["voss"], ids["council"])
        crowd = [await self.add(f"Guard {i}", status=CONFIRMED) for i in range(61)]
        with self.assertRaises(MemoryRuleError):
            await self.memory.check_knowers(GUILD_A, self.c, crowd)
        await self.memory.check_knowers(GUILD_A, self.c, crowd[:60])
        with self.assertRaises(MemoryRuleError):
            await self.memory.set_connection_knowers(GUILD_A, self.c, link, crowd, source=DM)

    async def test_another_campaigns_character_cannot_know_this_connection(self) -> None:
        ids = await self.setup_table()
        link = await self.hidden_link(ids["voss"], ids["council"])
        elsewhere = (await self.campaigns.create(GUILD_A, "Elsewhere", DM_USER)).id
        stranger = await self.pc("Stranger", 1003, campaign=elsewhere)
        with self.assertRaises(MemoryRuleError):
            await self.memory.set_connection_knowers(GUILD_A, self.c, link, [stranger], source=DM)
        self.assertEqual(await self.memory.connection_knowers(GUILD_A, elsewhere, [link]), {})

    async def test_a_connection_everyone_knows_has_nothing_to_choose(self) -> None:
        ids = await self.setup_table()
        known = (
            await self.memory.add_relation(
                GUILD_A, self.c, ids["voss"], "knows", ids["council"], source=DM,
                confidence=1.0, status=CONFIRMED,
            )
        ).value[0].id  # fmt: skip
        with self.assertRaises(MemoryRuleError):
            await self.memory.set_connection_knowers(
                GUILD_A, self.c, known, [ids["tavin"]], source=DM
            )

    async def test_revealing_to_everyone_leaves_the_party_knowing_it_as_before(self) -> None:
        ids = await self.setup_table()
        link = await self.hidden_link(ids["voss"], ids["council"])
        await self.memory.set_connection_knowers(GUILD_A, self.c, link, [ids["tavin"]], source=DM)
        await self.memory.update_relation(GUILD_A, self.c, link, source=DM, secret=False)
        self.assertEqual((await self.party()).get(link), 1.0)  # the existing reveal, unchanged

    async def test_the_change_can_be_undone(self) -> None:
        ids = await self.setup_table()
        link = await self.hidden_link(ids["voss"], ids["council"])
        done = await self.memory.set_connection_knowers(
            GUILD_A, self.c, link, [ids["tavin"]], source=DM
        )
        assert done.batch is not None
        await self.memory.undo(GUILD_A, self.c, done.batch, source=DM)
        self.assertEqual(await self.memory.connection_knowers(GUILD_A, self.c, [link]), {})

    # ---- an other name ----

    async def secret_name(self, entity: str, text: str = "Rook") -> str:
        made = await self.memory.add_alias(
            GUILD_A, self.c, entity, text, kind="title", secret=True, source=DM, status=CONFIRMED
        )
        return str(made.value.id)

    async def test_an_other_name_known_by_some_characters(self) -> None:
        ids = await self.setup_table()
        alias = await self.secret_name(ids["tavin"])
        await self.memory.set_alias_knowers(GUILD_A, self.c, alias, [ids["tavin"]], source=DM)
        self.assertEqual(
            await self.memory.alias_knowers(GUILD_A, self.c, [alias]), {alias: [ids["tavin"]]}
        )
        await self.memory.set_alias_knowers(GUILD_A, self.c, alias, [], source=DM)
        self.assertEqual(await self.memory.alias_knowers(GUILD_A, self.c, [alias]), {})

    async def test_only_a_secret_name_can_be_known_by_some(self) -> None:
        ids = await self.setup_table()
        public = await self.memory.add_alias(
            GUILD_A, self.c, ids["voss"], "Old Voss", kind="nickname", source=DM
        )
        with self.assertRaises(MemoryRuleError):
            await self.memory.set_alias_knowers(
                GUILD_A, self.c, public.value.id, [ids["tavin"]], source=DM
            )

    async def test_who_knows_a_name_goes_with_the_name(self) -> None:
        ids = await self.setup_table()
        alias = await self.secret_name(ids["voss"], "Shade")
        done = await self.memory.set_alias_knowers(GUILD_A, self.c, alias, [ids["mara"]], source=DM)
        self.assertEqual(await self.count("memory_alias_knowers"), 1)
        async with self.db.guild(GUILD_A) as conn:  # the name is removed (an Undo of adding it)
            await conn.execute("DELETE FROM memory_aliases WHERE id = %s", (alias,))
        self.assertEqual(await self.count("memory_alias_knowers"), 0)
        self.assertEqual(done.value, [ids["mara"]])

    async def test_another_server_sees_none_of_it(self) -> None:
        ids = await self.setup_table()
        alias = await self.secret_name(ids["tavin"])
        await self.memory.set_alias_knowers(GUILD_A, self.c, alias, [ids["tavin"]], source=DM)
        self.assertEqual(await self.count("memory_alias_knowers", GUILD_B), 0)

    async def test_a_name_made_public_has_no_knowers_and_a_public_reader_never_lists_it(
        self,
    ) -> None:
        ids = await self.setup_table()
        alias = await self.secret_name(ids["tavin"], "Shade")
        await self.memory.set_alias_knowers(GUILD_A, self.c, alias, [ids["mara"]], source=DM)
        shown = [a.text for a in await self.memory.aliases(GUILD_A, self.c)]
        self.assertNotIn("Shade", shown)  # (the plain reader hides a secret name)
        await self.memory.update_alias(GUILD_A, self.c, alias, secret=False, source=DM)
        self.assertEqual(await self.memory.alias_knowers(GUILD_A, self.c, [alias]), {})
        await self.memory.update_alias(GUILD_A, self.c, alias, secret=True, source=DM)
        self.assertEqual(
            await self.memory.alias_knowers(GUILD_A, self.c, [alias]), {alias: [ids["mara"]]}
        )

    async def test_saying_the_same_again_writes_nothing_new(self) -> None:
        ids = await self.setup_table()
        link = await self.hidden_link(ids["voss"], ids["council"])
        alias = await self.secret_name(ids["tavin"], "Shade")
        for _ in range(2):
            await self.memory.set_connection_knowers(
                GUILD_A, self.c, link, [ids["tavin"]], source=DM
            )
            await self.memory.set_alias_knowers(GUILD_A, self.c, alias, [ids["tavin"]], source=DM)
        self.assertEqual(await self.count("memory_alias_knowers"), 1)
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute(
                "SELECT count(*) AS n FROM memory_beliefs b JOIN memory_perspectives p"
                " ON p.id = b.perspective_id AND p.campaign_id = b.campaign_id"
                " WHERE b.relation_id = %s AND p.kind = 'pc'",
                (link,),
            )
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual(row["n"], 1)

    async def test_taking_a_character_off_keeps_the_history_and_the_party_unchanged(self) -> None:
        ids = await self.setup_table()
        link = await self.hidden_link(ids["voss"], ids["council"])
        await self.memory.set_connection_knowers(GUILD_A, self.c, link, [ids["tavin"]], source=DM)
        await self.memory.set_connection_knowers(GUILD_A, self.c, link, [], source=DM)
        self.assertEqual(await self.memory.connection_knowers(GUILD_A, self.c, [link]), {})
        self.assertNotIn(link, await self.party())
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute(
                "SELECT count(*) AS n FROM memory_beliefs WHERE relation_id = %s", (link,)
            )
            row = await cur.fetchone()
        assert row is not None
        self.assertGreaterEqual(row["n"], 3)  # the truth, then a yes and a no from the character

    async def test_a_characters_earlier_npc_view_becomes_a_players(self) -> None:
        ids = await self.setup_table()
        async with self.db.guild(GUILD_A) as conn:  # made as an NPC's before the player joined
            await conn.execute(  # (so the view the table made for the player is the NPC's first)
                "UPDATE memory_perspectives SET kind = 'npc' WHERE entity_id = %s", (ids["tavin"],)
            )
            await conn.execute(
                "INSERT INTO memory_perspectives (guild_id, campaign_id, id, kind, entity_id,"
                " created_at) SELECT %s, %s, %s, 'npc', %s, 1 WHERE NOT EXISTS (SELECT 1 FROM"
                " memory_perspectives WHERE entity_id = %s)",
                (GUILD_A, self.c, "e" * 32, ids["tavin"], ids["tavin"]),
            )
        link = await self.hidden_link(ids["voss"], ids["council"])
        await self.memory.set_connection_knowers(GUILD_A, self.c, link, [ids["tavin"]], source=DM)
        self.assertEqual(
            await self.memory.connection_knowers(GUILD_A, self.c, [link]), {link: [ids["tavin"]]}
        )

    async def test_the_change_of_who_knows_a_name_can_be_undone(self) -> None:
        ids = await self.setup_table()
        alias = await self.secret_name(ids["voss"], "Shade")
        done = await self.memory.set_alias_knowers(
            GUILD_A, self.c, alias, [ids["tavin"]], source=DM
        )
        assert done.batch is not None
        await self.memory.undo(GUILD_A, self.c, done.batch, source=DM)
        self.assertEqual(await self.memory.alias_knowers(GUILD_A, self.c, [alias]), {})

    async def test_removing_a_name_and_undoing_it_brings_its_knowers_back(self) -> None:
        from dmbot.memory._changes import ALIASES

        ids = await self.setup_table()
        alias = await self.secret_name(ids["voss"], "Shade")
        await self.memory.set_alias_knowers(GUILD_A, self.c, alias, [ids["tavin"]], source=DM)
        async with self.memory._write(GUILD_A, self.c, DM) as w:
            await w.delete(ALIASES, alias)
            batch = w.batch
        assert batch is not None
        self.assertEqual(await self.count("memory_alias_knowers"), 0)
        await self.memory.undo(GUILD_A, self.c, batch, source=DM)
        self.assertEqual(
            await self.memory.alias_knowers(GUILD_A, self.c, [alias]), {alias: [ids["tavin"]]}
        )

    async def test_who_knows_a_name_follows_a_merged_character(self) -> None:
        ids = await self.setup_table()
        twin = await self.pc("Tavin the Younger", TAVIN_PLAYER)
        alias = await self.secret_name(ids["voss"], "Shade")
        await self.memory.set_alias_knowers(GUILD_A, self.c, alias, [twin], source=DM)
        await self.memory.merge(GUILD_A, self.c, ids["tavin"], twin, source=DM, dm_said_same=True)
        self.assertEqual(
            await self.memory.alias_knowers(GUILD_A, self.c, [alias]), {alias: [ids["tavin"]]}
        )

    # ---- who a viewer plays ----

    async def test_a_player_plays_only_their_own_confirmed_characters(self) -> None:
        ids = await self.setup_table()
        self.assertEqual(
            await self.memory.characters_of(GUILD_A, self.c, TAVIN_PLAYER), [ids["tavin"]]
        )
        self.assertEqual(await self.memory.characters_of(GUILD_A, self.c, 4242), [])

    # ---- a player's own view (slice B) ----

    async def mine(self, user: int, campaign: str | None = None, guild: int = GUILD_A) -> Any:
        return await self.memory.what_my_characters_know(guild, campaign or self.c, user)

    async def test_a_player_sees_what_their_character_knows_and_who_else_does(self) -> None:
        ids = await self.setup_table()
        link = await self.hidden_link(ids["voss"], ids["council"])
        alias = await self.secret_name(ids["tavin"], "Rook")
        both = [ids["tavin"], ids["mara"]]
        await self.memory.set_connection_knowers(GUILD_A, self.c, link, both, source=DM)
        await self.memory.set_alias_knowers(GUILD_A, self.c, alias, [ids["tavin"]], source=DM)
        tavin = await self.mine(TAVIN_PLAYER)
        self.assertEqual(tavin.characters, ("Tavin",))
        (connection,) = tavin.connections
        self.assertEqual(
            (connection.subject, connection.object, connection.also),
            ("Voss", "Lantern Council", ("Mara",)),
        )
        (name,) = tavin.names
        self.assertEqual((name.text, name.of, name.also), ("Rook", "Tavin", ()))
        mara = await self.mine(MARA_PLAYER)
        self.assertEqual(len(mara.connections), 1)
        self.assertEqual(mara.names, ())  # the name is Tavin's alone

    async def test_another_player_a_stranger_and_another_campaign_see_nothing(self) -> None:
        ids = await self.setup_table()
        link = await self.hidden_link(ids["voss"], ids["council"])
        alias = await self.secret_name(ids["tavin"])
        await self.memory.set_connection_knowers(GUILD_A, self.c, link, [ids["tavin"]], source=DM)
        await self.memory.set_alias_knowers(GUILD_A, self.c, alias, [ids["tavin"]], source=DM)
        for user in (MARA_PLAYER, 4242):  # a player whose character does not know it; no character
            seen = await self.mine(user)
            self.assertEqual((seen.connections, seen.names), ((), ()), user)
        # this campaign asked for through another server is not found at all
        with self.assertRaises(MemoryRuleError):
            await self.mine(TAVIN_PLAYER, guild=GUILD_B, campaign=self.c)

    async def test_a_connection_everyone_now_knows_is_public_and_no_longer_listed(self) -> None:
        ids = await self.setup_table()
        link = await self.hidden_link(ids["voss"], ids["council"])
        await self.memory.set_connection_knowers(GUILD_A, self.c, link, [ids["tavin"]], source=DM)
        await self.memory.reveal_exposed(GUILD_A, self.c, spellings=[], relation_ids=[link])
        self.assertEqual((await self.mine(TAVIN_PLAYER)).connections, ())
        self.assertEqual(await self.memory.connection_knowers(GUILD_A, self.c, [link]), {})

    async def test_a_name_the_table_said_is_public_and_no_longer_listed(self) -> None:
        ids = await self.setup_table()
        made = await self.memory.add_alias(
            GUILD_A, self.c, ids["tavin"], "Rook", kind="nickname", source=DM,
            status=CONFIRMED, secret=True,
        )  # fmt: skip
        alias = str(made.value.id)
        await self.memory.set_alias_knowers(GUILD_A, self.c, alias, [ids["tavin"]], source=DM)
        await self.memory.reveal_exposed(
            GUILD_A, self.c, spellings=[spelling_key("Rook")], relation_ids=[]
        )
        self.assertEqual((await self.mine(TAVIN_PLAYER)).names, ())

    async def test_a_proposed_name_a_merged_owner_and_a_proposed_link_never_show(self) -> None:
        ids = await self.setup_table()
        made = await self.memory.add_alias(
            GUILD_A, self.c, ids["tavin"], "Rook", kind="nickname", source=DM, secret=True
        )  # (not confirmed: it is still only proposed)
        await self.memory.set_alias_knowers(
            GUILD_A, self.c, str(made.value.id), [ids["tavin"]], source=DM
        )
        self.assertEqual((await self.mine(TAVIN_PLAYER)).names, ())
        # a character who knows nothing still gets their name in the line
        seen = await self.mine(MARA_PLAYER)
        self.assertEqual(seen.characters, ("Mara",))

    async def test_a_player_with_two_characters_sees_what_either_knows(self) -> None:
        ids = await self.setup_table()
        second = await self.pc("Brann", TAVIN_PLAYER)
        link = await self.hidden_link(ids["voss"], ids["council"])
        await self.memory.set_connection_knowers(GUILD_A, self.c, link, [second], source=DM)
        seen = await self.mine(TAVIN_PLAYER)
        self.assertEqual(seen.characters, ("Brann", "Tavin"))
        self.assertEqual(len(seen.connections), 1)

    async def test_an_ended_connection_is_not_shown(self) -> None:
        ids = await self.setup_table()
        link = await self.hidden_link(ids["voss"], ids["council"])
        await self.memory.set_connection_knowers(GUILD_A, self.c, link, [ids["tavin"]], source=DM)
        await self.memory.update_relation(GUILD_A, self.c, link, source=DM, status="rejected")
        self.assertEqual((await self.mine(TAVIN_PLAYER)).connections, ())

    async def test_what_the_dm_took_back_is_gone_from_the_players_view(self) -> None:
        ids = await self.setup_table()
        link = await self.hidden_link(ids["voss"], ids["council"])
        await self.memory.set_connection_knowers(GUILD_A, self.c, link, [ids["tavin"]], source=DM)
        await self.memory.set_connection_knowers(GUILD_A, self.c, link, [], source=DM)
        self.assertEqual((await self.mine(TAVIN_PLAYER)).connections, ())

    # ---- backups ----

    async def test_a_backup_keeps_who_knows(self) -> None:
        ids = await self.setup_table()
        link = await self.hidden_link(ids["voss"], ids["council"])
        alias = await self.secret_name(ids["tavin"])
        await self.memory.set_connection_knowers(GUILD_A, self.c, link, [ids["tavin"]], source=DM)
        await self.memory.set_alias_knowers(GUILD_A, self.c, alias, [ids["mara"]], source=DM)
        raw = encode_backup(await self.campaigns.export(GUILD_A, self.c))
        restored = await self.campaigns.import_backup(GUILD_B, decode_backup(raw), DM_USER)
        self.assertEqual(await self.count("memory_alias_knowers", GUILD_B), 1)
        copy: dict[str, Any] = await self.memory.connection_knowers(GUILD_B, restored.id, [link])
        self.assertEqual(copy, {link: [ids["tavin"]]})
        self.assertEqual(
            await self.memory.alias_knowers(GUILD_B, restored.id, [alias]), {alias: [ids["mara"]]}
        )
