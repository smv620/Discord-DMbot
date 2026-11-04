"""The bot's half of the website's "These are separate names", "Yes, that group", "They're a
group" and their Undo (#3406, #3383): the same store calls as Discord's buttons (#3403, #3404),
the finished answer kept as a `done` row with its batch for the Undo, and the fixed refusal
words. Invented names only."""

from __future__ import annotations

import time
import uuid
from typing import Any

from dmbot.memory.models import CONFIRMED, PROPOSED
from dmbot.recaps import name_answers as na
from tests.test_web_api import ALICE
from tests.test_web_name_checks import GUILD, WithChecks


class SplitAndGroup(WithChecks):
    async def waiting(self, name: str, kind: str = "place") -> str:
        written = await self.memory.add_entity(
            GUILD, self.one.id, type=kind, name=name, source="scan", description="Heard 2 times"
        )
        return written.value.id

    async def queue(self, entity: str, action: str, *, target: str = "", text: str = "") -> None:
        async with self.db.guild(GUILD) as conn:
            await conn.execute(
                "INSERT INTO name_answers (guild_id, campaign_id, id, entity_id, user_id, action,"
                " target_id, text, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    GUILD, self.one.id, uuid.uuid4().hex, entity, ALICE.id, action, target, text,
                    int(time.time()),
                ),
            )  # fmt: skip

    async def apply_one(self, dms: Any = None) -> str:
        """The bot's loop for one answer, as `run_name_answers` does it."""
        answer = await self.answers.claim_next(GUILD)
        assert answer is not None
        kept = na.Kept()
        why = await na.apply(
            self.memory, GUILD, dms if dms is not None else {ALICE.id}, answer, kept, self.answers
        )
        stays = answer.action in na.KEPT_ACTIONS and kept.batch is not None
        await self.answers.finish(GUILD, answer, error=why, kept=kept if stays else None)
        return why

    async def rows(self) -> list[dict[str, Any]]:
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                "SELECT action, state, batch, undo_name, note, error FROM name_answers"
            )
            return list(await cur.fetchall())

    async def waiting_names(self) -> list[str]:
        found = await self.memory.entities(GUILD, self.one.id, statuses=[PROPOSED])
        return sorted(e.name for e in found)

    async def test_a_split_makes_a_question_for_each_part_and_stays_for_its_undo(self) -> None:
        listed = await self.waiting("Aldmoor, Quillgate, Fenwick")
        await self.queue(listed, "split")
        self.assertEqual(await self.apply_one(), "")
        self.assertEqual(await self.waiting_names(), ["Aldmoor", "Fenwick", "Quillgate"])
        [row] = await self.rows()
        self.assertEqual((row["action"], row["state"]), ("split", "done"))
        self.assertEqual(row["note"], "Split into 3 names.")
        self.assertEqual(row["undo_name"], "Aldmoor, Quillgate, Fenwick")
        self.assertIsNotNone(row["batch"])

    async def test_undo_brings_the_list_back_and_cannot_be_asked_twice(self) -> None:
        listed = await self.waiting("Aldmoor, Quillgate, Fenwick")
        await self.queue(listed, "split")
        await self.apply_one()
        [row] = await self.rows()
        await self.queue(listed, "undo", target=str(row["batch"]))
        self.assertEqual(await self.apply_one(), "")
        self.assertEqual(await self.waiting_names(), ["Aldmoor, Quillgate, Fenwick"])
        self.assertEqual(await self.rows(), [])  # the answer and its undo are both gone
        await self.queue(listed, "undo", target=str(row["batch"]))
        self.assertEqual(await self.apply_one(), na.NOT_FOUND_WORD)

    async def test_undo_after_a_part_was_answered_is_refused(self) -> None:
        listed = await self.waiting("Aldmoor, Quillgate, Fenwick")
        await self.queue(listed, "split")
        await self.apply_one()
        [row] = await self.rows()
        [part] = [
            e
            for e in await self.memory.entities(GUILD, self.one.id, statuses=[PROPOSED])
            if e.name == "Aldmoor"
        ]
        await self.memory.confirm_entity(GUILD, self.one.id, part.id, "place", source="dm")
        await self.queue(listed, "undo", target=str(row["batch"]))
        self.assertEqual(await self.apply_one(), na.ANSWERED)

    async def test_make_group_and_its_undo(self) -> None:
        listed = await self.waiting("Tavin and Mira", "character")
        await self.queue(listed, "make_group", text="The Ash Wolves")
        self.assertEqual(await self.apply_one(), "")
        [row] = await self.rows()
        self.assertEqual((row["action"], row["state"]), ("make_group", "done"))
        self.assertEqual(row["note"], "Made the group The Ash Wolves.")
        self.assertIsNone(row["undo_name"])
        self.assertNotIn("Tavin and Mira", await self.waiting_names())
        await self.queue(listed, "undo", target=str(row["batch"]))
        self.assertEqual(await self.apply_one(), "")
        self.assertIn("Tavin and Mira", await self.waiting_names())

    async def test_join_group_adds_the_list_to_a_known_group(self) -> None:
        async def entity(name: str, kind: str, status: str) -> str:
            written = await self.memory.add_entity(
                GUILD, self.one.id, type=kind, name=name, source="dm", status=status
            )
            return written.value.id

        group = await entity("The Ash Wolves", "faction", CONFIRMED)
        for member in ("Tavin", "Mira"):
            mid = await entity(member, "character", CONFIRMED)
            await self.memory.add_relation(
                GUILD, self.one.id, mid, "member_of", group, source="dm", confidence=1.0,
                status=CONFIRMED,
            )  # fmt: skip
        listed = await self.waiting("Tavin and Mira", "character")
        await self.queue(listed, "join_group", target=group)
        self.assertEqual(await self.apply_one(), "")
        [row] = await self.rows()
        self.assertEqual(row["note"], "Added to the group The Ash Wolves.")

    async def test_the_refusals_are_the_fixed_words(self) -> None:
        one = await self.waiting("Ironhold")
        await self.queue(one, "split")
        self.assertEqual(await self.apply_one(), na.NOT_A_LIST_WORD)
        places = await self.waiting("Aldmoor, Quillgate", "place")
        await self.queue(places, "make_group", text="The Two Towns")
        self.assertEqual(await self.apply_one(), na.NOT_A_LIST_WORD)  # no group of places
        people = await self.waiting("Tavin and Mira", "character")
        await self.queue(people, "join_group", target="f" * 32)
        self.assertEqual(
            await self.apply_one(), na.NOT_FOUND_WORD
        )  # not a group the campaign knows
        await self.memory.add_entity(
            GUILD, self.one.id, type="faction", name="The Ash Wolves", source="dm",
            status=CONFIRMED,
        )  # fmt: skip
        await self.queue(people, "make_group", text="The Ash Wolves")
        self.assertEqual(await self.apply_one(), na.SPELLING_TAKEN_WORD)

    async def test_a_sender_who_is_no_longer_a_dm_is_refused(self) -> None:
        listed = await self.waiting("Aldmoor, Quillgate, Fenwick")
        await self.queue(listed, "split")
        self.assertEqual(await self.apply_one(dms=set()), na.NOT_DM)
        self.assertEqual(await self.waiting_names(), ["Aldmoor, Quillgate, Fenwick"])

    async def test_a_group_hidden_from_the_players_is_not_joined(self) -> None:
        listed = await self.waiting("Tavin and Mira", "character")
        hidden = (
            await self.memory.add_entity(
                GUILD, self.one.id, type="faction", name="The Ash Wolves", source="dm",
                status=CONFIRMED, hidden_from_players=True,
            )
        ).value.id  # fmt: skip
        await self.queue(listed, "join_group", target=hidden)
        self.assertEqual(await self.apply_one(), na.NOT_FOUND_WORD)
        self.assertEqual([r["state"] for r in await self.rows()], ["failed"])  # nothing was kept
        self.assertIn("Tavin and Mira", await self.waiting_names())

    async def test_a_group_with_a_secret_spelling_is_joined_as_in_discord(self) -> None:
        listed = await self.waiting("Tavin and Mira", "character")
        group = (
            await self.memory.add_entity(
                GUILD, self.one.id, type="faction", name="The Ash Wolves", source="dm",
                status=CONFIRMED,
            )
        ).value.id  # fmt: skip
        await self.memory.add_alias(
            GUILD, self.one.id, group, "The Grey Hand", kind="full", source="dm", secret=True
        )
        await self.queue(listed, "join_group", target=group)
        self.assertEqual(await self.apply_one(), "")
        [row] = await self.rows()
        self.assertEqual(row["state"], "done")

    async def test_a_waiting_name_with_a_secret_spelling_is_not_split(self) -> None:
        listed = await self.waiting("Aldmoor, Quillgate, Fenwick")
        await self.memory.add_alias(
            GUILD, self.one.id, listed, "The Hidden Three", kind="full", source="dm", secret=True
        )
        await self.queue(listed, "split")
        self.assertEqual(await self.apply_one(), na.GONE)
        self.assertEqual(await self.waiting_names(), ["Aldmoor, Quillgate, Fenwick"])

    async def test_another_names_batch_cannot_be_undone_by_this_one(self) -> None:
        first = await self.waiting("Aldmoor, Quillgate, Fenwick")
        other = await self.waiting("Brinwick, Corrow, Dunmere")
        await self.queue(first, "split")
        await self.apply_one()
        [row] = await self.rows()
        await self.queue(other, "undo", target=str(row["batch"]))
        self.assertEqual(await self.apply_one(), na.NOT_FOUND_WORD)
        self.assertIn("Aldmoor", await self.waiting_names())  # the split stands

    async def test_a_real_split_and_its_undo_go_through_the_door_end_to_end(self) -> None:
        """Nothing is put in by hand: the page's door queues the split, the bot applies it, the
        door takes the Undo with the batch the split left, the bot applies that, and the list is
        waiting again. Proves the recap's link to the list survives a real split, which the
        door's Undo check joins on (#3406)."""
        listed = await self.suggest("Aldmoor, Quillgate, Fenwick")
        await self.ask(listed, action="split")
        self.assertEqual(await self.apply_one(), "")
        self.assertEqual(await self.waiting_names(), ["Aldmoor", "Fenwick", "Quillgate"])
        [row] = await self.rows()
        self.assertEqual(row["state"], "done")
        await self.ask(listed, action="undo", batch=int(row["batch"]))
        self.assertEqual(await self.apply_one(), "")
        self.assertEqual(await self.waiting_names(), ["Aldmoor, Quillgate, Fenwick"])
        self.assertEqual(await self.rows(), [])
        # and the same Undo cannot be asked again
        self.assertEqual(
            await self.code(self.ask(listed, action="undo", batch=int(row["batch"]))), "not_found"
        )

    async def test_the_new_names_belong_to_the_same_recap_as_the_list(self) -> None:
        """Without a pointer to the recap's name checks the page's list would not show them."""
        listed = await self.suggest("Aldmoor, Quillgate, Fenwick")
        await self.ask(listed, action="split")
        self.assertEqual(await self.apply_one(), "")
        names = (await self.listed())["names"]
        self.assertEqual(sorted(n["name"] for n in names), ["Aldmoor", "Fenwick", "Quillgate"])

    async def test_a_failed_link_does_not_lose_the_answer_or_its_undo(self) -> None:
        from unittest import mock

        listed = await self.suggest("Aldmoor, Quillgate, Fenwick")
        await self.ask(listed, action="split")
        with mock.patch.object(
            na.AnswerStore, "link_like", side_effect=RuntimeError("the database blinked")
        ):
            self.assertEqual(await self.apply_one(), "")
        [row] = await self.rows()
        self.assertEqual((row["state"], row["note"]), ("done", "Split into 3 names."))
        self.assertIsNotNone(row["batch"])
        await self.ask(listed, action="undo", batch=int(row["batch"]))
        self.assertEqual(await self.apply_one(), "")
        self.assertEqual(await self.waiting_names(), ["Aldmoor, Quillgate, Fenwick"])
