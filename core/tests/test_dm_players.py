"""A DM's remembered players (#4226; migration 0450): kept on the DM's account across campaigns and
servers, written whenever a player is linked to a confirmed player character in a campaign the DM
runs, read only by that DM (and, as "DMs who remember you", by the player), and removed by the
player's or the DM's account deletion or by hand; "Stop recording me" and a deleted campaign leave
it alone. Real Postgres; invented names and ids."""

from __future__ import annotations

from typing import Any

from dmbot.campaigns import CampaignStore
from dmbot.consent import ConsentStore
from dmbot.db import Database, drop_schema
from dmbot.members import MemberDirectory
from dmbot.schema import MIGRATIONS
from tests.pg import TEST_URL
from tests.test_memory_store import DM, GUILD_A, GUILD_B, MemoryTest

DM2 = 8
ALICE = 111111111111111234
BOB = 222222222222225678


class Players(MemoryTest):
    async def play(
        self, user: int, name: str, *, campaign: str | None = None, guild: int = GUILD_A,
        status: str = "confirmed", kind: str = "player_character",
    ) -> str:  # fmt: skip
        written = await self.memory.add_entity(
            guild, campaign or self.c, type=kind, name=name, source="dm", status=status,
            role="player_character" if kind == "player_character" else None,
            played_by=user if kind == "player_character" else None,
        )  # fmt: skip
        return str(written.value.id)

    async def rows(self) -> list[dict[str, Any]]:
        async with self.db.unscoped() as conn:
            # (the table is forced-RLS: only the functions below ever see its rows, so a test
            # reads it through the doors, as the website does)
            cur = await conn.execute("SELECT 1")
            await cur.fetchall()
        return []

    async def door(self, sql: str, user: int, *args: Any) -> Any:
        async with self.db.user(user) as conn:
            cur = await conn.execute(f"SELECT {sql} AS found", args)
            row = await cur.fetchone()
        return row["found"] if row else None

    async def listed(self, dm: int = DM) -> list[dict[str, Any]]:
        found = await self.door("dmbot_web_dm_players_list()", dm)
        return list(found or [])

    async def second_campaign(self, dm: int = DM, guild: int = GUILD_A) -> str:
        return (await self.campaigns.create(guild, "Second", dm)).id


class TheTrigger(Players):
    async def test_linking_a_player_to_a_character_remembers_them_for_the_dm(self) -> None:
        await self.play(ALICE, "Cerric")
        [row] = await self.listed()
        self.assertEqual((row["player"], row["campaigns"]), (str(ALICE), 1))
        self.assertGreater(row["since"], 0)

    async def test_the_name_is_the_directory_name_when_there_is_one(self) -> None:
        await ConsentStore(self.db).grant(GUILD_A, ALICE)
        await MemberDirectory(self.db).remember(GUILD_A, ALICE, "Eric", "eric_92")
        await self.play(ALICE, "Cerric")
        [row] = await self.listed()
        self.assertEqual(row["name"], "Eric")

    async def test_a_second_character_in_one_campaign_is_not_a_second_campaign(self) -> None:
        await self.play(ALICE, "Cerric")
        await self.play(ALICE, "Wit")
        [row] = await self.listed()
        self.assertEqual(row["campaigns"], 1)

    async def test_another_campaign_counts_even_in_another_server(self) -> None:
        await self.play(ALICE, "Cerric")
        other = await self.second_campaign(DM, GUILD_B)
        await self.play(ALICE, "Wit", campaign=other, guild=GUILD_B)
        [row] = await self.listed()
        self.assertEqual(row["campaigns"], 2)

    async def test_every_dm_of_the_campaign_remembers_them(self) -> None:
        await self.campaigns.add_dm(GUILD_A, self.c, DM2)
        await self.play(ALICE, "Cerric")
        self.assertEqual([r["player"] for r in await self.listed(DM)], [str(ALICE)])
        self.assertEqual([r["player"] for r in await self.listed(DM2)], [str(ALICE)])

    async def test_only_a_confirmed_player_character_with_a_player_counts(self) -> None:
        await self.play(ALICE, "Maybe", status="proposed")  # not confirmed
        await self.play(BOB, "Local", kind="npc")  # not a player character
        await self.add("Belleros")  # no player
        self.assertEqual(await self.listed(), [])

    async def test_a_proposed_character_counts_once_it_is_confirmed(self) -> None:
        entity = await self.play(ALICE, "Maybe", status="proposed")
        self.assertEqual(await self.listed(), [])
        await self.memory.confirm_entity(
            GUILD_A, self.c, entity, "player_character", source="dm", played_by=ALICE
        )
        self.assertEqual([r["player"] for r in await self.listed()], [str(ALICE)])

    async def test_the_dm_playing_in_their_own_campaign_is_not_remembered(self) -> None:
        await self.play(DM, "Own")
        self.assertEqual(await self.listed(), [])

    async def test_it_survives_the_campaign_being_deleted(self) -> None:
        await self.play(ALICE, "Cerric")
        await self.campaigns.delete(GUILD_A, self.c)
        self.assertEqual([r["player"] for r in await self.listed()], [str(ALICE)])

    async def test_stopping_does_not_remove_it(self) -> None:
        await self.play(ALICE, "Cerric")
        await ConsentStore(self.db).grant(GUILD_A, ALICE)
        await ConsentStore(self.db).revoke(GUILD_A, ALICE)
        await self.memory.forget_learned(GUILD_A, ALICE)
        self.assertEqual([r["player"] for r in await self.listed()], [str(ALICE)])


