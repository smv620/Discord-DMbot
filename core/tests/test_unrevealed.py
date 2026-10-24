"""Names and connections the story holds back from the players until the table has said them
(#2404, slice 2). A name is "revealed" when the players may know it (not hidden from them); a
connection is revealed when The party believes it. Invented names throughout."""

from __future__ import annotations

import unittest
import uuid
from typing import Any
from unittest.mock import AsyncMock

from dmbot.campaigns import CampaignStore
from dmbot.db import Database, drop_schema
from dmbot.memory import beliefs
from dmbot.memory.models import DM, MemoryRuleError, NewName, new_id
from dmbot.schema import MIGRATIONS
from tests.pg import REQUIRE_DB, TEST_URL
from tests.test_memory_store import DM as DM_USER
from tests.test_memory_store import GUILD_A
from tests.test_web_relationships import Web

BEFORE = "0187_unrevealed_connections"


class Held(Web):
    async def seen(self, who: Any = None, **kw: Any) -> dict[str, dict[str, Any]]:
        got = await self.graph(who, center=self.mira, hops=1, **kw)
        return {x["id"]: x for e in got["edges"] for x in e["links"]}

    async def held_link(self) -> str:
        made = await self.memory.add_relation(
            self.g, self.one.id, self.mira, "owns", self.keep, source=DM, confidence=1.0,
            status="confirmed", unrevealed=True,
        )  # fmt: skip
        return made.value[0].id


class TheConnection(Held):
    async def test_a_held_back_connection_is_not_shown_to_players_until_revealed(self) -> None:
        held = await self.held_link()
        self.assertNotIn(held, await self.seen(self.player))
        self.assertNotIn(held, await self.seen(view="players"))
        full = await self.seen(view="full")
        self.assertIn(held, full)
        self.assertFalse(full[held]["revealed"])  # the DM sees it dimmed
        self.assertTrue(full[self.links["mira-keep"]]["revealed"])
        await self.memory.update_relation(self.g, self.one.id, held, source=DM, unrevealed=False)
        shown = await self.seen(self.player)
        self.assertIn(held, shown)
        self.assertTrue(shown[held]["revealed"])

    async def test_undoing_a_reveal_hides_it_again(self) -> None:
        held = await self.held_link()
        shown = await self.memory.update_relation(
            self.g, self.one.id, held, source=DM, unrevealed=False
        )
        self.assertIn(held, await self.seen(self.player))
        await self.memory.undo(self.g, self.one.id, shown.batch, source=DM)  # type: ignore[arg-type]
        self.assertNotIn(held, await self.seen(self.player))

    async def test_a_connection_waits_for_both_of_its_names(self) -> None:
        other = await self.add("Aldous Pell", "npc", hidden_from_players=True)
        made = await self.memory.add_relation(
            self.g, self.one.id, self.mira, "knows", other, source=DM, confidence=1.0,
            status="confirmed", unrevealed=True,
        )  # fmt: skip
        with self.assertRaises(MemoryRuleError):
            await self.memory.update_relation(
                self.g, self.one.id, made.value[0].id, source=DM, unrevealed=False
            )
        await self.memory.set_hidden_from_players(self.g, self.one.id, other, False, source=DM)
        await self.memory.update_relation(
            self.g, self.one.id, made.value[0].id, source=DM, unrevealed=False
        )

    async def test_only_the_dm_holds_one_back_or_shows_it(self) -> None:
        with self.assertRaises(MemoryRuleError):
            await self.memory.add_relation(
                self.g, self.one.id, self.mira, "owns", self.keep, source="scan",
                confidence=0.5, unrevealed=True,
            )  # fmt: skip
        held = await self.held_link()
        with self.assertRaises(MemoryRuleError):
            await self.memory.update_relation(
                self.g, self.one.id, held, source="scan", unrevealed=False
            )

    async def test_a_secret_one_is_never_shown_by_a_reveal(self) -> None:
        made = await self.memory.add_relation(
            self.g, self.one.id, self.mira, "owns", self.keep, source=DM, confidence=1.0,
            status="confirmed", unrevealed=True, secret=True,
        )  # fmt: skip
        link = made.value[0].id
        await self.memory.update_relation(self.g, self.one.id, link, source=DM, unrevealed=False)
        self.assertNotIn(link, await self.seen(self.player))  # still 🤫

    async def test_the_wording_of_a_held_back_connection_stays_held_back(self) -> None:
        held = await self.held_link()
        got = await self.memory.change_relation_words(
            self.g, self.one.id, held, "enemy_of", "", source=DM
        )
        new = got.value.id
        self.assertNotIn(new, await self.seen(self.player))

    async def test_the_name_card_reader_hides_an_unrevealed_connection_from_players(self) -> None:
        held = await self.held_link()
        shown = await self.memory.relations(self.g, self.one.id, include_secret=False)
        self.assertNotIn(held, {r.id for r in shown})
        dm = await self.memory.relations(self.g, self.one.id, include_secret=True)
        self.assertIn(held, {r.id for r in dm})

    async def test_making_a_held_back_connection_public_reveals_it_once_both_names_are_shown(
        self,
    ) -> None:
        from dmbot.memory import link_edits

        other = await self.add("Aldous Pell", "npc", hidden_from_players=True)
        made = await self.memory.add_relation(
            self.g, self.one.id, self.mira, "knows", other, source=DM, confidence=1.0,
            status="confirmed", unrevealed=True,
        )  # fmt: skip
        link = made.value[0]
        with self.assertRaises(MemoryRuleError):  # a name is still hidden
            await link_edits.set_secret(self.memory, self.g, self.one.id, link, False)
        await self.memory.set_hidden_from_players(self.g, self.one.id, other, False, source=DM)
        await link_edits.set_secret(self.memory, self.g, self.one.id, link, False)
        self.assertIn(link.id, await self.seen(self.player))


