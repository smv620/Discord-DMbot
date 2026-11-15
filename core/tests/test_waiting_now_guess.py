"""The website's connection card (#4432, migration 0499): each waiting connection carries `now` (the
connection the campaign holds between the same two entries, when its words differ) and `guess`
(DMbot's reason for an inferred one); inferred connections are listed; "let players see it" has its
own queue action. Real Postgres; invented names."""

from __future__ import annotations

from typing import Any

from dmbot.memory import waiting_now
from dmbot.memory import web_actions as wa
from dmbot.memory.backstory_keep import changes_among as keep_changes
from dmbot.memory.backstory_keep import keep_all
from dmbot.memory.backstory_links import BackstoryLinks, NewLink
from dmbot.web import relationships
from dmbot.web.recaps import RecapError
from tests.test_web_relationships import Web


class NowAndGuess(Web):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.backstory = BackstoryLinks(self.db)

    async def update(self, statement: str, *args: Any) -> None:
        async with self.db.guild(self.g) as conn:
            await conn.execute(statement, args)

    async def got(self) -> dict[str, Any]:
        return await relationships.read_waiting(
            self.web, self.session, self.cref, outside_engine=None
        )

    async def rows(self) -> list[dict[str, Any]]:
        # the bot writes what a keep would replace on each row (#4441); the door returns it
        await waiting_now.refresh(self.memory, self.backstory, self.g, self.one.id)
        return list((await self.got())["backstory"])

    async def waiting(self, predicate: str = "knows", detail: str = "", **kw: Any) -> None:
        await self.backstory.add(
            self.g, self.one.id,
            [NewLink(self.mira, predicate, self.keep, "Mira knows the Keep", detail=detail, **kw)],
        )  # fmt: skip


class TheNowLine(NowAndGuess):
    async def test_a_held_connection_of_the_same_kind_with_other_words_is_now(self) -> None:
        await self.waiting(detail="friendly")  # (Mira already knows the Keep, plainly)
        (row,) = await self.rows()
        self.assertEqual(row["now"], {"how": "knows", "hidden": False})
        self.assertNotIn("guess", row)

    async def test_a_hidden_held_connection_says_so(self) -> None:
        await self.update(
            "UPDATE memory_relations SET secret = TRUE WHERE guild_id = %s AND id = %s",
            self.g, self.links["mira-keep"],
        )  # fmt: skip
        await self.waiting(detail="friendly")
        (row,) = await self.rows()
        self.assertTrue(row["now"]["hidden"])

    async def test_an_assumed_held_connection_is_hidden_too(self) -> None:
        await self.update(
            "UPDATE memory_relations SET assumed = TRUE WHERE guild_id = %s AND id = %s",
            self.g, self.links["mira-keep"],
        )  # fmt: skip
        await self.waiting(detail="friendly")
        (row,) = await self.rows()
        self.assertTrue(row["now"]["hidden"])

    async def test_the_website_says_what_discords_card_says_for_a_twin_the_other_way_round(
        self,
    ) -> None:
        # (#4441) the bot's rule decides, not a same-kind match in the door: whatever the card on
        # Discord would end, the website says
        await self.link("knows", self.mira, self.keep, "twin")
        await self.waiting(detail="friendly")
        await self.link("knows", self.keep, self.mira, "twin-friendly")
        (row,) = await self.rows()
        waiting = await self.backstory.waiting(self.g, self.one.id)
        ends = await keep_changes(self.memory, self.g, self.one.id, waiting)
        discord = {
            r.predicate for found in ends.values() for r in found if r.subject_id == self.mira
        }
        self.assertEqual("now" in row, bool(discord))

    async def test_a_connection_of_another_kind_that_can_sit_beside_it_has_no_now(self) -> None:
        await self.waiting(predicate="works_for")
        (row,) = await self.rows()
        self.assertNotIn("now", row)

    async def test_an_alliance_an_enemy_would_end_is_now_though_the_kind_differs(self) -> None:
        # (#4441) the ontology decides, as for Discord's card: an ally against an enemy
        await self.link("ally_of", self.mira, self.keep, "alliance")
        await self.waiting(predicate="enemy_of")
        (row,) = await self.rows()
        self.assertEqual(row["now"]["how"], "is a friend or ally of")
        self.assertFalse(row["now"]["hidden"])

    async def test_what_a_press_ends_is_what_the_card_said(self) -> None:
        await self.link("ally_of", self.mira, self.keep, "alliance")
        await self.waiting(predicate="enemy_of")
        (row,) = await self.rows()
        self.assertEqual(row["now"]["how"], "is a friend or ally of")
        done = await keep_all(
            self.memory, self.backstory, self.g, self.one.id,
            await self.backstory.waiting(self.g, self.one.id), one_press=False,
        )  # fmt: skip
        self.assertEqual(len(done.kept), 1)
        ended = [
            r for r in await self.memory.relations(self.g, self.one.id, include_secret=True)
            if r.predicate == "ally_of" and {r.subject_id, r.object_id} == {self.mira, self.keep}
        ]  # fmt: skip
        self.assertTrue(ended and all(r.to_session_at is not None for r in ended))  # (as said)

    async def test_a_changed_held_connection_is_read_again_on_the_next_look(self) -> None:
        await self.link("ally_of", self.mira, self.keep, "alliance")
        await self.waiting(predicate="enemy_of")
        (row,) = await self.rows()
        self.assertEqual(row["now"]["how"], "is a friend or ally of")
        await self.update(
            "UPDATE memory_relations SET to_session_at = 5 WHERE guild_id = %s AND id = %s",
            self.g, self.links["alliance"],
        )  # fmt: skip
        (again,) = await self.rows()
        self.assertNotIn("now", again)

    async def test_the_same_words_are_already_known_and_not_asked(self) -> None:
        await self.waiting(detail="")  # (the campaign holds exactly this): cleaned away, no card
        self.assertEqual(await self.rows(), [])

    async def test_an_ended_connection_is_not_now(self) -> None:
        await self.update(
            "UPDATE memory_relations SET to_session_at = 5 WHERE guild_id = %s AND id = %s",
            self.g, self.links["mira-keep"],
        )  # fmt: skip
        await self.waiting(detail="friendly")
        (row,) = await self.rows()
        self.assertNotIn("now", row)

    async def test_now_carries_no_name_and_no_quote(self) -> None:
        await self.waiting(detail="friendly")
        (row,) = await self.rows()
        text = repr(row["now"])
        for word in ("Mira", "Keep", "quote"):
            self.assertNotIn(word, text)

    async def test_someone_who_is_not_a_dm_gets_nothing(self) -> None:
        await self.waiting(detail="friendly")
        with self.assertRaises(RecapError) as caught:
            await relationships.read_waiting(self.web, self.player, self.cref, outside_engine=None)
        self.assertEqual(caught.exception.code, "not_found")


