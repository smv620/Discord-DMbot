"""A companion travels with a player character or the party (#3176): the party stays the players'
point of view and never gets a member from this, a document never points at the party, and the
span is the connection's own. Invented names."""

from __future__ import annotations

from typing import Any

from dmbot.memory.models import BACKGROUND_SOURCE, CONFIRMED, MemoryRuleError
from tests.test_memory_names import GUILD, NamesTest

TRAVELS = "travels_with"


class Companions(NamesTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        made = await self.memory.add_entity(
            GUILD, self.campaign.id, name="Cerric", type="character", role="player_character",
            played_by=101, status=CONFIRMED, source="dm",
        )  # fmt: skip
        self.pc = made.value.id
        self.owl = await self.npc("Glyph")
        self.guide = await self.npc("Old Marn")
        await self.memory.link_party(GUILD, self.campaign.id, {101})
        entities = await self.memory.entities(GUILD, self.campaign.id)
        self.party = next(e.id for e in entities if e.source == "party")
        self.guild_house = (
            await self.memory.add_entity(
                GUILD, self.campaign.id, name="Silver Lanterns", type="faction",
                status=CONFIRMED, source="dm",
            )
        ).value.id  # fmt: skip

    async def npc(self, name: str) -> str:
        made = await self.memory.add_entity(
            GUILD, self.campaign.id, name=name, type="character", role="npc",
            status=CONFIRMED, source="dm",
        )  # fmt: skip
        return made.value.id

    async def go(self, who: str, to: str, source: str = "dm", **kw: Any) -> Any:
        return await self.memory.add_relation(
            GUILD, self.campaign.id, who, TRAVELS, to, source=source, confidence=1.0, **kw
        )

    async def test_a_companion_may_travel_with_a_player_character_from_any_source(self) -> None:
        for source in ("dm", "extractor", BACKGROUND_SOURCE):
            companion = await self.npc(f"Friend {source}")
            written = await self.go(companion, self.pc, source)
            self.assertEqual(written.value[0].predicate, TRAVELS)

    async def test_the_party_may_be_the_target_for_the_dm_and_the_session_reader(self) -> None:
        await self.go(self.owl, self.party, "dm")
        await self.go(self.guide, self.party, "extractor")

    async def test_a_document_never_points_at_the_party(self) -> None:
        for source in (BACKGROUND_SOURCE, "scan"):
            with self.assertRaises(MemoryRuleError):
                await self.go(self.owl, self.party, source)

    async def test_every_other_source_is_refused_for_the_party(self) -> None:
        for source in ("worked_out", "character", "cleaner"):
            with self.assertRaises(MemoryRuleError):
                await self.go(self.owl, self.party, source)

    async def test_a_player_character_or_the_party_is_never_the_companion(self) -> None:
        with self.assertRaises(MemoryRuleError):
            await self.go(self.pc, self.party, "dm")
        with self.assertRaises(MemoryRuleError):
            await self.go(self.party, self.pc, "dm")

    async def test_nothing_else_is_a_target(self) -> None:
        for target in (self.guide, self.guild_house):
            with self.assertRaises(MemoryRuleError):
                await self.go(self.owl, target, "dm")

    async def test_a_companion_never_becomes_a_member_of_the_party(self) -> None:
        await self.go(self.owl, self.party, "dm")
        done = await self.memory.link_party(GUILD, self.campaign.id, {101})
        self.assertEqual(done.members, 0)  # (already linked; the owl is not one of them)
        members = {
            r.subject_id
            for r in await self.memory.relations(GUILD, self.campaign.id, include_secret=True)
            if r.predicate == "member_of"
        }
        self.assertNotIn(self.owl, members)
        self.assertEqual(members, {self.pc})

    async def test_the_span_is_the_connections_own_and_can_close(self) -> None:
        written = await self.go(self.guide, self.party, "dm", from_session_at=100)
        rel = written.value[0]
        self.assertIsNone(rel.to_session_at)  # (no end: with the party now)
        await self.memory.update_relation(
            GUILD, self.campaign.id, rel.id, source="dm", to_session_at=200
        )
        rows = await self.memory.relations(GUILD, self.campaign.id, include_secret=True)
        (kept,) = [r for r in rows if r.id == rel.id]
        self.assertEqual((kept.from_session_at, kept.to_session_at), (100, 200))

    async def test_a_hidden_companion_shows_nothing_to_a_player(self) -> None:
        await self.go(self.guide, self.party, "dm", secret=True)
        shown = await self.memory.relations(GUILD, self.campaign.id)  # (the public reader)
        self.assertEqual([r for r in shown if r.predicate == TRAVELS], [])

    async def test_the_party_merged_into_a_group_makes_it_the_party_and_keeps_the_companion(
        self,
    ) -> None:
        """#3954: the `party` flag is the one marker, and a merge moves it to a group it is kept
        on (#1655), so the companion still travels with the party."""
        await self.go(self.owl, self.party, "dm")
        other = (
            await self.memory.add_entity(
                GUILD, self.campaign.id, name="Grey Wolves", type="faction",
                status=CONFIRMED, source="dm",
            )
        ).value.id  # fmt: skip
        await self.memory.merge(
            GUILD, self.campaign.id, other, self.party, source="dm", dm_said_same=True
        )
        rows = await self.memory.relations(GUILD, self.campaign.id, include_secret=True)
        self.assertIn((self.owl, other), {(r.subject_id, r.object_id) for r in rows
                                          if r.predicate == TRAVELS})  # fmt: skip

    async def test_a_merge_cannot_leave_a_companion_with_an_invalid_target(self) -> None:
        await self.go(self.owl, self.party, "dm")
        town = (
            await self.memory.add_entity(
                GUILD, self.campaign.id, name="Bryn Shander", type="place",
                status=CONFIRMED, source="dm",
            )
        ).value.id  # fmt: skip
        with self.assertRaises(MemoryRuleError):  # (a place is never the party)
            await self.memory.merge(
                GUILD, self.campaign.id, town, self.party, source="dm", dm_said_same=True
            )

    async def test_a_player_character_with_a_companion_cannot_become_an_npc(self) -> None:
        await self.go(self.owl, self.pc, "dm")
        with self.assertRaises(MemoryRuleError):
            await self.memory.set_entity_type(
                GUILD, self.campaign.id, self.pc, "character", source="dm", role="npc"
            )
