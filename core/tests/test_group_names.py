"""A waiting list of names that is a group the campaign knows (#3383, part 2). Invented names."""

from __future__ import annotations

from dmbot.memory import claims
from dmbot.memory.list_names import split_list
from dmbot.memory.models import (
    CONFIRMED,
    PROPOSED,
    REJECTED,
    MemoryRuleError,
    lookup_key,
)
from tests.test_memory_store import GUILD_A, MemoryTest


class _Base(MemoryTest):
    async def character(self, name: str) -> str:
        return await self.add(name, type="character", status=CONFIRMED)

    async def wolves(self, *members: str) -> str:
        group = await self.add("The Ash Wolves", type="faction", status=CONFIRMED)
        for m in members:
            mid = await self.character(m)
            await self.relate(mid, "member_of", group, status=CONFIRMED)
        return group

    async def waiting(self, name: str) -> str:
        return await self.add(name, type="concept", status=PROPOSED)

    async def names(self, *statuses: str) -> list[str]:
        data = await self.memory.lookup_data(GUILD_A, self.c)
        return sorted(e.name for e in data.entities if e.status in statuses)


class GroupOfNames(_Base):
    async def test_a_list_of_members_is_offered_the_group(self) -> None:
        group = await self.wolves("Tavin", "Mira")
        listed = await self.waiting("Tavin and Mira")
        hints = await self.memory.group_hints(GUILD_A, self.c, listed)
        self.assertEqual([(h.group_id, h.members, h.others) for h in hints],
                         [(group, ("Tavin", "Mira"), ())])  # fmt: skip

    async def test_no_hint_when_no_group_fits_or_it_is_one_name(self) -> None:
        await self.wolves("Tavin")
        await self.character("Mira")
        self.assertEqual(
            await self.memory.group_hints(GUILD_A, self.c, await self.waiting("Mira and Bram")), []
        )
        self.assertEqual(
            await self.memory.group_hints(GUILD_A, self.c, await self.waiting("Ironhold")), []
        )

    async def test_yes_that_group_makes_the_alias_and_no_new_names(self) -> None:
        group = await self.wolves("Tavin", "Mira")
        listed = await self.waiting("Tavin and Mira")
        done = await self.memory.join_group(GUILD_A, self.c, listed, group, source="dm")
        self.assertEqual((done.value.joined, done.value.made), (("Tavin", "Mira"), ()))
        data = await self.memory.lookup_data(GUILD_A, self.c)
        self.assertIn(
            "tavin and mira", {lookup_key(a.text) for a in data.aliases if a.entity_id == group}
        )
        self.assertEqual(await self.names(PROPOSED), [])

    async def test_the_nickname_is_never_split_again(self) -> None:
        group = await self.wolves("Tavin", "Mira")
        await self.memory.join_group(
            GUILD_A, self.c, await self.waiting("Tavin and Mira"), group, source="dm"
        )
        data = await self.memory.lookup_data(GUILD_A, self.c)
        known = {lookup_key(a.text) for a in data.aliases}
        self.assertEqual(split_list("Tavin and Mira", known=known, dm=True), ["Tavin and Mira"])
        self.assertEqual(split_list("Tavin and Mira", known=known), ["Tavin and Mira"])
        names = claims.names_of(
            [{"id": e.id, "name": e.name, "type": e.type} for e in data.entities],
            [
                {"entity_id": a.entity_id, "key": lookup_key(a.text), "secret": a.secret}
                for a in data.aliases
            ],
        )
        claim = claims.Extracted("Tavin and Mira", "is in", "Ironhold", "", ("1",), "dm_said")
        out, lists = claims.expand_lists([claim], names)
        self.assertEqual((out, lists), ([claim], 0))

    async def test_a_new_part_takes_its_membership_when_its_name_is_confirmed(self) -> None:
        group = await self.wolves("Tavin")
        listed = await self.waiting("Tavin and Bram")
        done = await self.memory.join_group(GUILD_A, self.c, listed, group, source="dm")
        self.assertEqual((done.value.joined, done.value.made), (("Tavin",), ("Bram",)))
        self.assertEqual(await self.names(PROPOSED), ["Bram"])
        data = await self.memory.lookup_data(GUILD_A, self.c)
        bram = next(e for e in data.entities if e.name == "Bram")
        waiting = await self.memory.relations(GUILD_A, self.c, entity_id=bram.id)
        # held as a proposal while the name waits: never a fact for a name the DM may refuse
        self.assertEqual([(r.predicate, r.status) for r in waiting], [("member_of", PROPOSED)])
        await self.memory.set_entity_status(GUILD_A, self.c, bram.id, CONFIRMED, source="dm")
        ties = await self.memory.relations(GUILD_A, self.c, entity_id=bram.id)
        self.assertEqual([(r.predicate, r.object_id, r.status) for r in ties],
                         [("member_of", group, CONFIRMED)])  # fmt: skip

    async def test_a_turned_down_part_takes_its_membership_with_it(self) -> None:
        group = await self.wolves("Tavin")
        await self.memory.join_group(
            GUILD_A, self.c, await self.waiting("Tavin and Bram"), group, source="dm"
        )
        data = await self.memory.lookup_data(GUILD_A, self.c)
        bram = next(e for e in data.entities if e.name == "Bram")
        await self.memory.set_entity_status(GUILD_A, self.c, bram.id, REJECTED, source="dm")
        self.assertEqual(await self.memory.relations(GUILD_A, self.c, entity_id=bram.id), [])

    async def test_a_waiting_claim_about_the_list_now_means_the_group(self) -> None:
        group = await self.wolves("Tavin", "Mira")
        listed = await self.waiting("Tavin and Mira")
        await self.memory.join_group(GUILD_A, self.c, listed, group, source="dm")
        data = await self.memory.lookup_data(GUILD_A, self.c)
        names = claims.names_of(
            [{"id": e.id, "name": e.name, "type": e.type} for e in data.entities],
            [
                {"entity_id": a.entity_id, "key": lookup_key(a.text), "secret": a.secret}
                for a in data.aliases
            ],
        )
        self.assertEqual(names.one("Tavin and Mira"), (group, "faction"))

    async def test_they_are_a_group_makes_the_group_the_members_and_the_alias(self) -> None:
        listed = await self.waiting("Tavin and Mira")
        done = await self.memory.make_group(GUILD_A, self.c, listed, "The Twin Blades", source="dm")
        self.assertEqual(done.value.group_name, "The Twin Blades")
        self.assertEqual(set(done.value.made), {"Tavin", "Mira"})
        data = await self.memory.lookup_data(GUILD_A, self.c)
        group = next(e for e in data.entities if e.name == "The Twin Blades")
        self.assertEqual((group.type, group.status), ("faction", CONFIRMED))
        self.assertEqual(len(await self.memory.relations(GUILD_A, self.c, entity_id=group.id)), 2)
        self.assertIn(
            "tavin and mira", {lookup_key(a.text) for a in data.aliases if a.entity_id == group.id}
        )

    async def test_undo_restores_the_question(self) -> None:
        group = await self.wolves("Tavin")
        listed = await self.waiting("Tavin and Bram")
        before = await self.snapshot()
        done = await self.memory.join_group(GUILD_A, self.c, listed, group, source="dm")
        assert done.batch is not None
        await self.memory.undo(GUILD_A, self.c, done.batch)
        self.assertEqual(await self.snapshot(), before)

    async def test_the_best_fit_comes_first_and_secret_or_hidden_groups_give_no_hint(self) -> None:
        wolves = await self.wolves("Tavin", "Mira")
        pack = await self.add("The Pack", type="faction", status=CONFIRMED)
        data = await self.memory.lookup_data(GUILD_A, self.c)
        tavin = next(e.id for e in data.entities if e.name == "Tavin")
        await self.relate(tavin, "member_of", pack, status=CONFIRMED)
        hints = await self.memory.group_hints(GUILD_A, self.c, await self.waiting("Tavin and Mira"))
        self.assertEqual([h.group_id for h in hints], [wolves, pack])  # all of them, then one
        await self.memory.set_hidden_from_players(GUILD_A, self.c, wolves, True, source="dm")
        hints = await self.memory.group_hints(GUILD_A, self.c, await self.waiting("Mira and Tavin"))
        self.assertEqual([h.group_id for h in hints], [pack])

    async def test_a_known_entry_that_is_not_a_character_is_skipped_and_said_so(self) -> None:
        group = await self.wolves("Tavin")
        await self.add("Aldmoor", type="place", status=CONFIRMED)
        done = await self.memory.join_group(
            GUILD_A, self.c, await self.waiting("Tavin and Aldmoor"), group, source="dm"
        )
        self.assertEqual((done.value.joined, done.value.skipped), (("Tavin",), ("Aldmoor",)))

    async def test_only_a_group_of_people_is_a_group_and_a_list_of_places_is_not(self) -> None:
        group = await self.wolves("Tavin")
        not_a_group = await self.add("Aldmoor", type="place", status=CONFIRMED)
        listed = await self.waiting("Tavin and Bram")
        with self.assertRaises(MemoryRuleError):
            await self.memory.join_group(GUILD_A, self.c, listed, not_a_group, source="dm")
        places = await self.add("Aldmoor, Quillgate", type="place", status=PROPOSED)
        with self.assertRaises(MemoryRuleError):
            await self.memory.join_group(GUILD_A, self.c, places, group, source="dm")

    async def test_refusals_change_nothing(self) -> None:
        group = await self.wolves("Tavin")
        single = await self.waiting("Ironhold")
        pair = await self.waiting("Mira and Bram")
        taken = await self.waiting("Bram and Mira")
        await self.add("The Pair", type="place", status=CONFIRMED)
        before = await self.snapshot()
        with self.assertRaises(MemoryRuleError):  # one name, not a list
            await self.memory.join_group(GUILD_A, self.c, single, group, source="dm")
        with self.assertRaises(MemoryRuleError):  # only the DM
            await self.memory.join_group(GUILD_A, self.c, pair, group, source="extractor")
        with self.assertRaises(MemoryRuleError):  # a group name another entry already holds
            await self.memory.make_group(GUILD_A, self.c, taken, "The Pair", source="dm")
        self.assertEqual(await self.snapshot(), before)


