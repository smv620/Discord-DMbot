"""Character sheets D3 (#1126): the player's view of their own sheet, section by section, and
the edit forms for a typed or document sheet. No Edit on a D&D Beyond sheet."""

from __future__ import annotations

import copy
import unittest
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import discord

from dmbot.memory import sheet_view, sheets
from dmbot.memory.sheet_store import CharacterSheet, PlayerCharacter, SheetRefused
from dmbot.ui import sheet_view as ui
from dmbot.ui import sheets as sheets_ui
from tests.test_sheet_checks import belleros
from tests.test_sheets_ui import CAMPAIGN, ENTITY, GUILD, PLAYER, interaction

CHARACTER = PlayerCharacter(CAMPAIGN, "Frostmaiden", ENTITY, "Belleros")
URL = "https://www.dndbeyond.com/characters/42"
NO_SENSES = {"darkvision": "", "blindsight": "", "tremorsense": "", "truesight": ""}


def ddb() -> dict[str, Any]:
    return sheets.parse(belleros())


def typed() -> dict[str, Any]:
    snap = dict(ddb())
    snap["source"] = "typed"
    cleaned = sheets.clean(snap)
    assert cleaned is not None
    return cleaned


def sheet_of(snapshot: dict[str, Any], *, linked: bool) -> CharacterSheet:
    return CharacterSheet(ENTITY, "Belleros", PLAYER, URL if linked else None, snapshot, 1700000000)


class Showing(unittest.TestCase):
    def test_the_basic_line_and_every_section(self) -> None:
        snap = ddb()
        self.assertEqual(
            sheet_view.basic_line(snap), "Belleros, Ranger 3 + Druid 3, HP 11 / 26 + 0, AC 18"
        )
        shown = {s.key: s.show(snap) for s in sheet_view.SECTIONS}
        self.assertEqual(
            [s.label for s in sheet_view.SECTIONS],
            ["Abilities", "Conditions", "Defenses", "Skills", "Proficiencies",
             "Features and feats", "Background", "Speed and senses", "Languages", "Inventory"],
        )  # fmt: skip
        self.assertIn("DEX 16 (+3)", shown["abilities"])
        self.assertIn("Poisoned", shown["conditions"])
        self.assertIn("Exhaustion 2", shown["conditions"])
        self.assertIn("Fire", shown["defenses"])
        self.assertIn("Expert: Perception", shown["skills"])
        self.assertIn("Herbalism Kit", shown["proficiencies"])
        self.assertIn("Favored Enemy", shown["features"])
        self.assertEqual(shown["background"], "Outlander")
        self.assertIn("Speed 35 ft", shown["speed"])
        self.assertIn("Darkvision 60 ft", shown["speed"])
        self.assertIn("Elvish", shown["languages"])
        self.assertIn("5 items, 21 lb", shown["inventory"])

    def test_skills_read_as_a_player_writes_them_and_buttons_fit_a_phone(self) -> None:
        snap = {**typed(), "skills": ["sleight-of-hand", "animal-handling"], "expertise": []}
        text = {s.key: s.show(snap) for s in sheet_view.SECTIONS}["skills"]
        self.assertEqual(text, "Trained: Sleight of Hand, Animal Handling")
        self.assertEqual(
            sheet_view.BY_KEY["skills"].fill(snap)["trained"], "Sleight of Hand, Animal Handling"
        )
        self.assertEqual([s.button for s in sheet_view.SECTIONS if s.short], ["Features", "Speed"])

    def test_exhaustion_level_zero_means_no_exhaustion(self) -> None:
        snap = typed()
        new, _ = sheet_view.edit(
            snap, sheet_view.BY_KEY["conditions"], {"names": "Exhaustion, Prone", "exhaustion": "0"}
        )
        assert new is not None
        self.assertEqual((new["conditions"], new["exhaustion"]), (["Prone"], 0))

    def test_empty_parts_say_so_in_plain_words(self) -> None:
        empty = sheets.typed("Mira", species="Elf", class_name="Wizard", level=3, names=[])
        assert empty is not None
        for section in sheet_view.SECTIONS:
            self.assertTrue(section.show(empty).strip())

    def test_a_long_list_is_shortened(self) -> None:
        snap = {**ddb(), "items": [f"Item {i}" for i in range(60)]}
        text = {s.key: s.show(snap) for s in sheet_view.SECTIONS}["inventory"]
        self.assertIn("and 35 more", text)


