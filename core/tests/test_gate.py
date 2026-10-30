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
        self.assertEqual(
            got.entries, {"bruno-entry", "shela-entry", "rook-entry", "sword-entry", TRAN}
        )
        self.assertEqual(visible(Viewer.character(CLYDE), data).entries, {CLYDE})


class Helpers(MemoryTest):
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
            # (hidden from the players, so the lists start empty: these tests set them)
            "bruno": await add(
                "Bruno", type="character", role="npc", status=CONFIRMED, hidden_from_players=True
            ),
            "shela": await add(
                "Shela", type="character", role="npc", status=CONFIRMED, hidden_from_players=True
            ),
            "sword": await add(
                "The Sword", type="item", status=CONFIRMED, hidden_from_players=True
            ),
        }  # fmt: skip

    async def link(self, a: str, b: str) -> str:
        made = await self.memory.add_relation(
            GUILD_A, self.c, a, "knows", b, source="dm", confidence=1.0, status=CONFIRMED,
            secret=True,
        )  # fmt: skip
        return str(made.value[0].id)

    async def know(self, kind: str, statement: str, who: list[str]) -> None:
        await self.memory.set_statement_knowers(GUILD_A, self.c, kind, statement, who, source="dm")


class Stored(Helpers):
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
        assert done.batch is not None
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
        got = await self.memory.statement_knowers(GUILD_B, restored.id, CONNECTION, [rel])
        self.assertEqual(got, {rel: [ids["bruno"]]})
        sword = await self.memory.statement_knowers(GUILD_B, restored.id, ENTRY, [ids["sword"]])
        self.assertEqual(sword, {ids["sword"]: [ids["mara"]]})

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
        both = await self.memory.statement_knowers(
            GUILD_A, self.c, ENTRY, [ids["sword"], ids["shela"]]
        )
        self.assertEqual(sum(len(v) for v in both.values()), 2)  # two rows, none doubled

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
        rows = [
            r
            for r in raw["sections"]["memory"]
            if r.get("table") == "knower" and r.get("about_entity_id") == ids["sword"]
        ]
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


GROUP = "irendi"  # a group (the Irendi): its row is what its members commonly know


class Certainty(unittest.TestCase):
    """How sure each point of view is (#2995, owner's example: Bruno, Shela and Tran)."""

    def test_the_owners_example_no_row_is_not_knowing_and_zero_is_believing_it_false(self) -> None:
        data = Statements(
            knowers={
                (CONNECTION, "bruno_desires_shela"): {BRUNO: 1.0, "shela": 0.95},
                (CONNECTION, "shela_desires_bruno"): {BRUNO: 0.3, "shela": 0.0},
            },
            player_characters=PCS,
        )
        bruno = visible(Viewer.character(BRUNO), data)
        self.assertEqual(bruno.how_sure(CONNECTION, "bruno_desires_shela"), 1.0)
        self.assertEqual(bruno.how_sure(CONNECTION, "shela_desires_bruno"), 0.3)
        shela = visible(Viewer.character("shela"), data)
        self.assertEqual(shela.how_sure(CONNECTION, "bruno_desires_shela"), 0.95)
        self.assertEqual(shela.how_sure(CONNECTION, "shela_desires_bruno"), 0.0)
        self.assertTrue(shela.knows(CONNECTION, "shela_desires_bruno"))  # knows of it, disbelieves
        tran = visible(Viewer.character(TRAN), data)
        self.assertFalse(tran.knows(CONNECTION, "bruno_desires_shela"))  # no row: does not know
        self.assertIsNone(tran.how_sure(CONNECTION, "bruno_desires_shela"))

    def test_a_player_is_as_sure_as_their_surest_character_and_the_table_as_the_least_sure(
        self,
    ) -> None:
        data = Statements(
            knowers={(PROPERTY, "p"): {OKTHANDOM: 0.4, MARA: 0.9}}, player_characters=PCS
        )
        both = visible(Viewer.player({OKTHANDOM, MARA}), data)
        self.assertEqual(both.how_sure(PROPERTY, "p"), 0.9)
        table = visible(Viewer.the_table({OKTHANDOM, MARA}), data)
        self.assertEqual(table.how_sure(PROPERTY, "p"), 0.4)
        one_missing = Statements(knowers={(PROPERTY, "p"): {MARA: 0.9}}, player_characters=PCS)
        self.assertFalse(
            visible(Viewer.the_table({OKTHANDOM, MARA}), one_missing).knows(PROPERTY, "p")
        )

    def test_the_dm_is_never_asked_how_sure(self) -> None:
        self.assertIsNone(visible(Viewer.the_dm(), lists()).how_sure(PROPERTY, "p"))

    def test_a_plain_list_means_everyone_is_sure(self) -> None:
        got = visible(Viewer.character(MARA), lists(**{"property__p": {MARA}}))
        self.assertEqual(got.how_sure(PROPERTY, "p"), 1.0)


