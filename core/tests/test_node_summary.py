"""The node summary builder (#3236 slice 1): the category of a fact, the sort, the caps, the DM and
player versions, the spoiler check. Pure: no database. Invented names throughout."""

from __future__ import annotations

import unittest

from dmbot.memory import node_summary as ns
from dmbot.memory.node_summary import Fact, Focus, Neighbour

MIRA = Focus("mira", "Mira Voss", "character")


def kin(role: str = "step-sibling", **kw: object) -> Fact:
    return Fact("kin_of", detail=role, **kw)  # type: ignore[arg-type]


def person(name: str, rank: int, *facts: Fact, score: float = 0.0, **kw: object) -> Neighbour:
    return Neighbour(name.lower(), name, "character", rank, score, facts or (Fact("knows"),), **kw)  # type: ignore[arg-type]


class Categories(unittest.TestCase):
    def test_the_table_is_a_lookup_on_the_type(self) -> None:
        got = {
            "kin": ns.category_of(kin()),
            "kill": ns.category_of(Fact("did", deed="kill")),
            "steal": ns.category_of(Fact("did", deed="steal")),
            "feeling": ns.category_of(Fact("knows", detail="trusts")),
            "plain": ns.category_of(Fact("knows")),
            "mentor": ns.category_of(Fact("mentor_of")),
            "belief": ns.category_of(Fact("associated_with", belief=True)),
            "label": ns.category_of(Fact("associated_with", detail="drinking partner")),
        }
        self.assertEqual(
            got,
            {
                "kin": ns.LASTING, "kill": ns.LIFE_AND_DEATH, "steal": ns.OTHER_DEEDS,
                "feeling": ns.STANDING, "plain": ns.STANDING, "mentor": ns.ROLES,
                "belief": ns.BELIEFS, "label": ns.OTHER,
            },
        )  # fmt: skip

    def test_free_text_goes_by_the_word_list_and_the_rest_is_other(self) -> None:
        for text, want in (
            ("ex-lover", ns.LASTING), ("tried to kill her", ns.LIFE_AND_DEATH),
            ("her old teacher", ns.ROLES), ("stole from her", ns.OTHER_DEEDS),
            ("shared a table once", ns.OTHER),
        ):  # fmt: skip
            with self.subTest(text=text):
                self.assertEqual(ns.category_of(Fact("associated_with", detail=text)), want)

    def test_the_order_of_the_categories_is_the_agreed_one(self) -> None:
        self.assertEqual(
            [
                ns.LASTING,
                ns.LIFE_AND_DEATH,
                ns.STANDING,
                ns.ROLES,
                ns.OTHER_DEEDS,
                ns.BELIEFS,
                ns.OTHER,
            ],
            [1, 2, 3, 4, 5, 6, 7],
        )


class FactsBetweenTwo(unittest.TestCase):
    def test_sorted_by_category_then_newest_session_then_detail(self) -> None:
        facts = [
            Fact("associated_with", detail="drinking partner", session=9),
            Fact("did", deed="steal", session=5),
            Fact("did", deed="kill", session=2),
            Fact("did", deed="rescue", session=8),
            kin(),
        ]
        shown = ns.facts_to_show(facts)
        cats = [ns.category_of(f) for f in shown]
        self.assertEqual(cats, sorted(cats))
        self.assertEqual(shown[0].kind, "kin_of")
        self.assertEqual([f.session for f in shown if f.deed in ("rescue", "kill")], [8, 2])
        self.assertEqual(shown[-1].detail, "drinking partner")

    def test_a_newer_fact_of_the_same_category_comes_first(self) -> None:
        old = Fact("associated_with", detail="ex-lover", session=2)
        new = Fact("associated_with", detail="her cousin", session=9)
        self.assertEqual(ns.facts_to_show([old, new]), [new, old])

    def test_a_plain_knows_next_to_a_stronger_tie_is_dropped(self) -> None:
        shown = ns.facts_to_show([Fact("knows"), kin(), Fact("mentor_of")])
        self.assertEqual({f.kind for f in shown}, {"kin_of", "mentor_of"})

    def test_a_feeling_that_changed_is_told_once(self) -> None:
        text = ns.summarize(
            MIRA,
            [
                person(
                    "Kesh",
                    3,
                    Fact("knows", detail="hostile", session=2),
                    Fact("knows", detail="friendly", session=7),
                )
            ],
            ns.DM,
        ).text
        self.assertIn("was hostile, now friendly", text)

    def test_a_fact_with_no_words_is_not_shown(self) -> None:
        self.assertEqual(ns.facts_to_show([Fact("associated_with")]), [])


