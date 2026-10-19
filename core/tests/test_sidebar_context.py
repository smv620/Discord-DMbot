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


class CombatEncounters(unittest.TestCase):
    """The SRD's Combat Encounters section (#2000): the DM's, found only when asked for."""

    def hits(self, question: str, *, table: bool = False) -> list[Hit]:
        return context.mentioned_rules(question, INDEX, "2024", "2014", table=table)

    def names(self, question: str, *, table: bool = False) -> list[str]:
        return [h.entry.name for h in self.hits(question, table=table)]

    def prompt(self, question: str, *, table: bool = False) -> str:
        return context.build(
            question,
            target="2024",
            fallback="2014",
            index=INDEX,
            house_rules=[],
            lookup=None,
            scene="",
            table=table,
        ).prompt

    def test_the_cue_words_find_the_budget_entry(self) -> None:
        for question in (
            "how do I work out the xp budget",
            "what is a deadly encounter",
            "how do I choose encounter difficulty",
            "what is the difficulty of a fight with 4 goblins",
        ):
            self.assertIn("Step 2: Determine Your XP Budget", self.names(question), question)

    def test_a_fight_with_a_party_finds_it_and_the_line_for_the_level_asked(self) -> None:
        question = "how hard is a fight with 4 goblins for 4 level 3 characters"
        self.assertEqual(self.names(question)[0], "Step 2: Determine Your XP Budget")
        prompt = self.prompt(question)
        self.assertIn("Level 3: Low 150, Moderate 225, High 400 XP per character", prompt)
        self.assertNotIn("Level 2:", prompt)
        self.assertNotIn("Level 4:", prompt)
        (hit,) = [h for h in self.hits(question) if h.entry.name.startswith("Step 2")]
        self.assertEqual(hit.entry.citation, "SRD 5.2.1, Gameplay Toolbox, p. 202")

    def test_cr_with_a_party_finds_it_and_a_creatures_cr_does_not(self) -> None:
        self.assertIn("Step 2: Determine Your XP Budget", self.names("what cr for my party"))
        self.assertNotIn("Step 2: Determine Your XP Budget", self.names("what's a goblin's CR"))
        (hit,) = self.hits("what's a goblin's CR")
        self.assertEqual(hit.entry.kind, "monster")

    def test_plain_questions_leave_it_alone(self) -> None:
        for question in ("how does long rest work", "what is a fight", "does a goblin have xray"):
            self.assertFalse(
                [n for n in self.names(question) if "XP" in n or "Encounter" in n], question
            )

    def test_a_table_question_gets_none_of_it(self) -> None:
        question = "how hard is a fight with 4 goblins for 4 level 3 characters"
        self.assertNotIn("XP Budget", self.prompt(question, table=True))

    def test_a_named_creature_is_never_pushed_out_by_the_budget(self) -> None:
        names = self.names("how hard is a fight with a goblin, an ogre and a wolf for the party")
        self.assertEqual(len(names), 3)
        self.assertNotIn("Step 2: Determine Your XP Budget", names)

    def test_everyday_combat_talk_leaves_it_alone(self) -> None:
        for question in (
            "what is a deadly trap",
            "how does surprise work in combat for characters",
            "what level can a wizard fight",
            "what is the xp for a level 3 goblin",
            "troubleshooting my mic",
        ):
            self.assertFalse(
                [n for n in self.names(question) if "Budget" in n or "Encounter" in n], question
            )

    def test_xp_or_difficulty_alone_is_everyday_talk(self) -> None:
        for question in ("how much xp does a goblin give", "what is the difficulty of the lock"):
            self.assertNotIn("Step 2: Determine Your XP Budget", self.names(question), question)
        self.assertIn("Step 2: Determine Your XP Budget", self.names("xp for a party of four"))

    def test_levels_are_read_in_the_ways_a_dm_says_them(self) -> None:
        for question in (
            "xp budget for levels 3 and 4",
            "xp budget for 3rd level and 4th level characters",
        ):
            entry = self.hits(question)[0].entry
            kept = context.only_levels_asked(entry, question)
            self.assertIn("Level 3:", kept, question)
            self.assertIn("Level 4:", kept, question)
            self.assertNotIn("Level 5:", kept, question)

    def test_only_the_levels_asked_are_kept(self) -> None:
        entry = next(h.entry for h in self.hits("xp budget for level 10 and level 20 characters"))
        kept = context.only_levels_asked(entry, "xp budget for level 10 and level 20 characters")
        self.assertIn("Level 10:", kept)
        self.assertIn("Level 20:", kept)
        self.assertNotIn("Level 11:", kept)
        self.assertIn("do not multiply", kept)


