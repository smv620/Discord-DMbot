"""A DM's edits to the relationship web (#2323): queued by the website's role, applied by the bot
through the same memory calls as the Discord name card (so the change log and Undo agree),
re-checked when applied, and read back only by the DM who made them. Invented names throughout."""

from __future__ import annotations

from typing import Any

import httpx

from dmbot.memory import web_actions as wa
from dmbot.memory.models import DM as DM_SOURCE
from dmbot.memory.models import MemoryRuleError
from dmbot.web import relationships, sessions
from dmbot.web.app import create_app
from dmbot.web.discord import DiscordGuild
from dmbot.web.payments import FakeProvider
from dmbot.web.recaps import RecapError
from tests.test_recap_web import BOB, HEADERS, NOW, callers
from tests.test_web_api import ALICE, API, THURSDAY, FakeDiscord, settings
from tests.test_web_relationships import Web


class Editing(Web):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.actions = wa.ActionStore(self.db)

    async def apply_all(self, dms: Any = None) -> list[wa.Outcome]:
        done = []
        while (action := await self.actions.claim_next(self.g)) is not None:
            outcome = await wa.apply(
                self.memory, self.g, {ALICE.id} if dms is None else dms, action
            )
            await self.actions.finish(
                self.g, action, error=outcome.error, result_id=outcome.result_id,
                result_other=outcome.result_other,
            )  # fmt: skip
            done.append(outcome)
        return done

    async def poll(self, answer: str, who: Any = None) -> dict[str, Any]:
        return await relationships.read_answer(self.web, who or self.session, self.cref, answer)

    async def add_link(self, a: str, b: str, words: str, *, secret: bool = False) -> str:
        return await relationships.queue_add(
            self.web, self.session, self.cref, {"a": a, "b": b, "words": words, "secret": secret}
        )

    async def edit(self, link: str, **body: Any) -> str:
        return await relationships.queue_edit(self.web, self.session, self.cref, link, body)

    async def refused_edit(self, who: Any = None, **kw: Any) -> str:
        with self.assertRaises(RecapError) as caught:
            await relationships.queue_edit(
                self.web, who or self.session, self.cref, kw.pop("link"), kw
            )
        return caught.exception.code

    def edge_of(self, polled: dict[str, Any], a: str, b: str) -> dict[str, Any]:
        edge = polled["edge"]
        self.assertEqual({edge["a"], edge["b"]}, {a, b})
        return dict(edge)

    async def log_since(self, version: int) -> list[Any]:
        async with self.db.guild(self.g) as conn:
            cur = await conn.execute(
                "SELECT version, batch, source FROM memory_changes"
                " WHERE campaign_id = %s AND version > %s ORDER BY version",
                (self.one.id, version),
            )
            return list(await cur.fetchall())


