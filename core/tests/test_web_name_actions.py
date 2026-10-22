"""A DM's actions on the website's All names page (#2294): queued by the website's role, applied by
the bot through the same memory paths as the Discord name card (so the change log and Undo agree),
re-checked when applied, in the order made, and read back only by the DM who made them. Invented
names throughout."""

from __future__ import annotations

from typing import Any

import httpx

from dmbot.memory import web_actions as wa
from dmbot.memory.models import DM as DM_SOURCE
from dmbot.web import all_names, sessions
from dmbot.web.app import create_app
from dmbot.web.discord import DiscordGuild
from dmbot.web.payments import FakeProvider
from dmbot.web.recaps import RecapError
from tests.test_recap_web import BOB, HEADERS, NOW, callers
from tests.test_web_all_names import Listing
from tests.test_web_api import ALICE, API, THURSDAY, FakeDiscord, settings


class Acting(Listing):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.actions = wa.ActionStore(self.db)
        self.mira_row = next(n for n in (await self.page())["names"] if n["name"] == "Mira Voss")

    async def queue(self, entity: str, **body: Any) -> str:
        return await all_names.queue_action(self.web, self.session, self.cref, entity, body)

    async def apply_all(self, dms: Any = None) -> list[wa.Outcome]:
        """The bot's loop for the queue: claim, apply, finish, until it is empty."""
        done = []
        while (action := await self.actions.claim_next(self.g)) is not None:
            outcome = await wa.apply(
                self.memory, self.g, {ALICE.id} if dms is None else dms, action
            )
            await self.actions.finish(
                self.g, action, error=outcome.error, result_id=outcome.result_id
            )
            done.append(outcome)
        return done

    async def poll(self, answer: str, who: Any = None) -> dict[str, Any]:
        return await all_names.read_action(self.web, who or self.session, self.cref, answer)

    async def refused_action(self, entity: str, who: Any = None, **body: Any) -> str:
        with self.assertRaises(RecapError) as caught:
            await all_names.queue_action(self.web, who or self.session, self.cref, entity, body)
        return caught.exception.code

    async def log_since(self, version: int) -> list[Any]:
        async with self.db.guild(self.g) as conn:
            cur = await conn.execute(
                "SELECT version, batch, source, table_name FROM memory_changes"
                " WHERE campaign_id = %s AND version > %s ORDER BY version",
                (self.one.id, version),
            )
            return list(await cur.fetchall())


