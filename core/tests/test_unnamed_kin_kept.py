"""An unnamed relative of a kept character is kept as an NPC (#4636 part 1, the owner's "stop
asking the obvious": "Brewner's daughter", "Cerric's paternal grandfather" were asked after every
upload). Never more visible than the tie that makes it (Supervisor's condition). Invented names."""

from __future__ import annotations

from dmbot.memory import auto_accept as auto
from dmbot.memory.backstory_links import NewLink
from dmbot.memory.models import Entity
from dmbot.ui.names import background_kind
from tests.test_accept_all import Base
from tests.test_memory_names import GUILD


class UnnamedKinKept(Base):
    async def apply(self, **kw: object) -> auto.Tally:
        tally = auto.Tally()
        args = {"narrow": True, "kind_of": background_kind, **kw}
        await auto.accept_names(self.memory, GUILD, self.sample.id, tally, **args)  # type: ignore[arg-type]
        return tally

    async def relative(self, of: str, what: str = "daughter", kind: str = "character") -> str:
        made = await self.memory.add_unnamed(
            GUILD, self.sample.id, what=what, anchor_id=of, role="child", kind=kind
        )
        return made.entity.id

    async def entry(self, entity_id: str) -> Entity:
        found = await self.memory.entity(GUILD, self.sample.id, entity_id)
        assert found is not None
        return found

    async def test_a_kept_characters_relative_is_kept_as_an_npc(self) -> None:
        daughter = await self.relative(self.dorn)
        tally = await self.apply()
        kept = await self.entry(daughter)
        self.assertEqual((kept.status, kept.role), ("confirmed", "npc"))
        self.assertTrue(kept.unseen_by_players)  # (unnamed: the DM's alone until named)
        self.assertFalse(kept.hidden_from_players)  # (no secret tie: naming it shows it)
        self.assertEqual(tally.accepted[auto.NAMES], 5)
        self.assertEqual(tally.waiting[auto.NAMES], 0)

    async def test_a_relative_from_a_secret_tie_stays_hidden_once_named(self) -> None:
        daughter = await self.relative(self.dorn)
        await self.links.add(
            GUILD, self.sample.id,
            [NewLink(self.dorn, "kin_of", daughter, "q", secret=True, upload="a file, 1 Jan")],
        )  # fmt: skip
        await self.apply()
        kept = await self.entry(daughter)
        self.assertEqual(kept.status, "confirmed")
        self.assertTrue(kept.hidden_from_players)

    async def test_a_relative_of_a_hidden_character_stays_hidden(self) -> None:
        await self.memory.confirm_entity(GUILD, self.sample.id, self.dorn, "npc", source="dm")
        await self.memory.set_hidden_from_players(
            GUILD, self.sample.id, self.dorn, True, source="dm", dm_chose_secret=True
        )
        daughter = await self.relative(self.dorn)
        await self.apply()
        self.assertTrue((await self.entry(daughter)).hidden_from_players)

    async def test_a_relative_of_a_name_still_waiting_waits(self) -> None:
        wen = await self.read_name("Wen", "character", "player_character")  # (never kept here)
        son = await self.relative(wen, "son")
        tally = await self.apply()
        self.assertEqual((await self.entry(son)).status, "proposed")
        self.assertEqual(tally.waiting[auto.NAMES], 2)  # (Wen and her son)

    async def test_a_relative_of_a_relative_is_kept_once_the_first_is(self) -> None:
        father = await self.relative(self.dorn, "father")
        grandfather = await self.relative(father, "father")
        await self.apply()
        self.assertEqual((await self.entry(father)).status, "confirmed")
        self.assertEqual((await self.entry(grandfather)).status, "confirmed")

    async def test_a_group_that_hangs_off_a_character_stays_a_question(self) -> None:
        crew = await self.relative(self.dorn, "the loud sailors", kind="faction")
        await self.apply()
        self.assertEqual((await self.entry(crew)).status, "proposed")

    async def test_one_undo_takes_the_relative_back(self) -> None:
        daughter = await self.relative(self.dorn)
        tally = await self.apply()
        last = tally.batches[-1]  # (the relative is kept after the names)
        await self.memory.undo(GUILD, self.sample.id, last, source="dm")
        back = await self.entry(daughter)
        self.assertEqual(back.status, "proposed")
        self.assertFalse(back.hidden_from_players)

    async def test_a_dry_run_counts_it_and_changes_nothing(self) -> None:
        daughter = await self.relative(self.dorn)
        tally = await self.apply(dry_run=True)
        self.assertEqual(tally.accepted[auto.NAMES], 5)
        self.assertEqual((await self.entry(daughter)).status, "proposed")

    async def test_the_old_accept_all_is_unchanged(self) -> None:
        await self.relative(self.dorn)
        tally = await self.apply(narrow=False)
        self.assertEqual(tally.accepted[auto.NAMES] + tally.waiting[auto.NAMES], 5)