class TheNames(Held):
    async def test_a_list_of_names_starts_unrevealed(self) -> None:
        added = await self.memory.add_names(
            self.g, self.one.id, [NewName("Orla Finch", "npc", "confirmed")], source=DM
        )
        (entity_id,) = added.value
        assert entity_id is not None
        entity = await self.memory.entity(self.g, self.one.id, entity_id)
        assert entity is not None
        self.assertTrue(entity.hidden_from_players)

    async def test_revealing_a_name_shows_it_in_the_web_and_undo_hides_it(self) -> None:
        fresh = await self.add("Orla Finch", "npc", hidden_from_players=True)
        await self.link("knows", self.mira, fresh, "orla")

        def names(got: dict[str, Any]) -> dict[str, dict[str, Any]]:
            return {n["id"]: n for n in got["nodes"]}

        theirs = names(await self.graph(self.player, center=self.mira, hops=1))
        self.assertNotIn(fresh, theirs)
        mine = names(await self.graph(center=self.mira, hops=1, view="full"))
        self.assertFalse(mine[fresh]["revealed"])
        shown = await self.memory.set_hidden_from_players(
            self.g, self.one.id, fresh, False, source=DM
        )
        theirs = names(await self.graph(self.player, center=self.mira, hops=1))
        self.assertTrue(theirs[fresh]["revealed"])
        await self.memory.undo(self.g, self.one.id, shown.batch, source=DM)  # type: ignore[arg-type]
        theirs = names(await self.graph(self.player, center=self.mira, hops=1))
        self.assertNotIn(fresh, theirs)


class TheListeners(Held):
    async def test_a_name_made_on_the_spot_in_a_thread_starts_unrevealed(self) -> None:
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, MagicMock, patch

        from dmbot.memory.lookup import CampaignLookup
        from dmbot.ui import threads

        campaign = SimpleNamespace(guild_id=self.g, id=self.one.id)
        lookup = CampaignLookup.build(await self.memory.lookup_data(self.g, self.one.id))
        interaction = MagicMock()
        interaction.client.lookup.get = AsyncMock(return_value=lookup)
        then = AsyncMock()
        with (
            patch.object(
                threads, "_dm_and_memory", AsyncMock(return_value=(campaign, self.memory))
            ),
            patch.object(threads, "changed", MagicMock()),
        ):
            await threads.link_by_name(
                interaction, self.one.id, "t" * 32, "The Ford Fight",
                kinds=None, then=then, make="event",
            )  # fmt: skip
        assert then.await_args is not None
        made = then.await_args.args[3]
        entity = await self.memory.entity(self.g, self.one.id, made)
        assert entity is not None
        self.assertTrue(entity.hidden_from_players)

    async def test_an_unrevealed_name_is_not_secret_for_the_listeners(self) -> None:
        from dmbot.memory.lookup import CampaignLookup

        fresh = await self.add("Orla Finch", "npc", hidden_from_players=True)
        clue = await self.add("The Map", "clue")
        await self.link("knows", self.mira, clue, "knows-clue")
        await self.memory.set_audience_limited(self.g, self.one.id, clue, True, source=DM)
        secret = await self.add("Lord Veil", "npc")
        await self.alias(secret, "The Stranger", secret=True)
        lookup = CampaignLookup.build(await self.memory.lookup_data(self.g, self.one.id))
        by_entity = {e.entity_id: e for e in lookup.names if e.key in ("orla finch", "the map")}
        self.assertFalse(by_entity[fresh].secret)  # the cleaner and the sidebar use it
        self.assertTrue(by_entity[fresh].entry_hidden)
        self.assertTrue(by_entity[clue].secret)  # only a clue (or a 🤫 spelling) stays secret
        self.assertTrue(any(e.secret and e.text == "The Stranger" for e in lookup.names))

    async def test_the_recap_may_use_an_unrevealed_name_only_if_the_table_said_it(self) -> None:
        from types import SimpleNamespace

        from dmbot.memory.lookup import CampaignLookup
        from dmbot.recaps.inputs import recap_name_sets, said_in

        await self.add("Orla Finch", "npc", hidden_from_players=True)
        secret = await self.add("Lord Veil", "npc")
        await self.alias(secret, "The Stranger", secret=True)
        lookup = CampaignLookup.build(await self.memory.lookup_data(self.g, self.one.id))

        def line(text: str, sidebar: str = "") -> Any:
            return SimpleNamespace(heard=text, text=text, sidebar=sidebar)

        allowed, hidden = recap_name_sets(lookup, said_in([line("We met orla finch at the ford")]))
        self.assertIn("Orla Finch", allowed)
        self.assertNotIn("Orla Finch", hidden)
        self.assertIn(
            "The Stranger", hidden
        )  # a 🤫 spelling stays secret unless it is said (#2591)
        for lines in ([line("We met Mira at the ford")], [line("Orla Finch?", sidebar="question")]):
            allowed, hidden = recap_name_sets(lookup, said_in(lines))
            self.assertNotIn("Orla Finch", allowed)  # not said at the table: not for the recap
            self.assertIn("Orla Finch", hidden)
        allowed, hidden = recap_name_sets(lookup, None)  # lines unreadable: stricter, not looser
        self.assertIn("Orla Finch", hidden)
        allowed, hidden = recap_name_sets(lookup, said_in([line("Orla Finchley was there")]))
        self.assertIn("Orla Finch", hidden)  # a whole name, not a prefix of another


