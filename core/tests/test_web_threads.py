"""The story threads DMbot found in an upload, on the website (#4457, quests slice 1, core): a
database door that answers a DM of the campaign and nobody else, with the people of each
suggestion by name, and three queue actions (✅ Keep with the people left on, 🚫, ↩ Undo) that the
bot applies through the same store calls as Discord's 📜 card, each keep one Undo. Invented names.
Needs Postgres."""

from __future__ import annotations

from typing import Any

from dmbot.consent import TERMS_VERSION
from dmbot.memory import thread_parts as tp
from dmbot.memory import web_actions as wa
from dmbot.memory.thread_read import ReadBranch, ReadPart, Suggestion
from dmbot.memory.thread_suggestions import ThreadSuggestionStore
from dmbot.transcript.models import Line as Heard
from dmbot.transcript.store import TranscriptStore
from dmbot.web import thread_suggestions as web_threads
from dmbot.web.recaps import RecapError, _scoped
from tests.agreed import agree
from tests.test_recap_web import BOB
from tests.test_web_api import ALICE, THURSDAY
from tests.test_web_name_actions import Acting

THREAD = Suggestion(
    "quest", "Find the Lost Heir", "Bring the heir home.", "Find the heir.", (), "now"
)


class Threads(Acting):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.threads = ThreadSuggestionStore(self.db)
        found = Suggestion(
            "quest", "The Lost Heir", "Bring the heir home.", "Find the heir.",
            (("quest giver", self.mira), ("target", self.keep)), "now",
        )  # fmt: skip
        await self.threads.offer(self.g, self.one.id, [found], "Frostmaiden notes", 1_000)

    async def door(self, who: Any = None) -> Any:
        session = who or self.session
        async with self.web.user(session.user_id, session=session.id_hash) as conn:
            await _scoped(conn, self.g)
            cur = await conn.execute(
                "SELECT dmbot_web_threads(%s, %s, %s, %s) AS r",
                (self.g, self.one.id, TERMS_VERSION, None),
            )
            found = await cur.fetchone()
        return found["r"] if found else None

    async def waiting(self) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = (await self.door())["suggestions"]
        return found

    async def add_action(self, action: str, text: str) -> Any:
        async with self.web.user(self.session.user_id, session=self.session.id_hash) as conn:
            await _scoped(conn, self.g)
            cur = await conn.execute(
                "SELECT dmbot_web_name_action_add(%s, %s, %s, %s, %s, %s, %s, %s) AS r",
                (self.g, self.one.id, None, action, "", "", text, False),
            )
            found = await cur.fetchone()
        return found["r"] if found else None

    async def act(self, action: str, text: str) -> wa.Outcome:
        got = await self.add_action(action, text)
        self.assertEqual(got["status"], "ok", got)
        claimed = await self.actions.claim_next(self.g)
        assert claimed is not None
        outcome = await wa.apply(self.memory, self.g, {ALICE.id}, claimed, actions=self.actions)
        await self.actions.finish(
            self.g, claimed, error=outcome.error, result_batches=outcome.batches
        )
        return outcome


class TheDoor(Threads):
    async def test_the_dm_sees_each_suggestion_with_its_people_by_name(self) -> None:
        (one,) = await self.waiting()
        self.assertEqual(
            (one["kind"], one["name"], one["aim"], one["quote"], one["upload"]),
            (
                "quest",
                "The Lost Heir",
                "Bring the heir home.",
                "Find the heir.",
                "Frostmaiden notes",
            ),
        )
        self.assertEqual(
            [(p["index"], p["role"]) for p in one["people"]], [(0, "quest giver"), (1, "target")]
        )
        self.assertTrue(all(p["name"] for p in one["people"]))
        self.assertNotIn(self.mira, str(one))  # (names, never entry ids)

    async def test_a_player_or_a_stranger_gets_nothing(self) -> None:
        # (Bob is a player here)
        self.assertIsNone(await self.door(await self.signed(BOB, THURSDAY)))

    async def test_a_person_gone_since_is_left_out_and_the_others_keep_their_places(
        self,
    ) -> None:
        await self.sql(
            "DELETE FROM memory_entities WHERE guild_id = %s AND id = %s", self.g, self.mira
        )
        (one,) = await self.waiting()
        self.assertEqual([p["index"] for p in one["people"]], [1])

    async def test_an_answered_suggestion_is_not_listed(self) -> None:
        (one,) = await self.waiting()
        self.assertEqual((await self.act("thread-not", one["id"])).error, "")
        self.assertEqual(await self.waiting(), [])

    async def test_only_the_action_shapes_are_taken(self) -> None:
        (one,) = await self.waiting()
        sid = one["id"]
        for action, text in (
            ("thread-keep", sid),  # (no people word)
            ("thread-keep", f"{sid} 0,x"),
            ("thread-not", f"{sid} extra"),
            ("thread-undo", "7"),
            ("thread-undo", f"keep {sid}"),
        ):
            got = await self.add_action(action, text)
            self.assertNotEqual(got["status"], "ok", (action, text))


