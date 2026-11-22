"""The correction pass for the counts of names said (#3172 item d, Supervisor's ruling): a session's
names are counted again from its stored transcript without the lines read aloud as last time's
recap, once its split is made. Invented names throughout. The database tests need Postgres."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any, ClassVar, cast
from unittest.mock import AsyncMock

from dmbot.bot import DMBot
from dmbot.memory import heard
from dmbot.memory.models import CONFIRMED, Heard, NewName
from dmbot.transcript.models import Line
from dmbot.transcript.store import TranscriptStore
from tests.test_memory_names import DM, GUILD, PLAYER, NamesTest, make_table

START = 1_700_000_000


def line(n: int, user: int, text: str, **kw: Any) -> Line:
    return Line(START * 1000 + n * 1000, user, text, text, **kw)


def named(text: str) -> set[str]:
    """A stand-in matcher: ids by the names in the line."""
    return {key for key, word in (("orla", "Orla"), ("rook", "Rook")) if word in text}


class ThePureCount(unittest.TestCase):
    LINES: ClassVar[list[Line]] = [
        line(1, DM, "Last time Orla met Rook at the ford."),  # 1 (recap)
        line(2, DM, "Orla is here again."),  # 2 (play)
        line(3, PLAYER, "Orla said hello, Orla said it twice."),  # 3 (play; once for the line)
        line(4, DM, "Orla", sidebar="question"),  # 4 (a sidebar line: never counted)
        line(5, PLAYER, "  "),  # 5 (blank)
        line(6, 999, "Rook!"),  # 6 (nobody who agrees)
        line(7, PLAYER, "Rook sneaks by.", topic="off_topic"),  # 7 (the live count had it too)
    ]

    def test_the_live_lines_without_the_retold_ones(self) -> None:
        got = heard.count_lines(self.LINES, {DM, PLAYER}, named, retold={1})
        self.assertEqual(got, {("orla", DM): 1, ("orla", PLAYER): 1, ("rook", PLAYER): 1})

    def test_with_no_split_every_line_counts(self) -> None:
        got = heard.count_lines(self.LINES, {DM, PLAYER}, named)
        self.assertEqual(got[("rook", DM)], 1)  # (the recap's line: no split, so play)
        self.assertEqual(got[("orla", DM)], 2)

    def test_a_name_only_the_recap_said_is_not_heard_in_this_session(self) -> None:
        got = heard.count_lines(self.LINES[:1], {DM}, named, retold={1})
        self.assertEqual(got, {})

    def test_someone_who_no_longer_agrees_adds_nothing(self) -> None:
        got = heard.count_lines(self.LINES, {DM}, named, retold=())
        self.assertTrue(all(speaker == DM for _e, speaker in got))


class TheRecount(NamesTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.store = TranscriptStore(self.db)
        self.bot.transcripts = self.store
        await self.consent.grant(GUILD, DM)
        await self.consent.grant(GUILD, PLAYER)
        made = await self.memory.add_names(
            GUILD,
            self.campaign.id,
            [
                NewName("Orla Finch", "character", CONFIRMED),
                NewName("Rook Pell", "character", CONFIRMED),
            ],
            source="dm",
        )
        self.orla, self.rook = (cast(str, x) for x in made.value)
        self.sid = await self.store.open_session(GUILD, self.campaign.id, START)
        await self.store.add_lines(
            GUILD,
            self.sid,
            [
                line(1, DM, "Last time Orla Finch met Rook Pell at the ford."),  # the recap
                line(2, DM, "Orla Finch is here again."),
                line(3, PLAYER, "Hello, Orla Finch."),
            ],
        )

    async def rows(self) -> dict[tuple[str, int], int]:
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                "SELECT entity_id, speaker_id, times FROM memory_heard"
                " WHERE session_started_at = %s",
                (START,),
            )
            return {(r["entity_id"], r["speaker_id"]): r["times"] for r in await cur.fetchall()}

    async def live(self) -> None:
        """The live flush: every line counted, the recap's too."""
        await self.memory.add_session_heard(
            GUILD,
            self.campaign.id,
            START,
            [
                Heard(self.orla, DM, 2),
                Heard(self.rook, DM, 1),
                Heard(self.orla, PLAYER, 1),
            ],
        )

    async def split(self, recap: tuple[int, int] | None) -> None:
        parts = [["recap", recap[0], recap[1]]] if recap else []
        await self.store.save_session_split(
            GUILD, self.sid, {"parts": parts, "unsure": False}, only_if_empty=False
        )

    async def test_a_retold_session_loses_the_names_only_the_recap_said(self) -> None:
        await self.live()
        await self.split((1, 1))
        kept = await self.bot.recount_heard(GUILD, self.campaign.id, START)
        self.assertEqual(kept, 2)
        self.assertEqual(await self.rows(), {(self.orla, DM): 1, (self.orla, PLAYER): 1})

    async def test_it_is_idempotent(self) -> None:
        await self.live()
        await self.split((1, 1))
        await self.bot.recount_heard(GUILD, self.campaign.id, START)
        first = await self.rows()
        await self.bot.recount_heard(GUILD, self.campaign.id, START)
        self.assertEqual(await self.rows(), first)

    async def test_no_split_leaves_the_counts_as_the_live_count_had_them(self) -> None:
        await self.live()
        before = await self.rows()
        await self.bot.recount_heard(GUILD, self.campaign.id, START)  # (the lines say the same)
        self.assertEqual(await self.rows(), before)

    async def test_a_person_who_stopped_drops_out(self) -> None:
        await self.live()
        await self.consent.revoke(GUILD, PLAYER)
        await self.bot.recount_heard(GUILD, self.campaign.id, START, retold=())
        self.assertTrue(all(speaker == DM for _e, speaker in await self.rows()))

    async def test_an_undone_split_brings_the_counts_back_from_the_transcript(self) -> None:
        await self.live()
        await self.split((1, 1))
        await self.bot.recount_heard(GUILD, self.campaign.id, START)
        self.assertNotIn((self.rook, DM), await self.rows())
        await self.split(None)  # (the DM handed the lines back: all play)
        await self.bot.recount_heard(GUILD, self.campaign.id, START)
        self.assertEqual(
            await self.rows(),
            {(self.orla, DM): 2, (self.rook, DM): 1, (self.orla, PLAYER): 1},
        )

    async def test_an_import_is_left_alone_but_a_live_session_with_no_counts_is_counted(
        self,
    ) -> None:
        # (an import has no live count to correct, and counting it would change its importance
        # rank; a live session whose counts were all retold has none left, and may be undone)
        await self.split((1, 1))
        async with self.db.guild(GUILD) as conn:
            await conn.execute(
                "INSERT INTO session_imports (guild_id, campaign_id, session_id, import_id,"
                " file_name, imported_by, imported_at) VALUES (%s, %s, %s, %s, 'a.txt', %s, 1)",
                (GUILD, self.campaign.id, self.sid, "a" * 32, DM),
            )
        self.assertIsNone(await self.bot.recount_heard(GUILD, self.campaign.id, START))
        self.assertEqual(await self.rows(), {})
        async with self.db.guild(GUILD) as conn:
            await conn.execute("DELETE FROM session_imports WHERE guild_id = %s", (GUILD,))
        await self.split(None)  # (a live session, nothing retold, and no counts at all)
        await self.bot.recount_heard(GUILD, self.campaign.id, START)
        self.assertEqual(
            await self.rows(),
            {(self.orla, DM): 2, (self.rook, DM): 1, (self.orla, PLAYER): 1},
        )

    async def test_counts_that_were_all_retold_come_back_when_the_split_is_undone(self) -> None:
        await self.live()
        await self.split((1, 3))  # (every line retold: no rows left)
        await self.bot.recount_heard(GUILD, self.campaign.id, START)
        self.assertEqual(await self.rows(), {})
        await self.split(None)  # (the DM hands the lines back)
        await self.bot.recount_heard(GUILD, self.campaign.id, START)
        self.assertEqual(
            await self.rows(),
            {(self.orla, DM): 2, (self.rook, DM): 1, (self.orla, PLAYER): 1},
        )

    async def test_the_later_wins_when_the_split_was_made_before_the_flush(self) -> None:
        await self.split((1, 1))  # (the split first)
        table = SimpleNamespace(
            guild_id=GUILD,
            campaign_id=self.campaign.id,
            started_at=START,
            transcript_session_id=self.sid,
            heard_counts={
                (self.orla, DM): 2,
                (self.rook, DM): 1,
                (self.orla, PLAYER): 1,
            },
        )
        await DMBot.keep_heard_names(self.bot, cast(Any, table))  # (then the flush)
        self.assertEqual(await self.rows(), {(self.orla, DM): 1, (self.orla, PLAYER): 1})

    async def test_a_failure_leaves_the_old_counts_and_never_raises(self) -> None:
        await self.live()
        before = await self.rows()
        await self.split((1, 1))
        self.memory.replace_session_heard = AsyncMock(side_effect=RuntimeError("down"))  # type: ignore[method-assign]
        with self.assertLogs("dmbot.bot", level="WARNING") as logs:
            self.assertIsNone(await self.bot.recount_heard(GUILD, self.campaign.id, START))
        self.assertIn("RuntimeError", " ".join(logs.output))
        self.assertEqual(await self.rows(), before)

    async def test_the_store_replaces_in_one_step_and_an_empty_result_clears(self) -> None:
        await self.live()
        await self.memory.replace_session_heard(
            GUILD, self.campaign.id, START, [Heard(self.rook, PLAYER, 3)]
        )
        self.assertEqual(await self.rows(), {(self.rook, PLAYER): 3})
        await self.memory.replace_session_heard(GUILD, self.campaign.id, START, [])
        self.assertEqual(await self.rows(), {})


