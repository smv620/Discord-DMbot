"""Typed evidence links (#2662): the migration that copies the old lists, a link kept inside its
campaign, a link turning into a placeholder when a person's words are removed, a restore that keeps
placeholders, and the audit's counts. Needs the test database; invented names throughout."""

from __future__ import annotations

from typing import Any

from dmbot.campaigns import CampaignStore
from dmbot.db import Database, drop_schema
from dmbot.memory import evidence
from dmbot.memory.backup import MemorySection
from dmbot.memory.models import MemoryRuleError
from dmbot.memory.store import MemoryStore
from dmbot.schema import MIGRATIONS
from dmbot.transcript.models import line_ref
from dmbot.transcript.store import TranscriptStore
from tests.pg import TEST_URL, DatabaseTest
from tests.test_delete_my_lines import DM, GUILD, ME, START, line
from tests.test_memory_store import GUILD_A, MemoryTest

FRIEND = 9


class Migration0235(MemoryTest):
    """The old lists are copied into the table, with rows already there; nothing is lost."""

    async def test_the_lists_are_copied_and_the_table_is_isolated(self) -> None:
        before = [m for m in MIGRATIONS if m[0] < "0235"]
        await self.db.close()
        await drop_schema(TEST_URL, self.schema)
        self.db = await Database.open(TEST_URL, schema=self.schema, migrations=before)
        campaigns = CampaignStore(self.db, clock=lambda: 1)
        c = (await campaigns.create(GUILD_A, "Frozen Wastes", DM)).id
        a, b, rel, mention, claim, gone = (
            "1" * 32,
            "2" * 32,
            "3" * 32,
            "4" * 32,
            "5" * 32,
            "6" * 32,
        )
        perspective, belief = "7" * 32, "8" * 32
        async with self.db.guild(GUILD_A) as conn:
            for eid, name, kind in ((a, "Heartseeker", "item"), (b, "Mira", "character")):
                await conn.execute(
                    "INSERT INTO memory_entities (guild_id, campaign_id, id, type, name,"
                    " description, status, source, created_at, role, needs_look)"
                    " VALUES (%s, %s, %s, %s, %s, '', 'confirmed', 'dm', 1, NULL, false)",
                    (GUILD_A, c, eid, kind, name),
                )
            await conn.execute(
                "INSERT INTO memory_mentions (guild_id, campaign_id, id, entity_id,"
                " session_started_at, line_ref, span_start, span_end, confidence, method,"
                " created_at) VALUES (%s, %s, %s, %s, 5, 's1:1000:7', 0, 4, 0.9, 'exact', 1)",
                (GUILD_A, c, mention, a),
            )
            extra = ["9" * 32]  # the same line named again, in another session
            await conn.execute(
                "INSERT INTO memory_mentions (guild_id, campaign_id, id, entity_id,"
                " session_started_at, line_ref, span_start, span_end, confidence, method,"
                " created_at) VALUES (%s, %s, %s, %s, 6, 's1:1000:7', 0, 4, 0.9, 'exact', 1)",
                (GUILD_A, c, extra[0], a),
            )
            for n in range(25):  # more than a connection may be given now: all still come over
                extra.append(f"{n:032x}")
                await conn.execute(
                    "INSERT INTO memory_mentions (guild_id, campaign_id, id, entity_id,"
                    " session_started_at, line_ref, span_start, span_end, confidence, method,"
                    " created_at) VALUES (%s, %s, %s, %s, 7, %s, 0, 4, 0.9, 'exact', 1)",
                    (GUILD_A, c, extra[-1], a, f"s2:{n}:7"),
                )
            await conn.execute(
                "INSERT INTO memory_relations (guild_id, campaign_id, id, subject_id, predicate,"
                " object_id, detail, confidence, status, source, mention_ids, secret, created_at,"
                " definition_version) VALUES (%s, %s, %s, %s, 'owns', %s, '', 1, 'confirmed',"
                " 'dm', %s, false, 1, 1)",
                (GUILD_A, c, rel, b, a, [mention, gone, *extra]),  # one mention is already gone
            )
            await conn.execute(
                "INSERT INTO memory_claims (guild_id, campaign_id, id, subject, relationship,"
                " object, how, lines, source, prompt_version, model, status, created_at)"
                " VALUES (%s, %s, %s, 'Mira', 'owns', 'Heartseeker', 'dm_said', %s, 'extractor',"
                " 'v1', 'm', 'confirmed', 1)",
                (GUILD_A, c, claim, ["s1:1000:7"]),
            )
            cur = await conn.execute(
                "SELECT id FROM memory_perspectives WHERE guild_id = %s AND campaign_id = %s"
                " AND kind = 'truth'",
                (GUILD_A, c),
            )
            perspective = str((await cur.fetchone() or {})["id"])
            await conn.execute(
                "INSERT INTO memory_beliefs (guild_id, campaign_id, id, perspective_id,"
                " relation_id, confidence, status, session_at, evidence_ids, seq, recorded_at)"
                " VALUES (%s, %s, %s, %s, %s, 1.0, 'revealed_fact', 6, %s, 1, 2)",
                (GUILD_A, c, belief, perspective, rel, [claim, "s1:2000:7"]),
            )
        await self.db.migrate()
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute(
                "SELECT relation_id, belief_id, kind, target_id, session_started_at"
                " FROM memory_evidence ORDER BY kind, target_id NULLS LAST"
            )
            rows = [dict(r) for r in await cur.fetchall()]
        self.assertEqual(len(rows), 4 + 25)  # nothing is lost to a limit or a repeat
        self.assertIn(
            ("claim", claim, 6),
            [(r["kind"], r["target_id"], r["session_started_at"]) for r in rows],
        )
        self.assertEqual(
            [r["session_started_at"] for r in rows if r["target_id"] == "s1:1000:7"], [5]
        )  # (the same line named twice is one link, with the earlier session)
        self.assertEqual(
            sum(1 for r in rows if r["relation_id"] == rel and r["target_id"] is None), 1
        )  # the mention that was already gone: a placeholder
        self.assertEqual(
            {(r["relation_id"], r["belief_id"]) for r in rows}, {(rel, None), (None, belief)}
        )
        # it is isolated like every campaign table, and the migration left no opening behind
        async with self.db.unscoped() as conn:
            cur = await conn.execute(
                "SELECT count(*) AS n FROM pg_policies WHERE policyname = 'migrate_backfill'"
                " AND schemaname = current_schema()"
            )
            self.assertEqual((await cur.fetchone() or {})["n"], 0)
        async with self.db.guild(GUILD_A + 1) as conn:
            cur = await conn.execute("SELECT count(*) AS n FROM memory_evidence")
            self.assertEqual((await cur.fetchone() or {})["n"], 0)  # another server sees none


