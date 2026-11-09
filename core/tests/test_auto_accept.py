"""What DMbot is sure of (#3878): the shared step that adds the names, other names and connections
a reading found, as the DM's own calls, and leaves the unsure ones as questions. Invented names."""

from __future__ import annotations

from typing import Any, cast

from dmbot.memory import auto_accept as auto
from dmbot.memory.backstory_links import NewLink, NewWhich
from dmbot.memory.models import DM as DM_SOURCE
from dmbot.memory.models import MoreNames
from dmbot.ui.names import background_kind
from tests.test_accept_all import Base
from tests.test_memory_names import GUILD


class Sure(Base):
    async def apply(self, **kw: object) -> auto.Tally:
        tally = auto.Tally()
        args = {"narrow": True, "kind_of": background_kind}
        await auto.accept_names(self.memory, GUILD, self.sample.id, tally, **{**args, **kw})  # type: ignore[arg-type]
        return tally

    async def test_what_the_reader_is_sure_of_is_added_with_its_kind(self) -> None:
        tally = await self.apply()
        self.assertEqual(tally.accepted[auto.NAMES], 4)
        self.assertEqual(dict(tally.kinds), {"npc": 3, "place": 1})
        self.assertEqual(len(tally.batches), 4)  # (each its own change, kept to undo together)
        self.assertEqual(set((await self.statuses()).values()), {"confirmed"})

    async def test_a_player_character_stays_a_question(self) -> None:
        await self.read_name("Wen", "character", "player_character")
        tally = await self.apply()
        self.assertEqual(tally.waiting[auto.NAMES], 1)
        self.assertEqual((await self.statuses())["Wen"], "proposed")

    async def test_a_described_person_is_not_a_sure_name(self) -> None:
        made = await self.memory.add_unnamed(
            GUILD, self.sample.id, what="a woman with a scar", role="guard"
        )
        tally = await self.apply()
        self.assertEqual(tally.accepted[auto.NAMES], 4)  # (only the four read names)
        self.assertEqual(tally.waiting[auto.NAMES], 1)
        statuses = {e.id: e.status for e in await self.memory.entities(GUILD, self.sample.id)}
        self.assertEqual(statuses[made.entity.id], "proposed")

    async def test_even_with_a_kind_a_described_person_waits_when_narrowed(self) -> None:
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        woman = SimpleNamespace(
            id="u", name="The woman", type="character", role="npc", unnamed=True, source="x"
        )
        memory = SimpleNamespace(
            entities=AsyncMock(return_value=[woman]), confirm_entity=AsyncMock()
        )
        for narrow, accepted in ((True, 0), (False, 1)):
            tally = auto.Tally()
            await auto.accept_names(
                cast(Any, memory),
                GUILD,
                "c",
                tally,
                narrow=narrow,
                kind_of=lambda e: "npc",
            )
            self.assertEqual(tally.accepted[auto.NAMES], accepted)

    async def test_a_creature_kind_or_a_rules_word_is_a_question_not_a_fact(self) -> None:
        await self.read_name("Owlbear", "character", "npc")
        await self.read_name("Initiative", "place")
        tally = await self.apply()
        self.assertEqual(tally.waiting[auto.NAMES], 2)
        found = await self.statuses()
        self.assertEqual((found["Owlbear"], found["Initiative"]), ("proposed", "proposed"))

    async def test_the_old_accept_all_still_takes_them_all(self) -> None:
        await self.read_name("Owlbear", "character", "npc")
        tally = await self.apply(narrow=False)
        self.assertEqual(tally.accepted[auto.NAMES], 5)

    async def test_only_the_readings_own_names_are_touched(self) -> None:
        tally = await self.apply(only={self.mira, self.oak})
        self.assertEqual(tally.accepted[auto.NAMES], 2)
        found = await self.statuses()
        self.assertEqual((found["Dorn"], found["Mirelle"]), ("proposed", "confirmed"))

    async def test_other_names_of_the_readings_entries_are_added(self) -> None:
        await self.memory.confirm_entity(
            GUILD, self.sample.id, self.mira, "character", source=DM_SOURCE, role="npc"
        )
        await self.memory.add_names(
            GUILD, self.sample.id, [], source=DM_SOURCE,
            more=[MoreNames(self.mira, ("Mira",), (), proposed=True)],
        )  # fmt: skip
        tally = auto.Tally()
        await auto.accept_other_names(
            self.memory, GUILD, self.sample.id, tally, only_entries={self.dorn}
        )
        self.assertEqual(tally.accepted[auto.OTHER_NAMES], 0)  # (another entry's)
        await auto.accept_other_names(self.memory, GUILD, self.sample.id, tally)
        self.assertEqual(tally.accepted[auto.OTHER_NAMES], 1)

    async def test_connections_found_in_the_text_are_kept_when_both_names_are_answered(
        self,
    ) -> None:
        await self.apply()
        tally = auto.Tally()
        await auto.accept_connections(
            self.memory, self.links, GUILD, self.sample.id, tally, upload="a file, 1 Jan"
        )
        self.assertEqual(tally.accepted[auto.CONNECTIONS], 1)
        self.assertEqual(await self.links.count(GUILD, self.sample.id), 0)

    async def test_a_connection_from_another_upload_is_left_alone(self) -> None:
        await self.apply()
        tally = auto.Tally()
        await auto.accept_connections(
            self.memory, self.links, GUILD, self.sample.id, tally, upload="another file"
        )
        self.assertEqual(tally.accepted[auto.CONNECTIONS], 0)
        self.assertEqual(await self.links.count(GUILD, self.sample.id), 1)

    async def test_which_one_questions_are_never_answered_here(self) -> None:
        await self.apply()
        await self.links.add_which(
            GUILD, self.sample.id,
            [NewWhich(self.oak, False, "knows", "Dor", (self.dorn, self.dorna), "q")],
        )  # fmt: skip
        tally = auto.Tally()
        await auto.accept_connections(self.memory, self.links, GUILD, self.sample.id, tally)
        self.assertEqual(await self.links.count_which(GUILD, self.sample.id), 1)

    async def test_a_connection_whose_name_is_still_waiting_waits(self) -> None:
        await self.links.add(
            GUILD, self.sample.id, [NewLink(self.mira, "knows", self.dorn, "q", upload="a file")]
        )
        tally = auto.Tally()
        await auto.accept_connections(self.memory, self.links, GUILD, self.sample.id, tally)
        self.assertEqual(tally.waiting[auto.CONNECTIONS], 2)
        self.assertEqual(tally.accepted[auto.CONNECTIONS], 0)

    async def test_a_dry_run_changes_nothing(self) -> None:
        tally = await self.apply(dry_run=True)
        self.assertEqual(tally.accepted[auto.NAMES], 4)
        self.assertEqual(set((await self.statuses()).values()), {"proposed"})
