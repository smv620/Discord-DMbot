"""The node summary builder (#3236 slice 1): the category of a fact, the sort, the caps, the DM and
player versions, the spoiler check. Pure: no database. Invented names throughout."""

from __future__ import annotations

import unittest
from typing import Any

from dmbot.memory import node_summary as ns
from dmbot.memory.node_summary import Fact, Focus, Neighbour

MIRA = Focus("mira", "Mira Voss", "character")


def fact(kind: str, **kw: Any) -> Fact:
    """A fact the players know and the person clicking knows, unless a test says otherwise."""
    kw.setdefault("players_know", True)
    kw.setdefault("viewer_knows", True)
    return Fact(kind, **kw)


def kin(role: str = "step-sibling", **kw: Any) -> Fact:
    return fact("kin_of", detail=role, **kw)


def person(name: str, rank: int, *facts: Fact, **kw: Any) -> Neighbour:
    kw.setdefault("players_know", True)
    return Neighbour(
        name.lower(), name, "character", rank, facts=facts or (fact("mentor_of"),), **kw
    )


class Categories(unittest.TestCase):
    def test_the_table_is_a_lookup_on_the_type(self) -> None:
        got = {
            "kin": ns.category_of(kin()),
            "kill": ns.category_of(fact("did", deed="kill")),
            "steal": ns.category_of(fact("did", deed="steal")),
            "feeling": ns.category_of(fact("knows", detail="trusts")),
            "plain": ns.category_of(fact("knows")),
            "mentor": ns.category_of(fact("mentor_of")),
            "belief": ns.category_of(fact("associated_with", belief=True)),
            "label": ns.category_of(fact("associated_with", detail="drinking partner")),
        }
        self.assertEqual(
            got,
            {
                "kin": ns.LASTING, "kill": ns.LIFE_AND_DEATH, "steal": ns.OTHER_DEEDS,
                "feeling": ns.STANDING, "plain": ns.STANDING, "mentor": ns.ROLES,
                "belief": ns.BELIEFS, "label": ns.OTHER,
            },
        )  # fmt: skip

    def test_free_text_goes_by_whole_words_and_the_rest_is_other(self) -> None:
        for text, want in (
            ("ex-lover", ns.LASTING), ("tried to kill her", ns.LIFE_AND_DEATH),
            ("her old teacher", ns.ROLES), ("stole from her", ns.OTHER_DEEDS),
            ("killed", ns.LIFE_AND_DEATH), ("friends", ns.STANDING), ("sister", ns.LASTING),
            ("shared a table once", ns.OTHER),
        ):  # fmt: skip
            with self.subTest(text=text):
                self.assertEqual(ns.category_of(fact("associated_with", detail=text)), want)

    def test_a_word_inside_another_word_is_not_a_match(self) -> None:
        for text in (
            "skilled", "arrival", "whatever", "trustworthy", "apparent", "really", "midwife",
            "clover", "entwined", "smother", "finally", "totally",
        ):  # fmt: skip
            with self.subTest(text=text):
                self.assertEqual(ns.category_of(fact("associated_with", detail=text)), ns.OTHER)

    def test_the_order_of_the_categories_is_the_agreed_one(self) -> None:
        self.assertEqual(
            [
                ns.LASTING, ns.LIFE_AND_DEATH, ns.STANDING, ns.ROLES, ns.OTHER_DEEDS,
                ns.BELIEFS, ns.OTHER,
            ],
            [1, 2, 3, 4, 5, 6, 7],
        )  # fmt: skip


