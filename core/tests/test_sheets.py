"""D&D Beyond character sheets (#723): links, the allow-list parser, and the fetch,
without network. The fixture is made up (placeholder names, numbers, and "LEAK" text in
every field that may carry descriptions), never a real character or sourcebook text."""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
import unittest
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, cast
from unittest import mock

import aiohttp

from dmbot.memory import sheets
from dmbot.memory.sheet_refresh import SHEET_HINTS_MAX
from dmbot.memory.sheet_refresh import hint_names as sheet_hint_names
from dmbot.memory.sheet_refresh import refresh as sheet_refresh
from dmbot.memory.sheet_store import CharacterSheet

FIXTURE = Path(__file__).parent / "fixtures" / "dndbeyond_character.json"


def answer() -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(FIXTURE.read_text())
    return loaded


class Links(unittest.TestCase):
    def test_character_links(self) -> None:
        for link in (
            "https://www.dndbeyond.com/characters/12345678",
            "https://dndbeyond.com/characters/12345678",
            "  https://www.dndbeyond.com/characters/12345678/AbCdEf  ",  # a share link
            "https://www.dndbeyond.com/characters/12345678?view=sheet",
            "<https://www.dndbeyond.com/characters/12345678>",  # Discord's no-preview form
            "HTTPS://WWW.DNDBEYOND.COM/characters/12345678",
        ):
            self.assertEqual(sheets.character_id(link), 12345678, link)
        self.assertEqual(
            sheets.sheet_url(12345678), "https://www.dndbeyond.com/characters/12345678"
        )

    def test_anything_else_is_refused(self) -> None:
        for link in (
            "",
            "12345678",
            "http://www.dndbeyond.com/characters/12345678",  # not https
            "https://www.dndbeyond.com/campaigns/12345",
            "https://www.dndbeyond.com/characters/",
            "https://dndbeyond.com.evil.example/characters/1",
            "https://evil.example/?https://www.dndbeyond.com/characters/1",
            "https://www.dndbeyond.com/characters/1 and more",
            "https://www.dndbeyond.com/characters/1234567890123",  # too long a number
        ):
            self.assertIsNone(sheets.character_id(link), link)


class Parsing(unittest.TestCase):
    def setUp(self) -> None:
        self.snapshot = sheets.parse(answer())

    def test_the_allowed_fields(self) -> None:
        s = self.snapshot
        self.assertEqual((s["v"], s["source"], s["name"]), (1, "dndbeyond", "Testa Placeholder"))
        self.assertEqual(s["species"], "Test Species")
        self.assertEqual(
            s["classes"],
            [
                {"name": "Test Class", "level": 3, "subclass": "Test Subclass"},
                {"name": "Second Class", "level": 2, "subclass": None},
            ],
        )
        self.assertEqual(s["level"], 5)
        # dex 14 + 1 bonus; int 16 + 2 from the species; cha overridden to 11.
        self.assertEqual(
            s["abilities"], {"str": 10, "dex": 15, "con": 13, "int": 18, "wis": 12, "cha": 11}
        )
        self.assertEqual(s["max_hp"], 28 + 1 * 5)  # constitution +1 a level
        self.assertEqual(s["ac"], 12 + 2 + 2 + 1)  # light armour + dex, a shield, a bonus
        self.assertEqual((s["speed"], s["proficiency_bonus"]), (30, 3))
        self.assertEqual(s["saves"], ["int", "wis"])
        self.assertEqual(s["skills"], ["arcana", "history"])
        self.assertEqual(s["senses"], {"darkvision": 60})
        self.assertEqual(s["languages"], ["Common", "Test Tongue"])
        self.assertEqual(s["spells"], ["Test Spell", "Other Test Spell", "Species Test Spell"])
        self.assertEqual(s["features"], ["Test Trait", "Test Feature"])  # too long: dropped
        self.assertEqual(s["feats"], ["Test Feat"])
        self.assertEqual(
            s["items"], ["Test Armour", "Test Shield", "Spare Heavy Armour", "Test Rope"]
        )

    def test_nothing_else_is_kept(self) -> None:
        stored = json.dumps(self.snapshot)
        self.assertNotIn("LEAK", stored)  # descriptions, notes, snippets, custom values
        self.assertNotIn("avatar", stored)
        self.assertEqual(
            set(self.snapshot),
            {
                "v", "source", "name", "species", "classes", "level", "abilities", "max_hp",
                "ac", "speed", "proficiency_bonus", "saves", "skills", "senses",
                "languages", "spells", "features", "feats", "items",
            },
        )  # fmt: skip

    def test_text_in_an_allowed_field_is_still_dropped(self) -> None:
        data = answer()
        data["data"]["feats"].append({"definition": {"name": "word " * 40}})
        data["data"]["race"]["fullName"] = "LEAK " * 30
        snapshot = sheets.parse(data)
        self.assertEqual(snapshot["feats"], ["Test Feat"])
        self.assertIsNone(snapshot["species"])

    def test_odd_shapes_never_break_it(self) -> None:
        data = answer()
        data["data"]["classes"] = "nonsense"
        data["data"]["modifiers"] = [1, 2]
        data["data"]["stats"] = [{"id": "x"}, None]
        data["data"]["inventory"] = [None, {"equipped": True}]
        snapshot = sheets.parse(data)
        self.assertEqual((snapshot["classes"], snapshot["level"]), ([], 0))
        self.assertIsNone(snapshot["proficiency_bonus"])
        with self.assertRaises(sheets.SheetError):
            sheets.parse({"data": {"name": ""}})

    def test_hint_names_and_who(self) -> None:
        names = sheets.hint_names(self.snapshot)
        self.assertEqual(names[:3], ["Test Spell", "Other Test Spell", "Species Test Spell"])
        self.assertIn("Test Feat", names)
        self.assertEqual(sheets.who(self.snapshot), "Test Species · Test Class 3 / Second Class 2")


