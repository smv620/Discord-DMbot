"""Splitting a list of names and saying it is a group, on a recap's names to check (#3406, #3383).

The pure part (the hints rule, the checks before the database) runs everywhere. The database part
needs Postgres: the door takes the four new answers, the page's hints equal the store's for a
campaign with a secret spelling, and the page reads the bot's last word on a name. Invented names.
"""

from __future__ import annotations

import unittest
from typing import Any

from dmbot.memory.models import CONFIRMED, DM
from dmbot.web import name_checks as web
from dmbot.web import name_groups
from dmbot.web.recaps import RecapError
from tests.test_web_name_checks import GUILD, WithChecks


def info(
    aliases: dict[str, str], members: list[tuple[str, str]], groups: dict[str, str]
) -> dict[str, Any]:
    return {
        "aliases": [{"key": k, "entity": e} for k, e in aliases.items()],
        "members": [{"subject": s, "group": g} for s, g in members],
        "groups": [{"id": i, "name": n} for i, n in groups.items()],
    }


class TheHintsRule(unittest.TestCase):
    """`name_groups.hints_for`: the store's rule, over the facts the door gives."""

    def test_a_list_is_found_only_for_two_or_more_parts(self) -> None:
        self.assertEqual(name_groups.parts_of("Brannoch and Veshla"), ["Brannoch", "Veshla"])
        self.assertEqual(name_groups.parts_of("Aldmoor, Quillgate and Fenwick")[0], "Aldmoor")
        self.assertEqual(name_groups.parts_of("Aldmoor"), [])

    def test_every_part_or_all_but_one_in_the_group_gives_a_hint(self) -> None:
        facts = info({"brannoch": "e1", "veshla": "e2"}, [("e1", "g1")], {"g1": "The Ashen Circle"})
        (hint,) = name_groups.hints_for(["Brannoch", "Veshla"], facts)
        self.assertEqual(
            (hint.group_id, hint.group_name, hint.members, hint.others),
            ("g1", "The Ashen Circle", ("Brannoch",), ("Veshla",)),
        )

    def test_fewer_than_all_but_one_gives_none(self) -> None:
        facts = info({"a": "e1", "b": "e2", "c": "e3"}, [("e1", "g1")], {"g1": "The Trio"})
        self.assertEqual(name_groups.hints_for(["A", "B", "C"], facts), [])

    def test_the_best_fit_comes_first_then_by_name_and_at_most_three(self) -> None:
        facts = info(
            {"a": "e1", "b": "e2"},
            [("e1", "g1"), ("e2", "g1"), ("e1", "g2"), ("e1", "g3"), ("e1", "g4"), ("e1", "g5")],
            {"g1": "Zed", "g2": "Beta", "g3": "alpha", "g4": "Delta", "g5": "Gamma"},
        )
        got = name_groups.hints_for(["A", "B"], facts)
        self.assertEqual([h.group_name for h in got], ["Zed", "alpha", "Beta"])

    def test_a_part_the_campaign_does_not_know_is_not_a_member(self) -> None:
        facts = info({"brannoch": "e1"}, [("e1", "g1")], {"g1": "The Ashen Circle"})
        (hint,) = name_groups.hints_for(["Brannoch", "Veshla"], facts)
        self.assertEqual(hint.others, ("Veshla",))

    def test_no_facts_no_hints(self) -> None:
        self.assertEqual(name_groups.hints_for(["A", "B"], {}), [])
        self.assertEqual(name_groups.hints_for(["A"], info({}, [], {})), [])