class FactsBetweenTwo(unittest.TestCase):
    def test_sorted_by_category_then_newest_session_then_detail(self) -> None:
        facts = [
            fact("associated_with", detail="drinking partner", session=9),
            fact("did", deed="steal", session=5),
            fact("did", deed="kill", session=2),
            fact("did", deed="rescue", session=8),
            kin(),
        ]
        shown = [f for f, _ in ns.facts_to_show(facts)]
        cats = [ns.category_of(f) for f in shown]
        self.assertEqual(cats, sorted(cats))
        self.assertEqual(shown[0].kind, "kin_of")
        self.assertEqual([f.session for f in shown if f.deed in ("rescue", "kill")], [8, 2])
        self.assertEqual(shown[-1].detail, "drinking partner")

    def test_a_newer_fact_of_the_same_category_comes_first(self) -> None:
        old = fact("associated_with", detail="ex-lover", session=2)
        new = fact("associated_with", detail="her cousin", session=9)
        self.assertEqual([f for f, _ in ns.facts_to_show([old, new])], [new, old])

    def test_an_ended_fact_comes_after_a_current_one_and_says_formerly(self) -> None:
        gone = fact("mentor_of", ended=True, session=9)
        now = fact("rival_of", session=1)
        shown = ns.facts_to_show([gone, now])
        self.assertEqual([t for _, t in shown], ["their rival", "formerly their apprentice"])

    def test_a_plain_knows_says_nothing_beside_anything(self) -> None:
        shown = ns.facts_to_show([fact("knows"), kin(), fact("mentor_of")])
        self.assertEqual({f.kind for f, _ in shown}, {"kin_of", "mentor_of"})

    def test_a_feeling_beside_a_family_tie_is_kept(self) -> None:
        shown = ns.facts_to_show([fact("knows", detail="trusts", outward=False), kin()])
        self.assertEqual({f.kind for f, _ in shown}, {"kin_of", "knows"})

    def test_a_feeling_that_changed_is_told_once(self) -> None:
        shown = ns.facts_to_show(
            [
                fact("knows", detail="hostile", session=2, outward=False),
                fact("knows", detail="friendly", session=7, outward=False),
            ],
            "Mira Voss",
        )
        self.assertEqual([t for _, t in shown], ["was hostile toward Mira Voss, now friendly"])

    def test_neutral_after_hostile_is_the_change_not_hostile_still(self) -> None:
        shown = ns.facts_to_show(
            [
                fact("knows", detail="hostile", session=1, outward=False),
                fact("knows", detail="neutral", session=2, outward=False),
            ],
            "Mira Voss",
        )
        self.assertEqual([t for _, t in shown], ["was hostile toward Mira Voss, now neutral"])

    def test_two_directions_are_two_lines_not_one(self) -> None:
        shown = ns.facts_to_show(
            [
                fact("knows", detail="trusts", session=3, outward=True),
                fact("knows", detail="hostile", session=4, outward=False),
            ]
        )
        self.assertEqual(sorted(t for _, t in shown), ["hostile toward them", "trusted by them"])

    def test_an_unset_session_is_older_than_session_zero(self) -> None:
        shown = ns.facts_to_show(
            [
                fact("knows", detail="trusts", session=None, outward=False),
                fact("knows", detail="hostile", session=0, outward=False),
            ],
            "Mira Voss",
        )
        self.assertEqual([t for _, t in shown], ["was trusting toward Mira Voss, now hostile"])

    def test_a_fact_with_no_words_is_not_shown(self) -> None:
        self.assertEqual(ns.facts_to_show([fact("associated_with")]), [])


