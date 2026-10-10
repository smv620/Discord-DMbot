"""One kind for each memory entry, in Postgres (#1034): roles, links to the rules, the
older kinds read as a character with a role, spells refused, undo, merges, backups (older
ones too), isolation, and the migration on tables that hold rows."""

from __future__ import annotations

import copy
import json
from typing import Any

from dmbot.campaigns import CampaignStore
from dmbot.campaigns.store import decode_backup, encode_backup
from dmbot.db import Database, drop_schema
from dmbot.memory.models import (
    CONFIRMED,
    PROPOSED,
    MemoryRuleError,
    NewName,
)
from dmbot.memory.ontology import SPELL_NOT_KEPT, Ontology, TypeTerm
from dmbot.memory.store import MemoryStore
from dmbot.schema import MIGRATIONS
from tests.pg import TEST_URL
from tests.test_memory_store import GUILD_A, GUILD_B, MemoryTest

DM, PLAYER = 7, 55


class Roles(MemoryTest):
    async def test_snot_and_auril(self) -> None:
        snot = await self.memory.add_entity(
            GUILD_A, self.c, type="character", role="npc", name="Snot", source="dm"
        )
        auril = await self.memory.add_entity(
            GUILD_A, self.c, type="character", role="god", name="Auril", source="dm"
        )
        self.assertEqual((snot.value.type, snot.value.role), ("character", "npc"))
        self.assertEqual((auril.value.type, auril.value.role), ("character", "god"))
        self.assertEqual((snot.value.needs_look, snot.value.played_by), (False, None))

    async def test_the_older_kinds_are_stored_as_a_character_with_a_role(self) -> None:
        for old, role in (("npc", "npc"), ("deity", "god"), ("creature", "npc")):
            made = await self.memory.add_entity(
                GUILD_A, self.c, type=old, name=f"Some {old}", source="dm"
            )
            self.assertEqual((made.value.type, made.value.role), ("character", role), old)
        pc = await self.memory.add_entity(
            GUILD_A, self.c, type="player_character", name="Testa", source="dm", played_by=PLAYER
        )
        self.assertEqual(
            (pc.value.type, pc.value.role, pc.value.played_by),
            ("character", "player_character", PLAYER),
        )

    async def test_a_spell_is_not_an_entry(self) -> None:
        with self.assertRaisesRegex(MemoryRuleError, "/dmbot rule"):
            await self.memory.add_entity(
                GUILD_A, self.c, type="spell", name="Fireball", source="dm"
            )
        with self.assertRaises(MemoryRuleError):
            await self.memory.add_names(
                GUILD_A, self.c, [NewName("Fireball", "spell", CONFIRMED)], source="dm"
            )
        self.assertEqual(await self.memory.entities(GUILD_A, self.c), [])
        self.assertIn("/dmbot rule", SPELL_NOT_KEPT)

    async def test_only_a_character_has_a_role_and_only_a_player_character_has_a_player(
        self,
    ) -> None:
        for kw in (
            {"type": "place", "role": "npc"},
            {"type": "character", "role": "demigod"},
            {"type": "character", "role": "npc", "played_by": PLAYER},
            {"type": "character", "played_by": PLAYER},
            {"type": "item", "played_by": PLAYER},
        ):
            with self.subTest(kw), self.assertRaises(MemoryRuleError):
                await self.memory.add_entity(GUILD_A, self.c, name="X", source="dm", **kw)
        self.assertEqual(await self.memory.entities(GUILD_A, self.c), [])

    async def test_names_from_a_list_carry_their_role(self) -> None:
        ids = (
            await self.memory.add_names(
                GUILD_A,
                self.c,
                [
                    NewName("Snot", "npc", CONFIRMED),
                    NewName("Auril", "character", CONFIRMED, role="god"),
                    NewName("Rex", "creature", PROPOSED),
                    NewName("Tower", "place", CONFIRMED),
                ],
                source="dm",
            )
        ).value
        by_name = {e.name: e for e in await self.memory.entities(GUILD_A, self.c)}
        self.assertEqual(len(ids), 4)
        self.assertEqual(
            {n: (e.type, e.role) for n, e in by_name.items()},
            {
                "Snot": ("character", "npc"),
                "Auril": ("character", "god"),
                "Rex": ("character", "npc"),
                "Tower": ("place", None),
            },
        )
        with self.assertRaises(MemoryRuleError):  # a player's character needs its player
            await self.memory.add_names(
                GUILD_A, self.c, [NewName("Testa", "player_character", CONFIRMED)], source="dm"
            )

    async def test_the_dm_changes_what_it_is_and_the_role_follows(self) -> None:
        e = await self.add("Thing", type="character", role="npc", played_by=None)
        changed = await self.memory.set_entity_type(GUILD_A, self.c, e, "place", source="dm")
        self.assertEqual((changed.value.type, changed.value.role), ("place", None))
        again = await self.memory.set_entity_type(
            GUILD_A, self.c, e, "character", source="dm", role="god"
        )
        self.assertEqual((again.value.type, again.value.role), ("character", "god"))
        pc = await self.memory.confirm_entity(
            GUILD_A, self.c, e, "player_character", source="dm", played_by=PLAYER
        )
        self.assertEqual((pc.value.role, pc.value.played_by), ("player_character", PLAYER))
        npc = await self.memory.set_entity_type(GUILD_A, self.c, e, "npc", source="dm")
        self.assertEqual((npc.value.role, npc.value.played_by), ("npc", None))  # not played now
        assert npc.batch is not None
        await self.memory.undo(GUILD_A, self.c, npc.batch, source="dm")
        back = await self.memory.entity(GUILD_A, self.c, e)
        assert back is not None
        self.assertEqual((back.role, back.played_by), ("player_character", PLAYER))

    async def test_confirming_a_group_gives_them_all_the_role(self) -> None:
        written = await self.memory.add_names(
            GUILD_A,
            self.c,
            [NewName("Mage 1", "concept", PROPOSED), NewName("Mage 2", "concept", PROPOSED)],
            source="scan",
        )
        ids = [i for i in written.value if i is not None]
        done = await self.memory.confirm_kinds(GUILD_A, self.c, ids, "npc", source="dm")
        self.assertEqual(done.value, 2)
        confirmed = await self.memory.entities(GUILD_A, self.c, statuses=[CONFIRMED])
        self.assertEqual({(e.type, e.role) for e in confirmed}, {("character", "npc")})

    async def test_two_with_different_roles_merge_only_if_the_dm_says_so(self) -> None:
        npc = await self.add("Bell", type="character", role="npc", status=PROPOSED)
        god = await self.add("Belleros", type="character", role="god", status=PROPOSED)
        with self.assertRaises(MemoryRuleError):
            await self.memory.merge(GUILD_A, self.c, npc, god, source="entitybot")
        merged = await self.memory.merge(GUILD_A, self.c, npc, god, source="dm", dm_said_same=True)
        self.assertEqual(merged.value.id, npc)

    async def test_a_campaigns_own_kind_under_character_may_have_a_role(self) -> None:
        await self.memory.add_type(
            GUILD_A,
            self.c,
            TypeTerm("horse", "character", "named horse", "A named horse.", core=False),
            examples=["Shadowfax"],
            reason="Mounts matter in this campaign.",
            source="entitybot",
        )
        horse = await self.memory.add_entity(
            GUILD_A, self.c, type="horse", role="npc", name="Dobbin", source="dm"
        )
        self.assertEqual((horse.value.type, horse.value.role), ("horse", "npc"))

    async def test_a_role_or_a_rules_word_cannot_be_added_as_a_kind(self) -> None:
        for key, label in (("goblin", "goblin"), ("pc", "PC"), ("monster", "monster")):
            with self.subTest(key), self.assertRaises(MemoryRuleError):
                await self.memory.add_type(
                    GUILD_A,
                    self.c,
                    TypeTerm(key, "character", label, "d", core=False),
                    examples=["x"],
                    reason="y",
                    source="entitybot",
                )