class Cleaning(unittest.TestCase):
    def test_a_stored_snapshot_comes_back_the_same(self) -> None:
        snapshot = sheets.parse(answer())
        self.assertEqual(sheets.clean(json.loads(json.dumps(snapshot))), snapshot)

    def test_a_hand_edited_backup_cannot_smuggle_text(self) -> None:
        snapshot = sheets.parse(answer())
        snapshot["description"] = "LEAK: rules text"
        snapshot["spells"] = [*snapshot["spells"], "LEAK " * 40, 7, None]
        snapshot["abilities"]["str"] = 999
        snapshot["skills"] = ["arcana", "LEAK"]
        snapshot["senses"] = {"darkvision": 60, "LEAK": 5}
        cleaned = sheets.clean(snapshot)
        assert cleaned is not None
        self.assertNotIn("LEAK", json.dumps(cleaned))
        self.assertNotIn("str", cleaned["abilities"])
        self.assertEqual(cleaned["skills"], ["arcana"])

    def test_a_snapshot_is_never_too_big_to_keep(self) -> None:
        names = ["\u0928" * 55 + str(i) for i in range(100)]  # three bytes a letter
        snapshot = sheets.clean(
            {
                "v": 1,
                "source": "typed",
                "name": "X",
                **{k: names for k in ("spells", "features", "feats", "items")},
            }
        )
        assert snapshot is not None
        stored = json.dumps(snapshot, ensure_ascii=False).encode()  # as Postgres keeps it
        self.assertLessEqual(len(stored), sheets.SNAPSHOT_MAX_BYTES)
        self.assertGreater(len(snapshot["spells"]), 10)

    def test_the_size_cap_ends_even_when_only_languages_are_long(self) -> None:
        # The review's case: 100 sixty-character non-ASCII languages, every name list
        # empty. The old trim loop never ended here.
        languages = ["\u0928" * 57 + f"{i:03d}" for i in range(100)]
        long_classes = [{"name": "\u0928" * 60, "level": 1, "subclass": "\u0928" * 60}] * 4
        started = time.monotonic()
        snapshot = sheets.clean(
            {
                "v": 1,
                "source": "typed",
                "name": "X",
                "languages": languages,
                "classes": long_classes,
            }
        )
        self.assertLess(time.monotonic() - started, 1)
        assert snapshot is not None
        stored = json.dumps(snapshot, ensure_ascii=False).encode()
        self.assertLessEqual(len(stored), sheets.SNAPSHOT_MAX_BYTES)

    def test_odd_entries_in_a_backup_are_dropped_not_raised(self) -> None:
        snapshot = sheets.clean(
            {"v": 1, "source": "typed", "name": "X", "skills": [["arcana"], {"a": 1}, "arcana"]}
        )
        assert snapshot is not None
        self.assertEqual(snapshot["skills"], ["arcana"])

    def test_not_a_snapshot(self) -> None:
        bad: object
        for bad in (None, [], {"v": 2, "name": "A", "source": "typed"}, {"v": 1, "source": "x"}):
            self.assertIsNone(sheets.clean(bad))

    def test_the_typed_fallback(self) -> None:
        snapshot = sheets.typed(
            "Testa",
            species="Test Species",
            class_name="Test Class",
            level=4,
            names=[f"Name {n}" for n in range(30)],
        )
        assert snapshot is not None
        self.assertEqual(snapshot["source"], "typed")
        self.assertEqual(sheets.who(snapshot), "Test Species · Test Class 4")
        self.assertEqual(len(snapshot["features"]), sheets.TYPED_NAMES_MAX)


