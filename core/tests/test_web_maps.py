"""The Maps page's doors (#4347, migration 0487): the read door that cuts by the player rule itself,
and the five queue actions the bot applies through the map store, each one Undo. Invented names.
Needs Postgres."""

from __future__ import annotations

from typing import Any

from dmbot.maps import store as map_store
from dmbot.maps.store import MapStore
from dmbot.memory import web_actions as wa
from dmbot.pictures.store import MB, PictureStore
from dmbot.web import maps as web_maps
from dmbot.web.recaps import RecapError, _scoped
from tests.test_recap_web import BOB
from tests.test_web_api import ALICE, THURSDAY
from tests.test_web_name_actions import Acting


class MapsDoor(Acting):
    """Alice is the DM of campaign One; Bob is a player there (and the DM of its sibling "Two")."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.pictures = PictureStore(self.db, clock=lambda: 1000.0)
        self.maps = MapStore(self.db, self.memory, clock=lambda: 2000.0)
        self.player = await self.signed(BOB, THURSDAY)
        self.n = 0
        # a DM's own map (usable), a player's map still waiting, one shown to the table, one hidden
        self.dm_map = await self.make_map(ALICE.id, "The Keep", dm=True)
        self.bobs = await self.make_map(BOB.id, "Bob's sketch", dm=False)
        self.shown = await self.make_map(ALICE.id, "Icewind Dale", dm=True)
        await self.maps.set_audience(self.g, self.one.id, self.shown, "table")

    async def make_map(self, user: int, title: str, *, dm: bool) -> str:
        self.n += 1
        added = await self.pictures.add(
            self.g, self.one.id, poster_id=user, session_id="", line_id=None, message_id=self.n,
            description="", consent_version=7, right_granted_at=900, sha256=f"{self.n:064x}",
            object_key=f"{self.g}/{self.one.id}/{self.n:064x}.png", preview_key="",
            size_bytes=MB, preview_bytes=0, storage_mb=10,
        )  # fmt: skip
        assert added.picture is not None
        made = await self.maps.add_map(
            self.g, self.one.id, picture_id=added.picture.id, uploader_id=user,
            uploader_is_dm=dm, right_to_use=True, kind="region", came_from="website",
            storage_mb=10, title=title,
        )  # fmt: skip
        return made.value.id

    async def door(self, who: Any = None) -> Any:
        session = who or self.session
        async with self.web.user(session.user_id, session=session.id_hash) as conn:
            await _scoped(conn, self.g)
            cur = await conn.execute("SELECT dmbot_web_maps(%s, %s) AS r", (self.g, self.one.id))
            found = await cur.fetchone()
        return found["r"] if found else None

    async def add_action(self, action: str, text: str, who: Any = None) -> Any:
        session = who or self.session
        async with self.web.user(session.user_id, session=session.id_hash) as conn:
            await _scoped(conn, self.g)
            cur = await conn.execute(
                "SELECT dmbot_web_name_action_add(%s, %s, %s, %s, %s, %s, %s, %s) AS r",
                (self.g, self.one.id, None, action, "", "", text, False),
            )
            found = await cur.fetchone()
        return found["r"] if found else None

    async def act(self, action: str, text: str, user: int = ALICE.id) -> wa.Outcome:
        got = await self.add_action(action, text)
        self.assertEqual(got["status"], "ok", got)
        claimed = await self.actions.claim_next(self.g)
        assert claimed is not None
        outcome = await wa.apply(
            self.memory, self.g, {ALICE.id}, claimed, actions=self.actions, maps=self.maps
        )
        await self.actions.finish(
            self.g, claimed, error=outcome.error, result_batches=outcome.batches
        )
        return outcome

    async def state_of(self, map_id: str) -> Any:
        return await self.maps.get(self.g, self.one.id, map_id, map_store.Viewer(dm=True))


class TheReadDoor(MapsDoor):
    async def test_a_dm_gets_every_map_with_its_state_and_whether_it_is_shown(self) -> None:
        got = await self.door()
        self.assertTrue(got["isDm"])
        by_id = {m["id"]: m for m in got["maps"]}
        self.assertEqual(set(by_id), {self.dm_map, self.bobs, self.shown})
        self.assertEqual(
            (by_id[self.dm_map]["state"], by_id[self.dm_map]["shown"]), ("usable", False)
        )
        self.assertEqual((by_id[self.bobs]["state"], by_id[self.bobs]["shown"]), ("waiting", False))
        self.assertEqual((by_id[self.shown]["state"], by_id[self.shown]["shown"]), ("usable", True))

    async def test_a_player_gets_the_shown_usable_maps_and_their_own_and_nothing_else(self) -> None:
        got = await self.door(self.player)
        self.assertFalse(got["isDm"])
        by_id = {m["id"]: m for m in got["maps"]}
        # the shown one (not theirs) and their own waiting one; never the DM's hidden map
        self.assertEqual(set(by_id), {self.shown, self.bobs})
        self.assertNotIn("state", by_id[self.shown])  # (a player is not told another map's state)
        self.assertNotIn("shown", by_id[self.shown])
        self.assertEqual((by_id[self.bobs]["state"], by_id[self.bobs]["mine"]), ("waiting", True))

    async def test_a_hidden_map_never_reaches_a_player_in_any_form(self) -> None:
        got = await self.door(self.player)
        text = repr(got)
        self.assertNotIn(self.dm_map, text)
        self.assertNotIn("The Keep", text)

    async def test_a_map_a_dm_said_no_to_or_one_still_waiting_is_not_shown_to_others(self) -> None:
        other = await self.make_map(ALICE.id, "Old road", dm=False)  # (waiting, not Bob's)
        await self.maps.set_audience(self.g, self.one.id, other, "table")
        ids = {m["id"] for m in (await self.door(self.player))["maps"]}
        self.assertNotIn(other, ids)  # shown but not yet usable: still hidden
        await self.maps.answer_use(self.g, self.one.id, other, dm_id=ALICE.id, yes=False)
        ids = {m["id"] for m in (await self.door(self.player))["maps"]}
        self.assertNotIn(other, ids)  # a no: never used, so never shown

    async def test_a_stranger_to_the_server_gets_nothing(self) -> None:
        async with self.web.user(999, session="x" * 64) as conn:
            cur = await conn.execute("SELECT dmbot_web_maps(%s, %s) AS r", (self.g, self.one.id))
            found = await cur.fetchone()
        self.assertTrue(found is None or found["r"] is None)

    async def test_the_web_module_shapes_the_door(self) -> None:
        got = await web_maps.read(self.web, self.session, self.cref)
        self.assertTrue(got["canManage"])
        first = got["maps"][0]
        self.assertTrue(first["openUrl"].startswith("/pictures/"))
        self.assertEqual(first["via"], "website")
        with self.assertRaises(RecapError):
            await web_maps.read(self.web, self.session, f"999999.{self.one.id}")


class TheActions(MapsDoor):
    async def test_a_dms_yes_makes_the_map_usable_and_undo_puts_it_back(self) -> None:
        done = await self.act("maps-use", f"{self.bobs} yes")
        self.assertEqual(done.error, "")
        self.assertTrue(map_store.may_use_map(await self.state_of(self.bobs)))
        (batch,) = done.batches
        undone = await self.act("maps-undo", str(batch))
        self.assertEqual(undone.error, "")
        self.assertIsNone((await self.state_of(self.bobs)).use_ok)

    async def test_a_second_answer_changes_nothing_the_first_stands(self) -> None:
        await self.act("maps-use", f"{self.bobs} no")
        again = await self.act("maps-use", f"{self.bobs} yes")
        self.assertEqual((again.error, again.batches), ("", ()))
        self.assertIs((await self.state_of(self.bobs)).use_ok, False)

    async def test_show_and_hide_and_undo(self) -> None:
        done = await self.act("maps-show", f"{self.dm_map} shown")
        self.assertEqual((await self.state_of(self.dm_map)).audience, "table")
        await self.act("maps-show", f"{self.dm_map} hidden")
        self.assertEqual((await self.state_of(self.dm_map)).audience, "dm")
        (batch,) = done.batches
        await self.act("maps-undo", str(batch))
        self.assertEqual((await self.state_of(self.dm_map)).audience, "dm")

    async def test_remove_takes_only_the_map_and_undo_brings_it_back(self) -> None:
        done = await self.act("maps-remove", self.dm_map)
        self.assertIsNone(await self.state_of(self.dm_map))
        (batch,) = done.batches
        await self.act("maps-undo", str(batch))
        self.assertIsNotNone(await self.state_of(self.dm_map))

    async def test_a_gone_map_is_answered_gone(self) -> None:
        await self.act("maps-remove", self.dm_map)
        again = await self.act("maps-show", f"{self.dm_map} shown")
        self.assertEqual(again.error, wa.GONE)

    async def test_undo_takes_back_only_a_change_the_sender_made(self) -> None:
        done = await self.act("maps-show", f"{self.dm_map} shown")
        (batch,) = done.batches
        # a made-up number, and a real one that no Maps press of this person made
        self.assertEqual((await self.act("maps-undo", "99999")).error, wa.GONE)
        other = await self.maps.set_audience(self.g, self.one.id, self.bobs, "table")
        assert other.batch is not None
        self.assertEqual((await self.act("maps-undo", str(other.batch))).error, wa.GONE)
        self.assertEqual((await self.act("maps-undo", str(batch))).error, "")

    async def test_the_door_refuses_a_bad_shape_and_a_non_dm_cannot_queue(self) -> None:
        self.assertEqual((await self.add_action("maps-use", "nope"))["status"], "bad_text")
        self.assertEqual(
            (await self.add_action("maps-show", f"{self.dm_map} maybe"))["status"], "bad_text"
        )
        self.assertEqual((await self.add_action("maps-undo", "-1"))["status"], "bad_text")
        # (the queue door is for a campaign's DMs only today; a player's Take it back comes
        # with the upload slice)
        self.assertIsNone(await self.add_action("maps-remove", self.dm_map, who=self.player))

    async def test_the_bot_refuses_a_press_from_someone_who_is_not_a_dm(self) -> None:
        got = await self.add_action("maps-remove", self.dm_map)
        self.assertEqual(got["status"], "ok")
        claimed = await self.actions.claim_next(self.g)
        assert claimed is not None
        outcome = await wa.apply(
            self.memory, self.g, {BOB.id}, claimed, actions=self.actions, maps=self.maps
        )  # (the sender, Alice, is not in this set)
        self.assertEqual(outcome.error, wa.NOT_ALLOWED)
        self.assertIsNotNone(await self.state_of(self.dm_map))

    async def test_the_poster_takes_their_own_map_back_and_nobody_else_can(self) -> None:
        got = await self.add_action("maps-takeback", self.bobs)  # (Alice queues it: a DM)
        self.assertEqual(got["status"], "ok")
        claimed = await self.actions.claim_next(self.g)
        assert claimed is not None
        refused = await wa.apply(
            self.memory, self.g, {ALICE.id}, claimed, actions=self.actions, maps=self.maps
        )
        self.assertEqual(refused.error, wa.NOT_ALLOWED)  # (Alice did not post it)
        self.assertIsNotNone(await self.state_of(self.bobs))
