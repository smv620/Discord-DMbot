"""The website's "Names that look alike" presses (#5259, migration 0624): 🔗 Merge runs the store's
own `merge_many` as the DM with one Undo, Not the same is remembered so the pair is never grouped
again, the names the DM left unticked stay apart from the kept one, and every shape and every
other campaign is refused. Invented names. Needs Postgres."""

from __future__ import annotations

from typing import Any

from dmbot.memory.models import CONFIRMED
from dmbot.web import look_alikes
from dmbot.web.recaps import RecapError
from tests.test_web_thread_state import ThreadWeb


class Actions(ThreadWeb):
    async def name(self, name: str, *, campaign: str | None = None) -> str:
        written = await self.memory.add_entity(
            self.g, campaign or self.one.id, type="character", name=name, source="dm",
            status=CONFIRMED, role="npc",
        )  # fmt: skip
        return written.value.id

    async def groups(self) -> list[list[str]]:
        got = await look_alikes.read_groups(self.web, self.session, self.cref)
        return [[n["id"] for n in g] for g in got["groups"]]


class Merging(Actions):
    async def test_a_merge_is_the_stores_own_with_one_undo_that_puts_it_back(self) -> None:
        a = await self.name("Belleros")
        b = await self.name("Belerros")
        await look_alikes.queue_merge(
            self.web, self.session, self.cref, {"keep": a, "others": [b], "left": []}
        )
        (outcome,) = await self.bot_loop()
        self.assertFalse(outcome.error)
        (batch,) = outcome.batches
        gone = await self.memory.entity(self.g, self.one.id, b)
        assert gone is not None
        self.assertEqual(gone.merged_into, a)
        self.assertEqual(await self.groups(), [])
        await look_alikes.queue_undo(self.web, self.session, self.cref, str(batch))
        (undone,) = await self.bot_loop()
        self.assertFalse(undone.error)
        back = await self.memory.entity(self.g, self.one.id, b)
        assert back is not None
        self.assertIsNone(back.merged_into)
        self.assertEqual({a, b}, set((await self.groups())[0]))

    async def test_an_unticked_name_stays_apart_from_the_kept_one(self) -> None:
        a = await self.name("Belleros")
        b = await self.name("Belerros")
        c = await self.name("Belleross")
        await look_alikes.queue_merge(
            self.web, self.session, self.cref, {"keep": a, "others": [b], "left": [c]}
        )
        (outcome,) = await self.bot_loop()
        self.assertFalse(outcome.error)
        self.assertEqual(await self.groups(), [])  # (c is not offered with a again)
        kept = await self.memory.entity(self.g, self.one.id, c)
        assert kept is not None
        self.assertIsNone(kept.merged_into)

    async def test_another_campaigns_name_is_refused_at_the_door(self) -> None:
        a = await self.name("Belleros")
        other = await self.name("Belerros", campaign=self.two.id)
        with self.assertRaises(RecapError) as refused:
            await look_alikes.queue_merge(
                self.web, self.session, self.cref, {"keep": a, "others": [other]}
            )
        self.assertEqual(refused.exception.code, "bad_target")

    async def test_the_shapes_are_checked(self) -> None:
        a = await self.name("Belleros")
        bad: list[Any] = [
            None, {}, {"keep": "x", "others": [a]}, {"keep": a, "others": []},
            {"keep": a, "others": [a]}, {"keep": a, "others": ["nope"]},
            {"keep": a, "others": [a[:-1] + "0", a[:-1] + "0"]},
        ]  # fmt: skip
        for body in bad:
            with self.assertRaises(RecapError, msg=str(body)):
                await look_alikes.queue_merge(self.web, self.session, self.cref, body)

    async def test_a_player_cannot_undo_a_number_they_made_up(self) -> None:
        await look_alikes.queue_undo(self.web, self.session, self.cref, "999999")
        (outcome,) = await self.bot_loop()
        self.assertEqual(outcome.error, "gone")
        with self.assertRaises(RecapError):
            await look_alikes.queue_undo(self.web, self.session, self.cref, "1; drop")


class NotTheSame(Actions):
    async def test_not_the_same_is_remembered_so_the_pair_is_never_grouped_again(self) -> None:
        a = await self.name("Belleros")
        b = await self.name("Belerros")
        self.assertEqual({a, b}, set((await self.groups())[0]))
        await look_alikes.queue_apart(self.web, self.session, self.cref, {"ids": [a, b]})
        (outcome,) = await self.bot_loop()
        self.assertFalse(outcome.error)
        self.assertEqual(await self.groups(), [])

    async def test_one_name_or_a_stranger_is_refused(self) -> None:
        a = await self.name("Belleros")
        with self.assertRaises(RecapError):
            await look_alikes.queue_apart(self.web, self.session, self.cref, {"ids": [a]})
        other = await self.name("Belerros", campaign=self.two.id)
        await look_alikes.queue_apart(self.web, self.session, self.cref, {"ids": [a, other]})
        (outcome,) = await self.bot_loop()
        self.assertEqual(outcome.error, "not_found")