class TheLines(Threads):
    """What is in a suggestion (#4501, migration 0517): its steps, rewards and branches in words,
    and a keep that names the ones left on."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        rich = Suggestion(
            "quest", "The Second Heir", "Bring her home.", "Find the heir.", (), "now",
            reads=(
                ReadPart(tp.OBJECTIVE, "Find the heir", "to_do", "Find the heir."),
                ReadPart(tp.REWARD, "100 gold", "owed", "100 gold.", self.mira, "afterwards"),
            ),
            branches=(ReadBranch("mystery", "Who took her?", "Who took her?", "x", "hinted"),),
        )  # fmt: skip
        await self.threads.offer(self.g, self.one.id, [rich], "more notes", 2_000)

    async def second(self) -> dict[str, Any]:
        return next(s for s in await self.waiting() if s["name"] == "The Second Heir")

    async def test_the_lines_come_in_the_cards_own_words_by_position(self) -> None:
        got = await web_threads.read(self.web, self.session, self.cref)
        mine = next(s for s in got["suggestions"] if s["name"] == "The Second Heir")
        lines = mine["lines"]
        self.assertEqual(
            [(line["words"], line["branch"]) for line in lines],
            [("Find the heir", False), ("100 gold", False), ("Who took her?", True)],
        )
        self.assertEqual(lines[0]["what"], "Step")
        self.assertTrue(lines[1]["what"].startswith("Reward from "), lines[1])
        self.assertTrue(lines[2]["what"].startswith("🌿 Branch, just a hint"), lines[2])
        self.assertIn(", when the job's done", lines[1]["what"])
        self.assertNotIn(self.mira, str(mine))  # (names, never entry ids)

    async def test_a_suggestion_with_no_lines_has_an_empty_list(self) -> None:
        got = await web_threads.read(self.web, self.session, self.cref)
        lost = next(s for s in got["suggestions"] if s["name"] == "The Lost Heir")
        self.assertEqual(lost["lines"], [])

    async def test_a_keep_with_the_lines_left_on_keeps_those_and_not_the_others(self) -> None:
        sid = (await self.second())["id"]
        done = await self.act("thread-keep", f"{sid} - +0,2")
        self.assertEqual(done.error, "")
        parts, _, _, branches = await self.memory.thread_card(self.g, self.one.id, done.result_id)
        self.assertEqual([p.words for p in parts], ["Find the heir"])  # (position 1 was off)
        self.assertEqual(len(branches), 1)

    async def test_a_keep_with_the_lines_and_a_name_keeps_both(self) -> None:
        sid = (await self.second())["id"]
        done = await self.act("thread-keep", f"{sid} - +1 My Own Name")
        self.assertEqual(done.error, "")
        parts, _, _, branches = await self.memory.thread_card(self.g, self.one.id, done.result_id)
        self.assertEqual(([p.words for p in parts], branches), (["100 gold"], []))
        thread = await self.memory.entity(self.g, self.one.id, done.result_id)
        self.assertEqual(thread.name if thread else "", "My Own Name")

    async def test_no_lines_word_or_none_keeps_none(self) -> None:
        for n, text in enumerate(("{sid} -", "{sid} - +-")):
            await self.threads.offer(
                self.g, self.one.id,
                [Suggestion("quest", f"Heir {n}", "a", "q", (), "now",
                            reads=(ReadPart(tp.OBJECTIVE, "Step", "to_do", "q"),))],
                "x", 3_000 + n,
            )  # fmt: skip
            sid = next(s["id"] for s in await self.waiting() if s["name"] == f"Heir {n}")
            done = await self.act("thread-keep", text.format(sid=sid))
            parts, _, _, _ = await self.memory.thread_card(self.g, self.one.id, done.result_id)
            self.assertEqual(parts, [], text)

    async def test_one_undo_takes_back_the_thread_and_its_lines(self) -> None:
        sid = (await self.second())["id"]
        (batch,) = (await self.act("thread-keep", f"{sid} - +0,1,2")).batches
        self.assertEqual((await self.act("thread-undo", f"keep {batch}")).error, "")
        self.assertEqual((await self.second())["id"], sid)  # (waiting again)

    async def test_the_lines_door_gives_a_player_nothing(self) -> None:
        bob = await self.signed(BOB, THURSDAY)
        async with self.web.user(bob.user_id, session=bob.id_hash) as conn:
            await _scoped(conn, self.g)
            cur = await conn.execute(
                "SELECT dmbot_web_thread_lines(%s, %s) AS r", (self.g, self.one.id)
            )
            found = await cur.fetchone()
        self.assertIsNone(found and found["r"])

    async def test_a_name_that_looks_like_a_lines_word_is_a_name_and_keeps_no_lines(self) -> None:
        sid = (await self.second())["id"]
        await web_threads.queue_keep(
            self.web, self.session, self.cref, {"id": sid, "on": [], "name": "+1 Sword Quest"}
        )
        claimed = await self.actions.claim_next(self.g)
        assert claimed is not None
        done = await wa.apply(self.memory, self.g, {ALICE.id}, claimed, actions=self.actions)
        await self.actions.finish(self.g, claimed, error=done.error, result_batches=done.batches)
        self.assertEqual(done.error, "")
        thread = await self.memory.entity(self.g, self.one.id, done.result_id)
        self.assertEqual(thread.name if thread else "", "+1 Sword Quest")
        parts, _, _, branches = await self.memory.thread_card(self.g, self.one.id, done.result_id)
        self.assertEqual((parts, branches), ([], []))

    async def test_the_page_names_for_lines_are_checked_before_anything_is_queued(self) -> None:
        for lines in ("0", [True], [0, 0], [100], [-1], list(range(51))):
            with self.assertRaises(RecapError, msg=str(lines)):
                await web_threads.queue_keep(
                    self.web, self.session, self.cref, {"id": "a" * 32, "on": [], "lines": lines}
                )
        sid = (await self.second())["id"]
        self.assertTrue(
            await web_threads.queue_keep(
                self.web, self.session, self.cref, {"id": sid, "on": [], "lines": [0, 2]}
            )
        )


LINE_MS = 5_000
SAY, ALSO, OUTSIDER = 71, 72, 73


class TheSessions(Threads):
    """A suggestion read from a session (#4526, migration 0518): listed only while every one of its
    speakers is recorded now, with where it was heard; a lore suggestion is as before."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        await agree(self.db, self.g, SAY, ALSO)
        self.transcripts = TranscriptStore(self.db)
        self.sid = await self.transcripts.open_session(self.g, self.one.id, 1_700_000_000)
        await self.transcripts.add_lines(
            self.g, self.sid,
            [Heard(LINE_MS, SAY, "x", "Keegan  wrings his cap."),
             Heard(LINE_MS + 1_000, ALSO, "x", "We'll find him."),
             Heard(LINE_MS + 2_000, OUTSIDER, "x", "Pass me that rope.")],
        )  # fmt: skip
        heard = Suggestion(
            "quest", "Heard Heir", "find the heir", "", (("quest giver", self.mira),), "now",
            line_starts=(LINE_MS, LINE_MS + 1_000, LINE_MS + 2_000, LINE_MS + 9_999),
            speaker_ids=(SAY, ALSO),
        )  # fmt: skip
        made = await self.threads.offer(
            self.g, self.one.id, [heard], "the session of 14 Nov 2026", 5,
            session_id=self.sid, session_at=1_700_000_000,
        )  # fmt: skip
        self.assertEqual(made, 1)

    async def listed(self) -> dict[str, Any]:
        got = await web_threads.read(self.web, self.session, self.cref)
        return {s["name"]: s for s in got["suggestions"]}

    async def test_a_session_suggestion_says_where_it_was_heard_in_its_speakers_lines(self) -> None:
        one = (await self.listed())["Heard Heir"]
        self.assertTrue(one["session"])
        self.assertEqual(one["upload"], "the session of 14 Nov 2026")
        # (only the speakers' own lines, in the order spoken, spaces tidied; a line of someone who
        # is not a speaker, and a start no line has, are not there)
        self.assertEqual(one["heard"], ["Keegan wrings his cap.", "We'll find him."])
        self.assertEqual(one["quote"], "")

    async def test_a_lore_suggestion_is_not_from_a_session_and_has_no_heard_lines(self) -> None:
        lost = (await self.listed())["The Lost Heir"]
        self.assertEqual((lost["session"], lost["heard"]), (False, []))

    async def test_it_goes_when_one_speaker_stops_being_recorded_and_comes_back_with_them(
        self,
    ) -> None:
        await self.sql("DELETE FROM consent WHERE guild_id = %s AND user_id = %s", self.g, ALSO)
        listed = await self.listed()
        self.assertEqual(sorted(listed), ["The Lost Heir"])  # (the lore one stays)
        await agree(self.db, self.g, ALSO)
        self.assertIn("Heard Heir", await self.listed())

    async def test_an_older_wording_does_not_count_as_recorded(self) -> None:
        await self.sql(
            "UPDATE consent SET terms_version = %s WHERE guild_id = %s AND user_id = %s",
            TERMS_VERSION - 1, self.g, SAY,
        )  # fmt: skip
        self.assertNotIn("Heard Heir", await self.listed())

    async def test_while_an_outside_company_hears_only_those_recorded_for_it_count(self) -> None:
        got = await web_threads.read(self.web, self.session, self.cref, outside_engine="acme")
        self.assertEqual([s["name"] for s in got["suggestions"]], ["The Lost Heir"])
        await self.sql(
            "UPDATE consent SET outside_to = 'acme' WHERE guild_id = %s AND user_id IN (%s, %s)",
            self.g, SAY, ALSO,
        )  # fmt: skip
        got = await web_threads.read(self.web, self.session, self.cref, outside_engine="acme")
        self.assertIn("Heard Heir", [s["name"] for s in got["suggestions"]])

    async def test_a_line_deleted_since_is_not_shown(self) -> None:
        await self.sql(
            "DELETE FROM transcript_lines WHERE guild_id = %s AND started_ms = %s",
            self.g, LINE_MS + 1_000,
        )  # fmt: skip
        self.assertEqual((await self.listed())["Heard Heir"]["heard"], ["Keegan wrings his cap."])

    async def test_a_session_of_another_campaign_is_never_read(self) -> None:
        other = await self.transcripts.open_session(self.g, self.two.id, 1_700_000_500)
        await self.transcripts.add_lines(self.g, other, [Heard(LINE_MS, SAY, "x", "Not here.")])
        await self.sql(
            "UPDATE thread_suggestions SET session_id = %s WHERE guild_id = %s AND name = %s",
            other, self.g, "Heard Heir",
        )  # fmt: skip
        self.assertEqual((await self.listed())["Heard Heir"]["heard"], [])

    async def test_a_player_still_gets_nothing(self) -> None:
        self.assertIsNone(await self.door(await self.signed(BOB, THURSDAY)))


class TheActions(Threads):
    async def test_keep_with_one_person_left_on_is_one_undo(self) -> None:
        (one,) = await self.waiting()
        outcome = await self.act("thread-keep", f"{one['id']} 0 My Own Name")
        self.assertEqual(outcome.error, "")
        self.assertEqual(await self.waiting(), [])
        (batch,) = outcome.batches
        self.assertEqual((await self.act("thread-undo", f"keep {batch}")).error, "")
        (back,) = await self.waiting()
        self.assertEqual(back["id"], one["id"])

    async def test_a_keep_on_the_website_keeps_none_of_the_lines_it_didnt_show(self) -> None:
        """#4500: the website shows no steps, rewards or branches yet, so its keep keeps none."""
        with_lines = Suggestion(
            "quest", "The Second Heir", "Bring her home.", "Find the heir.", (), "now",
            reads=(ReadPart(tp.OBJECTIVE, "find the heir", "to_do", "Find the heir."),),
            branches=(ReadBranch("mystery", "Who took her?", "Who took her?", "x", "hinted"),),
        )  # fmt: skip
        await self.threads.offer(self.g, self.one.id, [with_lines], "more notes", 2_000)
        sid = next(s["id"] for s in await self.waiting() if s["name"] == "The Second Heir")
        done = await self.act("thread-keep", f"{sid} -")
        self.assertEqual(done.error, "")
        parts, _, _, branches = await self.memory.thread_card(self.g, self.one.id, done.result_id)
        self.assertEqual((parts, branches), ([], []))

    async def test_a_keep_whose_name_the_store_refuses_is_not_called_gone(self) -> None:
        (one,) = await self.waiting()
        got = await self.act("thread-keep", f"{one['id']} 0 @@@")
        self.assertIn(got.error, (wa.BAD_TARGET, ""))  # (refused as a name, or kept as typed)
        if got.error:
            self.assertEqual(len(await self.waiting()), 1)  # (still waiting: nothing was kept)

    async def test_a_second_undo_of_a_keep_is_done_not_changed(self) -> None:
        (one,) = await self.waiting()
        (batch,) = (await self.act("thread-keep", f"{one['id']} 0")).batches
        self.assertEqual((await self.act("thread-undo", f"keep {batch}")).error, "")
        self.assertEqual((await self.act("thread-undo", f"keep {batch}")).error, "")

    async def test_a_player_cannot_queue_a_thread_action(self) -> None:
        (one,) = await self.waiting()
        bob = await self.signed(BOB, THURSDAY)
        async with self.web.user(bob.user_id, session=bob.id_hash) as conn:
            await _scoped(conn, self.g)
            cur = await conn.execute(
                "SELECT dmbot_web_name_action_add(%s, %s, %s, %s, %s, %s, %s, %s) AS r",
                (self.g, self.one.id, None, "thread-not", "", "", one["id"], False),
            )
            found = await cur.fetchone()
        self.assertIsNone(found and found["r"])  # (the door answers NULL: a DM only)

    async def test_keep_with_nobody_left_on_still_keeps_the_thread(self) -> None:
        (one,) = await self.waiting()
        self.assertEqual((await self.act("thread-keep", f"{one['id']} -")).error, "")

    async def test_a_second_keep_or_a_turn_down_after_it_is_gone(self) -> None:
        (one,) = await self.waiting()
        self.assertEqual((await self.act("thread-keep", f"{one['id']} 0,1")).error, "")
        self.assertEqual((await self.act("thread-keep", f"{one['id']} 0,1")).error, wa.GONE)
        self.assertEqual((await self.act("thread-not", one["id"])).error, wa.GONE)

    async def test_not_a_story_thread_is_never_offered_again_and_undo_puts_it_back(self) -> None:
        (one,) = await self.waiting()
        self.assertEqual((await self.act("thread-not", one["id"])).error, "")
        await self.threads.offer(self.g, self.one.id, [THREAD], "again", 2_000)
        (other,) = await self.waiting()  # (only the new one: the turned-down one stays out)
        self.assertNotEqual(other["id"], one["id"])
        self.assertEqual((await self.act("thread-undo", f"not {one['id']}")).error, "")
        self.assertEqual({s["id"] for s in await self.waiting()}, {one["id"], other["id"]})

    async def test_undo_takes_back_only_this_dms_own_keep(self) -> None:
        (one,) = await self.waiting()
        (batch,) = (await self.act("thread-keep", f"{one['id']} 0")).batches
        await self.sql(  # (a batch another website action of this DM recorded)
            "UPDATE name_actions SET action = 'map-terrain' WHERE guild_id = %s"
            " AND %s = ANY (result_batches)",
            self.g, batch,
        )  # fmt: skip
        self.assertEqual((await self.act("thread-undo", f"keep {batch}")).error, wa.GONE)


class TheWebModule(Threads):
    async def test_the_page_names_are_checked_before_anything_is_queued(self) -> None:
        for body in (
            {},
            {"id": "x", "on": []},
            {"id": "a" * 32, "on": "0"},
            {"id": "a" * 32, "on": [True]},
            {"id": "a" * 32, "on": [0, 0]},
            {"id": "a" * 32, "on": [100]},
            {"id": "a" * 32, "on": [0], "name": "x" * 101},
            {"id": "a" * 32, "on": [0], "name": 7},
        ):
            with self.assertRaises(RecapError, msg=str(body)):
                await web_threads.queue_keep(self.web, self.session, self.cref, body)
        with self.assertRaises(RecapError):
            await web_threads.queue_undo(
                self.web, self.session, self.cref, {"keep": "1", "not": "a"}
            )
