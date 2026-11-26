"""The weather a session described, for the after-session note (#4260 slice 10c-2,
DMBOT_WEATHER_NOTES; Supervisor's ruling A): the reader's rules on the invented `weather_cases` text
(the DM's narration only, the kind's own word in the line and not negated or asked, a named place,
never the place's own name), the extractor's use of the claims call (no extra call, line numbers and
counts only in the log), and the bot's note against the kept climate. Invented names, a canned
AI."""

from __future__ import annotations

import json
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

from dmbot.ai import Reply
from dmbot.config import ConfigError, load_settings
from dmbot.maps import weather_note as wn
from dmbot.memory import extractor, weather_reading
from dmbot.memory import map_facts as mf
from dmbot.memory.weather_reading import check, once_each, parse_weather
from dmbot.transcript.models import NARRATING
from tests.test_config import BASE
from tests.test_extractor import ALICE, DM, Kept, dm_session, go, line

CASES = Path(__file__).parent / "fixtures" / "story_graph"


def session() -> tuple[dict[str, str], list[str]]:
    rows = (CASES / "weather_cases_session.txt").read_text(encoding="utf-8").splitlines()
    said = {str(i): r.split("] ", 1)[1].split(": ", 1)[1] for i, r in enumerate(rows, 1)}
    return said, [str(i) for i, r in enumerate(rows, 1) if "] DM:" in r]


def answer(*items: tuple[str, str, int]) -> str:
    return json.dumps(
        {"claims": [], "weather": [{"place": p, "kind": k, "lines": [n]} for p, k, n in items]}
    )


class TheCases(unittest.TestCase):
    def test_the_key_is_what_the_reader_keeps(self) -> None:
        key = json.loads((CASES / "weather_cases_key.json").read_text(encoding="utf-8"))
        said, dm = session()
        got = parse_weather(
            answer(
                ("Frostmere", "snow", 1), ("Frostmere", "heat", 2), ("Sunreach", "snow", 3),
                ("Sunreach", "frost", 4), ("Sunreach", "snow", 5), ("Frostmere", "snow", 6),
                ("Sunreach", "snow", 7), ("Sunreach", "snow", 8), ("Frostmere", "frost", 9),
                ("Sunreach", "snow", 10), ("Sunreach", "snow", 11), ("Frostmere", "heat", 12),
                ("Sunreach", "snow", 13), ("Frostmere", "heat", 14), ("Sunreach", "snow", 15),
                ("Sunreach", "snow", 16),
            ),
            said,
            dm,
        )  # fmt: skip
        self.assertEqual(
            [(w.place, w.kind, w.lines) for w in got.weather],
            [(k["place"], k["kind"], (str(k["line"]),)) for k in key["weather"]],
        )
        self.assertEqual(
            got.dropped,
            {
                "weather_not_dm": 2,  # (3: a player's guess; 16: said as an NPC, #4748)
                "weather_not_there": 2,  # (6: no snow; 10: doesn't snow)
                "weather_asked": 2,  # (7 with a "?", 15 without one)
                "weather_not_in_line": 2,  # (8: rain; 9: "cold as ice", Frostmere's own name)
                "weather_not_yet": 1,  # (11: coming next week)
                "weather_figure": 3,  # (12: Scorching Ray; 13: snow-white; 14: heat of the moment)
            },
        )
        must_not = {str(m["line"]) for m in key["must_not_weather"]}
        self.assertEqual(must_not, {str(n) for n in range(3, 17)} - {"4", "5"})

    def test_the_key_gives_its_notes_against_the_climates(self) -> None:
        key = json.loads((CASES / "weather_cases_key.json").read_text(encoding="utf-8"))
        climate = {c["place"]: c["climate"] for c in key["climates"]}
        noted = [
            k["line"] for k in key["weather"]
            if wn.check(k["place"], k["kind"], [(fact(climate[k["place"]]), None)]) is not None
        ]  # fmt: skip
        self.assertEqual(noted, key["notes"])

    def test_each_rule(self) -> None:
        said = "Snow falls over Frostmere. The sun bakes Sunreach."
        self.assertEqual(check("Frostmere", "snow", "Snow falls over Frostmere.", said), "")
        self.assertEqual(check("here", "snow", "Snow falls here.", said), "weather_place")
        self.assertEqual(check("Lonely Pass", "snow", "Snow falls.", said), "weather_place")
        self.assertEqual(check("Frostmere", "rain", "Rain over Frostmere.", said), "weather_kind")
        self.assertEqual(check("Frostmere", "frost", "Frostmere is quiet.", said),
                         "weather_not_in_line")  # (its own name holds "frost")  # fmt: skip
        self.assertEqual(check("Frostmere", "snow", "There is no snow in Frostmere.", said),
                         "weather_not_there")  # fmt: skip
        self.assertEqual(check("Frostmere", "snow", "Is that snow on Frostmere?", said),
                         "weather_asked")  # fmt: skip
        self.assertEqual(check("Sunreach", "heat", "In Sunreach, the heat is scorching.", said),
                         "")  # fmt: skip

    def test_only_the_dms_lines_and_repeats(self) -> None:
        said = {"1": "Snow falls over Frostmere.", "2": "Snow falls over Frostmere."}
        got = parse_weather(answer(("Frostmere", "snow", 1)), said, ["2"])
        self.assertEqual(got.dropped, {"weather_not_dm": 1})
        got = parse_weather(
            answer(("Frostmere", "snow", 1), ("frostmere", "snow", 2)), said, ["1", "2"]
        )
        self.assertEqual(len(got.weather), 1)
        self.assertEqual(len(once_each([*got.weather, *got.weather])), 1)
        self.assertEqual(parse_weather("not json", said, ["1"]).weather, [])
        both = parse_weather(
            json.dumps({"claims": [], "weather": [
                {"place": "Frostmere", "kind": "snow", "lines": [2, 1]}]}),
            {"1": "Snow falls over Frostmere.", "2": "I think snow falls over Frostmere."},
            ["1"],
        )  # fmt: skip
        self.assertEqual(both.weather[0].lines, ("1",))  # (only the DM line that held)