class TheActions(Acting):
    async def test_spell_is_waiting_then_done_with_the_new_row_and_one_logged_change(self) -> None:
        before = await self.memory.version(self.g, self.one.id)
        answer = await self.queue(self.mira, action="spell", name="  Mira   Vossk ")
        self.assertEqual((await self.poll(answer))["state"], "waiting")
        self.assertEqual(await self.memory.version(self.g, self.one.id), before)  # nothing yet
        self.assertEqual([o.error for o in await self.apply_all()], [""])
        done = await self.poll(answer)
        self.assertEqual((done["state"], done["error"], done["removed"]), ("done", "", False))
        self.assertEqual(done["row"]["name"], "Mira Vossk")
        log = await self.log_since(before)
        self.assertTrue(log and all(r["source"] == "dm" for r in log))
        # Undo on the Discord card takes it back, exactly as for a spelling set there.
        await self.memory.undo(self.g, self.one.id, log[-1]["batch"], source=DM_SOURCE)
        names = [n["name"] for n in (await self.page())["names"]]
        self.assertIn("Mira Voss", names)

    async def test_kind_changes_the_kind_and_refuses_a_players_character(self) -> None:
        answer = await self.queue(self.keep, action="kind", kind="faction")
        await self.apply_all()
        self.assertEqual((await self.poll(answer))["row"]["kind"], "faction")
        bad = await self.queue(self.keep, action="kind", kind="player_character")
        await self.apply_all()
        self.assertEqual((await self.poll(bad))["error"], "bad_kind")
        self.assertEqual((await self.poll(bad))["state"], "failed")

    async def test_other_names_are_added_and_removed_and_a_taken_one_is_refused(self) -> None:
        added = await self.queue(self.keep, action="add-other", other="the Old Keep")
        await self.apply_all()
        self.assertEqual((await self.poll(added))["row"]["others"], ["the Old Keep"])
        taken = await self.queue(self.keep, action="add-other", other="Dawnbreaker")
        await self.apply_all()
        self.assertEqual((await self.poll(taken))["error"], "exists")
        gone = await self.queue(self.keep, action="remove-other", other="THE OLD KEEP")
        await self.apply_all()
        self.assertEqual((await self.poll(gone))["row"]["others"], [])
        missing = await self.queue(self.keep, action="remove-other", other="Nothing Here")
        await self.apply_all()
        self.assertEqual((await self.poll(missing))["error"], "not_found")

    async def test_secret_marks_one_other_name(self) -> None:
        other = await self.queue(self.mira, action="secret", other="the Ferrywoman", secret=True)
        await self.apply_all()
        row = (await self.poll(other))["row"]
        self.assertEqual(
            (row["others"], row["secretOthers"]), ([], ["Lady Grey", "the Ferrywoman"])
        )
        back = await self.queue(self.mira, action="secret", other="the Ferrywoman", secret=False)
        await self.apply_all()
        self.assertEqual((await self.poll(back))["row"]["others"], ["the Ferrywoman"])

    async def test_hide_flips_the_whole_entry_and_undo_reverses_it(self) -> None:
        before = await self.memory.version(self.g, self.one.id)
        whole = await self.queue(self.mira, action="hide", hidden=True)
        await self.apply_all()
        self.assertTrue((await self.poll(whole))["row"]["hidden"])
        self.assertNotIn("Mira Voss", str(await self.page(await self.signed(BOB, THURSDAY))))
        await self.memory.undo(
            self.g, self.one.id, (await self.log_since(before))[-1]["batch"], source=DM_SOURCE
        )
        self.assertFalse(
            next(n for n in (await self.page())["names"] if n["id"] == self.mira)["hidden"]
        )
        again = await self.queue(self.mira, action="hide", hidden=True)
        await self.apply_all()
        self.assertTrue((await self.poll(again))["row"]["hidden"])
        shown = await self.queue(self.mira, action="hide", hidden=False)
        await self.apply_all()
        self.assertFalse((await self.poll(shown))["row"]["hidden"])

    async def test_secret_without_an_other_name_is_refused_at_the_post_and_at_the_door(
        self,
    ) -> None:
        for body in ({"action": "secret", "secret": True}, {"action": "secret", "secret": False}):
            self.assertEqual(await self.refused_action(self.mira, **body), "bad_name", body)
        async with self.web.user(ALICE.id, session=self.session.id_hash) as conn:
            await conn.execute("SELECT set_config('dmbot.guild_id', %s, true)", (str(self.g),))
            cur = await conn.execute(
                "SELECT dmbot_web_name_action_add(%s, %s, %s, 'secret', NULL, NULL, '', true) AS r",
                (self.g, self.one.id, self.mira),
            )
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual(row["r"]["status"], "bad_text")  # (the API says bad_name)
        self.assertEqual(await self.apply_all(), [])  # and nothing was queued

    async def test_hide_needs_its_boolean(self) -> None:
        for body in ({"action": "hide"}, {"action": "hide", "hidden": "yes"}):
            self.assertEqual(await self.refused_action(self.mira, **body), "bad_action", body)

    async def test_the_main_name_cannot_be_made_secret(self) -> None:
        answer = await self.queue(self.mira, action="secret", other="Mira Voss", secret=True)
        await self.apply_all()
        self.assertEqual((await self.poll(answer))["error"], "not_found")

    async def test_merge_keeps_the_target_and_returns_its_row_and_undo_splits_them(self) -> None:
        before = await self.memory.version(self.g, self.one.id)
        answer = await self.queue(self.sword, action="merge", into=self.keep)
        await self.apply_all()
        got = await self.poll(answer)
        self.assertEqual(got["row"]["name"], "Greywater Keep")
        self.assertIn("Dawnbreaker", got["row"]["others"])
        self.assertNotIn("Dawnbreaker", [n["name"] for n in (await self.page())["names"]])
        await self.memory.undo(
            self.g, self.one.id, (await self.log_since(before))[-1]["batch"], source=DM_SOURCE
        )
        self.assertIn("Dawnbreaker", [n["name"] for n in (await self.page())["names"]])

    async def test_a_bad_merge_target_is_refused_at_the_door_and_by_the_bot(self) -> None:
        self.assertEqual(
            await self.refused_action(self.sword, action="merge", into=self.sword), "bad_target"
        )
        self.assertEqual(
            await self.refused_action(self.sword, action="merge", into="f" * 32), "bad_target"
        )
        self.assertEqual(
            await self.refused_action(self.sword, action="merge", into="nope"), "bad_target"
        )

    async def test_remove_needs_its_confirmation_and_then_forgets_the_name_with_undo(self) -> None:
        self.assertEqual(await self.refused_action(self.sword, action="remove"), "needs_confirm")
        self.assertEqual(
            await self.refused_action(self.sword, action="remove", confirm="yes"), "needs_confirm"
        )
        before = await self.memory.version(self.g, self.one.id)
        answer = await self.queue(self.sword, action="remove", confirm=True)
        await self.apply_all()
        got = await self.poll(answer)
        self.assertEqual((got["state"], got["removed"], got["row"]), ("done", True, None))
        self.assertNotIn("Dawnbreaker", str(await self.page()))
        await self.memory.undo(
            self.g, self.one.id, (await self.log_since(before))[-1]["batch"], source=DM_SOURCE
        )
        self.assertIn("Dawnbreaker", str(await self.page()))