class TheGuess(NowAndGuess):
    async def test_an_inferred_connection_is_listed_counted_and_gives_its_reason(self) -> None:
        await self.waiting(inferred="offered him food to ease his hunger")
        got = await self.got()
        (row,) = got["backstory"]
        self.assertEqual(row["guess"], "offered him food to ease his hunger")
        self.assertGreaterEqual(got["counts"]["backstory"], 1)

    async def test_a_stated_connection_has_no_guess(self) -> None:
        await self.waiting(detail="friendly")
        (row,) = await self.rows()
        self.assertNotIn("guess", row)


class TheKeepShowAction(NowAndGuess):
    async def queued(self, **body: Any) -> Any:
        await self.waiting(detail="friendly")
        (row,) = await self.rows()
        await relationships.queue_waiting(
            self.web,
            self.session,
            self.cref,
            {"kind": "backstory", "action": "keep", "id": row["id"], **body},
        )
        return await wa.ActionStore(self.db).claim_next(self.g)

    async def test_secret_false_queues_the_show_action_with_no_text_and_no_flag(self) -> None:
        action = await self.queued(secret=False)
        self.assertEqual(
            (action.action, action.text, action.flag), ("backstory-keep-show", "", False)
        )

    async def test_no_secret_is_a_plain_keep(self) -> None:
        action = await self.queued()
        self.assertEqual((action.action, action.flag), ("backstory-keep", False))

    async def test_secret_true_is_a_keep_as_secret(self) -> None:
        action = await self.queued(secret=True)
        self.assertEqual((action.action, action.flag), ("backstory-keep", True))

    async def test_a_player_cannot_queue_it(self) -> None:
        await self.waiting(detail="friendly")
        (row,) = await self.rows()
        with self.assertRaises(RecapError) as caught:
            await relationships.queue_waiting(
                self.web, self.player, self.cref,
                {"kind": "backstory", "action": "keep", "id": row["id"], "secret": False},
            )  # fmt: skip
        self.assertEqual(caught.exception.code, "not_allowed")

    async def test_a_made_up_id_is_not_found(self) -> None:
        with self.assertRaises(RecapError) as caught:
            await relationships.queue_waiting(
                self.web, self.session, self.cref,
                {"kind": "backstory", "action": "keep", "id": "0" * 32, "secret": False},
            )  # fmt: skip
        self.assertEqual(caught.exception.code, "not_found")
