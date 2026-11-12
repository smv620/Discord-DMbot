"""People DMbot knows only by number get their Discord names (#4225): a placeholder entry is
renamed after one lookup by id (no privileged intent), the player's own listened-for name changes
with it, a name the DM wrote is never touched, someone who left is worded so it is no name, the
sweep is slow and rate limited, and the log holds counts only. Real Postgres; invented names."""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord

from dmbot import member_names
from dmbot.consent import ConsentStore
from dmbot.members import MemberDirectory
from dmbot.memory.store import left_name
from tests.test_memory_store import GUILD_A, MemoryTest

ALICE = 111111111111111234
BOB = 222222222222225678
CARL = 333333333333339999


def member(display: str, name: str, *, bot: bool = False) -> Any:
    return SimpleNamespace(display_name=display, name=name, bot=bot)


class Names(MemoryTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.found: dict[int, Any] = {}
        self.asked: list[int] = []
        self.errors: dict[int, Exception] = {}

        async def fetch_member(uid: int) -> Any:
            self.asked.append(uid)
            if uid in self.errors:
                raise self.errors[uid]
            if uid not in self.found:
                raise discord.NotFound(MagicMock(status=404, reason="x"), "gone")
            return self.found[uid]

        self.guild = SimpleNamespace(
            id=GUILD_A,
            get_member=lambda uid: None,
            fetch_member=AsyncMock(side_effect=fetch_member),
        )
        self.directory = MemberDirectory(self.db)
        self.bot = SimpleNamespace(
            memory=self.memory,
            members=self.directory,
            get_guild=lambda gid: self.guild if gid == GUILD_A else None,
            _closing=False,
        )
        self.skip: dict[tuple[int, int], float] = {}

    async def play(self, user: int, name: str, campaign: str | None = None) -> str:
        return await self.add(
            name, type="player_character", played_by=user, campaign=campaign, status="confirmed"
        )

    async def person(self, user: int, campaign: str | None = None) -> Any:
        found = await self.memory.entities(GUILD_A, campaign or self.c)
        return next(e for e in found if e.type == "person" and e.played_by == user)

    async def sql(self, sql: str, *args: Any) -> None:
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(sql, args)

    async def aliases(self, entity: str) -> list[str]:
        return sorted(a.text for a in await self.memory.aliases(GUILD_A, self.c, entity_id=entity))

    async def sweep(self) -> int:
        return await member_names.run(self.bot, GUILD_A, self.skip, now=1000.0)


class TheStore(Names):
    async def test_a_placeholder_is_renamed_and_its_own_name_changes_with_it(self) -> None:
        await self.play(ALICE, "Cerric")
        self.assertEqual((await self.person(ALICE)).name, "Player 1234")
        self.assertTrue(await self.memory.name_person(GUILD_A, self.c, ALICE, "Eric (eric_92)"))
        person = await self.person(ALICE)
        self.assertEqual(person.name, "Eric (eric_92)")
        self.assertEqual(await self.aliases(person.id), ["Eric (eric_92)"])  # (no "Player 1234")

    async def test_a_name_the_dm_wrote_is_never_replaced(self) -> None:
        await self.play(ALICE, "Cerric")
        person = await self.person(ALICE)
        await self.memory.rename_entity(GUILD_A, self.c, person.id, "Alice Vane", source="dm")
        self.assertFalse(await self.memory.name_person(GUILD_A, self.c, ALICE, "Eric"))
        self.assertEqual((await self.person(ALICE)).name, "Alice Vane")

    async def test_the_same_name_again_changes_nothing(self) -> None:
        await self.play(ALICE, "Cerric")
        await self.memory.name_person(GUILD_A, self.c, ALICE, "Eric")
        self.assertFalse(await self.memory.name_person(GUILD_A, self.c, ALICE, "Eric"))

    async def test_a_left_person_can_be_named_when_they_come_back(self) -> None:
        await self.play(ALICE, "Cerric")
        await self.memory.name_person(GUILD_A, self.c, ALICE, left_name(ALICE))
        self.assertEqual((await self.person(ALICE)).name, "Someone who left the server (…1234)")
        self.assertTrue(await self.memory.name_person(GUILD_A, self.c, ALICE, "Eric"))
        self.assertEqual((await self.person(ALICE)).name, "Eric")

    async def test_the_rename_is_one_logged_change_with_one_undo(self) -> None:
        await self.play(ALICE, "Cerric")
        before = await self.snapshot()
        await self.memory.name_person(GUILD_A, self.c, ALICE, "Eric")
        self.assertNotEqual(await self.snapshot(), before)
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute(
                "SELECT max(batch) AS b, count(DISTINCT batch) AS n FROM memory_changes"
                " WHERE guild_id = %s AND campaign_id = %s AND source = 'scan'",
                (GUILD_A, self.c),
            )
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual(row["n"], 1)  # (one change for the rename)
        await self.memory.undo(GUILD_A, self.c, int(row["b"]))
        self.assertEqual(await self.snapshot(), before)

    async def test_only_placeholders_are_found(self) -> None:
        await self.play(ALICE, "Cerric")
        self.assertEqual(await self.memory.placeholder_people(GUILD_A, 10), [ALICE])
        await self.memory.name_person(GUILD_A, self.c, ALICE, "Eric")
        self.assertEqual(await self.memory.placeholder_people(GUILD_A, 10), [])


class TheConsent(Names):
    async def refuse(self, user: int, kind: str = "stopped") -> None:
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "INSERT INTO consent_refusals (guild_id, user_id, kind, refused_at)"
                " VALUES (%s, %s, %s, %s)",
                (GUILD_A, user, kind, 1),
            )

    async def test_someone_who_said_no_or_stopped_is_never_named(self) -> None:
        await self.play(ALICE, "Cerric")
        await self.play(BOB, "Wit")
        await self.refuse(ALICE, "stopped")
        await self.refuse(BOB, "declined")
        self.found[ALICE] = member("Eric", "eric_92")
        self.found[BOB] = member("Bobby", "bob_b")
        self.assertEqual(await self.sweep(), 0)
        self.assertEqual(self.asked, [])  # (not even looked up)
        self.assertEqual((await self.person(ALICE)).name, "Player 1234")

    async def test_stopping_puts_the_placeholder_back_on_an_entry_that_stays(self) -> None:
        pc = await self.play(ALICE, "Cerric")
        self.found[ALICE] = member("Eric", "eric_92")
        await self.sweep()
        person = await self.person(ALICE)
        self.assertEqual(person.name, "Eric (eric_92)")
        await self.sql(
            "UPDATE memory_entities SET description = 'The player' WHERE id = %s", person.id
        )
        await self.memory.forget_learned(GUILD_A, ALICE)
        again = await self.person(ALICE)
        self.assertEqual(again.name, "Player 1234")  # (kept for the DM's own words, not the name)
        self.assertEqual(await self.aliases(again.id), ["Player 1234"])
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute(
                "SELECT count(*) AS n FROM memory_changes WHERE guild_id = %s"
                " AND (before::text LIKE '%%eric_92%%' OR after::text LIKE '%%eric_92%%')",
                (GUILD_A,),
            )
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual(row["n"], 0)  # (the log no longer holds the name)
        self.assertTrue(pc)

    async def test_stopping_with_nothing_else_on_the_entry_leaves_no_discord_name(self) -> None:
        await self.play(ALICE, "Cerric")  # (the DM's own assignment made this entry)
        self.found[ALICE] = member("Eric", "eric_92")
        await self.sweep()
        await self.memory.forget_learned(GUILD_A, ALICE)
        found = [e for e in await self.memory.entities(GUILD_A, self.c) if e.type == "person"]
        self.assertTrue(all("eric" not in e.name.lower() for e in found))

    async def test_a_name_the_dm_chose_stays_when_the_person_stops(self) -> None:
        await self.play(ALICE, "Cerric")
        person = await self.person(ALICE)
        await self.memory.rename_entity(GUILD_A, self.c, person.id, "Alice Vane", source="dm")
        await self.memory.forget_learned(GUILD_A, ALICE)
        self.assertEqual((await self.person(ALICE)).name, "Alice Vane")


