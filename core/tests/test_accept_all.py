"""accept_all (#3544): accepts every waiting suggestion of the public sample campaign only, through
the store's own DM calls, counts only. Invented names."""

from __future__ import annotations

from typing import Any

from dmbot.devtools import accept_all as tool
from dmbot.memory.backstory_links import BackstoryLinks, NewLink, NewWhich
from dmbot.memory.models import BACKGROUND_SOURCE, PROPOSED, MemoryRuleError, MoreNames, NewName
from dmbot.memory.models import DM as DM_SOURCE
from tests.test_memory_names import DM, GUILD, NamesTest


class Base(NamesTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.sample = await self.campaigns.create(GUILD, tool.SAMPLE_CAMPAIGN, DM)
        self.links = BackstoryLinks(self.db)
        self.mira = await self.read_name("Mirelle", "character", "npc")
        self.oak = await self.read_name("Oakmere", "place")
        self.dorn = await self.read_name("Dorn", "character", "npc")
        self.dorna = await self.read_name("Dorna", "character", "npc")
        await self.links.add(
            GUILD, self.sample.id,
            [NewLink(self.mira, "knows", self.oak, "q", upload="a file, 1 Jan")],
        )  # fmt: skip

    async def read_name(self, name: str, type: str, role: str | None = None) -> str:
        """A waiting name as a lore read leaves it: with its kind (the DM's one-press Yes)."""
        made = await self.memory.add_entity(
            GUILD, self.sample.id, type=type, name=name, role=role, source=BACKGROUND_SOURCE
        )
        return made.value.id

    async def statuses(self, campaign: str | None = None) -> dict[str, str]:
        found = await self.memory.entities(
            GUILD, campaign or self.sample.id, statuses=("proposed", "confirmed")
        )
        return {e.name: e.status for e in found}

    async def run_it(self, **kw: Any) -> tool.Counts:
        return await tool.run(self.db, GUILD, self.sample.id, **{"dry_run": False, **kw})


class OnTheSample(Base):
    async def test_it_refuses_any_other_campaign_and_does_nothing(self) -> None:
        with self.assertRaises(tool.WrongCampaign):
            await tool.run(self.db, GUILD, self.campaign.id, dry_run=False)
        with self.assertRaises(tool.WrongCampaign):
            await tool.run(self.db, GUILD, "no-such-campaign", dry_run=True)
        self.assertEqual(set((await self.statuses()).values()), {"proposed"})

    async def test_a_dry_run_counts_and_changes_nothing(self) -> None:
        counts = await self.run_it(dry_run=True)
        self.assertEqual(counts.accepted[tool.NAMES], 4)
        self.assertEqual(set((await self.statuses()).values()), {"proposed"})
        self.assertEqual(await self.links.count(GUILD, self.sample.id), 1)

    async def test_every_kind_is_accepted(self) -> None:
        await self.memory.confirm_entity(
            GUILD, self.sample.id, self.mira, "character", source=DM_SOURCE, role="npc"
        )
        await self.memory.add_names(  # the confirmed name gets a suggested other name
            GUILD, self.sample.id, [], source=DM_SOURCE,
            more=[MoreNames(self.mira, ("Mira",), (), proposed=True)],
        )  # fmt: skip
        await self.links.add_which(
            GUILD, self.sample.id,
            [NewWhich(self.oak, False, "knows", "Dor", (self.dorn, self.dorna), "q")],
        )  # fmt: skip
        counts = await self.run_it()
        self.assertEqual(set((await self.statuses()).values()), {"confirmed"})
        self.assertEqual(counts.accepted[tool.NAMES], 3)  # (Mirelle was confirmed by hand)
        self.assertEqual(counts.accepted[tool.OTHER_NAMES], 1)
        self.assertEqual(counts.accepted[tool.QUESTIONS], 1)
        self.assertEqual(counts.accepted[tool.CONNECTIONS], 2)  # the link, and the answer
        self.assertEqual(await self.memory.suggested_names(GUILD, self.sample.id), [])
        self.assertEqual(await self.links.count_which(GUILD, self.sample.id), 0)

    async def test_a_name_that_needs_a_person_stays_waiting_and_is_counted(self) -> None:
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        wen = SimpleNamespace(
            id="w", type="character", role="player_character", source=BACKGROUND_SOURCE
        )
        memory = SimpleNamespace(entities=AsyncMock(return_value=[wen]), confirm_entity=AsyncMock())
        counts = tool.Counts()
        await tool.accept_names(memory, GUILD, "c", counts, False)  # type: ignore[arg-type]
        self.assertEqual(counts.waiting[tool.NAMES], 1)
        memory.confirm_entity.assert_not_awaited()  # who plays a character is for a person to say

    async def test_a_rule_that_refuses_is_counted_not_crashed(self) -> None:
        async def refuse(*args: Any, **kw: Any) -> Any:
            raise MemoryRuleError("no")

        self.memory.confirm_entity = refuse  # type: ignore[method-assign]
        counts = await tool.run(self.db, GUILD, self.sample.id, dry_run=False, memory=self.memory)
        self.assertEqual(counts.refused[tool.NAMES], 4)
        self.assertEqual(counts.accepted[tool.NAMES], 0)

    async def test_another_campaign_is_untouched(self) -> None:
        other = self.campaign.id
        await self.memory.add_names(
            GUILD, other, [NewName("Zed", "character", PROPOSED, role="npc")], source=DM_SOURCE
        )
        await self.run_it()
        self.assertEqual(await self.statuses(other), {"Zed": "proposed"})

    async def test_the_output_has_counts_and_no_names(self) -> None:
        line = (await self.run_it()).line(False)
        for name in ("Mirelle", "Oakmere", "Dorn"):
            self.assertNotIn(name, line)
        self.assertIn("names: 4 accepted", line)


class SamePerson(NamesTest):
    async def test_a_same_person_question_is_answered_with_the_best_offer(self) -> None:
        sample = await self.campaigns.create(GUILD, tool.SAMPLE_CAMPAIGN, DM)
        ken = await self.memory.add_entity(
            GUILD, sample.id, type="character", name="Ken the guard", role="npc",
            description="the guard of the north gate", status="confirmed", source=DM_SOURCE,
        )  # fmt: skip
        await self.memory.add_unnamed(
            GUILD, sample.id, what="guard at the north gate", role="guard"
        )
        offers = await self.memory.same_person_offers(GUILD, sample.id)
        self.assertEqual(len(offers), 1, "the fixture must make exactly one offer")
        counts = await tool.run(self.db, GUILD, sample.id, dry_run=False)
        self.assertEqual(counts.accepted[tool.SAME_PERSON], 1)
        self.assertEqual(await self.memory.same_person_offers(GUILD, sample.id), [])
        self.assertEqual(ken.value.name, "Ken the guard")


class Robust(Base):
    async def test_a_link_to_a_name_left_waiting_is_not_kept(self) -> None:
        plain = (
            await self.memory.add_entity(  # no kind read with it: a person must say what it is
                GUILD, self.sample.id, type="character", name="Kesh", source=DM_SOURCE
            )
        ).value.id
        await self.links.add(GUILD, self.sample.id, [NewLink(self.mira, "knows", plain, "q")])
        counts = await self.run_it()
        self.assertEqual(counts.accepted[tool.CONNECTIONS], 1)  # only the ready one
        self.assertEqual(counts.waiting[tool.CONNECTIONS], 1)
        self.assertEqual((await self.statuses())["Kesh"], "proposed")

    async def test_a_name_with_no_kind_read_stays_waiting(self) -> None:
        await self.memory.add_entity(
            GUILD, self.sample.id, type="character", name="Kesh", source=DM_SOURCE
        )
        counts = await self.run_it()
        self.assertEqual(counts.waiting[tool.NAMES], 1)

    async def test_a_question_with_no_candidate_stays_waiting(self) -> None:
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        question = SimpleNamespace(id="q", candidates=())
        links = SimpleNamespace(which_waiting=AsyncMock(return_value=[question]))
        counts = tool.Counts()
        await tool.accept_questions(object(), links, GUILD, "c", counts, False)  # type: ignore[arg-type]
        self.assertEqual(counts.waiting[tool.QUESTIONS], 1)

    async def test_a_failure_in_one_kind_keeps_the_counts_and_the_other_kinds(self) -> None:
        async def boom(*args: Any, **kw: Any) -> Any:
            raise RuntimeError("down")

        self.links.waiting = boom  # type: ignore[method-assign]
        # (the run builds its own queue store, so make the store fail where it is shared)
        self.memory.suggested_names = boom  # type: ignore[method-assign]
        counts = await tool.run(self.db, GUILD, self.sample.id, dry_run=False, memory=self.memory)
        self.assertEqual(counts.accepted[tool.NAMES], 4)
        self.assertEqual(counts.refused[tool.OTHER_NAMES], 1)

    async def test_the_numbers_of_the_changes_are_reported(self) -> None:
        counts = await self.run_it()
        self.assertGreaterEqual(len(counts.batches), 4)
        self.assertIn(f"changes {min(counts.batches)} to {max(counts.batches)}", counts.line(False))


class TheCommand(Base):
    async def test_it_needs_yes_unless_a_dry_run_and_refuses_another_campaign(self) -> None:
        import argparse
        import os
        from unittest.mock import patch

        def args(campaign: str, yes: bool = False) -> argparse.Namespace:
            return argparse.Namespace(guild=GUILD, campaign=campaign, dry_run=False, yes=yes)

        self.assertEqual(await tool.main_async(args(self.sample.id)), 2)  # no --yes: nothing done
        self.assertEqual(set((await self.statuses()).values()), {"proposed"})
        with patch.dict(os.environ, {"DATABASE_URL": ""}):
            self.assertEqual(await tool.main_async(args(self.sample.id, yes=True)), 2)
