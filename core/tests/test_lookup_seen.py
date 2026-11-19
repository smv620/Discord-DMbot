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

    async def lookup(self) -> CampaignLookup:
        return CampaignLookup.build(await self.memory.lookup_data(GUILD, self.c))

    async def view(self, user: int) -> CampaignLookup:
        seen = await self.memory.entries_seen_by(GUILD, self.c, user)
        return (await self.lookup()).for_viewer(False, seen)

    async def rows(self) -> int:
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                "SELECT count(*) AS n FROM memory_knowers WHERE guild_id = %s", (GUILD,)
            )
            row = await cur.fetchone()
        return int(row["n"]) if row else 0

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
        whole = await self.lookup()
        self.assertFalse(names_ui.plays_one(whole, NOBODY))
        self.assertTrue(names_ui.plays_one(whole, CARA))
        campaign = await CampaignStore(self.db).get(GUILD, self.c)
        assert campaign is not None
        text, _ = names_ui.home_text(nobody, campaign, 0, no_character=True)
        self.assertIn(names_ui.NO_CHARACTER, text)
        self.assertNotIn("None yet", text)  # (not "add some names": they aren't the DM)
        mine, _ = names_ui.home_text(await self.view(CARA), campaign, 0)
        self.assertNotIn(names_ui.NO_CHARACTER, mine)

    async def test_the_people_at_the_table_stay(self) -> None:
        whole = await self.lookup()
        person: Any = next(iter(whole.names))
        with_people = dataclasses.replace(whole, people_by_key={"anna": (person,)})
        self.assertEqual(
            with_people.for_viewer(False, frozenset()).people_by_key, {"anna": (person,)}
        )

    async def test_no_read_writes_a_row(self) -> None:
        before = await self.rows()
        for user in (CARA, NOBODY):
            await self.view(user)
        self.assertEqual(await self.rows(), before)  # (§9 rule 6)

    async def test_a_read_with_no_viewer_set_says_so_in_the_log(self) -> None:
        whole = await self.lookup()
        with self.assertLogs("dmbot.memory.lookup", "WARNING") as logged:
            whole.for_viewer(False)
        self.assertIn("lookup read with no viewer set", logged.output[0])
        with self.assertNoLogs("dmbot.memory.lookup", "WARNING"):
            whole.for_viewer(True)
            whole.for_viewer(False, frozenset())