class TheSweep(Names):
    async def test_an_undone_rename_is_not_done_again_by_the_next_round(self) -> None:
        await self.play(ALICE, "Cerric")
        self.found[ALICE] = member("Eric", "eric_92")
        await self.sweep()
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute(
                "SELECT max(batch) AS b FROM memory_changes WHERE guild_id = %s"
                " AND campaign_id = %s AND source = 'scan'",
                (GUILD_A, self.c),
            )
            row = await cur.fetchone()
        assert row is not None
        await self.memory.undo(GUILD_A, self.c, int(row["b"]))
        self.assertEqual((await self.person(ALICE)).name, "Player 1234")
        self.skip.clear()
        await self.sweep()
        self.assertEqual((await self.person(ALICE)).name, "Player 1234")  # (the DM's choice)

    async def test_a_name_that_is_a_secret_name_elsewhere_is_not_given(self) -> None:
        await self.play(ALICE, "Cerric")
        villain = await self.add("Eric the Red", type="npc")
        await self.memory.add_alias(
            GUILD_A, self.c, villain, "Eric", kind="full", source="dm", secret=True
        )
        self.assertFalse(await self.memory.name_person(GUILD_A, self.c, ALICE, "Eric"))
        self.assertFalse(await self.memory.name_person(GUILD_A, self.c, ALICE, "DM"))
        self.assertFalse(await self.memory.name_person(GUILD_A, self.c, ALICE, "   "))

    async def test_fifty_skipped_people_do_not_starve_the_ones_after_them(self) -> None:
        low = [100_000_000_000_000_000 + n for n in range(50)]
        for n, user in enumerate(low):
            await self.play(user, f"Low{n}")
            self.skip[(GUILD_A, user)] = 5000.0  # (skipped for a while, e.g. bots or errors)
        late = 900_000_000_000_000_000
        await self.play(late, "Late")
        self.found[late] = member("Late", "late_l")
        await self.sweep()
        self.assertEqual(self.asked, [late])

    async def test_finished_skips_are_forgotten(self) -> None:
        self.skip[(GUILD_A, ALICE)] = 10.0
        self.skip[(GUILD_A, BOB)] = 5000.0
        await self.sweep()
        self.assertEqual(list(self.skip), [(GUILD_A, BOB)])

    async def test_someone_wrongly_worded_as_left_is_looked_for_again_a_day_later(self) -> None:
        await self.play(ALICE, "Cerric")
        await self.sweep()  # (not found)
        self.assertEqual((await self.person(ALICE)).name, left_name(ALICE))
        self.found[ALICE] = member("Eric", "eric_92")
        await self.sweep()  # (the same minute: left alone)
        self.assertEqual((await self.person(ALICE)).name, left_name(ALICE))
        await member_names.run(self.bot, GUILD_A, self.skip, now=1000.0 + 24 * 3600 + 1)
        self.assertEqual((await self.person(ALICE)).name, "Eric (eric_92)")

    async def test_one_lookup_renames_the_placeholder_in_every_campaign(self) -> None:
        other = (await self.campaigns.create(GUILD_A, "Second", ALICE)).id
        await self.play(ALICE, "Cerric")
        await self.play(ALICE, "Wit", campaign=other)
        self.found[ALICE] = member("Eric", "eric_92")
        self.assertEqual(await self.sweep(), 1)
        self.assertEqual(self.asked, [ALICE])  # (one lookup for the person)
        self.assertEqual((await self.person(ALICE)).name, "Eric (eric_92)")
        self.assertEqual((await self.person(ALICE, other)).name, "Eric (eric_92)")

    async def test_a_display_name_that_is_the_username_is_not_said_twice(self) -> None:
        self.assertEqual(member_names.shown_name("eric_92", "eric_92"), "eric_92")
        self.assertEqual(member_names.shown_name("Eric", "eric_92"), "Eric (eric_92)")
        self.assertEqual(member_names.shown_name("", "eric_92"), "eric_92")

    async def test_someone_who_left_keeps_a_placeholder_worded_so_it_is_no_name(self) -> None:
        await self.play(ALICE, "Cerric")  # (not found: Discord says 404)
        await self.sweep()
        self.assertEqual((await self.person(ALICE)).name, left_name(ALICE))
        self.assertNotIn("Player", (await self.person(ALICE)).name)

    async def test_a_discord_hiccup_changes_nothing_and_is_asked_again_much_later(self) -> None:
        await self.play(ALICE, "Cerric")
        self.errors[ALICE] = discord.HTTPException(MagicMock(status=500, reason="x"), "boom")
        await self.sweep()
        self.assertEqual((await self.person(ALICE)).name, "Player 1234")
        await self.sweep()  # (the same minute: skipped, not hammered)
        self.assertEqual(self.asked, [ALICE])
        del self.errors[ALICE]
        self.found[ALICE] = member("Eric", "eric_92")
        await member_names.run(self.bot, GUILD_A, self.skip, now=1000.0 + 3601)
        self.assertEqual((await self.person(ALICE)).name, "Eric (eric_92)")

    async def test_a_round_looks_up_only_a_few_people(self) -> None:
        for n in range(8):
            user = 100_000_000_000_000_000 + n * 1111
            await self.play(user, f"Char{n}")
            self.found[user] = member(f"Name{n}", f"user{n}")
        self.assertEqual(await self.sweep(), member_names.PER_ROUND)
        self.assertEqual(len(self.asked), member_names.PER_ROUND)
        await self.sweep()  # the next round takes the rest
        self.assertEqual(len(self.asked), 8)

    async def test_a_bot_is_left_alone(self) -> None:
        await self.play(ALICE, "Cerric")
        self.found[ALICE] = member("Helper", "helper", bot=True)
        await self.sweep()
        self.assertEqual((await self.person(ALICE)).name, "Player 1234")

    async def test_the_directory_gets_the_name_only_of_someone_who_agreed(self) -> None:
        await self.play(ALICE, "Cerric")
        await self.play(BOB, "Wit")
        await ConsentStore(self.db).grant(GUILD_A, ALICE)
        self.found[ALICE] = member("Eric", "eric_92")
        self.found[BOB] = member("Bobby", "bob_b")
        await self.sweep()
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute("SELECT user_id FROM member_directory ORDER BY user_id")
            kept = [r["user_id"] for r in await cur.fetchall()]
        self.assertEqual(kept, [ALICE])  # (Bob's name is only on the campaign's entry)
        self.assertEqual((await self.person(BOB)).name, "Bobby (bob_b)")

    async def test_shutting_down_stops_between_people(self) -> None:
        await self.play(ALICE, "Cerric")
        self.bot._closing = True
        await self.sweep()
        self.assertEqual(self.asked, [])

    async def test_the_log_has_counts_only(self) -> None:
        await self.play(ALICE, "Cerric")
        self.found[ALICE] = member("Eric", "eric_92")
        with self.assertLogs("dmbot.member_names", level="INFO") as logged:
            await self.sweep()
        text = "\n".join(logged.output)
        self.assertNotIn("Eric", text)
        self.assertNotIn("eric_92", text)
        self.assertNotIn("1234", text)
        self.assertIn("1 looked up", text)

    async def test_nothing_waits_means_no_lookup(self) -> None:
        self.assertEqual(await self.sweep(), 0)
        self.assertEqual(self.asked, [])


_ = logging
