"""The website's "Which one?" choices come from the store's own function and tags (#3463). Pure:
the facts a database door sends go in, and the choices Discord's card offers come out. Invented
names throughout."""

from __future__ import annotations

import unittest

from dmbot.memory.claims import which_choices
from dmbot.web import which_choices as web


def entity(ident: str, name: str, **over: object) -> dict[str, object]:
    return {
        "id": ident, "type": "character", "name": name, "description": "", "status": "confirmed",
        "source": "dm", "unnamed": False, "unnamed_anchor": None, "unnamed_role": None,
        "unnamed_what": None, **over,
    }  # fmt: skip


def info(
    entities: list[dict[str, object]],
    aliases: list[dict[str, object]] | None = None,
    ties: list[dict[str, object]] | None = None,
    **claim: object,
) -> dict[str, object]:
    return {
        "claims": [{"id": "c1", "subject": "Rook", "object": "Mira",
                    "subject_picked": None, "object_picked": None, **claim}],
        "entities": entities, "aliases": aliases or [], "ties": ties or [],
    }  # fmt: skip


class TheChoices(unittest.TestCase):
    def test_two_entries_with_one_name_are_offered_with_their_notes(self) -> None:
        facts = info([
            entity("a" * 32, "Mira", description="the ferrywoman"),
            entity("b" * 32, "Mira", description="a smuggler's daughter"),
        ])  # fmt: skip
        got = web.choices_by_claim(facts)
        (one,) = got["c1"]
        self.assertEqual((one["side"], one["words"]), ("object", "Mira"))
        self.assertEqual(
            sorted((e["name"], e["note"]) for e in one["entries"]),
            [("Mira", "a smuggler's daughter"), ("Mira", "the ferrywoman")],
        )

    def test_it_is_what_the_store_function_gives(self) -> None:
        facts = info([entity("a" * 32, "Mira", description="x"), entity("b" * 32, "Mira")])
        claims, entities, aliases = facts["claims"], facts["entities"], facts["aliases"]
        direct = which_choices(claims[0], entities, aliases)  # type: ignore[arg-type,index]
        site = web.choices_by_claim(facts)["c1"]
        self.assertEqual(
            [(p.side, p.words, [o.id for o in p.entries]) for p in direct],
            [(c["side"], c["words"], [e["id"] for e in c["entries"]]) for c in site],
        )

    def test_a_secret_name_never_tells_two_entries_apart(self) -> None:
        facts = info(
            [entity("a" * 32, "Mira"), entity("b" * 32, "Mira")],
            [{"entity_id": "a" * 32, "text": "Hidden Name", "key": "hiddenname", "kind": "short",
              "source": "dm", "secret": True}],
        )  # fmt: skip
        notes = [e["note"] for c in web.choices_by_claim(facts)["c1"] for e in c["entries"]]
        self.assertNotIn("Hidden Name", " ".join(notes))

    def test_a_name_with_one_entry_or_already_picked_has_no_choice(self) -> None:
        one = info([entity("a" * 32, "Mira")])
        self.assertEqual(web.choices_by_claim(one), {})
        two = [entity("a" * 32, "Mira"), entity("b" * 32, "Mira")]
        self.assertEqual(web.choices_by_claim(info(two, object_picked="a" * 32)), {})

    def test_an_entry_that_needs_a_tag_gets_its_place_tie(self) -> None:
        woman = entity("a" * 32, "the woman")
        other = entity("b" * 32, "the woman")
        place = entity("c" * 32, "Termalaine", type="place")
        facts = info(
            [woman, other, place],
            ties=[{"subject_id": "a" * 32, "object_id": "c" * 32, "predicate": "located_in",
                   "secret": False, "detail": ""}],
            object="the woman",
        )  # fmt: skip
        tagged = {
            e["id"]: e["tag"] for c in web.choices_by_claim(facts)["c1"] for e in c["entries"]
        }
        self.assertEqual(tagged["a" * 32], "the woman (in Termalaine)")
        self.assertEqual(tagged["b" * 32], "the woman")  # (nothing tells it apart)

    def test_a_proper_name_is_its_own_tag(self) -> None:
        facts = info([entity("a" * 32, "Mira"), entity("b" * 32, "Mira")])
        tags = [e["tag"] for c in web.choices_by_claim(facts)["c1"] for e in c["entries"]]
        self.assertEqual(tags, ["Mira", "Mira"])


if __name__ == "__main__":
    unittest.main()


def test_the_door_migration_copies_the_stores_reason_word_for_word() -> None:
    import importlib

    from dmbot.memory.claims import UNPLACED_REASON

    for name in ("0342_which_info", "0343_claim_pick_actions"):
        module = importlib.import_module(f"dmbot.migrations.{name}")
        assert module.UNPLACED_REASON == UNPLACED_REASON


def test_the_widened_action_list_keeps_every_action_the_last_one_allowed() -> None:
    """0343 rewrites the whole CHECK list; dropping an action (0315's confirm-kinds and
    confirm-undo, in the first draft) would break its queue rows."""
    import importlib
    import re

    def actions(sql: str) -> set[str]:
        block = sql.split("name_actions_action_check CHECK", 1)[1].split("));", 1)[0]
        return set(re.findall(r"'([a-z-]+)'", block))

    old = importlib.import_module("dmbot.migrations.0315_confirm_kinds_actions")
    new = importlib.import_module("dmbot.migrations.0343_claim_pick_actions")
    assert actions(old.SQL) <= actions(new.SQL)
    assert {"claim-pick", "claim-unpick"} <= actions(new.SQL)
