"""Channel names (docs/PLAN.md, "Channel structure"): the examples from the plan."""

from dmbot.dm_screen.names import (
    is_screen_name,
    numbered,
    pick_channel_number,
    screen_channel_name,
    short_name,
    slug,
    sub_channel_name,
)

FROSTMAIDEN = "Rime of the Frostmaiden"


def test_dm_screen_uses_the_full_name_in_lowercase() -> None:
    assert slug(FROSTMAIDEN) == "rime-of-the-frostmaiden"
    assert screen_channel_name(FROSTMAIDEN) == "dmb-dm-screen-rime-of-the-frostmaiden"


def test_sub_channels_use_the_short_name() -> None:
    assert short_name(FROSTMAIDEN) == "rmfthfrstmdn"
    assert sub_channel_name("time", FROSTMAIDEN) == "dmb-time-rmfthfrstmdn"
    assert sub_channel_name("npcs", FROSTMAIDEN) == "dmb-npcs-rmfthfrstmdn"


def test_short_name_drops_spaces_dashes_and_punctuation() -> None:
    assert short_name("Frozen Sick") == "frznsck"
    assert short_name("Frozens-Cake!") == "frznsck"


def test_y_is_not_a_vowel() -> None:
    assert short_name("Mystery Cry") == "mystrycry"


def test_mostly_vowels_keeps_the_vowels() -> None:
    assert short_name("Eerie Aura") == "eerieaura"  # 7 of 9 letters are vowels


def test_exactly_half_vowels_still_drops_them() -> None:
    assert short_name("Abed") == "bd"  # 2 of 4: not *more* than half


def test_short_name_is_at_most_15_characters() -> None:
    assert len(short_name("The Long and Winding Campaign of Doom")) == 15
    assert len(short_name("Aeiou Aeiou Aeiou Aeiou")) == 15  # vowels kept, then cut


def test_digits_are_kept_and_do_not_count_as_vowels() -> None:
    assert short_name("2 Frozen") == "2frzn"
    assert short_name("Curse of Strahd 2") == "crsfstrhd2"


def test_names_without_usable_characters_fall_back() -> None:
    assert short_name("🐉🐉") == "campaign"
    assert slug("!!!") == "campaign"
    assert screen_channel_name("!!!") == "dmb-dm-screen-campaign"


def test_names_fit_discords_100_character_limit() -> None:
    name = screen_channel_name("x" * 200)
    assert len(name) == 100 and not name.endswith("-")


def test_clash_number_goes_in_front_of_both_names() -> None:
    assert numbered("frznsck", 1) == "frznsck"
    assert screen_channel_name("Frozens Cake", 2) == "dmb-dm-screen-2frozens-cake"
    assert sub_channel_name("time", "Frozens Cake", 2) == "dmb-time-2frznsck"


def test_first_campaign_gets_no_number() -> None:
    assert pick_channel_number("Frozen Sick", []) == 1


def test_same_short_name_gets_the_next_number() -> None:
    assert pick_channel_number("Frozens Cake", [("Frozen Sick", 1)]) == 2
    assert pick_channel_number("Frozn Sack", [("Frozen Sick", 1), ("Frozens Cake", 2)]) == 3


def test_same_full_name_gets_a_number() -> None:
    # Different campaign names, but both become "frost-maiden".
    assert pick_channel_number("Frost Maiden", [("Frost-Maiden", 1)]) == 2


def test_campaigns_that_dont_clash_dont_block_numbers() -> None:
    assert pick_channel_number("Curse of Strahd", [("Frozen Sick", 1), ("Frozens Cake", 2)]) == 1


def test_campaigns_without_a_number_yet_dont_block() -> None:
    assert pick_channel_number("Frozens Cake", [("Frozen Sick", None)]) == 1


def test_lowest_free_number_is_reused() -> None:
    assert pick_channel_number("Frozn Sack", [("Frozen Sick", 1), ("Frozens Cake", 3)]) == 2


def test_screen_names_new_and_old_style() -> None:
    assert is_screen_name("dmb-dm-screen-rime-of-the-frostmaiden")
    assert is_screen_name("dm-screen-frostmaiden")  # made before the dmb- prefix
    assert is_screen_name("dmb-dm-screen")
    assert not is_screen_name("general")
    assert not is_screen_name("dm-screenshots")
    assert not is_screen_name("dmb-time-rmfthfrstmdn")