class TheText(unittest.TestCase):
    def test_the_example_reads_plainly(self) -> None:
        kesh = person(
            "Kesh",
            3,
            kin(),
            Fact("did", outward=False, deed="kill", session=2),
            Fact("did", outward=False, deed="rescue", session=8),
        )
        text = ns.summarize(MIRA, [kesh], ns.DM).text
        self.assertEqual(
            text,
            "Mira Voss is a character. Joined to Kesh (step-sibling; rescued them; killed them).",
        )

    def test_at_most_three_facts_per_neighbour_and_the_rest_counted(self) -> None:
        many = person(
            "Kesh", 3, kin(), Fact("mentor_of"), Fact("rival_of"), Fact("serves"),
            Fact("did", deed="kill"),
        )  # fmt: skip
        text = ns.summarize(MIRA, [many], ns.DM).text
        self.assertIn("+2 more", text)

    def test_at_most_eight_neighbours_and_the_rest_counted(self) -> None:
        crowd = [person(f"Npc{i:02d}", 2, score=float(i)) for i in range(20)]
        got = ns.summarize(MIRA, crowd, ns.DM)
        self.assertEqual(len(got.named), 8)
        self.assertEqual(got.more, 12)
        self.assertIn("and 12 more", got.text)

    def test_the_dm_order_is_rank_then_category_then_score(self) -> None:
        a = person("Aldo", 4, Fact("knows"))
        b = person("Bram", 3, kin())
        c = person("Cora", 3, Fact("knows"), score=9.0)
        d = person("Dane", 3, Fact("knows"), score=1.0)
        got = ns.summarize(MIRA, [d, c, b, a], ns.DM)
        self.assertEqual(got.named, ("Aldo", "Bram", "Cora", "Dane"))

    def test_below_minor_is_left_out_of_the_dm_version_and_counted_nowhere(self) -> None:
        got = ns.summarize(MIRA, [person("Nobody", 0), person("Someone", 1)], ns.DM)
        self.assertEqual(got.named, ("Someone",))

    def test_the_second_hop_is_one_best_entry_not_already_named(self) -> None:
        far = (person("Zed", 2), person("Yara", 4, kin()))
        kesh = person("Kesh", 3, kin(), further=far)
        got = ns.summarize(MIRA, [kesh, person("Yara", 2)], ns.DM)
        self.assertEqual(got.named.count("Yara"), 1)  # not named twice
        self.assertIn("through them Zed", got.text)

    def test_the_second_hop_never_names_the_node_itself(self) -> None:
        kesh = person("Kesh", 3, kin(), further=(Neighbour("mira", "Mira Voss", "character", 5),))
        self.assertNotIn("through them", ns.summarize(MIRA, [kesh], ns.DM).text)

    def test_the_same_input_always_reads_the_same(self) -> None:
        crowd = [person(f"Npc{i:02d}", 2, score=1.0) for i in range(12)]
        self.assertEqual(
            ns.summarize(MIRA, crowd, ns.DM).text,
            ns.summarize(MIRA, list(reversed(crowd)), ns.DM).text,
        )

    def test_a_node_with_no_neighbours_says_only_what_it_is(self) -> None:
        self.assertEqual(ns.summarize(MIRA, [], ns.DM).text, "Mira Voss is a character.")


class ThePlayerVersion(unittest.TestCase):
    def secret_world(self) -> list[Neighbour]:
        return [
            person("Kesh", 3, kin(), Fact("did", deed="kill", players_know=False)),
            person("Hidden Villain", 5, Fact("enemy_of"), players_know=False),
            person("Seen Ally", 1, Fact("ally_of")),
        ]

    def test_dm_only_facts_and_entries_never_appear(self) -> None:
        text = ns.summarize(MIRA, self.secret_world(), ns.PLAYER).text
        self.assertNotIn("Hidden Villain", text)
        self.assertNotIn("killed", text)
        self.assertNotIn("kill", text)
        self.assertIn("Kesh (step-sibling)", text)

    def test_a_neighbour_with_no_visible_fact_is_dropped(self) -> None:
        only_hidden = person("Ghost", 4, Fact("mentor_of", players_know=False))
        self.assertEqual(ns.summarize(MIRA, [only_hidden], ns.PLAYER).named, ())

    def test_roles_and_rank_words_are_for_the_dm_only(self) -> None:
        focus = Focus("mira", "Mira Voss", "character", roles=("mentor", "rival"))
        self.assertIn("Roles: mentor, rival.", ns.summarize(focus, [], ns.DM).text)
        player = ns.summarize(focus, [person("Kesh", 4, kin())], ns.PLAYER).text
        for word in ("Roles", "rank", "major", "Major", "significant"):
            self.assertNotIn(word, player)

    def test_the_player_order_ignores_the_rank(self) -> None:
        low = person("Lowrank", 1, kin(), score=5.0)
        high = person("Highrank", 5, kin(), score=1.0)
        self.assertEqual(ns.summarize(MIRA, [low, high], ns.DM).named, ("Highrank", "Lowrank"))
        self.assertEqual(ns.summarize(MIRA, [low, high], ns.PLAYER).named, ("Lowrank", "Highrank"))

    def test_a_player_neighbour_is_not_cut_for_a_low_rank(self) -> None:
        self.assertEqual(ns.summarize(MIRA, [person("Kesh", 0, kin())], ns.PLAYER).named, ("Kesh",))


class TheSpoilerCheck(unittest.TestCase):
    def test_a_fact_the_players_hold_and_this_person_does_not_needs_a_peek(self) -> None:
        n = person("Kesh", 3, kin(viewer_knows=False))
        self.assertTrue(ns.needs_peek([n]))

    def test_nothing_hidden_from_this_person_means_no_peek(self) -> None:
        self.assertFalse(ns.needs_peek([person("Kesh", 3, kin())]))

    def test_a_fact_nobody_at_the_table_knows_is_not_a_spoiler_to_peek_at(self) -> None:
        n = person("Kesh", 3, kin(players_know=False, viewer_knows=False))
        self.assertFalse(ns.needs_peek([n]))

    def test_it_looks_past_the_caps(self) -> None:
        crowd = [person(f"Npc{i:02d}", 2, score=float(i)) for i in range(20)]
        crowd.append(person("Zzz", 2, kin(viewer_knows=False), score=-1.0))
        self.assertTrue(ns.needs_peek(crowd))