class TheEdits(Editing):
    async def test_adding_a_connection_by_known_words_and_by_free_ones(self) -> None:
        before = await self.memory.version(self.g, self.one.id)
        known = await self.add_link(self.mira, self.hall, "Is a Member of")  # any case
        self.assertEqual((await self.poll(known))["state"], "waiting")
        self.assertEqual([o.error for o in await self.apply_all()], [""])
        edge = self.edge_of(await self.poll(known), self.mira, self.hall)
        self.assertEqual(edge["label"], "is a member of")
        free = await self.add_link(self.mira, self.hall, "sworn on the ashes of", secret=True)
        await self.apply_all()
        edge = self.edge_of(await self.poll(free), self.mira, self.hall)
        self.assertEqual(edge["more"], 1)
        words = {x["words"]: x for x in edge["links"]}
        self.assertTrue(words["sworn on the ashes of"]["secret"])
        self.assertFalse(words["is a member of"]["secret"])
        self.assertEqual(edge["label"], "is a member of")  # never the secret one
        log = await self.log_since(before)
        self.assertTrue(log and all(r["source"] == "dm" for r in log))

    async def test_changing_the_words_replaces_the_connection_and_undo_takes_it_back(self) -> None:
        link = self.links["mira-keep"]
        before = await self.memory.version(self.g, self.one.id)
        answer = await self.edit(link, action="words", words="is an enemy of")
        await self.apply_all()
        edge = self.edge_of(await self.poll(answer), self.mira, self.keep)
        self.assertEqual([x["words"] for x in edge["links"]], ["is an enemy of"])
        self.assertNotIn(link, [x["id"] for x in edge["links"]])  # the old one was ended
        log = await self.log_since(before)
        await self.memory.undo(self.g, self.one.id, log[-1]["batch"], source=DM_SOURCE)
        again = await relationships.read_web(self.web, self.session, self.cref, center=self.mira)
        pair = {
            x["words"]
            for e in again["edges"]
            for x in e["links"]
            if {e["a"], e["b"]} == {self.mira, self.keep}
        }
        self.assertEqual(pair, {"knows"})  # ONE Undo puts the old words back and ends the new ones
        self.assertEqual(len({r["batch"] for r in log}), 1)  # one change: one batch, one Undo

    async def test_the_cards_undo_takes_a_reworded_connection_back_whole(self) -> None:
        link = self.links["mira-keep"]
        before = await self.memory.version(self.g, self.one.id)
        await self.edit(link, action="words", words="is an enemy of")
        await self.apply_all()
        (batch,) = {r["batch"] for r in await self.log_since(before)}
        back = await self.memory.undo_connection(
            self.g, self.one.id, batch, within=600, secrets=True
        )
        self.assertEqual((back.value.id, back.value.predicate), (link, "knows"))
        mine = await self.memory.relations(self.g, self.one.id, entity_id=self.mira)
        self.assertEqual(
            sorted(r.predicate for r in mine if self.keep in (r.subject_id, r.object_id)),
            ["knows"],
        )  # the new words went too: no second connection is left
        with self.assertRaises(MemoryRuleError):  # a second press finds it changed
            await self.memory.undo_connection(self.g, self.one.id, batch, within=600, secrets=True)

    async def free_link(self, words: str) -> str:
        made = await self.add_link(self.mira, self.hall, words)
        await self.apply_all()
        edge = self.edge_of(await self.poll(made), self.mira, self.hall)
        return str(next(x["id"] for x in edge["links"] if x["words"] == words))

    async def test_changing_only_the_capitals_keeps_the_connection(self) -> None:
        link = await self.free_link("sworn to")
        answer = await self.edit(link, action="words", words="Sworn To")
        await self.apply_all()
        done = await self.poll(answer)
        self.assertEqual((done["state"], done["removed"]), ("done", False))
        edge = self.edge_of(done, self.mira, self.hall)
        self.assertEqual([x["id"] for x in edge["links"]], [link])  # still there, untouched

    async def test_new_words_that_already_exist_merge_and_a_secret_stays_secret(self) -> None:
        secret = await self.free_link("sworn to")
        await self.apply_all()
        public = await self.add_link(self.mira, self.hall, "owes")
        await self.apply_all()
        self.assertTrue(public)
        answer = await self.edit(secret, action="secret", secret=True)
        await self.apply_all()
        self.assertTrue(answer)
        answer = await self.edit(
            await self.link_id(self.mira, self.hall, "owes"), action="words", words="sworn to"
        )
        await self.apply_all()
        edge = self.edge_of(await self.poll(answer), self.mira, self.hall)
        self.assertEqual([(x["words"], x["secret"]) for x in edge["links"]], [("sworn to", True)])

    async def link_id(self, a: str, b: str, words: str) -> str:
        web = await relationships.read_web(self.web, self.session, self.cref, center=a)
        return str(
            next(
                x["id"]
                for e in web["edges"]
                if {e["a"], e["b"]} == {a, b}
                for x in e["links"]
                if x["words"] == words
            )
        )

    async def test_a_retried_job_is_done_not_missing(self) -> None:
        gone = self.links["rook-keep"]
        await self.edit(gone, action="remove", confirm=True)
        action = await self.actions.claim_next(self.g)
        assert action is not None
        first = await wa.apply(self.memory, self.g, {ALICE.id}, action)
        self.assertEqual(first.error, "")
        await self.actions.finish(self.g, action, error="", result_id=first.result_id)
        again = await wa.apply(self.memory, self.g, {ALICE.id}, action)  # the first try died here
        self.assertEqual(again.error, "")
        self.assertEqual(
            (again.result_id, again.result_other), (first.result_id, first.result_other)
        )
        link = await self.add_link(self.mira, self.hall, "sworn to")
        await self.apply_all()
        self.assertTrue(link)
        old = await self.link_id(self.mira, self.hall, "sworn to")
        await self.edit(old, action="words", words="owes")
        action = await self.actions.claim_next(self.g)
        assert action is not None
        self.assertEqual((await wa.apply(self.memory, self.g, {ALICE.id}, action)).error, "")
        await self.actions.finish(self.g, action, error="")
        self.assertEqual((await wa.apply(self.memory, self.g, {ALICE.id}, action)).error, "")
        # the door refuses a new edit to a connection that has ended
        self.assertEqual(
            await self.refused_edit(link=old, action="secret", secret=True), "not_found"
        )

    async def test_the_same_words_change_nothing(self) -> None:
        before = await self.memory.version(self.g, self.one.id)
        answer = await self.edit(self.links["mira-keep"], action="words", words=" KNOWS ")
        await self.apply_all()
        done = await self.poll(answer)
        self.assertEqual(done["state"], "done")
        self.assertEqual(await self.memory.version(self.g, self.one.id), before)

    async def test_a_dm_pins_and_unpins_a_connection_and_a_retry_is_done(self) -> None:
        link = await self.free_link("sworn to")
        pinned = await self.edit(link, action="pin", pinned=True)
        self.assertEqual([o.error for o in await self.apply_all()], [""])
        self.assertEqual((await self.poll(pinned))["state"], "done")
        self.assertEqual(await self.memory.pinned_connections(self.g, self.one.id), {link})
        again = await self.edit(link, action="pin", pinned=True)  # (a retried job: already so)
        self.assertEqual([o.error for o in await self.apply_all()], [""])
        self.assertEqual((await self.poll(again))["state"], "done")
        off = await self.edit(link, action="pin", pinned=False)
        self.assertEqual([o.error for o in await self.apply_all()], [""])
        self.assertEqual((await self.poll(off))["state"], "done")
        self.assertEqual(await self.memory.pinned_connections(self.g, self.one.id), set())

    async def test_only_a_dm_can_queue_a_pin_and_it_needs_a_yes_or_no(self) -> None:
        link = await self.free_link("sworn to")
        self.assertEqual(await self.refused_edit(link=link, action="pin"), "bad_action")
        self.assertEqual(
            await self.refused_edit(link=link, action="pin", pinned="yes"), "bad_action"
        )
        self.assertEqual(
            await self.refused_edit(self.player, link=link, action="pin", pinned=True),
            "not_allowed",
        )

    async def test_a_stranger_cannot_queue_a_pin_nor_read_its_answer(self) -> None:
        link = await self.free_link("sworn to")
        token = await sessions.sign_in(
            self.web, ALICE, [DiscordGuild(id=999, name="Elsewhere", manage=True)], now=NOW, days=30
        )
        stranger = await sessions.find(self.web, token, now=NOW)  # in another server only
        self.assertIn(
            await self.refused_edit(stranger, link=link, action="pin", pinned=True),
            ("not_found", "not_allowed"),
        )
        queued = await self.edit(link, action="pin", pinned=True)
        with self.assertRaises(RecapError):
            await self.poll(queued, stranger)
        await self.apply_all()
        self.assertEqual(await self.memory.pinned_connections(self.g, self.one.id), {link})

    async def test_secret_and_known_switch_one_connection(self) -> None:
        link = self.links["rook-keep"]
        secret = await self.edit(link, action="secret", secret=True)
        await self.apply_all()
        edge = self.edge_of(await self.poll(secret), self.rook, self.keep)
        self.assertTrue(edge["links"][0]["secret"])
        player = await relationships.read_web(
            self.web, self.player, self.cref, center=self.rook, hops=1
        )
        self.assertNotIn(link, [x["id"] for e in player["edges"] for x in e["links"]])
        known = await self.edit(link, action="secret", secret=False)
        await self.apply_all()
        self.assertFalse(
            self.edge_of(await self.poll(known), self.rook, self.keep)["links"][0]["secret"]
        )

    async def test_removing_needs_the_confirmation_and_the_last_one_removes_the_edge(self) -> None:
        link = self.links["keep-hall"]
        self.assertEqual(await self.refused_edit(link=link, action="remove"), "needs_confirm")
        self.assertEqual(
            await self.refused_edit(link=link, action="remove", confirm="yes"), "needs_confirm"
        )
        answer = await self.edit(link, action="remove", confirm=True)
        await self.apply_all()
        done = await self.poll(answer)
        self.assertEqual((done["state"], done["edge"], done["removed"]), ("done", None, True))
        web = await relationships.read_web(
            self.web, self.session, self.cref, center=self.keep, hops=1
        )
        self.assertNotIn(self.hall, [n["id"] for n in web["nodes"]])

    async def test_removing_one_of_two_leaves_the_edge(self) -> None:
        extra = await self.link("enemy_of", self.mira, self.keep, "extra")
        answer = await self.edit(extra, action="remove", confirm=True)
        await self.apply_all()
        done = await self.poll(answer)
        self.assertFalse(done["removed"])
        self.assertEqual(done["edge"]["more"], 0)


