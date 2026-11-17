"""A recap's beats chosen by the importance rank (#3172 slice 4, behind DMBOT_RECAP_BY_RANK): the
rank sorts first, the connection score breaks ties, player characters don't count, and with the
switch off nothing is read and nothing changes. Invented names only."""

from __future__ import annotations

import unittest

from dmbot.devtools.recap_run import __main__ as run
from dmbot.memory import importance as imp
from dmbot.memory import importance_rank as ir
from dmbot.memory.models import CONFIRMED, PROPOSED, NewName, name_key
from dmbot.recaps import draft as rd
from dmbot.recaps.beats import Beat
from tests.test_importance_rank import Base
from tests.test_memory_names import GUILD


def beat(number: int, who: list[str], lines: int = 1, *, matters: bool = False) -> Beat:
    if matters:
        return Beat(number, f"beat {number}", tuple(who), tuple(range(lines)), "event",
                    note="matters", keep=True)  # fmt: skip
    return Beat(number, f"beat {number}", tuple(who), tuple(range(lines)), "event")


def numbers(beats: list[Beat]) -> list[int]:
    return [b.number for b in beats]


RANKS = {
    name_key("Vellis"): ir.MAJOR,  # the artifact's keeper, said once
    name_key("Garrick"): ir.RECURRING,
    name_key("Mott"): ir.MINOR,
    name_key("Wren"): ir.PLAYER,
}


class TheBeatRank(unittest.TestCase):
    def test_highest_first_and_player_characters_left_out(self) -> None:
        got = ir.beat_rank(["Mott", "Wren", "Vellis", "Mott"], RANKS)
        self.assertEqual(got, (ir.MAJOR, ir.MINOR))  # Wren (a player) and the repeat add nothing

    def test_an_unknown_name_adds_nothing_and_no_table_is_empty(self) -> None:
        self.assertEqual(ir.beat_rank(["Stranger"], RANKS), ())
        self.assertEqual(ir.beat_rank(["Vellis"], {}), ())

    def test_background_names_count_for_nothing(self) -> None:
        ranks = {**RANKS, name_key("Pip"): ir.BACKGROUND}
        self.assertEqual(ir.beat_rank(["Garrick", "Pip"], ranks), ir.beat_rank(["Garrick"], ranks))

    def test_more_names_that_matter_win_between_equal_bests(self) -> None:
        self.assertGreater(
            ir.beat_rank(["Garrick", "Mott"], RANKS), ir.beat_rank(["Garrick"], RANKS)
        )

    def test_a_shared_name_takes_the_higher_rank(self) -> None:
        got = ir.names_ranked(
            [("e1", "The Keeper"), ("e2", "the keeper"), ("e3", "Nobody")], {"e1": 1, "e2": 4}
        )
        self.assertEqual(got, {name_key("The Keeper"): 4})  # e3 has no rank: left out


SHEET = [
    beat(1, []),  # the opening
    beat(2, ["Garrick", "Wren"], 6),  # a long scene with a recurring NPC
    beat(3, ["Vellis"], 1),  # one line with the Major entry
    beat(4, ["Mott", "Wren"], 4),
    beat(5, []),  # where the session stopped
]


class PickingBeats(unittest.TestCase):
    sheet = tuple(SHEET)

    def test_the_rank_sorts_before_the_connection_score(self) -> None:
        score = {name_key("Mott"): 15.0}  # Mott is busy in The truth, but only Minor
        before = rd.pick_beats(self.sheet, limit=3, importance=score)
        self.assertEqual(numbers(before), [1, 4, 5])
        after = rd.pick_beats(self.sheet, limit=3, importance=score, ranks=RANKS)
        self.assertEqual(numbers(after), [1, 3, 5])

    def test_the_connection_score_breaks_ties_inside_a_rank(self) -> None:
        sheet = [beat(1, []), beat(2, ["Garrick"], 6), beat(3, ["Hale"], 1), beat(4, [])]
        ranks = {name_key("Garrick"): ir.RECURRING, name_key("Hale"): ir.RECURRING}
        got = rd.pick_beats(sheet, limit=3, importance={name_key("Hale"): 9.0}, ranks=ranks)
        self.assertEqual(numbers(got), [1, 3, 4])

    def test_a_background_name_does_not_beat_the_connection_score(self) -> None:
        sheet = [beat(1, []), beat(2, ["Garrick", "Pip"]), beat(3, ["Hale"]), beat(4, [])]
        ranks = {
            name_key("Garrick"): ir.RECURRING,
            name_key("Pip"): ir.BACKGROUND,
            name_key("Hale"): ir.RECURRING,
        }
        got = rd.pick_beats(sheet, limit=3, importance={name_key("Hale"): 15.0}, ranks=ranks)
        self.assertEqual(numbers(got), [1, 3, 4])

    def test_what_plotbot_marked_still_comes_first(self) -> None:
        sheet = [beat(1, []), beat(2, ["Mott"], matters=True), beat(3, ["Vellis"]), beat(4, [])]
        self.assertEqual(numbers(rd.pick_beats(sheet, limit=3, ranks=RANKS)), [1, 2, 4])

    def test_no_ranks_changes_nothing(self) -> None:
        score = {name_key("Mott"): 15.0}
        self.assertEqual(
            numbers(rd.pick_beats(self.sheet, limit=3, importance=score)),
            numbers(rd.pick_beats(self.sheet, limit=3, importance=score, ranks={})),
        )


