"""A name tied to a player character that pass 1 never listed becomes a name to check (#3238,
drop 2). Pure; invented names."""

from __future__ import annotations

import unittest
from collections import Counter

from dmbot.memory.background import Connection, Found
from dmbot.memory.lore_pc_names import MAX_PC_NAMES, pc_new_names


def link(a: str, b: str, how: str = "kin_of") -> Connection:
    return Connection(a, how, b, f"{a} and {b} are kin", words="sibling")


def named(*names: str) -> list[Found]:
    return [Found(n, "npc", (), (), "") for n in names]


class ThePlayerCharactersFamily(unittest.TestCase):
    def run_it(
        self,
        links: list[Connection],
        names: list[Found] | None = None,
        known: tuple[str, ...] = (),
        spellings: tuple[str, ...] = (),
    ) -> tuple[list[str], Counter[str]]:
        lost: Counter[str] = Counter()
        made = pc_new_names(
            links, names or named("Cerric Vale"), known, {"cerric vale"}, lost, spellings=spellings
        )
        return [m.name for m in made], lost

    def test_a_name_unlisted_next_to_a_player_character_is_added(self) -> None:
        made, lost = self.run_it([link("Cerric Vale", "Ulric Vale")])
        self.assertEqual((made, lost["pc_name_made"]), (["Ulric Vale"], 1))

    def test_either_way_round(self) -> None:
        made, _ = self.run_it([link("Ulric Vale", "Cerric Vale")])
        self.assertEqual(made, ["Ulric Vale"])

    def test_a_name_the_campaign_or_the_read_has_is_not_added_again(self) -> None:
        self.assertEqual(
            self.run_it([link("Cerric Vale", "Ulric Vale")], known=("ulric vale",))[0], []
        )
        both = named("Cerric Vale", "Ulric Vale")
        self.assertEqual(self.run_it([link("Cerric Vale", "Ulric Vale")], both)[0], [])

    def test_a_tie_between_two_other_people_adds_nothing(self) -> None:
        self.assertEqual(self.run_it([link("Brann", "Ulric Vale")])[0], [])

    def test_a_description_or_a_plain_word_is_not_a_name(self) -> None:
        links = [link("Cerric Vale", x) for x in ("the sister", "a hooded figure", "five days ago")]
        self.assertEqual(self.run_it(links)[0], [])

    def test_a_secret_or_new_spelling_of_a_known_name_is_not_a_new_name(self) -> None:
        links = [link("Cerric Vale", "The Raven", "knows")]
        self.assertEqual(self.run_it(links, spellings=("the raven",))[0], [])

    def test_a_first_name_of_a_listed_name_is_that_name_not_a_second_one(self) -> None:
        both = named("Cerric Vale", "Ulric Vale")
        self.assertEqual(self.run_it([link("Cerric Vale", "Ulric")], both)[0], [])

    def test_only_a_tie_between_two_people_can_add_a_name(self) -> None:
        for how in ("owns", "located_in", "member_of", "contains", "leads", "founded"):
            self.assertEqual(self.run_it([link("Cerric Vale", "Dawnbreaker", how)])[0], [], how)
        serves = self.run_it([link("Cerric Vale", "Dawnbreaker", "serves")])[0]
        self.assertEqual(serves, ["Dawnbreaker"])

    def test_a_phrase_is_not_a_name(self) -> None:
        for end in (
            "Cerric's brother", "Cerric's", "Ulric, the smith", "Ulric (his twin)",
            "Her mother Mara", "Route 66",
        ):  # fmt: skip
            self.assertEqual(self.run_it([link("Cerric Vale", end)])[0], [], end)

    def test_the_same_name_in_two_ties_is_added_once(self) -> None:
        made, _ = self.run_it([link("Cerric Vale", "Ulric"), link("Ulric", "Cerric Vale", "knows")])
        self.assertEqual(made, ["Ulric"])

    def test_at_most_twenty_are_added_and_the_rest_counted(self) -> None:
        links = [link("Cerric Vale", f"Person{chr(65 + i)}") for i in range(MAX_PC_NAMES + 3)]
        made, lost = self.run_it(links)
        self.assertEqual((len(made), lost["pc_name_over_cap"]), (MAX_PC_NAMES, 3))


if __name__ == "__main__":
    unittest.main()
