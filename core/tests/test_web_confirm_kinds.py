"""The website's "Add all" for the names an upload found (#3207): the upload row keeps what the
reading proposed (ids by kind), the progress read says how many of each still wait, and the DM
queues `confirm-kinds` and `confirm-undo` for the bot. Invented names throughout."""

from __future__ import annotations

import json
from typing import Any

from dmbot.memory import web_actions as wa
from dmbot.memory.models import CONFIRMED, PROPOSED
from dmbot.memory.models import DM as DM_SOURCE
from dmbot.web import all_names, lore
from dmbot.web.recaps import RecapError
from tests.test_web_relationships import Web


class Confirming(Web):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.upload = "a" * 32
        self.ids: dict[str, list[str]] = {"place": [], "character": [], "concept": []}
        for kind, names in {
            "place": ["Rivermouth", "The Grey Pier"],
            "character": ["Old Hob", "Sister Wren", "Captain Dace"],
            "concept": ["A debt unpaid"],
        }.items():
            for name in names:
                made = await self.memory.add_entity(  # proposed: still waiting for a yes
                    self.g, self.one.id, type=kind,
                    name=name, source="background", role="npc" if kind == "character" else None,
                )  # fmt: skip
                self.ids[kind].append(made.value.id)
        await self.put_upload("done")

    async def put_upload(self, state: str) -> None:
        await self.sql("DELETE FROM lore_uploads")
        await self.sql(
            "INSERT INTO lore_uploads (guild_id, campaign_id, id, user_id, source, choice,"
            " rights_by, rights_at, state, stage, names_found, created_at, updated_at, found)"
            " VALUES (%s, %s, %s, %s, 'paste', 'world', %s, 1, %s, 'done', 6,"
            " floor(extract(epoch FROM now())), floor(extract(epoch FROM now())), %s::jsonb)",
            self.g, self.one.id, self.upload, self.session.user_id, self.session.user_id,
            state, json.dumps(self.ids),
        )  # fmt: skip

    async def waiting(self) -> dict[str, int]:
        got = await lore.read(self.web, self.session, self.cref, self.upload)
        return dict(got["upload"]["waiting"])

    async def queue(self, body: dict[str, Any], who: Any = None) -> str:
        return await all_names.queue_confirm(self.web, who or self.session, self.cref, body)

    async def refused_confirm(self, body: dict[str, Any], who: Any = None) -> str:
        with self.assertRaises(RecapError) as caught:
            await self.queue(body, who)
        return caught.exception.code

    async def rows(self) -> list[dict[str, Any]]:
        async with self.db.guild(self.g) as conn:
            cur = await conn.execute(
                "SELECT action, target_id, entity_id, text FROM name_actions"
                " WHERE campaign_id = %s",
                (self.one.id,),
            )
            return list(await cur.fetchall())


class TheCounts(Confirming):
    async def test_the_read_says_how_many_of_each_kind_still_wait_and_never_ideas(self) -> None:
        self.assertEqual(await self.waiting(), {"place": 2, "character": 3})

    async def test_a_name_answered_since_is_not_counted(self) -> None:
        await self.memory.confirm_kinds(
            self.g, self.one.id, [self.ids["place"][0]], "place", source=DM_SOURCE
        )
        self.assertEqual(await self.waiting(), {"place": 1, "character": 3})

    async def test_a_player_never_gets_the_counts(self) -> None:
        with self.assertRaises(RecapError):
            await lore.read(self.web, self.player, self.cref, self.upload)


