"""What the sidebar tells the AI (#934): the rules and names the question mentions, only this
campaign's house rules, and nothing secret. The database test uses the real house-rule store
for two campaigns to prove the second one's rule never reaches the first one's prompt."""

from __future__ import annotations

import unittest
from dataclasses import replace
from typing import Any

from dmbot.campaigns.store import CampaignStore
from dmbot.memory.lookup import CampaignLookup
from dmbot.memory.models import Relation
from dmbot.rules import facts as rule_facts
from dmbot.rules.house import HouseRuleStore
from dmbot.rules.index import Hit, srd
from dmbot.sidebar import context
from dmbot.sidebar.answer import Sidebar
from tests.pg import DatabaseTest
from tests.sidebar_brevity_cases import reply
from tests.test_memory_lookup import BELLEROS, CERRIC, data
from tests.test_sidebar_answer import FakeAI, house

INDEX = srd()


class HeaderFacts(unittest.TestCase):
    """#1039: a spell's range and duration, and a creature's AC and speed, are kept apart from
    the entry's text, so they are sent in front of it."""

    def prompt(self, question: str) -> str:
        return context.build(
            question,
            target="2024",
            fallback="2014",
            index=INDEX,
            house_rules=[],
            lookup=None,
            scene="",
        ).prompt

    def test_a_spells_header_is_in_the_prompt(self) -> None:
        prompt = self.prompt("what's the range of fireball")
        self.assertIn("FACTS: Level 3 Evocation; Casting time Action; Range 150 feet;", prompt)
        self.assertIn("Duration Instantaneous. TEXT: A bright streak", prompt)

    def test_a_creatures_header_is_in_the_prompt(self) -> None:
        prompt = self.prompt("what's the AC of a goblin warrior")
        self.assertIn("AC 15; HP 10 (3d6); Speed 30 ft.", prompt)

    def test_the_older_edition_has_its_own_header(self) -> None:
        prompt = self.prompt("fireball in 2014 versus 2024")
        self.assertIn("Casting time 1 action", prompt)  # the 2014 entry's own words
        self.assertIn("Casting time Action", prompt)

    def test_components_are_in_words_not_letters(self) -> None:
        self.assertEqual(
            rule_facts.components_words("V, S, M (a ball of bat guano)"),
            "Verbal, Somatic, Material (a ball of bat guano)",
        )
        self.assertEqual(rule_facts.components_words("V, S"), "Verbal, Somatic")
        self.assertIn("Components Verbal, Somatic, Material (", self.prompt("range of fireball"))

    def test_a_condition_has_no_header(self) -> None:
        self.assertNotIn("FACTS:", self.prompt("what does grappled do"))

    def test_a_cantrip_and_a_missing_field_do_not_break_it(self) -> None:
        entry = INDEX.lookup("prestidigitation", "2024", "2014", kind="spell")
        assert entry is not None
        self.assertTrue(entry.entry.facts.startswith("Transmutation cantrip;"))


class MentionedRules(unittest.TestCase):
    def names(self, question: str, target: str = "2024", fallback: str = "2014") -> list[str]:
        return [h.entry.name for h in context.mentioned_rules(question, INDEX, target, fallback)]

    def test_a_spell_named_in_the_question(self) -> None:
        self.assertEqual(self.names("do you need line of sight for fireball"), ["Fireball"])

    def test_several_in_the_order_asked_and_never_more_than_three(self) -> None:
        found = self.names("can a goblin that is grappled and prone and stunned cast fireball")
        self.assertLessEqual(len(found), context.ENTRIES_MAX)
        self.assertEqual(found[0], "Goblin Warrior")

    def test_common_words_alone_are_not_looked_up(self) -> None:
        self.assertEqual(self.names("do you need to find it"), [])

    def test_an_older_name_leads_to_the_newer_entry(self) -> None:
        self.assertEqual(self.names("what is a goblin AC"), ["Goblin Warrior"])

    def test_the_fallback_is_used_and_tagged_when_the_target_has_nothing(self) -> None:
        hits = context.mentioned_rules("what is a goblin AC", INDEX, "2014", "none")
        self.assertEqual(hits[0].entry.edition, "2014")
        prompt = context.build(
            "what is a goblin AC",
            target="2024",
            fallback="2014",
            index=INDEX,
            house_rules=[],
            lookup=None,
            scene="",
        ).prompt
        self.assertIn("Goblin", prompt)


