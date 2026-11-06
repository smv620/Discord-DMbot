"""Which of two disagreeing house rules wins (#2284): the store, its isolation and its backup."""

from __future__ import annotations

import json
import unittest

from dmbot.campaigns import CampaignStore
from dmbot.campaigns.models import CampaignError
from dmbot.rules.house import HouseRulesSection
from dmbot.rules.wins import RuleRef, WinsSection, WinsStore
from tests.pg import DatabaseTest

GUILD_A, GUILD_B, DM = 71, 72, 5
OWN = RuleRef("", 1)
OTHER = RuleRef("", 2)
SET_RULE = RuleRef("Red Table", 2)  # shares a number with the campaign's own rule 2


class TheStore(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.wins = WinsStore(self.db, clock=lambda: 1000)
        self.campaigns = CampaignStore(self.db)
        self.campaigns.register_section(HouseRulesSection())
        self.campaigns.register_section(WinsSection())
        self.one = await self.campaigns.create(GUILD_A, "One", DM)

    async def test_an_answer_is_followed_whichever_way_the_pair_is_asked(self) -> None:
        await self.wins.record(GUILD_A, self.one.id, winner=OTHER, loser=OWN, by=DM)
        for a, b in ((OWN, OTHER), (OTHER, OWN)):
            self.assertEqual(await self.wins.winner_of(GUILD_A, self.one.id, a, b), OTHER)
        self.assertIsNone(await self.wins.winner_of(GUILD_A, self.one.id, OWN, SET_RULE))

    async def test_a_second_answer_replaces_the_first_and_a_set_rule_is_told_by_its_set(
        self,
    ) -> None:
        await self.wins.record(GUILD_A, self.one.id, winner=OWN, loser=OTHER, by=DM)
        await self.wins.record(GUILD_A, self.one.id, winner=OTHER, loser=OWN, by=DM)
        await self.wins.record(GUILD_A, self.one.id, winner=SET_RULE, loser=OWN, by=DM)
        self.assertEqual(await self.wins.winner_of(GUILD_A, self.one.id, OWN, OTHER), OTHER)
        self.assertEqual(await self.wins.winner_of(GUILD_A, self.one.id, OWN, SET_RULE), SET_RULE)
        self.assertEqual(len(await self.wins.all(GUILD_A, self.one.id)), 2)

    async def test_a_rule_cannot_win_over_itself(self) -> None:
        with self.assertRaises(ValueError):
            await self.wins.record(GUILD_A, self.one.id, winner=OWN, loser=OWN, by=DM)

    async def test_forgetting_a_rule_removes_only_its_own_pairs(self) -> None:
        await self.wins.record(GUILD_A, self.one.id, winner=OWN, loser=OTHER, by=DM)
        await self.wins.record(GUILD_A, self.one.id, winner=SET_RULE, loser=RuleRef("", 3), by=DM)
        self.assertEqual(await self.wins.forget_own_rule(GUILD_A, self.one.id, 2), 1)
        # (the set's rule 2 is not the campaign's rule 2)
        self.assertEqual([w.winner for w in await self.wins.all(GUILD_A, self.one.id)], [SET_RULE])

    async def test_changing_or_removing_a_rule_forgets_what_was_decided_about_it(self) -> None:
        from dmbot.rules.house import HouseRuleStore

        rules = HouseRuleStore(self.db)
        for words in ("Potions are a bonus action", "Potions take an item interaction", "Other"):
            await rules.add(GUILD_A, self.one.id, DM, words)
        await self.wins.record(GUILD_A, self.one.id, winner=RuleRef("", 2), loser=OWN, by=DM)
        await self.wins.record(GUILD_A, self.one.id, winner=RuleRef("", 3), loser=OWN, by=DM)
        await rules.edit(GUILD_A, self.one.id, DM, 2, "Potions are a free action")
        self.assertEqual(
            [w.winner for w in await self.wins.all(GUILD_A, self.one.id)], [RuleRef("", 3)]
        )
        await rules.remove(GUILD_A, self.one.id, DM, 3)
        self.assertEqual(await self.wins.all(GUILD_A, self.one.id), [])

    async def test_another_server_never_sees_them(self) -> None:
        await self.wins.record(GUILD_A, self.one.id, winner=OWN, loser=OTHER, by=DM)
        self.assertEqual(await self.wins.all(GUILD_B, self.one.id), [])

    async def test_they_travel_in_a_backup_and_go_with_the_campaign(self) -> None:
        await self.wins.record(GUILD_A, self.one.id, winner=SET_RULE, loser=OWN, by=DM)
        backup = await self.campaigns.export(GUILD_A, self.one.id)
        self.assertIn("house_rule_wins", json.dumps(backup))
        await self.campaigns.delete(GUILD_A, self.one.id)
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute("SELECT count(*) AS n FROM house_rule_wins")
            self.assertEqual((await cur.fetchone() or {})["n"], 0)
        restored = await self.campaigns.import_backup(GUILD_B, backup, DM)
        (win,) = await self.wins.all(GUILD_B, restored.id)
        self.assertEqual((win.winner, win.loser, win.decided_by), (SET_RULE, OWN, DM))

    def test_a_damaged_backup_is_refused(self) -> None:
        section = WinsSection()
        good = {
            "winner": {"set": "", "number": 1},
            "loser": {"set": "", "number": 2},
            "by": "5",
            "at": 1,
        }
        self.assertEqual(len(section.check([good])), 1)
        for bad in (
            [{**good, "extra": 1}],
            [{**good, "loser": good["winner"]}],
            [{**good, "winner": {"set": "", "number": 0}}],
            [{**good, "winner": {"set": 3, "number": 1}}],
            [{**good, "by": "x"}],
            [{**good, "at": True}],
            [good, good],
            [good, {**good, "winner": good["loser"], "loser": good["winner"]}],
            "nope",
        ):
            with self.subTest(bad), self.assertRaises(CampaignError):
                section.check(bad)  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
