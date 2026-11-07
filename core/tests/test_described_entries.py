"""An entry named by a description ("the woman") is offered to the DM to become an unnamed person
(#3431). Real Postgres; invented names."""

from __future__ import annotations

from dmbot.memory import claims
from dmbot.memory.claims import Extracted
from dmbot.memory.models import CLAIM_CONFIRMED, CONFIRMED, MemoryRuleError
from tests.test_memory_store import GUILD_A, MemoryTest


def said(subject: str, relationship: str, obj: str) -> Extracted:
    return Extracted(subject, relationship, obj, "", ("1",), "dm_said")


class TheDescribedEntries(MemoryTest):
    async def made(self, name: str, **kw: object) -> str:
        kw.setdefault("source", "extractor")
        kw.setdefault("type", "npc")
        return await self.add(name, **kw)  # type: ignore[arg-type]

    async def test_only_a_description_made_by_dmbot_is_offered(self) -> None:
        woman = await self.made("the woman")
        await self.made("Mira Vane")
        await self.made("the ferryman", source="dm")
        found = await self.memory.described_entries(GUILD_A, self.c)
        self.assertEqual([e.id for e in found], [woman])

    async def test_an_entry_with_a_real_other_name_is_not_offered(self) -> None:
        woman = await self.made("the woman")
        await self.memory.add_alias(
            GUILD_A, self.c, woman, "Ysolde", kind="full", source="dm", status=CONFIRMED
        )
        self.assertEqual(await self.memory.described_entries(GUILD_A, self.c), [])

    async def test_the_dm_makes_it_an_unnamed_person_and_undo_takes_it_back(self) -> None:
        woman = await self.made("the woman")
        done = await self.memory.make_unnamed(GUILD_A, self.c, woman, source="dm")
        self.assertTrue(done.value.unnamed)
        self.assertEqual(done.value.unnamed_what, "the woman")
        self.assertEqual(done.value.name, "unnamed - the woman")
        self.assertEqual(await self.memory.described_entries(GUILD_A, self.c), [])
        listed = await self.memory.unnamed(GUILD_A, self.c)
        self.assertEqual([e.id for e, _ in listed], [woman])
        assert done.batch is not None
        await self.memory.undo(GUILD_A, self.c, done.batch, source="dm")
        again = await self.memory.described_entries(GUILD_A, self.c)
        self.assertEqual([e.id for e in again], [woman])

    async def test_a_named_entry_or_a_non_dm_source_is_refused(self) -> None:
        mira = await self.made("Mira Vane")
        woman = await self.made("the woman")
        with self.assertRaises(MemoryRuleError):
            await self.memory.make_unnamed(GUILD_A, self.c, mira, source="dm")
        with self.assertRaises(MemoryRuleError):
            await self.memory.make_unnamed(GUILD_A, self.c, woman, source="extractor")


class TheMainNameBlock(MemoryTest):
    async def test_an_old_entry_named_by_a_described_person_places_nothing(self) -> None:
        woman = await self.add("the woman", source="extractor")
        await self.add("the cave", type="place", source="extractor")
        mira = await self.add("Mira Vane")
        data = await self.memory.lookup_data(GUILD_A, self.c)
        entities = [
            {"id": e.id, "name": e.name, "type": e.type, "source": e.source} for e in data.entities
        ]
        names = claims.names_of(entities, [])
        self.assertEqual(names.one("the woman"), claims.UNKNOWN_NAME_REASON)
        self.assertEqual(names.one("Mira Vane")[0], mira)
        self.assertEqual(names.one("the cave")[1], "place")  # (only described people are held)
        self.assertNotIn(woman, [i for found in names.by_key.values() for i, _ in found])

    async def test_one_the_dm_typed_still_places(self) -> None:
        woman = await self.add("the ferrywoman", source="dm")
        data = await self.memory.lookup_data(GUILD_A, self.c)
        entities = [
            {"id": e.id, "name": e.name, "type": e.type, "source": e.source} for e in data.entities
        ]
        self.assertEqual(claims.names_of(entities, []).one("the ferrywoman")[0], woman)


