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

    async def test_a_new_campaign_has_its_perspectives_from_the_start(self) -> None:
        a, b = await self.pair()
        before = await self.kinds()
        self.assertEqual(sorted(before), ["party", "truth"])  # before any connection
        known = await self.link(a, b)
        self.assertEqual(await self.kinds(), before)  # and the same two afterwards
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

    async def test_believe_records_a_belief_with_its_evidence_and_undo_takes_it_back(self) -> None:
        a, b = await self.pair()
        rel = await self.link(a, b, secret=True)
        truth = (await self.kinds())["truth"]
        doubt = await self.memory.believe(
            GUILD_A, self.c, truth, rel, 0.5, "theory", source="dm",
            evidence=["line:s1:1000:7"], session_at=1_000,
        )  # fmt: skip
        self.assertEqual((doubt.value.confidence, doubt.value.status), (0.5, "theory"))
        self.assertEqual(doubt.value.evidence_ids, ("line:s1:1000:7",))
        self.assertEqual(await self.party(), {})  # nothing here reveals anything to the players
        self.assertEqual((await self.truth())[rel], (0.5, "theory"))
        await self.memory.undo(GUILD_A, self.c, doubt.batch, source="dm")  # type: ignore[arg-type]
        self.assertEqual((await self.truth())[rel], (1.0, "secret_plot"))

    async def test_the_newest_belief_wins_and_undo_brings_the_one_before_back(self) -> None:
        a, b = await self.pair()
        rel = await self.link(a, b, secret=True)
        truth = (await self.kinds())["truth"]
        await self.memory.believe(GUILD_A, self.c, truth, rel, 0.5, "theory", source="dm")
        proof = await self.memory.believe(
            GUILD_A, self.c, truth, rel, 0.9, "evasion_under_magic", source="dm"
        )
        self.assertEqual((await self.truth())[rel], (0.9, "evasion_under_magic"))
        await self.memory.undo(GUILD_A, self.c, proof.batch, source="dm")  # type: ignore[arg-type]
        self.assertEqual((await self.truth())[rel], (0.5, "theory"))

    async def test_a_new_belief_wins_over_rows_numbered_by_another_campaigns_history(self) -> None:
        a, b = await self.pair()
        rel = await self.link(a, b)
        party = (await self.kinds())["party"]
        async with self.db.guild(GUILD_A) as conn:  # as a restored backup brings them
            await conn.execute(
                "INSERT INTO memory_beliefs (guild_id, campaign_id, id, perspective_id,"
                " relation_id, confidence, status, evidence_ids, seq, recorded_at)"
                " VALUES (%s, %s, %s, %s, %s, 0.5, 'theory', '{}', 1000000, 1)",
                (GUILD_A, self.c, new_id(), party, rel),
            )
        await self.memory.believe(GUILD_A, self.c, party, rel, 1.0, "revealed_fact", source="dm")
        self.assertEqual(await self.party(), {rel: 1.0})

    # ---- a reworded connection carries what the old one held (#2408) ----

    async def test_new_words_carry_the_beliefs_evidence_and_session_span_of_the_old(self) -> None:
        a, b = await self.pair()
        rel = await self.link(a, b)
        truth = (await self.kinds())["truth"]
        await self.memory.believe(
            GUILD_A, self.c, truth, rel, 0.6, "theory", source="dm",
            evidence=["line:s1:1000:7"], session_at=1_000,
        )  # fmt: skip
        await self.memory.update_relation(GUILD_A, self.c, rel, source="dm", to_session_at=None)
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "UPDATE memory_relations SET from_session_at = 1000 WHERE id = %s", (rel,)
            )
        moved = await self.memory.change_relation_words(
            GUILD_A, self.c, rel, "ally_of", "since the war", source="dm"
        )
        new = moved.value.id
        self.assertNotEqual(new, rel)
        self.assertEqual(moved.value.from_session_at, 1_000)
        self.assertEqual((await self.truth())[new], (0.6, "theory"))  # the DM's word, not 1.0
        self.assertEqual(await self.party(), {new: 1.0})  # the players still know it, once
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute(
                "SELECT evidence_ids, session_at FROM memory_beliefs WHERE relation_id = %s"
                " AND perspective_id = %s ORDER BY seq DESC LIMIT 1",
                (new, truth),
            )
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual((row["evidence_ids"], row["session_at"]), (["line:s1:1000:7"], 1_000))

    async def test_undo_of_the_new_words_takes_the_carried_beliefs_with_them(self) -> None:
        a, b = await self.pair()
        rel = await self.link(a, b)
        truth = (await self.kinds())["truth"]
        await self.memory.believe(GUILD_A, self.c, truth, rel, 0.6, "theory", source="dm")
        moved = await self.memory.change_relation_words(
            GUILD_A, self.c, rel, "ally_of", "", source="dm"
        )
        assert moved.batch is not None
        await self.memory.undo(GUILD_A, self.c, moved.batch, source="dm")  # type: ignore[arg-type]
        self.assertEqual((await self.truth())[rel], (0.6, "theory"))
        self.assertEqual(set(await self.truth()), {rel})
        self.assertEqual(await self.party(), {rel: 1.0})

    async def test_undo_of_new_words_puts_back_mentions_and_the_session_span_exactly(self) -> None:
        a, b = await self.pair()
        rel = await self.link(a, b)
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "UPDATE memory_relations SET from_session_at = 1000, mention_ids = %s"
                " WHERE id = %s",
                (["m" * 32], rel),
            )
        moved = await self.memory.change_relation_words(
            GUILD_A, self.c, rel, "ally_of", "", source="dm"
        )
        assert moved.batch is not None
        self.assertEqual(moved.value.from_session_at, 1_000)
        await self.memory.undo(GUILD_A, self.c, moved.batch, source="dm")  # type: ignore[arg-type]
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute(
                "SELECT id, status, from_session_at FROM memory_relations WHERE campaign_id = %s",
                (self.c,),
            )
            rows = {r["id"]: (r["status"], r["from_session_at"]) for r in await cur.fetchall()}
        self.assertEqual(rows, {rel: ("confirmed", 1_000)})
        self.assertEqual(set(await self.truth()), {rel})

    async def test_a_secret_connection_reworded_stays_secret_to_the_players(self) -> None:
        a, b = await self.pair()
        rel = await self.link(a, b, secret=True)
        moved = await self.memory.change_relation_words(
            GUILD_A, self.c, rel, "ally_of", "", source="dm"
        )
        self.assertTrue(moved.value.secret)
        self.assertEqual(await self.party(), {})
        self.assertEqual((await self.truth())[moved.value.id][1], "secret_plot")

    async def test_words_that_already_exist_reuse_that_connection_without_copying(self) -> None:
        a, b = await self.pair()
        rel = await self.link(a, b)
        other = await self.link(a, b, pred="ally_of")
        truth = (await self.kinds())["truth"]
        await self.memory.believe(GUILD_A, self.c, truth, rel, 0.6, "theory", source="dm")
        moved = await self.memory.change_relation_words(
            GUILD_A, self.c, rel, "ally_of", "", source="dm"
        )
        self.assertEqual(moved.value.id, other)
        self.assertEqual((await self.truth())[other], (1.0, "revealed_fact"))  # its own, untouched

    async def test_a_connection_that_is_no_longer_confirmed_drops_out_of_the_views(self) -> None:
        a, b = await self.pair()
        rel = await self.link(a, b)
        self.assertEqual(set(await self.party()), {rel})
        await self.memory.update_relation(GUILD_A, self.c, rel, source="dm", status=REJECTED)
        self.assertEqual(await self.party(), {})
        self.assertEqual(await self.truth(), {})
        self.assertEqual(await self.memory.party_view(GUILD_A, self.c, known_only=False), [])

    async def test_only_the_dm_writes_the_truth_and_the_shapes_are_checked(self) -> None:
        a, b = await self.pair()
        rel = await self.link(a, b)
        ids = await self.kinds()
        for source in ("cleaner", "entitybot", "extractor"):
            with self.subTest(source=source), self.assertRaises(MemoryRuleError):
                await self.memory.believe(
                    GUILD_A, self.c, ids["truth"], rel, 1.0, "revealed_fact", source=source
                )
        # and nobody writes a guess for the players yet: what they hold is the shown connections
        with self.assertRaises(MemoryRuleError):
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
                    ids["truth"],
                    rel,
                    bad["confidence"],  # type: ignore[arg-type]
                    bad["status"],  # type: ignore[arg-type]
                    source="dm",
                    evidence=bad.get("evidence", ()),  # type: ignore[arg-type]
                )

    async def test_undoing_the_first_confirmed_connection_leaves_the_later_ones_alone(self) -> None:
        a, b = await self.pair()
        c = await self.add("Hollis")
        first = await self.relate(a, "knows", b, status=CONFIRMED)  # makes the perspectives
        second = await self.link(a, c)
        await self.memory.undo(GUILD_A, self.c, first.batch, source="dm")  # type: ignore[arg-type]
        self.assertEqual(set(await self.party()), {second})
        self.assertEqual(sorted(await self.kinds()), ["party", "truth"])  # they stay

    async def test_a_change_log_snapshot_from_before_the_column_reads_as_not_faked(self) -> None:
        from dmbot.memory._changes import ENTITIES, _filled

        old = {c: None for c in ENTITIES.columns if c != "manipulated_by"}
        self.assertIsNone(_filled(ENTITIES, old)["manipulated_by"])

    async def test_the_party_never_holds_a_secret_or_unconfirmed_connection_as_fact(self) -> None:
        a, b = await self.pair()
        secret = await self.link(a, b, secret=True)
        waiting = (await self.relate(a, "ally_of", b, status=PROPOSED)).value[0].id
        party = (await self.kinds())["party"]
        for rel in (secret, waiting):
            with self.subTest(rel=rel), self.assertRaises(MemoryRuleError):
                await self.memory.believe(
                    GUILD_A, self.c, party, rel, 1.0, "revealed_fact", source="dm"
                )
        for guess in (0.5, 0.0):  # a guess or a hide goes through the connection's own switch
            with self.subTest(guess=guess), self.assertRaises(MemoryRuleError):
                await self.memory.believe(
                    GUILD_A, self.c, party, secret, guess, "theory", source="dm"
                )
        # and even a row put there by hand is not shown as known
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "INSERT INTO memory_beliefs (guild_id, campaign_id, id, perspective_id,"
                " relation_id, confidence, status, evidence_ids, seq, recorded_at)"
                " VALUES (%s, %s, %s, %s, %s, 1.0, 'revealed_fact', '{}', 999999, 1)",
                (GUILD_A, self.c, new_id(), party, secret),
            )
        self.assertEqual(await self.party(), {})
        self.assertEqual(await self.memory.party_view(GUILD_A, self.c, known_only=False), [])

    async def test_a_truth_the_dm_set_another_way_is_not_overwritten_by_the_flag(self) -> None:
        a, b = await self.pair()
        rel = await self.link(a, b)
        truth = (await self.kinds())["truth"]
        await self.memory.believe(GUILD_A, self.c, truth, rel, 0.0, "false_evidence", source="dm")
        await self.memory.update_relation(GUILD_A, self.c, rel, source="dm", secret=True)
        self.assertEqual((await self.truth()).get(rel), None)  # (0.0 is "not believed")
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute(
                "SELECT confidence, status FROM memory_beliefs WHERE perspective_id = %s"
                " AND relation_id = %s ORDER BY seq DESC LIMIT 1",
                (truth, rel),
            )
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual((row["confidence"], row["status"]), (0.0, "false_evidence"))

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
        shown: list[str] = []
        hidden: list[str] = []
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
        truth = (await self.kinds())["truth"]
        await self.memory.believe(
            GUILD_A, self.c, truth, rel, 0.5, "theory", source="dm", evidence=["claim:abc"]
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

    async def test_a_file_that_hands_the_players_a_secret_is_refused_and_gaps_are_filled(
        self,
    ) -> None:
        from dmbot.campaigns.models import CampaignError

        a, b = await self.pair()
        c = await self.add("Hollis")
        known, secret = await self.link(a, b), await self.link(a, c, secret=True)
        backup = await self.campaigns.export(GUILD_A, self.c)
        rows = self.memory_rows(backup)
        party = next(r["id"] for r in rows if r["table"] == "perspective" and r["kind"] == "party")
        crafted = [*rows, {**next(r for r in rows if r["table"] == "belief"),
                           "id": new_id(), "perspective_id": party, "relation_id": secret,
                           "confidence": 1.0, "seq": 99}]  # fmt: skip
        rows[:] = crafted
        with self.assertRaises(CampaignError):
            await self.campaigns.import_backup(GUILD_B, decode_backup(encode_backup(backup)), DM)
        # a file whose connection has no beliefs at all gets them, and keeps the ones it has
        backup = await self.campaigns.export(GUILD_A, self.c)
        rows = self.memory_rows(backup)
        rows[:] = [r for r in rows if not (r["table"] == "belief" and r["relation_id"] == known)]
        restored = await self.campaigns.import_backup(
            GUILD_B, decode_backup(encode_backup(backup)), DM
        )
        async with self.db.guild(GUILD_B) as conn:
            cur = await conn.execute(
                "SELECT p.kind, b.relation_id FROM memory_beliefs b JOIN memory_perspectives p"
                " ON p.guild_id = b.guild_id AND p.campaign_id = b.campaign_id"
                " AND p.id = b.perspective_id WHERE b.campaign_id = %s",
                (restored.id,),
            )
            got = {(r["kind"], r["relation_id"]) for r in await cur.fetchall()}
        self.assertEqual(got, {("truth", known), ("party", known), ("truth", secret)})

    async def test_a_connection_shown_and_hidden_again_still_backs_up_and_restores(self) -> None:
        a, b = await self.pair()
        rel = await self.link(a, b)
        await self.memory.update_relation(GUILD_A, self.c, rel, source="dm", secret=True)
        raw = encode_backup(await self.campaigns.export(GUILD_A, self.c))
        restored = await self.campaigns.import_backup(GUILD_B, decode_backup(raw), DM)
        async with self.db.guild(GUILD_B) as conn:
            cur = await conn.execute(
                "SELECT count(*) AS n FROM memory_beliefs WHERE campaign_id = %s", (restored.id,)
            )
            row = await cur.fetchone()
        assert row is not None
        self.assertGreaterEqual(row["n"], 3)  # the history came with it: it was known, then hidden
        view = await self.memory.party_view(GUILD_B, restored.id)
        self.assertEqual(view, [])  # and the players hold nothing of it now

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
