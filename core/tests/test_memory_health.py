"""The story graph's health numbers for the server log (#1699): each count on a seeded campaign,
another campaign's rows never counted, no name in the line, and a failing read that costs the
line and nothing else. The store tests need Postgres."""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

from dmbot.bot import DMBot
from dmbot.memory import health
from dmbot.memory.models import Heard
from tests.test_memory_store import BORN_IN, GUILD_A, SERVES, ConfirmedMemoryTest

SESSION = 1_700_000_000


class Counting(ConfirmedMemoryTest):
    async def seed(self, campaign: str | None = None) -> list[str]:
        """One duplicate pair, one orphan, one unused term, one flag; returns the names used."""
        c = campaign or self.c
        for term in (BORN_IN, SERVES):
            await self.memory.add_predicate(
                GUILD_A, c, term, examples=["x"], reason="y", source="entitybot"
            )
        mira = await self.add("Mira", campaign=c, type="character")
        town = await self.add("Dunhollow", campaign=c, type="place")
        self.ids = {"Mira": mira, "Dunhollow": town}
        await self.memory.add_relation(
            GUILD_A, c, mira, "born_in", town, confidence=1.0, source="dm"
        )  # (uses born_in; retained_by stays unused)
        # no relation, never heard:
        self.ids["hermit"] = await self.add("Lonely hermit", campaign=c, type="character")
        self.ids["k1"] = await self.add("Ka'zeth", campaign=c, type="character")
        self.ids["k2"] = await self.add("Kazeth", campaign=c, type="character")  # the same key
        return ["Mira", "Dunhollow", "Lonely hermit", "Ka'zeth", "Kazeth"]

    async def numbers(self, **kw: Any) -> health.Health:
        return await health.read(self.memory, GUILD_A, self.c, since=SESSION, **kw)

    async def test_each_count_on_a_seeded_campaign(self) -> None:
        await self.seed()
        got = await self.numbers()
        self.assertEqual(got.confirmed, 5)
        self.assertEqual(got.proposed, 0)
        self.assertEqual(got.entities, got.confirmed + got.proposed)
        self.assertEqual(got.duplicates, 1)
        self.assertEqual(got.orphans, 3)  # the hermit and the two Ka'zeths: no relation
        self.assertEqual(got.unused_terms, 1)  # retained_by
        self.assertEqual(got.flags_this_session, 0)
        self.assertEqual(got.asked_dm, 0)

    async def test_a_name_heard_lately_is_not_an_orphan_and_an_old_one_is(self) -> None:
        await self.seed()
        hermit = self.ids["hermit"]
        busy = await self.add("Passer-by", type="character")
        await self.memory.add_session_heard(GUILD_A, self.c, SESSION + 100, [Heard(hermit, 7, 1)])
        # Ka'zeth, Kazeth and the passer-by have no relation and were not heard; the hermit was:
        self.assertEqual((await self.numbers()).orphans, 3)
        for n in (2, 3, 4):  # three sessions after it, in which the hermit is not heard
            await self.memory.add_session_heard(
                GUILD_A, self.c, SESSION + n * 100, [Heard(busy, 7, 1)]
            )
        # The hermit was heard only before the last three sessions: an orphan again; the
        # passer-by was heard in them and is not.
        self.assertEqual((await self.numbers()).orphans, 3)
        await self.memory.add_session_heard(GUILD_A, self.c, SESSION + 500, [Heard(hermit, 7, 1)])
        self.assertEqual((await self.numbers()).orphans, 2)  # heard again

    async def test_a_flag_this_session_is_counted_and_an_older_one_is_not(self) -> None:
        await self.seed()
        mira, town = self.ids["Mira"], self.ids["Dunhollow"]
        await self.memory.add_relation(  # a wrong way round: a flag, not a refusal
            GUILD_A, self.c, town, "born_in", mira, confidence=1.0, source="dm"
        )
        self.assertGreaterEqual((await self.numbers()).flags_this_session, 1)
        later = await health.read(self.memory, GUILD_A, self.c, since=self.now + 10)
        self.assertEqual(later.flags_this_session, 0)

    async def test_the_questions_put_to_the_dm_are_added(self) -> None:
        self.assertEqual((await self.numbers(names_asked=3)).asked_dm, 3)

    async def test_a_same_as_link_takes_a_pair_out_of_duplicates(self) -> None:
        await self.seed()
        ids = [self.ids["k1"], self.ids["k2"]]
        await self.memory.add_predicate(
            GUILD_A,
            self.c,
            health_same_as(),
            examples=["x"],
            reason="y",
            source="entitybot",
        )
        await self.memory.add_relation(
            GUILD_A, self.c, ids[0], "same_as", ids[1], confidence=1.0, source="dm"
        )
        self.assertEqual((await self.numbers()).duplicates, 0)

    async def test_one_same_as_link_among_three_leaves_two_pairs_and_an_ended_one_none(
        self,
    ) -> None:
        await self.memory.add_predicate(
            GUILD_A, self.c, health_same_as(), examples=["x"], reason="y", source="entitybot"
        )
        a = await self.add("Brann", type="character")
        b = await self.add("Brann", type="character")
        c = await self.add("Brann", type="character")
        self.assertEqual((await self.numbers()).duplicates, 3)  # a-b, a-c, b-c
        link = await self.memory.add_relation(
            GUILD_A, self.c, a, "same_as", b, confidence=1.0, source="dm"
        )
        self.assertEqual((await self.numbers()).duplicates, 2)  # a-c and b-c remain
        await self.memory.update_relation(
            GUILD_A, self.c, link.value[0].id, source="dm", to_session_at=SESSION + 1
        )
        self.assertEqual((await self.numbers()).duplicates, 3)  # an ended link joins nothing
        del c

    async def test_people_are_not_counted_as_orphans_or_duplicates(self) -> None:
        before = await self.numbers()
        async with self.db.guild(GUILD_A) as conn:  # (people are made by DMbot, not by the menus)
            for n in range(2):  # two people with one name, linked to nothing
                made = f"{n + 1:032x}"
                await conn.execute(
                    "INSERT INTO memory_entities (guild_id, campaign_id, id, type, name,"
                    " description, status, source, created_at)"
                    " VALUES (%s, %s, %s, 'person', 'Sam', '', 'confirmed', 'dm', %s)",
                    (GUILD_A, self.c, made, SESSION),
                )
                await conn.execute(
                    "INSERT INTO memory_aliases (guild_id, campaign_id, id, entity_id, text, key,"
                    " kind, secret, status, sound_codes, source, created_at)"
                    " VALUES (%s, %s, %s, %s, 'Sam', 'sam', 'full', false, 'confirmed', '{}',"
                    " 'dm', %s)",
                    (GUILD_A, self.c, f"{n + 9:032x}", made, SESSION),
                )
        after = await self.numbers()
        self.assertEqual((after.duplicates, after.orphans), (before.duplicates, before.orphans))
        self.assertEqual(after.confirmed, before.confirmed + 2)  # (they are entities)

    async def test_another_campaigns_rows_never_count(self) -> None:
        mine = await self.numbers()
        other = (await self.campaigns.create(GUILD_A, "Elsewhere", 7)).id
        await self.seed(other)
        self.assertEqual(await self.numbers(), mine)
        self.assertEqual(mine.entities, 0)

    async def test_the_line_holds_counts_and_no_name(self) -> None:
        names = await self.seed()
        line = (await self.numbers()).line()
        self.assertTrue(line.startswith("memory health: entities="))
        for name in names:
            self.assertNotIn(name, line)
        self.assertRegex(line, r"^memory health: (\w+=\d+ ?)+$")