class TheChecks(Acting):
    async def test_a_spelling_already_another_names_is_refused(self) -> None:
        answer = await self.queue(self.sword, action="spell", name="Greywater Keep")
        await self.apply_all()
        self.assertEqual((await self.poll(answer))["error"], "exists")

    async def test_odd_words_and_actions_are_refused_before_anything_is_queued(self) -> None:
        for body, code in (
            ({"action": "spell", "name": ""}, "bad_name"),
            ({"action": "spell", "name": "bell\x07"}, "bad_name"),
            ({"action": "spell", "name": 5}, "bad_name"),
            ({"action": "spell", "name": "x" * 101}, "too_long"),
            ({"action": "add-other"}, "bad_name"),
            ({"action": "kind", "kind": "Bad Kind!"}, "bad_kind"),
            ({"action": "secret", "secret": True}, "bad_name"),
            ({"action": "fly"}, "bad_action"),
            ({}, "bad_action"),
        ):
            self.assertEqual(await self.refused_action(self.keep, **body), code, body)
        self.assertEqual(await self.apply_all(), [])  # (nothing was queued)

    async def test_a_name_that_is_not_in_this_campaign_is_not_found(self) -> None:
        other = await self.memory.add_entity(
            self.g, self.two.id, type="npc", name="Brynwater", source=DM_SOURCE, status="confirmed"
        )
        for entity in (other.value.id, "0" * 32, "nope"):
            code = await self.refused_action(entity, action="spell", name="Anything")
            self.assertEqual(code, "not_found", entity)

    async def test_a_manager_who_is_not_a_dm_is_not_allowed_and_a_member_is_not_found(self) -> None:
        bob = await self.signed(BOB, THURSDAY)
        self.assertEqual(
            await self.refused_action(self.keep, bob, action="spell", name="X"), "not_allowed"
        )
        outsider = await self.signed(BOB, DiscordGuild(id=999, name="Elsewhere", manage=True))
        self.assertEqual(
            await self.refused_action(self.keep, outsider, action="spell", name="X"), "not_found"
        )

    async def test_only_the_dm_who_made_an_action_can_read_it(self) -> None:
        answer = await self.queue(self.keep, action="spell", name="Greywater Hold")
        bob = await self.signed(BOB, THURSDAY)
        for who, ref, ident in ((bob, self.cref, answer), (self.session, self.cref, "0" * 32)):
            with self.assertRaises(RecapError) as caught:
                await all_names.read_action(self.web, who, ref, ident)
            self.assertEqual(caught.exception.code, "not_found")

    async def test_a_co_dm_cannot_read_another_dms_action(self) -> None:
        await self.campaigns.add_dm(self.g, self.one.id, BOB.id)  # Bob DMs "One" too now
        answer = await self.queue(self.keep, action="spell", name="Greywater Hold")
        bob = await self.signed(BOB, THURSDAY)
        with self.assertRaises(RecapError) as caught:
            await all_names.read_action(self.web, bob, self.cref, answer)
        self.assertEqual(caught.exception.code, "not_found")
        self.assertEqual((await self.poll(answer))["state"], "waiting")  # (its own DM can)

    async def test_the_door_itself_asks_for_the_confirmation_and_checks_the_words(self) -> None:
        async with self.web.user(ALICE.id, session=self.session.id_hash) as conn:
            await conn.execute("SELECT set_config('dmbot.guild_id', %s, true)", (str(self.g),))
            answers = []
            for args in (
                (self.sword, "remove", None, None, None, False),  # no confirmation
                (self.sword, "remove", None, None, None, None),
                (self.sword, "merge", None, "f" * 32, None, False),  # no such target
                (self.sword, "spell", None, None, "bell\x07", False),
                (self.sword, "fly", None, None, None, False),
            ):
                cur = await conn.execute(
                    "SELECT dmbot_web_name_action_add(%s, %s, %s, %s, %s, %s, %s, %s) AS r",
                    (self.g, self.one.id, *args),
                )
                row = await cur.fetchone()
                answers.append(row and row["r"]["status"])
        self.assertEqual(
            answers, ["needs_confirm", "needs_confirm", "bad_target", "bad_text", "bad_action"]
        )

    async def test_thirty_waiting_actions_fill_the_queue(self) -> None:
        for _ in range(30):
            await self.queue(self.keep, action="add-other", other="Another")
        self.assertEqual(
            await self.refused_action(self.keep, action="add-other", other="One More"), "too_soon"
        )