class Refusals(Editing):
    async def test_only_a_dm_may_edit_and_the_bot_checks_again(self) -> None:
        for call in (
            relationships.queue_add(
                self.web, self.player, self.cref,
                {"a": self.mira, "b": self.hall, "words": "knows", "secret": False},
            ),
            relationships.queue_edit(
                self.web, self.player, self.cref, self.links["mira-keep"],
                {"action": "remove", "confirm": True},
            ),
        ):  # fmt: skip
            with self.assertRaises(RecapError) as caught:
                await call
            self.assertEqual(caught.exception.code, "not_allowed")
        queued = await self.edit(self.links["mira-keep"], action="secret", secret=True)
        done = await self.apply_all(dms={BOB.id})  # the sender stopped being a DM in between
        self.assertEqual([o.error for o in done], ["not_allowed"])
        self.assertEqual((await self.poll(queued))["error"], "not_allowed")

    async def test_the_words_the_names_and_the_link_are_checked(self) -> None:
        bad = {"a": self.mira, "b": self.hall, "secret": False}
        for body, code in (
            ({**bad, "words": ""}, "bad_name"),
            ({**bad, "words": "x" * 101}, "too_long"),
            ({**bad, "words": "knows\x00"}, "bad_name"),
            ({**bad, "words": "knows", "b": self.mira}, "bad_target"),
            ({**bad, "words": "knows", "b": "f" * 32}, "bad_target"),
            ({**bad, "words": "knows", "a": "f" * 32}, "not_found"),
            ({**bad, "words": "knows", "a": "nope"}, "not_found"),
            ({**bad, "words": "knows", "secret": "no"}, "bad_action"),
        ):
            with self.subTest(code=code), self.assertRaises(RecapError) as caught:
                await relationships.queue_add(self.web, self.session, self.cref, body)
            self.assertEqual(caught.exception.code, code)
        with self.assertRaises(RecapError) as caught:
            await relationships.queue_add(self.web, self.session, self.cref, ["x"])
        self.assertEqual(caught.exception.code, "bad_action")
        self.assertEqual(
            await self.refused_edit(link="f" * 32, action="remove", confirm=True), "not_found"
        )
        self.assertEqual(
            await self.refused_edit(link="nope", action="remove", confirm=True), "not_found"
        )
        self.assertEqual(
            await self.refused_edit(link=self.links["mira-keep"], action="burn"), "bad_action"
        )
        self.assertEqual(
            await self.refused_edit(link=self.links["mira-keep"], action="secret"), "bad_action"
        )

    async def test_the_door_itself_asks_for_the_confirmation_and_checks_the_link(self) -> None:
        async def door(*args: Any) -> Any:
            async with self.web.user(ALICE.id, session=self.session.id_hash) as conn:
                await conn.execute("SELECT set_config('dmbot.guild_id', %s, true)", (str(self.g),))
                cur = await conn.execute(
                    "SELECT dmbot_web_name_action_add(%s, %s, %s, %s, %s, %s, %s, %s) AS r",
                    (self.g, self.one.id, *args),
                )
                return (await cur.fetchone())["r"]  # type: ignore[index]

        link = self.links["mira-keep"]
        sure = await door(None, "link-remove", "", link, "", False)
        self.assertEqual(sure["status"], "needs_confirm")  # straight to the door: still refused
        gone = await door(None, "link-remove", "", "f" * 32, "", True)
        self.assertEqual(gone["status"], "not_found")
        pair = await door(self.mira, "link-add", "", self.mira, "knows", False)
        self.assertEqual(pair["status"], "bad_target")  # not with itself
        junk = await door(None, "link-words", "", link, "knows", True)
        self.assertEqual(junk["status"], "bad_text")  # (a words change has no switch)

    async def test_a_connection_gone_by_the_time_the_bot_gets_to_it_is_not_found(self) -> None:
        link = self.links["rook-keep"]
        queued = await self.edit(link, action="secret", secret=True)
        await self.memory.update_relation(
            self.g, self.one.id, link, source=DM_SOURCE, status="rejected"
        )
        await self.apply_all()
        self.assertEqual((await self.poll(queued))["error"], "not_found")

    async def test_an_answer_is_only_the_makers_and_only_a_link_edits(self) -> None:
        answer = await self.edit(self.links["mira-keep"], action="secret", secret=True)
        with self.assertRaises(RecapError):
            await self.poll(answer, who=self.player)
        with self.assertRaises(RecapError):
            await self.poll("nope")
        # an answer for a name action (the All names page) is not a link answer
        from dmbot.web import all_names

        name_answer = await all_names.queue_action(
            self.web, self.session, self.cref, self.mira, {"action": "hide", "hidden": False}
        )
        with self.assertRaises(RecapError):
            await self.poll(name_answer)

    def test_only_the_relationship_and_name_code_calls_the_door(self) -> None:
        self.assertEqual(
            callers("dmbot_web_name_action_add("),
            {"schema.py", "web/all_names.py", "web/relationships.py"},
        )