class Links(MemoryTest):
    async def snot(self) -> str:
        return await self.add("Snot", type="character", role="npc", status=CONFIRMED)

    async def link(self, entity: str, kind: str, name: str, **kw: Any) -> Any:
        kw.setdefault("source", "dm")
        return await self.memory.set_rule_link(GUILD_A, self.c, entity, kind, name, **kw)

    async def test_a_character_links_to_its_species_type_and_stat_block(self) -> None:
        snot = await self.snot()
        await self.link(snot, "species", "goblin", rules_source="srd52", known=True)
        await self.link(snot, "creature_type", "humanoid", rules_source="srd52", known=True)
        await self.link(snot, "stat_block", "Goblin Warrior", rules_source="srd52", known=True)
        links = await self.memory.rule_links(GUILD_A, self.c, {snot})
        self.assertEqual(
            sorted((x.kind, x.name, x.known) for x in links),
            [
                ("creature_type", "humanoid", True),
                ("species", "goblin", True),
                ("stat_block", "Goblin Warrior", True),
            ],
        )

    async def test_one_species_and_one_type_but_any_number_of_classes(self) -> None:
        snot = await self.snot()
        await self.link(snot, "species", "goblin", rules_source="srd52", known=True)
        await self.link(snot, "species", "hobgoblin", rules_source="srd52", known=True)
        await self.link(snot, "class", "wizard", rules_source="srd52", known=True)
        await self.link(snot, "class", "rogue", rules_source="srd52", known=True)
        await self.link(snot, "class", "rogue", rules_source="srd52", known=True)  # not twice
        links = await self.memory.rule_links(GUILD_A, self.c, {snot})
        self.assertEqual(
            sorted((x.kind, x.name) for x in links),
            [("class", "rogue"), ("class", "wizard"), ("species", "hobgoblin")],
        )

    async def test_a_name_not_in_the_rules_keeps_its_words_and_is_marked(self) -> None:
        snot = await self.snot()
        made = await self.link(snot, "species", "Gnoll prince")
        self.assertEqual(
            (made.value.name, made.value.known, made.value.rules_source),
            ("Gnoll prince", False, ""),
        )
        with self.assertRaises(MemoryRuleError):  # "known" needs a source
            await self.link(snot, "class", "wizard", known=True)

    async def test_only_a_character_is_linked_and_the_words_are_checked(self) -> None:
        tower = await self.add("Tower", type="place")
        for entity, kind, name, kw in (
            (tower, "species", "goblin", {}),
            (await self.snot(), "alignment", "evil", {}),
            (await self.snot(), "species", "  ", {}),
            (await self.snot(), "species", "x" * 101, {}),
            (await self.snot(), "species", "elf", {"edition": "1999"}),
        ):
            with self.subTest(kind=kind, name=name[:5]), self.assertRaises(MemoryRuleError):
                await self.link(entity, kind, name, **kw)
        self.assertEqual(await self.memory.rule_links(GUILD_A, self.c), [])

    async def test_links_can_be_removed_and_the_removal_undone(self) -> None:
        snot = await self.snot()
        made = await self.link(snot, "species", "goblin", rules_source="srd52", known=True)
        removed = await self.memory.remove_rule_link(GUILD_A, self.c, made.value.id, source="dm")
        self.assertEqual(await self.memory.rule_links(GUILD_A, self.c), [])
        assert removed.batch is not None
        await self.memory.undo(GUILD_A, self.c, removed.batch, source="dm")
        back = await self.memory.rule_links(GUILD_A, self.c)
        self.assertEqual([(x.kind, x.name) for x in back], [("species", "goblin")])

    async def test_only_the_dm_removes_a_link(self) -> None:
        snot = await self.snot()
        made = await self.link(snot, "species", "goblin", rules_source="srd52", known=True)
        with self.assertRaises(MemoryRuleError):
            await self.memory.remove_rule_link(GUILD_A, self.c, made.value.id, source="entitybot")
        self.assertEqual(len(await self.memory.rule_links(GUILD_A, self.c)), 1)

    async def test_a_new_species_replaces_the_old_and_undo_puts_it_back(self) -> None:
        snot = await self.snot()
        await self.link(snot, "species", "goblin", rules_source="srd52", known=True)
        swapped = await self.link(snot, "species", "hobgoblin", rules_source="srd52", known=True)
        assert swapped.batch is not None
        await self.memory.undo(GUILD_A, self.c, swapped.batch, source="dm")
        links = await self.memory.rule_links(GUILD_A, self.c, {snot})
        self.assertEqual([x.name for x in links], ["goblin"])

    async def test_merging_moves_the_links_that_fit(self) -> None:
        keep = await self.add("Snot", type="character", role="npc")
        gone = await self.add("Snotling", type="character", role="npc")
        await self.link(keep, "species", "goblin", rules_source="srd52", known=True)
        await self.link(gone, "species", "hobgoblin", rules_source="srd52", known=True)
        await self.link(gone, "class", "rogue", rules_source="srd52", known=True)
        await self.memory.merge(GUILD_A, self.c, keep, gone, source="dm", dm_said_same=True)
        links = await self.memory.rule_links(GUILD_A, self.c, {keep})
        self.assertEqual(
            sorted((x.kind, x.name) for x in links), [("class", "rogue"), ("species", "goblin")]
        )

    async def test_links_reach_the_lookup_for_the_cards(self) -> None:
        snot = await self.snot()
        await self.link(snot, "species", "goblin", rules_source="srd52", known=True)
        data = await self.memory.lookup_data(GUILD_A, self.c)
        self.assertEqual([(x.entity_id, x.name) for x in data.links], [(snot, "goblin")])

    async def test_another_campaign_and_another_server_see_none(self) -> None:
        snot = await self.snot()
        await self.link(snot, "species", "goblin", rules_source="srd52", known=True)
        other = (await self.campaigns.create(GUILD_A, "Sunken City", DM)).id
        self.assertEqual(await self.memory.rule_links(GUILD_A, other), [])
        self.assertEqual(await self.count("memory_rule_links", GUILD_B), 0)
        with self.assertRaises(MemoryRuleError):
            await self.memory.rule_links(GUILD_B, self.c)
        with self.assertRaises(MemoryRuleError):  # a link on another campaign's character
            await self.memory.set_rule_link(GUILD_A, other, snot, "class", "wizard", source="dm")

    async def test_the_links_go_with_the_campaign(self) -> None:
        snot = await self.snot()
        await self.link(snot, "species", "goblin", rules_source="srd52", known=True)
        self.assertEqual(await self.count("memory_rule_links"), 1)
        await self.campaigns.delete(GUILD_A, self.c)
        self.assertEqual(await self.count("memory_rule_links"), 0)


