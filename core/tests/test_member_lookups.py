"""Typing a Discord ID in a person picker (#4225, point 5; migration 0445): the page asks, the bot
looks the member up in the server and answers, and the page shows who it is. Run as the website's
own database role for the doors; the bot's worker with a stand-in server. Real Postgres; invented
names and ids."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord
import psycopg

from dmbot import member_lookups
from dmbot.consent import ConsentStore
from dmbot.web import member_lookup, sessions
from dmbot.web.discord import DiscordUser
from dmbot.web.recaps import RecapError
from tests.test_recap_web import BOB, NOW, Base
from tests.test_web_api import THURSDAY

ERIC = 444444444444440001
FAR = 555555555555550002


class Lookups(Base):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        token = await sessions.sign_in(
            self.web, DiscordUser(id=BOB.id, name=BOB.name, email=BOB.email), [THURSDAY],
            now=NOW, days=30,
        )  # fmt: skip
        found = await sessions.find(self.web, token, now=NOW)
        assert found is not None
        self.bob = found
        self.g = THURSDAY.id
        self.members: dict[int, Any] = {}
        self.errors: dict[int, Exception] = {}

        async def fetch_member(uid: int) -> Any:
            if uid in self.errors:
                raise self.errors[uid]
            if uid not in self.members:
                raise discord.NotFound(MagicMock(status=404, reason="x"), "gone")
            return self.members[uid]

        self.guild = SimpleNamespace(
            get_member=lambda uid: None, fetch_member=AsyncMock(side_effect=fetch_member)
        )
        self.bot = SimpleNamespace(
            campaigns=SimpleNamespace(db=self.db),
            get_guild=lambda gid: self.guild if gid == self.g else None,
        )

    async def ask(self, raw: object, who: Any = None) -> str:
        return await member_lookup.ask(self.web, who or self.session, self.g, raw)

    async def read(self, lid: str, who: Any = None) -> dict[str, Any]:
        return await member_lookup.read(self.web, who or self.session, self.g, lid)

    async def refused(self, raw: object) -> str:
        with self.assertRaises(RecapError) as caught:
            await self.ask(raw)
        return str(caught.exception.code)


def person(display: str, name: str, *, bot: bool = False) -> Any:
    return SimpleNamespace(display_name=display, name=name, bot=bot)


class TheDoors(Lookups):
    async def test_a_question_waits_until_the_bot_answers(self) -> None:
        lid = await self.ask(str(ERIC))
        got = await self.read(lid)
        self.assertEqual((got["state"], got["outcome"], got["name"]), ("waiting", "", ""))

    async def test_only_a_long_number_is_a_discord_id(self) -> None:
        for bad in (
            "",
            "abc",
            "1234",
            "12345678901234567890123",
            "-1",
            " ",
            "１２３４５６７８９０１２３４５６７８",
            None,
            True,
            1.5,
        ):
            self.assertEqual(await self.refused(bad), "bad_id", repr(bad))

    async def test_a_number_beyond_a_discord_id_is_refused(self) -> None:
        self.assertEqual(await self.refused("99999999999999999999"), "bad_id")

    async def test_only_the_asker_can_read_the_answer(self) -> None:
        lid = await self.ask(str(ERIC))
        with self.assertRaises(RecapError):
            await self.read(lid, who=self.bob)

    async def test_a_server_not_in_the_session_is_refused(self) -> None:
        with self.assertRaises(RecapError):
            await member_lookup.ask(self.web, self.session, 999, str(ERIC))

    async def test_twenty_questions_a_minute_is_the_most(self) -> None:
        for n in range(20):
            await self.ask(str(ERIC + n))
        self.assertEqual(await self.refused(str(ERIC + 99)), "too_soon")

    async def test_a_question_nobody_answered_reads_as_failed_not_spinning(self) -> None:
        lid = await self.ask(str(ERIC))
        async with self.db.guild(self.g) as conn:
            await conn.execute("UPDATE member_lookups SET created_at = created_at - 3600")
        self.assertEqual((await self.read(lid))["state"], "failed")

    async def test_the_website_role_cannot_read_the_table_directly(self) -> None:
        await self.ask(str(ERIC))
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            async with self.web.user(self.session.user_id, session=self.session.id_hash) as c:
                await c.execute("SELECT set_config('dmbot.guild_id', %s, true)", (str(self.g),))
                await c.execute("SELECT * FROM member_lookups")


class TheBot(Lookups):
    async def answered(self, text: str) -> dict[str, Any]:
        lid = await self.ask(text)
        await member_lookups.run(self.bot, self.g)
        return await self.read(lid)

    async def test_a_member_in_the_server_is_shown_by_name_and_username(self) -> None:
        self.members[ERIC] = person("Eric", "eric_92")
        got = await self.answered(str(ERIC))
        self.assertEqual(
            (got["state"], got["outcome"], got["name"]), ("done", "found", "Eric (eric_92)")
        )
        self.assertIs(got["agreed"], False)  # (not agreed yet: the picker marks them)

    async def test_someone_who_agreed_is_not_marked(self) -> None:
        self.members[ERIC] = person("Eric", "eric_92")
        await ConsentStore(self.db).grant(self.g, ERIC)
        self.assertIs((await self.answered(str(ERIC)))["agreed"], True)

    async def test_not_in_this_server_is_not_here_and_gives_no_name(self) -> None:
        got = await self.answered(str(FAR))
        self.assertEqual((got["state"], got["outcome"], got["name"]), ("done", "not_here", ""))
        self.assertIsNone(got["agreed"])

    async def test_a_bot_account_is_not_a_person_here(self) -> None:
        self.members[ERIC] = person("Helper", "helper", bot=True)
        got = await self.answered(str(ERIC))
        self.assertEqual((got["outcome"], got["name"]), ("not_here", ""))

    async def test_a_discord_hiccup_fails_the_question_and_the_next_one_still_goes(self) -> None:
        self.errors[FAR] = discord.HTTPException(MagicMock(status=500, reason="x"), "boom")
        self.members[ERIC] = person("Eric", "eric_92")
        first = await self.ask(str(FAR))
        second = await self.ask(str(ERIC))
        await member_lookups.run(self.bot, self.g)
        self.assertEqual((await self.read(first))["state"], "failed")
        self.assertEqual((await self.read(second))["outcome"], "found")

    async def test_a_round_answers_a_few_at_a_time(self) -> None:
        ids = [await self.ask(str(ERIC + n)) for n in range(5)]
        for n in range(5):
            self.members[ERIC + n] = person(f"P{n}", f"p{n}")
        await member_lookups.run(self.bot, self.g)
        states = [(await self.read(i))["state"] for i in ids]
        self.assertEqual(states.count("done"), member_lookups.PER_ROUND)
        await member_lookups.run(self.bot, self.g)
        for i in ids:
            self.assertEqual((await self.read(i))["state"], "done")

    async def test_a_taken_question_is_answered_once(self) -> None:
        self.members[ERIC] = person("Eric", "eric_92")
        lid = await self.ask(str(ERIC))
        lookups = member_lookups.MemberLookups(self.db)
        [(row, _target, stamp)] = await lookups.claim(self.g)
        self.assertEqual(await lookups.claim(self.g), [])  # (held: nobody else takes it)
        self.assertTrue(await lookups.answer(self.g, row, stamp, outcome="found", shown="Eric"))
        self.assertFalse(await lookups.answer(self.g, row, stamp, outcome="found", shown="Other"))
        self.assertEqual((await self.read(lid))["name"], "Eric")

    async def test_nothing_waiting_means_no_lookup(self) -> None:
        self.assertEqual(await member_lookups.run(self.bot, self.g), 0)
        self.guild.fetch_member.assert_not_awaited()

    async def test_the_log_has_counts_only(self) -> None:
        self.members[ERIC] = person("Eric", "eric_92")
        await self.ask(str(ERIC))
        with self.assertLogs("dmbot.member_lookups", level="INFO") as logged:
            await member_lookups.run(self.bot, self.g)
        text = "\n".join(logged.output)
        for secret in ("Eric", "eric_92", str(ERIC)):
            self.assertNotIn(secret, text)
        self.assertIn("1 found", text)

    async def test_the_name_is_never_written_into_a_campaign(self) -> None:
        self.members[ERIC] = person("Eric", "eric_92")
        await self.answered(str(ERIC))
        async with self.db.guild(self.g) as conn:
            cur = await conn.execute(
                "SELECT count(*) AS n FROM memory_entities WHERE name LIKE %s", ("%Eric%",)
            )
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual(row["n"], 0)

    async def test_a_valid_id_is_read_back_by_its_clean_form(self) -> None:
        self.assertEqual(member_lookup.clean_id(f" {ERIC} "), ERIC)
        self.assertEqual(member_lookup.clean_id(ERIC), ERIC)
