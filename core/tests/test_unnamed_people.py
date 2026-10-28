"""People the story describes but never names (#2790): the label built from its parts, finding the
same person again, every matching path skipping them, the DM giving a name, merges, and a backup
round trip. Invented names."""

from __future__ import annotations

import unittest

from dmbot.campaigns import CampaignError
from dmbot.campaigns.store import decode_backup, encode_backup
from dmbot.memory import unnamed
from dmbot.memory.models import CONFIRMED, MERGED, PROPOSED, MemoryRuleError, name_key
from dmbot.memory.sounds import sound_codes
from tests.test_memory_store import GUILD_A, GUILD_B, MemoryTest

DM = 7


class TheLabel(unittest.TestCase):
    def test_a_label_hangs_off_its_anchor_or_stands_alone(self) -> None:
        self.assertEqual(unnamed.label("sister", "Ken"), "Ken's sister (name unknown)")
        self.assertEqual(unnamed.label("king", "Kesh Arn"), "Kesh Arn's king (name unknown)")
        self.assertEqual(unnamed.label("a hooded figure"), "a hooded figure (name unknown)")
        self.assertEqual(unnamed.label("the merchant"), "the merchant (name unknown)")
        self.assertEqual(
            unnamed.label("elf archer with long blue hair"),
            "elf archer with long blue hair (name unknown)",
        )

    def test_a_name_ending_in_s_takes_only_an_apostrophe_and_an_article_is_dropped(self) -> None:
        self.assertEqual(unnamed.label("the king", "Hollis"), "Hollis' king (name unknown)")

    def test_the_label_follows_the_anchors_present_name(self) -> None:
        self.assertEqual(
            unnamed.shown("sister", "k1", {"k1": "Kenneth"}), "Kenneth's sister (name unknown)"
        )
        self.assertEqual(unnamed.shown("sister", "gone", {}), "sister (name unknown)")

    def test_the_same_person_is_the_same_relationship_and_word_other_is_another(self) -> None:
        key = unnamed.same_person_key
        self.assertEqual(key("sister", "sibling"), key("  The Sister ", "sibling"))
        self.assertNotEqual(key("sister", "sibling"), key("other sister", "sibling"))
        self.assertNotEqual(key("king", "liege"), key("king", None))


class TheStoredCopy(unittest.TestCase):
    def test_the_stored_label_fits_and_still_ends_in_the_suffix(self) -> None:
        long_anchor = "A" * 100
        for what in ("sister", "w" * 100):
            got = unnamed.stored(what, long_anchor)
            with self.subTest(what=what[:5]):
                self.assertLessEqual(len(got), 100)
                self.assertTrue(got.endswith(unnamed.SUFFIX))
        self.assertEqual(unnamed.stored("sister", "Ken"), "Ken's sister (name unknown)")