class Backups(MemoryTest):
    async def fill(self) -> tuple[str, str]:
        snot = await self.add("Snot", type="character", role="npc", status=CONFIRMED)
        testa = await self.add("Testa", type="player_character", played_by=PLAYER, status=CONFIRMED)
        await self.memory.set_rule_link(
            GUILD_A,
            self.c,
            snot,
            "species",
            "goblin",
            source="dm",
            rules_source="srd52",
            known=True,
        )
        await self.memory.set_rule_link(GUILD_A, self.c, snot, "class", "Gnoll shaman", source="dm")
        return snot, testa

    async def test_roles_and_links_survive_a_backup(self) -> None:
        await self.fill()
        raw = encode_backup(await self.campaigns.export(GUILD_A, self.c))
        restored = await self.campaigns.import_backup(GUILD_B, decode_backup(raw), DM)
        original, copied = await self.snapshot(), await self.snapshot(GUILD_B, restored.id)
        for table in ("memory_mentions",):
            original.pop(table), copied.pop(table)
        self.assertEqual(copied, original)
        self.assertEqual(len(copied["memory_rule_links"]), 2)
        roles = {r["name"]: (r["type"], r["role"]) for r in copied["memory_entities"]}
        self.assertEqual(
            roles, {"Snot": ("character", "npc"), "Testa": ("character", "player_character")}
        )

    async def test_a_backup_from_before_reads_the_older_kinds(self) -> None:
        names = {
            "Snot": "npc", "Testa": "player_character", "Auril": "deity",
            "Rex": "creature", "Fireball": "spell", "Tower": "place",
        }  # fmt: skip
        await self.fill()
        data = await self.campaigns.export(GUILD_A, self.c)
        rows = data["sections"]["memory"]
        old: list[dict[str, Any]] = []
        for row in rows:
            if row["table"] == "rule_link":
                continue  # a file from before has no links
            if row["table"] == "entity":
                kind = names[row["name"]]
                row = {k: v for k, v in row.items() if k not in ("role", "needs_look")}
                row["type"] = kind
            old.append(row)
        for i, (name, kind) in enumerate(n for n in names.items() if n[0] not in ("Snot", "Testa")):
            old.append(
                {
                    "table": "entity", "id": f"{i + 10:032x}", "type": kind, "name": name,
                    "description": "", "status": "confirmed", "merged_into": None,
                    "source": "dm", "created_at": 1, "played_by": None,
                }
            )  # fmt: skip
        old_data = copy.deepcopy(data)
        old_data["sections"]["memory"] = old
        restored = await self.campaigns.import_backup(GUILD_B, old_data, DM)
        entities = await self.memory.entities(GUILD_B, restored.id)
        self.assertEqual(
            {e.name: (e.type, e.role, e.needs_look, e.played_by) for e in entities},
            {
                "Snot": ("character", "npc", False, None),
                "Testa": ("character", "player_character", False, PLAYER),
                "Auril": ("character", "god", False, None),
                "Rex": ("character", None, True, None),
                "Fireball": ("concept", None, True, None),
                "Tower": ("place", None, False, None),
            },
        )

    async def test_a_damaged_link_refuses_the_backup(self) -> None:
        await self.fill()
        data = await self.campaigns.export(GUILD_A, self.c)
        data["sections"]["memory"].append(
            {"table": "rule_link", "id": "9" * 32, "entity_id": "8" * 32, "kind": "species",
             "rules_source": "", "name": "x", "edition": None, "known": False, "created_at": 1}
        )  # fmt: skip
        from dmbot.campaigns import CampaignError

        with self.assertRaises(CampaignError):
            await self.campaigns.import_backup(GUILD_B, data, DM)

    async def test_the_json_in_a_backup_has_the_new_columns(self) -> None:
        await self.fill()
        data = await self.campaigns.export(GUILD_A, self.c)
        entity = next(r for r in data["sections"]["memory"] if r["table"] == "entity")
        self.assertIn("role", entity)
        self.assertIn("needs_look", entity)
        json.dumps(data)  # still plain data