class TheText(unittest.TestCase):
    def test_the_example_reads_plainly(self) -> None:
        kesh = person(
            "Kesh", 3, kin(),
            fact("did", outward=False, deed="kill", session=2),
            fact("did", outward=False, deed="rescue", session=8),
        )  # fmt: skip
        self.assertEqual(
            ns.summarize(MIRA, [kesh], ns.DM).text,
            "Mira Voss (character). Joined to Kesh (step-sibling; rescued them; killed them).",
        )

    def test_at_most_three_facts_per_neighbour_and_the_rest_counted(self) -> None:
        many = person(
            "Kesh", 3, kin(), fact("mentor_of"), fact("rival_of"), fact("serves"),
            fact("did", deed="kill"),
        )  # fmt: skip
        self.assertIn("+2 more", ns.summarize(MIRA, [many], ns.DM).text)

    def test_at_most_eight_neighbours_and_the_rest_counted_in_the_list(self) -> None:
        crowd = [person(f"Npc{i:02d}", 2, score=float(i)) for i in range(20)]
        got = ns.summarize(MIRA, crowd, ns.DM)
        self.assertEqual((len(got.named), got.more), (8, 12))
        self.assertTrue(got.text.endswith(", and 12 more."))

    def test_one_neighbour_and_more_reads_as_a_pair(self) -> None:
        crowd = [person(f"Npc{i:02d}", 2) for i in range(9)]
        self.assertTrue(
            ns.summarize(MIRA, crowd[:1], ns.DM).text.endswith("Npc00 (their apprentice).")
        )

    def test_the_dm_order_is_rank_then_category_then_score(self) -> None:
        a = person("Aldo", 4, fact("rival_of"))
        b = person("Bram", 3, kin())
        c = person("Cora", 3, fact("mentor_of"), score=9.0)
        d = person("Dane", 3, fact("mentor_of"), score=1.0)
        self.assertEqual(
            ns.summarize(MIRA, [d, c, b, a], ns.DM).named, ("Aldo", "Bram", "Cora", "Dane")
        )

    def test_below_minor_is_left_out_of_the_dm_version(self) -> None:
        got = ns.summarize(MIRA, [person("Nobody", 0), person("Someone", 1)], ns.DM)
        self.assertEqual(got.named, ("Someone",))

    def test_the_second_hop_is_one_best_entry_not_already_named(self) -> None:
        far = (person("Zed", 2), person("Yara", 4, kin()))
        kesh = person("Kesh", 3, kin(), further=far)
        got = ns.summarize(MIRA, [kesh, person("Yara", 2)], ns.DM)
        self.assertEqual(got.named.count("Yara"), 1)
        self.assertIn("through them Zed", got.text)

    def test_the_second_hop_never_names_the_node_itself_and_follows_the_dm_rank_cut(self) -> None:
        low = Neighbour("low", "Lowly", "character", 0, facts=(fact("mentor_of"),))
        me = Neighbour("mira", "Mira Voss", "character", 5, facts=(fact("mentor_of"),))
        kesh = person("Kesh", 3, kin(), further=(me, low))
        self.assertNotIn("through them", ns.summarize(MIRA, [kesh], ns.DM).text)

    def test_a_neighbour_that_is_the_node_itself_is_skipped(self) -> None:
        me = Neighbour("mira", "Mira Voss", "character", 4, facts=(fact("mentor_of"),))
        self.assertEqual(ns.summarize(MIRA, [me], ns.DM).named, ())

    def test_the_same_input_reads_the_same_in_any_order_even_with_equal_names(self) -> None:
        crowd = [person(f"Npc{i % 6:02d}", 2, score=1.0) for i in range(12)]
        crowd = [
            Neighbour(f"id{i}", n.name, n.kind, n.rank, facts=n.facts, players_know=True)
            for i, n in enumerate(crowd)
        ]
        self.assertEqual(
            ns.summarize(MIRA, crowd, ns.DM).text, ns.summarize(MIRA, crowd[::-1], ns.DM).text
        )

    def test_a_node_with_no_neighbours_says_only_what_it_is(self) -> None:
        self.assertEqual(ns.summarize(MIRA, [], ns.DM).text, "Mira Voss (character).")