class TheBot(Acting):
    async def test_a_sender_who_stopped_being_a_dm_is_refused_when_it_is_applied(self) -> None:
        answer = await self.queue(self.keep, action="spell", name="Greywater Hold")
        self.assertEqual([o.error for o in await self.apply_all(dms={999})], ["not_allowed"])
        self.assertEqual((await self.poll(answer))["error"], "not_allowed")
        self.assertIn("Greywater Keep", str(await self.page()))  # nothing changed

    async def test_a_name_that_went_after_it_was_queued_is_not_found(self) -> None:
        answer = await self.queue(self.keep, action="spell", name="Greywater Hold")
        await self.sql("UPDATE memory_entities SET status = 'rejected' WHERE id = %s", self.keep)
        await self.apply_all()
        self.assertEqual((await self.poll(answer))["error"], "not_found")

    async def test_actions_run_in_the_order_they_were_made(self) -> None:
        first = await self.queue(self.keep, action="spell", name="Greywater Hold")
        second = await self.queue(self.keep, action="kind", kind="faction")
        third = await self.queue(self.keep, action="spell", name="Greywater Tower")
        order = []
        while (a := await self.actions.claim_next(self.g)) is not None:
            order.append(a.action + ":" + a.text)
            await self.actions.finish(self.g, a)
        self.assertEqual(order, ["spell:Greywater Hold", "kind:", "spell:Greywater Tower"])
        for answer in (first, second, third):
            self.assertEqual((await self.poll(answer))["state"], "done")

    async def test_the_bot_refuses_what_a_hand_made_row_gets_past_the_door(self) -> None:
        def action(**kw: Any) -> wa.Action:
            return wa.Action("a" * 32, self.one.id, self.keep, ALICE.id, **kw)

        for bad, why in (
            (action(action="remove", flag=False), wa.BAD_TARGET),  # no confirmation
            (action(action="merge", target_id=self.keep), wa.BAD_TARGET),  # into itself
            (action(action="merge", target_id="e" * 32), wa.BAD_TARGET),  # no such name
            (action(action="fly"), wa.FAILED),
        ):
            outcome = await wa.apply(self.memory, self.g, {ALICE.id}, bad)
            self.assertEqual(outcome.error, why, bad.action)
        self.assertIn("Greywater Keep", str(await self.page()))  # nothing changed

    async def test_a_second_worker_cannot_pass_an_action_the_first_is_still_applying(self) -> None:
        await self.queue(self.keep, action="spell", name="Greywater Hold")
        await self.queue(self.keep, action="kind", kind="faction")
        first = await self.actions.claim_next(self.g)
        assert first is not None and first.action == "spell"
        self.assertIsNone(await self.actions.claim_next(self.g))  # the kind waits for the spell
        await self.actions.finish(self.g, first)
        second = await self.actions.claim_next(self.g)
        assert second is not None
        self.assertEqual(second.action, "kind")

    async def test_a_worker_cannot_pass_a_row_another_worker_has_locked_but_not_committed(
        self,
    ) -> None:
        await self.queue(self.keep, action="spell", name="Greywater Hold")
        await self.queue(self.keep, action="kind", kind="faction")
        async with self.db.guild(self.g) as holder:
            await holder.execute(
                "SELECT 1 FROM name_actions WHERE guild_id = %s ORDER BY seq LIMIT 1 FOR UPDATE",
                (self.g,),
            )  # worker A is claiming row 1: locked, not yet committed
            self.assertIsNone(await self.actions.claim_next(self.g))  # B must not take row 2
        first = await self.actions.claim_next(self.g)  # A's lock is gone: row 1 is next
        assert first is not None
        self.assertEqual(first.action, "spell")

    async def test_a_retaken_merge_or_remove_that_already_happened_is_done_not_failed(self) -> None:
        merge = await self.queue(self.sword, action="merge", into=self.keep)
        taken = await self.actions.claim_next(self.g)
        assert taken is not None
        first = await wa.apply(self.memory, self.g, {ALICE.id}, taken)  # done, but never marked
        self.assertEqual(first.error, "")
        again = await wa.apply(self.memory, self.g, {ALICE.id}, taken)  # the retake runs it again
        self.assertEqual((again.error, again.result_id), ("", self.keep))
        await self.actions.finish(self.g, taken, result_id=again.result_id)
        self.assertEqual((await self.poll(merge))["state"], "done")
        remove = await self.queue(self.mira, action="remove", confirm=True)
        taken = await self.actions.claim_next(self.g)
        assert taken is not None
        await wa.apply(self.memory, self.g, {ALICE.id}, taken)
        self.assertEqual((await wa.apply(self.memory, self.g, {ALICE.id}, taken)).error, "")
        await self.actions.finish(self.g, taken)
        self.assertTrue((await self.poll(remove))["removed"])

    async def test_a_name_that_is_only_punctuation_can_still_have_other_names_removed(self) -> None:
        odd = await self.add("???", "item")
        await self.alias(odd, "!!!")
        answer = await self.queue(odd, action="remove-other", other="!!!")
        await self.apply_all()
        self.assertEqual((await self.poll(answer))["error"], "")

    async def test_adding_a_name_it_already_has_in_secret_says_so(self) -> None:
        answer = await self.queue(self.mira, action="add-other", other="Lady Grey")
        await self.apply_all()
        self.assertEqual((await self.poll(answer))["error"], "exists")

    async def test_how_to_say_it_is_not_a_spelling_here_either(self) -> None:
        self.assertEqual(
            await self.refused_action(self.keep, action="spell", name="Greywater pronounced Gray"),
            "bad_name",
        )
        hand_made = wa.Action(
            "a" * 32, self.one.id, self.keep, ALICE.id, "spell", text="Greywater sounds like Gray"
        )
        self.assertEqual(
            (await wa.apply(self.memory, self.g, {ALICE.id}, hand_made)).error, "bad_name"
        )
        await self.queue(self.keep, action="spell", name="Pronouncer Vex")  # (a real name is fine)

    async def test_a_stalled_claim_is_picked_up_again_and_only_its_holder_may_finish(self) -> None:
        await self.queue(self.keep, action="kind", kind="faction")
        now = [1000.0]
        store = wa.ActionStore(self.db, clock=lambda: now[0])
        first = await store.claim_next(self.g)
        assert first is not None
        self.assertIsNone(await store.claim_next(self.g))  # still being worked on
        now[0] += wa.RETAKE_AFTER_S + 1
        second = await store.claim_next(self.g)
        assert second is not None
        self.assertFalse(await store.finish(self.g, first))  # not its row any more
        self.assertTrue(await store.finish(self.g, second))

    async def test_old_actions_are_swept_whatever_their_state(self) -> None:
        await self.queue(self.keep, action="kind", kind="faction")
        await self.sql("UPDATE name_actions SET created_at = 1")
        self.assertEqual(await self.actions.sweep(self.g), 1)


