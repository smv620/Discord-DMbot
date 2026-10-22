"""Who believes a connection, and how strongly (#2409; docs/STORY_MEMORY.md, section 9): the
migration's backfill, the one write path, the views, Undo, isolation and backups. Invented names."""

from __future__ import annotations

import unittest
import uuid
from typing import Any

from dmbot.campaigns import CampaignStore
from dmbot.campaigns.store import decode_backup, encode_backup
from dmbot.db import Database, drop_schema
from dmbot.memory.models import (
    CONFIRMED,
    PROPOSED,
    REJECTED,
    MemoryRuleError,
    new_id,
)
from dmbot.schema import MIGRATIONS
from tests.pg import REQUIRE_DB, TEST_URL
from tests.test_memory_store import DM, GUILD_A, GUILD_B, MemoryTest

BEFORE = "0179_beliefs"


class TheMigration(unittest.IsolatedAsyncioTestCase):
    """0179 on tables that already hold rows: nothing is silently skipped by row-level security."""

    async def asyncSetUp(self) -> None:
        if not TEST_URL:
            if REQUIRE_DB:
                self.fail("DMBOT_TEST_DATABASE_URL is needed")
            self.skipTest("set DMBOT_TEST_DATABASE_URL to test the migration")
        self.schema = f"t_{uuid.uuid4().hex[:16]}"
        self.before = tuple(m for m in MIGRATIONS if m[0] != BEFORE)
        self.db = await Database.open(
            TEST_URL, schema=self.schema, max_size=2, migrations=self.before
        )

    async def asyncTearDown(self) -> None:
        await self.db.close()
        await drop_schema(TEST_URL, self.schema)

    async def seed(self, guild: int, name: str) -> tuple[str, dict[str, str]]:
        campaign = await CampaignStore(self.db).create(guild, name, DM)
        ids = {k: new_id() for k in ("a", "b", "public", "secret", "proposed", "rejected")}
        async with self.db.guild(guild) as conn:
            for key in ("a", "b"):
                await conn.execute(
                    "INSERT INTO memory_entities (guild_id, campaign_id, id, type, name,"
                    " description, status, source, created_at)"
                    " VALUES (%s, %s, %s, 'npc', %s, '', 'confirmed', 'dm', 1)",
                    (guild, campaign.id, ids[key], key.upper() * 3),
                )
            for key, status, secret in (
                ("public", "confirmed", False),
                ("secret", "confirmed", True),
                ("proposed", "proposed", False),
                ("rejected", "rejected", False),
            ):
                await conn.execute(
                    "INSERT INTO memory_relations (guild_id, campaign_id, id, subject_id,"
                    " predicate, object_id, detail, confidence, status, source, mention_ids,"
                    " secret, created_at, definition_version)"
                    " VALUES (%s, %s, %s, %s, 'knows', %s, %s, 1.0, %s, 'dm', '{}', %s, 1, 1)",
                    (guild, campaign.id, ids[key], ids["a"], ids["b"], key, status, secret),
                )
        return campaign.id, ids

    async def beliefs(self, guild: int, campaign: str) -> dict[tuple[str, str], tuple[float, str]]:
        """(perspective kind, connection id) -> (confidence, status)."""
        async with self.db.guild(guild) as conn:
            cur = await conn.execute(
                "SELECT p.kind, b.relation_id, b.confidence, b.status FROM memory_beliefs b"
                " JOIN memory_perspectives p ON p.guild_id = b.guild_id"
                " AND p.campaign_id = b.campaign_id AND p.id = b.perspective_id"
                " WHERE b.campaign_id = %s",
                (campaign,),
            )
            return {
                (r["kind"], r["relation_id"]): (r["confidence"], r["status"])
                for r in await cur.fetchall()
            }

    async def test_a_secret_connection_has_no_party_belief_and_a_known_one_has_both(self) -> None:
        campaign, ids = await self.seed(GUILD_A, "Frostmaiden")
        other, other_ids = await self.seed(GUILD_B, "Elsewhere")
        await self.db.migrate(MIGRATIONS)  # the beliefs arrive while the rows are there
        got = await self.beliefs(GUILD_A, campaign)
        self.assertEqual(
            got,
            {
                ("truth", ids["public"]): (1.0, "revealed_fact"),
                ("party", ids["public"]): (1.0, "revealed_fact"),
                ("truth", ids["secret"]): (1.0, "secret_plot"),
            },  # no party belief for the secret one; nothing for a proposed or rejected one
        )
        # each campaign has exactly one truth and one party, and nothing crossed over
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute("SELECT campaign_id, kind FROM memory_perspectives")
            seen = sorted((r["campaign_id"], r["kind"]) for r in await cur.fetchall())
        self.assertEqual(seen, sorted([(campaign, "truth"), (campaign, "party")]))
        elsewhere = await self.beliefs(GUILD_B, other)
        self.assertEqual(
            set(elsewhere), {("truth", other_ids["public"]), ("party", other_ids["public"]),
                             ("truth", other_ids["secret"])},
        )  # fmt: skip
        self.assertTrue(all(rel in other_ids.values() for _kind, rel in elsewhere))

    async def test_a_campaign_with_no_connections_still_gets_both_perspectives(self) -> None:
        campaign = await CampaignStore(self.db).create(GUILD_A, "Empty", DM)
        await self.db.migrate(MIGRATIONS)
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute(
                "SELECT kind FROM memory_perspectives WHERE campaign_id = %s ORDER BY kind",
                (campaign.id,),
            )
            self.assertEqual([r["kind"] for r in await cur.fetchall()], ["party", "truth"])


