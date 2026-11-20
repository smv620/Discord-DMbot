"""The website's pack updates (#4841 packs slice, migration 0566): only a DM reads them, a followed
pack with a newer version gives core's own lines (worked out by `rules.pack_diff`, as Discord's
note), a pack that stopped updating or is on the latest gives none, an update DMbot took shows
with the copy before it, a damaged copy is left out, and a share code never leaves. Postgres.
Invented names."""

from __future__ import annotations

import json
from typing import Any

from dmbot.rules import pack_diff
from dmbot.web import pack_updates
from dmbot.web.recaps import RecapError
from tests.test_recap_web import BOB
from tests.test_web_api import ALICE, THURSDAY
from tests.test_web_rule_sharing import SECRET_CODE, SET_A, SharingBase


def pack(*entries: tuple[str, str]) -> dict[str, Any]:
    return {
        "entries": [
            {"kind": "item", "name": n, "summary": None, "text": t, "known": False}
            for n, t in entries
        ]
    }


OLD = pack(("Lamp", "Glows."), ("Rope", "Long."))
NEW = pack(("Lamp", "Glows."), ("Rope", "Very long."), ("Flute", "Plays."))


class PackBase(SharingBase):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        await self.publish(self.two.id, SET_A, "Frost Pack")  # (versions 1 and 2)
        await self.sql("UPDATE rule_sets SET kind = 'homebrew' WHERE id = %s", SET_A)
        await self.sql(
            "UPDATE rule_set_versions SET content = %s::jsonb WHERE set_id = %s AND version = 2",
            json.dumps(NEW), SET_A,
        )  # fmt: skip
        await self.follow_pack(OLD)

    async def follow_pack(self, content: dict[str, Any], **extra: Any) -> None:
        cols = {"version": 1, "updating": True, "previous_version": None,
                "previous_content": None, **extra}  # fmt: skip
        await self.sql("DELETE FROM rule_set_follows")
        await self.sql(
            "INSERT INTO rule_set_follows (guild_id, campaign_id, set_id, kind, set_name,"
            " publisher, version, content, updating, previous_version, previous_content,"
            " followed_by, followed_at) VALUES (%s, %s, %s, 'homebrew', 'Frost Pack', 'Two',"
            " %s, %s::jsonb, %s, %s, %s::jsonb, %s, 5)",
            THURSDAY.id, self.one.id, SET_A, cols["version"], json.dumps(content),
            cols["updating"], cols["previous_version"],
            None if cols["previous_content"] is None else json.dumps(cols["previous_content"]),
            ALICE.id,
        )  # fmt: skip

    async def fetch(self, session: Any = None) -> Any:
        return await pack_updates.read(self.web, session or self.session, self.cref())


class PackUpdates(PackBase):
    async def test_a_newer_version_gives_core_s_own_lines(self) -> None:
        got = await self.fetch()
        changes = pack_diff.diff(OLD, NEW)
        self.assertEqual(
            got["waiting"],
            [{
                "setId": SET_A, "name": "Frost Pack", "publisher": "Two", "from": 1, "to": 2,
                "words": pack_diff.count_words(changes),
                "lines": [pack_diff.describe(c) for c in changes], "more": 0,
            }],
        )  # fmt: skip
        self.assertEqual((got["waiting"][0]["words"], got["taken"]), ("2 entries", []))
        self.assertEqual(
            set(got["waiting"][0]),
            {"setId", "name", "publisher", "from", "to", "words", "lines", "more"},
        )

    async def test_nothing_waits_when_on_the_latest_or_no_longer_updating(self) -> None:
        await self.follow_pack(NEW, version=2)
        self.assertEqual((await self.fetch())["waiting"], [])
        await self.follow_pack(OLD, updating=False)
        self.assertEqual((await self.fetch())["waiting"], [])

    async def test_a_pack_that_stopped_sharing_gives_nothing(self) -> None:
        await self.sql("UPDATE rule_sets SET status = 'unshared' WHERE id = %s", SET_A)
        self.assertEqual((await self.fetch())["waiting"], [])

    async def test_a_taken_update_shows_with_the_copy_before_it(self) -> None:
        await self.follow_pack(NEW, version=2, previous_version=1, previous_content=OLD)
        got = await self.fetch()
        changes = pack_diff.diff(OLD, NEW)
        self.assertEqual(got["waiting"], [])
        self.assertEqual(
            [(t["from"], t["to"], t["lines"]) for t in got["taken"]],
            [(1, 2, [pack_diff.describe(c) for c in changes])],
        )

    async def test_a_damaged_copy_is_left_out_and_house_rules_are_not_packs(self) -> None:
        await self.follow_pack({"entries": "nope"})
        self.assertEqual(await self.fetch(), {"waiting": [], "taken": []})
        await self.follow_pack(OLD)
        await self.sql("UPDATE rule_set_follows SET kind = 'house_rules'")
        self.assertEqual(await self.fetch(), {"waiting": [], "taken": []})

    async def test_a_player_is_refused(self) -> None:
        with self.assertRaises(RecapError) as err:
            await self.fetch(await self.signed(BOB))
        self.assertEqual(err.exception.code, "not_allowed")

    async def test_a_follow_by_share_code_reads_through_the_guarded_function(self) -> None:
        await self.code(self.two.id, SET_A)
        await self.sql("UPDATE rule_set_follows SET code = %s", SECRET_CODE)
        got = await self.fetch()
        self.assertEqual([(w["from"], w["to"]) for w in got["waiting"]], [(1, 2)])
        self.assertNotIn(SECRET_CODE, json.dumps(got))

    async def test_a_declined_version_is_still_shown_to_the_dm(self) -> None:
        await self.sql("UPDATE rule_set_follows SET declined_version = 2")
        self.assertEqual(len((await self.fetch())["waiting"]), 1)

    async def test_a_long_list_of_changes_is_cut_and_counted(self) -> None:
        many = pack(*[(f"Item {n}", "Plain.") for n in range(pack_updates.MAX_LINES + 6)])
        await self.sql(
            "UPDATE rule_set_versions SET content = %s::jsonb WHERE set_id = %s AND version = 2",
            json.dumps(many), SET_A,
        )  # fmt: skip
        got = (await self.fetch())["waiting"][0]
        self.assertEqual((len(got["lines"]), got["more"]), (pack_updates.MAX_LINES, 8))