class ReadersLeadWithAGroup(_Base):
    async def test_a_known_group_followed_by_its_members_proposes_membership(self) -> None:
        group = await self.wolves("Tavin")
        data = await self.memory.lookup_data(GUILD_A, self.c)
        names = claims.names_of(
            [{"id": e.id, "name": e.name, "type": e.type} for e in data.entities],
            [
                {"entity_id": a.entity_id, "key": lookup_key(a.text), "secret": a.secret}
                for a in data.aliases
            ],
        )
        claim = claims.Extracted(
            "The Ash Wolves, Tavin and Mira", "fight", "Ironhold", "", ("1",), "dm_said"
        )
        out, lists = claims.expand_lists([claim], names)
        self.assertEqual(lists, 1)
        self.assertEqual(
            [(c.subject, c.relationship, c.object) for c in out],
            [("Tavin", "member of", "The Ash Wolves"), ("Mira", "member of", "The Ash Wolves")],
        )
        self.assertTrue(group)

    async def test_a_list_not_led_by_a_group_stays_a_list(self) -> None:
        await self.wolves("Tavin")
        names = claims.names_of([], [])
        claim = claims.Extracted("Tavin, Mira", "fight", "Ironhold", "", ("1",), "dm_said")
        out, _ = claims.expand_lists([claim], names)
        self.assertEqual([c.subject for c in out], ["Tavin", "Mira"])