def health_same_as() -> Any:
    from dmbot.memory.ontology import PredicateTerm

    return PredicateTerm(
        "same_as", "is the same as", "Two names for one thing.", ("character",), ("character",),
        symmetric=True, core=False,
    )  # fmt: skip


class Failing(ConfirmedMemoryTest):
    async def test_a_failed_read_is_a_warning_and_the_next_steps_still_run(self) -> None:
        table = SimpleNamespace(
            guild_id=GUILD_A,
            campaign_id=self.c,
            started_at=SESSION,
            questions=SimpleNamespace(closed_why={}),
        )
        bot = SimpleNamespace(memory=self.memory)
        broken = patch.object(health, "read", AsyncMock(side_effect=RuntimeError("db down")))
        warned = self.assertLogs("dmbot.bot", level=logging.WARNING)
        with broken, warned as logged:
            await DMBot.log_memory_health(bot, table, SESSION + 5)  # type: ignore[arg-type]
        self.assertIn("Couldn't read the memory health numbers", logged.output[0])

    async def test_a_good_read_writes_one_info_line(self) -> None:
        table = SimpleNamespace(
            guild_id=GUILD_A,
            campaign_id=self.c,
            started_at=SESSION,
            questions=SimpleNamespace(closed_why={"a": "x", "b": "y"}),
        )
        bot = SimpleNamespace(memory=self.memory)
        with self.assertLogs("dmbot.bot", level=logging.INFO) as logged:
            await DMBot.log_memory_health(bot, table, SESSION + 5)  # type: ignore[arg-type]
        (line,) = [r for r in logged.output if "memory health:" in r]
        self.assertIn("asked_dm=2", line)

    async def test_nothing_happens_without_memory_or_a_campaign(self) -> None:
        table = SimpleNamespace(guild_id=GUILD_A, campaign_id=None)
        await DMBot.log_memory_health(SimpleNamespace(memory=self.memory), table, 1)  # type: ignore[arg-type]
        table2 = SimpleNamespace(guild_id=GUILD_A, campaign_id=self.c)
        await DMBot.log_memory_health(SimpleNamespace(memory=None), table2, 1)  # type: ignore[arg-type]
