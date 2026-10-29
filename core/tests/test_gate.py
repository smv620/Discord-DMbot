"""The one gate (#2995; STORY_MEMORY §9b): what each viewer may know, worked out from the lists of
who knows each statement. Pure rules first (every row of §9b's table, Clyde and Tran, a connection
learned implies its entries), then the store: the writer, Undo, a merge, a backup, isolation.
Invented names throughout."""

from __future__ import annotations

import unittest

from dmbot.campaigns.store import decode_backup, encode_backup
from dmbot.memory import gate
from dmbot.memory.gate import (
    ALIAS,
    CONNECTION,
    ENTRY,
    PROPERTY,
    Statements,
    Viewer,
    visible,
    word_of,
)
from dmbot.memory.models import CONFIRMED, MemoryRuleError
from tests.test_memory_store import GUILD_A, GUILD_B, MemoryTest

OKTHANDOM, MARA, BRUNO, TRAN, CLYDE = "okthandom", "mara", "bruno", "tran", "clyde"
PCS = frozenset({OKTHANDOM, MARA})


def lists(**kw: set[str]) -> Statements:
    """`Statements` from (kind_id → knowers) keywords such as `sword=...`: kinds by prefix."""
    knowers = {}
    for key, who in kw.items():
        kind, _, ident = key.partition("__")
        knowers[(kind, ident)] = frozenset(who)
    return Statements(knowers=knowers, player_characters=PCS)


class Words(unittest.TestCase):
    def test_each_row_of_the_table(self) -> None:
        self.assertEqual(word_of(set(), PCS), gate.SECRET)
        self.assertEqual(word_of({BRUNO, TRAN}, PCS), gate.HIDDEN_FROM_THE_PLAYERS)
        self.assertEqual(word_of({BRUNO}, PCS), gate.KNOWN_TO_ONE_NPC)
        self.assertEqual(word_of({OKTHANDOM}, PCS), gate.PARTLY_REVEALED)
        self.assertEqual(word_of({OKTHANDOM, BRUNO}, PCS), gate.PARTLY_REVEALED)
        self.assertEqual(word_of({OKTHANDOM, MARA}, PCS), gate.KNOWN_TO_THE_PARTY)
        self.assertEqual(word_of({OKTHANDOM, MARA, BRUNO}, PCS), gate.KNOWN_TO_THE_PARTY)