class Migration0044(MemoryTest):
    """Migrations run with no server set: 0044's backfill must open every table it reads or
    writes (memory_entities, memory_types and memory_predicates), or it changes nothing."""

    async def test_the_older_kinds_move_on_tables_that_hold_rows(self) -> None:
        before = [m for m in MIGRATIONS if m[0] < "0044"]
        await self.db.close()
        await drop_schema(TEST_URL, self.schema)
        self.db = await Database.open(TEST_URL, schema=self.schema, migrations=before)
        campaigns = CampaignStore(self.db, clock=lambda: 1)
        c = (await campaigns.create(GUILD_A, "Frozen Wastes", DM)).id
        other = (await campaigns.create(GUILD_B, "Elsewhere", DM)).id
        ids = {
            name: f"{n:032x}"
            for n, name in enumerate(
                ("snot", "testa", "auril", "rex", "fireball", "tower", "elsewhere"), start=1
            )
        }
        rows = [
            ("snot", "npc", None, GUILD_A, c),
            ("testa", "player_character", PLAYER, GUILD_A, c),
            ("auril", "deity", None, GUILD_A, c),
            ("rex", "creature", None, GUILD_A, c),
            ("fireball", "spell", None, GUILD_A, c),
            ("tower", "place", None, GUILD_A, c),
            ("elsewhere", "npc", None, GUILD_B, other),
        ]
        for guild in (GUILD_A, GUILD_B):
            async with self.db.guild(guild) as conn:
                for name, kind, player, g, camp in rows:
                    if g != guild:
                        continue
                    await conn.execute(
                        "INSERT INTO memory_entities (guild_id, campaign_id, id, type, name,"
                        " description, status, source, created_at, played_by)"
                        " VALUES (%s, %s, %s, %s, %s, '', 'confirmed', 'dm', 1, %s)",
                        (g, camp, ids[name], kind, name.title(), player),
                    )
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "INSERT INTO memory_types (guild_id, campaign_id, key, parent, label, description,"
                " examples, reason, status, created_at)"
                " VALUES (%s, %s, 'villain', 'npc', 'villain', 'd', ARRAY['x'], 'r', 'active', 1),"
                " (%s, %s, 'ship', 'item', 'ship', 'd', ARRAY['x'], 'r', 'active', 1)",
                (GUILD_A, c, GUILD_A, c),
            )
            await conn.execute(
                "INSERT INTO memory_predicates (guild_id, campaign_id, key, label, description,"
                " examples, reason, subject_types, object_types, is_symmetric, conflicts_with,"
                " status, created_at) VALUES (%s, %s, 'friends_with', 'friends with', 'd',"
                " ARRAY['x'], 'r', ARRAY['npc', 'player_character'], ARRAY['creature', 'faction'],"
                " false, ARRAY[]::text[], 'active', 1)",
                (GUILD_A, c),
            )
        await self.db.migrate()
        memory = MemoryStore(self.db, clock=lambda: 1)
        got = {e.name: e for e in await memory.entities(GUILD_A, c)}
        self.assertEqual(
            {n: (e.type, e.role, e.needs_look, e.played_by) for n, e in got.items()},
            {
                "Snot": ("character", "npc", False, None),
                "Testa": ("character", "player_character", False, PLAYER),
                "Auril": ("character", "god", False, None),
                "Rex": ("character", None, True, None),  # nothing deleted; the DM decides
                "Fireball": ("concept", None, True, None),
                "Tower": ("place", None, False, None),
            },
        )
        elsewhere = await memory.entities(GUILD_B, other)  # another server's rows moved too
        self.assertEqual([(e.type, e.role) for e in elsewhere], [("character", "npc")])
        onto = await memory.ontology(GUILD_A, c)
        self.assertTrue(onto.is_a("villain", "character"))  # its parent moved with the kind
        self.assertIsInstance(onto, Ontology)
        predicate = onto.predicates["friends_with"]
        self.assertEqual(
            (sorted(predicate.subject_types), sorted(predicate.object_types)),
            (["character"], ["character", "faction"]),
        )
        async with self.db.unscoped() as conn:
            cur = await conn.execute(
                "SELECT count(*) AS n FROM pg_policies WHERE policyname = 'migrate_backfill'"
                " AND schemaname = current_schema()"
            )
            self.assertEqual((await cur.fetchone() or {})["n"], 0)  # no opening left

    async def test_the_new_table_is_isolated_like_the_rest(self) -> None:
        async with self.db.unscoped() as conn:
            cur = await conn.execute(
                "SELECT relrowsecurity, relforcerowsecurity FROM pg_class"
                " WHERE relname = 'memory_rule_links'"
                " AND relnamespace = current_schema()::regnamespace"
            )
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual((row["relrowsecurity"], row["relforcerowsecurity"]), (True, True))