class TheRanksByName(Base):
    async def test_names_and_confirmed_other_names_carry_the_rank(self) -> None:
        await self.store.add(GUILD, self.campaign.id, [ir.Assessment(self.garrick, 3, "reading")])
        await self.memory.add_alias(
            GUILD, self.campaign.id, self.garrick, "Old Garr", kind="nickname", source="dm",
            status=CONFIRMED,
        )  # fmt: skip
        got = await self.store.by_name(GUILD, self.campaign.id)
        self.assertEqual(got[name_key("Garrick")], 3)
        self.assertEqual(got[name_key("Old Garr")], 3)
        self.assertEqual(got[name_key("Wren")], ir.BACKGROUND)

    async def test_an_unconfirmed_name_picks_nothing(self) -> None:
        await self.memory.add_names(
            GUILD, self.campaign.id, [NewName("Brother Quill", "npc", PROPOSED)], source="dm"
        )
        got = await self.store.by_name(GUILD, self.campaign.id)
        self.assertNotIn(name_key("Brother Quill"), got)

    async def test_a_merged_entrys_other_name_counts_for_the_one_that_stayed(self) -> None:
        await self.memory.add_alias(
            GUILD, self.campaign.id, self.wren, "Little Wren", kind="nickname", source="dm",
            status=CONFIRMED,
        )  # fmt: skip
        await self.memory.merge(
            GUILD, self.campaign.id, self.garrick, self.wren, source="dm", dm_said_same=True
        )
        await self.store.add(GUILD, self.campaign.id, [ir.Assessment(self.garrick, 3, "reading")])
        got = await self.store.by_name(GUILD, self.campaign.id)
        self.assertEqual(got[name_key("Little Wren")], 3)

    async def test_another_campaign_has_none(self) -> None:
        other = (await self.campaigns.create(GUILD, "Sunken City", 7)).id
        await self.memory.add_names(
            GUILD, other, [NewName("Garrick", "npc", CONFIRMED)], source="dm"
        )
        await self.store.add(GUILD, self.campaign.id, [ir.Assessment(self.garrick, 4, "reading")])
        got = await self.store.by_name(GUILD, other)
        self.assertEqual(got[name_key("Garrick")], ir.BACKGROUND)  # its own Garrick, not ours
        self.assertNotIn(name_key("Ashmere"), got)  # and none of our names


class Broken:
    """A memory store whose every read fails: what a cache asks of it shows in `asked`."""

    def __init__(self, scores_fail: bool = True) -> None:
        self.asked: list[str] = []
        self.scores_fail = scores_fail

    @property
    def _db(self) -> object:
        self.asked.append("ranks")
        raise RuntimeError("down")

    def _read(self, *a: object, **k: object) -> object:
        self.asked.append("scores")
        raise RuntimeError("down")


class TheSwitchOff(unittest.IsolatedAsyncioTestCase):
    async def test_off_reads_no_rank(self) -> None:
        store = Broken()
        cache = imp.ImportanceCache(store)
        with self.assertLogs("dmbot.memory.importance", level="ERROR"):
            self.assertEqual(await cache.ranks(1, "c"), {})
            await cache.refresh(1, "c")
        self.assertNotIn("ranks", store.asked)

    async def test_a_failing_rank_read_is_no_ranks(self) -> None:
        store = Broken()
        cache = imp.ImportanceCache(store, ranks_on=True)
        with self.assertLogs("dmbot.memory.importance", level="WARNING") as logs:
            self.assertEqual(await cache.ranks(1, "c"), {})
        self.assertIn("ranks", store.asked)
        self.assertTrue(any("couldn't read the campaign's ranks" in m for m in logs.output))


class TheSwitchOn(Base):
    async def test_on_reads_them_and_keeps_them_until_refreshed(self) -> None:
        cache = imp.ImportanceCache(self.memory, ranks_on=True)
        self.assertEqual((await cache.ranks(GUILD, self.campaign.id))[name_key("Garrick")], 0)
        await self.store.add(GUILD, self.campaign.id, [ir.Assessment(self.garrick, 4, "reading")])
        # kept until the refresh, so every stage of one recap picks the same beats
        self.assertEqual((await cache.ranks(GUILD, self.campaign.id))[name_key("Garrick")], 0)
        await cache.refresh(GUILD, self.campaign.id)
        self.assertEqual((await cache.ranks(GUILD, self.campaign.id))[name_key("Garrick")], 4)


class TheRecapRun(unittest.IsolatedAsyncioTestCase):
    async def test_by_rank_reads_ranks_and_scores_always(self) -> None:
        from unittest import mock

        seen: list[imp.ImportanceCache] = []

        async def load(**kw: object) -> object:
            seen.append(kw["importance"])  # type: ignore[arg-type]
            return None

        with mock.patch("dmbot.recaps.inputs.load_inputs_or_why", load):
            await run.inputs_for(object(), 1, "c", 2)
            await run.inputs_for(object(), 1, "c", 2, by_rank=True)
        self.assertEqual([c._ranks_on for c in seen], [False, True])

    async def test_by_rank_goes_with_a_stored_session_only(self) -> None:
        import contextlib
        import io

        err = io.StringIO()
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(err):
            run.parse_args(["--golden", "--by-rank", "--yes"])
        self.assertIn("--by-rank goes with --session only", err.getvalue())
        got = run.parse_args(["--session", "1", "c", "2", "--by-rank", "--yes"])
        self.assertTrue(got.by_rank)