class ToolsBase(PackBase):
    """The bot's pack tools for the actions (#4841 packs P2 and P3)."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        from dmbot.memory import web_actions as wa
        from dmbot.memory.store import MemoryStore
        from dmbot.rules import rule_sets
        from dmbot.rules.pack_updates import PackUpdateStore
        from dmbot.rules.web_updates import RuleTools, TakeStore

        await self.follow_pack(NEW, version=2, previous_version=1, previous_content=OLD)
        self.settled: list[str] = []
        self.changed: list[tuple[int, str]] = []

        async def settle(guild_id: int, campaign_id: str) -> None:
            self.settled.append(campaign_id)

        self.tools = RuleTools(
            rule_sets.RuleSetStore(self.db), MemoryStore(self.db), TakeStore(self.db), settle,
            PackUpdateStore(self.db), lambda g, c: self.changed.append((g, c)),
        )  # fmt: skip
        self.actions = wa.ActionStore(self.db)

    async def drain(self) -> list[Any]:
        from dmbot.memory import web_actions as wa
        from dmbot.memory.store import MemoryStore

        done = []
        while (action := await self.actions.claim_next(THURSDAY.id)) is not None:
            outcome = await wa.apply(
                MemoryStore(self.db), THURSDAY.id, {ALICE.id}, action,
                actions=self.actions, rules=self.tools,
            )  # fmt: skip
            await self.actions.finish(
                THURSDAY.id, action, error=outcome.error, result_id=outcome.result_id,
                result_counts=outcome.counts, result_batches=outcome.batches,
            )  # fmt: skip
            done.append(outcome)
        return done

    async def undo(self, version: int, set_id: str = SET_A) -> Any:
        await pack_updates.queue_undo_last(
            self.web, self.session, self.cref(), {"setId": set_id, "version": version}
        )
        (done,) = await self.drain()
        return done


class UndoingATake(ToolsBase):
    """The Undo of a pack's taken update (#4841 packs P2, migration 0567)."""

    async def test_the_undo_puts_the_copy_before_back_and_a_session_is_told(self) -> None:
        done = await self.undo(2)
        self.assertEqual((done.error, done.counts), ("", "back:1"))
        self.assertEqual(self.changed, [(THURSDAY.id, self.one.id)])
        got = await self.fetch()
        self.assertEqual(got["taken"], [])
        self.assertEqual([w["from"] for w in got["waiting"]], [1])  # (waiting again)
        self.assertEqual((await self.undo(2)).error, "too_late")

    async def test_an_old_page_cannot_undo_a_newer_take(self) -> None:
        self.assertEqual((await self.undo(1)).error, "too_late")
        self.assertEqual(len((await self.fetch())["taken"]), 1)

    async def test_another_pack_is_not_touched(self) -> None:
        self.assertEqual((await self.undo(2, "b" * 32)).error, "too_late")
        self.assertEqual(len((await self.fetch())["taken"]), 1)

    async def test_the_answer_says_which_version_it_went_back_to(self) -> None:
        from dmbot.web import relationships

        answer = await pack_updates.queue_undo_last(
            self.web, self.session, self.cref(), {"setId": SET_A, "version": 2}
        )
        await self.drain()
        read = await relationships.read_answer(self.web, self.session, self.cref(), answer)
        self.assertEqual((read["state"], read["back"], read["error"]), ("done", 1, ""))

    async def test_a_bad_body_is_refused_before_anything_is_queued(self) -> None:
        for body in [{}, {"setId": "x", "version": 2}, {"setId": SET_A}, {"setId": SET_A,
                     "version": True}, {"setId": SET_A.upper(), "version": 2}, 5]:  # fmt: skip
            with self.assertRaises(RecapError) as err:
                await pack_updates.queue_undo_last(self.web, self.session, self.cref(), body)
            self.assertEqual(err.exception.code, "bad_action")
        self.assertEqual(await self.drain(), [])

    async def test_someone_who_is_not_a_dm_is_refused_at_the_queue_door(self) -> None:
        with self.assertRaises(RecapError) as err:
            await pack_updates.queue_undo_last(
                self.web, await self.signed(BOB), self.cref(),
                {"setId": SET_A, "version": 2},
            )  # fmt: skip
        self.assertEqual(err.exception.code, "not_allowed")
        self.assertEqual(len((await self.fetch())["taken"]), 1)  # (the take is still there)


