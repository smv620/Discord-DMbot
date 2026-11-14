"""A player's Discord avatar for the Story Graph's icons (#3621): the hash only, only for members
who agreed (the directory keeps no one else), cleared when they leave, and read again each day for
the players who play a character. Invented names and numbers throughout."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import discord

from dmbot.bot import DMBot
from dmbot.campaigns import CampaignStore
from dmbot.consent import ConsentStore
from dmbot.members import MemberDirectory
from dmbot.memory.models import DM as DM_SOURCE
from dmbot.memory.store import MemoryStore
from tests.pg import DatabaseTest

GUILD, DM, ALICE, BOB = 7001, 11, 9101, 9102
HASH = "ab" * 16


class Avatars(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.directory = MemberDirectory(self.db)
        self.memory = MemoryStore(self.db)
        self.campaign = await CampaignStore(self.db).create(GUILD, "One", DM)
        await ConsentStore(self.db).grant(GUILD, ALICE)
        await self.directory.remember(GUILD, ALICE, "Alice", "alice_a")

    async def avatar(self, user: int = ALICE) -> str | None:
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                "SELECT avatar FROM member_directory WHERE guild_id = %s AND user_id = %s",
                (GUILD, user),
            )
            row = await cur.fetchone()
        return None if row is None else row["avatar"]

    async def play(self, user: int, name: str = "Rook") -> str:
        made = await self.memory.add_entity(
            GUILD, self.campaign.id, type="character", name=name, source=DM_SOURCE,
            status="confirmed", role="player_character", played_by=user,
        )  # fmt: skip
        return made.value.id


class TheDirectory(Avatars):
    async def test_the_hash_is_kept_and_cleared(self) -> None:
        await self.directory.set_avatar(GUILD, ALICE, HASH)
        self.assertEqual(await self.avatar(), HASH)
        await self.directory.set_avatar(GUILD, ALICE, f"a_{HASH}")  # an animated one
        self.assertEqual(await self.avatar(), f"a_{HASH}")
        await self.directory.set_avatar(GUILD, ALICE, None)  # the default picture, or left
        self.assertIsNone(await self.avatar())

    async def test_anything_that_is_not_a_hash_is_cleared_never_kept(self) -> None:
        await self.directory.set_avatar(GUILD, ALICE, HASH)
        for bad in ("https://evil.example/x.png", "../../x", HASH + "0", HASH.upper(), ""):
            await self.directory.set_avatar(GUILD, ALICE, bad)
            self.assertIsNone(await self.avatar(), bad)

    async def test_someone_who_never_agreed_gets_no_row_and_so_no_avatar(self) -> None:
        await self.directory.set_avatar(GUILD, BOB, HASH)  # (no row: nothing is written)
        self.assertIsNone(await self.avatar(BOB))
        await self.directory.remember(GUILD, BOB, "Bob", "bob_b")  # no consent row: nothing kept
        self.assertIsNone(await self.avatar(BOB))

    async def test_a_member_who_stops_loses_the_avatar_with_the_row(self) -> None:
        await self.directory.set_avatar(GUILD, ALICE, HASH)
        await ConsentStore(self.db).revoke(GUILD, ALICE)
        await self.directory.forget(GUILD, ALICE)
        self.assertIsNone(await self.avatar())

    async def test_only_players_who_play_a_confirmed_character_are_swept(self) -> None:
        self.assertEqual(await self.directory.linked_players(GUILD), [])
        entry = await self.play(ALICE)
        await self.play(BOB, "Wen")  # (Bob never agreed: not in the directory)
        self.assertEqual(await self.directory.linked_players(GUILD), [ALICE])
        await self.memory.set_entity_status(GUILD, self.campaign.id, entry, "rejected", source="dm")
        self.assertEqual(await self.directory.linked_players(GUILD), [])


class TheSweep(Avatars):
    def bot(self, members: dict[int, Any]) -> Any:
        async def fetch_member(user_id: int) -> Any:
            found = members[user_id]
            if isinstance(found, Exception):
                raise found
            return found

        guild = SimpleNamespace(id=GUILD, fetch_member=fetch_member)
        return SimpleNamespace(
            members=self.directory,
            guilds=[guild],
            _closing=False,
            _avatars_swept={},
            _avatar_queue={},
        )

    async def sweep(self, bot: Any) -> None:
        await DMBot.run_member_avatars(bot)

    async def test_a_linked_players_avatar_is_read_and_a_departed_one_is_cleared(self) -> None:
        await self.play(ALICE)
        bot = self.bot({ALICE: SimpleNamespace(avatar=SimpleNamespace(key=HASH))})
        await self.sweep(bot)
        self.assertEqual(await self.avatar(), HASH)
        bot = self.bot(
            {ALICE: discord.NotFound(cast(Any, SimpleNamespace(status=404, reason="")), "gone")}
        )
        await self.sweep(bot)
        self.assertIsNone(await self.avatar())

    async def test_it_runs_once_a_day_per_server(self) -> None:
        await self.play(ALICE)
        bot = self.bot({ALICE: SimpleNamespace(avatar=SimpleNamespace(key=HASH))})
        await self.sweep(bot)
        await self.directory.set_avatar(GUILD, ALICE, None)
        await self.sweep(bot)  # the same minute: not again
        self.assertIsNone(await self.avatar())

    async def test_a_discord_failure_keeps_what_it_had_and_tries_again_later(self) -> None:
        await self.play(ALICE)
        await self.directory.set_avatar(GUILD, ALICE, HASH)
        bot = self.bot(
            {ALICE: discord.HTTPException(cast(Any, SimpleNamespace(status=500, reason="")), "x")}
        )
        await self.sweep(bot)
        self.assertEqual(await self.avatar(), HASH)

    async def test_many_players_are_read_a_few_at_a_time_across_rounds(self) -> None:
        from dmbot.bot import AVATARS_PER_ROUND

        users = [8000 + n for n in range(AVATARS_PER_ROUND + 5)]
        for n, user in enumerate(users):
            await ConsentStore(self.db).grant(GUILD, user)
            await self.directory.remember(GUILD, user, f"P{n}", f"p{n}")
            await self.play(user, f"Hero {n}")
        members = {u: SimpleNamespace(avatar=SimpleNamespace(key=HASH)) for u in users}
        bot = self.bot(members)
        await self.sweep(bot)
        kept = [u for u in users if await self.avatar(u) == HASH]
        self.assertEqual(len(kept), AVATARS_PER_ROUND)  # one round's worth
        await self.sweep(bot)  # the rest, not a new day's sweep
        self.assertEqual(len([u for u in users if await self.avatar(u) == HASH]), len(users))
        self.assertEqual(bot._avatar_queue, {})

    async def test_one_failing_player_does_not_stop_the_others(self) -> None:
        await ConsentStore(self.db).grant(GUILD, BOB)
        await self.directory.remember(GUILD, BOB, "Bob", "bob_b")
        await self.play(ALICE)
        await self.play(BOB, "Wen")
        bot = self.bot(
            {
                ALICE: RuntimeError("down"),
                BOB: SimpleNamespace(avatar=SimpleNamespace(key=HASH)),
            }
        )
        with self.assertLogs("dmbot.bot", level="INFO") as logs:
            await self.sweep(bot)
        self.assertEqual(await self.avatar(BOB), HASH)
        self.assertIn("1 kept, 0 cleared, 1 failed", " ".join(logs.output))

    async def test_shutting_down_stops_between_players(self) -> None:
        await self.play(ALICE)
        bot = self.bot({ALICE: SimpleNamespace(avatar=SimpleNamespace(key=HASH))})
        bot._closing = True
        await self.sweep(bot)
        self.assertIsNone(await self.avatar())

    async def test_the_log_has_counts_only(self) -> None:
        await self.play(ALICE)
        bot = self.bot({ALICE: SimpleNamespace(avatar=SimpleNamespace(key=HASH))})
        with self.assertLogs("dmbot.bot", level="INFO") as logs:
            await self.sweep(bot)
        shown = " ".join(logs.output)
        self.assertIn("1 kept, 0 cleared, 0 failed, 0 to go", shown)
        self.assertNotIn(HASH, shown)
        self.assertNotIn(str(ALICE), shown)
