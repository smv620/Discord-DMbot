"""The official relationships, traceable (#1189): every core relationship has a full definition,
the definitions are written to the database by version, facts record the version they were
written under, and a meaning change without a version bump fails here."""

from __future__ import annotations

import unittest
from typing import Any, ClassVar

from dmbot.db import Database, drop_schema
from dmbot.memory import definitions
from dmbot.memory.backup import _TAGS, _check_terms, _with_definition_fields
from dmbot.memory.models import MemoryRuleError
from dmbot.memory.ontology import (
    CORE_PREDICATES,
    SYNONYMS,
    Ontology,
    PredicateTerm,
    decode_not_this,
    encode_not_this,
)
from dmbot.schema import MIGRATIONS
from tests.pg import TEST_URL
from tests.test_memory_store import DM, GUILD_A, MemoryTest

# What each relationship meant when it was released at that version (hash of its meaning, see
# `definitions.fingerprint`). Change a meaning -> raise `version` on the term AND add the new
# line here; never edit an old line.
RELEASED = {
    ("located_in", 1): "660cd505901eb6db",
    ("member_of", 1): "cf1ae6d76b79fc21",
    ("ally_of", 1): "6897b28a808f39a8",
    ("enemy_of", 1): "fda137c9ea8ad81f",
    ("kin_of", 1): "e1f9d598e3cef251",
    ("owns", 1): "8b44723365895601",
    ("owns", 2): "c7a7b789cb942d9d",  # "carries" and "wields" moved to the new carries (#1267)
    ("carries", 1): "df898c860718ce82",
    ("knows", 1): "14954b5521977a9b",
    ("did", 1): "234fbe8bbfd0c486",  # deeds (#1732)
    ("affected", 1): "04e6e8435d6c2d2e",
    ("serves", 1): "66d4cd47531358b3",
    ("sworn_to", 1): "924d02543c8d4ae8",
    ("plays", 1): "8e243f3375034f3a",
    ("appears_in", 1): "e35920d115999396",
    ("of_people", 1): "bb3ebbef732e6ad4",  # the DM's word that a character is of a people (#1655)
    ("led_to", 1): "85af23ae5f495e35",  # one thing led to another (#1679)
    ("promised_by", 1): "614c63ec0cd9acef",  # who made a promise (#1674)
    ("promised_to", 1): "e1cb64b67f87e7be",  # who it was made to (#1674)
    ("in_thread", 1): "cbc940251441c3b4",  # who is in a story thread (#1662)
    ("advances", 1): "fa3b7b2b0aae2deb",  # an event moved a thread (#1662)
    ("resolves", 1): "aa8c8c7d656fce4e",  # an event ended a thread (#1662)
    ("associated_with", 1): "9b1afee4b882cde9",  # the catch-all for a refused term (#1303)
}


