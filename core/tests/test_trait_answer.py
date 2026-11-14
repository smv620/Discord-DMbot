"""The bot's application of the website's lineage and class answers (#3946): one claimed at a
time, every check made again, the same store calls as the Discord card, and a short word when it
stops. Invented names and ids."""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

from dmbot import trait_answer
from dmbot.memory import trait_offers as to
from dmbot.memory.models import CLASS, CONFIRMED, SPECIES, MemoryRuleError, NewName, Trait
from dmbot.trait_answer import TraitAnswers
from tests.test_memory_names import DM, GUILD, MANAGER, NamesTest

NOW = int(time.time())  # (the round uses the real clock)
DWARF = Trait(SPECIES, "Dwarf", "SRD 5.2.1", "2024", True)
CLERIC = Trait(CLASS, "Cleric", "SRD 5.2.1", "2024", True)


class Answers(NamesTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.now = float(NOW)
        self.answers = TraitAnswers(self.db, clock=lambda: self.now)
        self.store = to.TraitOfferStore(self.db)
        made = await self.memory.add_names(
            GUILD, self.campaign.id, [NewName("Torvin Ashgrove", "npc", CONFIRMED)], source="dm"
        )
        (torvin,) = made.value
        assert torvin
        self.torvin: str = torvin
        await self.memory.set_rule_link(
            GUILD, self.campaign.id, torvin, SPECIES, "Human", source="dm",
            rules_source="SRD 5.2.1", edition="2024", known=True,
        )  # fmt: skip
        await self.store.offer(GUILD, self.campaign.id, [self.offer(DWARF), self.offer(CLERIC)])
        self.n = 0

    def offer(self, trait: Trait) -> to.Offer:
        return to.Offer(self.torvin, trait, "Torvin Ashgrove is a dwarf", "Notes")

    async def queue(
        self, action: str = "keep", trait: Trait = DWARF, user: int = DM, *, at: int = NOW
    ) -> str:
        self.n += 1
        rid = f"{self.n:032x}"
        async with self.db.guild(GUILD) as conn:
            await conn.execute(
                "INSERT INTO trait_answers (guild_id, campaign_id, id, user_id, entity_id, kind,"
                " name_key, action, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (GUILD, self.campaign.id, rid, user, self.torvin, trait.kind,
                 self.offer(trait).key, action, at),
            )  # fmt: skip
        return rid

    async def row(self, rid: str) -> dict[str, Any]:
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute("SELECT * FROM trait_answers WHERE id = %s", (rid,))
            row = await cur.fetchone()
        assert row is not None
        return dict(row)

    def fake_bot(self) -> Any:
        return SimpleNamespace(
            memory=self.memory, campaigns=SimpleNamespace(get=AsyncMock(return_value=self.campaign),
                                                          db=self.db)
        )  # fmt: skip

    async def test_the_oldest_waiting_answer_is_claimed_once(self) -> None:
        first = await self.queue(at=NOW - 20)
        await self.queue("never", CLERIC, at=NOW - 10)
        got = await self.answers.claim_next(GUILD)
        assert got is not None
        self.assertEqual((got.id, got.tries), (first, 1))
        again = await self.answers.claim_next(GUILD)
        assert again is not None and again.id != first
        self.assertIsNone(await self.answers.claim_next(GUILD))

    async def test_an_answer_that_stopped_moving_is_retaken_and_an_old_one_never(self) -> None:
        rid = await self.queue()
        await self.answers.claim_next(GUILD)
        self.now += trait_answer.RETAKE_AFTER_S + 5
        retaken = await self.answers.claim_next(GUILD)
        assert retaken is not None and (retaken.id, retaken.tries) == (rid, 2)
        self.now += trait_answer.GIVE_UP_S + 5
        self.assertIsNone(await self.answers.claim_next(GUILD))

    async def test_a_yes_changes_the_lineage_and_keeps_the_batch_for_undo(self) -> None:
        rid = await self.queue()
        self.assertEqual(await trait_answer.run_round(self.fake_bot(), GUILD), 1)
        row = await self.row(rid)
        self.assertEqual((row["state"], row["error"]), ("done", ""))
        links = await self.memory.rule_links(GUILD, self.campaign.id, {self.torvin})
        self.assertEqual([lk.name for lk in links if lk.kind == SPECIES], ["Dwarf"])
        self.assertIsNotNone(row["batch"])
        await self.memory.undo(GUILD, self.campaign.id, row["batch"])
        links = await self.memory.rule_links(GUILD, self.campaign.id, {self.torvin})
        self.assertEqual([lk.name for lk in links if lk.kind == SPECIES], ["Human"])

    async def test_a_no_is_remembered_and_changes_nothing(self) -> None:
        rid = await self.queue("never")
        await trait_answer.run_round(self.fake_bot(), GUILD)
        row = await self.row(rid)
        self.assertEqual((row["state"], row["error"], row["batch"]), ("done", "", None))
        waiting = await self.store.waiting(GUILD, self.campaign.id)
        self.assertEqual([o.trait.name for o in waiting], ["Cleric"])
        links = await self.memory.rule_links(GUILD, self.campaign.id, {self.torvin})
        self.assertEqual([lk.name for lk in links if lk.kind == SPECIES], ["Human"])

    async def test_someone_who_is_no_longer_a_dm_is_not_allowed_and_nothing_changes(self) -> None:
        rid = await self.queue(user=MANAGER)
        await trait_answer.run_round(self.fake_bot(), GUILD)
        row = await self.row(rid)
        self.assertEqual((row["state"], row["error"]), ("failed", trait_answer.NOT_ALLOWED))
        self.assertEqual(len(await self.store.waiting(GUILD, self.campaign.id)), 2)

    async def test_a_question_answered_in_discord_meanwhile_is_gone(self) -> None:
        rid = await self.queue()
        await self.store.never(GUILD, self.campaign.id, self.offer(DWARF))
        await trait_answer.run_round(self.fake_bot(), GUILD)
        row = await self.row(rid)
        self.assertEqual((row["state"], row["error"]), ("failed", trait_answer.GONE))

    async def test_a_campaign_that_is_gone_ends_gone(self) -> None:
        rid = await self.queue()
        me = self.fake_bot()
        me.campaigns.get = AsyncMock(return_value=None)
        await trait_answer.run_round(me, GUILD)
        self.assertEqual((await self.row(rid))["error"], trait_answer.GONE)

    async def test_a_yes_the_rules_refuse_ends_refused(self) -> None:
        rid = await self.queue()
        with patch.object(to.TraitOfferStore, "keep", AsyncMock(side_effect=MemoryRuleError("x"))):
            await trait_answer.run_round(self.fake_bot(), GUILD)
        row = await self.row(rid)
        self.assertEqual((row["state"], row["error"]), ("failed", trait_answer.REFUSED))

    async def test_any_other_failure_ends_failed_and_a_lost_claim_is_not_written(self) -> None:
        rid = await self.queue()
        with patch.object(to.TraitOfferStore, "keep", AsyncMock(side_effect=RuntimeError("x"))):
            await trait_answer.run_round(self.fake_bot(), GUILD)
        self.assertEqual((await self.row(rid))["error"], trait_answer.FAILED)
        rid2 = await self.queue("never", CLERIC)
        got = await self.answers.claim_next(GUILD)
        assert got is not None and got.id == rid2
        self.now += trait_answer.RETAKE_AFTER_S + 5
        other = await self.answers.claim_next(GUILD)  # another worker retakes it
        assert other is not None
        self.assertFalse(await self.answers.end(GUILD, got, ok=True))
        self.assertEqual((await self.row(rid2))["state"], "working")

    async def test_an_answer_tried_too_often_is_failed_not_applied(self) -> None:
        rid = await self.queue()
        async with self.db.guild(GUILD) as conn:  # (it has been taken MAX_TRIES times already)
            await conn.execute("UPDATE trait_answers SET tries = %s", (trait_answer.MAX_TRIES,))
        await trait_answer.run_round(self.fake_bot(), GUILD)
        self.assertEqual((await self.row(rid))["error"], trait_answer.FAILED)
        self.assertEqual(len(await self.store.waiting(GUILD, self.campaign.id)), 2)

    async def test_a_no_for_a_question_already_gone_ends_gone(self) -> None:
        rid = await self.queue("never")
        await self.store.never(GUILD, self.campaign.id, self.offer(DWARF))
        await trait_answer.run_round(self.fake_bot(), GUILD)
        self.assertEqual((await self.row(rid))["error"], trait_answer.GONE)

    async def test_the_question_is_matched_on_its_kind_too(self) -> None:
        same_name_class = Trait(CLASS, "Dwarf", "SRD 5.2.1", "2024", True)
        answer = trait_answer.Answer(
            "i", self.campaign.id, DM, self.torvin, CLASS, self.offer(DWARF).key, "keep", 0
        )
        self.assertIsNone(trait_answer._find([self.offer(DWARF)], answer))
        self.assertIsNotNone(trait_answer._find([self.offer(same_name_class)], answer))

    async def test_without_memory_nothing_is_taken(self) -> None:
        rid = await self.queue()
        me = self.fake_bot()
        me.memory = None
        self.assertEqual(await trait_answer.run_round(me, GUILD), 0)
        self.assertEqual((await self.row(rid))["state"], "waiting")

    async def test_a_retaken_yes_whose_write_already_landed_ends_done(self) -> None:
        rid = await self.queue()
        await self.store.keep(self.memory, GUILD, self.campaign.id, self.offer(DWARF))
        async with self.db.guild(GUILD) as conn:  # (it was taken once, then the bot stopped)
            await conn.execute("UPDATE trait_answers SET tries = 1")
        await trait_answer.run_round(self.fake_bot(), GUILD)
        row = await self.row(rid)
        self.assertEqual((row["state"], row["error"]), ("done", ""))