class TheReveal(Editing):
    """The DM's 👁️ Reveal to players (#2405): the same calls as Discord's, so the change log and
    Undo agree, and only a DM of this campaign can ask."""

    async def hidden_link(self) -> str:
        made = await self.memory.add_relation(
            self.g, self.one.id, self.rook, "ally_of", self.hall, source=DM_SOURCE,
            confidence=1.0, status="confirmed", unrevealed=True,
        )  # fmt: skip
        return made.value[0].id

    async def reveal(self, kind: str, target: str, who: Any = None) -> str:
        return await relationships.queue_reveal(
            self.web, who or self.session, self.cref, {"kind": kind, "id": target}
        )

    async def refused_reveal(self, kind: str, target: str, who: Any = None) -> str:
        with self.assertRaises(RecapError) as caught:
            await self.reveal(kind, target, who)
        return caught.exception.code

    async def test_a_dm_reveals_a_connection_and_undo_hides_it_again(self) -> None:
        link = await self.hidden_link()
        before = await self.memory.version(self.g, self.one.id)
        answer = await self.reveal("link", link)
        self.assertEqual([o.error for o in await self.apply_all()], [""])
        self.assertEqual((await self.poll(answer))["state"], "done")
        shown = await self.memory.relation(self.g, self.one.id, link)
        assert shown is not None
        self.assertFalse(shown.unrevealed)
        log = await self.log_since(before)
        self.assertTrue(log and all(r["source"] == "dm" for r in log))
        await self.memory.undo(self.g, self.one.id, log[-1]["batch"], source=DM_SOURCE)
        hidden = await self.memory.relation(self.g, self.one.id, link)
        assert hidden is not None
        self.assertTrue(hidden.unrevealed)  # ONE Undo, as on the Discord card

    async def test_a_dm_reveals_a_name_and_undo_hides_it_again(self) -> None:
        name = await self.add("Hidden Tower", "place")
        await self.memory.set_hidden_from_players(self.g, self.one.id, name, True, source=DM_SOURCE)
        before = await self.memory.version(self.g, self.one.id)
        answer = await self.reveal("name", name)
        self.assertEqual([o.error for o in await self.apply_all()], [""])
        from dmbot.web import all_names

        done = await all_names.read_action(self.web, self.session, self.cref, answer)
        self.assertEqual(done["state"], "done")
        shown = await self.memory.entity(self.g, self.one.id, name)
        assert shown is not None
        self.assertFalse(shown.hidden_from_players)
        log = await self.log_since(before)
        await self.memory.undo(self.g, self.one.id, log[-1]["batch"], source=DM_SOURCE)
        again = await self.memory.entity(self.g, self.one.id, name)
        assert again is not None
        self.assertTrue(again.hidden_from_players)

    async def test_a_player_is_refused_and_nothing_is_queued(self) -> None:
        link = await self.hidden_link()
        name = await self.add("Hidden Tower", "place")
        self.assertEqual(await self.refused_reveal("link", link, self.player), "not_allowed")
        self.assertEqual(await self.refused_reveal("name", name, self.player), "not_allowed")
        self.assertEqual(await self.apply_all(), [])  # (so nothing reached the bot)

    async def test_another_campaigns_id_is_not_found_not_forbidden(self) -> None:
        elsewhere = await self.memory.add_entity(
            self.g, self.two.id, type="npc", name="Brynwater", source=DM_SOURCE, status="confirmed"
        )
        self.assertEqual(await self.refused_reveal("name", elsewhere.value.id), "not_found")
        self.assertEqual(await self.apply_all(), [])
        # (applied: not found)
        self.assertEqual(await self.refused_reveal("link", "f" * 32), "not_found")

    async def test_the_body_is_checked(self) -> None:
        with self.assertRaises(RecapError) as caught:
            await relationships.queue_reveal(self.web, self.session, self.cref, "nope")
        self.assertEqual(caught.exception.code, "bad_action")
        self.assertEqual(await self.refused_reveal("planet", self.mira), "not_found")
        self.assertEqual(await self.refused_reveal("name", "not an id"), "not_found")