class Hints(unittest.TestCase):
    def sheet(self, *names: str) -> CharacterSheet:
        snapshot = sheets.clean({"v": 1, "source": "typed", "name": "X", "spells": list(names)})
        return CharacterSheet("0" * 32, "X", 1, None, snapshot, 1)

    def test_characters_take_turns_within_the_cap(self) -> None:
        wizard = self.sheet(*(f"Wizard Spell {i}" for i in range(40)))
        fighter = self.sheet("Second Wind", "Action Surge")
        names = sheet_hint_names(
            [wizard, fighter, CharacterSheet("1" * 32, "Y", 2, "u", None, None)]
        )
        self.assertEqual(
            names[:4], ["Wizard Spell 0", "Second Wind", "Wizard Spell 1", "Action Surge"]
        )
        self.assertEqual(len(names), SHEET_HINTS_MAX)
        self.assertEqual(sheet_hint_names([self.sheet("Shield"), self.sheet("shield")]), ["Shield"])


class RefreshStore:
    """Just enough of SheetStore for refresh(): no database."""

    def __init__(self, found: list[CharacterSheet]) -> None:
        self.found = found
        self.saved: list[str] = []

    async def sheets(self, guild_id: int, campaign_id: str) -> list[CharacterSheet]:
        return self.found

    async def save(
        self,
        guild_id: int,
        campaign_id: str,
        entity_id: str,
        snapshot: Any,
        now: int,
        *,
        url: str | None = None,
    ) -> bool:
        self.saved.append(entity_id)
        return True


