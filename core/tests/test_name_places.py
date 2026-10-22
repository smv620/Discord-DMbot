"""Where a suggested name was said (#2329): the pointers kept with a suggestion (never words), the
two windows on the review card, "More places", the deleted-transcript message and Discord's
limits."""

from __future__ import annotations

import unittest
from dataclasses import replace
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from dmbot.discord_limits import MESSAGE_MAX
from dmbot.memory import places
from dmbot.transcript.models import Line
from dmbot.transcript.store import Around, TranscriptStore
from dmbot.ui import name_places
from dmbot.ui import names as ui
from tests.test_memory_names import DM, GUILD, PLAYER, NamesTest

START = 1_700_000_000
SAID = "we ride to meet Hrothgar at the old gate before dawn"


def line(seconds: int, user: int, text: str, **kw: Any) -> Line:
    return Line(START * 1000 + seconds * 1000, user, text, text, **kw)


class Finding(unittest.TestCase):
    def test_a_name_is_found_as_a_whole_word_in_table_speech_only(self) -> None:
        lines = [
            line(1, PLAYER, "Hrothgar is here"),
            line(2, PLAYER, "Hrothgarian wine"),  # not the name
            line(3, DM, "hrothgar's axe"),  # any case, with a possessive
            replace(line(4, DM, "who is Hrothgar"), sidebar="question"),  # the DM sidebar: no
        ]
        found = places.find_spots(lines, ["Hrothgar"])
        self.assertEqual([s.line.started_ms // 1000 - START for s in found], [1, 3])

    def test_the_other_spellings_heard_with_it_count_too(self) -> None:
        found = places.find_spots([line(1, PLAYER, "ask Vane about it")], ["Oskar Vane", "Vane"])
        self.assertEqual(len(found), 1)

    def test_the_kept_ones_are_spread_across_the_session(self) -> None:
        spots = places.find_spots([line(i, PLAYER, "Hrothgar") for i in range(100)], ["Hrothgar"])
        kept = places.spread(spots)
        self.assertEqual(len(kept), places.MAX_PLACES)
        times = [s.line.started_ms // 1000 - START for s in kept]
        self.assertEqual((times[0], times[-1]), (0, 99))  # first and last
        self.assertEqual(times, sorted(times))
        self.assertEqual(places.spread(spots[:3]), spots[:3])  # few: all of them

    def test_a_long_line_is_cut_around_the_name(self) -> None:
        text = "x " * 100 + "Hrothgar" + " y" * 100
        start = text.index("Hrothgar")
        before, name, after = places.cut(text, start, start + 8)
        self.assertEqual(name, "Hrothgar")
        self.assertLessEqual(len(before) + len(name) + len(after), places.LINE_CHARS + 2)
        self.assertTrue(before.startswith("…") and after.endswith("…"))

    def test_neighbours_are_cut_at_the_start_or_the_end(self) -> None:
        words = "a" * 300
        self.assertTrue(places.trimmed(words).endswith("…"))
        self.assertTrue(places.trimmed(words, from_end=True).startswith("…"))
        self.assertEqual(places.trimmed("short   words"), "short words")


class Window(unittest.TestCase):
    def test_the_name_is_bold_and_each_line_says_who(self) -> None:
        around = Around((PLAYER, "ready?"), (DM, SAID), (PLAYER, "lead on"))
        text = name_places.window_text(
            "Session 4, 1:12:30", around, {PLAYER: "Mia", DM: "Sam"}, ["Hrothgar"]
        )
        self.assertEqual(
            text.split("\n"),
            [
                "_Session 4, 1:12:30_",
                "> Mia: ready?",
                "> Sam: we ride to meet **Hrothgar** at the old gate before dawn",
                "> Mia: lead on",
            ],
        )

    def test_the_edges_have_no_neighbour_and_a_mention_cannot_ping(self) -> None:
        around = Around(None, (DM, "@everyone meet Hrothgar _now_"), None)
        text = name_places.window_text("Session 1, 0:00:01", around, {}, ["Hrothgar"])
        self.assertIn("Someone: ", text)  # no name known
        self.assertNotIn("@everyone", text.replace("@​everyone", ""))
        self.assertEqual(text.count("\n"), 1)

    def test_a_line_respelled_since_is_shown_without_the_bold(self) -> None:
        around = Around(None, (DM, "we meet Hrothgarr"), None)
        text = name_places.window_text("Session 1, 0:00:01", around, {DM: "Sam"}, ["Hrothgar"])
        self.assertNotIn("**", text)

    def test_a_pointer_reads_back(self) -> None:
        self.assertEqual(
            name_places.parse_ref("abc123:5000:77"), name_places.Pointer("abc123", 5000, 77)
        )
        self.assertIsNone(name_places.parse_ref("nonsense"))
        self.assertIsNone(name_places.parse_ref("a:b:c"))


class Fitting(unittest.TestCase):
    def test_windows_that_are_too_much_for_a_card_are_cut_to_fit(self) -> None:
        big = name_places.Places(windows=["_Session 1, 0:00:01_\n" + "> a: " + "x" * 700] * 2)
        big.gone = True
        text = big.text()
        self.assertLessEqual(len(text), 1100)  # what a card can spare
        self.assertTrue(text.startswith("_Session 1"))  # the first window stays

    def test_a_gone_transcript_is_said_once(self) -> None:
        self.assertEqual(name_places.Places(gone=True).text(), name_places.GONE)


class Cards(NamesTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.store = TranscriptStore(self.db)
        self.bot.transcripts = self.store
        self.bot.settings = replace(self.bot.settings, site_url="https://dmbot.example")
        self.sid = await self.store.open_session(GUILD, self.campaign.id, START)
        self.speakers = {DM: "Sam", PLAYER: "Mia"}
        await self.consent.grant(GUILD, DM)  # both agree, so the card may quote them
        await self.consent.grant(GUILD, PLAYER)

    def it(self, user_id: int = DM) -> Any:
        it = super().it(user_id)
        it.guild = SimpleNamespace(
            id=GUILD,
            get_member=lambda uid: (
                SimpleNamespace(display_name=self.speakers[uid]) if uid in self.speakers else None
            ),
            fetch_member=AsyncMock(
                side_effect=discord.NotFound(MagicMock(status=404), "Unknown Member")
            ),
        )
        return it

    async def said(self, count: int, text: str = SAID) -> list[str]:
        """Save `count` lines saying the name and suggest it; the pointers it kept."""
        lines = [
            line(10 * i + 2, PLAYER if i % 2 else DM, text if i % 2 else f"{text} {i}")
            for i in range(count)
        ]
        # a quiet line between each, so every window has neighbours
        quiet = [line(10 * i + 1, PLAYER, "okay then") for i in range(count)]
        await self.store.add_lines(
            GUILD, self.sid, sorted([*lines, *quiet], key=lambda x: x.started_ms)
        )
        stored = await self.store.lines(GUILD, self.sid)
        await self.bot._propose_new_names(
            GUILD, self.campaign.id, [x.text for x in stored], {DM, PLAYER}, self.sid
        )
        return [x.text for x in stored]

    async def entity(self) -> Any:
        found = await self.memory.entities(GUILD, self.campaign.id, statuses=["proposed"])
        self.assertEqual(len(found), 1)
        return found[0]

    async def card(self) -> tuple[ui.SuggestionReview, str]:
        it = self.it()
        await ui.start_review(it, self.campaign.id)
        text, kw = it.response.sent[0]
        return kw["view"], text

    async def test_the_scan_keeps_pointers_and_never_the_words(self) -> None:
        await self.said(10)
        entity = await self.entity()
        refs = await self.memory.mentions(GUILD, self.campaign.id, entity.id)
        self.assertEqual(len(refs), places.MAX_PLACES)
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute("SELECT * FROM memory_mentions")
            rows = await cur.fetchall()
        flat = " ".join(str(v) for row in rows for v in row.values())
        self.assertNotIn("gate", flat)  # no transcript words anywhere in the pointers
        self.assertNotIn("dawn", flat)
        self.assertTrue(
            all(r["method"] == "exact" and r["session_started_at"] == START for r in rows)
        )

    async def test_the_card_shows_two_windows_under_the_count(self) -> None:
        await self.said(10)
        _view, text = await self.card()
        self.assertEqual(text.count("_Session 1, 0:"), 2)
        self.assertIn("**Hrothgar**", text)
        self.assertIn("> Mia: okay then", text)  # the line before
        self.assertLess(text.index("heard"), text.index("_Session 1"))
        self.assertLessEqual(len(text), MESSAGE_MAX)

    async def test_more_places_shows_the_rest_in_a_private_message(self) -> None:
        await self.said(10)
        view, _text = await self.card()
        labels = [getattr(b, "label", "") for b in view.children]
        self.assertIn("🔎 More places", labels)
        self.assertEqual(len(view.places.more), places.MAX_PLACES - 2)
        it = self.it()
        button = next(b for b in view.children if getattr(b, "label", "") == "🔎 More places")
        await button.callback(it)
        text, kw = it.response.sent[-1]
        self.assertTrue(kw.get("ephemeral"))
        self.assertEqual(text.count("_Session 1, 0:"), places.MAX_PLACES - 2)
        self.assertLessEqual(len(text), MESSAGE_MAX)

    async def test_the_link_opens_the_sessions_page(self) -> None:
        await self.said(4)
        view, _text = await self.card()
        link = next(b for b in view.children if getattr(b, "label", "") == "📜 Open in transcript")
        self.assertEqual(
            getattr(link, "url", ""),
            f"https://dmbot.example/account/transcripts/{GUILD}.{self.campaign.id}/1",
        )

    async def test_few_places_have_no_more_button(self) -> None:
        await self.said(2)
        view, text = await self.card()
        self.assertEqual(text.count("_Session 1, 0:"), 2)
        self.assertNotIn("🔎 More places", [getattr(b, "label", "") for b in view.children])

    async def test_a_deleted_transcript_says_so_and_the_count_stays(self) -> None:
        await self.said(6)
        await self.store.end_session(GUILD, self.sid, START + 100)
        gone = await self.store.delete_session_transcript(
            GUILD, self.campaign.id, self.sid, START + 10**9, START + 10**9
        )
        self.assertGreater(gone, 0)
        _view, text = await self.card()
        self.assertIn(name_places.GONE, text)
        self.assertIn("heard 6 times", text)
        self.assertNotIn("**Hrothgar** at", text)

    async def test_a_person_who_withdrew_leaves_no_words_behind(self) -> None:
        await self.said(6)
        await self.store.delete_person(GUILD, DM)
        await self.store.delete_person(GUILD, PLAYER)
        _view, text = await self.card()
        self.assertNotIn("old gate", text)  # no words
        self.assertNotIn("_Session", text)  # and their pointers went with them (their rule)
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute("SELECT count(*) AS n FROM memory_mentions")
            self.assertEqual((await cur.fetchone())["n"], 0)  # type: ignore[index]

    async def test_someone_who_stopped_without_deleting_is_not_quoted(self) -> None:
        await self.said(10)
        await self.consent.revoke(GUILD, PLAYER)  # Stop: their past lines stay in the transcript
        _view, text = await self.card()
        self.assertNotIn("okay then", text)  # (Mia's quiet lines are the neighbours)
        self.assertNotIn("Mia:", text)
        self.assertIn("Sam:", text)  # the DM still agrees

    async def test_an_imported_off_topic_line_is_not_a_place(self) -> None:
        off = line(5, PLAYER, f"{SAID} off the record", topic="off_topic")
        game = line(9, DM, SAID)
        await self.store.add_lines(GUILD, self.sid, [off, game])
        stored = await self.store.lines(GUILD, self.sid)
        await self.bot._propose_new_names(
            GUILD, self.campaign.id, [x.text for x in stored], {DM, PLAYER}, self.sid,
            agreed={DM, PLAYER}, game_only=True,
        )  # fmt: skip
        entity = await self.entity()
        refs = await self.memory.mentions(GUILD, self.campaign.id, entity.id)
        self.assertEqual(len(refs), 1)  # only the game line
        self.assertTrue(refs[0][0].endswith(f":{game.started_ms}:{DM}"))

    async def test_a_bold_name_near_the_cut_is_never_left_open(self) -> None:
        window = name_places.window_text(
            "Session 1, 0:00:01",
            Around(None, (DM, "we meet Hrothgar " + "x" * 60), None),
            {DM: "Sam"},
            ["Hrothgar"],
            width=40,
        )
        places = name_places.Places(windows=[window] * 2)
        for limit in range(30, len(places.text()) + 1, 7):
            with patch.object(name_places, "BLOCK_MAX", limit):
                text = places.text()
            self.assertEqual(text.count("**") % 2, 0, (limit, text))
            self.assertLessEqual(len(text), limit)

    async def test_long_lines_keep_the_card_inside_discords_limit(self) -> None:
        wall = "word " * 200
        await self.said(10, f"{wall}Hrothgar{wall}")
        view, text = await self.card()
        self.assertLessEqual(len(text), MESSAGE_MAX)
        it = self.it()
        button = next(b for b in view.children if getattr(b, "label", "") == "🔎 More places")
        await button.callback(it)
        self.assertLessEqual(len(it.response.sent[-1][0]), MESSAGE_MAX)

    async def test_a_suggestion_with_no_pointers_is_the_card_it_was(self) -> None:
        await self.memory.add_entity(
            GUILD, self.campaign.id, type="concept", name="Hrothgar", source="scan",
            description="Heard 3 times",
        )  # fmt: skip
        view, text = await self.card()
        self.assertNotIn("_Session", text)
        self.assertEqual(
            [getattr(b, "label", "") for b in view.children if "places" in getattr(b, "label", "")],
            [],
        )