class Editing(unittest.TestCase):
    def test_every_form_filled_as_it_is_changes_nothing(self) -> None:
        snap = typed()
        for section in sheet_view.SECTIONS:
            new, problem = sheet_view.edit(snap, section, section.fill(snap))
            self.assertIsNone(problem, section.key)
            assert new is not None
            self.assertEqual(section.show(new), section.show(snap), section.key)

    def test_each_form_saves_through_the_allow_list(self) -> None:
        snap = typed()
        by = sheet_view.BY_KEY
        cases = {
            "abilities": {"scores": "8 10 12 14 16 18"},
            "conditions": {"names": "prone, Blinded", "exhaustion": "3"},
            "defenses": {"resistances": "Fire", "immunities": "", "vulnerabilities": "Cold"},
            "skills": {"trained": "Sleight of Hand, Stealth", "expert": "stealth"},
            "proficiencies": {"tools": "Dice set", "weapon_profs": "", "armor_profs": "Shields"},
            "features": {"features": "Rage", "feats": "Alert"},
            "background": {"background": "Sage"},
            "speed": {**NO_SENSES, "speed": "40", "blindsight": "10"},
            "languages": {"languages": "Common; Dwarvish"},
            "inventory": {"items": "Rope", "weight": "5.5", "rarity": "common 2, rare 1"},
        }  # fmt: skip
        self.assertEqual(set(cases), set(by))
        for key, values in cases.items():
            new, problem = sheet_view.edit(snap, by[key], values)
            self.assertIsNone(problem, key)
            assert new is not None
            self.assertEqual(new["name"], "Belleros")
        out = sheet_view.edit(snap, by["abilities"], cases["abilities"])[0]
        assert out is not None
        self.assertEqual(out["abilities"]["wis"], 16)
        out = sheet_view.edit(snap, by["conditions"], cases["conditions"])[0]
        assert out is not None
        self.assertEqual(
            (out["conditions"], out["exhaustion"]), (["Blinded", "Exhaustion", "Prone"], 3)
        )
        out = sheet_view.edit(snap, by["skills"], cases["skills"])[0]
        assert out is not None
        self.assertEqual(
            (out["skills"], out["expertise"]), (["sleight-of-hand", "stealth"], ["stealth"])
        )
        out = sheet_view.edit(snap, by["inventory"], cases["inventory"])[0]
        assert out is not None
        self.assertEqual(out["inventory"]["count"], 3)
        self.assertEqual(out["inventory"]["weight"], 5.5)

    def test_mistakes_are_one_plain_line_and_save_nothing(self) -> None:
        snap = typed()
        by = sheet_view.BY_KEY
        for key, values, word in (
            ("abilities", {"scores": "8 10 12"}, "six numbers"),
            ("abilities", {"scores": "8 10 12 14 16 99"}, "from 1 to"),
            ("conditions", {"names": "Sleepy", "exhaustion": "0"}, "isn't a condition"),
            ("conditions", {"names": "", "exhaustion": "9"}, "from 0 to 6"),
            ("skills", {"trained": "Juggling", "expert": ""}, "isn't a skill"),
            ("skills", {"trained": "Stealth", "expert": "Arcana"}, "add it to Trained skills"),
            ("speed", {**NO_SENSES, "speed": "fast"}, "digits"),
            ("inventory", {"items": "", "weight": "heavy", "rarity": ""}, "pounds"),
            ("inventory", {"items": "", "weight": "1", "rarity": "shiny 3"}, "isn't a rarity"),
        ):  # fmt: skip
            new, problem = sheet_view.edit(snap, by[key], values)
            self.assertIsNone(new, key)
            self.assertIn(word, problem or "", key)

    def test_text_in_a_name_box_is_still_only_a_name(self) -> None:
        snap = typed()
        new, _ = sheet_view.edit(
            snap, sheet_view.BY_KEY["features"], {"features": "word " * 40 + ", Rage", "feats": ""}
        )
        assert new is not None
        self.assertEqual(new["features"], ["Rage"])

    def test_a_dndbeyond_snapshot_is_never_edited(self) -> None:
        new, problem = sheet_view.edit(
            ddb(), sheet_view.BY_KEY["skills"], {"trained": "", "expert": ""}
        )
        self.assertIsNone(new)
        self.assertIn("D&D Beyond", problem or "")

    def test_what_changed_is_named(self) -> None:
        old = typed()
        new, _ = sheet_view.edit(old, sheet_view.BY_KEY["background"], {"background": "Sage"})
        assert new is not None
        self.assertEqual(sheet_view.changed_sections(old, new), ["Background"])
        self.assertEqual(sheet_view.changed_sections(old, copy.deepcopy(old)), [])