class TheRulesGlossary(unittest.TestCase):
    """The SRD's Rules Glossary (#1884): cited when the question names nothing else."""

    def hits(self, question: str) -> list[Hit]:
        return context.mentioned_rules(question, INDEX, "2024", "2014")

    def test_a_question_about_a_game_term_gets_the_glossary_entry_and_its_citation(self) -> None:
        (hit,) = self.hits("how does long rest work")
        self.assertEqual((hit.entry.kind, hit.entry.name), ("rule", "Long Rest"))
        prompt = context.build(
            "how does long rest work",
            target="2024",
            fallback="2014",
            index=INDEX,
            house_rules=[],
            lookup=None,
            scene="",
        )
        self.assertIn("- Long Rest (rule) [SRD 5.2.1 p. 185]:", prompt.prompt)
        self.assertIn("SRD 5.2.1 p. 185", prompt.sources)

    def test_a_tagged_entry_is_found_by_its_plain_name(self) -> None:
        found = {
            h.entry.name: h.entry.details["tag"] for h in self.hits("what does the dash action")
        }
        self.assertEqual(found.get("Dash"), "Action")

    def test_a_named_spell_condition_or_creature_keeps_the_places_to_itself(self) -> None:
        # "line" is a glossary entry (an area of effect): beside a spell it would be noise.
        self.assertEqual(
            [h.entry.name for h in self.hits("line of sight for fireball")], ["Fireball"]
        )
        self.assertEqual(
            [h.entry.kind for h in self.hits("can a grappled goblin dash")],
            ["condition", "monster"],
        )

    def test_the_glossary_has_no_spell_condition_or_creature_of_the_same_kind(self) -> None:
        kinds = {h.entry.kind for h in self.hits("what is cover and half cover")}
        self.assertEqual(kinds, {"rule"})

    def test_a_house_rule_on_the_same_term_comes_before_the_glossary_entry(self) -> None:
        prompt = context.build(
            "how does cover work",
            target="2024",
            fallback="2014",
            index=INDEX,
            house_rules=[house(3, "Half cover gives +3 to AC at our table.")],
            lookup=None,
            scene="",
        ).prompt
        self.assertIn("HOUSE RULES (they come first):", prompt)
        self.assertIn("House rule 3: Half cover gives +3 to AC", prompt)
        self.assertLess(prompt.index("HOUSE RULES"), prompt.index("RULES ENTRIES"))
        self.assertIn("- Cover (rule) [SRD 5.2.1 p. 179]:", prompt)

    def test_a_glossary_word_does_not_make_a_dmbot_question_a_rules_question(self) -> None:
        prompt = context.build(
            "can you track initiative?",
            target="2024",
            fallback="2014",
            index=INDEX,
            house_rules=[],
            lookup=None,
            scene="",
        ).prompt
        self.assertIn("ABOUT DMBOT", prompt)

    def test_a_class_feature_is_cited_with_its_source(self) -> None:
        prompt = context.build(
            "how does rage work",
            target="2024",
            fallback="2014",
            index=INDEX,
            house_rules=[],
            lookup=None,
            scene="",
        )
        self.assertIn(
            "- Rage (feature) [SRD 5.2.1 p. 28]: FACTS: Class feature, Barbarian level 1",
            prompt.prompt,
        )
        self.assertIn("SRD 5.2.1 p. 28", prompt.sources)

    def test_a_named_class_picks_its_feature_and_otherwise_every_class_has_its_say(self) -> None:
        only = [h.entry.details["class"] for h in self.hits("what does rogue evasion do")]
        self.assertEqual(only, ["Rogue"])
        every = [h.entry.details["class"] for h in self.hits("what does evasion do")]
        self.assertEqual(sorted(every), ["Monk", "Rogue"])

    def test_a_named_spell_keeps_the_places_to_itself_and_a_feature_is_no_rules_question(
        self,
    ) -> None:
        self.assertEqual(
            [h.entry.name for h in self.hits("can fireball and rage stack")], ["Fireball"]
        )
        prompt = context.build(
            "can you track extra attack?",
            target="2024",
            fallback="2014",
            index=INDEX,
            house_rules=[],
            lookup=None,
            scene="",
        ).prompt
        self.assertIn("ABOUT DMBOT", prompt)

    def test_a_name_many_classes_share_leaves_a_place_for_the_next_name(self) -> None:
        names = [h.entry.name for h in self.hits("how do rage and extra attack and evasion work")]
        self.assertEqual(len(names), 3)
        self.assertEqual(sorted(set(names)), ["Evasion", "Extra Attack", "Rage"])
        classes = [h.entry.details["class"] for h in self.hits("how does extra attack work")]
        self.assertEqual(
            len(classes), 3
        )  # a name alone gets the three places, five classes share it

    def test_a_house_rule_on_a_feature_comes_first(self) -> None:
        prompt = context.build(
            "how does rage work",
            target="2024",
            fallback="2014",
            index=INDEX,
            house_rules=[house(2, "Rage ends if you cast a spell.")],
            lookup=None,
            scene="",
        ).prompt
        self.assertLess(prompt.index("HOUSE RULES"), prompt.index("RULES ENTRIES"))

    def test_the_glossary_adds_at_most_three_entries(self) -> None:
        found = self.hits("explain cover, dash, long rest and short rest")
        self.assertEqual(
            [h.entry.name for h in found], ["Cover", "Long Rest", "Short Rest"]
        )  # longest names first