class TheRoutes(Acting):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.app = create_app(
            settings(), self.web, FakeDiscord(), payments=FakeProvider(b"h" * 32, "https://s.example"),
            clock=lambda: NOW,
        )  # fmt: skip
        token = await sessions.sign_in(self.web, ALICE, [THURSDAY], now=NOW, days=30)
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url=API, follow_redirects=False
        )
        self.client.cookies.set("__Host-dmbot_session", token, domain="api.dmbot.example")
        self.addAsyncCleanup(self.client.aclose)

    async def test_post_then_poll_over_http(self) -> None:
        url = f"/me/campaigns/{self.cref}/all-names"
        sent = await self.client.post(
            f"{url}/{self.keep}",
            json={"action": "spell", "name": "Greywater Hold"},
            headers=HEADERS,
        )
        self.assertEqual(sent.status_code, 202)
        answer = sent.json()["answer"]
        waiting = await self.client.get(f"{url}/answers/{answer}")
        self.assertEqual((waiting.status_code, waiting.json()["state"]), (200, "waiting"))
        await self.apply_all()
        done = await self.client.get(f"{url}/answers/{answer}")
        self.assertEqual(done.json()["row"]["name"], "Greywater Hold")

    async def test_refusals_over_http(self) -> None:
        url = f"/me/campaigns/{self.cref}/all-names/{self.keep}"
        for body, status, word in (
            ({"action": "remove"}, 400, "needs_confirm"),
            ({"action": "spell", "name": "x" * 101}, 400, "too_long"),
            ({"action": "kind", "kind": "!"}, 400, "bad_kind"),
            ({"action": "nope"}, 400, "bad_action"),
        ):
            got = await self.client.post(url, json=body, headers=HEADERS)
            self.assertEqual((got.status_code, got.json()), (status, {"error": word}), body)
        notjson = await self.client.post(url, content=b"x", headers=HEADERS)
        self.assertEqual(notjson.status_code, 400)
        missing = await self.client.get(f"/me/campaigns/{self.cref}/all-names/answers/{'0' * 32}")
        self.assertEqual((missing.status_code, missing.json()), (404, {"error": "not_found"}))
        anonymous = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url=API, follow_redirects=False
        )
        self.addAsyncCleanup(anonymous.aclose)
        self.assertEqual((await anonymous.post(url, json={}, headers=HEADERS)).status_code, 401)


class TheDoors(Acting):
    async def test_the_doors_have_one_caller(self) -> None:
        for door in ("dmbot_web_name_action_add(", "dmbot_web_name_action_read("):
            # the relationship web queues its edits through the same doors (#2323)
            self.assertEqual(
                callers(door), {"schema.py", "web/all_names.py", "web/relationships.py"}
            )