class TheDoor(Confirming):
    async def test_a_dm_queues_kinds_or_all(self) -> None:
        await self.queue({"action": "confirm-kinds", "upload": self.upload, "kinds": ["place"]})
        await self.queue({"action": "confirm-kinds", "upload": self.upload, "kinds": "all"})
        got = sorted((r["action"], r["target_id"], r["text"]) for r in await self.rows())
        self.assertEqual(
            got,
            [
                ("confirm-kinds", self.upload, "all"),
                ("confirm-kinds", self.upload, "place"),
            ],
        )

    async def test_ideas_bad_words_and_repeats_are_refused_before_the_door(self) -> None:
        for kinds in (
            ["idea"], ["concept"], ["all"], ["Place"], [], ["place", "place"], ["a"],
            "everything", None,
        ):  # fmt: skip
            with self.subTest(kinds=kinds):
                self.assertEqual(
                    await self.refused_confirm(
                        {"action": "confirm-kinds", "upload": self.upload, "kinds": kinds}
                    ),
                    "bad_target",
                )
        self.assertEqual(await self.rows(), [])

    async def test_an_upload_still_being_read_or_not_there_is_not_found(self) -> None:
        await self.put_upload("working")
        body = {"action": "confirm-kinds", "upload": self.upload, "kinds": "all"}
        self.assertEqual(await self.refused_confirm(body), "not_found")
        self.assertEqual(
            await self.refused_confirm({**body, "upload": "b" * 32}), "not_found"
        )  # another campaign's, or none
        self.assertEqual(await self.rows(), [])

    async def test_eight_long_kind_words_fit(self) -> None:
        kinds = [f"{chr(97 + n) * 20}" for n in range(8)]  # 167 characters
        await self.queue({"action": "confirm-kinds", "upload": self.upload, "kinds": kinds})
        self.assertEqual(len((await self.rows())[0]["text"]), 8 * 20 + 7)

    async def test_another_campaigns_finished_upload_is_not_found(self) -> None:
        other = await self.campaigns.create(self.g, "Other", self.session.user_id)
        await self.sql(
            "INSERT INTO lore_uploads (guild_id, campaign_id, id, user_id, source, choice,"
            " rights_by, rights_at, state, stage, created_at, updated_at)"
            " VALUES (%s, %s, %s, %s, 'paste', 'world', %s, 1, 'done', 'done', 1, 1)",
            self.g, other.id, "d" * 32, self.session.user_id, self.session.user_id,
        )  # fmt: skip
        body = {"action": "confirm-kinds", "upload": "d" * 32, "kinds": "all"}
        self.assertEqual(await self.refused_confirm(body), "not_found")

    async def test_a_badly_shaped_found_does_not_break_the_read(self) -> None:
        await self.sql(
            "UPDATE lore_uploads SET found = %s::jsonb WHERE id = %s",
            json.dumps({"place": "x", "character": None, "item": [self.ids["character"][0]]}),
            self.upload,
        )
        self.assertEqual(await self.waiting(), {"item": 1})

    async def test_a_player_cannot_queue_it(self) -> None:
        body = {"action": "confirm-kinds", "upload": self.upload, "kinds": "all"}
        self.assertEqual(await self.refused_confirm(body, self.player), "not_allowed")

    async def test_only_the_two_actions_come_through_here(self) -> None:
        self.assertEqual(await self.refused_confirm({"action": "remove"}), "bad_action")
        self.assertEqual(await self.refused_confirm({}), "bad_action")

    async def test_undo_needs_your_own_finished_press_that_made_something(self) -> None:
        first = await self.queue(
            {"action": "confirm-kinds", "upload": self.upload, "kinds": ["character"]}
        )
        undo = {"action": "confirm-undo", "answer": first}
        self.assertEqual(await self.refused_confirm(undo), "not_found")  # still waiting
        await self.sql(
            "UPDATE name_actions SET state = 'done', result_counts = 'character:3',"
            " result_batches = ARRAY[7, 8] WHERE id = %s",
            first,
        )
        again = await self.queue(undo)
        self.assertRegex(again, r"^[0-9a-f]{32}$")
        self.assertEqual(await self.refused_confirm(undo), "not_found")  # one Undo for a press
        self.assertEqual(await self.refused_confirm({**undo, "answer": "c" * 32}), "not_found")
        await self.sql("UPDATE name_actions SET result_batches = '{}' WHERE id = %s", first)
        self.assertEqual(await self.refused_confirm(undo), "not_found")  # it made nothing

    async def test_the_answer_reads_back_the_counts_and_offers_undo(self) -> None:
        first = await self.queue(
            {"action": "confirm-kinds", "upload": self.upload, "kinds": ["place", "character"]}
        )
        waiting = await all_names.read_action(self.web, self.session, self.cref, first)
        self.assertEqual(
            (waiting["state"], waiting["counts"], waiting["undo"]), ("waiting", {}, False)
        )
        await self.sql(
            "UPDATE name_actions SET state = 'done', result_counts = 'place:2 character:3',"
            " result_batches = ARRAY[9], progress_done = 2, progress_total = 2 WHERE id = %s",
            first,
        )
        done = await all_names.read_action(self.web, self.session, self.cref, first)
        self.assertEqual(done["counts"], {"place": 2, "character": 3})
        self.assertTrue(done["undo"])
        self.assertEqual((done["progress"]["done"], done["progress"]["total"]), (2, 2))