class TheDoors(Players):
    async def test_a_dm_sees_only_their_own_list(self) -> None:
        await self.play(ALICE, "Cerric")
        other = await self.second_campaign(DM2)
        await self.play(BOB, "Wit", campaign=other)
        self.assertEqual([r["player"] for r in await self.listed(DM)], [str(ALICE)])
        self.assertEqual([r["player"] for r in await self.listed(DM2)], [str(BOB)])

    async def test_the_picker_mark_is_true_only_for_the_dms_own_players(self) -> None:
        await self.play(ALICE, "Cerric")
        self.assertTrue(await self.door("dmbot_dm_remembers(%s)", DM, ALICE))
        self.assertFalse(await self.door("dmbot_dm_remembers(%s)", DM, BOB))
        self.assertFalse(await self.door("dmbot_dm_remembers(%s)", DM2, ALICE))  # not their list

    async def test_forgetting_a_player_removes_only_that_one_from_that_dms_list(self) -> None:
        await self.campaigns.add_dm(GUILD_A, self.c, DM2)
        await self.play(ALICE, "Cerric")
        await self.play(BOB, "Wit")
        done = await self.door("dmbot_web_dm_players_forget(%s)", DM, ALICE)
        self.assertEqual(done, {"status": "ok", "removed": 1})
        self.assertEqual([r["player"] for r in await self.listed(DM)], [str(BOB)])
        self.assertEqual(len(await self.listed(DM2)), 2)  # (the other DM's list is theirs)

    async def test_a_player_sees_which_dms_remember_them_and_clears_one_or_all(self) -> None:
        await self.campaigns.add_dm(GUILD_A, self.c, DM2)
        await self.play(ALICE, "Cerric")
        seen = await self.door("dmbot_web_players_dms()", ALICE)
        self.assertEqual({r["dm"] for r in seen}, {str(DM), str(DM2)})
        self.assertEqual(await self.door("dmbot_web_players_dms()", BOB), [])  # not theirs
        done = await self.door("dmbot_web_players_clear(%s)", ALICE, DM2)
        self.assertEqual(done["removed"], 1)
        self.assertEqual([r["dm"] for r in await self.door("dmbot_web_players_dms()", ALICE)],
                         [str(DM)])  # fmt: skip
        self.assertEqual((await self.door("dmbot_web_players_clear(%s)", ALICE, 0))["removed"], 1)
        self.assertEqual(await self.door("dmbot_web_players_dms()", ALICE), [])

    async def test_nobody_can_clear_a_row_that_names_neither_them(self) -> None:
        await self.play(ALICE, "Cerric")
        self.assertEqual((await self.door("dmbot_web_players_clear(%s)", BOB, 0))["removed"], 0)
        self.assertEqual(
            (await self.door("dmbot_web_dm_players_forget(%s)", DM2, ALICE))["removed"], 0
        )
        self.assertEqual(len(await self.listed()), 1)

    async def test_the_website_role_cannot_read_the_table_directly(self) -> None:
        await self.play(ALICE, "Cerric")
        async with self.db.user(DM) as conn:
            cur = await conn.execute("SELECT * FROM dm_players")
            self.assertEqual(await cur.fetchall(), [])  # (forced RLS: no policy opens it)


