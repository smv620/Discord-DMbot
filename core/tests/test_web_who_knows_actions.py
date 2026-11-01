"""The website's "Who knows this?" actions (#2999, #2935 D2): `link-who` and `alias-who` queued by
the DM's role with the answer in the action's text, checked by the door (a DM, a live connection
or other name of this campaign, only confirmed characters of it) before the bot ever sees them.
Invented names."""

from __future__ import annotations

import unittest
from typing import Any

from dmbot.memory.models import DM as DM_SOURCE
from dmbot.web import all_names, relationships
from dmbot.web.recaps import RecapError
from dmbot.web.who_knows import who_text
from tests.test_web_relationships import Web


class TheText(unittest.TestCase):
    def test_the_three_answers_become_one_string(self) -> None:
        a, b = "a" * 32, "b" * 32
        self.assertEqual(who_text({"who": "dm"}), "dm")
        self.assertEqual(who_text({"who": "everyone", "characters": []}), "everyone")
        self.assertEqual(who_text({"who": "some", "characters": [a, b]}), f"some {a} {b}")

    def test_a_bad_answer_is_refused_before_the_door(self) -> None:
        a = "a" * 32
        bodies: list[dict[str, Any]] = [
            {"who": "some"},
            {"who": "some", "characters": []},
            {"who": "some", "characters": [a, a]},
            {"who": "some", "characters": ["nope"]},
            {"who": "some", "characters": [a] * 51},
            {"who": "dm", "characters": [a]},
            {"who": "party"},
            {},
        ]
        for body in bodies:
            with self.subTest(body=body), self.assertRaises(RecapError):
                who_text(body)


class TheDoor(Web):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.npc = await self.add("Tran", "character", role="npc")

    async def rows(self) -> list[dict[str, Any]]:
        async with self.db.guild(self.g) as conn:
            cur = await conn.execute(
                "SELECT action, entity_id, target_id, text, flag FROM name_actions"
                " WHERE campaign_id = %s ORDER BY created_at, id",
                (self.one.id,),
            )
            return list(await cur.fetchall())

    async def link_who(self, body: dict[str, Any], who: Any = None, link: str | None = None) -> str:
        return await relationships.queue_edit(
            self.web, who or self.session, self.cref, link or self.links["mira-rook"],
            {"action": "who", **body},
        )  # fmt: skip

    async def refused_who(self, body: dict[str, Any], **kw: Any) -> str:
        with self.assertRaises(RecapError) as caught:
            await self.link_who(body, **kw)
        return caught.exception.code

    async def test_a_dm_queues_each_answer_for_a_connection(self) -> None:
        await self.link_who({"who": "dm"})
        await self.link_who({"who": "everyone"})
        await self.link_who({"who": "some", "characters": [self.rook, self.npc]})
        got = await self.rows()  # (queued in the same second, so the order is not asserted)
        self.assertEqual(
            sorted((r["action"], r["target_id"], r["text"], r["flag"]) for r in got),
            sorted(
                [
                    ("link-who", self.links["mira-rook"], "dm", False),
                    ("link-who", self.links["mira-rook"], "everyone", False),
                    ("link-who", self.links["mira-rook"], f"some {self.rook} {self.npc}", False),
                ]
            ),
        )

    async def test_a_player_and_a_stranger_cannot_queue_one(self) -> None:
        self.assertEqual(await self.refused_who({"who": "dm"}, who=self.player), "not_allowed")
        self.assertEqual(await self.rows(), [])

    async def test_only_confirmed_characters_of_this_campaign_may_know(self) -> None:
        other = await self.campaigns.create(self.g, "Other", self.session.user_id)
        stray = await self.memory.add_entity(  # a character of another campaign
            self.g, other.id, type="character", name="Stray", role="npc", source=DM_SOURCE,
            status="confirmed",
        )  # fmt: skip
        for ids in (
            [self.keep],  # a place is not a character
            [stray.value.id],  # another campaign's
            ["0" * 32],  # nothing at all
        ):
            with self.subTest(ids=ids):
                self.assertEqual(
                    await self.refused_who({"who": "some", "characters": ids}), "bad_target"
                )
        self.assertEqual(await self.rows(), [])

    async def test_a_connection_that_is_not_there_is_not_found(self) -> None:
        self.assertEqual(await self.refused_who({"who": "dm"}, link="0" * 32), "not_found")

    async def test_an_other_name_is_asked_of_the_name_it_belongs_to(self) -> None:
        made = await self.memory.add_alias(
            self.g, self.one.id, self.keep, "the old fort", kind="nickname",
            source=DM_SOURCE, status="confirmed", secret=True,
        )  # fmt: skip
        alias = made.value.id
        body = {"action": "alias-who", "alias": alias, "who": "some", "characters": [self.npc]}
        await all_names.queue_action(self.web, self.session, self.cref, self.keep, body)
        got = await self.rows()
        self.assertEqual(
            [(r["action"], r["entity_id"], r["target_id"], r["text"]) for r in got],
            [("alias-who", self.keep, alias, f"some {self.npc}")],
        )
        with self.assertRaises(RecapError) as wrong_name:  # filed under a name it isn't one of
            await all_names.queue_action(self.web, self.session, self.cref, self.mira, body)
        self.assertEqual(wrong_name.exception.code, "not_found")
        with self.assertRaises(RecapError) as player:
            await all_names.queue_action(self.web, self.player, self.cref, self.keep, body)
        self.assertEqual(player.exception.code, "not_allowed")
        self.assertEqual(len(await self.rows()), 1)

    async def test_an_unconfirmed_character_and_a_stranger_do_not_get_through_for_a_name(
        self,
    ) -> None:
        maybe = await self.memory.add_entity(  # proposed, not confirmed
            self.g, self.one.id, type="character", name="Maybe", role="npc", source=DM_SOURCE
        )
        self.assertEqual(
            await self.refused_who({"who": "some", "characters": [maybe.value.id]}), "bad_target"
        )
        made = await self.memory.add_alias(
            self.g, self.one.id, self.keep, "the old fort", kind="nickname",
            source=DM_SOURCE, status="confirmed", secret=True,
        )  # fmt: skip
        body = {"action": "alias-who", "alias": made.value.id, "who": "some"}
        with self.assertRaises(RecapError) as bad:  # a character the campaign does not have
            await all_names.queue_action(
                self.web, self.session, self.cref, self.keep, {**body, "characters": ["f" * 32]}
            )
        self.assertEqual(bad.exception.code, "bad_target")
        self.assertEqual(await self.rows(), [])

    async def test_fifty_characters_fit_the_table(self) -> None:
        """The longest answer the door allows (about 1,650 characters) must also be storable."""
        ids = []
        for n in range(50):
            ids.append(await self.add(f"Extra {n:02d}", "character", role="npc"))
        await self.link_who({"who": "some", "characters": ids})
        got = await self.rows()
        self.assertEqual(len(got), 1)
        self.assertEqual(len(got[0]["text"]), 5 + 50 * 33 - 1)