def fact(value: str) -> mf.Fact:
    return mf.Fact(
        id=uuid.uuid4().hex, place_id="p" * 32, layer=mf.CLIMATE, value=value, words="", how="",
        part="", status=mf.CONFIRMED, source="dm", table_knows=False,
    )  # fmt: skip


class WeatherAnswer:
    def __init__(self, text: str) -> None:
        self.systems: list[str] = []
        self.text = text

    async def complete(self, system: str, text: str, *, max_tokens: int = 0) -> Reply:
        self.systems.append(system)
        return Reply(self.text, False, 10, 5, "m-fast")


class InTheClaimsCall(unittest.IsolatedAsyncioTestCase):
    async def test_off_by_default(self) -> None:
        ai = WeatherAnswer(answer(("Frostmere", "snow", 1)))
        report = await go(ai, Kept(), dm_session(10))
        self.assertEqual(ai.systems, [extractor.SYSTEM])
        self.assertEqual(report.heard_weather, [])

    async def test_on_it_costs_no_call_and_logs_lines_never_words(self) -> None:
        ai = WeatherAnswer(answer(("Frostmere", "heat", 1), ("Frostmere", "snow", 2)))
        lines = [
            line(1, DM, "In Frostmere, a scorching heatwave melts the ice."),
            line(2, ALICE, "Snow will fall in Frostmere tomorrow."),
            *dm_session(10)[2:],
        ]
        report = await go(ai, Kept(), lines, weather=True)
        self.assertEqual(report.calls, 1)
        self.assertIn(weather_reading.WEATHER_ADDENDUM, ai.systems[0])
        self.assertEqual([(w.place, w.kind) for w in report.heard_weather], [("Frostmere", "heat")])
        self.assertIn("weather: 1 heard (lines 1)", report.line())
        self.assertNotIn("Frostmere", report.line())

    async def test_a_line_said_as_an_npc_gives_no_weather(self) -> None:
        # (#4748, as #4708 for the map checks: a line labelled as a named NPC can be a lie or a
        # mistake in the story; narrating, or no label, is the DM's own words)
        ai = WeatherAnswer(answer(("Sunreach", "snow", 1), ("Frostmere", "heat", 2)))
        lines = [
            line(1, DM, "Snow piles up in the streets of Sunreach.", entity="Mayor Brask"),
            line(2, DM, "In Frostmere, a scorching heatwave.", entity=NARRATING),
            *dm_session(10)[2:],
        ]
        report = await go(ai, Kept(), lines, weather=True)
        self.assertEqual([(w.place, w.kind) for w in report.heard_weather], [("Frostmere", "heat")])
        self.assertEqual(report.weather_dropped["weather_not_dm"], 1)

    async def test_a_speaker_who_stops_meanwhile_leaves_nothing(self) -> None:
        ai = WeatherAnswer(answer(("Frostmere", "heat", 1)))
        lines = [line(1, DM, "In Frostmere, a scorching heatwave."), *dm_session(20)[1:]]
        asked: list[int] = []

        async def consenting() -> set[int]:
            asked.append(1)
            return {DM} if len(asked) < 3 else set()

        report = await go(ai, Kept(), lines, consenting, weather=True)
        self.assertEqual(report.heard_weather, [])
        self.assertIn("weather_speaker_gone", report.line())