class TheAccount(Players):
    async def forget(self, user: int) -> None:
        async with self.db.user(user) as conn:
            await conn.execute("SELECT dmbot_forget_dm_players()")

    async def test_a_players_account_deletion_removes_them_from_every_dms_list(self) -> None:
        await self.campaigns.add_dm(GUILD_A, self.c, DM2)
        await self.play(ALICE, "Cerric")
        await self.play(BOB, "Wit")
        await self.forget(ALICE)
        self.assertEqual([r["player"] for r in await self.listed(DM)], [str(BOB)])
        self.assertEqual([r["player"] for r in await self.listed(DM2)], [str(BOB)])

    async def test_a_dms_account_deletion_removes_their_list_and_not_another_dms(self) -> None:
        await self.campaigns.add_dm(GUILD_A, self.c, DM2)
        await self.play(ALICE, "Cerric")
        await self.forget(DM)
        self.assertEqual(await self.listed(DM), [])
        self.assertEqual([r["player"] for r in await self.listed(DM2)], [str(ALICE)])

    async def test_account_deletion_calls_it(self) -> None:
        import inspect

        from dmbot import accounts

        self.assertIn("dmbot_forget_dm_players", inspect.getsource(accounts.delete_person))


class TheNames(Players):
    async def test_the_bots_name_refresh_changes_every_dms_row_for_that_player(self) -> None:
        await self.campaigns.add_dm(GUILD_A, self.c, DM2)
        await self.play(ALICE, "Cerric")
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute("SELECT dmbot_dm_players_name(%s, %s)", (ALICE, "Eric (eric_92)"))
        for dm in (DM, DM2):
            self.assertEqual([r["name"] for r in await self.listed(dm)], ["Eric (eric_92)"])


class Backfill0450(MemoryTest):
    """Migrations run with no server set: the backfill must open every table it reads and the
    one it writes, or the list is empty on day one."""

    async def test_campaigns_that_already_exist_fill_the_list(self) -> None:
        before = [m for m in MIGRATIONS if m[0] < "0450"]
        await self.db.close()
        await drop_schema(TEST_URL, self.schema)
        self.db = await Database.open(TEST_URL, schema=self.schema, migrations=before)
        store = CampaignStore(self.db, clock=lambda: 1)
        first = (await store.create(GUILD_A, "One", DM)).id
        second = (await store.create(GUILD_B, "Two", DM)).id
        async with self.db.guild(GUILD_A) as conn:
            for eid, name, user, campaign, status in (
                ("a" * 32, "Cerric", ALICE, first, "confirmed"),
                ("b" * 32, "Maybe", BOB, first, "proposed"),
            ):
                await conn.execute(
                    "INSERT INTO memory_entities (guild_id, campaign_id, id, type, name,"
                    " description, status, source, created_at, played_by, role, needs_look)"
                    " VALUES (%s, %s, %s, 'character', %s, '', %s, 'dm', 5, %s,"
                    " 'player_character', false)",
                    (GUILD_A, campaign, eid, name, status, user),
                )
        async with self.db.guild(GUILD_B) as conn:
            await conn.execute(
                "INSERT INTO memory_entities (guild_id, campaign_id, id, type, name,"
                " description, status, source, created_at, played_by, role, needs_look)"
                " VALUES (%s, %s, %s, 'character', 'Wit', '', 'confirmed', 'dm', 9, %s,"
                " 'player_character', false)",
                (GUILD_B, second, "c" * 32, ALICE),
            )
        await self.db.migrate()
        async with self.db.user(DM) as conn:
            cur = await conn.execute("SELECT dmbot_web_dm_players_list() AS found")
            row = await cur.fetchone()
        assert row is not None
        [mine] = row["found"]  # (Bob's proposed character is not a player yet)
        self.assertEqual((mine["player"], mine["campaigns"]), (str(ALICE), 2))
        self.assertEqual((mine["since"], mine["last"]), (5, 9))
        async with self.db.unscoped() as conn:
            cur = await conn.execute(
                "SELECT count(*) AS n FROM pg_policies WHERE policyname = 'migrate_backfill'"
                " AND schemaname = current_schema()"
            )
            self.assertEqual((await cur.fetchone() or {})["n"], 0)  # no opening left