class TheChecksBeforeTheDatabase(unittest.IsolatedAsyncioTestCase):
    """What `answer` refuses without asking the database (so none is needed)."""

    async def refused(self, **body: Any) -> str:
        with self.assertRaises(RecapError) as err:
            await web.answer(None, None, "r", "a" * 32, body)  # type: ignore[arg-type]
        return err.exception.code

    async def test_join_group_needs_a_group_id(self) -> None:
        self.assertEqual(await self.refused(action="join_group"), "bad_target")
        self.assertEqual(await self.refused(action="join_group", group="nope"), "bad_target")

    async def test_make_group_needs_a_name_of_at_most_120(self) -> None:
        self.assertEqual(await self.refused(action="make_group"), "bad_text")
        self.assertEqual(await self.refused(action="make_group", name="  "), "bad_text")
        self.assertEqual(await self.refused(action="make_group", name="x" * 121), "bad_text")
        self.assertEqual(await self.refused(action="make_group", name="bad\x07"), "bad_text")

    async def test_undo_needs_a_batch_number(self) -> None:
        for batch in (None, "7", 0, -1, True, 10**18, 1.5):
            self.assertEqual(await self.refused(action="undo", batch=batch), "bad_text", batch)

    async def test_an_unknown_action_is_still_refused(self) -> None:
        self.assertEqual(await self.refused(action="explode"), "bad_action")


