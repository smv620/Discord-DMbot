"""The names lists ask the gate, not the flags (#4537, party knowledge slice 5b; Supervisor's
rulings O and P): a person who isn't a DM sees the names of what their own characters know
(`MemoryStore.entries_seen_by`), someone with no character sees common knowledge only and is told
why, the DM-only types never show, the people at the table stay, and no read writes a row.
Invented names and ids only."""

from __future__ import annotations

import dataclasses
from typing import Any

from dmbot.campaigns import CampaignStore
from dmbot.memory.lookup import CampaignLookup
from dmbot.memory.models import CONFIRMED, DM
from dmbot.ui import names as names_ui
from tests.test_party_join import CARA, GUILD, Party

NOBODY = 700_000_000_000_000_199  # (in the server, plays no character here)


class TheNames(Party):
    """Vell and Tuck know the harbourmaster; Cara joined and hasn't been told anything yet."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        async with self.db.guild(GUILD) as conn:  # (the flag says "shown": a DM-added name starts
            await conn.execute(  # unseen, #2404; the who-knows rows are unchanged)
                "UPDATE memory_entities SET hidden_from_players = false WHERE id = %s", (self.npc,)
            )
        self.cara = await self.pc("Cara", CARA)
        made = await self.memory.add_entity(
            GUILD, self.c, name="The drowned bell", type="quest", status=CONFIRMED, source=DM
        )
        self.thread = str(made.value.id)  # (a story thread: the DM's plot note)
        made = await self.memory.add_entity(
            GUILD, self.c, name="Saltmarsh", type="place", status=CONFIRMED, source=DM
        )
        self.world = str(made.value.id)
        await self.memory.add_common_knowledge(GUILD, self.c, "entry", [self.world], source=DM)

    async def lookup(self) -> CampaignLookup:
        return CampaignLookup.build(await self.memory.lookup_data(GUILD, self.c))

    async def view(self, user: int) -> CampaignLookup:
        seen = await self.memory.entries_seen_by(GUILD, self.c, user)
        return (await self.lookup()).for_viewer(False, seen)

    async def rows(self) -> tuple[int, int]:
        """Who-knows rows and change-log rows: a write anywhere shows in one of them."""
        out = []
        async with self.db.guild(GUILD) as conn:
            for table in ("memory_knowers", "memory_changes"):
                cur = await conn.execute(
                    f"SELECT count(*) AS n FROM {table} WHERE guild_id = %s",
                    (GUILD,),
                )
                row = await cur.fetchone()
                out.append(int(row["n"]) if row else 0)
        return out[0], out[1]

    async def test_a_newcomer_not_told_isnt_shown_it_while_the_flag_says_shown(self) -> None:
        from tests.test_party_join import ANNA

        self.assertIn(self.npc, (await self.view(ANNA)).entities)  # (Vell knows it)
        cara = await self.view(CARA)
        self.assertNotIn(self.npc, cara.entities)
        self.assertIn(self.cara, cara.entities)  # (a character knows its own entry)
        self.assertFalse((await self.lookup()).entities[self.npc].hidden_from_players)  # (the flag)

    async def test_the_dm_only_types_never_show(self) -> None:
        from tests.test_party_join import ANNA

        seen = await self.memory.entries_seen_by(GUILD, self.c, ANNA)
        whole = await self.lookup()
        self.assertNotIn(self.thread, whole.for_viewer(False, seen | {self.thread}).entities)

    async def test_someone_with_no_character_sees_common_knowledge_and_is_told_why(self) -> None:
        nobody = await self.view(NOBODY)
        self.assertNotIn(self.npc, nobody.entities)
        self.assertIn(self.world, nobody.entities)  # (what everyone knows: not an empty answer)
        self.assertEqual(await self.memory.characters_of(GUILD, self.c, NOBODY), [])
        campaign = await CampaignStore(self.db).get(GUILD, self.c)
        assert campaign is not None
        text, _ = names_ui.home_text(nobody, campaign, 0, no_character=True)
        self.assertIn(names_ui.NO_CHARACTER, text)
        self.assertNotIn("None yet", text)  # (not "add some names": they aren't the DM)
        empty = dataclasses.replace(nobody, entities={})  # (nobody knows any name yet)
        bare, _ = names_ui.home_text(empty, campaign, 0, no_character=True)
        self.assertIn(names_ui.NO_CHARACTER, bare)
        for word in ("0 names", "these names", "None yet"):
            self.assertNotIn(word, bare)
        mine, _ = names_ui.home_text(await self.view(CARA), campaign, 0)
        self.assertNotIn(names_ui.NO_CHARACTER, mine)

    async def test_the_people_at_the_table_stay(self) -> None:
        whole = await self.lookup()
        person: Any = next(iter(whole.names))
        with_people = dataclasses.replace(
            whole, people_by_key={"anna": (person,)}, people_by_sound={"AN": (person,)}
        )
        self.assertEqual(
            with_people.for_viewer(False, frozenset()).people_by_key, {"anna": (person,)}
        )
        self.assertEqual(
            with_people.for_viewer(False, frozenset()).people_by_sound, {"AN": (person,)}
        )

    async def test_no_read_writes_a_row(self) -> None:
        before = await self.rows()
        for user in (CARA, NOBODY):
            await self.view(user)
        self.assertEqual(await self.rows(), before)  # (§9 rule 6)

    async def test_a_read_for_a_player_without_seen_is_a_callers_mistake(self) -> None:
        # (#4802, ruling P: no fallback to the flags; the error names the caller, counts only)
        whole = await self.lookup()
        with self.assertRaises(ValueError) as raised:
            whole.for_viewer(False)
        said = str(raised.exception)
        self.assertIn("test_lookup_seen.py:", said)
        self.assertIn(f"{len(whole.entities)} entries", said)
        self.assertNotIn("harbourmaster", said.casefold())  # (no name, ever)
        whole.for_viewer(True)  # (a DM needs no `seen`)
        whole.for_viewer(False, frozenset())

    def interaction(self, user: int) -> Any:
        from types import SimpleNamespace

        from dmbot.memory.lookup import LookupCache

        bot = SimpleNamespace(lookup=LookupCache(self.memory), memory=self.memory)
        return SimpleNamespace(client=bot, user=SimpleNamespace(id=user), guild_id=GUILD)

    async def test_each_list_a_player_opens_holds_only_what_the_gate_gives(self) -> None:
        # (#4802 A: the name card's and the names lists' loaders, and the type-ahead, ask
        # `entries_seen_by` for anyone who isn't a DM, as `ui/names.py` does)
        from unittest.mock import AsyncMock, patch

        from dmbot.ui import name_card, name_lists

        campaign = await CampaignStore(self.db).get(GUILD, self.c)
        assert campaign is not None
        held = (await self.view(CARA)).entities.keys()
        for load in (name_card._names, name_lists._names):
            with self.subTest(load=load.__module__):
                got = await load(self.interaction(CARA), campaign)
                assert got is not None
                self.assertEqual(got.entities.keys(), held)
                self.assertNotIn(self.npc, got.entities)  # (Cara hasn't been told of it)
        with patch.object(name_card, "typeahead_campaign", AsyncMock(return_value=campaign)):
            picked = await name_card.find_typeahead(self.interaction(CARA), "")
            harbour = await name_card.find_typeahead(self.interaction(CARA), "harbour")
        whole = await self.lookup()
        shown = {str(c.value) for c in picked}
        self.assertTrue(shown <= set(held), shown)
        self.assertEqual(harbour, [])  # (the name she doesn't hold is never suggested)
        self.assertIn(self.npc, whole.entities)