class Links(MemoryTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.a, self.b = await self.add("Mira"), await self.add("Rook")
        self.rel = (await self.relate(self.a, "knows", self.b)).value[0].id

    async def link(self, campaign: str | None = None, **kw: Any) -> evidence.Link:
        async with self.memory._write(GUILD_A, campaign or self.c, "dm") as w:
            return await evidence.add_in(w, relation_id=self.rel, **kw)

    async def rows(self) -> list[dict[str, Any]]:
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute(
                "SELECT kind, target_id FROM memory_evidence ORDER BY kind, target_id"
            )
            return [dict(r) for r in await cur.fetchall()]

    async def test_a_mention_becomes_a_line_link_beside_the_old_list(self) -> None:
        place = await self.add("Bryn Shander", type="place")
        made = await self.memory.add_mention(
            GUILD_A, self.c, place, line_ref="s1:1000:7", span=(0, 4), confidence=0.9,
            method="exact", source="dm", session_started_at=5,
        )  # fmt: skip
        again = await self.relate(self.b, "located_in", place, mention_ids=[made.value])
        self.assertEqual(again.value[0].mention_ids, (made.value,))  # the list still holds it
        self.assertEqual(await self.rows(), [{"kind": "line", "target_id": "s1:1000:7"}])

    async def test_the_same_link_twice_is_kept_once_and_a_clue_must_be_a_clue(self) -> None:
        clue = await self.add("The torn map", type="clue")
        npc = await self.add("Brask")
        await self.link(kind=evidence.CLUE, target_id=clue)
        await self.link(kind=evidence.CLUE, target_id=clue)
        self.assertEqual(await self.rows(), [{"kind": "clue", "target_id": clue}])
        with self.assertRaises(MemoryRuleError):  # a character is not a clue
            await self.link(kind=evidence.CLUE, target_id=npc)

    async def test_another_campaigns_ids_are_refused(self) -> None:
        other = (await self.campaigns.create(GUILD_A, "Sunken Coast", DM)).id
        foreign_clue = await self.add("Their map", type="clue", campaign=other)
        foreign_event = await self.add("Their ambush", type="event", campaign=other)
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "INSERT INTO transcript_sessions (id, guild_id, campaign_id, started_at)"
                " VALUES ('theirs', %s, %s, 1)",
                (GUILD_A, other),
            )
        for kind, target in (
            (evidence.CLUE, foreign_clue),
            (evidence.EVENT, foreign_event),
            (evidence.LINE, "theirs:1000:7"),
            (evidence.CLAIM, "f" * 32),
        ):
            with self.subTest(kind):
                with self.assertRaises(MemoryRuleError) as raised:
                    await self.link(kind=kind, target_id=target)
                self.assertEqual(str(raised.exception), evidence.NOT_THIS_CAMPAIGN)
        self.assertEqual(await self.rows(), [])

    async def test_a_link_cannot_be_made_for_a_connection_of_another_campaign(self) -> None:
        other = (await self.campaigns.create(GUILD_A, "Sunken Coast", DM)).id
        clue = await self.add("Their map", type="clue", campaign=other)
        async with self.memory._write(GUILD_A, other, "dm") as w:
            from psycopg import errors

            with self.assertRaises(errors.ForeignKeyViolation):
                await evidence.add_in(
                    w,
                    kind=evidence.CLUE,
                    target_id=clue,
                    relation_id=self.rel,  # (self.c's)
                )

    async def test_a_connection_that_an_undo_removes_takes_its_links_with_it(self) -> None:
        clue = await self.add("The torn map", type="clue")
        made = await self.relate(self.b, "located_in", await self.add("Keep", type="place"))
        async with self.memory._write(GUILD_A, self.c, "dm") as w:
            await evidence.add_in(
                w, kind=evidence.CLUE, target_id=clue, relation_id=made.value[0].id
            )
        self.assertEqual(len(await self.rows()), 1)
        await self.memory.undo(GUILD_A, self.c, made.batch or 0)
        self.assertEqual(await self.rows(), [])