class MentionedNames(unittest.TestCase):
    def test_a_confirmed_name_with_what_dmbot_knows(self) -> None:
        lookup = CampaignLookup.build(data())
        lines = context.mentioned_names("what do I know about Belleros", lookup)
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].startswith("Belleros (NPC)"))

    def test_secret_and_unconfirmed_names_are_left_out(self) -> None:
        lookup = CampaignLookup.build(data())
        self.assertEqual(context.mentioned_names("who is the hooded stranger", lookup), [])
        self.assertEqual(context.mentioned_names("tell me about Kael", lookup), [])

    def test_links_are_not_named_here_recall_gives_the_safe_ones(self) -> None:
        link = Relation(
            "r1", BELLEROS, "serves", CERRIC, "", 1.0, "confirmed", "dm", (), None, None,
            None, None, False, 0,
        )  # fmt: skip
        lookup = CampaignLookup.build(data(relations=(link,)))
        (line,) = context.mentioned_names("what about Cerric", lookup)
        self.assertNotIn("Linked to", line)
        self.assertNotIn("Belleros", line)

    def test_no_lookup_no_names(self) -> None:
        self.assertEqual(context.mentioned_names("what about Belleros", None), [])

    def test_the_names_reach_the_prompt_and_are_a_source(self) -> None:
        lookup = CampaignLookup.build(data())
        built = context.build(
            "what about Belleros",
            target="2024",
            fallback="2014",
            index=INDEX,
            house_rules=[],
            lookup=lookup,
            scene="",
        )
        self.assertIn("CAMPAIGN NAMES", built.prompt)
        self.assertIn("campaign names", built.sources)
        self.assertNotIn("hooded stranger", built.prompt)


class Possessives(unittest.TestCase):
    def test_a_possessive_still_finds_the_entry(self) -> None:
        for question in ("Does Fireball's radius go through walls", "what is a Goblin\u2019s AC"):
            self.assertTrue(context.mentioned_rules(question, INDEX, "2024", "2014"), question)