class TheLiveCountAgrees(NamesTest):
    """One invented session through the live hooks, then counted again from the lines they kept:
    the two must give the same counts (condition 2 of the ruling), whatever kinds of line passed."""

    async def test_the_live_hook_path_and_the_recount_give_the_same_counts(self) -> None:
        from functools import partial

        from dmbot.memory.lookup import CampaignLookup
        from dmbot.memory.scene import mentions
        from dmbot.transcript.models import SIDEBAR_ANSWER, SIDEBAR_TABLE_QUESTION

        await self.consent.grant(GUILD, DM)
        await self.consent.grant(GUILD, PLAYER)
        await self.memory.add_names(
            GUILD,
            self.campaign.id,
            [
                NewName("Orla Finch", "character", CONFIRMED),
                NewName("Rook Pell", "character", CONFIRMED),
            ],
            source="dm",
        )
        table = make_table(self.campaign.id)
        lookup = CampaignLookup.build(await self.memory.lookup_data(GUILD, self.campaign.id))
        table.name_lookup = lookup
        self.bot.transcripts = TranscriptStore(self.db)
        self.bot.tables[GUILD] = table
        base = START * 1000
        self.bot._deliver_typed(table, PLAYER, "We met Orla Finch at the ford.", base + 1000)
        self.bot._deliver_typed(table, DM, "Orla Finch and Rook Pell, and Orla Finch.", base + 2000)
        self.bot._deliver_typed(table, PLAYER, "Rook Pell says nothing about it.", base + 3000)
        # (said off the record: in neither the count nor the transcript)
        self.bot._deliver_typed(table, PLAYER, "hey dmbot, off the record", base + 4000)
        # (a line the off-topic filter later hides: counted live, and in the recount)
        self.bot._deliver_typed(table, DM, "Did anyone see Orla Finch on the telly?", base + 5000)
        table.unsaved.set_topic(DM, base + 5000, "off_topic", typed=True)
        # (a DM sidebar question, DMbot's answer and a table question: kept, never counted)
        for kind, text in (
            ("question", "Who is Orla Finch?"),
            (SIDEBAR_ANSWER, "Orla Finch is a ranger."),
            (SIDEBAR_TABLE_QUESTION, "Where is Rook Pell?"),
        ):
            self.bot.sidebar_save(table, Line(base + 6000, DM, text, text, sidebar=kind))
        lines = table.unsaved.lines()
        self.assertTrue(any(x.sidebar for x in lines))  # (the sidebar kinds were kept)
        self.assertFalse(any("off the record" in x.text for x in lines))
        live = dict(table.heard_counts)
        again = heard.count_lines(lines, {DM, PLAYER}, partial(mentions, lookup))
        self.assertEqual(again, live)
        self.assertTrue(live)  # (and there was something to count)