class Definitions(unittest.TestCase):
    def test_every_core_relationship_has_a_full_definition(self) -> None:
        for term in CORE_PREDICATES:
            self.assertEqual(definitions.missing(term), [], term.key)

    def test_not_this_points_at_real_relationships(self) -> None:
        keys = {p.key for p in CORE_PREDICATES}
        for term in CORE_PREDICATES:
            for near, key in term.not_this:
                self.assertIn(key, keys, f"{term.key}: {near}")
                self.assertNotEqual(key, term.key)

    def test_sworn_to_takes_a_character_a_group_and_an_idea_but_not_a_place(self) -> None:
        onto = Ontology.build()
        sworn = onto.active_predicate("sworn_to")
        self.assertEqual(sworn.label, "is sworn to")
        self.assertEqual(sworn.inverse_label, "has sworn")
        self.assertTrue(onto.is_any("character", sworn.subject_types))
        for kind in ("character", "faction", "concept"):
            self.assertTrue(onto.is_any(kind, sworn.object_types), kind)
        self.assertFalse(onto.is_any("place", sworn.object_types))
        self.assertEqual({k for _, k in sworn.not_this}, {"serves", "member_of"})

    def test_synonyms_resolve(self) -> None:
        onto = Ontology.build()
        for word, key in (
            ("pact_with", "sworn_to"),
            ("pledged_to", "sworn_to"),
            ("oath_to", "sworn_to"),
            ("bound_to", "sworn_to"),
            ("friends_with", "ally_of"),
            ("controls", "plays"),
        ):
            self.assertEqual(SYNONYMS[word], key)
            self.assertEqual(onto.existing_match(word, word), key)
        with self.assertRaises(MemoryRuleError):
            onto.check_new_term("pact_with", "pact with", "d", ["e"], "r")

    def test_a_synonym_belongs_to_one_relationship_only(self) -> None:
        words = [w for p in CORE_PREDICATES for w in p.synonyms]
        self.assertEqual(len(words), len(set(words)))

    def test_no_synonym_is_also_a_relationship(self) -> None:
        keys = {p.key for p in CORE_PREDICATES}
        self.assertFalse(keys & set(SYNONYMS))

    def test_a_meaning_changed_without_a_new_version_fails(self) -> None:
        for term in CORE_PREDICATES:
            released = RELEASED.get((term.key, term.version))
            self.assertIsNotNone(
                released, f"{term.key} v{term.version} is not in RELEASED: add it (new version?)"
            )
            self.assertEqual(
                definitions.fingerprint(term),
                released,
                f"{term.key}'s meaning changed but its version is still {term.version}",
            )

    def test_not_this_round_trips_through_the_database_form(self) -> None:
        for term in CORE_PREDICATES:
            self.assertEqual(decode_not_this(encode_not_this(term.not_this)), term.not_this)


class OldBackups(unittest.TestCase):
    def test_a_backup_from_before_has_version_1_everywhere(self) -> None:
        rows = [
            {"table": "relation", "predicate": "ally_of"},
            {"table": "predicate", "key": "mentor_of"},
        ]
        out = _with_definition_fields(rows)
        self.assertEqual(out[0]["definition_version"], 1)
        self.assertEqual(out[1]["version"], 1)
        self.assertEqual(out[1]["covers"], [])

    def test_a_version_this_release_does_not_know_reads_as_the_oldest(self) -> None:
        out = _with_definition_fields(
            [
                {"table": "relation", "predicate": "ally_of", "definition_version": 7},
                {"table": "relation", "predicate": "ally_of", "definition_version": 1},
                {"table": "relation", "predicate": ["junk"], "definition_version": 3},
            ]
        )
        self.assertEqual([r["definition_version"] for r in out], [1, 1, 1])


class ACampaignsOwnSwornTo(unittest.TestCase):
    ROW: ClassVar[dict[str, Any]] = {
        "table": "predicate", "key": "sworn_to", "parent": None, "label": "swears to",
        "description": "Ours.", "examples": ["x"], "reason": "r", "subject_types": ["character"],
        "object_types": ["place"], "is_symmetric": False, "max_per_subject": None,
        "conflicts_with": [], "status": "active", "replaced_by": None, "created_at": 1,
    }  # fmt: skip

    def test_an_old_backup_with_its_own_sworn_to_is_never_refused(self) -> None:
        (row,) = _with_definition_fields([dict(self.ROW)])
        self.assertEqual((row["status"], row["replaced_by"]), ("deprecated", "sworn_to"))
        by_tag: dict[str, list[dict[str, object]]] = {tag: [] for tag in _TAGS}
        by_tag["predicate"] = [row]
        _check_terms(by_tag)  # no CampaignError

    def test_a_current_backup_naming_a_core_key_is_still_damaged(self) -> None:
        from dmbot.campaigns import CampaignError

        row = {**self.ROW, "version": 1}  # a current file: no excuse for a core key
        by_tag: dict[str, list[dict[str, object]]] = {tag: [] for tag in _TAGS}
        by_tag["predicate"] = [row]
        with self.assertRaises(CampaignError):
            _check_terms(by_tag)