class TheApplier(Confirming):
    """The bot's half (#3207): what a queued Add all and its Undo do, via the store's own calls."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.actions = wa.ActionStore(self.db)

    async def apply_all(self, dms: Any = None) -> list[wa.Outcome]:
        """The bot's loop for the queue: claim, apply, finish (counts and changes too)."""
        done = []
        while (action := await self.actions.claim_next(self.g)) is not None:
            outcome = await wa.apply(
                self.memory,
                self.g,
                {self.session.user_id} if dms is None else dms,
                action,
                actions=self.actions,
            )
            await self.actions.finish(
                self.g,
                action,
                error=outcome.error,
                result_id=outcome.result_id,
                result_other=outcome.result_other,
                result_counts=outcome.counts,
                result_batches=outcome.batches,
            )
            done.append(outcome)
        return done

    async def status(self, entity: str) -> str:
        found = await self.memory.entity(self.g, self.one.id, entity)
        assert found is not None
        return str(found.status)

    async def press(self, kinds: Any) -> str:
        return await self.queue({"action": "confirm-kinds", "upload": self.upload, "kinds": kinds})

    async def test_a_kinds_press_confirms_exactly_its_waiting_names(self) -> None:
        answer = await self.press(["place"])
        (outcome,) = await self.apply_all()
        self.assertEqual((outcome.error, outcome.counts), ("", "place:2"))
        for entity in self.ids["place"]:
            self.assertEqual(await self.status(entity), CONFIRMED)
        for entity in self.ids["character"] + self.ids["concept"]:
            self.assertEqual(await self.status(entity), PROPOSED)
        read = await all_names.read_action(self.web, self.session, self.cref, answer)
        self.assertEqual(
            (read["state"], read["counts"], read["undo"]), ("done", {"place": 2}, True)
        )

    async def test_all_confirms_every_kind_but_ideas(self) -> None:
        await self.press("all")
        (outcome,) = await self.apply_all()
        self.assertEqual(sorted(outcome.counts.split()), ["character:3", "place:2"])
        self.assertEqual(len(outcome.batches), 2)  # one change for each kind
        self.assertEqual(await self.status(self.ids["concept"][0]), PROPOSED)

    async def test_undo_takes_back_the_whole_press(self) -> None:
        first = await self.press("all")
        await self.apply_all()
        await self.queue({"action": "confirm-undo", "answer": first})
        (outcome,) = await self.apply_all()
        self.assertEqual(outcome.error, "")
        self.assertEqual(sorted(outcome.counts.split()), ["character:3", "place:2"])
        self.assertEqual(await self.waiting(), {"place": 2, "character": 3})

    async def test_a_name_answered_since_is_left_alone_and_a_second_press_adds_nothing(
        self,
    ) -> None:
        await self.memory.confirm_kinds(
            self.g, self.one.id, [self.ids["place"][0]], "place", source=DM_SOURCE
        )
        await self.press(["place"])
        await self.press(["place"])
        first, second = await self.apply_all()
        self.assertEqual((first.error, first.counts), ("", "place:1"))
        self.assertEqual((second.error, second.counts, second.batches), ("", "", ()))

    async def test_ideas_and_player_characters_are_never_added_at_once(self) -> None:
        for kind in ("concept", "player_character"):
            await self.press(["place"])
            # (the door already refuses these; the bot does not trust the row on that alone)
            await self.sql("UPDATE name_actions SET text = %s WHERE state = 'waiting'", kind)
            (outcome,) = await self.apply_all()
            self.assertEqual(outcome.error, wa.BAD_KIND, kind)
        self.assertEqual(await self.status(self.ids["concept"][0]), PROPOSED)

    async def test_someone_who_is_no_longer_a_dm_is_not_allowed(self) -> None:
        await self.press("all")
        (outcome,) = await self.apply_all(dms=set())
        self.assertEqual(outcome.error, wa.NOT_ALLOWED)
        self.assertEqual(await self.waiting(), {"place": 2, "character": 3})

    async def test_another_campaigns_upload_is_gone_to_the_bot(self) -> None:
        other = await self.campaigns.create(self.g, "Other", self.session.user_id)
        self.assertIsNone(await self.actions.found_of(self.g, other.id, self.upload))
        self.assertIsNotNone(await self.actions.found_of(self.g, self.one.id, self.upload))

    async def test_the_two_lists_of_kinds_that_need_a_look_agree(self) -> None:
        from dmbot.memory.models import NEEDS_A_LOOK
        from dmbot.ui.name_background import BulkConfirm

        # (one set for both: Discord's buttons and the website's skip ideas and player characters)
        self.assertEqual(NEEDS_A_LOOK, {"concept", "player_character"})
        bulk = BulkConfirm(
            self.one.id, {"concept": ["x"], "player_character": ["y"], "place": ["z"]}
        )
        self.assertEqual(list(bulk._addable()), ["place"])

    async def test_a_kind_the_rules_refuse_is_left_out_and_the_rest_still_undoes(self) -> None:
        first = await self.press(["place", "faction"])
        (outcome,) = await self.apply_all()
        self.assertEqual((outcome.error, outcome.counts), ("", "place:2"))  # (faction: none found)
        self.assertEqual(len(outcome.batches), 1)
        await self.queue({"action": "confirm-undo", "answer": first})
        self.assertEqual([o.error for o in await self.apply_all()], [""])

    async def test_a_press_taken_again_after_a_stop_does_not_add_twice_or_lose_its_undo(
        self,
    ) -> None:
        answer = await self.press(["place", "character"])
        action = await self.actions.claim_next(self.g)
        assert action is not None
        # the first try did the places, kept what it did, then the bot stopped before finishing
        written = await self.memory.confirm_kinds(
            self.g, self.one.id, self.ids["place"], "place", source=DM_SOURCE
        )
        assert written.batch is not None
        await self.actions.keep_result(self.g, action, "place:2", [written.batch])
        outcome = await wa.apply(
            self.memory, self.g, {self.session.user_id}, action, actions=self.actions
        )
        self.assertEqual(outcome.counts, "place:2 character:3")  # the places counted once
        self.assertEqual(len(outcome.batches), 2)
        await self.actions.finish(
            self.g, action, result_counts=outcome.counts, result_batches=outcome.batches
        )
        read = await all_names.read_action(self.web, self.session, self.cref, answer)
        self.assertEqual((read["state"], read["undo"]), ("done", True))

    async def test_an_undo_taken_again_after_a_stop_skips_what_is_already_back(self) -> None:
        first = await self.press("all")
        (pressed,) = await self.apply_all()
        await self.queue({"action": "confirm-undo", "answer": first})
        action = await self.actions.claim_next(self.g)
        assert action is not None
        await self.memory.undo(self.g, self.one.id, pressed.batches[-1])  # the first try's half
        outcome = await wa.apply(
            self.memory, self.g, {self.session.user_id}, action, actions=self.actions
        )
        self.assertEqual(outcome.error, "")
        self.assertEqual(await self.waiting(), {"place": 2, "character": 3})

    async def test_a_changed_since_undo_is_refused_not_a_crash(self) -> None:
        first = await self.press(["place"])
        await self.apply_all()
        await self.memory.set_entity_status(
            self.g, self.one.id, self.ids["place"][0], "rejected", source=DM_SOURCE
        )
        await self.queue({"action": "confirm-undo", "answer": first})
        (outcome,) = await self.apply_all()
        self.assertEqual(outcome.error, wa.GONE)