class Viewers(unittest.TestCase):
    def test_the_dm_knows_everything_without_being_listed(self) -> None:
        got = visible(Viewer.the_dm(), lists(**{"entry__sword": set()}))
        self.assertTrue(got.knows(ENTRY, "sword"))
        self.assertTrue(got.knows(CONNECTION, "anything"))

    def test_a_player_sees_only_what_their_own_characters_know(self) -> None:
        data = lists(**{"property__cursed": {OKTHANDOM}, "property__hidden": {MARA}})
        got = visible(Viewer.player({OKTHANDOM}), data)
        self.assertTrue(got.knows(PROPERTY, "cursed"))
        self.assertFalse(got.knows(PROPERTY, "hidden"))
        both = visible(Viewer.player({OKTHANDOM, MARA}), data)  # the union of their characters
        self.assertTrue(both.knows(PROPERTY, "hidden"))
        self.assertFalse(visible(Viewer.player(set()), data).knows(PROPERTY, "cursed"))

    def test_a_secret_is_seen_by_no_one_but_the_dm(self) -> None:
        data = lists(**{"property__sword": set()})
        for viewer in (Viewer.player(PCS), Viewer.the_party(), Viewer.the_table(PCS)):
            self.assertFalse(visible(viewer, data).knows(PROPERTY, "sword"))

    def test_the_party_and_the_table_are_what_every_player_character_knows(self) -> None:
        data = lists(**{"property__all": {OKTHANDOM, MARA}, "property__one": {OKTHANDOM}})
        for viewer in (Viewer.the_party(), Viewer.the_table(PCS)):
            got = visible(viewer, data)
            self.assertTrue(got.knows(PROPERTY, "all"))
            self.assertFalse(got.knows(PROPERTY, "one"))  # partly revealed: not for the table
        # the table at a session where only Okthandom played
        self.assertTrue(visible(Viewer.the_table({OKTHANDOM}), data).knows(PROPERTY, "one"))
        self.assertFalse(visible(Viewer.the_table(set()), data).knows(PROPERTY, "all"))

    def test_seeing_as_one_character_is_what_that_character_knows(self) -> None:
        data = lists(**{"property__bruno": {BRUNO}})
        self.assertTrue(visible(Viewer.character(BRUNO), data).knows(PROPERTY, "bruno"))
        self.assertFalse(visible(Viewer.character(OKTHANDOM), data).knows(PROPERTY, "bruno"))

    def test_clyde_learns_nothing_from_tran(self) -> None:
        data = lists(**{"connection__spy": {TRAN}})
        self.assertTrue(visible(Viewer.character(TRAN), data).knows(CONNECTION, "spy"))
        self.assertFalse(visible(Viewer.character(CLYDE), data).knows(CONNECTION, "spy"))

    def test_a_statement_learned_makes_the_entries_it_names_known(self) -> None:
        data = Statements(
            knowers={(CONNECTION, "r1"): frozenset({TRAN}), (ALIAS, "a1"): frozenset({TRAN}),
                     (PROPERTY, "s1"): frozenset({TRAN})},
            connections={"r1": ("bruno-entry", "shela-entry")},
            aliases={"a1": ("rook-entry",)},
            states={"s1": "sword-entry"},
            player_characters=PCS,
        )  # fmt: skip
        got = visible(Viewer.character(TRAN), data)
        self.assertEqual(got.entries, {"bruno-entry", "shela-entry", "rook-entry", "sword-entry"})
        self.assertEqual(visible(Viewer.character(CLYDE), data).entries, frozenset())


