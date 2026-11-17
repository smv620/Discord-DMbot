"""An unnamed relative of a kept character is kept as an NPC (#4636 part 1, the owner's "stop
asking the obvious": "Brewner's daughter", "Cerric's paternal grandfather" were asked after every
upload). Never more visible than the tie that makes it (Supervisor's condition). Invented names."""

from __future__ import annotations

from dmbot.memory import auto_accept as auto
from dmbot.memory.backstory_links import NewLink
from dmbot.memory.models import Entity, Relation
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

    async def tie(self, relative: str, of: str | None = None, **kw: object) -> None:
        """The stated waiting connection a lore read leaves between a character (Dorn unless
        `of` says) and their relative."""
        await self.links.add(
            GUILD, self.sample.id,
            [NewLink(of or self.dorn, "kin_of", relative, "q", upload="a file, 1 Jan", **kw)],  # type: ignore[arg-type]
        )  # fmt: skip

    async def ties(self, relative: str) -> list[Relation]:
        """The relative's connections in the graph."""
        return await self.memory.relations(
            GUILD, self.sample.id, entity_id=relative, include_secret=True
        )

    async def test_a_kept_characters_relative_is_kept_as_an_npc(self) -> None:
        daughter = await self.relative(self.dorn)
        await self.tie(daughter, world=1)  # (everyone knows Dorn has a daughter)
        tally = await self.apply()
        kept = await self.entry(daughter)
        self.assertEqual((kept.status, kept.role), ("confirmed", "npc"))
        self.assertTrue(kept.unseen_by_players)  # (unnamed: the DM's alone until named)
        self.assertFalse(kept.hidden_from_players)  # (a tie the players will see: naming shows it)
        self.assertEqual(tally.accepted[auto.NAMES], 5)
        self.assertEqual(tally.waiting[auto.NAMES], 0)
        (tie,) = await self.ties(daughter)  # (Supervisor's item 1: the tie is kept with her)
        self.assertEqual((tie.status, tie.source), ("confirmed", "dm"))
        self.assertTrue(tie.unrevealed)  # (not said at the table: she is the DM's alone)
        mine = [w for w in await self.links.waiting(GUILD, self.sample.id) if daughter in
                (w.subject_id, w.object_id)]  # fmt: skip
        self.assertEqual(mine, [])  # (nothing about her waits)

    async def test_a_relative_from_a_secret_tie_stays_hidden_once_named(self) -> None:
        daughter = await self.relative(self.dorn)
        await self.tie(daughter, secret=True, world=1)
        await self.apply()
        kept = await self.entry(daughter)
        self.assertEqual(kept.status, "confirmed")
        self.assertTrue(kept.hidden_from_players)
        await self.memory.rename_entity(GUILD, self.sample.id, daughter, "Bryn", source="dm")
        named = await self.entry(daughter)
        self.assertFalse(named.unnamed)
        self.assertTrue(named.unseen_by_players)  # (named, and still the DM's alone)

    async def test_one_characters_own_backstory_keeps_the_relative_hidden(self) -> None:
        """Supervisor's condition, the common case: a plain backstory is known by its character
        only (and kept unrevealed), so naming the relative must not show her to everyone."""
        daughter = await self.relative(self.dorn)
        await self.tie(daughter, known_by=self.mira, world=1)
        await self.apply()
        await self.memory.rename_entity(GUILD, self.sample.id, daughter, "Bryn", source="dm")
        self.assertTrue((await self.entry(daughter)).unseen_by_players)

    async def test_a_tie_not_said_to_be_known_keeps_the_relative_hidden(self) -> None:
        daughter = await self.relative(self.dorn)
        await self.tie(daughter)  # (world 0: kept, it would wait to be said at the table)
        await self.apply()
        self.assertTrue((await self.entry(daughter)).hidden_from_players)

    async def test_with_no_stated_tie_the_relative_stays_a_question(self) -> None:
        """Supervisor's item 2: no tie says how they are connected, only the label does."""
        daughter = await self.relative(self.dorn)
        tally = await self.apply()
        self.assertEqual((await self.entry(daughter)).status, "proposed")
        self.assertEqual(tally.waiting[auto.NAMES], 1)
        self.assertEqual(tally.refused[auto.NAMES], 0)

    async def test_a_worked_out_tie_is_no_stated_tie(self) -> None:
        daughter = await self.relative(self.dorn)
        await self.tie(daughter, inferred="raised in his house")
        await self.apply()
        self.assertEqual((await self.entry(daughter)).status, "proposed")

    async def test_a_backstory_tie_stays_known_by_its_character_only(self) -> None:
        pc = await self.memory.add_entity(
            GUILD, self.sample.id, name="Tavin", type="character", role="player_character",
            played_by=1001, status="confirmed", source="dm",
        )  # fmt: skip
        daughter = await self.relative(self.dorn)
        await self.tie(daughter, known_by=pc.value.id)
        await self.apply()
        (tie,) = await self.ties(daughter)
        knows = await self.memory.connection_knowers(GUILD, self.sample.id, [tie.id])
        self.assertEqual(knows.get(tie.id), [pc.value.id])

    async def test_a_connection_not_yet_shown_keeps_the_relative_hidden(self) -> None:
        daughter = await self.relative(self.dorn)
        await self.tie(daughter, world=1)
        await self.memory.add_relation(
            GUILD, self.sample.id, self.dorn, "kin_of", daughter, source="extractor",
            confidence=0.5,
        )  # fmt: skip
        await self.apply()
        self.assertTrue((await self.entry(daughter)).hidden_from_players)

    async def test_a_relative_of_a_hidden_character_stays_hidden(self) -> None:
        await self.memory.confirm_entity(GUILD, self.sample.id, self.dorn, "npc", source="dm")
        await self.memory.set_hidden_from_players(
            GUILD, self.sample.id, self.dorn, True, source="dm", dm_chose_secret=True
        )
        daughter = await self.relative(self.dorn)
        await self.tie(daughter, world=1)
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
        await self.tie(father)
        await self.tie(grandfather, of=father)
        await self.apply()
        self.assertEqual((await self.entry(father)).status, "confirmed")
        self.assertEqual((await self.entry(grandfather)).status, "confirmed")
        # (the father has no shown tie, so he is hidden, and his father with him)
        self.assertTrue((await self.entry(grandfather)).hidden_from_players)

    async def test_a_group_that_hangs_off_a_character_stays_a_question(self) -> None:
        crew = await self.relative(self.dorn, "the loud sailors", kind="faction")
        await self.apply()
        self.assertEqual((await self.entry(crew)).status, "proposed")

    async def test_one_undo_takes_the_relative_back(self) -> None:
        daughter = await self.relative(self.dorn)
        await self.tie(daughter, secret=True, world=1)
        tally = await self.apply()
        self.assertEqual(len(await self.ties(daughter)), 1)
        last = tally.batches[-1]  # (the relative is kept after the names)
        await self.memory.undo(GUILD, self.sample.id, last, source="dm")
        back = await self.entry(daughter)
        self.assertEqual(back.status, "proposed")
        self.assertFalse(back.hidden_from_players)
        self.assertEqual(await self.ties(daughter), [])  # (one Undo takes both back)

    async def test_a_dry_run_counts_it_and_changes_nothing(self) -> None:
        daughter = await self.relative(self.dorn)
        await self.tie(daughter)
        await self.relative(self.dorn, "son")  # (no tie: counted waiting, as the live run)
        tally = await self.apply(dry_run=True)
        self.assertEqual(tally.accepted[auto.NAMES], 5)
        self.assertEqual(tally.waiting[auto.NAMES], 1)
        self.assertEqual((await self.entry(daughter)).status, "proposed")

    async def test_a_dry_run_counts_a_place_s_unnamed_person_as_the_live_run_does(self) -> None:
        innkeeper = await self.relative(self.oak, "innkeeper")  # (hangs off a place)
        await self.tie(innkeeper, of=self.oak)
        dry = await self.apply(dry_run=True)
        live = await self.apply()
        self.assertEqual(dry.accepted[auto.NAMES], live.accepted[auto.NAMES])
        self.assertEqual((await self.entry(innkeeper)).status, "proposed")

    async def test_the_old_accept_all_is_unchanged(self) -> None:
        daughter = await self.relative(self.dorn)
        tally = await self.apply(narrow=False)
        self.assertEqual(tally.accepted[auto.NAMES] + tally.waiting[auto.NAMES], 5)
        self.assertFalse((await self.entry(daughter)).hidden_from_players)  # (never as kin)