class MagicItemRules(unittest.TestCase):
    """The SRD's Magic Items section (#2001): the players' rules, found when asked for."""

    def hits(self, question: str, *, table: bool = False) -> list[Hit]:
        return context.mentioned_rules(question, INDEX, "2024", "2014", table=table)

    def names(self, question: str, *, table: bool = False) -> list[str]:
        return [h.entry.name for h in self.hits(question, table=table)]

    def test_each_cue_word_finds_its_rule(self) -> None:
        for question, name in (
            ("how does attunement work", "Attunement"),
            ("how many items can I attune to", "Attunement"),
            ("how many charges does a wand have", "Magic Item Charges"),
            ("what is a cursed item", "Cursed Items"),
            ("how do I craft a magic item", "Crafting Magic Items"),
            ("tell me about sentient items", "Sentient Magic Items"),
            ("what does rarity mean", "Magic Item Rarity"),
            ("can a fighter attune to an item with a class prerequisite", "Attunement"),
        ):
            self.assertIn(name, self.names(question), question)

    def test_attune_to_a_bag_of_holding_gives_the_rule_and_the_item_each_cited(self) -> None:
        hits = self.hits("can I attune to a Bag of Holding")
        self.assertEqual(
            [(h.entry.kind, h.entry.name) for h in hits],
            [("rule", "Attunement"), ("item", "Bag of Holding")],
        )
        self.assertEqual(hits[0].entry.citation, "SRD 5.2.1, Rules Glossary, p. 177")
        self.assertEqual(hits[1].entry.citation, "SRD 5.2.1, Magic Items, p. 212")

    def test_a_players_rule_is_cited_in_the_magic_items_section(self) -> None:
        (hit,) = self.hits("how many charges does a wand have")
        self.assertEqual(hit.entry.citation, "SRD 5.2.1, Magic Items, p. 206")
        self.assertFalse(hit.entry.dm_side)

    def test_charges_come_before_the_wand_and_requirements_find_the_prerequisites(self) -> None:
        self.assertEqual(self.names("how many charges does a wand have")[0], "Magic Item Charges")
        self.assertIn(
            "Attunement Prerequisites", self.names("what are the attunement requirements")
        )

    def test_rarity_and_price_give_the_value_lines(self) -> None:
        (hit,) = self.hits("what does rarity mean for price")
        self.assertEqual(hit.entry.name, "Magic Item Values by Rarity")
        prompt = context.build(
            "what does rarity mean for price",
            target="2024",
            fallback="2014",
            index=INDEX,
            house_rules=[],
            lookup=None,
            scene="",
        ).prompt
        self.assertIn("Uncommon: 400 GP", prompt)

    def test_crafting_time_gives_the_time_and_cost_lines(self) -> None:
        question = "how long does crafting a rare item take"
        self.assertEqual(self.names(question), ["Crafting: Time and Cost"])

    def test_ordinary_crafting_and_a_stray_magic_item_leave_the_rules_out(self) -> None:
        for question in (
            "how long does crafting a longsword take",
            "how long does it take to craft a longsword",
            "this magic item is cool",
        ):
            self.assertNotIn("Magic Items", self.names(question), question)
            self.assertFalse([n for n in self.names(question) if n.startswith("Crafting")])

    def test_a_one_word_name_needs_a_cue_and_a_longer_one_does_not(self) -> None:
        self.assertEqual(self.names("ring of a hundred rooms"), [])
        self.assertIn("Magic Armor", self.names("what is a magic armor"))
        self.assertIn("Magic Potions", self.names("mixing potions"))
        self.assertEqual(self.names("potions are lovely"), [])

    def test_everyday_talk_about_items_leaves_the_rules_out(self) -> None:
        for question in ("what does the rogue do", "does the item in the shop cost much"):
            self.assertFalse(
                [n for n in self.names(question) if n.startswith(("Magic", "Crafting"))]
            )

    def test_the_gm_s_part_goes_to_the_dm_and_not_to_the_table(self) -> None:
        for question, name in (
            ("tell me about sentient items", "Sentient Magic Items"),
            ("what are paired items", "Paired Items"),
            ("what is unusual anatomy", "Unusual Anatomy"),
        ):
            self.assertIn(name, self.names(question), question)
            self.assertEqual(self.names(question, table=True), [], question)
            (hit,) = [h for h in self.hits(question) if h.entry.name == name]
            self.assertTrue(hit.entry.dm_side, name)

    def test_the_players_part_is_for_the_table_too(self) -> None:
        self.assertIn(
            "Attunement Prerequisites",
            self.names("what are the attunement prerequisites", table=True),
        )

    def test_a_long_rule_is_sent_whole(self) -> None:
        prompt = context.build(
            "how does crafting cost work",
            target="2024",
            fallback="2014",
            index=INDEX,
            house_rules=[],
            lookup=None,
            scene="",
        ).prompt
        self.assertIn("Legendary: 250 days, 100,000 GP", prompt)
        self.assertNotIn("(cut)", prompt)


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

    def test_an_item_only_the_2014_rules_have_is_cited_with_the_legacy_tag(self) -> None:
        built = self.build("what does an arrow of slaying do")
        self.assertIn("- Arrow of Slaying (item) [SRD 5.1 p. 209 [Legacy 2014]]", built.prompt)
        self.assertIn("FACTS: Weapon (Arrow), very rare.", built.prompt)
        self.assertIn("SRD 5.1 p. 209 [Legacy 2014]", built.sources)

    def test_an_item_both_editions_have_is_the_2024_one_alone(self) -> None:
        built = self.build("what does a bag of holding do")
        self.assertIn("SRD 5.2.1 p. 212", built.prompt)
        self.assertNotIn("SRD 5.1", built.prompt)

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

    def test_a_short_armor_name_needs_a_cue_and_is_the_2024_armor(self) -> None:
        # (#1937) "plate" and "leather" are the 5.1's names for Plate Armor and Leather Armor
        got = [(h.entry.name, h.entry.edition) for h in self.hits("what is the AC of plate")]
        self.assertEqual(got, [("Plate Armor", "2024")])
        got = [(h.entry.name, h.entry.edition) for h in self.hits("how heavy is leather armor")]
        self.assertEqual(got, [("Leather Armor", "2024")])
        for question in ("the leather pouch", "he wears plate", "a hide of the beast"):
            armor = [h for h in self.hits(question) if h.entry.kind == "armor"]
            self.assertEqual(armor, [], question)

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
        got = [h.entry.kind for h in self.hits("what is the AC of a shield")]
        self.assertEqual(sorted(got), ["armor", "spell"])  # both, each cited: "AC" fits both

    def test_a_cue_word_does_not_take_the_place_of_a_spell_the_question_calls_a_spell(self) -> None:
        for question in (
            "does the shield spell stop damage",
            "the wizard casts shield and I roll damage",
            "can I cast the Light spell on a weapon",
        ):
            kinds = [h.entry.kind for h in self.hits(question)]
            self.assertIn("spell", kinds, question)
            self.assertNotIn("armor", kinds, question)

    def test_ac_or_damage_beside_a_spell_name_gives_the_spell_and_the_gear(self) -> None:
        got = [(h.entry.kind, h.entry.name) for h in self.hits("how much AC does shield give")]
        self.assertIn(("spell", "Shield"), got)
        self.assertIn(("armor", "Shield"), got)
        spell_only = [(h.entry.kind) for h in self.hits("the shield spell")]
        self.assertEqual(spell_only, ["spell"])

    def test_a_word_only_gear_has_makes_a_shield_the_armor_and_not_the_spell(self) -> None:
        # "armor" can't be said of the spell: the armor alone answers (the spell is dropped)
        got = [(h.entry.kind, h.entry.name) for h in self.hits("what armor is a shield, its AC")]
        self.assertEqual(got, [("armor", "Shield")])

    def test_a_cue_word_brings_the_other_gear_in_the_question_too(self) -> None:
        got = [(h.entry.kind, h.entry.name) for h in self.hits("slow mastery for a whip")]
        self.assertIn(("rule", "Slow"), got)
        self.assertIn(("weapon", "Whip"), got)

    def test_gear_and_tools_are_cited_when_asked_about(self) -> None:
        self.assertEqual(
            [(h.entry.kind, h.entry.name) for h in self.hits("what does a healer's kit do")],
            [("gear", "Healer’s Kit")],
        )
        self.assertEqual(
            [(h.entry.kind, h.entry.name) for h in self.hits("what are thieves' tools for")],
            [("tool", "Thieves’ Tools")],
        )
        built = self.build("how much does a torch cost")
        self.assertIn(
            "- Torch (gear) [SRD 5.2.1 p. 100]: FACTS: Adventuring Gear, 1 CP, 1 lb.", built.prompt
        )

    def test_an_everyday_gear_word_alone_adds_nothing(self) -> None:
        for question in (
            "they light a torch",
            "he throws a rope down",
            "the oil is hot",
            "a bell rings",
        ):
            gear = [h for h in self.hits(question) if h.entry.kind in ("gear", "tool")]
            self.assertEqual(gear, [], question)

    def test_a_cue_word_brings_the_one_word_gear_and_a_mount_gives_way_to_its_price(self) -> None:
        self.assertEqual([h.entry.name for h in self.hits("how much does rope weigh")], ["Rope"])
        self.assertEqual(
            [(h.entry.kind, h.entry.name) for h in self.hits("how much does a camel cost")],
            [("gear", "Camel")],
        )
        self.assertEqual([h.entry.kind for h in self.hits("what does a camel do")], ["monster"])
        horse = self.hits("what does a riding horse cost")
        self.assertEqual([(h.entry.kind, h.entry.name) for h in horse], [("gear", "Horse, Riding")])

    def test_a_named_spell_is_kept_when_gear_is_named_beside_it(self) -> None:
        got = [(h.entry.kind, h.entry.name) for h in self.hits("the healer's kit and fireball")]
        self.assertIn(("spell", "Fireball"), got)
        self.assertIn(("gear", "Healer’s Kit"), got)
        self.assertEqual([h.entry.kind for h in self.hits("cast shield")], ["spell"])

    def test_a_race_only_the_2014_rules_have_is_cited_with_the_legacy_tag(self) -> None:
        built = self.build("what are the half-orc traits")
        self.assertIn("- Half-Orc (species) [SRD 5.1 p. 7 [Legacy 2014]]", built.prompt)
        self.assertIn("Relentless Endurance", built.prompt)
        got = [(h.entry.kind, h.entry.name) for h in self.hits("does a half elf see in the dark")]
        self.assertNotIn(("monster", "Elf"), got)

    def test_the_creature_of_the_shorter_name_gives_way_only_to_a_longer_species_name(self) -> None:
        self.assertEqual(
            [(h.entry.kind, h.entry.name) for h in self.hits("the half orc swings")],
            [("species", "Half-Orc")],
        )
        self.assertEqual([h.entry.kind for h in self.hits("the orc swings")], ["monster"])

    def test_a_2014_race_is_sent_whole_with_its_subrace(self) -> None:
        built = context.build(
            "a dwarf species hit points",
            target="2014",
            fallback="2024",
            index=INDEX,
            house_rules=[],
            lookup=None,
            scene="",
        )
        self.assertIn("Subrace: Hill Dwarf", built.prompt)
        self.assertIn("Dwarven Toughness. Your hit point maximum increases by 1", built.prompt)
        self.assertNotIn("(cut)", built.prompt)

    def test_a_dwarf_in_a_2024_campaign_is_the_2024_species_alone(self) -> None:
        got = [(h.entry.edition, h.entry.name) for h in self.hits("a dwarf species speed")]
        self.assertIn(("2024", "Dwarf"), got)
        self.assertNotIn(("2014", "Dwarf"), got)

    def test_a_2014_weapon_has_no_mastery_and_a_2014_only_piece_is_cited_with_the_tag(self) -> None:
        old = context.build(
            "what's the damage of a longsword",
            target="2014",
            fallback="2024",
            index=INDEX,
            house_rules=[],
            lookup=None,
            scene="",
        )
        self.assertIn("- Longsword (weapon) [SRD 5.1 p. 66 [Legacy 2014]]", old.prompt)
        self.assertIn("FACTS: Martial Melee, 1d8 Slashing, Versatile (1d10).", old.prompt)
        self.assertNotIn("mastery", old.prompt)
        new = self.build("what's the damage of a longsword")
        self.assertIn("SRD 5.2.1 p. 91", new.prompt)
        self.assertNotIn("SRD 5.1", new.prompt)  # the 2024 weapon alone
        net = self.build("what damage does a net do")
        self.assertIn("(weapon) [SRD 5.1 p. 66 [Legacy 2014]]", net.prompt)

    def test_a_piece_of_gear_only_the_2014_book_has_needs_its_cue_word(self) -> None:
        self.assertEqual(
            [(h.entry.name, h.entry.edition) for h in self.hits("how much does an abacus cost")],
            [("Abacus", "2014")],
        )
        self.assertEqual(self.hits("he counts on an abacus"), [])

    def test_an_item_is_no_rules_question(self) -> None:
        prompt = self.build("can you track a bag of holding?").prompt
        self.assertIn("ABOUT DMBOT", prompt)


