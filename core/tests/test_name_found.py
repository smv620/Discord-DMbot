"""What made a waiting name a name (#5133 slice 3b, migration 0608): how it was found and a
pointer to the one line that did it. It goes when the name is answered, when its entry goes and
when its line goes, and it holds no words. Postgres; invented names."""

from __future__ import annotations

from typing import Any

from dmbot.memory.name_found import Found, NameFound
from dmbot.transcript.models import line_ref
from dmbot.transcript.store import _forget_memory_traces, _forget_mentions
from tests.test_memory_store import DM, GUILD_A, MemoryTest

REF = line_ref("s1", 1000, 7)
OTHER = line_ref("s1", 2000, 8)
DM_SOURCE = "dm"


class TheTable(MemoryTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.found = NameFound(self.db, clock=lambda: 1_700_000_000.0)
        self.e = await self.add("Hrothgar", source="scan")

    async def rows(self) -> int:
        return await self.count("name_found")

    async def test_it_keeps_how_and_a_line_and_a_later_suggestion_replaces_it(self) -> None:
        await self.found.keep(GUILD_A, self.c, self.e, "spoken_of", OTHER)
        await self.found.keep(GUILD_A, self.c, self.e, "introduced", REF)
        self.assertEqual(
            await self.found.of(GUILD_A, self.c, [self.e]), {self.e: Found("introduced", REF)}
        )
        with self.assertRaises(ValueError):
            await self.found.keep(GUILD_A, self.c, self.e, "guessed", REF)

    async def test_an_answered_name_loses_it_kept_refused_or_joined(self) -> None:
        await self.found.keep(GUILD_A, self.c, self.e, "introduced", REF)
        await self.memory.confirm_entity(GUILD_A, self.c, self.e, "npc", source=DM_SOURCE)
        self.assertEqual(await self.rows(), 0)
        refused = await self.add("Rothgar", source="scan")
        await self.found.keep(GUILD_A, self.c, refused, "scan", None)
        await self.memory.set_entity_status(GUILD_A, self.c, refused, "rejected", source=DM_SOURCE)
        self.assertEqual(await self.rows(), 0)
        kept, joined = (
            await self.add("Vorna", source="scan"),
            await self.add("Vornah", source="scan"),
        )
        await self.found.keep(GUILD_A, self.c, joined, "addressed", REF)
        await self.memory.merge(GUILD_A, self.c, kept, joined, source=DM_SOURCE, dm_said_same=True)
        self.assertEqual(await self.rows(), 0)

    async def test_a_waiting_name_keeps_it_through_other_writes(self) -> None:
        await self.found.keep(GUILD_A, self.c, self.e, "introduced", REF)
        await self.memory.add_alias(
            GUILD_A, self.c, self.e, "Rothgar", kind="misheard", source="scan"
        )
        self.assertEqual(await self.rows(), 1)

    async def test_it_goes_with_its_entry(self) -> None:
        await self.found.keep(GUILD_A, self.c, self.e, "introduced", REF)
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "DELETE FROM memory_entities WHERE guild_id = %s AND id = %s", (GUILD_A, self.e)
            )
        self.assertEqual(await self.rows(), 0)

    async def test_it_goes_with_its_line_even_while_the_name_waits(self) -> None:
        # (a speaker's lines deleted, or the transcript: both go through `_forget_mentions`)
        other = await self.add("Ostrin", source="scan")
        await self.found.keep(GUILD_A, self.c, self.e, "introduced", REF)
        await self.found.keep(GUILD_A, self.c, other, "addressed", OTHER)
        async with self.db.guild(GUILD_A) as conn:
            await _forget_mentions(conn, GUILD_A, [REF], keep_pending=True)
        self.assertEqual(await self.found.of(GUILD_A, self.c, [self.e, other]), {
            other: Found("addressed", OTHER)
        })  # fmt: skip

    async def test_a_name_already_answered_gets_no_row(self) -> None:
        # (the website lists a name as soon as it is made; answered before its row is written)
        await self.memory.confirm_entity(GUILD_A, self.c, self.e, "npc", source=DM_SOURCE)
        await self.found.keep(GUILD_A, self.c, self.e, "introduced", REF)
        self.assertEqual(await self.rows(), 0)

    async def test_a_person_s_forget_takes_their_pointers_even_with_no_lines_left(self) -> None:
        other = await self.add("Ostrin", source="scan")
        await self.found.keep(GUILD_A, self.c, self.e, "introduced", REF)  # (speaker 7)
        await self.found.keep(GUILD_A, self.c, other, "addressed", OTHER)  # (speaker 8)
        async with self.db.guild(GUILD_A) as conn:
            await _forget_memory_traces(conn, GUILD_A, 7, [])
        self.assertEqual(
            await self.found.of(GUILD_A, self.c, [self.e, other]),
            {other: Found("addressed", OTHER)},
        )

    async def test_one_campaign_never_reads_another_s(self) -> None:
        second = (await self.campaigns.create(GUILD_A, "Other", DM)).id
        theirs = await self.add("Hrothgar", campaign=second, source="scan")
        await self.found.keep(GUILD_A, second, theirs, "introduced", REF)
        self.assertEqual(await self.found.of(GUILD_A, self.c, [theirs, self.e]), {})

    async def test_the_table_holds_no_words(self) -> None:
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute(
                "SELECT column_name FROM information_schema.columns"
                " WHERE table_schema = current_schema() AND table_name = 'name_found'"
                " ORDER BY ordinal_position"
            )
            got: Any = [r["column_name"] for r in await cur.fetchall()]
        self.assertEqual(
            got, ["guild_id", "campaign_id", "entity_id", "how", "line_ref", "found_at"]
        )