class SecretIdentities(unittest.TestCase):
    def test_a_person_with_a_secret_name_gets_no_description_or_links(self) -> None:
        # Belleros has a secret alias (the hooded stranger): only his name is given.
        described = replace(data().entities[0], description="Secretly the lich's brother.")
        lookup = CampaignLookup.build(data(entities=(described, *data().entities[1:])))
        (line,) = context.mentioned_names("what about Belleros", lookup)
        self.assertEqual(line, "Belleros (NPC)")
        self.assertNotIn("lich", line)

    def test_nobody_gets_their_description_sent_it_is_the_dms_note(self) -> None:
        secret_sentence = "Secretly the duke's spy and the lich's brother."
        described = replace(data().entities[1], description=secret_sentence)
        lookup = CampaignLookup.build(
            data(entities=(data().entities[0], described, data().entities[2]))
        )
        (line,) = context.mentioned_names("what about Cerric", lookup)
        self.assertEqual(line, "Cerric (NPC)")
        built = context.build(
            "what about Cerric", target="2024", fallback="2014", index=INDEX, house_rules=[],
            lookup=lookup, scene="",
        )  # fmt: skip
        self.assertNotIn("duke", built.prompt)
        self.assertNotIn("lich", built.prompt)

    def test_other_names_that_are_not_secret_are_given(self) -> None:
        from tests.test_memory_lookup import alias

        lookup = CampaignLookup.build(
            data(aliases=(*data().aliases, alias(CERRIC, "Cer the Baker")))
        )  # fmt: skip
        (line,) = context.mentioned_names("what about Cerric", lookup)
        self.assertEqual(line, "Cerric (NPC). Also called: Cer the Baker.")
        # Only confirmed names, at most three, and never a spelling that is a secret name's.
        lookup = CampaignLookup.build(
            data(aliases=(*data().aliases,
                          alias(CERRIC, "Cer", status="proposed"),
                          alias(CERRIC, "Baker One"), alias(CERRIC, "Baker Two"),
                          alias(CERRIC, "Baker Three"), alias(CERRIC, "Baker Four"),
                          alias(CERRIC, "The Hooded Stranger")))
        )  # fmt: skip
        (many,) = context.mentioned_names("what about Cerric", lookup)
        self.assertEqual(many, "Cerric (NPC). Also called: Baker One, Baker Two, Baker Three.")
        self.assertNotIn("Cer,", many)  # not confirmed
        self.assertNotIn("Hooded", many)  # the same words as Belleros's secret name
        # Someone with a secret name is given by name only, other names included.
        (hidden,) = context.mentioned_names("what about Belleros", lookup)
        self.assertEqual(hidden, "Belleros (NPC)")