class MakingThem(MemoryTest):
    async def ken(self) -> str:
        return await self.add("Ken", status=CONFIRMED)

    async def test_a_sister_is_a_proposed_character_with_no_name_to_match(self) -> None:
        ken = await self.ken()
        made = await self.memory.add_unnamed(
            GUILD_A,
            self.c,
            what="sister",
            anchor_id=ken,
            role="sibling",
            details="lives by the lake",
        )
        e = made.entity
        self.assertTrue(made.created)
        self.assertEqual(
            (e.type, e.role, e.status, e.source, e.unnamed, e.unnamed_anchor, e.unnamed_role),
            ("character", "npc", PROPOSED, "extractor", True, ken, "sibling"),
        )
        self.assertEqual(e.unnamed_what, "sister")
        self.assertEqual(e.description, "lives by the lake")
        self.assertEqual(e.name, "Ken's sister (name unknown)")
        self.assertEqual(await self.memory.aliases(GUILD_A, self.c, entity_id=e.id), [])

    async def test_a_second_kens_sister_is_the_first_but_his_other_sister_is_another(self) -> None:
        ken = await self.ken()
        first = await self.memory.add_unnamed(
            GUILD_A, self.c, what="sister", anchor_id=ken, role="sibling"
        )
        again = await self.memory.add_unnamed(
            GUILD_A, self.c, what="The Sister", anchor_id=ken, role="sibling"
        )
        other = await self.memory.add_unnamed(
            GUILD_A, self.c, what="other sister", anchor_id=ken, role="sibling"
        )
        self.assertEqual((again.entity.id, again.created), (first.entity.id, False))
        self.assertNotEqual(other.entity.id, first.entity.id)
        mira = await self.add("Mira", status=CONFIRMED)
        hers = await self.memory.add_unnamed(
            GUILD_A, self.c, what="sister", anchor_id=mira, role="sibling"
        )
        self.assertNotEqual(hers.entity.id, first.entity.id)  # another anchor, another person

    async def test_with_no_anchor_only_the_same_read_joins_and_other_reads_are_offered(
        self,
    ) -> None:
        read: dict[str, str] = {}
        a = await self.memory.add_unnamed(GUILD_A, self.c, what="a hooded figure", read=read)
        b = await self.memory.add_unnamed(GUILD_A, self.c, what="a hooded figure", read=read)
        self.assertEqual((b.entity.id, b.created), (a.entity.id, False))
        later = await self.memory.add_unnamed(GUILD_A, self.c, what="a hooded figure", read={})
        self.assertTrue(later.created)
        self.assertEqual(later.similar, (a.entity.id,))  # a question for the DM, never a join
        none = await self.memory.add_unnamed(GUILD_A, self.c, what="a hooded figure")
        self.assertTrue(none.created)  # no record of a read: never joined on its own

    async def test_a_label_that_is_not_a_person_or_a_missing_anchor_is_refused(self) -> None:
        with self.assertRaises(MemoryRuleError):
            await self.memory.add_unnamed(GUILD_A, self.c, what="   ")
        with self.assertRaises(MemoryRuleError):
            await self.memory.add_unnamed(GUILD_A, self.c, what="sister", role="x" * 41)
        with self.assertRaises(MemoryRuleError):
            await self.memory.add_unnamed(GUILD_A, self.c, what="sister", anchor_id="0" * 32)

    async def test_the_label_shown_follows_a_rename_of_the_anchor(self) -> None:
        ken = await self.ken()
        await self.memory.add_unnamed(GUILD_A, self.c, what="sister", anchor_id=ken, role="sibling")
        await self.memory.rename_entity(GUILD_A, self.c, ken, "Kenneth", source="dm")
        [(_, shown)] = await self.memory.unnamed(GUILD_A, self.c)
        self.assertEqual(shown, "Kenneth's sister (name unknown)")

    async def test_the_stored_name_follows_a_rename_and_a_merge_in_the_same_change(self) -> None:
        ken = await self.ken()
        made = await self.memory.add_unnamed(
            GUILD_A, self.c, what="sister", anchor_id=ken, role="sibling"
        )
        renamed = await self.memory.rename_entity(GUILD_A, self.c, ken, "Kenneth", source="dm")
        stored = {e.id: e.name for e in await self.memory.entities(GUILD_A, self.c)}
        self.assertEqual(stored[made.entity.id], "Kenneth's sister (name unknown)")
        assert renamed.batch is not None
        await self.memory.undo(GUILD_A, self.c, renamed.batch, source="dm")  # one undo, both back
        stored = {e.id: e.name for e in await self.memory.entities(GUILD_A, self.c)}
        self.assertEqual(stored[made.entity.id], "Ken's sister (name unknown)")

    async def test_one_unnamed_person_may_hang_off_another(self) -> None:
        ken = await self.ken()
        sis = await self.memory.add_unnamed(GUILD_A, self.c, what="sister", anchor_id=ken)
        kid = await self.memory.add_unnamed(GUILD_A, self.c, what="son", anchor_id=sis.entity.id)
        shown = {e.id: label for e, label in await self.memory.unnamed(GUILD_A, self.c)}
        self.assertEqual(shown[kid.entity.id], "Ken's sister (name unknown)'s son (name unknown)")

    async def test_she_follows_the_anchor_when_two_entries_are_one(self) -> None:
        ken = await self.ken()
        kenneth = await self.add("Kenneth", status=CONFIRMED)
        made = await self.memory.add_unnamed(
            GUILD_A, self.c, what="sister", anchor_id=kenneth, role="sibling"
        )
        await self.memory.merge(GUILD_A, self.c, ken, kenneth, source="dm", dm_said_same=True)
        [(entity, shown)] = await self.memory.unnamed(GUILD_A, self.c)
        self.assertEqual((entity.id, entity.unnamed_anchor), (made.entity.id, ken))
        self.assertEqual(shown, "Ken's sister (name unknown)")