class Refreshing(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        url = sheets.sheet_url(5)
        self.store = RefreshStore(
            [
                CharacterSheet("a" * 32, "A", 1, url, None, None),
                CharacterSheet("b" * 32, "B", 2, None, {"v": 1}, 1),  # typed: nothing to read
                CharacterSheet("c" * 32, "C", 3, url, None, None),
            ]
        )
        self.read: list[int] = []

    async def fetch(self, character: int) -> dict[str, Any]:
        self.read.append(character)
        if len(self.read) == 1:
            raise sheets.SheetError("down")
        return sheets.parse(answer())

    async def test_only_linked_sheets_are_read_and_a_failure_keeps_going(self) -> None:
        with self.assertLogs("dmbot.memory.sheet_refresh", "INFO"):
            await sheet_refresh(self.store, 1, "c", 5, fetch=self.fetch)
        self.assertEqual(self.read, [5, 5])
        self.assertEqual(self.store.saved, ["c" * 32])  # the first failed: its old one stays

    async def test_a_cancelled_refresh_closes_its_session(self) -> None:
        closed: list[bool] = []

        class Session:
            async def __aenter__(self) -> Session:
                return self

            async def __aexit__(self, *_: object) -> None:
                closed.append(True)

        async def hang(character: int, *, session: Any) -> dict[str, Any]:
            await asyncio.Event().wait()
            return {}

        with (
            mock.patch.object(sheets, "new_session", Session),
            mock.patch.object(sheets, "fetch", hang),
        ):
            run = asyncio.ensure_future(sheet_refresh(self.store, 1, "c", 5))
            for _ in range(5):
                await asyncio.sleep(0)
            run.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await run
        self.assertEqual(closed, [True])

    async def test_a_session_that_ended_stops_reading(self) -> None:
        await sheet_refresh(
            self.store,
            1,
            "c",
            5,
            fetch=self.fetch,
            still_wanted=lambda: not self.read,
        )
        self.assertEqual(self.read, [5])


class FakeContent:
    """A body that arrives in small pieces, as a real one does."""

    def __init__(self, body: bytes) -> None:
        self.body = body

    async def iter_chunked(self, size: int) -> AsyncIterator[bytes]:
        for start in range(0, len(self.body), 1000):
            yield self.body[start : start + 1000]


class FakeResponse:
    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self.content = FakeContent(body)

    async def __aenter__(self) -> FakeResponse:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None


class FakeSession:
    def __init__(self, status: int, body: bytes = b"{}") -> None:
        self.response = FakeResponse(status, body)
        self.urls: list[str] = []

    def get(self, url: str, **_: Any) -> FakeResponse:
        self.urls.append(url)
        return self.response


class Fetching(unittest.IsolatedAsyncioTestCase):
    async def test_a_public_sheet(self) -> None:
        session = FakeSession(200, json.dumps(answer()).encode())
        snapshot = await sheets.fetch(42, session=session)  # type: ignore[arg-type]
        self.assertEqual(snapshot["name"], "Testa Placeholder")
        self.assertEqual(
            session.urls, ["https://character-service.dndbeyond.com/character/v5/character/42"]
        )

    async def test_not_public_tells_the_player_how(self) -> None:
        for session in (
            FakeSession(403),
            FakeSession(404),
            FakeSession(200, b'{"success": false}'),
        ):
            with self.assertRaises(sheets.SheetError) as caught:
                await sheets.fetch(42, session=session)  # type: ignore[arg-type]
            self.assertTrue(caught.exception.refused)
            self.assertEqual(str(caught.exception), sheets.NOT_PUBLIC)

    async def test_a_redirect_is_refused(self) -> None:
        with self.assertRaises(sheets.SheetError) as caught:
            await sheets.fetch(42, session=FakeSession(302))  # type: ignore[arg-type]
        self.assertFalse(caught.exception.refused)

    async def test_a_slow_answer_times_out(self) -> None:
        class Slow(FakeSession):
            def get(self, url: str, **_: Any) -> Any:
                outer = self

                class Hang:
                    async def __aenter__(self) -> FakeResponse:
                        await asyncio.Event().wait()
                        return outer.response

                    async def __aexit__(self, *_: object) -> None:
                        return None

                return Hang()

        with (
            mock.patch.object(sheets, "FETCH_TIMEOUT_S", 0.05),
            self.assertRaises(sheets.SheetError),
        ):
            await sheets.fetch(42, session=Slow(200))  # type: ignore[arg-type]

    async def test_two_at_a_time_across_the_process(self) -> None:
        inside, most = 0, 0
        release = asyncio.Event()

        class Counting(FakeSession):
            def get(self, url: str, **_: Any) -> Any:
                outer = self

                class Count:
                    async def __aenter__(self) -> FakeResponse:
                        nonlocal inside, most
                        inside += 1
                        most = max(most, inside)
                        await release.wait()
                        return outer.response

                    async def __aexit__(self, *_: object) -> None:
                        nonlocal inside
                        inside -= 1

                return Count()

        body = json.dumps(answer()).encode()
        session = cast(aiohttp.ClientSession, Counting(200, body))
        runs = [asyncio.ensure_future(sheets.fetch(n, session=session)) for n in range(5)]
        for _ in range(10):
            await asyncio.sleep(0)
        self.assertEqual(most, sheets.FETCHES_AT_ONCE)
        release.set()
        await asyncio.gather(*runs)

    async def test_a_body_in_many_pieces_is_read_whole(self) -> None:
        body = json.dumps(answer()).encode()
        self.assertGreater(len(body), 3000)  # several pieces
        snapshot = await sheets.fetch(42, session=FakeSession(200, body))  # type: ignore[arg-type]
        self.assertEqual(snapshot["spells"][0], "Test Spell")

    async def test_odd_numbers_in_an_answer_are_a_sheet_error(self) -> None:
        data = answer()
        data["data"]["baseHitPoints"] = float("inf")
        data["data"]["stats"][0]["value"] = float("nan")
        body = json.dumps(data).encode()  # NaN and Infinity, as json allows
        snapshot = await sheets.fetch(42, session=FakeSession(200, body))  # type: ignore[arg-type]
        self.assertIsNone(snapshot["max_hp"])
        self.assertNotIn("str", snapshot["abilities"])

    async def test_other_failures(self) -> None:
        for session in (
            FakeSession(500),
            FakeSession(200, b"not json"),
            FakeSession(200, b"[]"),
            FakeSession(200, b"x" * (sheets.MAX_BYTES + 1)),
        ):
            with self.assertRaises(sheets.SheetError) as caught:
                await sheets.fetch(42, session=session)  # type: ignore[arg-type]
            self.assertFalse(caught.exception.refused)


if __name__ == "__main__":
    unittest.main()