class Beliefs(MemoryTest):
    async def pair(self) -> tuple[str, str]:
        return await self.add("Mira"), await self.add("Rook")

    async def link(self, a: str, b: str, *, secret: bool = False, pred: str = "knows") -> str:
        written = await self.relate(a, pred, b, status=CONFIRMED, secret=secret)
        return str(written.value[0].id)

    async def party(self) -> dict[str, float]:
        return {b.relation_id: b.confidence for b in await self.memory.party_view(GUILD_A, self.c)}

    async def truth(self) -> dict[str, tuple[float, str]]:
        return {
            b.relation_id: (b.confidence, b.status)
            for b in await self.memory.truth_view(GUILD_A, self.c)
        }

    async def kinds(self) -> dict[str, str]:
        return {p.kind: p.id for p in await self.memory.perspectives(GUILD_A, self.c)}

    # ---- the beliefs follow the connection ----

    async def test_a_new_campaign_gets_its_perspectives_with_its_first_connection(self) -> None:
        a, b = await self.pair()
        self.assertEqual(await self.memory.perspectives(GUILD_A, self.c), [])
        known = await self.link(a, b)
        self.assertEqual(sorted(await self.kinds()), ["party", "truth"])
        self.assertEqual(await self.party(), {known: 1.0})
        self.assertEqual(await self.truth(), {known: (1.0, "revealed_fact")})

    async def test_a_secret_connection_is_believed_by_the_truth_alone(self) -> None:
        a, b = await self.pair()
        c = await self.add("Hollis")
        secret = await self.link(a, b, secret=True)
        known = await self.link(a, c)
        self.assertEqual(await self.party(), {known: 1.0})
        self.assertEqual(
            await self.truth(), {secret: (1.0, "secret_plot"), known: (1.0, "revealed_fact")}
        )

    async def test_a_connection_still_proposed_or_rejected_has_no_beliefs(self) -> None:
        a, b = await self.pair()
        made = await self.relate(a, "knows", b, status=PROPOSED)
        self.assertEqual(await self.truth(), {})
        await self.memory.update_relation(
            GUILD_A, self.c, made.value[0].id, source="dm", status=REJECTED
        )
        self.assertEqual(await self.truth(), {})

    async def test_confirming_and_flipping_secret_move_the_party_belief_and_keep_history(
        self,
    ) -> None:
        a, b = await self.pair()
        made = await self.relate(a, "knows", b, status=PROPOSED)
        rel = made.value[0].id
        await self.memory.update_relation(GUILD_A, self.c, rel, source="dm", status=CONFIRMED)
        self.assertEqual(await self.party(), {rel: 1.0})
        await self.memory.update_relation(GUILD_A, self.c, rel, source="dm", secret=True)
        self.assertEqual(await self.party(), {})  # hidden again: the latest party belief is 0.0
        self.assertEqual((await self.truth())[rel], (1.0, "secret_plot"))
        await self.memory.update_relation(GUILD_A, self.c, rel, source="dm", secret=False)
        self.assertEqual(await self.party(), {rel: 1.0})
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute(
                "SELECT count(*) AS n FROM memory_beliefs WHERE relation_id = %s", (rel,)
            )
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual(row["n"], 3 + 3)  # truth and party each: first, hidden, shown. Kept

    async def test_undoing_a_flip_takes_the_belief_with_it_in_the_same_change(self) -> None:
        a, b = await self.pair()
        rel = await self.link(a, b)
        flipped = await self.memory.update_relation(GUILD_A, self.c, rel, source="dm", secret=True)
        self.assertEqual(await self.party(), {})
        await self.memory.undo(GUILD_A, self.c, flipped.batch, source="dm")  # type: ignore[arg-type]
        self.assertEqual(await self.party(), {rel: 1.0})
        self.assertEqual(await self.truth(), {rel: (1.0, "revealed_fact")})

    async def test_deleting_a_connection_removes_its_beliefs_and_undo_brings_them_back(
        self,
    ) -> None:
        a, b = await self.pair()
        keep = await self.add("Keep", type="place")
        rel = await self.link(a, b)
        twin = await self.link(keep, b)
        merged = await self.memory.merge(GUILD_A, self.c, keep, a, source="dm", dm_said_same=True)
        left = await self.truth()
        self.assertEqual(len(left), 1)  # the merge kept one of the twin connections
        await self.memory.undo(GUILD_A, self.c, merged.batch, source="dm")  # type: ignore[arg-type]
        self.assertEqual(set(await self.truth()), {rel, twin})
        self.assertEqual(set(await self.party()), {rel, twin})

    # ---- the one write path ----

    async def test_believe_records_a_theory_and_the_party_view_hides_it_until_known(self) -> None:
        a, b = await self.pair()
        rel = await self.link(a, b, secret=True)
        party = (await self.kinds())["party"]
        theory = await self.memory.believe(
            GUILD_A, self.c, party, rel, 0.5, "theory", source="dm",
            evidence=["line:s1:1000:7"], session_at=1_000,
        )  # fmt: skip
        self.assertEqual((theory.value.confidence, theory.value.status), (0.5, "theory"))
        self.assertEqual(theory.value.evidence_ids, ("line:s1:1000:7",))
        self.assertEqual(await self.party(), {})  # a guess is never a reveal
        shown = await self.memory.party_view(GUILD_A, self.c, known_only=False)
        self.assertEqual([(x.relation_id, x.confidence) for x in shown], [(rel, 0.5)])
        await self.memory.undo(GUILD_A, self.c, theory.batch, source="dm")  # type: ignore[arg-type]
        self.assertEqual(await self.memory.party_view(GUILD_A, self.c, known_only=False), [])

    async def test_the_newest_belief_wins_and_undo_brings_the_one_before_back(self) -> None:
        a, b = await self.pair()
        rel = await self.link(a, b, secret=True)
        party = (await self.kinds())["party"]
        await self.memory.believe(GUILD_A, self.c, party, rel, 0.5, "theory", source="dm")
        proof = await self.memory.believe(
            GUILD_A, self.c, party, rel, 1.0, "revealed_fact", source="dm"
        )
        self.assertEqual(await self.party(), {rel: 1.0})
        await self.memory.undo(GUILD_A, self.c, proof.batch, source="dm")  # type: ignore[arg-type]
        (still,) = await self.memory.party_view(GUILD_A, self.c, known_only=False)
        self.assertEqual((still.confidence, still.status), (0.5, "theory"))

    async def test_only_the_dm_writes_the_truth_and_the_shapes_are_checked(self) -> None:
        a, b = await self.pair()
        rel = await self.link(a, b)
        ids = await self.kinds()
        for source in ("cleaner", "entitybot", "extractor"):
            with self.subTest(source=source), self.assertRaises(MemoryRuleError):
                await self.memory.believe(
                    GUILD_A, self.c, ids["truth"], rel, 1.0, "revealed_fact", source=source
                )
        # the party may be written by a helper (a theory it heard), never the truth
        await self.memory.believe(
            GUILD_A, self.c, ids["party"], rel, 0.5, "theory", source="extractor"
        )
        for bad in (
            {"confidence": 1.5, "status": "theory"},
            {"confidence": -0.1, "status": "theory"},
            {"confidence": 0.5, "status": "hunch"},
            {"confidence": 0.5, "status": "theory", "evidence": ["x" * 101]},
            {"confidence": 0.5, "status": "theory", "evidence": ["e"] * 21},
        ):
            with self.subTest(bad=bad), self.assertRaises(MemoryRuleError):
                await self.memory.believe(
                    GUILD_A,
                    self.c,
                    ids["party"],
                    rel,
                    bad["confidence"],
                    bad["status"],  # type: ignore[arg-type]
                    source="dm",
                    evidence=bad.get("evidence", ()),  # type: ignore[arg-type]
                )

    async def test_nothing_crosses_a_campaign_or_a_server(self) -> None:
        a, b = await self.pair()
        rel = await self.link(a, b)
        ids = await self.kinds()
        other = (await self.campaigns.create(GUILD_A, "Other", DM)).id
        x, y = await self.add("Xan", campaign=other), await self.add("Yel", campaign=other)
        theirs = (
            (
                await self.memory.add_relation(
                    GUILD_A, other, x, "knows", y, source="dm", confidence=1.0, status=CONFIRMED
                )
            )
            .value[0]
            .id
        )
        with self.assertRaises(
            MemoryRuleError
        ):  # this campaign's perspective, the other's connection
            await self.memory.believe(
                GUILD_A, other, ids["party"], theirs, 1.0, "revealed_fact", source="dm"
            )
        with self.assertRaises(MemoryRuleError):  # the other's connection, from this campaign
            await self.memory.believe(
                GUILD_A, self.c, ids["party"], theirs, 1.0, "revealed_fact", source="dm"
            )
        self.assertEqual(set(await self.party()), {rel})
        async with self.db.guild(GUILD_B) as conn:  # another server sees none of it
            for table in ("memory_beliefs", "memory_perspectives"):
                cur = await conn.execute(f"SELECT count(*) AS n FROM {table}")
                row = await cur.fetchone()
                assert row is not None
                self.assertEqual(row["n"], 0, table)

    async def test_a_character_perspective_needs_its_entry_and_a_standard_one_must_not(
        self,
    ) -> None:
        a, _ = await self.pair()
        await self.link(a, await self.add("Z"))
        async with self.db.guild(GUILD_A) as conn:
            for kind, entity in (("pc", None), ("truth", a), ("party", a), ("npc", None)):
                with self.subTest(kind=kind, entity=entity), self.assertRaises(Exception):  # noqa: B017
                    async with conn.transaction():
                        await conn.execute(
                            "INSERT INTO memory_perspectives (guild_id, campaign_id, id, kind,"
                            " entity_id, created_at) VALUES (%s, %s, %s, %s, %s, 1)",
                            (GUILD_A, self.c, new_id(), kind, entity),
                        )

    async def test_an_entry_cannot_have_manipulated_itself_or_something_elsewhere(self) -> None:
        a, b = await self.pair()
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "UPDATE memory_entities SET manipulated_by = %s WHERE id = %s", (b, a)
            )  # the ledger Fabio planted: allowed
            with self.assertRaises(Exception):  # noqa: B017
                async with conn.transaction():
                    await conn.execute(
                        "UPDATE memory_entities SET manipulated_by = %s WHERE id = %s", (a, a)
                    )
            with self.assertRaises(Exception):  # noqa: B017
                async with conn.transaction():
                    await conn.execute(
                        "UPDATE memory_entities SET manipulated_by = %s WHERE id = %s",
                        (new_id(), a),
                    )
                    await conn.execute("SET CONSTRAINTS ALL IMMEDIATE")  # (the link is deferred)

    # ---- today's readers see what they see today ----

    async def test_the_party_view_is_exactly_the_connections_players_can_see_today(self) -> None:
        names = [await self.add(f"N{n}") for n in range(6)]
        shown, hidden = [], []
        for n in range(1, 6):
            secret = n % 2 == 0
            rel = await self.link(names[0], names[n], secret=secret)
            (hidden if secret else shown).append(rel)
        await self.relate(names[1], "knows", names[2], status=PROPOSED)  # not confirmed: neither
        flagged = await self.memory.relations(GUILD_A, self.c, confirmed_only=True)
        by_flag = {r.id for r in flagged if not r.secret}
        self.assertEqual(set(await self.party()), by_flag)
        self.assertEqual(by_flag, set(shown))
        self.assertEqual(set(await self.truth()), set(shown) | set(hidden))
        # and after the DM hides one and shows another
        await self.memory.update_relation(GUILD_A, self.c, shown[0], source="dm", secret=True)
        await self.memory.update_relation(GUILD_A, self.c, hidden[0], source="dm", secret=False)
        now = {
            r.id
            for r in await self.memory.relations(GUILD_A, self.c, confirmed_only=True)
            if not r.secret
        }
        self.assertEqual(set(await self.party()), now)

    # ---- backups ----

    async def test_a_backup_round_trip_keeps_perspectives_and_beliefs(self) -> None:
        a, b = await self.pair()
        rel = await self.link(a, b, secret=True)
        party = (await self.kinds())["party"]
        await self.memory.believe(
            GUILD_A, self.c, party, rel, 0.5, "theory", source="dm", evidence=["claim:abc"]
        )
        raw = encode_backup(await self.campaigns.export(GUILD_A, self.c))
        restored = await self.campaigns.import_backup(GUILD_B, decode_backup(raw), DM)
        original, copy = await self.snapshot(), await self.snapshot(GUILD_B, restored.id)
        for table in ("memory_mentions",):
            original.pop(table), copy.pop(table)
        self.assertEqual(copy, original)
        self.assertEqual(len(copy["memory_perspectives"]), 2)
        self.assertTrue(copy["memory_beliefs"])

    async def test_an_older_backup_without_beliefs_gets_them_worked_out(self) -> None:
        a, b = await self.pair()
        c = await self.add("Hollis")
        known, secret = await self.link(a, b), await self.link(a, c, secret=True)
        backup = await self.campaigns.export(GUILD_A, self.c)
        rows = self.memory_rows(backup)
        rows[:] = [r for r in rows if r["table"] not in ("perspective", "belief")]
        for r in rows:
            if r["table"] == "entity":
                r.pop("manipulated_by", None)  # a file from before the column
        raw = encode_backup(backup)
        restored = await self.campaigns.import_backup(GUILD_B, decode_backup(raw), DM)
        async with self.db.guild(GUILD_B) as conn:
            cur = await conn.execute(
                "SELECT p.kind, b.relation_id FROM memory_beliefs b JOIN memory_perspectives p"
                " ON p.guild_id = b.guild_id AND p.campaign_id = b.campaign_id"
                " AND p.id = b.perspective_id WHERE b.campaign_id = %s",
                (restored.id,),
            )
            got = {(r["kind"], r["relation_id"]) for r in await cur.fetchall()}
        self.assertEqual(got, {("truth", known), ("party", known), ("truth", secret)})

    def memory_rows(self, backup: dict[str, Any]) -> list[dict[str, Any]]:
        """The memory section's row list inside an exported backup, wherever it sits."""
        found: list[list[dict[str, Any]]] = []

        def walk(node: Any) -> None:
            if (
                isinstance(node, list)
                and node
                and all(isinstance(x, dict) and "table" in x for x in node)
            ):
                found.append(node)
            elif isinstance(node, dict):
                for v in node.values():
                    walk(v)
            elif isinstance(node, list):
                for v in node:
                    walk(v)

        walk(backup)
        self.assertEqual(len(found), 1)
        return found[0]

    async def test_a_damaged_belief_in_a_backup_is_refused(self) -> None:
        from dmbot.campaigns.models import CampaignError

        a, b = await self.pair()
        await self.link(a, b)
        for change in (
            {"confidence": 2.0},
            {"status": "hunch"},
            {"evidence_ids": ["e"] * 21},
            {"relation_id": "nope"},
        ):
            backup = await self.campaigns.export(GUILD_A, self.c)
            row = next(r for r in self.memory_rows(backup) if r["table"] == "belief")
            row.update(change)
            with self.subTest(change=change), self.assertRaises(CampaignError):
                await self.campaigns.import_backup(
                    GUILD_B, decode_backup(encode_backup(backup)), DM
                )


if __name__ == "__main__":
    unittest.main()