class MagicItems(unittest.TestCase):
    """The SRD's magic items (#1912): cited like the class features, when the question names no
    spell, condition or creature."""

    def hits(self, question: str) -> list[Hit]:
        return context.mentioned_rules(question, INDEX, "2024", "2014")

    def build(self, question: str) -> context.Context:
        return context.build(
            question,
            target="2024",
            fallback="2014",
            index=INDEX,
            house_rules=[],
            lookup=None,
            scene="",
        )

    def test_a_question_naming_an_item_cites_it(self) -> None:
        built = self.build("what does a bag of holding do")
        self.assertIn(
            "- Bag of Holding (item) [SRD 5.2.1 p. 212]: FACTS: Wondrous Item, uncommon.",
            built.prompt,
        )
        self.assertIn("SRD 5.2.1 p. 212", built.sources)

    def test_an_item_that_needs_attunement_says_so_and_for_whom(self) -> None:
        self.assertIn(
            "uncommon, needs attunement. ", self.build("is a cloak of protection ok").prompt
        )
        hat = self.build("how does the hat of many spells work").prompt
        self.assertIn("needs attunement by a Wizard", hat)

    def test_a_name_longer_than_four_words_is_found(self) -> None:
        (hit,) = self.hits("does an amulet of proof against detection and location protect me")
        self.assertEqual(hit.entry.name, "Amulet of Proof against Detection and Location")

    def test_the_other_name_of_an_item_finds_it(self) -> None:
        self.assertEqual(
            [h.entry.name for h in self.hits("what does a luckstone do")],
            ["Stone of Good Luck (Luckstone)"],
        )

    def test_a_word_in_an_item_name_alone_finds_nothing_and_a_named_spell_keeps_the_places(
        self,
    ) -> None:
        self.assertEqual(self.hits("does the bag hold it"), [])
        self.assertEqual(self.hits("how does rope work"), [])
        self.assertEqual(
            [h.entry.name for h in self.hits("can a bag of holding take a fireball")], ["Fireball"]
        )

    def test_a_potion_of_healing_is_the_item_and_not_the_glossary_word(self) -> None:
        (hit,) = self.hits("can the party use a potion of healing")
        self.assertEqual((hit.entry.kind, hit.entry.name), ("item", "Potions of Healing"))

    def test_an_item_named_with_a_spell_gives_the_places_to_the_spell(self) -> None:
        # Sending Stones are found by their own name; a named spell (here Sending) wins the places.
        self.assertEqual(
            [h.entry.name for h in self.hits("what do sending stones do")], ["Sending"]
        )

    def test_a_feat_species_or_background_is_cited_when_the_question_says_what_it_is(self) -> None:
        self.assertEqual(
            [(h.entry.kind, h.entry.name) for h in self.hits("what does the alert feat do")],
            [("feat", "Alert")],
        )
        built = self.build("what does the alert feat do")
        self.assertIn("- Alert (feat) [SRD 5.2.1 p. 87]: FACTS: Origin feat.", built.prompt)
        self.assertIn("SRD 5.2.1 p. 87", built.sources)

    def test_an_everyday_word_alone_is_not_a_feat_or_a_background(self) -> None:
        for question in ("the guards are alert", "a sage walks in", "the lucky sage"):
            self.assertEqual(self.hits(question), [], question)

    def test_a_species_name_is_enough_but_human_needs_the_word_species(self) -> None:
        names = [h.entry.name for h in self.hits("what is a goliath's speed")]
        self.assertIn("Goliath", names)  # (and the glossary's Speed)
        self.assertEqual(self.hits("a human walks in"), [])
        self.assertEqual(
            [h.entry.name for h in self.hits("can the human species see in the dark")], ["Human"]
        )
        self.assertIn("Speed: 35 feet", self.build("what is a goliath's speed").prompt)

    def test_a_background_comes_with_the_feat_it_gives_cited_as_the_feat(self) -> None:
        got = [
            (h.entry.kind, h.entry.name) for h in self.hits("what does the sage background give")
        ]
        self.assertEqual(got, [("background", "Sage"), ("feat", "Magic Initiate")])

    def test_most_species_names_are_everyday_words_and_need_the_cue(self) -> None:
        for question in ("the dwarf bartender", "a dwarf walks in", "help, a gnome", "the elf"):
            self.assertEqual([h for h in self.hits(question) if h.entry.kind == "species"], [])
        self.assertEqual(
            [
                h.entry.name
                for h in self.hits("the dwarf species speed")
                if h.entry.kind == "species"
            ],
            ["Dwarf"],
        )
        self.assertEqual(
            [h.entry.name for h in self.hits("a dragonborn walks in")], ["Dragonborn"]
        )  # not an everyday word

    def test_the_cue_makes_a_background_or_species_win_over_a_creature_of_the_same_name(
        self,
    ) -> None:
        got = [
            (h.entry.kind, h.entry.name) for h in self.hits("what does the acolyte background give")
        ]
        self.assertEqual(got, [("background", "Acolyte"), ("feat", "Magic Initiate")])
        orc = [(h.entry.kind, h.entry.name) for h in self.hits("an orc species speed")]
        self.assertIn(("species", "Orc"), orc)
        self.assertEqual([h.entry.kind for h in self.hits("what does an acolyte do")], ["monster"])

    def test_a_named_spell_still_keeps_the_places_with_the_cue_word(self) -> None:
        got = [(h.entry.kind, h.entry.name) for h in self.hits("fireball and the sage background")]
        self.assertEqual(got, [("spell", "Fireball")])

    def test_a_long_feat_name_needs_no_extra_word(self) -> None:
        self.assertEqual(
            [h.entry.name for h in self.hits("how does great weapon fighting work")],
            ["Great Weapon Fighting"],
        )

    def test_a_weapon_armor_or_mastery_is_cited_when_asked_about(self) -> None:
        self.assertEqual(
            [(h.entry.kind, h.entry.name) for h in self.hits("what's the damage of a longsword")],
            [("weapon", "Longsword")],
        )
        self.assertEqual(
            [(h.entry.kind, h.entry.name) for h in self.hits("what does topple do")],
            [("rule", "Topple")],
        )
        built = self.build("what's the damage of a longsword")
        self.assertIn(
            "- Longsword (weapon) [SRD 5.2.1 p. 91]: FACTS: Martial Melee, 1d8 Slashing, "
            "Versatile (1d10), mastery Sap.",
            built.prompt,
        )
        self.assertIn("Disadvantage on Stealth", self.build("how heavy is plate armor").prompt)

    def test_an_everyday_weapon_or_property_word_alone_adds_nothing(self) -> None:
        for question in ("the guard swings a club", "the light is dim", "a heavy door", "push it"):
            gear = [h for h in self.hits(question) if h.entry.kind in ("weapon", "armor", "rule")]
            self.assertEqual(gear, [], question)

    def test_a_cue_word_brings_the_one_word_weapon_or_property(self) -> None:
        self.assertEqual([h.entry.name for h in self.hits("what damage does a club do")], ["Club"])
        props = [h.entry.name for h in self.hits("what does the light property do")]
        self.assertEqual(props, ["Light"])

    def test_the_shield_spell_and_the_shield_armor_are_told_apart_by_the_question(self) -> None:
        self.assertEqual([(h.entry.kind) for h in self.hits("cast shield")], ["spell"])
        self.assertEqual(
            [(h.entry.kind) for h in self.hits("what is the AC of a shield")], ["armor"]
        )

    def test_a_cue_word_does_not_take_the_place_of_a_spell_the_question_calls_a_spell(self) -> None:
        for question in (
            "does the shield spell stop damage",
            "the wizard casts shield and I roll damage",
            "can I cast the Light spell on a weapon",
        ):
            kinds = [h.entry.kind for h in self.hits(question)]
            self.assertIn("spell", kinds, question)
            self.assertNotIn("armor", kinds, question)

    def test_a_cue_word_brings_the_other_gear_in_the_question_too(self) -> None:
        got = [(h.entry.kind, h.entry.name) for h in self.hits("slow mastery for a whip")]
        self.assertIn(("rule", "Slow"), got)
        self.assertIn(("weapon", "Whip"), got)

    def test_an_item_is_no_rules_question(self) -> None:
        prompt = self.build("can you track a bag of holding?").prompt
        self.assertIn("ABOUT DMBOT", prompt)