def labels_of(view: discord.ui.View) -> list[str]:
    return [str(getattr(b, "label", "")) for b in view.children]


def has_link(view: discord.ui.View) -> bool:
    return any(getattr(b, "url", None) for b in view.children)


class Views(unittest.IsolatedAsyncioTestCase):
    def it(self, current: CharacterSheet | None, *, plays: bool = True) -> Any:
        it = interaction([CHARACTER] if plays else [], current)
        it.guild = SimpleNamespace(id=GUILD)
        it.client.get_guild = MagicMock(
            return_value=MagicMock(get_member=MagicMock(return_value=MagicMock()))
        )
        it.client.sheets_changed = MagicMock()
        it.client.campaigns.get = AsyncMock(return_value=None)
        return it

    def told(self, it: Any) -> str:
        """Everything said to this person, in a reply or a follow-up (a slow step answers first)."""
        calls = it.response.send_message.await_args_list + it.followup.send.await_args_list
        return " ".join(str(c) for c in calls)

    def sent(self, it: Any) -> tuple[str, discord.ui.View]:
        call = it.followup.send.await_args or it.response.send_message.await_args
        return str(call.args[0]), call.kwargs["view"]

    async def test_a_typed_sheet_opens_a_part_and_offers_edit(self) -> None:
        it = self.it(sheet_of(typed(), linked=False))
        await ui.show_my_sheet(it, GUILD, CHARACTER)
        text, view = self.sent(it)
        self.assertIn("Belleros, Ranger 3 + Druid 3, HP 11 / 26 + 0, AC 18", text)
        self.assertEqual(len(view.children), 10)  # one button per part, no Edit yet
        self.assertFalse([b for b in labels_of(view) if b.startswith(ui.EDIT_LABEL + " ")])
        self.assertFalse(has_link(view))
        await cast(Any, view.children[3]).callback(it)  # Skills
        edited = it.response.edit_message.await_args or it.edit_original_response.await_args
        self.assertIn("Trained: Perception, Survival", edited.kwargs["content"])
        self.assertIn("Edit Skills", labels_of(edited.kwargs["view"]))

    async def test_a_dndbeyond_sheet_has_a_link_and_no_edit_anywhere(self) -> None:
        it = self.it(sheet_of(ddb(), linked=True))
        await ui.show_my_sheet(it, GUILD, CHARACTER)
        text, view = self.sent(it)
        self.assertTrue(has_link(view))
        self.assertIn(ui.DDB_LABEL, labels_of(view))
        self.assertFalse([b for b in labels_of(view) if b.startswith(ui.EDIT_LABEL + " ")])
        self.assertIn("change it there", text)
        await cast(Any, view.children[3]).callback(it)
        edited = it.response.edit_message.await_args or it.edit_original_response.await_args
        self.assertFalse(
            [b for b in labels_of(edited.kwargs["view"]) if b.startswith(ui.EDIT_LABEL + " ")]
        )
        self.assertTrue(has_link(edited.kwargs["view"]))

    async def test_someone_who_no_longer_plays_it_sees_nothing(self) -> None:
        it = self.it(sheet_of(typed(), linked=False), plays=False)
        await ui.show_my_sheet(it, GUILD, CHARACTER)
        self.assertIn(
            sheets_ui.NOT_YOURS,
            str(it.followup.send.await_args or it.response.send_message.await_args),
        )

    async def test_no_sheet_says_so(self) -> None:
        it = self.it(None)
        await ui.show_my_sheet(it, GUILD, CHARACTER)
        self.assertIn(
            ui.NO_SHEET, str(it.followup.send.await_args or it.response.send_message.await_args)
        )

    async def submit(
        self, it: Any, section: str, values: dict[str, str], snapshot: dict[str, Any]
    ) -> None:
        form = ui.SectionForm(GUILD, CHARACTER, sheet_view.BY_KEY[section], snapshot)
        for key, text in values.items():
            form._inputs[key]._value = text
        await form.on_submit(it)

    async def test_an_edit_is_checked_saved_and_the_card_says_what_changed(self) -> None:
        snap = typed()
        it = self.it(sheet_of(snap, linked=False))
        await self.submit(it, "background", {"background": "Sage"}, snap)
        it.client.sheets.save.assert_awaited_once()
        saved = it.client.sheets.save.await_args
        self.assertEqual(saved.args[3]["background"], "Sage")
        self.assertEqual(saved.kwargs["player"], PLAYER)
        text = str(it.followup.send.await_args.args[0])
        self.assertIn("Changed: Background", text)
        self.assertIn("DMbot read your sheet", text)
        self.assertTrue(it.followup.send.await_args.kwargs["ephemeral"])
        it.client.sheets_changed.assert_called_once_with(GUILD, CAMPAIGN)

    async def test_a_mistake_saves_nothing(self) -> None:
        snap = typed()
        it = self.it(sheet_of(snap, linked=False))
        await self.submit(it, "abilities", {"scores": "1 2"}, snap)
        it.client.sheets.save.assert_not_awaited()
        self.assertIn("six numbers", self.told(it))

    async def test_no_change_says_so(self) -> None:
        snap = typed()
        it = self.it(sheet_of(snap, linked=False))
        form = ui.SectionForm(GUILD, CHARACTER, sheet_view.BY_KEY["background"], snap)
        await form.on_submit(it)  # the boxes still hold what was there
        it.client.sheets.save.assert_not_awaited()
        self.assertIn(ui.SAME, self.told(it))

    async def test_an_edit_by_someone_who_no_longer_plays_is_refused(self) -> None:
        snap = typed()
        it = self.it(sheet_of(snap, linked=False), plays=False)
        await self.submit(it, "background", {"background": "Sage"}, snap)
        it.client.sheets.save.assert_not_awaited()
        self.assertIn(sheets_ui.NOT_YOURS, self.told(it))

    async def test_the_store_refusing_the_save_is_told_too(self) -> None:
        snap = typed()
        it = self.it(sheet_of(snap, linked=False))
        it.client.sheets.save = AsyncMock(side_effect=SheetRefused("not yours"))
        await self.submit(it, "background", {"background": "Sage"}, snap)
        self.assertIn(sheets_ui.NOT_YOURS, self.told(it))

    async def test_a_sheet_that_became_a_dndbeyond_link_is_not_edited(self) -> None:
        snap = typed()
        it = self.it(sheet_of(snap, linked=True))
        await self.submit(it, "background", {"background": "Sage"}, snap)
        it.client.sheets.save.assert_not_awaited()
        self.assertIn("D&D Beyond", self.told(it))

    def test_the_form_fits_discords_limits(self) -> None:
        snap = typed()
        for section in sheet_view.SECTIONS:
            self.assertLessEqual(len(section.fields), 5, section.key)  # a form has 5 boxes
            self.assertLessEqual(len(f"Edit: {section.label}"), 45)
            for field in section.fields:
                self.assertLessEqual(len(field.label), 45, field.key)
                self.assertLessEqual(len(field.placeholder), 100)
            form = ui.SectionForm(GUILD, CHARACTER, section, snap)
            self.assertEqual(len(form.children), len(section.fields))

    def test_the_panel_has_show_my_sheet_only_with_a_sheet(self) -> None:
        with_sheet = sheets_ui.SheetPanel(GUILD, CHARACTER, has_sheet=True)
        without = sheets_ui.SheetPanel(GUILD, CHARACTER, has_sheet=False)
        self.assertIn(sheets_ui.SHOW_LABEL, labels_of(with_sheet))
        self.assertNotIn(sheets_ui.SHOW_LABEL, labels_of(without))