class ThePersonWithoutAName(MemoryTest):
    """A claim about "the woman" waits for the DM and never makes a name to check (#3431)."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        from dmbot.memory.claims import ClaimStore

        self.claims = ClaimStore(self.memory)
        self.bram = await self.add("Bram", type="character", status=CONFIRMED)

    async def propose(self, *found: Extracted) -> claims.Proposed:
        return await self.claims.propose(
            GUILD_A, self.c, list(found), {"1": 7}, opted_in={7},
            prompt_version="p1", model="m1", session_started_at=500,
        )  # fmt: skip

    async def waiting(self) -> list[claims.Claim]:
        return await self.claims.waiting(GUILD_A, self.c, opted_in={7})

    async def test_a_described_person_waits_and_is_not_made_a_name(self) -> None:
        got = await self.propose(said("Bram", "knows", "the woman"))
        self.assertEqual((got.waiting, got.described, got.named, got.new), (1, 1, 0, ()))
        (claim,) = await self.waiting()
        self.assertEqual(claim.reason, claims.DESCRIBED_REASON)
        self.assertEqual(await self.memory.described_entries(GUILD_A, self.c), [])

    async def test_a_described_person_at_both_ends_is_not_kept(self) -> None:
        got = await self.propose(said("the beggar woman", "knows", "the old man"))
        counts = (got.waiting, got.described, got.described_dropped, got.named)
        self.assertEqual(counts, (0, 0, 1, 0))

    async def test_a_described_thing_is_not_a_person(self) -> None:
        got = await self.propose(said("Bram", "is in", "the cave"))
        self.assertEqual(got.described, 0)

    async def test_keeping_it_makes_the_unnamed_person_and_writes_the_fact(self) -> None:
        await self.propose(said("Bram", "knows", "the woman"))
        (claim,) = await self.waiting()
        done = await self.claims.confirm(GUILD_A, self.c, claim.id, opted_in={7})
        self.assertTrue(done.batch is not None or done.claim.fact_id)
        people = await self.memory.unnamed(GUILD_A, self.c)
        self.assertEqual([label for _, label in people], ["unnamed - the woman"])
        (entity, _label) = people[0]
        self.assertEqual(entity.status, CONFIRMED)
        self.assertEqual(done.claim.status, CLAIM_CONFIRMED)

    async def test_a_race_or_a_job_is_not_a_described_person(self) -> None:
        got = await self.propose(
            said("Bram", "is", "a dwarf"),
            said("Bram", "is", "a wizard"),
            said("Bram", "is", "a guard"),
        )
        self.assertEqual(got.described, 0)
        self.assertEqual(await self.memory.unnamed(GUILD_A, self.c), [])

    async def test_a_fact_that_fails_leaves_no_person_behind(self) -> None:
        await self.propose(said("Bram", "knows", "the woman"))
        (claim,) = await self.waiting()

        async def refuse(_w: object) -> None:  # (runs inside the same write as the fact)
            raise MemoryRuleError("refused")

        with self.assertRaises(MemoryRuleError):
            await self.claims.confirm(GUILD_A, self.c, claim.id, opted_in={7}, before=refuse)
        self.assertEqual(await self.memory.unnamed(GUILD_A, self.c), [])
        self.assertEqual(len(await self.waiting()), 1)

    async def keep_all(self) -> None:
        for claim in await self.waiting():
            await self.claims.confirm(GUILD_A, self.c, claim.id, opted_in={7})

    async def test_the_woman_twice_in_one_session_is_one_woman(self) -> None:
        await self.add("Mira Vane", type="character", status=CONFIRMED)
        await self.propose(said("Bram", "knows", "the woman"))
        await self.propose(said("Mira Vane", "knows", "the woman"))
        self.assertEqual(len(await self.waiting()), 2)
        await self.keep_all()
        self.assertEqual(len(await self.memory.unnamed(GUILD_A, self.c)), 1)

    async def test_the_woman_in_two_sessions_is_two_women(self) -> None:
        await self.propose(said("Bram", "knows", "the woman"))
        await self.keep_all()
        await self.claims.propose(
            GUILD_A, self.c, [said("Bram", "talks to", "the woman")], {"1": 7}, opted_in={7},
            prompt_version="p1", model="m1", session_started_at=900,
        )  # fmt: skip
        await self.keep_all()
        self.assertEqual(len(await self.memory.unnamed(GUILD_A, self.c)), 2)

    async def test_a_claim_whose_person_was_named_since_is_written_the_ordinary_way(self) -> None:
        await self.propose(said("Bram", "knows", "the woman"))
        await self.add("the woman", source="dm", type="character", status=CONFIRMED)
        (claim,) = await self.waiting()
        done = await self.claims.confirm(GUILD_A, self.c, claim.id, opted_in={7})
        self.assertEqual(done.claim.status, CLAIM_CONFIRMED)
        self.assertEqual(await self.memory.unnamed(GUILD_A, self.c), [])