class AboutDMbot(unittest.TestCase):
    def test_the_digest_is_sent_for_questions_about_dmbot_not_for_game_ones(self) -> None:
        for question in (
            "how do I stop DMbot recording me",
            "is this being recorded",
            "what does /dmbot names do",
            "can players read the transcript",
        ):
            self.assertTrue(context.asks_about_dmbot(question), question)
        for question in ("how do I handle a grapple", "what is the plan for the heist"):
            self.assertFalse(context.asks_about_dmbot(question), question)

    def test_the_digest_says_what_it_must(self) -> None:
        text = context.about_dmbot()
        for phrase in (
            "/consent give",
            "Anthropic",
            "[DM Sidebar]",
            "holds the DM's secrets",
            "never decides",
            "Legacy 2014",
        ):
            self.assertIn(phrase, text)


class HouseRulesChosen(unittest.TestCase):
    def test_one_naming_the_entry_comes_before_one_sharing_a_word(self) -> None:
        hits = context.mentioned_rules("how does fireball spread", INDEX, "2024", "2014")
        rules = [
            house(1, "Falling damage is doubled."),
            house(2, "Spread out when casting anything big."),
            house(3, "Fireball ignores cover."),
        ]
        chosen = context.relevant_house_rules(rules, "how does fireball spread", hits)
        self.assertEqual([r.number for r in chosen], [3, 2])

    def test_at_most_five(self) -> None:
        rules = [house(n, f"Fireball rule number {n}.") for n in range(1, 12)]
        hits = context.mentioned_rules("fireball", INDEX, "2024", "2014")
        self.assertEqual(len(context.relevant_house_rules(rules, "fireball", hits)), 5)


