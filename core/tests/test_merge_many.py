"""Merging several names at once (#2426): `MemoryStore.merge_many` (one write, one Undo, all or
nothing), the look-alike groups and "Not the same" answers in the store, and the website's
`merge-many` action through the queue. Invented names throughout."""

from __future__ import annotations

from typing import Any

from dmbot.memory import web_actions as wa
from dmbot.memory.models import DM as DM_SOURCE
from dmbot.memory.models import MemoryRuleError
from tests.test_recap_web import BOB
from tests.test_web_api import ALICE, THURSDAY
from tests.test_web_name_actions import Acting

TABLES = ("memory_entities", "memory_aliases", "memory_relations", "memory_mentions")


class Spellings(Acting):
    """One creature written four ways, a place, and a secret name."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.b1 = await self.add("Belleros", "npc")
        self.b2 = await self.add("Beleros", "npc")
        self.b3 = await self.add("Bel Eros the Elder", "npc")
        self.b4 = await self.add("the Belleros", "character")
        for n, e in enumerate((self.b1, self.b1, self.b1, self.b2, self.b3), start=1):
            await self.mention(e, n)

    async def mention(self, entity: str, n: int) -> None:
        await self.sql(
            "INSERT INTO memory_mentions (guild_id, campaign_id, id, entity_id, line_ref,"
            " span_start, span_end, confidence, method, created_at)"
            " VALUES (%s, %s, %s, %s, %s, 0, 5, 1, 'dm', 1)",
            self.g, self.one.id, f"{n:032x}", entity, f"line {n}",
        )  # fmt: skip

    async def dump(self) -> dict[str, list[Any]]:
        out: dict[str, list[Any]] = {}
        async with self.db.guild(self.g) as conn:
            for table in TABLES:
                cur = await conn.execute(
                    f"SELECT * FROM {table} WHERE campaign_id = %s ORDER BY id",
                    (self.one.id,),
                )
                out[table] = [dict(r) for r in await cur.fetchall()]
        return out

    async def connect(self, a: str, b: str, *, secret: bool = False) -> None:
        await self.memory.add_relation(
            self.g, self.one.id, a, "located_in", b, source=DM_SOURCE, confidence=1.0,
            status="confirmed", secret=secret,
        )  # fmt: skip


class TheStoreMerge(Spellings):
    async def test_every_name_goes_onto_the_kept_one_in_one_batch(self) -> None:
        await self.connect(self.b2, self.keep)
        await self.alias(self.b3, "the Old Wolf")
        before = await self.memory.version(self.g, self.one.id)
        written = await self.memory.merge_many(
            self.g, self.one.id, self.b1, [self.b2, self.b3, self.b4], source=DM_SOURCE
        )
        self.assertEqual(written.value.id, self.b1)
        names = {n["name"] for n in (await self.page())["names"]}
        self.assertNotIn("Beleros", names)
        row = next(n for n in (await self.page())["names"] if n["id"] == self.b1)
        self.assertEqual(
            set(row["others"]), {"Beleros", "Bel Eros the Elder", "the Belleros", "the Old Wolf"}
        )
        self.assertEqual(row["mentions"], 5)  # every mention moved with its name
        rels = await self.memory.relations(self.g, self.one.id, entity_id=self.b1)
        self.assertEqual([(r.subject_id, r.object_id) for r in rels], [(self.b1, self.keep)])
        rows = await self.log_since(before)
        batches = {r["batch"] for r in rows if r["table_name"] == "memory_entities"}
        self.assertEqual(batches, {written.batch})  # one batch for all three merges

    async def test_one_undo_restores_every_name_connection_and_mention(self) -> None:
        await self.connect(self.b2, self.keep)
        await self.connect(self.b4, self.mira, secret=True)
        await self.alias(self.b3, "the Old Wolf", secret=True)
        before = await self.dump()
        written = await self.memory.merge_many(
            self.g, self.one.id, self.b1, [self.b2, self.b3, self.b4], source=DM_SOURCE
        )
        self.assertNotEqual(await self.dump(), before)
        assert written.batch is not None
        await self.memory.undo(self.g, self.one.id, written.batch, source=DM_SOURCE)
        after = await self.dump()
        for table in TABLES:
            self.assertEqual(after[table], before[table], table)
        names = {n["name"] for n in (await self.page())["names"]}
        self.assertTrue({"Belleros", "Beleros", "Bel Eros the Elder", "the Belleros"} <= names)

    async def test_a_secret_other_name_stays_a_secret_other_name(self) -> None:
        await self.alias(self.b2, "the Grey Ferryman", secret=True)
        await self.memory.merge_many(
            self.g, self.one.id, self.b1, [self.b2, self.b3], source=DM_SOURCE
        )
        row = next(n for n in (await self.page())["names"] if n["id"] == self.b1)
        self.assertIn("the Grey Ferryman", row["secretOthers"])
        self.assertNotIn("the Grey Ferryman", row["others"])
        players = await self.page(await self.signed(BOB, THURSDAY))
        self.assertNotIn("Grey Ferryman", str(players))

    async def test_a_secret_connection_stays_secret(self) -> None:
        await self.connect(self.b3, self.mira, secret=True)
        await self.memory.merge_many(self.g, self.one.id, self.b1, [self.b3], source=DM_SOURCE)
        rels = await self.memory.relations(
            self.g, self.one.id, entity_id=self.b1, include_secret=True
        )
        self.assertEqual([(r.subject_id, r.secret) for r in rels], [(self.b1, True)])

    async def test_names_of_different_kinds_merge_when_the_dm_asks(self) -> None:
        written = await self.memory.merge_many(
            self.g,
            self.one.id,
            self.keep,
            [self.b1],
            source=DM_SOURCE,  # a place keeps an npc
        )
        self.assertEqual(written.value.name, "Greywater Keep")

    async def test_all_or_nothing(self) -> None:
        before = await self.dump()
        with self.assertRaises(MemoryRuleError):
            await self.memory.merge_many(
                self.g, self.one.id, self.b1, [self.b2, "f" * 32], source=DM_SOURCE
            )
        self.assertEqual(await self.dump(), before)

    async def test_the_rules_for_the_list(self) -> None:
        for others in ([], [self.b1]):
            with self.assertRaises(MemoryRuleError):
                await self.memory.merge_many(self.g, self.one.id, self.b1, others, source=DM_SOURCE)
        with self.assertRaises(MemoryRuleError):
            await self.memory.merge_many(
                self.g, self.one.id, self.b1, [f"{i:032x}" for i in range(51)], source=DM_SOURCE
            )
        with self.assertRaises(ValueError):
            await self.memory.merge_many(self.g, self.one.id, self.b1, [self.b2], source="scan")

    async def test_a_repeated_name_is_merged_once(self) -> None:
        written = await self.memory.merge_many(
            self.g, self.one.id, self.b1, [self.b2, self.b2], source=DM_SOURCE
        )
        self.assertEqual(written.value.id, self.b1)


class TheLookAlikes(Spellings):
    async def groups(self) -> list[list[str]]:
        found = await self.memory.look_alikes(self.g, self.one.id)
        return [[n.name for n in g] for g in found]

    async def test_the_spellings_are_one_group_most_heard_first(self) -> None:
        found = await self.groups()
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0][0], "Belleros")  # heard three times
        self.assertEqual(
            sorted(found[0]), sorted(["Belleros", "Beleros", "Bel Eros the Elder", "the Belleros"])
        )

    async def test_only_the_groups_holding_a_name_can_be_asked_for(self) -> None:
        self.assertEqual(len(await self.memory.look_alikes(self.g, self.one.id, only=[self.b3])), 1)
        self.assertEqual(await self.memory.look_alikes(self.g, self.one.id, only=[self.keep]), [])

    async def test_people_at_the_table_and_gone_names_are_left_out(self) -> None:
        await self.memory.merge_many(self.g, self.one.id, self.b1, [self.b2], source=DM_SOURCE)
        await self.memory.set_entity_status(
            self.g, self.one.id, self.b3, "rejected", source=DM_SOURCE
        )
        names = [n for g in await self.groups() for n in g]
        self.assertNotIn("Beleros", names)  # merged away
        self.assertNotIn("Bel Eros the Elder", names)  # forgotten

    async def test_the_groups_follow_every_change_even_though_they_are_kept(self) -> None:
        self.assertEqual(len(await self.groups()), 1)
        self.assertEqual(len(await self.groups()), 1)  # (kept)
        extra = await self.add("Belleroz", "npc")
        self.assertIn("Belleroz", [n for g in await self.groups() for n in g])
        await self.memory.set_entity_status(
            self.g, self.one.id, extra, "rejected", source=DM_SOURCE
        )
        await self.memory.not_the_same(
            self.g, self.one.id, [self.b1, self.b2, self.b3, self.b4], user_id=ALICE.id
        )
        self.assertNotIn("Beleros", [n for g in await self.groups() for n in g])

    async def test_not_the_same_takes_up_to_fifty_one_names(self) -> None:
        with self.assertRaises(MemoryRuleError):
            await self.memory.not_the_same(
                self.g, self.one.id, [f"{i:032x}" for i in range(52)], user_id=ALICE.id
            )

    async def test_not_the_same_is_remembered_and_not_offered_again(self) -> None:
        added = await self.memory.not_the_same(
            self.g, self.one.id, [self.b1, self.b2, self.b3, self.b4], user_id=ALICE.id
        )
        self.assertEqual(added, 6)  # every pair among four
        self.assertEqual(await self.groups(), [])
        again = await self.memory.not_the_same(
            self.g, self.one.id, [self.b1, self.b2], user_id=ALICE.id
        )
        self.assertEqual(again, 0)

    async def test_not_the_same_needs_confirmed_names_of_this_campaign(self) -> None:
        other = (
            await self.memory.add_entity(
                self.g,
                self.two.id,
                type="npc",
                name="Belleros",
                source=DM_SOURCE,
                status="confirmed",
            )
        ).value.id
        with self.assertRaises(MemoryRuleError):
            await self.memory.not_the_same(self.g, self.one.id, [self.b1, other], user_id=ALICE.id)

    async def test_campaigns_do_not_see_each_others_names_or_answers(self) -> None:
        await self.memory.add_entity(
            self.g, self.two.id, type="npc", name="Beleros", source=DM_SOURCE, status="confirmed"
        )
        await self.memory.add_entity(
            self.g, self.two.id, type="npc", name="Belleros", source=DM_SOURCE, status="confirmed"
        )
        for other in (self.b3, self.b4):  # leave only the pair in campaign one
            await self.memory.set_entity_status(
                self.g, self.one.id, other, "rejected", source=DM_SOURCE
            )
        await self.memory.not_the_same(self.g, self.one.id, [self.b1, self.b2], user_id=ALICE.id)
        theirs = await self.memory.look_alikes(self.g, self.two.id)
        self.assertEqual([sorted(n.name for n in g) for g in theirs], [["Beleros", "Belleros"]])
        self.assertEqual(await self.memory.look_alikes(self.g, self.one.id), [])  # apart in one

    async def test_an_answer_goes_with_either_name(self) -> None:
        await self.memory.not_the_same(self.g, self.one.id, [self.b1, self.b2], user_id=ALICE.id)
        await self.sql("DELETE FROM memory_entities WHERE id = %s", self.b1)
        async with self.db.guild(self.g) as conn:
            cur = await conn.execute("SELECT count(*) AS n FROM name_not_same")
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual(row["n"], 0)


class TheWebAction(Spellings):
    async def test_the_names_are_merged_and_the_kept_row_comes_back(self) -> None:
        before = await self.memory.version(self.g, self.one.id)
        answer = await self.queue(self.b1, action="merge-many", others=[self.b2, self.b3, self.b4])
        self.assertEqual((await self.poll(answer))["state"], "waiting")
        self.assertEqual([o.error for o in await self.apply_all()], [""])
        done = await self.poll(answer)
        self.assertEqual((done["state"], done["row"]["name"]), ("done", "Belleros"))
        self.assertEqual(
            set(done["row"]["others"]), {"Beleros", "Bel Eros the Elder", "the Belleros"}
        )
        batches = {r["batch"] for r in await self.log_since(before)}
        self.assertEqual(len(batches), 1)  # the Discord card's Undo takes all of it back
        await self.memory.undo(self.g, self.one.id, batches.pop(), source=DM_SOURCE)
        self.assertIn("Beleros", [n["name"] for n in (await self.page())["names"]])

    async def test_rows_say_how_often_a_name_was_heard(self) -> None:
        by_name = {n["name"]: n["mentions"] for n in (await self.page())["names"]}
        self.assertEqual(
            (by_name["Belleros"], by_name["Beleros"], by_name["the Belleros"]), (3, 1, 0)
        )

    async def test_a_list_is_refused_at_the_post_and_at_the_door(self) -> None:
        bad: list[Any] = [
            {},
            {"others": "x"},
            {"others": []},
            {"others": [self.b1]},  # the kept name itself: the door's word
            {"others": [self.b2, self.b2]},
            {"others": ["nope"]},
            {"others": [f"{i:032x}" for i in range(51)]},
            {"others": ["f" * 32]},  # no such name
            {"others": [self.b2, 7]},
        ]
        for body in bad:
            code = await self.refused_action(self.b1, action="merge-many", **body)
            self.assertEqual(code, "bad_target", body)

    async def test_a_name_of_the_other_campaign_is_refused(self) -> None:
        theirs = (
            await self.memory.add_entity(
                self.g,
                self.two.id,
                type="npc",
                name="Beleros",
                source=DM_SOURCE,
                status="confirmed",
            )
        ).value.id
        code = await self.refused_action(self.b1, action="merge-many", others=[theirs])
        self.assertEqual(code, "bad_target")

    async def test_a_long_text_is_only_for_merge_many(self) -> None:
        long = "x" * 101
        self.assertEqual(await self.refused_action(self.b1, action="spell", name=long), "too_long")

    async def test_only_a_dm_may_queue_it(self) -> None:
        bob = await self.signed(BOB, THURSDAY)
        code = await self.refused_action(self.b1, who=bob, action="merge-many", others=[self.b2])
        self.assertEqual(code, "not_allowed")

    async def test_the_bot_refuses_a_name_that_went_and_changes_nothing(self) -> None:
        answer = await self.queue(self.b1, action="merge-many", others=[self.b2, self.b3])
        await self.memory.set_entity_status(
            self.g, self.one.id, self.b3, "rejected", source=DM_SOURCE
        )
        await self.apply_all()
        got = await self.poll(answer)
        self.assertEqual((got["state"], got["error"]), ("failed", "not_found"))
        names = [n["name"] for n in (await self.page())["names"]]
        self.assertIn("Beleros", names)  # all or nothing

    async def test_a_retried_job_that_already_went_through_is_done(self) -> None:
        answer = await self.queue(self.b1, action="merge-many", others=[self.b2, self.b3])
        await self.memory.merge_many(
            self.g, self.one.id, self.b1, [self.b2, self.b3], source=DM_SOURCE
        )
        await self.apply_all()
        self.assertEqual((await self.poll(answer))["state"], "done")

    async def test_the_bot_checks_the_sender_is_still_a_dm(self) -> None:
        await self.queue(self.b1, action="merge-many", others=[self.b2])
        action = await self.actions.claim_next(self.g)
        assert action is not None
        out = await wa.apply(self.memory, self.g, set(), action)
        self.assertEqual(out.error, wa.NOT_ALLOWED)