class TheSwitch(unittest.TestCase):
    def test_off_by_default_and_checked(self) -> None:
        self.assertFalse(load_settings(BASE).weather_notes_on)
        self.assertTrue(load_settings({**BASE, "DMBOT_WEATHER_NOTES": "1"}).weather_notes_on)
        with self.assertRaises(ConfigError):
            load_settings({**BASE, "DMBOT_WEATHER_NOTES": "maybe"})


class TheNote(unittest.IsolatedAsyncioTestCase):
    async def bot(self, held: Any, *, names: tuple[str, ...] = ("Frostmere",)) -> Any:
        place = SimpleNamespace(id="p" * 32, name="Frostmere", status="confirmed", type="place")
        region = SimpleNamespace(id="r" * 32, name="the Cold Coast", status="confirmed",
                                 type="place")  # fmt: skip
        lookup = SimpleNamespace(
            entities={place.id: place, region.id: region},
            names=tuple(SimpleNamespace(text=n, entity_id=place.id, confirmed=True) for n in names),
        )
        memory = SimpleNamespace(
            lookup_data=AsyncMock(), ontology=AsyncMock(return_value=SimpleNamespace(
                is_a=lambda kind, base: kind == base)),
            map_facts_up=AsyncMock(return_value={mf.CLIMATE: held} if held else {}),
        )  # fmt: skip
        # (the marks are stood in for in `run_it`; `test_weather_marks` has the database's)
        memory._db = None
        bot = SimpleNamespace(
            campaigns=SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(
                dm_accounts=frozenset({7})))),
            memory=memory, _dm_message=AsyncMock(return_value=object()), tables={},
        )  # fmt: skip
        return bot, lookup

    async def run_it(self, bot: Any, lookup: Any, *kinds: str, first: bool = True) -> list[Any]:
        """Runs the note; `first`: whether the marks say each note is new. Returns the marks."""
        from unittest.mock import patch

        from dmbot.bot import DMBot
        from dmbot.memory.weather_reading import Weather

        marked: list[Any] = []

        async def first_time(
            _db: Any, _g: int, _c: str, marks: Any, now: int | None = None
        ) -> list[bool]:
            marked.extend(marks)
            return [first] * len(marks)

        report = SimpleNamespace(heard_weather=[Weather("Frostmere", k, ("2",)) for k in kinds])
        with (
            patch("dmbot.memory.lookup.CampaignLookup.build", return_value=lookup),
            patch("dmbot.memory.weather_marks.first_time", first_time),
        ):
            await DMBot._weather_notes(bot, 1, "c" * 32, report, 1)  # type: ignore[arg-type]
        return marked

    async def test_with_no_discord_the_note_is_an_outbox_row_and_no_message(self) -> None:
        from unittest.mock import AsyncMock

        bot, lookup = await self.bot(mf.Held((fact("arctic"),), "r" * 32))
        bot.headless = True  # (#4904 3b-4b: the worker has no Discord)
        bot.outbox = SimpleNamespace(add_now=AsyncMock())
        await self.run_it(bot, lookup, wn.HEAT)
        bot._dm_message.assert_not_awaited()
        args, kwargs = bot.outbox.add_now.await_args
        self.assertEqual(args[2], "story_weather")
        self.assertEqual(sorted(args[3]), ["at", "session"])  # a moment and a date, no word
        self.assertTrue(kwargs["unique"])

    async def test_heat_in_a_freezing_place_is_noted_privately_with_its_working(self) -> None:
        climate = fact("arctic")
        bot, lookup = await self.bot(mf.Held((climate,), "r" * 32))
        marked = await self.run_it(bot, lookup, wn.HEAT)
        # (marked before it is sent: the place, the kind, and the climate row and value, #4748)
        self.assertEqual(
            [tuple(m) for m in marked], [("p" * 32, wn.HEAT, climate.id, "arctic", (climate.id,))]
        )
        bot._dm_message.assert_awaited_once()
        assert bot._dm_message.await_args is not None
        self.assertEqual(
            bot._dm_message.await_args.args[1],
            "🌦️ Weather check: hot weather in Frostmere in the session of 01 Jan 1970, but it's in"
            " the Cold Coast, whose climate is freezing. If that's on purpose, like magic or a"
            " story turn, you can ignore this.",
        )

    async def test_a_note_already_sent_is_not_sent_again(self) -> None:
        bot, lookup = await self.bot(mf.Held((fact("arctic"),)))
        marked = await self.run_it(bot, lookup, wn.HEAT, first=False)
        self.assertEqual(len(marked), 1)
        bot._dm_message.assert_not_awaited()

    async def test_never_while_the_table_plays_or_from_a_place_it_cant_name(self) -> None:
        bot, lookup = await self.bot(mf.Held((fact("arctic"),)))
        bot.tables = {1: SimpleNamespace(campaign_id="c" * 32)}
        await self.run_it(bot, lookup, wn.HEAT)
        bot._dm_message.assert_not_awaited()
        bot, lookup = await self.bot(mf.Held((fact("arctic"),), "x" * 32))  # (not in the lookup)
        await self.run_it(bot, lookup, wn.HEAT)
        bot._dm_message.assert_not_awaited()

    async def test_a_name_not_yet_kept_never_points_the_note(self) -> None:
        bot, lookup = await self.bot(mf.Held((fact("arctic"),)))
        lookup.names = (SimpleNamespace(text="Frostmere", entity_id="p" * 32, confirmed=False),)
        await self.run_it(bot, lookup, wn.HEAT)
        bot._dm_message.assert_not_awaited()

    def test_several_notes_go_under_one_heading(self) -> None:
        notes = [
            wn.Note("Frostmere", wn.HEAT, "arctic", None, None),
            wn.Note("Sunreach", wn.SNOW, "hot", None, None),
        ]
        self.assertEqual(
            wn.message(notes, "the session of 1 Jan 2027"),
            "🌦️ Weather check, the session of 1 Jan 2027:\n"
            "• hot weather in Frostmere, but the climate there is freezing. If that's on purpose,"
            " like magic or a story turn, you can ignore this.\n"
            "• snow in Sunreach, but the climate there is hot. If that's on purpose, like magic"
            " or a story turn, you can ignore this.",
        )

    async def test_silence_when_it_fits_or_nothing_is_known(self) -> None:
        for held, kind in ((mf.Held((fact("arctic"),)), wn.SNOW), (None, wn.HEAT)):
            bot, lookup = await self.bot(held)
            await self.run_it(bot, lookup, kind)
            bot._dm_message.assert_not_awaited()

    async def test_a_name_two_places_share_is_no_note(self) -> None:
        bot, lookup = await self.bot(mf.Held((fact("arctic"),)))
        lookup.names = (
            SimpleNamespace(text="Frostmere", entity_id="p" * 32, confirmed=True),
            SimpleNamespace(text="Frostmere", entity_id="r" * 32, confirmed=True),
        )
        await self.run_it(bot, lookup, wn.HEAT)
        bot._dm_message.assert_not_awaited()
