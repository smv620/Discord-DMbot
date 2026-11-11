"""The website's "Who knows this?" actions (#2999, #2935 D2): `link-who` and `alias-who` queued by
the DM's role with the answer in the action's text, checked by the door (a DM, a live connection
or other name of this campaign, only confirmed characters of it) before the bot ever sees them.
Invented names."""

from __future__ import annotations

import unittest
from typing import Any

from dmbot.memory import link_edits
from dmbot.memory.models import DM as DM_SOURCE
from dmbot.web import all_names, relationships
from dmbot.web.recaps import RecapError
from dmbot.web.who_knows import who_text
from tests.test_web_link_actions import Editing
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


class TheBotAppliesIt(Editing):
    """#4159: the bot never applied either answer (both ended `failed`). It now makes the name
    card's own calls, with the website's whole list (NPCs included), and returns every change it
    made so the page's Undo takes all of them back."""

    async def knowers(self, link: str) -> list[str]:
        return (await self.memory.connection_knowers(self.g, self.one.id, [link])).get(link, [])

    async def test_a_connection_some_everyone_and_only_the_dm(self) -> None:
        link = self.links["mira-rook"]
        await self.edit(link, action="who", who="some", characters=[self.rook])
        done = await self.apply_all()
        self.assertEqual([o.error for o in done], [""])
        self.assertTrue((await self.memory.relation(self.g, self.one.id, link)).secret)
        self.assertEqual(await self.knowers(link), [self.rook])
        self.assertEqual(len(done[0].batches), 2)  # hidden, then who knows it: both, for Undo
        await self.edit(link, action="who", who="dm")
        await self.apply_all()
        self.assertEqual(await self.knowers(link), [])
        self.assertTrue((await self.memory.relation(self.g, self.one.id, link)).secret)
        await self.edit(link, action="who", who="everyone")
        await self.apply_all()
        self.assertFalse((await self.memory.relation(self.g, self.one.id, link)).secret)

    async def test_the_same_answer_again_writes_nothing(self) -> None:
        link = self.links["mira-rook"]
        await self.edit(link, action="who", who="some", characters=[self.rook])
        await self.apply_all()
        before = await self.memory.version(self.g, self.one.id)
        await self.edit(link, action="who", who="some", characters=[self.rook])
        again = await self.apply_all()
        self.assertEqual([(o.error, o.batches) for o in again], [("", ())])
        self.assertEqual(await self.memory.version(self.g, self.one.id), before)

    async def test_undo_takes_it_back(self) -> None:
        link = self.links["mira-rook"]
        await self.edit(link, action="who", who="dm")
        (done,) = await self.apply_all()
        self.assertTrue((await self.memory.relation(self.g, self.one.id, link)).secret)
        await self.memory.undo(self.g, self.one.id, done.batches[0])
        self.assertFalse((await self.memory.relation(self.g, self.one.id, link)).secret)

    async def test_the_websites_pick_is_the_whole_list(self) -> None:
        link = self.links["mira-rook"]
        npc = await self.add("Tran", "character", role="npc")
        await self.edit(link, action="who", who="some", characters=[self.rook, npc])
        await self.apply_all()
        self.assertEqual(await self.gate_rows(link), 2)  # an NPC knower lives in the gate's table
        await self.edit(link, action="who", who="some", characters=[self.rook])
        await self.apply_all()
        self.assertEqual(await self.gate_rows(link), 1)  # the NPC the DM left out stops knowing
        self.assertEqual(await self.knowers(link), [self.rook])

    async def gate_rows(self, link: str) -> int:
        async with self.db.guild(self.g) as conn:
            cur = await conn.execute(
                "SELECT count(*) AS n FROM memory_knowers WHERE campaign_id = %s"
                " AND relation_id = %s",
                (self.one.id, link),
            )
            row = await cur.fetchone()
        assert row is not None
        return int(row["n"])

    async def test_a_secret_other_name(self) -> None:
        made = await self.memory.add_alias(
            self.g, self.one.id, self.keep, "the old fort", kind="nickname",
            source=DM_SOURCE, status="confirmed", secret=True,
        )  # fmt: skip
        alias = made.value.id
        body = {"action": "alias-who", "alias": alias, "who": "some", "characters": [self.rook]}
        await all_names.queue_action(self.web, self.session, self.cref, self.keep, body)
        done = await self.apply_all()
        self.assertEqual([o.error for o in done], [""])
        knew = await self.memory.alias_knowers(self.g, self.one.id, [alias])
        self.assertEqual(knew.get(alias, []), [self.rook])
        body = {"action": "alias-who", "alias": alias, "who": "everyone"}
        await all_names.queue_action(self.web, self.session, self.cref, self.keep, body)
        self.assertEqual([o.error for o in await self.apply_all()], [""])
        names = await self.memory.aliases(
            self.g, self.one.id, entity_id=self.keep, include_secret=True
        )
        self.assertFalse(next(a for a in names if a.id == alias).secret)

    async def test_a_non_dm_sender_is_refused(self) -> None:
        await self.edit(self.links["mira-rook"], action="who", who="dm")
        self.assertEqual([o.error for o in await self.apply_all(dms={999})], ["not_allowed"])

    async def test_the_pages_undo_takes_back_both_changes(self) -> None:
        link = self.links["mira-rook"]
        self.assertFalse((await self.memory.relation(self.g, self.one.id, link)).secret)
        answer = await self.edit(link, action="who", who="some", characters=[self.rook])
        (done,) = await self.apply_all()
        self.assertEqual(len(done.batches), 2)  # hidden, then who knows it
        batch = (await self.poll(answer))["batch"]
        undo = await relationships.queue_link_undo(self.web, self.session, self.cref, str(batch))
        self.assertEqual([o.error for o in await self.apply_all()], [""])
        self.assertTrue((await self.poll(undo))["undone"])
        self.assertFalse((await self.memory.relation(self.g, self.one.id, link)).secret)
        self.assertEqual(await self.knowers(link), [])

    async def test_only_the_dm_alone_has_a_change_to_undo(self) -> None:
        link = await self.memory.relation(self.g, self.one.id, self.links["mira-rook"])
        written = await link_edits.set_who_knows(self.memory, self.g, self.one.id, link, "dm")
        self.assertIsNotNone(written.batch)  # (the name card's Undo has the hiding to take back)

    async def secret_name(self, *, secret: bool = True) -> str:
        made = await self.memory.add_alias(
            self.g, self.one.id, self.keep, "the old fort", kind="nickname",
            source=DM_SOURCE, status="confirmed", secret=secret,
        )  # fmt: skip
        return made.value.id

    async def alias_who(self, alias: str, **body: Any) -> None:
        await all_names.queue_action(
            self.web, self.session, self.cref, self.keep,
            {"action": "alias-who", "alias": alias, **body},
        )  # fmt: skip

    async def test_only_the_dm_on_a_shown_name_hides_it_and_undo_shows_it(self) -> None:
        alias = await self.secret_name(secret=False)
        await self.alias_who(alias, who="dm")
        (done,) = await self.apply_all()
        self.assertEqual((done.error, len(done.batches)), ("", 1))
        names = await self.memory.aliases(
            self.g, self.one.id, entity_id=self.keep, include_secret=True
        )
        self.assertTrue(next(a for a in names if a.id == alias).secret)
        await self.memory.undo(self.g, self.one.id, done.batches[0])
        names = await self.memory.aliases(
            self.g, self.one.id, entity_id=self.keep, include_secret=True
        )
        self.assertFalse(next(a for a in names if a.id == alias).secret)

    async def test_a_names_same_answer_again_writes_nothing(self) -> None:
        alias = await self.secret_name()
        await self.alias_who(alias, who="some", characters=[self.rook])
        await self.apply_all()
        before = await self.memory.version(self.g, self.one.id)
        await self.alias_who(alias, who="some", characters=[self.rook])
        self.assertEqual([(o.error, o.batches) for o in await self.apply_all()], [("", ())])
        self.assertEqual(await self.memory.version(self.g, self.one.id), before)

    async def test_a_name_from_a_non_dm_sender_is_refused(self) -> None:
        alias = await self.secret_name()
        await self.alias_who(alias, who="dm")
        self.assertEqual([o.error for o in await self.apply_all(dms={999})], ["not_allowed"])
