"""The session reader offers "travels with" (#3176): a claim that a companion goes along with a
player character or "the party" becomes the typed connection, and nothing else does. Invented
names; real Postgres."""

from __future__ import annotations

from dmbot.memory import extractor
from dmbot.memory.claims import ClaimStore
from dmbot.memory.models import CONFIRMED
from tests.test_memory_claims import said
from tests.test_memory_names import GUILD, NamesTest


class FromTheSession(NamesTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.claims = ClaimStore(self.memory)
        c = self.campaign.id
        made = await self.memory.add_entity(
            GUILD, c, name="Cerric", type="character", role="player_character",
            played_by=101, status=CONFIRMED, source="dm",
        )  # fmt: skip
        self.pc = made.value.id
        for name in ("Glyph", "Old Marn"):
            await self.memory.add_entity(
                GUILD, c, name=name, type="character", role="npc", status=CONFIRMED, source="dm"
            )
        await self.memory.link_party(GUILD, c, {101})

    async def kinds(self) -> list[tuple[str, str, str]]:
        names = {e.id: e.name for e in await self.memory.entities(GUILD, self.campaign.id)}
        rels = await self.memory.relations(GUILD, self.campaign.id, include_secret=True)
        return sorted((names[r.subject_id], r.predicate, names[r.object_id]) for r in rels)

    async def propose(self, *claims: object) -> None:
        await self.claims.propose(
            GUILD, self.campaign.id, list(claims), {"1": 7},  # type: ignore[arg-type]
            opted_in={7}, prompt_version=extractor.PROMPT_VERSION, model="m",
            session_started_at=500,
        )  # fmt: skip

    async def keep_all(self) -> None:
        """The DM says yes to every waiting question."""
        for claim in await self.claims.waiting(GUILD, self.campaign.id, opted_in={7}):
            await self.claims.confirm(GUILD, self.campaign.id, claim.id, opted_in={7})

    async def test_a_companion_with_a_player_character_is_offered_then_kept(self) -> None:
        await self.propose(said("Glyph", "travels with", "Cerric"))
        waiting = await self.claims.waiting(GUILD, self.campaign.id, opted_in={7})
        self.assertEqual([(c.subject, c.object) for c in waiting], [("Glyph", "Cerric")])
        self.assertNotIn(("Glyph", "travels_with", "Cerric"), await self.kinds())  # (not yet)
        await self.keep_all()
        self.assertIn(("Glyph", "travels_with", "Cerric"), await self.kinds())

    async def test_a_companion_with_the_party_is_offered_then_kept(self) -> None:
        await self.propose(said("Old Marn", "travels with", "the party"))
        await self.keep_all()
        self.assertIn(("Old Marn", "travels_with", "The party"), await self.kinds())

    async def waiting(self) -> list[tuple[str, str]]:
        found = await self.claims.waiting(GUILD, self.campaign.id, opted_in={7})
        return [(c.subject, c.object) for c in found]

    async def test_two_non_player_characters_are_never_asked(self) -> None:
        await self.propose(said("Glyph", "travels with", "Old Marn"))
        self.assertEqual(await self.waiting(), [])
        self.assertEqual([r for r in await self.kinds() if r[1] == "travels_with"], [])

    async def test_a_player_character_first_is_not_asked(self) -> None:
        await self.propose(said("Cerric", "travels with", "Glyph"))
        self.assertEqual(await self.waiting(), [])

    async def test_a_place_as_the_object_is_not_asked(self) -> None:
        await self.memory.add_entity(
            GUILD, self.campaign.id, name="Termalaine", type="place", status=CONFIRMED, source="dm"
        )
        await self.propose(said("Glyph", "travels with", "Termalaine"))
        self.assertEqual(await self.waiting(), [])

    async def test_the_order_said_from_the_other_end_is_still_asked(self) -> None:
        await self.propose(said("Cerric", "is joined by", "Glyph"))
        self.assertEqual(await self.waiting(), [("Cerric", "Glyph")])

    async def test_the_prompt_asks_for_it_and_its_version_moved(self) -> None:
        self.assertIn('"travels with"', extractor.SYSTEM)
        self.assertNotEqual(extractor.PROMPT_VERSION, "extractor-1")