class Groups(unittest.TestCase):
    def data(self, own: dict[str, float] | None = None) -> Statements:
        return Statements(
            knowers={(CONNECTION, "spy"): {GROUP: 0.8, **(own or {})}},
            groups={BRUNO: frozenset({GROUP}), TRAN: frozenset({GROUP})},
            player_characters=PCS,
        )

    def test_a_member_knows_what_the_group_commonly_knows_at_the_groups_certainty(self) -> None:
        got = visible(Viewer.character(BRUNO), self.data())
        self.assertEqual(got.how_sure(CONNECTION, "spy"), 0.8)

    def test_a_members_own_row_wins_even_when_lower(self) -> None:
        got = visible(Viewer.character(BRUNO), self.data({BRUNO: 0.1}))
        self.assertEqual(got.how_sure(CONNECTION, "spy"), 0.1)
        self.assertEqual(
            visible(Viewer.character(TRAN), self.data({BRUNO: 0.1})).how_sure(CONNECTION, "spy"),
            0.8,
        )

    def test_a_friend_who_is_not_a_member_learns_nothing(self) -> None:
        self.assertFalse(visible(Viewer.character(CLYDE), self.data()).knows(CONNECTION, "spy"))

    def test_a_player_character_in_the_group_knows_it_so_the_party_does_not_unless_all_are(
        self,
    ) -> None:
        data = Statements(
            knowers={(CONNECTION, "spy"): {GROUP: 1.0}},
            groups={OKTHANDOM: frozenset({GROUP})},
            player_characters=PCS,
        )
        self.assertTrue(visible(Viewer.character(OKTHANDOM), data).knows(CONNECTION, "spy"))
        self.assertFalse(
            visible(Viewer.the_party(), data).knows(CONNECTION, "spy")
        )  # Mara is not in it


class StoredCertainty(Helpers):
    async def test_the_writer_keeps_each_certainty_and_changes_it_in_place(self) -> None:
        ids = await self.table()
        rel = await self.link(ids["bruno"], ids["shela"])
        await self.memory.set_statement_knowers(
            GUILD_A, self.c, CONNECTION, rel, {ids["bruno"]: 1.0, ids["shela"]: 0.95}, source="dm"
        )
        seen = await self.memory.visible_to(GUILD_A, self.c, Viewer.character(ids["shela"]))
        self.assertEqual(seen.how_sure(CONNECTION, rel), 0.95)
        await self.memory.set_statement_knowers(
            GUILD_A, self.c, CONNECTION, rel, {ids["bruno"]: 1.0, ids["shela"]: 0.0}, source="dm"
        )
        again = await self.memory.visible_to(GUILD_A, self.c, Viewer.character(ids["shela"]))
        self.assertEqual(again.how_sure(CONNECTION, rel), 0.0)
        got = await self.memory.statement_knowers(GUILD_A, self.c, CONNECTION, [rel])
        self.assertEqual(len(got[rel]), 2)  # one row each, changed in place
        with self.assertRaises(MemoryRuleError):
            await self.memory.set_statement_knowers(
                GUILD_A, self.c, CONNECTION, rel, {ids["bruno"]: 1.5}, source="dm"
            )

    async def test_a_group_is_a_knower_and_its_members_follow_it(self) -> None:
        ids = await self.table()
        guild = await self.add("The Irendi", type="faction", status=CONFIRMED)
        await self.relate(ids["bruno"], "member_of", guild, status=CONFIRMED)
        rel = await self.link(ids["shela"], ids["sword"])
        await self.memory.set_statement_knowers(
            GUILD_A, self.c, CONNECTION, rel, {guild: 0.7}, source="dm"
        )
        bruno = await self.memory.visible_to(GUILD_A, self.c, Viewer.character(ids["bruno"]))
        self.assertEqual(bruno.how_sure(CONNECTION, rel), 0.7)
        outsider = await self.memory.visible_to(GUILD_A, self.c, Viewer.character(ids["mara"]))
        self.assertFalse(outsider.knows(CONNECTION, rel))
        await self.memory.set_statement_knowers(
            GUILD_A, self.c, CONNECTION, rel, {guild: 0.7, ids["bruno"]: 0.2}, source="dm"
        )
        own = await self.memory.visible_to(GUILD_A, self.c, Viewer.character(ids["bruno"]))
        self.assertEqual(own.how_sure(CONNECTION, rel), 0.2)  # his own row wins

    async def test_a_backup_keeps_the_certainties(self) -> None:
        ids = await self.table()
        await self.memory.set_statement_knowers(
            GUILD_A, self.c, ENTRY, ids["sword"], {ids["mara"]: 0.35}, source="dm"
        )
        raw = encode_backup(await self.campaigns.export(GUILD_A, self.c))
        restored = await self.campaigns.import_backup(GUILD_B, decode_backup(raw), 7)
        seen = await self.memory.visible_to(GUILD_B, restored.id, Viewer.character(ids["mara"]))
        self.assertEqual(seen.how_sure(ENTRY, ids["sword"]), 0.35)