class MoreRules(MemoryTest):
    async def pair(self) -> tuple[str, str]:
        ken = await self.add("Ken", status=CONFIRMED)
        made = await self.memory.add_unnamed(
            GUILD_A, self.c, what="sister", anchor_id=ken, role="sibling"
        )
        return ken, made.entity.id

    async def test_an_unnamed_person_is_not_retyped_or_bulk_confirmed(self) -> None:
        _ken, sis = await self.pair()
        with self.assertRaises(MemoryRuleError):
            await self.memory.set_entity_type(GUILD_A, self.c, sis, "place", source="dm")
        done = await self.memory.confirm_kinds(GUILD_A, self.c, [sis], "place", source="dm")
        self.assertEqual(done.value, 0)
        [(e, _)] = await self.memory.unnamed(GUILD_A, self.c)
        self.assertEqual((e.type, e.status), ("character", PROPOSED))

    async def test_undo_of_a_merge_puts_the_anchor_and_the_label_back(self) -> None:
        ken, _sis = await self.pair()
        kenneth = await self.add("Kenneth", status=CONFIRMED)
        other = await self.memory.add_unnamed(GUILD_A, self.c, what="aunt", anchor_id=kenneth)
        merged = await self.memory.merge(
            GUILD_A, self.c, ken, kenneth, source="dm", dm_said_same=True
        )
        rows = {e.id: e for e in await self.memory.entities(GUILD_A, self.c)}
        self.assertEqual(
            (rows[other.entity.id].unnamed_anchor, rows[other.entity.id].name),
            (ken, "Ken's aunt (name unknown)"),
        )
        assert merged.batch is not None
        await self.memory.undo(GUILD_A, self.c, merged.batch, source="dm")
        rows = {e.id: e for e in await self.memory.entities(GUILD_A, self.c)}
        self.assertEqual(
            (rows[other.entity.id].unnamed_anchor, rows[other.entity.id].name),
            (kenneth, "Kenneth's aunt (name unknown)"),
        )

    async def test_undo_of_giving_a_name_makes_them_unnamed_again_with_no_alias(self) -> None:
        _ken, sis = await self.pair()
        done = await self.memory.rename_entity(GUILD_A, self.c, sis, "Mira", source="dm")
        assert done.batch is not None
        await self.memory.undo(GUILD_A, self.c, done.batch, source="dm")
        [(e, shown)] = await self.memory.unnamed(GUILD_A, self.c)
        self.assertEqual(
            (e.id, e.unnamed_what, shown), (sis, "sister", "Ken's sister (name unknown)")
        )
        self.assertEqual(await self.memory.aliases(GUILD_A, self.c, entity_id=sis), [])

    async def test_merge_many_keeps_the_named_one_even_when_the_first_pick_was_unnamed(
        self,
    ) -> None:
        _ken, sis = await self.pair()
        mira = await self.add("Mira", status=CONFIRMED)
        lia = await self.add("Lia", status=CONFIRMED)
        done = await self.memory.merge_many(GUILD_A, self.c, sis, [mira, lia], source="dm")
        self.assertFalse(done.value.unnamed)
        self.assertIn(done.value.name, {"Mira", "Lia"})

    async def test_an_unnamed_entry_merged_with_what_it_hangs_off_keeps_a_valid_anchor(
        self,
    ) -> None:
        ken, sis = await self.pair()
        done = await self.memory.merge(GUILD_A, self.c, sis, ken, source="dm", dm_said_same=True)
        self.assertEqual(done.value.id, ken)  # the named one is kept