class TheRoutes(Editing):
    async def test_the_routes_queue_and_answer(self) -> None:
        from dmbot.web import sessions

        app = create_app(
            settings(), self.web, FakeDiscord(), payments=FakeProvider(b"h" * 32, "https://x"),
            clock=lambda: NOW,
        )  # fmt: skip
        token = await sessions.sign_in(
            self.web,
            ALICE,
            [THURSDAY],
            now=NOW,
            days=30,
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=API, headers=HEADERS
        ) as client:
            client.cookies.set("__Host-dmbot_session", token, domain="api.dmbot.example")
            posted = await client.post(
                f"/me/campaigns/{self.cref}/web/links",
                json={"a": self.mira, "b": self.hall, "words": "knows", "secret": False},
            )
            self.assertEqual(posted.status_code, 202)
            await self.apply_all()
            answer = await client.get(
                f"/me/campaigns/{self.cref}/web/answers/{posted.json()['answer']}"
            )
            self.assertEqual(answer.json()["state"], "done")
            self.assertEqual(answer.json()["edge"]["label"], "knows")
            edit = await client.post(
                f"/me/campaigns/{self.cref}/web/links/{self.links['mira-keep']}",
                json={"action": "remove"},
            )
            self.assertEqual((edit.status_code, edit.json()["error"]), (400, "needs_confirm"))
            name = await self.add("Hidden Tower", "place")
            await self.memory.set_hidden_from_players(
                self.g, self.one.id, name, True, source=DM_SOURCE
            )
            revealed = await client.post(
                f"/me/campaigns/{self.cref}/web/reveal", json={"kind": "name", "id": name}
            )
            self.assertEqual(revealed.status_code, 202)
            self.assertTrue(revealed.json()["answer"])
            nope = await client.post(
                f"/me/campaigns/{self.cref}/web/reveal", json={"kind": "name", "id": "g" * 32}
            )
            self.assertEqual((nope.status_code, nope.json()["error"]), (404, "not_found"))
            junk = await client.post(f"/me/campaigns/{self.cref}/web/links", content=b"not json")
            self.assertEqual((junk.status_code, junk.json()["error"]), (400, "bad_action"))