class TheCoreRules(unittest.TestCase):
    """The SRD 5.1's core rules in the sidebar (#1943)."""

    def names(self, question: str, target: str = "2024", fallback: str = "2014") -> list[str]:
        hits = context.mentioned_rules(question, INDEX, target, fallback)
        return [f"{h.entry.name} {h.entry.edition}" for h in hits]

    def test_an_everyday_5_1_title_adds_nothing_in_a_2024_campaign(self) -> None:
        for question in (
            "how much time does the party have",
            "can I use the water to put out the fire",
            "what is my wisdom bonus",
            "how much food do we need",
            "what about space and movement",
        ):
            self.assertEqual(self.names(question), [], question)

    def test_a_longer_5_1_rule_is_found_tagged_in_2024_and_plain_in_2014(self) -> None:
        self.assertEqual(self.names("how does underwater combat work"), ["Underwater Combat 2014"])
        self.assertEqual(self.names("how does grappling work", "2014", "2024"), ["Grappling 2014"])
        self.assertEqual(self.names("how does grappling work"), ["Grappling 2024"])

    def test_the_2024_feat_beats_the_2014_rule_of_the_same_name(self) -> None:
        self.assertEqual(
            self.names("how do I do two-weapon fighting"), ["Two-Weapon Fighting 2024"]
        )

    def test_a_single_word_is_found_in_a_2014_campaign(self) -> None:
        self.assertIn("Time 2014", self.names("how much time does the party have", "2014", "2024"))


