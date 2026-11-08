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

    async def propose(self, *found: Extracted, **kw: object) -> claims.Proposed:
        return await self.claims.propose(
            GUILD_A, self.c, list(found), {"1": 7}, opted_in={7},
            prompt_version="p1", model="m1", session_started_at=500, **kw,  # type: ignore[arg-type]
        )  # fmt: skip

    async def waiting(self) -> list[claims.Claim]:
        return await self.claims.waiting(GUILD_A, self.c, opted_in={7})

    async def test_a_described_person_waits_and_is_not_made_a_name(self) -> None:
        got = await self.propose(said("Bram", "knows", "the woman"))
        self.assertEqual((got.waiting, got.described, got.named, got.new), (1, 1, 0, ()))
        (claim,) = await self.waiting()
        self.assertEqual(claim.reason, claims.DESCRIBED_REASON)
        self.assertEqual(await self.memory.described_entries(GUILD_A, self.c), [])

    async def test_a_chain_of_two_unnamed_people_is_kept_and_keep_makes_both(self) -> None:
        """Nothing is dropped early (owner, #3736): the two may be tied to the story later."""
        got = await self.propose(said("the beggar woman", "knows", "the old man"))
        counts = (got.waiting, got.described, got.described_dropped, got.named)
        self.assertEqual(counts, (1, 1, 0, 0))
        (claim,) = await self.waiting()
        self.assertEqual(claim.reason, claims.DESCRIBED_REASON)
        done = await self.claims.confirm(GUILD_A, self.c, claim.id, opted_in={7})
        people = {label: e.id for e, label in await self.memory.unnamed(GUILD_A, self.c)}
        self.assertEqual(sorted(people), ["unnamed - the beggar woman", "unnamed - the old man"])
        (fact,) = [
            r for r in await self.memory.relations(GUILD_A, self.c) if r.id == done.claim.fact_id
        ]
        self.assertEqual(
            {fact.subject_id, fact.object_id},
            {people["unnamed - the beggar woman"], people["unnamed - the old man"]},
        )

    async def test_a_fact_about_one_alone_is_kept_and_keep_makes_them(self) -> None:
        got = await self.propose(said("the beggar woman", "is", "dead"))
        self.assertEqual((got.waiting, got.described), (1, 1))
        (claim,) = await self.waiting()
        await self.claims.confirm(GUILD_A, self.c, claim.id, opted_in={7})
        (person, label) = (await self.memory.unnamed(GUILD_A, self.c))[0]
        self.assertEqual((label, person.status), ("unnamed - the beggar woman", CONFIRMED))
        state = (await self.memory.states(GUILD_A, self.c, [person.id]))[person.id]
        self.assertEqual(state.condition, "dead")  # (the fact is written on her)

    async def test_one_woman_in_one_session_after_a_fact_about_her(self) -> None:
        await self.propose(said("the beggar woman", "is", "dead"))
        (first,) = await self.waiting()
        await self.claims.confirm(GUILD_A, self.c, first.id, opted_in={7})
        await self.propose(said("Bram", "knows", "the beggar woman"))
        (second,) = await self.waiting()
        await self.claims.confirm(GUILD_A, self.c, second.id, opted_in={7})
        self.assertEqual(len(await self.memory.unnamed(GUILD_A, self.c)), 1)

    async def test_the_same_words_at_both_ends_is_not_kept(self) -> None:
        for subject in ("the woman", "The woman"):
            with self.subTest(subject):
                got = await self.propose(said(subject, "knows", "the woman"))
                self.assertEqual((got.waiting, got.described), (0, 0))
        self.assertEqual(await self.waiting(), [])

    async def test_one_beside_a_new_name_waits_on_the_name_then_keep_makes_the_person(
        self,
    ) -> None:
        got = await self.propose(said("Corwin Hale", "knows", "the old man"))
        self.assertEqual(got.new, ("Corwin Hale",))  # (the name goes to the names to check)
        self.assertEqual(got.described, 1)  # (kept now, for when the name is answered)
        self.assertEqual(await self.memory.unnamed(GUILD_A, self.c), [])  # (no one made yet)
        self.assertEqual(await self.waiting(), [])  # (not asked while Corwin is only proposed)
        (corwin,) = [
            e.id
            for e in (await self.memory.lookup_data(GUILD_A, self.c)).entities
            if e.name == "Corwin Hale"
        ]
        await self.memory.set_entity_status(GUILD_A, self.c, corwin, CONFIRMED, source="dm")
        (claim,) = await self.waiting()  # (asked once the name is answered)
        self.assertEqual(claim.reason, claims.DESCRIBED_REASON)
        await self.claims.confirm(GUILD_A, self.c, claim.id, opted_in={7})
        self.assertEqual(
            [label for _, label in await self.memory.unnamed(GUILD_A, self.c)],
            ["unnamed - the old man"],
        )

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

    async def test_held_for_its_name_past_the_limit_then_asked(self) -> None:
        """Past the new-name limit the claim waits for its name; the described end is never made
        into a name to check meanwhile."""
        got = await self.propose(said("Corwin Hale", "knows", "the old man"), new_names=0)
        self.assertEqual((got.new, got.described), ((), 0))
        await self.propose(new_names=5)  # (a later read with room: the name is proposed now)
        names = [e.name for e in (await self.memory.lookup_data(GUILD_A, self.c)).entities]
        self.assertIn("Corwin Hale", names)
        self.assertNotIn("the old man", names)
        (corwin,) = [
            e.id
            for e in (await self.memory.lookup_data(GUILD_A, self.c)).entities
            if e.name == "Corwin Hale"
        ]
        await self.memory.set_entity_status(GUILD_A, self.c, corwin, CONFIRMED, source="dm")
        await self.propose(new_names=5)  # (the read after the answer asks it)
        (claim,) = await self.waiting()
        self.assertEqual(claim.reason, claims.DESCRIBED_REASON)

    async def test_a_chain_after_the_dm_named_one_end(self) -> None:
        await self.propose(said("the beggar woman", "knows", "the old man"))
        (claim,) = await self.waiting()
        hesta = await self.add("the beggar woman", type="character", status=CONFIRMED)
        done = await self.claims.confirm(GUILD_A, self.c, claim.id, opted_in={7})
        people = await self.memory.unnamed(GUILD_A, self.c)
        self.assertEqual([label for _, label in people], ["unnamed - the old man"])
        (fact,) = [
            r for r in await self.memory.relations(GUILD_A, self.c) if r.id == done.claim.fact_id
        ]
        self.assertEqual({fact.subject_id, fact.object_id}, {hesta, people[0][0].id})
