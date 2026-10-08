"""D&D Beyond character sheets (#723): links, the allow-list parser, and the fetch,
without network. The fixture is made up (placeholder names, numbers, and "LEAK" text in
every field that may carry descriptions), never a real character or sourcebook text."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from dmbot.memory import sheets

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


class FakeResponse:
    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self.content = mock.Mock(read=mock.AsyncMock(return_value=body))

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
            self.assertFalse(caught.exception.public)
            self.assertEqual(str(caught.exception), sheets.NOT_PUBLIC)

    async def test_other_failures(self) -> None:
        for session in (
            FakeSession(500),
            FakeSession(200, b"not json"),
            FakeSession(200, b"[]"),
            FakeSession(200, b"x" * (sheets.MAX_BYTES + 1)),
        ):
            with self.assertRaises(sheets.SheetError) as caught:
                await sheets.fetch(42, session=session)  # type: ignore[arg-type]
            self.assertTrue(caught.exception.public)


if __name__ == "__main__":
    unittest.main()