class TheDatabaseHalf(WithChecks):
    async def sql(self, text: str, *args: Any) -> None:
        async with self.db.guild(GUILD) as conn:
            await conn.execute(text, args)

    async def group(
        self, name: str, *members: str, secret: bool = False, status: str = CONFIRMED
    ) -> str:
        made = await self.memory.add_entity(
            GUILD, self.one.id, type="faction", name=name, source=DM, status=CONFIRMED
        )
        for member in members:
            await self.memory.add_relation(
                GUILD, self.one.id, member, "member_of", made.value.id,
                source=DM, confidence=1.0, status=status, secret=secret,
            )  # fmt: skip
        return made.value.id

    async def person(self, name: str) -> str:
        return await self.known(name, "character")

    async def test_a_group_they_left_is_not_carried(self) -> None:
        # (#4134: the door's members read current memberships only)
        brannoch = await self.person("Brannoch")
        await self.person("Veshla")
        await self.group("The Ashen Circle", brannoch)
        await self.sql("UPDATE memory_relations SET to_session_at = 5")
        listed = await self.suggest("Brannoch and Veshla")
        cards = {c["id"]: c for c in (await self.listed())["names"]}
        self.assertFalse(cards[listed].get("groups"))

    async def test_a_list_name_carries_its_groups_and_other_names_carry_none(self) -> None:
        brannoch, veshla = await self.person("Brannoch"), await self.person("Veshla")
        duo = await self.group("The Ashen Circle", brannoch)
        listed = await self.suggest("Brannoch and Veshla")
        plain = await self.suggest("Gotham")
        cards = {c["id"]: c for c in (await self.listed())["names"]}
        self.assertTrue(cards[listed]["isList"])
        self.assertEqual(
            cards[listed]["groups"],
            [
                {
                    "id": duo,
                    "name": "The Ashen Circle",
                    "members": ["Brannoch"],
                    "others": ["Veshla"],
                }
            ],
        )
        self.assertNotIn("isList", cards[plain])
        self.assertNotIn("groups", cards[plain])
        self.assertTrue(veshla)

    async def test_the_page_offers_what_the_store_offers_with_a_secret_spelling(self) -> None:
        """Parity (Supervisor, #3406): the store and the page give the same hints."""
        brannoch, veshla = await self.person("Brannoch"), await self.person("Veshla")
        await self.memory.add_alias(
            GUILD, self.one.id, veshla, "The Boy Wonder", kind="nickname", source=DM, secret=True
        )
        await self.group("The Ashen Circle", brannoch, veshla)
        await self.group("A Secret Club", brannoch, secret=True)  # a secret membership: no hint
        await self.group("A Guessed Club", brannoch, veshla, status="proposed")  # a guess: no hint
        unnamed = await self.group("Garret's clients", brannoch, veshla)  # an unnamed group
        await self.sql(
            "UPDATE memory_entities SET unnamed = true, unnamed_what = 'a group of clients'"
            " WHERE id = %s",
            unnamed,
        )
        listed = await self.suggest("Brannoch and Veshla")
        store = await self.memory.group_hints(GUILD, self.one.id, listed)
        page = next(c for c in (await self.listed())["names"] if c["id"] == listed)["groups"]
        self.assertEqual(
            page,
            [
                {"id": h.group_id, "name": h.group_name, "members": list(h.members),
                 "others": list(h.others)}
                for h in store
            ],
        )  # fmt: skip
        self.assertEqual([g["name"] for g in page], ["The Ashen Circle"])

    async def test_the_door_takes_the_new_answers_and_refuses_what_it_can_see_is_wrong(
        self,
    ) -> None:
        listed = await self.suggest("Aldmoor, Quillgate and Fenwick")
        duo = await self.group("The Ashen Circle")
        await self.ask(listed, action="split")
        self.assertEqual(await self.apply_state(listed), "waiting")
        await self.sql("DELETE FROM name_answers WHERE entity_id = %s", listed)
        await self.ask(listed, action="join_group", group=duo)
        await self.sql("DELETE FROM name_answers WHERE entity_id = %s", listed)
        await self.ask(listed, action="make_group", name="The Three Towns")
        await self.sql("DELETE FROM name_answers WHERE entity_id = %s", listed)
        not_a_group = await self.known("Gotham", "place")
        self.assertEqual(
            await self.code(self.ask(listed, action="join_group", group=not_a_group)), "bad_target"
        )

    async def apply_state(self, entity: str) -> str:
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                "SELECT state FROM name_answers WHERE guild_id = %s AND entity_id = %s",
                (GUILD, entity),
            )
            row = await cur.fetchone()
        return str(row["state"]) if row else ""

    async def test_a_second_answer_for_a_name_is_busy_through_the_rebuilt_door(self) -> None:
        listed = await self.suggest("Aldmoor, Quillgate and Fenwick")
        await self.ask(listed, action="split")
        self.assertEqual(await self.code(self.ask(listed, action="make_group", name="X")), "busy")
        self.assertEqual(await self.code(self.ask(listed, action="not_a_name")), "busy")

    async def test_undo_needs_a_finished_answer_with_that_batch(self) -> None:
        listed = await self.suggest("Aldmoor, Quillgate and Fenwick")
        self.assertEqual(await self.code(self.ask(listed, action="undo", batch=7)), "not_found")
        await self.sql(
            "INSERT INTO name_answers (guild_id, campaign_id, id, entity_id, user_id, action,"
            " state, batch, note, created_at) VALUES (%s, %s, %s, %s, 1, 'split', 'done', 7,"
            " 'Split into 3 names.', 1000000000000)",
            GUILD,
            self.one.id,
            "b" * 32,
            listed,
        )  # (a finished split, as the bot leaves it)
        # the list was turned down by the split, but its Undo is still asked
        await self.sql(
            "UPDATE memory_entities SET status = 'rejected' WHERE guild_id = %s AND id = %s",
            GUILD,
            listed,
        )
        self.assertEqual(await self.code(self.ask(listed, action="undo", batch=8)), "not_found")
        queued = await self.ask(listed, action="undo", batch=7)
        self.assertIn("answerId", queued)

    async def test_the_page_reads_the_last_answer_to_a_name(self) -> None:
        listed = await self.suggest("Aldmoor, Quillgate and Fenwick")
        self.assertEqual(
            await self.code(web.read_answer(self.web, self.session, self.ref, listed)), "not_found"
        )
        await self.sql(
            "INSERT INTO name_answers (guild_id, campaign_id, id, entity_id, user_id, action,"
            " state, batch, note, created_at) VALUES (%s, %s, %s, %s, 1, 'split', 'done', 7,"
            " 'Split into 3 names.', 1000000000000)",
            GUILD,
            self.one.id,
            "b" * 32,
            listed,
        )
        got = await web.read_answer(self.web, self.session, self.ref, listed)
        self.assertEqual(
            got, {"state": "done", "note": "Split into 3 names.", "error": None, "batch": 7}
        )