class GameplayToolbox(unittest.TestCase):
    """The DM's chapter (#1935): poisons, traps, curses and the hazard and travel rules."""

    def hits(self, question: str) -> list[Hit]:
        return context.mentioned_rules(question, INDEX, "2024", "2014")

    def build(self, question: str, *, table: bool = False) -> context.Context:
        return context.build(
            question,
            target="2024",
            fallback="2014",
            index=INDEX,
            house_rules=[],
            lookup=None,
            scene="",
            table=table,
        )

    def names(self, question: str) -> list[tuple[str, str]]:
        return [(h.entry.kind, h.entry.name) for h in self.hits(question)]

    def test_a_poison_with_a_long_name_beats_the_creature_named_inside_it(self) -> None:
        self.assertEqual(
            self.names("what does Purple Worm Poison do?"), [("poison", "Purple Worm Poison")]
        )
        self.assertEqual(self.names("how much is wyvern poison"), [("poison", "Wyvern Poison")])
        self.assertEqual(self.names("what does a wyvern do"), [("monster", "Wyvern")])

    def test_a_poison_is_found_by_its_name_with_the_possessive_taken_off(self) -> None:
        self.assertEqual(self.names("what is Assassin's Blood"), [("poison", "Assassin’s Blood")])
        self.assertEqual(self.names("spiders sting poison dc")[:1], [("poison", "Spider’s Sting")])

    def test_a_one_word_name_counts_only_with_a_cue_word(self) -> None:
        for question in ("his malice grew", "a torpor falls over the room", "what does malice do"):
            self.assertEqual([k for k, _ in self.names(question) if k == "poison"], [], question)
        for cue in ("malice poison", "what is the dc for malice", "is malice a hazard"):
            self.assertIn(("poison", "Malice"), self.names(cue), cue)
        self.assertIn(("poison", "Torpor"), self.names("torpor poison"))

    def test_the_two_pits_are_both_found_by_pit_trap_and_one_trap_by_its_own_name(self) -> None:
        self.assertEqual(
            self.names("what's the DC to disarm a pit trap?"),
            [("trap", "Hidden Pit"), ("trap", "Spiked Pit")],
        )
        self.assertEqual(
            self.names("how does the falling net trap work"), [("trap", "Falling Net")]
        )
        self.assertEqual(self.names("the fire-casting statue"), [("trap", "Fire-Casting Statue")])

    def test_a_named_curse_and_the_hazard_rules_are_found(self) -> None:
        self.assertEqual(
            self.names("how does demonic possession work"), [("curse", "Demonic Possession")]
        )
        self.assertEqual(self.names("extreme cold dc"), [("rule", "Extreme Cold")])
        self.assertIn(("rule", "Special Movement"), self.names("special movement for flying"))

    def test_a_trap_or_poison_is_sent_with_its_facts_and_the_whole_text(self) -> None:
        trap = self.build("how do I disarm the spiked pit").prompt
        self.assertIn(
            "- Spiked Pit (trap) [SRD 5.2.1 p. 201]: FACTS: Deadly (Levels 1–4); "
            "Trigger A creature moves onto the pit’s lid; Duration Instantaneous. TEXT:",
            trap,
        )
        self.assertIn("Levels 17–20: Pit Depth 120 feet", trap)  # past the usual 1,200 characters
        poison = self.build("what does purple worm poison do").prompt
        self.assertIn(
            "- Purple Worm Poison (poison) [SRD 5.2.1 p. 198]: FACTS: Injury poison; "
            "Price 2,000 GP a dose. TEXT:",
            poison,
        )

    def test_a_table_question_never_gets_the_dms_chapter(self) -> None:
        for question in (
            "how do I disarm the pit trap",
            "what does purple worm poison do",
            "how does demonic possession work",
            "what is extreme cold",
        ):
            self.assertTrue(self.hits(question), question)  # the DM's sidebar finds it
            table = self.build(question, table=True)
            self.assertEqual(table.hits, (), question)
            self.assertNotIn("RULES ENTRIES", table.prompt, question)
            self.assertNotIn("Gameplay Toolbox", table.prompt, question)

    def test_a_comparison_never_brings_the_other_editions_dm_chapter_to_the_table(self) -> None:
        question = "travel pace 2014 vs 2024"
        for target, fallback in (("2014", "2024"), ("2024", "2014")):
            dms = context.mentioned_rules(question, INDEX, target, fallback, both=True)
            table = context.mentioned_rules(
                question, INDEX, target, fallback, both=True, table=True
            )
            self.assertTrue(any(h.entry.dm_side for h in dms), target)
            self.assertEqual([h for h in table if h.entry.dm_side], [], target)

    def test_a_spell_with_an_everyday_name_needs_cast_spell_or_cantrip(self) -> None:
        for question in ("they light a torch", "send a message", "we sleep here", "fly home"):
            self.assertEqual(self.hits(question), [], question)
        for question in ("I cast light on the torch", "the light spell", "cast sleep"):
            self.assertEqual([h.entry.kind for h in self.hits(question)], ["spell"], question)

    def test_a_table_question_still_gets_the_rules_glossary(self) -> None:
        table = self.build("how does cover work", table=True)
        self.assertEqual([h.entry.name for h in table.hits], ["Cover"])

    def test_the_toolbox_is_marked_as_the_dms(self) -> None:
        for hit in self.hits("purple worm poison") + self.hits("extreme cold"):
            self.assertTrue(hit.entry.dm_side, hit.entry.name)
        self.assertFalse(self.hits("how does cover work")[0].entry.dm_side)