class Stored(MemoryTest):
    async def test_a_campaigns_own_sworn_to_is_replaced_by_the_core_and_its_facts_rechecked(
        self,
    ) -> None:
        a = await self.add("Cerric")
        place = await self.add("Waterdeep", type="place")
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "INSERT INTO memory_predicates (guild_id, campaign_id, key, parent, label,"
                " description, examples, reason, subject_types, object_types, is_symmetric,"
                " max_per_subject, conflicts_with, status, replaced_by, created_at)"
                " VALUES (%s, %s, 'sworn_to', NULL, 'swears to', 'Our own.', ARRAY['x'], 'r',"
                " ARRAY['character'], ARRAY['place'], false, NULL, ARRAY[]::text[], 'active',"
                " NULL, 1)",
                (GUILD_A, self.c),
            )
            await conn.execute(
                "INSERT INTO memory_relations (guild_id, campaign_id, id, subject_id, predicate,"
                " object_id, detail, confidence, status, source, mention_ids, secret, created_at)"
                " VALUES (%s, %s, %s, %s, 'sworn_to', %s, '', 1.0, 'confirmed', 'dm', '{}',"
                " false, 1)",
                (GUILD_A, self.c, "c" * 32, a, place),
            )
        looked = await self.memory.recheck_definitions(GUILD_A, self.c)
        self.assertEqual(looked.value, 1)
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute("SELECT kind FROM memory_flags")
            flags = [r["kind"] for r in await cur.fetchall()]
            cur = await conn.execute("SELECT status, replaced_by, version FROM memory_predicates")
            row = await cur.fetchone()
        self.assertEqual(flags, ["wrong_object"])  # the core's meaning: not a place
        self.assertEqual(
            dict(row or {}), {"status": "deprecated", "replaced_by": "sworn_to", "version": 2}
        )
        self.assertEqual((await self.memory.recheck_definitions(GUILD_A, self.c)).value, 0)

    async def test_undo_works_across_the_migration_for_relations_and_predicates(self) -> None:
        a, b = await self.add("Cerric"), await self.add("Belleros")
        added = await self.relate(a, "ally_of", b)
        assert added.batch is not None
        # The change log as it was written before 0059: no definition_version in its snapshots.
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "UPDATE memory_changes SET after = after - 'definition_version'"
                " WHERE table_name = 'memory_relations'"
            )
        await self.memory.undo(GUILD_A, self.c, added.batch)
        self.assertEqual(await self.memory.relations(GUILD_A, self.c), [])
        # And an update from before: undoing it puts the fact back as it was.
        again = await self.relate(a, "ally_of", b)
        changed = await self.memory.update_relation(
            GUILD_A, self.c, again.value[0].id, source="dm", status="confirmed"
        )
        assert changed.batch is not None
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "UPDATE memory_changes SET before = before - 'definition_version',"
                " after = after - 'definition_version' WHERE table_name = 'memory_relations'"
            )
        await self.memory.undo(GUILD_A, self.c, changed.batch)
        facts = await self.memory.relations(GUILD_A, self.c)
        self.assertEqual([(f.status, f.definition_version) for f in facts], [("proposed", 1)])

    async def test_a_start_fills_the_table_and_a_second_start_adds_nothing(self) -> None:
        self.assertEqual(await definitions.seed(self.db), len(CORE_PREDICATES))
        self.assertEqual(await definitions.seed(self.db), 0)
        rows = await definitions.stored(self.db)
        self.assertEqual({k for k, _ in rows}, {p.key for p in CORE_PREDICATES})  # one row for each

    async def test_the_stored_meaning_is_the_codes_for_the_same_version(self) -> None:
        await definitions.seed(self.db)
        rows = await definitions.stored(self.db)
        for term in CORE_PREDICATES:
            row = rows[(term.key, term.version)]
            for column, value in definitions.meaning(term).items():
                if column in row:
                    self.assertEqual(row[column], value, f"{term.key}.{column}")

    async def test_the_shared_table_holds_no_campaign_data(self) -> None:
        async with self.db.unscoped() as conn:
            cur = await conn.execute(
                "SELECT column_name FROM information_schema.columns"
                " WHERE table_schema = current_schema()"
                " AND table_name = 'ontology_predicate_versions'"
            )
            columns = {r["column_name"] for r in await cur.fetchall()}
        self.assertFalse({"guild_id", "campaign_id"} & columns)

    async def test_a_new_fact_stores_the_current_version(self) -> None:
        a, b = await self.add("Cerric"), await self.add("Belleros")
        written = await self.relate(a, "ally_of", b)
        self.assertEqual(written.value[0].definition_version, 1)
        facts = await self.memory.relations(GUILD_A, self.c)
        self.assertEqual({f.definition_version for f in facts}, {1})

    async def test_a_fact_from_before_the_column_reads_as_version_1(self) -> None:
        before = [m for m in MIGRATIONS if m[0] < "0059"]
        await self.db.close()
        await drop_schema(TEST_URL, self.schema)
        self.db = await Database.open(TEST_URL, schema=self.schema, migrations=before)
        from dmbot.campaigns import CampaignStore

        c = (await CampaignStore(self.db, clock=lambda: 1).create(GUILD_A, "Old", DM)).id
        async with self.db.guild(GUILD_A) as conn:
            for eid, name in (("a" * 32, "Cerric"), ("b" * 32, "Belleros")):
                await conn.execute(
                    "INSERT INTO memory_entities (guild_id, campaign_id, id, type, name,"
                    " description, status, source, created_at, role, needs_look)"
                    " VALUES (%s, %s, %s, 'character', %s, '', 'confirmed', 'dm', 1, 'npc', false)",
                    (GUILD_A, c, eid, name),
                )
            await conn.execute(
                "INSERT INTO memory_relations (guild_id, campaign_id, id, subject_id, predicate,"
                " object_id, detail, confidence, status, source, mention_ids, secret, created_at)"
                " VALUES (%s, %s, %s, %s, 'ally_of', %s, '', 1.0, 'confirmed', 'dm', '{}',"
                " false, 1)",
                (GUILD_A, c, "c" * 32, "a" * 32, "b" * 32),
            )
        await self.db.migrate()
        from dmbot.memory.store import MemoryStore

        facts = await MemoryStore(self.db, clock=lambda: 1).relations(GUILD_A, c)
        self.assertEqual([f.definition_version for f in facts], [1])

    async def test_a_fact_on_an_older_meaning_is_looked_at_once_and_flagged(self) -> None:
        a, b = await self.add("Cerric"), await self.add("Belleros")
        await self.memory.add_predicate(
            GUILD_A,
            self.c,
            PredicateTerm(
                "mentor_of", "mentors", "Teaches and guides.", ("character",), ("character",)
            ),
            examples=["Belleros mentors Cerric."],
            reason="the table uses it",
            source="dm",
        )
        await self.relate(b, "mentor_of", a)
        self.assertEqual((await self.memory.recheck_definitions(GUILD_A, self.c)).value, 0)
        # The meaning moved on: version 2 now wants a place on the other end.
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "UPDATE memory_predicates SET version = 2, object_types = ARRAY['place']"
                " WHERE key = 'mentor_of'"
            )
        self.assertEqual((await self.memory.recheck_definitions(GUILD_A, self.c)).value, 1)
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute("SELECT kind FROM memory_flags")
            flags = [r["kind"] for r in await cur.fetchall()]
        self.assertEqual(flags, ["wrong_object"])  # a flag for the DM, nothing fixed
        facts = await self.memory.relations(GUILD_A, self.c)
        self.assertEqual([(f.predicate, f.definition_version) for f in facts], [("mentor_of", 2)])
        self.assertEqual((await self.memory.recheck_definitions(GUILD_A, self.c)).value, 0)