class WhenWordsAreRemoved(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.campaigns = CampaignStore(self.db)
        self.store = TranscriptStore(self.db)
        self.memory = MemoryStore(self.db)
        self.campaign = await self.campaigns.create(GUILD, "Frostmaiden", DM)
        self.other = await self.campaigns.create(GUILD, "Sunken City", DM)
        self.first = await self.store.open_session(GUILD, self.campaign.id, START)
        await self.store.add_lines(
            GUILD,
            self.first,
            [line(1, ME, "I attack the goblin"), line(2, FRIEND, "Roll for it")],
        )
        c = self.campaign.id
        entities = []
        for name in ("Mira", "Rook"):
            made = await self.memory.add_entity(
                GUILD, c, type="npc", name=name, source="dm", status="confirmed"
            )
            entities.append(made.value.id)
        made_rel = await self.memory.add_relation(
            GUILD, c, entities[0], "knows", entities[1], source="dm", confidence=1.0
        )
        self.rel = made_rel.value[0].id
        lines = await self.store.lines(GUILD, self.first)
        mine = next(x for x in lines if x.user_id == ME)
        friends = next(x for x in lines if x.user_id == FRIEND)
        self.mine = line_ref(self.first, mine.started_ms, ME)
        self.friends = line_ref(self.first, friends.started_ms, FRIEND)
        async with self.memory._write(GUILD, c, "dm") as w:
            for ref in (self.mine, self.friends):
                await evidence.add_in(
                    w, kind=evidence.LINE, target_id=ref, relation_id=self.rel,
                    session_started_at=START, trusted=True,
                )  # fmt: skip

    async def links(self) -> list[dict[str, Any]]:
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                "SELECT kind, target_id, session_started_at FROM memory_evidence"
                " ORDER BY target_id NULLS FIRST"
            )
            return [dict(r) for r in await cur.fetchall()]

    async def test_deleting_my_lines_turns_my_links_into_placeholders(self) -> None:
        await self.store.delete_person(GUILD, ME)
        self.assertEqual(
            await self.links(),
            [
                {"kind": "line", "target_id": None, "session_started_at": START},
                {"kind": "line", "target_id": self.friends, "session_started_at": START},
            ],
        )
        async with self.db.guild(GUILD) as conn:  # nothing of mine is left in the table
            cur = await conn.execute(
                "SELECT count(*) AS n FROM memory_evidence WHERE target_id = %s", (self.mine,)
            )
            self.assertEqual((await cur.fetchone() or {})["n"], 0)

    async def test_an_old_sessions_retention_does_the_same(self) -> None:
        await self.store.end_session(GUILD, self.first, START + 100)
        gone = await self.store.delete_session_transcript(
            GUILD, self.campaign.id, self.first, START + 10**8, START + 10**8
        )
        self.assertEqual(gone, 2)
        self.assertEqual(
            [r["target_id"] for r in await self.links()], [None, None]
        )  # both lines were in that session

    async def test_stop_recording_me_turns_a_claims_links_into_placeholders(self) -> None:
        from dmbot.memory.claims import ClaimStore

        c = self.campaign.id
        claim = "c" * 32
        async with self.db.guild(GUILD) as conn:
            await conn.execute(
                "INSERT INTO memory_claims (guild_id, campaign_id, id, subject, relationship,"
                " object, how, lines, speaker_ids, source, prompt_version, model, status,"
                " created_at) VALUES (%s, %s, %s, 'Mira', 'knows', 'Rook', 'player_said', %s,"
                " %s, 'extractor', 'v1', 'm', 'proposed', 1)",
                (GUILD, c, claim, [self.mine], [ME]),
            )
        async with self.memory._write(GUILD, c, "dm") as w:
            await evidence.add_in(
                w, kind=evidence.CLAIM, target_id=claim, relation_id=self.rel,
                session_started_at=START,
            )  # fmt: skip
        gone = await ClaimStore(self.memory).forget_speaker(GUILD, ME)
        self.assertEqual(gone, 1)
        links = await self.links()
        self.assertIn(("claim", None), [(r["kind"], r["target_id"]) for r in links])
        self.assertNotIn(claim, [r["target_id"] for r in links])  # the claim's id is gone from it
        self.assertEqual(  # the line links were not touched by it
            sorted(str(r["target_id"]) for r in links if r["kind"] == "line"),
            sorted([self.mine, self.friends]),
        )

    async def test_the_old_lists_lose_the_reference_too(self) -> None:
        c = self.campaign.id
        async with self.memory._write(GUILD, c, "dm") as w:
            from dmbot.memory import beliefs

            ids = await beliefs.perspective_ids(w)
            row = await beliefs._add(
                w, ids["truth"], self.rel, 1.0, "revealed_fact", [self.mine], START
            )
        await self.store.delete_person(GUILD, ME)
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                "SELECT evidence_ids FROM memory_beliefs WHERE id = %s", (row["id"],)
            )
            self.assertEqual((await cur.fetchone() or {})["evidence_ids"], [])
            cur = await conn.execute(
                "SELECT count(*) AS n FROM memory_changes WHERE table_name = 'memory_beliefs'"
                " AND (before::text LIKE %s OR after::text LIKE %s)",
                (f"%{self.mine}%", f"%{self.mine}%"),
            )
            self.assertEqual((await cur.fetchone() or {})["n"], 0)  # not in the undo log either

    async def test_a_restore_keeps_placeholders_and_never_brings_a_line_back(self) -> None:
        await self.store.delete_person(GUILD, ME)
        c = self.campaign.id
        section = MemorySection()
        async with self.db.guild(GUILD) as conn:
            rows = await section.dump(conn, GUILD, c)
        dumped = [r for r in rows if r["table"] == "evidence"]
        self.assertEqual(len(dumped), 2)
        self.assertEqual({r["target_id"] for r in dumped}, {None})  # a line is never in a backup
        copy = (await self.campaigns.create(GUILD, "Copy", DM)).id
        async with self.db.guild(GUILD) as conn:
            await section.load(conn, GUILD, copy, rows)
            cur = await conn.execute(
                "SELECT kind, target_id, session_started_at FROM memory_evidence"
                " WHERE campaign_id = %s",
                (copy,),
            )
            restored = [dict(r) for r in await cur.fetchall()]
        self.assertEqual(len(restored), 2)
        self.assertEqual({r["target_id"] for r in restored}, {None})
        self.assertEqual({r["session_started_at"] for r in restored}, {START})

    async def test_the_audit_counts_a_dangling_link_a_foreign_one_and_placeholders(self) -> None:
        c = self.campaign.id
        other_claim = "d" * 32
        async with self.db.guild(GUILD) as conn:
            await conn.execute(
                "INSERT INTO memory_claims (guild_id, campaign_id, id, subject, relationship,"
                " object, how, lines, source, prompt_version, model, status, created_at)"
                " VALUES (%s, %s, %s, 'Mira', 'knows', 'Rook', 'dm_said', %s, 'extractor', 'v1',"
                " 'm', 'proposed', 1)",
                (GUILD, self.other.id, other_claim, ["x:1:2"]),
            )
            for kind, target in (("claim", other_claim), ("claim", "e" * 32)):
                await conn.execute(
                    "INSERT INTO memory_evidence (guild_id, campaign_id, relation_id, kind,"
                    " target_id, session_started_at, added_at) VALUES (%s, %s, %s, %s, %s, 1, 1)",
                    (GUILD, c, self.rel, kind, target),
                )
        await self.store.delete_person(GUILD, ME)  # one of the two line links is now a placeholder
        got = await self.memory.evidence_audit(GUILD, c)
        self.assertEqual((got.links, got.placeholders, got.dangling, got.foreign), (4, 1, 1, 1))
        self.assertNotRegex(got.line(), r"[0-9a-f]{32}")  # counts only
        clean = await self.memory.evidence_audit(GUILD, self.other.id)
        self.assertEqual((clean.links, clean.dangling, clean.foreign), (0, 0, 0))