class TheGameMasterChapters(unittest.TestCase):
    """The SRD 5.1's traps, diseases, poisons and madness (#1944): the DM's, like the 5.2.1's."""

    def names(self, question: str, target: str = "2024", fallback: str = "2014") -> list[str]:
        hits = context.mentioned_rules(question, INDEX, target, fallback)
        return [f"{h.entry.kind}:{h.entry.name} {h.entry.edition}" for h in hits]

    def test_a_2014_only_poison_trap_and_disease_are_found_tagged_in_a_2024_campaign(self) -> None:
        self.assertEqual(self.names("what does drow poison do"), ["poison:Drow Poison 2014"])
        self.assertEqual(self.names("the rolling sphere"), ["trap:Rolling Stone 2024"])

    def test_the_2014_chapters_are_first_in_a_2014_campaign(self) -> None:
        self.assertEqual(
            self.names("how does cackle fever work", "2014", "2024"), ["disease:Cackle Fever 2014"]
        )
        self.assertEqual(
            self.names("what is the dc for a pit trap", "2014", "2024"),
            ["trap:Simple Pit 2014", "trap:Hidden Pit 2014", "trap:Locking Pit 2014"],  # (three)
        )

    def test_the_2024_contagion_wins_in_2024_and_the_disease_in_2014(self) -> None:
        # all three diseases are contagions in the 5.2.1: the newer entry, never the 2014 one
        for name in ("cackle fever", "sewer plague", "sight rot"):
            got = self.names(name)
            self.assertEqual(len(got), 1, name)
            self.assertTrue(got[0].startswith("rule:") and got[0].endswith("2024"), got)
            old = self.names(name, "2014", "2024")
            self.assertTrue(old[0].startswith("disease:") and old[0].endswith("2014"), old)

    def test_a_table_question_gets_none_of_them(self) -> None:
        for question, target, fallback in (
            ("how does sight rot work", "2024", "2014"),
            ("what does drow poison do", "2014", "2024"),
            ("how do I disarm the hidden pit", "2014", "2024"),
        ):
            built = context.build(
                question,
                target=target,
                fallback=fallback,
                index=INDEX,
                house_rules=[],
                lookup=None,
                scene="",
                table=True,
            )
            self.assertEqual(built.hits, (), question)
            self.assertNotIn("Gameplay Toolbox", built.prompt, question)

    def test_the_disease_cue_makes_a_one_word_name_count(self) -> None:
        self.assertIn("rule:Madness 2014", self.names("madness table", "2014", "2024"))


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