class PickingChanges(ToolsBase):
    """Pick changes on a followed pack and its Undo (#4841 packs P3, migration 0568)."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        await self.follow_pack(OLD)  # (a newer version waits, nothing taken)

    async def pick(self, kept: list[int], version: int = 2) -> Any:
        await pack_updates.queue_pick(
            self.web, self.session, self.cref(),
            {"setId": SET_A, "version": version, "kept": kept},
        )  # fmt: skip
        (done,) = await self.drain()
        return done

    async def follow_row(self) -> Any:
        async with self.db.guild(THURSDAY.id) as conn:
            cur = await conn.execute(
                "SELECT version, content, updating FROM rule_set_follows WHERE campaign_id = %s",
                (self.one.id,),
            )
            return await cur.fetchone()

    async def homebrew_keys(self) -> set[str]:
        async with self.db.guild(THURSDAY.id) as conn:
            cur = await conn.execute(
                "SELECT name_key FROM homebrew_entries WHERE campaign_id = %s", (self.one.id,)
            )
            return {str(r["name_key"]) for r in await cur.fetchall()}

    async def test_leaving_a_change_out_ends_the_follow_and_gives_an_undo(self) -> None:
        # (OLD to NEW: Flute added, Rope changed; the page lists them in core's order)
        shown = pack_updates_lines()
        done = await self.pick([0])
        self.assertEqual(done.error, "")
        self.assertEqual(done.counts, f"picked:1 left:{len(shown) - 1} skipped:0")
        self.assertIsNotNone(done.result_id)
        self.assertEqual(self.changed, [(THURSDAY.id, self.one.id)])
        row = await self.follow_row()
        self.assertIsNone(row)  # (the follow ended: the pack is the campaign's own now)
        first = pack_diff.diff(OLD, NEW)[0]  # the one kept: its entry is copied, the rest are not
        have = await self.homebrew_keys()
        self.assertIn(first.key, have)
        others = {c.key for c in pack_diff.diff(OLD, NEW)[1:] if c.kind == "added"}
        self.assertEqual(have & others, set())  # (a change left out is not copied)

    async def test_the_undo_puts_the_follow_back_and_is_for_the_same_dm_and_the_newest(
        self,
    ) -> None:
        done = await self.pick([0])
        await pack_updates.queue_undo(self.web, self.session, self.cref(), str(done.result_id))
        (undone,) = await self.drain()
        self.assertEqual((undone.error, undone.counts), ("", "undone:0"))
        row = await self.follow_row()
        assert row is not None
        self.assertEqual((row["version"], row["content"]), (1, OLD))
        self.assertEqual(await self.homebrew_keys(), set())  # (what the pick copied is gone)
        await pack_updates.queue_undo(self.web, self.session, self.cref(), str(done.result_id))
        (again,) = await self.drain()
        self.assertEqual(again.error, "gone")  # (the handle went with the undo)

    async def test_keeping_everything_or_a_stale_version_is_refused(self) -> None:
        total = len(pack_updates_lines())
        self.assertEqual((await self.pick(list(range(total)))).error, "nothing_left_out")
        self.assertEqual((await self.pick([0], version=7)).error, "changed")
        row = await self.follow_row()
        assert row is not None
        self.assertEqual(row["version"], 1)

    async def test_a_pick_past_the_list_is_refused(self) -> None:
        self.assertEqual((await self.pick([40])).error, "gone")

    async def test_a_sets_handle_is_not_a_packs(self) -> None:
        done = await self.pick([0])
        # the same id read as a house-rules set's Undo finds nothing of its kind
        from dmbot.web import rule_updates

        await rule_updates.queue_undo(self.web, self.session, self.cref(), str(done.result_id))
        (wrong,) = await self.drain()
        self.assertEqual(wrong.error, "gone")
        await pack_updates.queue_undo(self.web, self.session, self.cref(), str(done.result_id))
        (right,) = await self.drain()
        self.assertEqual(right.error, "")

    async def test_a_set_handle_given_to_a_packs_undo_finds_nothing(self) -> None:
        await self.sql(
            "INSERT INTO web_rule_takes (guild_id, campaign_id, user_id, taken, created_at)"
            " VALUES (%s, %s, %s, '{}'::jsonb, extract(epoch FROM now())::bigint)",
            THURSDAY.id, self.one.id, ALICE.id,
        )  # fmt: skip
        async with self.db.guild(THURSDAY.id) as conn:
            cur = await conn.execute("SELECT max(id) AS id, max(kind) AS kind FROM web_rule_takes")
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual(row["kind"], "set")  # (a row made as before the column came is a set's)
        await pack_updates.queue_undo(self.web, self.session, self.cref(), str(row["id"]))
        (done,) = await self.drain()
        self.assertEqual(done.error, "gone")

    async def test_picking_another_pack_does_not_block_this_packs_undo(self) -> None:
        done = await self.pick([0])
        await self.sql(
            "INSERT INTO web_rule_takes (guild_id, campaign_id, user_id, taken, kind, created_at)"
            " SELECT guild_id, campaign_id, user_id,"
            " jsonb_set(taken, '{before,set_id}', to_jsonb(%s::text)), 'pack', created_at"
            " FROM web_rule_takes WHERE id = %s",
            "b" * 32,
            int(done.result_id),
        )  # fmt: skip  (a newer pick of another pack)
        await pack_updates.queue_undo(self.web, self.session, self.cref(), str(done.result_id))
        (undone,) = await self.drain()
        self.assertEqual((undone.error, undone.counts), ("", "undone:0"))

    async def test_a_newer_pick_of_the_same_pack_makes_the_older_undo_too_late(self) -> None:
        done = await self.pick([0])
        await self.sql(
            "INSERT INTO web_rule_takes (guild_id, campaign_id, user_id, taken, kind, created_at)"
            " SELECT guild_id, campaign_id, user_id, taken, 'pack', created_at"
            " FROM web_rule_takes WHERE id = %s",
            int(done.result_id),
        )  # fmt: skip
        await pack_updates.queue_undo(self.web, self.session, self.cref(), str(done.result_id))
        (late,) = await self.drain()
        self.assertEqual(late.error, "too_late")

    async def test_another_dms_pick_cannot_be_undone_and_the_handle_expires(self) -> None:
        done = await self.pick([0])
        await self.sql("UPDATE web_rule_takes SET user_id = %s", BOB.id)
        await pack_updates.queue_undo(self.web, self.session, self.cref(), str(done.result_id))
        (other,) = await self.drain()
        self.assertEqual(other.error, "gone")
        await self.sql("UPDATE web_rule_takes SET user_id = %s, created_at = 5", ALICE.id)
        await pack_updates.queue_undo(self.web, self.session, self.cref(), str(done.result_id))
        (late,) = await self.drain()
        self.assertEqual(late.error, "too_late")

    async def test_the_answer_says_how_many_were_kept_and_left_out(self) -> None:
        from dmbot.web import relationships

        answer = await pack_updates.queue_pick(
            self.web, self.session, self.cref(), {"setId": SET_A, "version": 2, "kept": [0]}
        )
        await self.drain()
        read = await relationships.read_answer(self.web, self.session, self.cref(), answer)
        self.assertEqual((read["state"], read["kept"], read["skipped"]), ("done", 1, 0))
        self.assertIsNotNone(read["take"])

    async def test_a_bad_pick_body_is_refused_before_anything_is_queued(self) -> None:
        bodies: list[object] = [
            {}, {"setId": SET_A, "version": 2}, {"setId": "x", "version": 2, "kept": []},
            {"setId": SET_A, "version": 2, "kept": [50]}, {"setId": SET_A, "version": 2,
             "kept": [True]}, {"setId": SET_A, "version": 2, "kept": list(range(51))}, 5,
        ]  # fmt: skip
        for body in bodies:
            with self.assertRaises(RecapError) as err:
                await pack_updates.queue_pick(self.web, self.session, self.cref(), body)
            self.assertEqual(err.exception.code, "bad_action")
        self.assertEqual(await self.drain(), [])


def pack_updates_lines() -> list[str]:
    return [pack_diff.describe(c) for c in pack_diff.diff(OLD, NEW)]
