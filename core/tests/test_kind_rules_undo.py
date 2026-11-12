"""Kind rules after an Undo and in a group confirm (#4244): an Undo that gives an entry an older
kind settles it as any kind change does, and a waiting entry with a player is never retyped in
bulk. Either would otherwise leave a row a backup refuses. Invented names."""

from __future__ import annotations

from dmbot.campaigns.store import decode_backup, encode_backup
from dmbot.memory.models import CONFIRMED, PROPOSED
from tests.test_memory_store import DM, GUILD_A, GUILD_B, MemoryTest


class AfterAnUndo(MemoryTest):
    async def test_undoing_a_kind_change_takes_what_the_newer_kind_had(self) -> None:
        tower = await self.add("The Tower", type="place")
        made = await self.memory.set_entity_type(GUILD_A, self.c, tower, "npc", source="dm")
        await self.memory.set_rule_link(GUILD_A, self.c, tower, "class", "Wizard", source="dm")
        assert made.batch is not None
        undone = await self.memory.undo(GUILD_A, self.c, made.batch, source="dm")
        entity = await self.memory.entity(GUILD_A, self.c, tower)
        assert entity is not None
        self.assertEqual(entity.type, "place")
        self.assertEqual(await self.memory.rule_links(GUILD_A, self.c, [tower]), [])
        raw = encode_backup(await self.campaigns.export(GUILD_A, self.c))
        await self.campaigns.import_backup(GUILD_B, decode_backup(raw), DM)  # (loads)
        assert undone.batch is not None
        await self.memory.undo(GUILD_A, self.c, undone.batch, source="dm")  # the redo
        (back,) = await self.memory.rule_links(GUILD_A, self.c, [tower])
        self.assertEqual(back.name, "Wizard")
        redone = await self.memory.entity(GUILD_A, self.c, tower)
        assert redone is not None
        self.assertEqual(redone.type, "character")  # a character again, with its class


class InAGroup(MemoryTest):
    async def test_a_waiting_entry_with_a_player_is_not_retyped_in_bulk(self) -> None:
        person = (
            await self.memory.add_entity(
                GUILD_A, self.c, type="player_character", name="Sam", source="scan",
                status=PROPOSED, played_by=5,
            )
        ).value.id  # fmt: skip
        plain = await self.add("Grik", source="scan", status=PROPOSED)
        written = await self.memory.confirm_kinds(
            GUILD_A, self.c, [person, plain], "place", source="dm"
        )
        self.assertEqual(written.value, 1)  # the count the DM is told
        sam = await self.memory.entity(GUILD_A, self.c, person)
        grik = await self.memory.entity(GUILD_A, self.c, plain)
        assert sam is not None and grik is not None
        self.assertEqual((sam.status, sam.played_by), (PROPOSED, 5))  # left for its own card
        self.assertEqual((grik.type, grik.status), ("place", CONFIRMED))
        raw = encode_backup(await self.campaigns.export(GUILD_A, self.c))
        await self.campaigns.import_backup(GUILD_B, decode_backup(raw), DM)  # (loads)