class Shortening(unittest.TestCase):
    """A long rules entry is cut at a sentence end and says so (#1910)."""

    def test_a_text_that_fits_is_kept_whole(self) -> None:
        self.assertEqual(context.shorten("One.  Two.", 50), "One. Two.")

    def test_a_long_text_is_cut_at_the_end_of_a_sentence_and_marked(self) -> None:
        text = "First sentence here. " + "Second sentence goes on and on. " * 10
        cut = context.shorten(text, 120)
        self.assertLessEqual(len(cut), 120)
        self.assertTrue(cut.endswith("sentence goes on and on." + context.CUT_MARK), cut)

    def test_a_text_with_no_sentence_end_in_its_last_half_is_cut_at_the_limit(self) -> None:
        cut = context.shorten("Short. " + "word " * 100, 60)
        self.assertEqual(len(cut), 60)
        self.assertTrue(cut.endswith(context.CUT_MARK))

    def test_every_class_feature_but_a_few_fits_whole(self) -> None:
        features = [e for e in srd().entries if e.kind == "feature"]
        long = [
            e.name for e in features if len(" ".join(e.text.split())) > context.FEATURE_TEXT_MAX
        ]
        self.assertLess(
            len(long), 40, long
        )  # a table or list that grows past this should be looked at


class TheScene(unittest.TestCase):
    def test_only_the_tail_is_sent(self) -> None:
        built = context.build(
            "does it matter",
            target="2024",
            fallback="2014",
            index=INDEX,
            house_rules=[],
            lookup=None,
            scene="a" * 5000 + " the end",
        )
        self.assertIn("the end", built.prompt)
        self.assertLess(len(built.prompt), 2000)


class RealStoreIsolation(DatabaseTest):
    async def test_a_second_campaigns_house_rule_never_reaches_the_first_prompt(self) -> None:
        campaigns = CampaignStore(self.db)
        mine = await campaigns.create(111, "Mine", 7)
        other = await campaigns.create(111, "Other", 7)
        rules = HouseRuleStore(self.db)
        await rules.add(111, mine.id, 7, "Fireball needs a clear line of sight.")
        await rules.add(111, other.id, 7, "Fireball hits twice, OTHER-CAMPAIGN-ONLY.")

        async def gate(c: Any, user: int) -> None:
            return None

        async def houses(c: Any) -> Any:
            return await rules.list(c.guild_id, c.id)

        async def names(c: Any) -> None:
            return None

        ai = FakeAI([reply("Yes. House rule 1.", "House rule 1")])
        built = Sidebar(ai, INDEX, gate=gate, houses=houses, names=names)
        await built.answer(replace(mine), "do you need line of sight for fireball", asker_id=7)
        self.assertIn("clear line of sight", ai.prompts[0])
        self.assertNotIn("OTHER-CAMPAIGN-ONLY", ai.prompts[0])


if __name__ == "__main__":
    unittest.main()
