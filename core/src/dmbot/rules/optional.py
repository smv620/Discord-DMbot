"""Optional rules from other books (#49): what DMbot knows about them, without any book
text. Each rule has only its name, a one-line summary in our own words, and the book
it comes from (IP rule, CLAUDE.md). Pure: no Discord, no database.

A campaign's choice for each rule is stored in `campaign_optional_rules`
(`CampaignStore.set_optional_rule`); a rule with no stored choice follows the
campaign's `optional_rules_default`. A rule applies only to the rulesets it doesn't
clash with: one the newer books already include or change is listed for 2014 only.
Xanathar's and Tasha's are not legacy books as a whole (docs/PLAN.md): whatever the
2024 books don't reprint or replace stays listed for 2024, without a legacy tag.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

XANATHAR = "Xanathar's Guide to Everything"
TASHA = "Tasha's Cauldron of Everything"


@dataclass(frozen=True, slots=True)
class OptionalRule:
    id: str  # stored per campaign: letters, digits and - only, never renamed
    name: str  # short enough for a phone's menu
    summary: str  # one line, our own words
    source: str  # the book's name
    applies_to: tuple[str, ...] = ("2024", "2014")  # target rulesets it adds to


CATALOG: tuple[OptionalRule, ...] = (
    OptionalRule(
        "xge-no-long-rest",
        "Going without a long rest",
        "After 24 hours without a long rest, make a Constitution save or gain 1 level of "
        "Exhaustion.",
        XANATHAR,
    ),
    OptionalRule(
        "xge-sleep",
        "Sleeping in armor",
        "Sleeping in medium or heavy armor: you get back fewer Hit Dice, and Exhaustion "
        "doesn't go down.",
        XANATHAR,
    ),
    OptionalRule(
        "xge-knots",
        "Tying knots",
        "The check you make to tie a knot sets how hard it is to escape.",
        XANATHAR,
        ("2014",),  # the 2024 rules' rope already covers knots
    ),
    OptionalRule(
        "xge-tools",
        "Tools in detail",
        "Each tool has its own special uses and can help with related skill checks.",
        XANATHAR,
        ("2014",),  # the 2024 rules have their own tool rules
    ),
    OptionalRule(
        "xge-identify-spell",
        "Spotting a spell",
        "Use your reaction and an Arcana check to recognize a spell as it's cast.",
        XANATHAR,
        ("2014",),  # the 2024 rules treat studying a spell differently; check before widening
    ),
    OptionalRule(
        "xge-falling-rate",
        "How fast you fall",
        "A long fall takes time: you drop up to 500 feet each round.",
        XANATHAR,
        ("2014",),  # the 2024 falling rule already includes it
    ),
    OptionalRule(
        "tce-custom-origin",
        "Customizing your origin",
        "Move your race's ability score bonuses and swap some of its proficiencies.",
        TASHA,
        ("2014",),  # the 2024 rules tie ability scores to backgrounds instead
    ),
    OptionalRule(
        "tce-class-features",
        "Extra class features",
        "Extra or replacement features for each class, chosen as you level up.",
        TASHA,
        ("2014",),  # the 2024 classes were rewritten
    ),
    OptionalRule(
        "tce-group-patron",
        "Group patrons",
        "The party works for one patron who gives them jobs, perks and contacts.",
        TASHA,
    ),
    OptionalRule(
        "tce-parley",
        "Talking with monsters",
        "What each kind of monster wants, so the party can talk or trade instead of fight.",
        TASHA,
    ),
)

_BY_ID = {rule.id: rule for rule in CATALOG}


def rule(rule_id: str) -> OptionalRule | None:
    return _BY_ID.get(rule_id)


def applying(target_ruleset: str) -> list[OptionalRule]:
    """The rules that can add to a campaign's main rules, in catalog order."""
    return [r for r in CATALOG if target_ruleset in r.applies_to]


def is_on(rule_id: str, overrides: Mapping[str, bool], default: bool) -> bool:
    """A rule the DM switched keeps that choice; any other follows the campaign default."""
    return overrides.get(rule_id, default)
