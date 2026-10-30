"""Family links worked out from stated ones, as suggestions (#2960): proposed, source worked_out,
never stated; recomputed safely; taken away when a source goes; a rejection is final; secret
follows the sources. Invented names."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from dmbot.memory.models import CONFIRMED, PROPOSED, REJECTED, WORKED_OUT_SOURCE
from tests.test_memory_store import GUILD_A, ConfirmedMemoryTest


class TheWorkedOutLinks(ConfirmedMemoryTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.aldric = await self.add("Aldric", type="character", status=CONFIRMED)
        self.bryn = await self.add("Bryn", type="character", status=CONFIRMED)
        self.mira = await self.add("Mira", type="character", status=CONFIRMED)

    async def state(
        self, a: str, role: str, b: str, *, secret: bool = False, status: str = CONFIRMED
    ) -> Any:
        made = await self.memory.add_relation(
            GUILD_A, self.c, a, "kin_of", b, source="dm", confidence=1.0, status=status,
            detail=role, directional=True, secret=secret,
        )  # fmt: skip
        return made.value[0]

    async def family(self) -> list[Any]:
        """Every family link, rejected ones too (the reader leaves those out)."""
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute(
                "SELECT id, subject_id, object_id, detail, status, source, secret, directional"
                " FROM memory_relations WHERE campaign_id = %s AND predicate = 'kin_of'",
                (self.c,),
            )
            return [SimpleNamespace(**dict(r)) for r in await cur.fetchall()]

    async def mine(self) -> list[Any]:
        return [r for r in await self.family() if r.source == WORKED_OUT_SOURCE]

    async def mother_and_sisters(self, *, secret: bool = False) -> tuple[Any, Any]:
        mother = await self.state(self.aldric, "mother", self.bryn, secret=secret)
        sister = await self.state(self.bryn, "sister", self.mira)
        return mother, sister

    async def test_a_sisters_mother_is_suggested_as_the_parent_and_never_confirmed(self) -> None:
        await self.mother_and_sisters()
        done = await self.memory.rework_family(GUILD_A, self.c)
        self.assertEqual((done.added, done.removed, done.waiting), (1, 0, 1))
        (link,) = await self.mine()
        self.assertEqual(
            (link.subject_id, link.detail, link.object_id, link.status, link.directional),
            (self.aldric, "parent", self.mira, PROPOSED, True),
        )

    async def test_recomputing_changes_nothing(self) -> None:
        await self.mother_and_sisters()
        await self.memory.rework_family(GUILD_A, self.c)
        again = await self.memory.rework_family(GUILD_A, self.c)
        self.assertEqual((again.added, again.removed, again.waiting), (0, 0, 1))
        self.assertEqual(len(await self.mine()), 1)

    async def test_it_waits_with_the_words_of_its_two_sources(self) -> None:
        await self.mother_and_sisters()
        await self.memory.rework_family(GUILD_A, self.c)
        (card,) = await self.memory.worked_out_waiting(GUILD_A, self.c)
        self.assertEqual((card.subject, card.role, card.object), ("Aldric", "parent", "Mira"))
        self.assertEqual(
            set(card.from_words),
            {"Aldric is the mother of Bryn", "Bryn is the sister of Mira"},
        )

    async def test_a_source_that_goes_takes_the_suggestion_with_it(self) -> None:
        _, sister = await self.mother_and_sisters()
        await self.memory.rework_family(GUILD_A, self.c)
        await self.memory.update_relation(GUILD_A, self.c, sister.id, source="dm", status=REJECTED)
        done = await self.memory.rework_family(GUILD_A, self.c)
        self.assertEqual((done.removed, done.waiting), (1, 0))
        self.assertEqual(await self.mine(), [])

    async def test_a_confirmed_one_stands_on_its_own_when_a_source_goes(self) -> None:
        _, sister = await self.mother_and_sisters()
        await self.memory.rework_family(GUILD_A, self.c)
        (link,) = await self.mine()
        await self.memory.update_relation(GUILD_A, self.c, link.id, source="dm", status=CONFIRMED)
        await self.memory.update_relation(GUILD_A, self.c, sister.id, source="dm", status=REJECTED)
        await self.memory.rework_family(GUILD_A, self.c)
        (still,) = await self.mine()
        self.assertEqual((still.id, still.status), (link.id, CONFIRMED))

    async def test_a_rejection_is_final(self) -> None:
        await self.mother_and_sisters()
        await self.memory.rework_family(GUILD_A, self.c)
        (link,) = await self.mine()
        await self.memory.update_relation(GUILD_A, self.c, link.id, source="dm", status=REJECTED)
        done = await self.memory.rework_family(GUILD_A, self.c)
        self.assertEqual(done.added, 0)
        (kept,) = await self.mine()
        self.assertEqual(kept.status, REJECTED)

    async def test_a_secret_source_makes_a_secret_suggestion(self) -> None:
        await self.mother_and_sisters(secret=True)
        await self.memory.rework_family(GUILD_A, self.c)
        (link,) = await self.mine()
        self.assertTrue(link.secret)

    async def test_a_proposed_link_is_not_a_source(self) -> None:
        await self.state(self.aldric, "mother", self.bryn, status=PROPOSED)
        await self.state(self.bryn, "sister", self.mira)
        done = await self.memory.rework_family(GUILD_A, self.c)
        self.assertEqual(done.added, 0)

    async def test_a_link_already_stated_is_not_suggested(self) -> None:
        await self.mother_and_sisters()
        await self.state(self.aldric, "parent", self.mira)
        done = await self.memory.rework_family(GUILD_A, self.c)
        self.assertEqual(done.added, 0)

    async def test_a_worked_out_link_is_never_a_source_for_another(self) -> None:
        await self.mother_and_sisters()
        await self.memory.rework_family(GUILD_A, self.c)
        (link,) = await self.mine()
        await self.memory.update_relation(GUILD_A, self.c, link.id, source="dm", status=CONFIRMED)
        done = await self.memory.rework_family(GUILD_A, self.c)
        self.assertEqual(done.added, 0)

    async def test_a_source_made_secret_later_makes_the_suggestion_secret(self) -> None:
        _, sister = await self.mother_and_sisters()
        await self.memory.rework_family(GUILD_A, self.c)
        await self.memory.update_relation(GUILD_A, self.c, sister.id, source="dm", secret=True)
        await self.memory.rework_family(GUILD_A, self.c)
        (mine,) = await self.mine()
        self.assertTrue(mine.secret)

    async def confirm(self, relation: Any) -> None:
        await self.memory.update_relation(
            GUILD_A, self.c, relation.id, source="dm", status=CONFIRMED
        )

    async def shown_to_players(self) -> list[Any]:
        """What a player's view reads: confirmed, not secret."""
        found = await self.memory.relations(
            GUILD_A, self.c, confirmed_only=True, include_secret=False
        )
        return [r for r in found if r.source == WORKED_OUT_SOURCE]

    async def test_an_unrevealed_source_makes_the_suggestion_secret(self) -> None:
        await self.state(self.aldric, "mother", self.bryn)
        sister = await self.state(self.bryn, "sister", self.mira)
        await self.memory.update_relation(GUILD_A, self.c, sister.id, source="dm", unrevealed=True)
        await self.memory.rework_family(GUILD_A, self.c)
        (mine,) = await self.mine()
        self.assertTrue(mine.secret)

    async def test_a_hidden_middle_name_makes_the_suggestion_secret(self) -> None:
        await self.mother_and_sisters()
        await self.memory.set_hidden_from_players(GUILD_A, self.c, self.bryn, True, source="dm")
        await self.memory.rework_family(GUILD_A, self.c)
        (mine,) = await self.mine()
        self.assertTrue(mine.secret)

    async def test_a_source_made_unrevealed_later_upgrades_the_suggestion(self) -> None:
        mother, _ = await self.mother_and_sisters()
        await self.memory.rework_family(GUILD_A, self.c)
        await self.memory.update_relation(GUILD_A, self.c, mother.id, source="dm", unrevealed=True)
        await self.memory.rework_family(GUILD_A, self.c)
        (mine,) = await self.mine()
        self.assertTrue(mine.secret)

    async def test_players_see_only_a_confirmed_link_from_plain_sources(self) -> None:
        await self.mother_and_sisters()
        await self.memory.rework_family(GUILD_A, self.c)
        self.assertEqual(await self.shown_to_players(), [])  # waiting: not shown
        (mine,) = await self.mine()
        await self.confirm(mine)
        self.assertEqual(len(await self.shown_to_players()), 1)  # plain sources: shown

    async def test_players_never_see_a_confirmed_link_from_a_hidden_source(self) -> None:
        await self.mother_and_sisters()
        await self.memory.set_hidden_from_players(GUILD_A, self.c, self.bryn, True, source="dm")
        await self.memory.rework_family(GUILD_A, self.c)
        (mine,) = await self.mine()
        await self.confirm(mine)
        self.assertEqual(await self.shown_to_players(), [])