class TheUploadKeepsItsNames(Confirming):
    async def claim(self) -> Any:
        from dmbot.memory.lore_queue import LoreQueue

        await self.sql("UPDATE lore_uploads SET state = 'waiting', stage = 'waiting', found = '{}'")
        queue = LoreQueue(self.db)
        claim = await queue.claim_next(self.g)
        assert claim is not None
        self.queue_ = queue
        return claim

    async def stored(self) -> dict[str, list[str]]:
        async with self.db.guild(self.g) as conn:
            cur = await conn.execute("SELECT found FROM lore_uploads WHERE id = %s", (self.upload,))
            row = await cur.fetchone()
        assert row is not None
        found: dict[str, list[str]] = row["found"]
        return found

    async def test_finish_keeps_the_ids_by_kind(self) -> None:
        claim = await self.claim()
        await self.queue_.finish(self.g, claim, names=5, found=self.ids)
        self.assertEqual(await self.stored(), self.ids)

    async def test_a_failed_upload_keeps_none(self) -> None:
        claim = await self.claim()
        await self.queue_.finish(self.g, claim, error="failed", found=self.ids)
        self.assertEqual(await self.stored(), {})

    async def test_a_flood_of_ideas_cannot_crowd_out_the_names_add_all_covers(self) -> None:
        from dmbot.memory.lore_queue import FOUND_IDS_MAX

        claim = await self.claim()
        many = {
            "concept": [f"{n:032x}" for n in range(FOUND_IDS_MAX + 50)],
            "place": ["b" * 32, "c" * 32],
        }
        await self.queue_.finish(self.g, claim, names=1, found=many)
        self.assertEqual((await self.stored())["place"], ["b" * 32, "c" * 32])

    async def test_a_huge_read_is_cut_not_refused(self) -> None:
        from dmbot.memory.lore_queue import FOUND_IDS_MAX

        claim = await self.claim()
        many = {"place": [f"{n:032x}" for n in range(FOUND_IDS_MAX + 500)], "npc": ["a" * 32]}
        self.assertTrue(await self.queue_.finish(self.g, claim, names=1, found=many))
        kept = await self.stored()
        self.assertEqual(sum(len(v) for v in kept.values()), FOUND_IDS_MAX)
