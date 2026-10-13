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

    def test_text_in_a_name_box_is_refused_in_plain_words(self) -> None:
        new, problem = sheet_view.edit(
            typed(),
            sheet_view.BY_KEY["features"],
            {"features": "word " * 40 + ", Rage", "feats": ""},
        )
        self.assertIsNone(new)
        self.assertIn("is too long for a name", problem or "")

    def test_a_sign_before_a_sign_is_a_mistake_not_a_crash(self) -> None:
        new, problem = sheet_view.edit(
            typed(),
            sheet_view.BY_KEY["speed"],
            {
                "speed": "--5",
                "darkvision": "",
                "blindsight": "",
                "tremorsense": "",
                "truesight": "",
            },
        )
        self.assertIsNone(new)
        self.assertIn("digits only", problem or "")

    def test_a_rarity_amount_over_the_limit_is_said_not_dropped(self) -> None:
        new, problem = sheet_view.edit(
            typed(),
            sheet_view.BY_KEY["inventory"],
            {"items": "", "weight": "1", "rarity": "common 9999"},
        )
        self.assertIsNone(new)
        self.assertIn("at most", problem or "")

    def test_a_full_size_sheet_submitted_as_filled_keeps_every_stored_value(self) -> None:
        names = [f"Name {i:02d} " + "x" * 50 for i in range(sheets.LIST_MAX)]  # 100 long names
        snap = typed()
        for key in ("features", "feats", "languages", "items", "tools", "weapon_profs"):
            snap[key] = list(names)
        snap = sheets.clean(snap) or {}
        self.assertGreater(
            len(", ".join(snap["features"])), sheet_view.FIELD_MAX
        )  # overflows a box
        for section in sheet_view.SECTIONS:
            form = ui.SectionForm(GUILD, CHARACTER, section, snap)
            values = {k: str(box.default or "") for k, box in form._inputs.items()}
            for box in form._inputs.values():
                self.assertLessEqual(len(box.default or ""), sheet_view.FIELD_MAX)
            new, problem = sheet_view.edit(snap, section, values, kept=form._kept)
            assert new is not None, (section.key, problem)
            for key in sheet_view.OWNS[section.key]:
                self.assertEqual(new.get(key), snap.get(key), (section.key, key))

    def test_an_unchanged_rarity_box_keeps_a_documents_count(self) -> None:
        old = sheets.clean(
            {
                **typed(),
                "origin": sheets.ORIGIN_DOCUMENT,
                "inventory": {
                    "count": 40,
                    "weight": 12.0,
                    "rarity": {**dict.fromkeys(sheets.RARITIES, 0), "common": 3},
                },
            }
        )
        assert old is not None
        section = sheet_view.BY_KEY["inventory"]
        values = section.fill(old)
        new, _ = sheet_view.edit(old, section, values)
        assert new is not None
        self.assertEqual(new["inventory"]["count"], 40)
        self.assertEqual(sheet_view.changed_sections(old, new), [])
        values["rarity"] = "common 5"  # the player did change it: now the count is theirs
        new, _ = sheet_view.edit(old, section, values)
        assert new is not None
        self.assertEqual(new["inventory"]["count"], 5)

    def test_a_background_that_is_too_long_is_said_not_dropped(self) -> None:
        old = {**typed(), "background": "Sage"}
        new, problem = sheet_view.edit(
            old, sheet_view.BY_KEY["background"], {"background": "x" * 80}
        )
        self.assertIsNone(new)
        self.assertIn("too long for a name", problem or "")

    def test_more_than_the_list_limit_is_refused_in_plain_words(self) -> None:
        full = [f"Lang {i:03d}" for i in range(sheets.LIST_MAX)]
        old = sheets.clean({**typed(), "languages": full}) or {}
        section = sheet_view.BY_KEY["languages"]
        new, problem = sheet_view.edit(old, section, {"languages": ", ".join([*full, "Extra"])})
        self.assertIsNone(new)
        self.assertIn(f"at most {sheets.LIST_MAX} names", problem or "")

    def test_every_section_has_its_stored_keys(self) -> None:
        self.assertEqual({s.key for s in sheet_view.SECTIONS}, set(sheet_view.OWNS))

    def test_a_name_after_the_25th_is_a_change(self) -> None:
        names = [f"Feature {i:02d}" for i in range(40)]
        old = sheets.clean({**typed(), "features": names}) or {}
        renamed = [*names[:28], "Renamed", *names[29:]]
        new, _ = sheet_view.edit(
            old,
            sheet_view.BY_KEY["features"],
            {"features": ", ".join(renamed), "feats": ", ".join(old.get("feats", []))},
        )
        assert new is not None
        self.assertEqual(sheet_view.changed_sections(old, new), ["Features and feats"])

    def test_an_item_count_with_no_rarities_is_kept_on_a_document_sheet(self) -> None:
        old = sheets.clean(
            {
                **typed(),
                "origin": sheets.ORIGIN_DOCUMENT,
                "inventory": {
                    "count": 40,
                    "weight": 12.0,
                    "rarity": dict.fromkeys(sheets.RARITIES, 0),
                },
            }
        )
        assert old is not None
        new, _ = sheet_view.edit(
            old, sheet_view.BY_KEY["inventory"], {"items": "Rope", "weight": "15", "rarity": ""}
        )
        assert new is not None
        self.assertEqual(new["inventory"]["count"], 40)
        self.assertEqual(new["inventory"]["weight"], 15.0)

    def test_a_fixed_part_loses_its_flag(self) -> None:
        old = {**typed(), "flags": ["abilities", "basic"]}
        new, _ = sheet_view.edit(
            old, sheet_view.BY_KEY["abilities"], {"scores": "10 14 12 8 10 13"}
        )
        assert new is not None
        self.assertNotIn("abilities", new.get("flags") or [])
        self.assertIn("basic", new.get("flags") or [])  # the part not edited stays flagged

    def test_the_message_never_goes_over_discords_limit(self) -> None:
        names = [f"Name {i:02d} " + "x" * 50 for i in range(sheets.LIST_MAX)]
        full = (
            sheets.clean(
                {
                    **typed(),
                    **{
                        k: list(names)
                        for k in (
                            "features",
                            "feats",
                            "languages",
                            "items",
                            "tools",
                            "weapon_profs",
                            "armor_profs",
                            "resistances",
                            "immunities",
                            "vulnerabilities",
                        )
                    },
                }
            )
            or {}
        )
        for section in sheet_view.SECTIONS:
            text = ui.view_text(CHARACTER, sheet_of(full, linked=False), section.key)
            self.assertLessEqual(len(text), 2000, section.key)
        text = ui.view_text(CHARACTER, sheet_of(full, linked=False), "proficiencies")
        self.assertIn("and more", text)

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

    async def test_every_press_checks_the_person_is_still_in_the_server(self) -> None:
        it = self.it(sheet_of(typed(), linked=False))
        await ui.show_my_sheet(it, GUILD, CHARACTER)
        _, view = self.sent(it)
        gone = self.it(sheet_of(typed(), linked=False))
        guild = MagicMock(get_member=MagicMock(return_value=None))
        guild.fetch_member = AsyncMock(
            side_effect=discord.NotFound(MagicMock(status=404), "unknown member")
        )
        gone.client.get_guild = MagicMock(return_value=guild)
        await cast(Any, view.children[3]).callback(gone)  # Skills
        self.assertIn(sheets_ui.NOT_A_MEMBER, self.told(gone))
        self.assertFalse(view.is_finished())  # a refusal leaves the buttons as they were

    async def test_a_part_opens_after_answering_discord_and_edit_asks_nothing_first(self) -> None:
        it = self.it(sheet_of(typed(), linked=False))
        await ui.show_my_sheet(it, GUILD, CHARACTER)
        _, view = self.sent(it)
        press = self.it(sheet_of(typed(), linked=False))
        await cast(Any, view.children[3]).callback(press)
        press.response.defer.assert_awaited()  # answered before the look-ups
        edited = press.edit_original_response.await_args or press.response.edit_message.await_args
        opened = edited.kwargs["view"]
        again = self.it(sheet_of(typed(), linked=False))
        edit = next(b for b in opened.children if str(b.label).startswith(ui.EDIT_LABEL + " "))
        await cast(Any, edit).callback(again)
        again.response.send_modal.assert_awaited_once()

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

    async def test_a_form_opened_before_a_save_cannot_revert_it(self) -> None:
        snap = typed()
        it = self.it(sheet_of(snap, linked=False))
        await ui.show_my_sheet(it, GUILD, CHARACTER)
        _, view = self.sent(it)
        shown = cast(Any, view)
        # The player opens Skills and saves a change through the view's own form.
        opened = ui.SectionForm(GUILD, CHARACTER, sheet_view.BY_KEY["skills"], snap, view=shown)
        opened._inputs["trained"]._value = "Perception"
        fresh = copy.deepcopy(snap)
        await opened.on_submit(it)
        saved = it.client.sheets.save.await_args.args[3]
        self.assertEqual(saved["skills"], ["perception"])
        self.assertEqual(shown.snapshot["skills"], ["perception"])  # the view moved on
        shown.origin = SimpleNamespace(edit_original_response=AsyncMock())
        opened2 = ui.SectionForm(GUILD, CHARACTER, sheet_view.BY_KEY["skills"], saved, view=shown)
        opened2._inputs["trained"]._value = "Perception, Stealth"
        shown.open_key = "skills"
        await opened2.on_submit(self.it(sheet_of(saved, linked=False)))
        redrawn = shown.origin.edit_original_response.await_args.kwargs["content"]
        self.assertIn("Stealth", redrawn)  # the open part shows what was just saved
        # The store now holds the save; a form filled from the OLD snapshot must not undo it.
        again = self.it(sheet_of(saved, linked=False))
        stale = ui.SectionForm(GUILD, CHARACTER, sheet_view.BY_KEY["skills"], fresh)
        await stale.on_submit(again)
        again.client.sheets.save.assert_not_awaited()
        self.assertIn(ui.CHANGED_SINCE, self.told(again))
        # A form from the view after the save starts from it and goes through.
        edit = self.it(sheet_of(shown.snapshot, linked=False))
        current = ui.SectionForm(GUILD, CHARACTER, sheet_view.BY_KEY["skills"], shown.snapshot)
        current._inputs["trained"]._value = "Perception"
        await current.on_submit(edit)
        edit.client.sheets.save.assert_awaited_once()

    async def test_names_a_box_cannot_show_are_said_to_be_kept(self) -> None:
        names = [f"Name {i:02d} " + "x" * 50 for i in range(70)]
        snap = sheets.clean({**typed(), "features": names}) or {}
        form = ui.SectionForm(GUILD, CHARACTER, sheet_view.BY_KEY["features"], snap)
        hidden = len(form._kept["features"])
        self.assertGreater(hidden, 0)
        box = form._inputs["features"]
        box.default = None  # a cleared box shows the placeholder
        self.assertIn(f"{hidden} more names", str(box.placeholder))

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