class EveryMatchingPathSkipsThem(MemoryTest):
    async def make(self) -> str:
        ken = await self.add("Ken", status=CONFIRMED)
        made = await self.memory.add_unnamed(
            GUILD_A, self.c, what="sister", anchor_id=ken, role="sibling"
        )
        # even a confirmed one (the DM may confirm it before naming) is not a name to match
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "UPDATE memory_entities SET status = 'confirmed' WHERE id = %s", (made.entity.id,)
            )
        return made.entity.id

    async def test_no_alias_no_sound_no_confirmed_name_no_lookup_name(self) -> None:
        eid = await self.make()
        label = "Ken's sister (name unknown)"
        self.assertEqual(
            [a for a in await self.memory.aliases(GUILD_A, self.c) if a.entity_id == eid], []
        )
        self.assertIsNone(await self.memory.confirmed_name_for(GUILD_A, self.c, name_key(label)))
        self.assertEqual(
            await self.memory.sound_alikes(GUILD_A, self.c, list(sound_codes("sister"))), []
        )
        data = await self.memory.lookup_data(GUILD_A, self.c)
        self.assertNotIn(eid, {a.entity_id for a in data.aliases})

    async def test_not_a_look_alike_of_another_name(self) -> None:
        ken = await self.add("Kenn", status=CONFIRMED)
        a = await self.memory.add_unnamed(GUILD_A, self.c, what="sister", anchor_id=ken)
        b = await self.memory.add_unnamed(
            GUILD_A, self.c, what="sister", anchor_id=await self.add("Ken", status=CONFIRMED)
        )
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "UPDATE memory_entities SET status = 'confirmed' WHERE id = ANY(%s)",
                ([a.entity.id, b.entity.id],),
            )
        groups = await self.memory.look_alikes(GUILD_A, self.c)
        ids = {n.id for g in groups for n in g}
        self.assertFalse(ids & {a.entity.id, b.entity.id})

    async def test_nothing_to_say_as_and_no_offer(self) -> None:
        eid = await self.make()
        self.assertIsNone(await self.memory.said_as(GUILD_A, self.c, eid))
        with self.assertRaises(MemoryRuleError):
            await self.memory.set_said_as(GUILD_A, self.c, eid, "Soo-sta", source="dm")
        kept = await self.memory.add_said_as_suggestions(
            GUILD_A, self.c, [(eid, "sistah", "Ken's sister (name unknown)", 3)]
        )
        self.assertEqual(kept, 0)


class GivingThemANameAndMerging(MemoryTest):
    async def setUp2(self) -> tuple[str, str]:
        ken = await self.add("Ken", status=CONFIRMED)
        made = await self.memory.add_unnamed(
            GUILD_A, self.c, what="sister", anchor_id=ken, role="sibling"
        )
        await self.relate(ken, "kin_of", made.entity.id, status=PROPOSED)
        return ken, made.entity.id

    async def test_renaming_names_them_and_keeps_no_old_label(self) -> None:
        _ken, sis = await self.setUp2()
        done = await self.memory.rename_entity(GUILD_A, self.c, sis, "Mira", source="dm")
        e = done.value
        self.assertEqual(
            (e.name, e.unnamed, e.unnamed_anchor, e.unnamed_role, e.unnamed_what),
            ("Mira", False, None, None, None),
        )
        texts = {a.text for a in await self.memory.aliases(GUILD_A, self.c, entity_id=sis)}
        self.assertEqual(texts, {"Mira"})
        self.assertEqual(await self.memory.unnamed(GUILD_A, self.c), [])

    async def test_merging_into_a_named_entry_keeps_the_named_one_either_way_round(self) -> None:
        ken, sis = await self.setUp2()
        mira = await self.add("Mira", status=CONFIRMED)
        done = await self.memory.merge(GUILD_A, self.c, sis, mira, source="dm", dm_said_same=True)
        self.assertEqual(
            (done.value.id, done.value.name, done.value.unnamed), (mira, "Mira", False)
        )
        gone = {e.id: e for e in await self.memory.entities(GUILD_A, self.c, statuses=(MERGED,))}
        self.assertEqual(gone[sis].merged_into, mira)
        edges = await self.memory.relations(GUILD_A, self.c, entity_id=mira)
        self.assertEqual(
            {frozenset((r.subject_id, r.object_id)) for r in edges}, {frozenset((ken, mira))}
        )  # the edge moved

    async def test_two_unnamed_entries_merged_stay_unnamed(self) -> None:
        ken, sis = await self.setUp2()
        twin = await self.memory.add_unnamed(
            GUILD_A, self.c, what="other sister", anchor_id=ken, role="sibling"
        )
        done = await self.memory.merge(
            GUILD_A, self.c, sis, twin.entity.id, source="dm", dm_said_same=True
        )
        self.assertTrue(done.value.unnamed)
        self.assertEqual(done.value.id, sis)