class Untrusted(MemoryTest):
    """What a caller or a file may and may not put in a link."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.a, self.b = await self.add("Mira"), await self.add("Rook")
        self.rel = (await self.relate(self.a, "knows", self.b)).value[0].id
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "INSERT INTO transcript_sessions (id, guild_id, campaign_id, started_at)"
                " VALUES ('s1', %s, %s, 1)",
                (GUILD_A, self.c),
            )
            await conn.execute(
                "INSERT INTO memory_claims (guild_id, campaign_id, id, subject, relationship,"
                " object, how, lines, source, prompt_version, model, status, created_at)"
                " VALUES (%s, %s, %s, 'Mira', 'knows', 'Rook', 'dm_said', %s, 'extractor', 'v1',"
                " 'm', 'confirmed', 1)",
                (GUILD_A, self.c, "c" * 32, []),  # (a claim's own `lines` are a separate matter)
            )
        perspectives = await self.memory.perspectives(GUILD_A, self.c)
        self.truth = next(p.id for p in perspectives if p.kind == "truth")

    async def rows(self) -> list[dict[str, Any]]:
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute("SELECT kind, target_id FROM memory_evidence")
            return [dict(r) for r in await cur.fetchall()]

    async def believe(self, *items: str) -> Any:
        return await self.memory.believe(
            GUILD_A, self.c, self.truth, self.rel, 0.5, "theory", source="dm", evidence=list(items)
        )

    async def test_a_beliefs_evidence_is_a_claim_or_a_line_of_this_campaign(self) -> None:
        await self.believe("c" * 32, "s1:1000:7")
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute(
                "SELECT kind, target_id FROM memory_evidence WHERE belief_id IS NOT NULL"
                " ORDER BY kind"
            )
            self.assertEqual(
                [(r["kind"], r["target_id"]) for r in await cur.fetchall()],
                [("claim", "c" * 32), ("line", "s1:1000:7")],
            )
        other = (await self.campaigns.create(GUILD_A, "Sunken Coast", DM)).id
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "INSERT INTO transcript_sessions (id, guild_id, campaign_id, started_at)"
                " VALUES ('theirs', %s, %s, 1)",
                (GUILD_A, other),
            )
        for bad in ("theirs:1000:7", "free text", "d" * 32):  # another campaign's, none, unknown
            with self.subTest(bad), self.assertRaises(MemoryRuleError):
                await self.believe(bad)

    async def test_several_placeholders_may_stand_on_one_connection(self) -> None:
        async with self.db.guild(GUILD_A) as conn:
            for _ in range(3):
                await conn.execute(
                    "INSERT INTO memory_evidence (guild_id, campaign_id, relation_id, kind,"
                    " target_id, session_started_at, added_at) VALUES (%s, %s, %s, 'line', NULL,"
                    " 1, 1)",
                    (GUILD_A, self.c, self.rel),
                )
        self.assertEqual(len(await self.rows()), 3)

    async def test_a_backup_never_carries_a_speakers_line_and_a_hostile_file_cannot_add_one(
        self,
    ) -> None:
        import json

        await self.believe("c" * 32, "s1:1000:7")
        async with self.memory._write(GUILD_A, self.c, "dm") as w:
            await evidence.add_in(
                w, kind=evidence.LINE, target_id="s1:1000:7", relation_id=self.rel,
                session_started_at=5,
            )  # fmt: skip
        section = MemorySection()
        async with self.db.guild(GUILD_A) as conn:
            rows = await section.dump(conn, GUILD_A, self.c)
        self.assertNotIn(":1000:7", json.dumps(rows))  # no line reference, so no speaker's id
        links = [r for r in rows if r["table"] == "evidence"]
        self.assertIn("line", {r["kind"] for r in links})
        # a file that brings its own: a line, a claim and a clue that are not in it
        hostile = [r for r in rows if r["table"] != "evidence"]
        for kind, target in (("line", "s1:1000:7"), ("claim", "f" * 32), ("clue", "e" * 32)):
            hostile.append(
                {"table": "evidence", "id": f"{len(hostile):032x}", "relation_id": self.rel,
                 "belief_id": None, "kind": kind, "target_id": target,
                 "session_started_at": 1, "added_at": 1}
            )  # fmt: skip
        hostile += [r for r in rows if r["table"] == "belief"]  # (its lists are read as empty)
        copy = (await self.campaigns.create(GUILD_A, "Copy", DM)).id
        async with self.db.guild(GUILD_A) as conn:
            await section.load(conn, GUILD_A, copy, [r for r in hostile if r["table"] != "belief"])
            cur = await conn.execute(
                "SELECT kind, target_id FROM memory_evidence WHERE campaign_id = %s", (copy,)
            )
            self.assertEqual({r["target_id"] for r in await cur.fetchall()}, {None})

    async def test_a_file_without_evidence_rows_still_loads(self) -> None:
        section = MemorySection()
        async with self.db.guild(GUILD_A) as conn:
            rows = [
                r for r in await section.dump(conn, GUILD_A, self.c) if r["table"] != "evidence"
            ]
        copy = (await self.campaigns.create(GUILD_A, "Old", DM)).id
        async with self.db.guild(GUILD_A) as conn:
            await section.load(conn, GUILD_A, copy, rows)
            cur = await conn.execute(
                "SELECT count(*) AS n FROM memory_relations WHERE campaign_id = %s", (copy,)
            )
            self.assertEqual((await cur.fetchone() or {})["n"], 1)

    async def test_a_reworded_connection_carries_its_links(self) -> None:
        clue = await self.add("The torn map", type="clue")
        async with self.memory._write(GUILD_A, self.c, "dm") as w:
            await evidence.add_in(w, kind=evidence.CLUE, target_id=clue, relation_id=self.rel)
        await self.believe("c" * 32)
        from dmbot.memory import beliefs
        from dmbot.memory._changes import RELATIONS

        moved = await self.relate(self.a, "ally_of", self.b)
        async with self.memory._write(GUILD_A, self.c, "dm") as w:
            old = await w.get(RELATIONS, self.rel)
            assert old is not None
            await beliefs.carry(w, old, moved.value[0].id)
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute(
                "SELECT relation_id, belief_id FROM memory_evidence WHERE target_id = ANY(%s)",
                ([clue, "c" * 32],),
            )
            owners = {(r["relation_id"] is not None) for r in await cur.fetchall()}
        self.assertIn(True, owners)