class FlagsFollow(Helpers):
    """The old flags keep the lists in step (#2995, slice 2b)."""

    async def known(self, kind: str, statement: str) -> list[str]:
        got = await self.memory.statement_knowers(GUILD_A, self.c, kind, [statement])
        return sorted(got.get(statement, []))

    async def pcs(self, ids: dict[str, str]) -> list[str]:
        return sorted([ids["okthandom"], ids["mara"]])

    async def test_a_new_shown_entry_is_known_to_every_player_character(self) -> None:
        ids = await self.table()
        place = await self.add("Greywater", type="place", status=CONFIRMED)
        self.assertEqual(await self.known(ENTRY, place), await self.pcs(ids))

    async def test_a_hidden_or_unconfirmed_entry_is_known_to_nobody(self) -> None:
        ids = await self.table()
        hidden = await self.add("Rook", type="npc", status=CONFIRMED, hidden_from_players=True)
        waiting = await self.add("Zed", type="npc", status="proposed")
        self.assertEqual(await self.known(ENTRY, hidden), [])
        self.assertEqual(await self.known(ENTRY, waiting), [])
        self.assertIn(ids["okthandom"], await self.known(ENTRY, ids["okthandom"]))

    async def test_hiding_clears_the_list_and_showing_again_gives_it_to_every_pc(self) -> None:
        ids = await self.table()
        place = await self.add("Greywater", type="place", status=CONFIRMED)
        await self.memory.set_hidden_from_players(GUILD_A, self.c, place, True, source="dm")
        self.assertEqual(await self.known(ENTRY, place), [])
        await self.memory.set_hidden_from_players(GUILD_A, self.c, place, False, source="dm")
        self.assertEqual(await self.known(ENTRY, place), await self.pcs(ids))

    async def test_undo_of_hiding_brings_the_list_back(self) -> None:
        ids = await self.table()
        place = await self.add("Greywater", type="place", status=CONFIRMED)
        hide = await self.memory.set_hidden_from_players(GUILD_A, self.c, place, True, source="dm")
        assert hide.batch is not None
        await self.memory.undo(GUILD_A, self.c, hide.batch, source="dm")
        self.assertEqual(await self.known(ENTRY, place), await self.pcs(ids))

    async def test_a_list_the_dm_set_is_kept_while_the_flags_do_not_change(self) -> None:
        ids = await self.table()
        place = await self.add("Greywater", type="place", status=CONFIRMED)
        await self.know(ENTRY, place, [ids["mara"]])  # partly revealed
        await self.memory.add_alias(
            GUILD_A, self.c, place, "The Grey", kind="nickname", source="dm", status=CONFIRMED
        )  # another statement is written: this list is untouched
        self.assertEqual(await self.known(ENTRY, place), [ids["mara"]])

    async def test_a_revealed_connection_and_a_plain_name_follow_their_flags(self) -> None:
        ids = await self.table()
        a = await self.add("Tran", type="character", role="npc", status=CONFIRMED)
        b = await self.add("Clyde", type="character", role="npc", status=CONFIRMED)
        shown = await self.memory.add_relation(
            GUILD_A, self.c, a, "knows", b, source="dm", confidence=1.0, status=CONFIRMED
        )
        rel = str(shown.value[0].id)
        self.assertEqual(await self.known(CONNECTION, rel), await self.pcs(ids))
        # a connection with a hidden end is kept from the players, as the old readers kept it
        kept = await self.memory.add_relation(
            GUILD_A, self.c, a, "knows", ids["bruno"], source="dm", confidence=1.0,
            status=CONFIRMED,
        )  # fmt: skip
        self.assertEqual(await self.known(CONNECTION, str(kept.value[0].id)), [])
        # hiding an entry takes its connections and names with it, showing it brings them back
        await self.memory.set_hidden_from_players(GUILD_A, self.c, b, True, source="dm")
        self.assertEqual(await self.known(CONNECTION, rel), [])
        await self.memory.set_hidden_from_players(GUILD_A, self.c, b, False, source="dm")
        self.assertEqual(await self.known(CONNECTION, rel), await self.pcs(ids))

    async def test_a_condition_is_known_with_its_entry(self) -> None:
        ids = await self.table()
        place = await self.add("Greywater", type="place", status=CONFIRMED)
        await self.memory.set_condition(GUILD_A, self.c, place, "destroyed", source="dm")
        state = (await self.memory.states(GUILD_A, self.c, [place]))[place]
        self.assertEqual(await self.known(PROPERTY, state.id), await self.pcs(ids))

    async def test_the_old_knowers_writers_write_the_list_too_and_keep_other_rows(self) -> None:
        ids = await self.table()
        rel = await self.link(ids["bruno"], ids["shela"])  # secret
        await self.memory.set_statement_knowers(
            GUILD_A, self.c, CONNECTION, rel, {ids["bruno"]: 0.6}, source="dm"
        )
        await self.memory.set_connection_knowers(
            GUILD_A, self.c, rel, [ids["okthandom"]], source="dm"
        )
        self.assertEqual(
            await self.known(CONNECTION, rel), sorted([ids["bruno"], ids["okthandom"]])
        )  # the NPC's row stays; the player character was added
        await self.memory.set_connection_knowers(GUILD_A, self.c, rel, [], source="dm")
        self.assertEqual(await self.known(CONNECTION, rel), [ids["bruno"]])
        shade = await self.memory.add_alias(
            GUILD_A, self.c, ids["bruno"], "Shade", kind="title", secret=True, source="dm"
        )
        await self.memory.set_alias_knowers(
            GUILD_A, self.c, shade.value.id, [ids["mara"]], source="dm"
        )
        self.assertEqual(await self.known(ALIAS, shade.value.id), [ids["mara"]])

    async def test_a_character_knows_its_own_entry_without_a_row(self) -> None:
        ids = await self.table()
        seen = await self.memory.visible_to(GUILD_A, self.c, Viewer.character(ids["bruno"]))
        self.assertTrue(seen.knows(ENTRY, ids["bruno"]))
        self.assertEqual(seen.how_sure(ENTRY, ids["bruno"]), 1.0)
        other = await self.memory.visible_to(GUILD_A, self.c, Viewer.character(ids["shela"]))
        self.assertFalse(other.knows(ENTRY, ids["bruno"]))

    async def test_a_backup_from_before_the_lists_gets_them_from_its_flags(self) -> None:
        ids = await self.table()
        place = await self.add("Greywater", type="place", status=CONFIRMED)
        hidden = await self.add("Rook", type="npc", status=CONFIRMED, hidden_from_players=True)
        raw = await self.campaigns.export(GUILD_A, self.c)
        raw["sections"]["memory"] = [
            r for r in raw["sections"]["memory"] if r.get("table") != "knower"
        ]
        raw["version"] = 2  # (and an older file)
        restored = await self.campaigns.import_backup(GUILD_B, decode_backup(encode_backup(raw)), 7)
        got = await self.memory.statement_knowers(GUILD_B, restored.id, ENTRY, [place, hidden])
        self.assertEqual(sorted(got[place]), await self.pcs(ids))  # shown: every player character
        self.assertEqual(got[hidden], [hidden])  # hidden: nobody, but it knows itself

    async def test_a_current_backup_is_restored_as_it_is(self) -> None:
        ids = await self.table()
        place = await self.add("Greywater", type="place", status=CONFIRMED)
        await self.know(ENTRY, place, [ids["mara"]])
        raw = encode_backup(await self.campaigns.export(GUILD_A, self.c))
        restored = await self.campaigns.import_backup(GUILD_B, decode_backup(raw), 7)
        got = await self.memory.statement_knowers(GUILD_B, restored.id, ENTRY, [place])
        self.assertEqual(got[place], [ids["mara"]])  # not worked out again from the flags

    async def test_a_backup_with_lists_keeps_a_list_the_dm_trimmed(self) -> None:
        ids = await self.table()
        place = await self.add("Greywater", type="place", status=CONFIRMED)  # shown to every PC
        await self.know(ENTRY, place, [ids["mara"]])  # the DM trimmed it: only Mara
        raw = await self.campaigns.export(GUILD_A, self.c)
        raw["version"] = 2  # made by the version before this one: the file still has lists
        restored = await self.campaigns.import_backup(GUILD_B, decode_backup(encode_backup(raw)), 7)
        got = await self.memory.statement_knowers(GUILD_B, restored.id, ENTRY, [place])
        self.assertEqual(got[place], [ids["mara"]])  # not widened again from the flags

    async def test_undo_of_a_brand_new_shown_statement_takes_its_list_with_it(self) -> None:
        ids = await self.table()
        made = await self.memory.add_entity(
            GUILD_A, self.c, name="Greywater", type="place", source="dm", status=CONFIRMED
        )
        place = made.value.id
        self.assertEqual(await self.known(ENTRY, place), await self.pcs(ids))
        assert made.batch is not None
        await self.memory.undo(GUILD_A, self.c, made.batch, source="dm")
        self.assertEqual(await self.known(ENTRY, place), [])