class TheNarration(Held):
    async def test_the_audio_check_gets_the_same_lists_as_the_written_recap(self) -> None:
        from types import SimpleNamespace

        from dmbot.bot import DMBot
        from dmbot.memory.lookup import LookupCache

        await self.add("Orla Finch", "npc", hidden_from_players=True)
        sessions = [SimpleNamespace(started_at=100, id="s1")]

        async def run(text: str) -> Any:
            line = SimpleNamespace(heard=text, text=text, sidebar="")
            bot = SimpleNamespace(
                lookup=LookupCache(self.memory),
                transcripts=SimpleNamespace(
                    sessions=AsyncMock(return_value=sessions), lines=AsyncMock(return_value=[line])
                ),
            )
            job = SimpleNamespace(session_at=100)
            return await DMBot.recap_audio_inputs(bot, self.g, self.one.id, job)  # type: ignore[arg-type]

        silent = await run("We rode on")
        assert silent is not None
        self.assertIn("Orla Finch", silent.secret_names)  # not said: not for the narrator
        self.assertNotIn("Orla Finch", silent.allowed_names)
        spoken = await run("We met Orla Finch")
        assert spoken is not None
        self.assertNotIn("Orla Finch", spoken.secret_names)  # said at the table: allowed
        self.assertIn("Orla Finch", spoken.allowed_names)


class TheMigration(unittest.IsolatedAsyncioTestCase):
    """0187 on a table that already holds connections: each reads as revealed, as before."""

    async def asyncSetUp(self) -> None:
        if not TEST_URL:
            if REQUIRE_DB:
                self.fail("DMBOT_TEST_DATABASE_URL is needed")
            self.skipTest("set DMBOT_TEST_DATABASE_URL to test the migration")
        self.schema = f"t_{uuid.uuid4().hex[:16]}"
        self.before = tuple(m for m in MIGRATIONS if m[0] != BEFORE)
        self.db = await Database.open(
            TEST_URL, schema=self.schema, max_size=2, migrations=self.before
        )

    async def asyncTearDown(self) -> None:
        await self.db.close()
        await drop_schema(TEST_URL, self.schema)

    async def test_existing_connections_read_as_revealed(self) -> None:
        campaign = await CampaignStore(self.db).create(GUILD_A, "Frostmaiden", DM_USER)
        a, b, link = new_id(), new_id(), new_id()
        async with self.db.guild(GUILD_A) as conn:
            for key, name in ((a, "Aaa"), (b, "Bbb")):
                await conn.execute(
                    "INSERT INTO memory_entities (guild_id, campaign_id, id, type, name,"
                    " description, status, source, created_at)"
                    " VALUES (%s, %s, %s, 'npc', %s, '', 'confirmed', 'dm', 1)",
                    (GUILD_A, campaign.id, key, name),
                )
            await conn.execute(
                "INSERT INTO memory_relations (guild_id, campaign_id, id, subject_id,"
                " predicate, object_id, detail, confidence, status, source, mention_ids,"
                " secret, created_at, definition_version)"
                " VALUES (%s, %s, %s, %s, 'knows', %s, '', 1.0, 'confirmed', 'dm', '{}', false,"
                " 1, 1)",
                (GUILD_A, campaign.id, link, a, b),
            )
        await self.db.close()
        self.db = await Database.open(
            TEST_URL, schema=self.schema, max_size=2, migrations=tuple(MIGRATIONS)
        )
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute(
                "SELECT unrevealed FROM memory_relations WHERE id = %s", (link,)
            )
            row = await cur.fetchone()
        assert row is not None
        self.assertFalse(row["unrevealed"])
        self.assertTrue(callable(beliefs.party_knows))


if __name__ == "__main__":
    unittest.main()
