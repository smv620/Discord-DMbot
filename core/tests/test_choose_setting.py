"""The DM's own pick of the campaign's setting by name (#4291): one change, one Undo."""

from __future__ import annotations

from dmbot.memory.models import CONFIRMED, MemoryRuleError
from dmbot.memory.models import DM as BY_DM
from tests.test_memory_store import GUILD_A, GUILD_B, MemoryTest


class Choosing(MemoryTest):
    async def setting_name(self) -> str | None:
        found = await self.memory.setting(GUILD_A, self.c)
        return found.name if found else None

    async def test_a_new_world_is_made_shown_and_the_dms_word(self) -> None:
        made = await self.memory.choose_setting(GUILD_A, self.c, "Golarion", source=BY_DM)
        place = made.value
        self.assertEqual((place.place_level, place.hidden_from_players), ("world", False))
        self.assertEqual((place.setting, place.setting_from), (True, BY_DM))
        self.assertEqual(await self.setting_name(), "Golarion")

    async def test_one_undo_takes_all_of_it_back(self) -> None:
        old = await self.add("Faerûn", type="place", status=CONFIRMED)
        await self.memory.set_place_level(GUILD_A, self.c, old, "world", source=BY_DM)
        await self.memory.set_setting(GUILD_A, self.c, old, source=BY_DM)
        made = await self.memory.choose_setting(GUILD_A, self.c, "Golarion", source=BY_DM)
        self.assertEqual(await self.setting_name(), "Golarion")
        assert made.batch is not None
        await self.memory.undo(GUILD_A, self.c, made.batch)
        self.assertEqual(await self.setting_name(), "Faerûn")

    async def test_a_place_of_that_name_is_used_not_made_twice(self) -> None:
        own = await self.add("Eberron", type="place", status=CONFIRMED)
        await self.memory.set_place_level(GUILD_A, self.c, own, "world", source=BY_DM)
        made = await self.memory.choose_setting(GUILD_A, self.c, "the Eberron", source=BY_DM)
        self.assertEqual(made.value.id, own)

    async def test_a_place_the_dm_hid_stays_hidden(self) -> None:
        own = await self.add("Eberron", type="place", status=CONFIRMED)
        await self.memory.set_place_level(GUILD_A, self.c, own, "world", source=BY_DM)
        await self.memory.set_hidden_from_players(
            GUILD_A, self.c, own, True, source=BY_DM, dm_chose_secret=True
        )
        made = await self.memory.choose_setting(GUILD_A, self.c, "Eberron", source=BY_DM)
        self.assertTrue(made.value.hidden_from_players)

    async def test_a_refusal_leaves_the_old_setting_alone(self) -> None:
        guess = await self.memory.guess_setting(GUILD_A, self.c, "Faerûn", source="background")
        assert guess.value is not None
        town = await self.add("Phandalin", type="place", status=CONFIRMED)
        await self.memory.set_place_level(GUILD_A, self.c, town, "settlement", source=BY_DM)
        with self.assertRaises(MemoryRuleError):
            await self.memory.choose_setting(GUILD_A, self.c, "Phandalin", source=BY_DM)
        kept = await self.memory.entity(GUILD_A, self.c, guess.value.id)
        assert kept is not None
        self.assertTrue(kept.setting_guessed)  # (not turned into a "no")

    async def test_choosing_what_is_already_chosen_writes_nothing(self) -> None:
        first = await self.memory.choose_setting(GUILD_A, self.c, "Golarion", source=BY_DM)
        again = await self.memory.choose_setting(GUILD_A, self.c, "Golarion", source=BY_DM)
        self.assertEqual(again.value.id, first.value.id)
        self.assertIsNone(again.batch)

    async def test_a_hidden_guess_place_is_shown_when_chosen_and_undo_hides_it(self) -> None:
        guess = await self.memory.guess_setting(GUILD_A, self.c, "Faerûn", source="background")
        assert guess.value is not None
        made = await self.memory.choose_setting(GUILD_A, self.c, "Faerûn", source=BY_DM)
        self.assertEqual(made.value.id, guess.value.id)
        self.assertFalse(made.value.hidden_from_players)
        assert made.batch is not None
        await self.memory.undo(GUILD_A, self.c, made.batch)
        back = await self.memory.entity(GUILD_A, self.c, guess.value.id)
        assert back is not None
        self.assertTrue(back.hidden_from_players)

    async def test_a_place_made_for_a_replaced_guess_goes_with_it(self) -> None:
        guess = await self.memory.guess_setting(GUILD_A, self.c, "Faerûn", source="background")
        assert guess.value is not None
        made = await self.memory.choose_setting(GUILD_A, self.c, "Golarion", source=BY_DM)
        gone = await self.memory.entity(GUILD_A, self.c, guess.value.id)
        assert gone is not None
        self.assertEqual(gone.status, "rejected")
        assert made.batch is not None
        await self.memory.undo(GUILD_A, self.c, made.batch)  # (one Undo: the guess is back)
        back = await self.memory.entity(GUILD_A, self.c, guess.value.id)
        assert back is not None
        self.assertEqual((back.status, back.setting_guessed), (CONFIRMED, True))

    async def test_a_kept_guess_the_dm_hid_again_is_not_taken(self) -> None:
        """(the DM's own setting now: only DMbot's guess goes with its replacement)"""
        guess = await self.memory.guess_setting(GUILD_A, self.c, "Faerûn", source="background")
        assert guess.value is not None
        await self.memory.keep_setting(GUILD_A, self.c, guess.value.id, source=BY_DM)
        await self.memory.set_hidden_from_players(
            GUILD_A, self.c, guess.value.id, True, source=BY_DM, dm_chose_secret=True
        )
        await self.memory.choose_setting(GUILD_A, self.c, "Golarion", source=BY_DM)
        kept = await self.memory.entity(GUILD_A, self.c, guess.value.id)
        assert kept is not None
        self.assertEqual((kept.status, kept.setting, kept.setting_from), (CONFIRMED, False, None))

    async def test_a_made_place_with_a_map_fact_on_it_is_kept(self) -> None:
        """(#4291: the one rule `_guess_place_goes`, so a map fact ties it here too, #4495)"""
        from dmbot.memory import map_facts as mf

        guess = await self.memory.guess_setting(GUILD_A, self.c, "Faerûn", source="background")
        assert guess.value is not None
        await self.memory.set_map_fact(
            GUILD_A, self.c, guess.value.id, mf.TERRAIN, "hill", source=BY_DM
        )
        await self.memory.choose_setting(GUILD_A, self.c, "Golarion", source=BY_DM)
        kept = await self.memory.entity(GUILD_A, self.c, guess.value.id)
        assert kept is not None
        self.assertEqual((kept.status, kept.setting_from), (CONFIRMED, "declined"))

    async def test_a_small_place_of_that_name_is_refused_and_nothing_changes(self) -> None:
        town = await self.add("Phandalin", type="place", status=CONFIRMED)
        await self.memory.set_place_level(GUILD_A, self.c, town, "settlement", source=BY_DM)
        with self.assertRaises(MemoryRuleError):
            await self.memory.choose_setting(GUILD_A, self.c, "Phandalin", source=BY_DM)
        self.assertIsNone(await self.memory.setting(GUILD_A, self.c))

    async def test_two_places_of_one_name_are_refused(self) -> None:
        for _ in range(2):
            await self.add("Oerth", type="place", status=CONFIRMED)
        with self.assertRaises(MemoryRuleError):
            await self.memory.choose_setting(GUILD_A, self.c, "Oerth", source=BY_DM)

    async def test_a_guess_it_replaces_is_a_no_to_that_guess(self) -> None:
        guess = await self.memory.guess_setting(GUILD_A, self.c, "Faerûn", source="background")
        assert guess.value is not None
        await self.memory.choose_setting(GUILD_A, self.c, "Golarion", source=BY_DM)
        gone = await self.memory.entity(GUILD_A, self.c, guess.value.id)
        assert gone is not None
        self.assertEqual((gone.setting, gone.setting_from), (False, "declined"))

    async def test_an_empty_name_and_anyone_but_the_dm_are_refused(self) -> None:
        for name, source in (("  ", BY_DM), ("Golarion", "background")):
            with self.assertRaises(MemoryRuleError):
                await self.memory.choose_setting(GUILD_A, self.c, name, source=source)

    async def test_the_same_choice_twice_leaves_one_setting(self) -> None:
        await self.memory.choose_setting(GUILD_A, self.c, "Golarion", source=BY_DM)
        await self.memory.choose_setting(GUILD_A, self.c, "Golarion", source=BY_DM)
        self.assertEqual(await self.setting_name(), "Golarion")

    async def test_another_server_never_sees_it(self) -> None:
        await self.memory.choose_setting(GUILD_A, self.c, "Golarion", source=BY_DM)
        with self.assertRaises(MemoryRuleError):  # (the campaign isn't in that server)
            await self.memory.setting(GUILD_B, self.c)