class Stored(MemoryTest):
    async def table(self) -> dict[str, str]:
        add = self.add
        return {
            "okthandom": await add(
                "Okthandom", type="character", role="player_character", played_by=1,
                status=CONFIRMED,
            ),
            "mara": await add(
                "Mara", type="character", role="player_character", played_by=2, status=CONFIRMED
            ),
            "bruno": await add("Bruno", type="character", role="npc", status=CONFIRMED),
            "shela": await add("Shela", type="character", role="npc", status=CONFIRMED),
            "sword": await add("The Sword", type="item", status=CONFIRMED),
        }  # fmt: skip

    async def link(self, a: str, b: str) -> str:
        made = await self.memory.add_relation(
            GUILD_A, self.c, a, "knows", b, source="dm", confidence=1.0, status=CONFIRMED,
            secret=True,
        )  # fmt: skip
        return str(made.value[0].id)

    async def know(self, kind: str, statement: str, who: list[str]) -> None:
        await self.memory.set_statement_knowers(GUILD_A, self.c, kind, statement, who, source="dm")

    async def test_the_writer_and_the_gate_agree_for_each_kind(self) -> None:
        ids = await self.table()
        rel = await self.link(ids["bruno"], ids["shela"])
        await self.know(ENTRY, ids["shela"], [ids["bruno"]])
        await self.know(CONNECTION, rel, [ids["okthandom"], ids["bruno"]])
        got = await self.memory.statement_knowers(GUILD_A, self.c, CONNECTION, [rel])
        self.assertEqual(sorted(got[rel]), sorted([ids["okthandom"], ids["bruno"]]))
        player = await self.memory.visible_to(GUILD_A, self.c, Viewer.character(ids["okthandom"]))
        self.assertTrue(player.knows(CONNECTION, rel))
        self.assertTrue({ids["bruno"], ids["shela"]} <= player.entries)  # (the ends are known)
        other = await self.memory.visible_to(GUILD_A, self.c, Viewer.character(ids["mara"]))
        self.assertFalse(other.knows(CONNECTION, rel))
        self.assertFalse(other.knows(ENTRY, ids["shela"]))
        dm = await self.memory.visible_to(GUILD_A, self.c, Viewer.the_dm())
        self.assertTrue(dm.knows(CONNECTION, rel))

    async def test_setting_the_list_again_replaces_it_and_none_means_only_the_dm(self) -> None:
        ids = await self.table()
        await self.know(ENTRY, ids["sword"], [ids["okthandom"], ids["mara"]])
        await self.know(ENTRY, ids["sword"], [ids["mara"]])
        got = await self.memory.statement_knowers(GUILD_A, self.c, ENTRY, [ids["sword"]])
        self.assertEqual(got[ids["sword"]], [ids["mara"]])
        await self.know(ENTRY, ids["sword"], [])
        self.assertEqual(
            await self.memory.statement_knowers(GUILD_A, self.c, ENTRY, [ids["sword"]]), {}
        )

    async def test_only_the_dm_and_only_characters_of_this_campaign(self) -> None:
        ids = await self.table()
        with self.assertRaises(MemoryRuleError):
            await self.memory.set_statement_knowers(
                GUILD_A, self.c, ENTRY, ids["sword"], [ids["mara"]], source="scan"
            )
        for bad in (ids["sword"], "0" * 32):  # an item, an id from nowhere
            with self.assertRaises(MemoryRuleError):
                await self.know(ENTRY, ids["shela"], [bad])
        with self.assertRaises(MemoryRuleError):
            await self.know(ENTRY, "0" * 32, [ids["mara"]])

    async def test_undo_takes_a_change_of_who_knows_back(self) -> None:
        ids = await self.table()
        done = await self.memory.set_statement_knowers(
            GUILD_A, self.c, ENTRY, ids["sword"], [ids["mara"]], source="dm"
        )
        await self.memory.undo(GUILD_A, self.c, done.batch, source="dm")
        self.assertEqual(
            await self.memory.statement_knowers(GUILD_A, self.c, ENTRY, [ids["sword"]]), {}
        )

    async def test_isolation_holds(self) -> None:
        ids = await self.table()
        rel = await self.link(ids["bruno"], ids["shela"])
        await self.know(CONNECTION, rel, [ids["bruno"]])
        other = (await self.campaigns.create(GUILD_B, "Elsewhere", 9)).id
        seen = await self.memory.visible_to(GUILD_B, other, Viewer.character(ids["bruno"]))
        self.assertFalse(seen.knows(CONNECTION, rel))  # another server sees nothing
        self.assertEqual(await self.memory.statement_knowers(GUILD_B, other, CONNECTION, [rel]), {})

    async def test_a_backup_keeps_who_knows(self) -> None:
        ids = await self.table()
        rel = await self.link(ids["bruno"], ids["shela"])
        await self.know(CONNECTION, rel, [ids["bruno"]])
        await self.know(ENTRY, ids["sword"], [ids["mara"]])
        raw = encode_backup(await self.campaigns.export(GUILD_A, self.c))
        restored = await self.campaigns.import_backup(GUILD_B, decode_backup(raw), 7)
        self.assertEqual(await self.count("memory_knowers", GUILD_B), 2)
        got = await self.memory.statement_knowers(GUILD_B, restored.id, CONNECTION, [rel])
        self.assertEqual(got, {rel: [ids["bruno"]]})

    async def test_a_merge_moves_who_knows_the_entry_exists(self) -> None:
        ids = await self.table()
        twin = await self.add("Shela Vane", type="character", role="npc", status=CONFIRMED)
        await self.know(ENTRY, twin, [ids["mara"]])
        await self.know(ENTRY, ids["shela"], [ids["mara"], ids["okthandom"]])
        await self.memory.merge(GUILD_A, self.c, ids["shela"], twin, source="dm", dm_said_same=True)
        got = await self.memory.statement_knowers(GUILD_A, self.c, ENTRY, [ids["shela"]])
        self.assertEqual(sorted(got[ids["shela"]]), sorted([ids["mara"], ids["okthandom"]]))

    async def test_a_merged_character_brings_what_they_knew_and_shared_rows_fold(self) -> None:
        ids = await self.table()
        twin = await self.add("Brunhild", type="character", role="npc", status=CONFIRMED)
        await self.know(ENTRY, ids["sword"], [ids["bruno"], twin])  # both knew it
        await self.know(ENTRY, ids["shela"], [twin])  # only the twin knew this one
        await self.memory.merge(GUILD_A, self.c, ids["bruno"], twin, source="dm", dm_said_same=True)
        sword = await self.memory.statement_knowers(GUILD_A, self.c, ENTRY, [ids["sword"]])
        shela = await self.memory.statement_knowers(GUILD_A, self.c, ENTRY, [ids["shela"]])
        self.assertEqual(sword[ids["sword"]], [ids["bruno"]])  # one row, not two
        self.assertEqual(shela[ids["shela"]], [ids["bruno"]])
        self.assertEqual(await self.count("memory_knowers"), 2)

    async def test_a_rejected_statement_grants_nothing(self) -> None:
        ids = await self.table()
        rel = await self.link(ids["bruno"], ids["shela"])
        await self.know(CONNECTION, rel, [ids["okthandom"]])
        before = await self.memory.visible_to(GUILD_A, self.c, Viewer.character(ids["okthandom"]))
        self.assertTrue(before.knows(CONNECTION, rel))
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "UPDATE memory_relations SET status = 'rejected' WHERE id = %s", (rel,)
            )
        after = await self.memory.visible_to(GUILD_A, self.c, Viewer.character(ids["okthandom"]))
        self.assertFalse(after.knows(CONNECTION, rel))  # its rows stay, they grant nothing

    async def test_the_writer_refuses_a_place_a_rejected_entry_and_an_unknown_kind(self) -> None:
        ids = await self.table()
        place = await self.add("Greywater", type="place", status=CONFIRMED)
        with self.assertRaises(MemoryRuleError):
            await self.know(ENTRY, ids["sword"], [place])  # a place knows nothing
        with self.assertRaises(MemoryRuleError):
            await self.know("rumour", ids["sword"], [ids["mara"]])
        gone = await self.add("Nobody", type="npc", status="proposed")
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "UPDATE memory_entities SET status = 'rejected' WHERE id = %s", (gone,)
            )
        with self.assertRaises(MemoryRuleError):
            await self.know(ENTRY, gone, [ids["mara"]])

    async def test_an_npc_knows_it_exists_itself(self) -> None:
        ids = await self.table()
        await self.know(ENTRY, ids["bruno"], [ids["bruno"]])  # self-knowledge (§9b)
        got = await self.memory.visible_to(GUILD_A, self.c, Viewer.character(ids["bruno"]))
        self.assertTrue(got.knows(ENTRY, ids["bruno"]))
        other = await self.memory.visible_to(GUILD_A, self.c, Viewer.character(ids["mara"]))
        self.assertFalse(other.knows(ENTRY, ids["bruno"]))

    async def test_a_crafted_backup_with_a_bad_knower_row_is_refused(self) -> None:
        import copy

        from dmbot.campaigns.models import CampaignError

        ids = await self.table()
        await self.know(ENTRY, ids["sword"], [ids["mara"]])
        raw = await self.campaigns.export(GUILD_A, self.c)
        rows = [r for r in raw["sections"]["memory"] if r.get("table") == "knower"]
        self.assertEqual(len(rows), 1)
        for change in (
            {"about_entity_id": None},  # names no statement
            {"state_id": "f" * 32},  # names two
            {"perspective_id": "e" * 32},  # a point of view the file does not hold
        ):
            bad = copy.deepcopy(raw)
            for r in bad["sections"]["memory"]:
                if r.get("table") == "knower":
                    r.update(change)
            with self.assertRaises(CampaignError):
                await self.campaigns.import_backup(GUILD_B, decode_backup(encode_backup(bad)), 7)

    async def test_the_table_viewer_needs_every_listed_character_to_be_known(self) -> None:
        data = lists(**{"property__big": {OKTHANDOM, MARA, BRUNO}})  # a strict superset
        self.assertTrue(visible(Viewer.the_table({OKTHANDOM, MARA}), data).knows(PROPERTY, "big"))