class ThePlayerVersion(unittest.TestCase):
    def secret_world(self) -> list[Neighbour]:
        return [
            person("Kesh", 3, kin(), fact("did", deed="kill", players_know=False)),
            person("Hidden Villain", 5, fact("enemy_of"), players_know=False),
            person("Seen Ally", 1, fact("ally_of")),
        ]

    def test_dm_only_facts_and_entries_never_appear(self) -> None:
        text = ns.summarize(MIRA, self.secret_world(), ns.PLAYER).text
        self.assertNotIn("Hidden Villain", text)
        self.assertNotIn("kill", text)
        self.assertIn("Kesh (step-sibling)", text)

    def test_the_count_of_more_does_not_count_what_players_cannot_see(self) -> None:
        crowd = [person(f"Npc{i:02d}", 2) for i in range(9)]
        hidden = [person(f"Secret{i}", 4, fact("enemy_of"), players_know=False) for i in range(30)]
        with_hidden = ns.summarize(MIRA, [*crowd, *hidden], ns.PLAYER)
        without = ns.summarize(MIRA, crowd, ns.PLAYER)
        self.assertEqual(with_hidden.text, without.text)
        self.assertEqual(with_hidden.more, 1)

    def test_a_hidden_second_hop_entry_never_appears(self) -> None:
        far = (person("Secret Heir", 4, fact("rival_of"), players_know=False),)
        kesh = person("Kesh", 3, kin(), further=far)
        got = ns.summarize(MIRA, [kesh], ns.PLAYER)
        self.assertNotIn("Secret Heir", got.text)
        self.assertNotIn("Secret Heir", got.named)

    def test_a_neighbour_with_no_visible_fact_is_dropped(self) -> None:
        only_hidden = person("Ghost", 4, fact("mentor_of", players_know=False))
        self.assertEqual(ns.summarize(MIRA, [only_hidden], ns.PLAYER).named, ())

    def test_a_fact_nobody_marked_is_hidden_from_players(self) -> None:
        unmarked = Neighbour(
            "kesh", "Kesh", "character", 3, facts=(Fact("kin_of", detail="sibling"),)
        )
        self.assertEqual(ns.summarize(MIRA, [unmarked], ns.PLAYER).named, ())

    def test_roles_and_rank_words_are_for_the_dm_only(self) -> None:
        focus = Focus("mira", "Mira Voss", "character", roles=("mentor", "rival"))
        self.assertIn("Roles: mentor, rival.", ns.summarize(focus, [], ns.DM).text)
        player = ns.summarize(focus, [person("Kesh", 4, kin())], ns.PLAYER).text
        for word in ("Roles", "rank", "major", "Major", "significant"):
            self.assertNotIn(word, player)

    def test_the_player_order_ignores_the_rank_and_the_dm_score(self) -> None:
        low = person("Lowrank", 1, kin(), score=1.0, player_score=5.0)
        high = person("Highrank", 5, kin(), score=99.0, player_score=1.0)
        self.assertEqual(ns.summarize(MIRA, [low, high], ns.DM).named, ("Highrank", "Lowrank"))
        self.assertEqual(ns.summarize(MIRA, [low, high], ns.PLAYER).named, ("Lowrank", "Highrank"))

    def test_a_player_neighbour_is_not_cut_for_a_low_rank(self) -> None:
        self.assertEqual(ns.summarize(MIRA, [person("Kesh", 0, kin())], ns.PLAYER).named, ("Kesh",))


class TheSpoilerCheck(unittest.TestCase):
    def test_a_fact_the_players_hold_and_this_person_does_not_needs_a_peek(self) -> None:
        n = person("Kesh", 3, kin(viewer_knows=False))
        self.assertTrue(ns.needs_peek([n]))
        self.assertTrue(ns.summarize(MIRA, [n], ns.PLAYER).needs_peek)

    def test_nothing_hidden_from_this_person_means_no_peek(self) -> None:
        self.assertFalse(ns.summarize(MIRA, [person("Kesh", 3, kin())], ns.PLAYER).needs_peek)

    def test_a_fact_nobody_at_the_table_knows_is_not_a_spoiler_to_peek_at(self) -> None:
        n = person("Kesh", 3, kin(players_know=False, viewer_knows=False), kin())
        self.assertFalse(ns.needs_peek([n]))

    def test_it_looks_past_the_caps(self) -> None:
        crowd = [person(f"Npc{i:02d}", 2, score=float(i)) for i in range(20)]
        crowd.append(person("Zzz", 2, kin(viewer_knows=False), score=-1.0))
        self.assertTrue(ns.summarize(MIRA, crowd, ns.PLAYER).needs_peek)

    def test_a_second_hop_fact_the_clicker_does_not_know_needs_a_peek(self) -> None:
        far = (person("Zed", 2, kin(viewer_knows=False)),)
        kesh = person("Kesh", 3, kin(), further=far)
        self.assertTrue(ns.summarize(MIRA, [kesh], ns.PLAYER).needs_peek)

    def test_the_dm_version_never_asks_for_a_peek(self) -> None:
        n = person("Kesh", 3, kin(viewer_knows=False))
        self.assertFalse(ns.summarize(MIRA, [n], ns.DM).needs_peek)