class TheStepsChoice(MemoryTest):
    """`DMBot._keep_found`: how, and which line is the one that made it a name."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.e = await self.add("Hrothgar", source="scan")
        self.lines: Any = [
            line("we passed the gate", 1000, 7),
            line("Hrothgar waved at us", 2000, 7),
            line("a dwarf called hrothgar steps out", 3000, 8),
            line("hrothgar, I typed it", 4000, 9, typed=True),
        ]

    async def keep(self, group: Any, read: Any = (), spellings: Any = ("Hrothgar",)) -> Found:
        from types import SimpleNamespace

        from dmbot.bot import DMBot

        bot: Any = SimpleNamespace(_picture_db=self.db)
        await DMBot._keep_found(
            bot, GUILD_A, self.c, "s1", self.e, group, list(spellings), self.lines, self.lines, read
        )
        return (await NameFound(self.db).of(GUILD_A, self.c, [self.e]))[self.e]

    async def test_typed_points_at_a_typed_line(self) -> None:
        got = await self.keep(group("scan", typed=True))
        self.assertEqual(got, Found("typed", line_ref("s1", 4000, 9)))

    async def test_the_reader_s_reason_points_at_its_first_cited_line(self) -> None:
        from dmbot.memory.name_reader import Found as ReaderFound

        read = [ReaderFound("hrothgar", "person", "introduced", 1, ("3",))]
        got = await self.keep(group("introduced"), read)
        self.assertEqual(got, Found("introduced", line_ref("s1", 3000, 8)))

    async def test_typed_is_never_a_spoken_line(self) -> None:
        from dmbot.memory.name_reader import Found as ReaderFound

        # (typed under another spelling: the reader's cited typed line, else no line)
        self.lines[3] = line("hrotgarr! I typed it", 4000, 9, typed=True)
        read = [ReaderFound("Hrotgarr", "person", "addressed", 1, ("4",))]
        reader_said = Found("typed", line_ref("s1", 4000, 9))
        got = await self.keep(group("addressed", typed=True), read, ("Hrothgar", "Hrotgarr"))
        self.assertEqual(got, reader_said)
        self.lines[3] = line("nothing typed here", 4000, 9)
        self.assertEqual(await self.keep(group("scan", typed=True)), Found("typed", None))

    async def test_it_never_raises(self) -> None:
        from types import SimpleNamespace

        from dmbot.bot import DMBot

        class Down:
            def guild(self, _g: int) -> Any:
                raise RuntimeError("down")

        bot: Any = SimpleNamespace(_picture_db=Down())
        await DMBot._keep_found(
            bot, GUILD_A, self.c, "s1", self.e, None, ["Hrothgar"], self.lines, self.lines, ()
        )

    async def test_the_scan_alone_points_at_the_first_line_holding_it(self) -> None:
        self.assertEqual(await self.keep(group("scan")), Found("scan", line_ref("s1", 2000, 7)))
        self.assertEqual(await self.keep(None), Found("scan", line_ref("s1", 2000, 7)))


def line(text: str, at: int, who: int, *, typed: bool = False) -> Any:
    from types import SimpleNamespace

    return SimpleNamespace(text=text, started_ms=at, user_id=who, typed=typed)


def group(why: str, *, typed: bool = False) -> Any:
    from dmbot.memory.name_groups import Group

    return Group("Hrothgar", (), 2, why, typed, "")