class InABackup(MemoryTest):
    async def test_the_parts_survive_a_round_trip_and_an_old_file_reads_as_named(self) -> None:
        ken = await self.add("Ken", status=CONFIRMED)
        await self.memory.add_unnamed(
            GUILD_A, self.c, what="sister", anchor_id=ken, role="sibling", details="by the lake"
        )
        await self.memory.add_unnamed(GUILD_A, self.c, what="a hooded figure")
        raw = encode_backup(await self.campaigns.export(GUILD_A, self.c))
        restored = await self.campaigns.import_backup(GUILD_B, decode_backup(raw), DM)
        original, copied = await self.snapshot(), await self.snapshot(GUILD_B, restored.id)
        for table in ("memory_mentions",):
            original.pop(table), copied.pop(table)
        self.assertEqual(copied, original)
        rows = [r for r in copied["memory_entities"] if r["unnamed"]]
        self.assertEqual(
            sorted(
                (r["unnamed_what"], r["unnamed_anchor"] is not None, r["unnamed_role"])
                for r in rows
            ),
            [("a hooded figure", False, None), ("sister", True, "sibling")],
        )

    async def test_an_old_file_without_the_columns_restores_as_named(self) -> None:
        await self.add("Ken", status=CONFIRMED)
        data = await self.campaigns.export(GUILD_A, self.c)
        for row in data["sections"]["memory"]:
            if row.get("table") == "entity":
                for key in ("unnamed", "unnamed_anchor", "unnamed_role", "unnamed_what"):
                    row.pop(key, None)
        restored = await self.campaigns.import_backup(
            GUILD_B, decode_backup(encode_backup(data)), DM
        )
        copied = await self.snapshot(GUILD_B, restored.id)
        self.assertTrue(copied["memory_entities"])
        self.assertFalse(any(r["unnamed"] for r in copied["memory_entities"]))

    async def test_other_damaged_files_are_refused_too(self) -> None:
        ken = await self.add("Ken", status=CONFIRMED)
        await self.memory.add_unnamed(GUILD_A, self.c, what="sister", anchor_id=ken, role="sibling")
        await self.add("Mira", status=CONFIRMED)
        spoilers = (
            {"unnamed_anchor": "f" * 32},  # not in the file
            {"unnamed_role": "r" * 41},
            {"unnamed": False},  # still has a word
        )
        for spoil in spoilers:
            data = await self.campaigns.export(GUILD_A, self.c)
            for row in data["sections"]["memory"]:
                if row.get("table") == "entity" and row.get("unnamed"):
                    row.update(spoil)
            with self.subTest(spoil=spoil), self.assertRaises(CampaignError):
                await self.campaigns.import_backup(GUILD_B, decode_backup(encode_backup(data)), DM)
        data = await self.campaigns.export(GUILD_A, self.c)
        for row in data["sections"]["memory"]:
            if row.get("table") == "entity" and row["name"] == "Mira":
                row["unnamed_anchor"] = ken  # an anchor on a named entry
        with self.assertRaises(CampaignError):
            await self.campaigns.import_backup(GUILD_B, decode_backup(encode_backup(data)), DM)

    async def test_a_damaged_file_is_refused_not_half_loaded(self) -> None:
        ken = await self.add("Ken", status=CONFIRMED)
        await self.memory.add_unnamed(GUILD_A, self.c, what="sister", anchor_id=ken, role="sibling")
        data = await self.campaigns.export(GUILD_A, self.c)
        for row in data["sections"]["memory"]:
            if row.get("table") == "entity" and row.get("unnamed"):
                row["unnamed_what"] = None  # a named-looking row marked unnamed
        with self.assertRaises(CampaignError):
            await self.campaigns.import_backup(GUILD_B, decode_backup(encode_backup(data)), DM)


if __name__ == "__main__":
    unittest.main()
