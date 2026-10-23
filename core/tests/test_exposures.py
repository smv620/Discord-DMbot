"""Secrets said or typed at the table, recorded for the DM's summary (#2410, slice 1): the finding
(names and connections), the store (record, pending, settle, isolation) and the after-session
step. Nothing here applies anything: the players' readers must not change. Invented names."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

from dmbot.bot import DMBot
from dmbot.memory.exposures import (
    Exposure,
    ExposureStore,
    SecretConnection,
    find,
    named,
    secret_connections,
)
from dmbot.memory.models import CONFIRMED, spelling_key
from dmbot.memory.secret_aloud import SecretName
from dmbot.transcript.models import Line
from tests.test_memory_store import GUILD_A, GUILD_B, MemoryTest

PLAYER, DM_ID = 11, 7
AURIL = SecretName("a" * 32, "Auril")
PLOT = SecretConnection("r" * 32, ("Mira", "The Hooded One"), ("Rook", "Captain Rook"))


class Finding(unittest.TestCase):
    def test_a_connection_is_found_when_one_line_names_both_ends(self) -> None:
        counts = named([PLOT], ["Mira works for Rook", "the Hooded One and Captain Rook met", "x"])
        self.assertEqual(counts, {PLOT.relation_id: 2})  # any name of each end counts

    def test_one_end_alone_or_the_ends_in_different_lines_is_not_enough(self) -> None:
        self.assertEqual(named([PLOT], ["Mira went home", "Rook slept", "Mira", "Rook"]), {})

    def test_whole_names_only_and_short_names_are_not_looked_for(self) -> None:
        self.assertEqual(named([PLOT], ["Miranda and Rooky"]), {})
        short = SecretConnection("s" * 32, ("Al",), ("Bo",))
        self.assertEqual(named([short], ["Al and Bo"]), {})

    def test_find_gives_names_then_connections(self) -> None:
        got = find([AURIL], [PLOT], ["Auril, Auril", "Mira knows Rook"])
        self.assertEqual(
            got,
            [
                Exposure("name", "auril", None, 2),
                Exposure("connection", None, PLOT.relation_id, 1),
            ],
        )
        self.assertEqual(find([AURIL], [PLOT], ["nothing here"]), [])


class Store(MemoryTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.store = ExposureStore(self.db, clock=lambda: 1_700_000_000)

    async def secret_link(self) -> tuple[str, str, str]:
        mira = await self.add("Mira", type="character", role="npc", status=CONFIRMED)
        rook = await self.add("Rook", type="character", role="npc", status=CONFIRMED)
        written = await self.relate(mira, "knows", rook, status=CONFIRMED, secret=True)
        return mira, rook, str(written.value[0].id)

    async def test_record_then_pending_most_said_first(self) -> None:
        _, _, rid = await self.secret_link()
        found = [
            Exposure("name", "auril", None, 2),
            Exposure("name", "vex", None, 5),
            Exposure("connection", None, rid, 1),
        ]
        self.assertEqual(await self.store.record(GUILD_A, self.c, 100, found), 3)
        got = await self.store.pending(GUILD_A, self.c, 100)
        self.assertEqual(
            [(e.kind, e.spelling or e.relation_id, e.times) for e in got],
            [("name", "vex", 5), ("name", "auril", 2), ("connection", rid, 1)],
        )
        self.assertEqual(await self.store.pending(GUILD_A, self.c, 999), [])

    async def test_recording_again_updates_what_is_pending_and_never_revives_a_settled_one(
        self,
    ) -> None:
        await self.store.record(GUILD_A, self.c, 100, [Exposure("name", "auril", None, 1)])
        await self.store.record(GUILD_A, self.c, 100, [Exposure("name", "auril", None, 4)])
        (one,) = await self.store.pending(GUILD_A, self.c)
        self.assertEqual(one.times, 4)
        self.assertEqual(await self.store.settle_name(GUILD_A, self.c, "Auril's"), 1)
        await self.store.record(GUILD_A, self.c, 100, [Exposure("name", "auril", None, 9)])
        self.assertEqual(await self.store.pending(GUILD_A, self.c), [])

    async def test_a_name_is_settled_in_every_session_and_an_alias_finds_its_spelling(self) -> None:
        mira = await self.add("Mira", type="character", role="npc", status=CONFIRMED)
        alias = await self.memory.add_alias(
            GUILD_A, self.c, mira, "The Hooded One", kind="nickname", source="dm",
            status=CONFIRMED, secret=True,
        )  # fmt: skip
        key = spelling_key("The Hooded One")
        for session in (100, 200):
            await self.store.record(GUILD_A, self.c, session, [Exposure("name", key, None, 1)])
        self.assertEqual(await self.store.settle_alias(GUILD_A, self.c, alias.value.id), 2)
        self.assertEqual(await self.store.pending(GUILD_A, self.c), [])

    async def test_a_connection_can_be_settled_and_goes_with_the_connection(self) -> None:
        _, _, rid = await self.secret_link()
        await self.store.record(GUILD_A, self.c, 100, [Exposure("connection", None, rid, 1)])
        self.assertEqual(await self.store.settle_connection(GUILD_A, self.c, rid), 1)
        await self.store.record(GUILD_A, self.c, 200, [Exposure("connection", None, rid, 1)])
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute("DELETE FROM memory_relations WHERE id = %s", (rid,))
        self.assertEqual(await self.store.pending(GUILD_A, self.c), [])
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute("SELECT count(*) AS n FROM secret_exposures")
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual(row["n"], 0)  # (deleted with the connection)

    async def test_nothing_crosses_a_campaign_or_a_server(self) -> None:
        other = (await self.campaigns.create(GUILD_A, "Other", 8)).id
        await self.store.record(GUILD_A, self.c, 100, [Exposure("name", "auril", None, 1)])
        self.assertEqual(await self.store.pending(GUILD_A, other), [])
        self.assertEqual(await self.store.pending(GUILD_B, self.c), [])
        self.assertEqual(await self.store.settle_name(GUILD_A, other, "Auril"), 0)
        self.assertEqual(len(await self.store.pending(GUILD_A, self.c)), 1)

    async def test_reading_the_secret_connections_with_every_name_of_each_end(self) -> None:
        mira, rook, rid = await self.secret_link()
        await self.memory.add_alias(
            GUILD_A, self.c, mira, "The Hooded One", kind="nickname", source="dm",
            status=CONFIRMED, secret=True,
        )  # fmt: skip
        open_one = await self.relate(mira, "ally_of", rook, status=CONFIRMED)  # not secret
        self.assertTrue(open_one.value)
        (found,) = await secret_connections(self.memory, GUILD_A, self.c)
        self.assertEqual(found.relation_id, rid)
        self.assertEqual(sorted(found.subject), ["Mira", "The Hooded One"])
        self.assertEqual(found.object, ("Rook",))


def line(user: int, text: str, **kw: Any) -> Line:
    return Line(1000, user, text, text, **kw)


class AfterTheSession(unittest.IsolatedAsyncioTestCase):
    def bot(self, lines: list[Line]) -> Any:
        return SimpleNamespace(
            memory=object(),
            exposures=SimpleNamespace(record=AsyncMock(return_value=1)),
            transcripts=SimpleNamespace(lines=AsyncMock(return_value=lines)),
            campaigns=SimpleNamespace(
                get=AsyncMock(return_value=SimpleNamespace(dm_user_ids=frozenset({DM_ID})))
            ),
            consent=SimpleNamespace(consenting=AsyncMock(return_value={PLAYER, DM_ID})),
        )

    async def run_step(self, bot: Any, names: list[SecretName]) -> None:
        table = SimpleNamespace(
            guild_id=GUILD_A, campaign_id="c" * 32, transcript_session_id="s1", started_at=500
        )
        with (
            patch("dmbot.bot.secret_names", AsyncMock(return_value=names)),
            patch("dmbot.bot.secret_connections", AsyncMock(return_value=[PLOT])),
        ):
            await DMBot.record_exposures(bot, table)

    async def test_what_the_players_said_is_recorded_and_nothing_else_happens(self) -> None:
        bot = self.bot([line(PLAYER, "Ask Auril, Mira knows Rook")])
        await self.run_step(bot, [AURIL])
        bot.exposures.record.assert_awaited_once()
        args = bot.exposures.record.await_args.args
        self.assertEqual(args[2], 500)
        self.assertEqual([e.kind for e in args[3]], ["name", "connection"])

    async def test_the_dm_the_sidebar_and_people_not_recorded_never_count(self) -> None:
        bot = self.bot(
            [
                line(DM_ID, "Auril"),
                line(PLAYER, "Auril", sidebar="answer"),
                line(99, "Mira knows Rook"),
            ]
        )
        await self.run_step(bot, [AURIL])
        bot.exposures.record.assert_not_awaited()


class Settling(unittest.IsolatedAsyncioTestCase):
    async def test_an_answer_about_a_name_settles_its_exposure_and_a_bot_without_the_store_is_fine(
        self,
    ) -> None:
        from dmbot.ui.secret_aloud import settle

        store = SimpleNamespace(settle_alias=AsyncMock(return_value=1))
        campaign = SimpleNamespace(guild_id=GUILD_A, id="c" * 32)
        await settle(SimpleNamespace(exposures=store), campaign, "a" * 32)
        store.settle_alias.assert_awaited_once_with(GUILD_A, "c" * 32, "a" * 32)
        await settle(SimpleNamespace(), campaign, "a" * 32)  # (older stand-ins have none)
        await settle(SimpleNamespace(exposures=None), campaign, "a" * 32)


if __name__ == "__main__":
    unittest.main()
