"""World notes' Feelings on the website (#4932, migration 0585, the web half of #4229 slice 4): the
door lists the feelings DMbot read for a DM of the campaign and nobody else, the page's line words
come from the lineage's plural, ✅ and 🚫 take a `bias:` line, ✏️ Change keeps it at the step the DM
picked as one change with one Undo, and every shape is checked. Invented names. Needs Postgres."""

from __future__ import annotations

from typing import Any

from dmbot.memory import bias_read, standing
from dmbot.memory import web_actions as wa
from dmbot.web import sessions, world_notes
from dmbot.web.recaps import RecapError
from tests.test_recap_web import BOB, NOW
from tests.test_web_api import THURSDAY
from tests.test_web_world_notes import WorldNotesDoor


class Feelings(WorldNotesDoor):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        found = [bias_read.Found(self.keep, standing.UNFRIENDLY, lineage="elf")]
        await self.memory.add_read_biases(self.g, self.one.id, found, source="background")

    async def feelings(self) -> list[dict[str, Any]]:
        got = await world_notes.read(self.web, self.session, self.cref)
        return list(got["feelings"])


class TheFeelingsDoor(Feelings):
    async def test_the_dm_gets_each_feeling_with_its_step_and_target(self) -> None:
        (line,) = await self.feelings()
        self.assertTrue(line["id"].startswith("bias:"))
        self.assertEqual(
            (line["entityId"], line["name"], line["target"], line["step"]),
            (self.keep, "Greywater Keep", "elves", standing.UNFRIENDLY),
        )

    async def test_a_player_gets_nothing(self) -> None:
        who: Any = BOB
        bob = await sessions.find(
            self.web, await sessions.sign_in(self.web, who, [THURSDAY], now=NOW, days=30), now=NOW
        )
        assert bob is not None
        with self.assertRaises(RecapError) as err:
            await world_notes.read(self.web, bob, self.cref)
        self.assertEqual(err.exception.code, "not_allowed")

    async def test_a_row_the_dm_answered_is_not_listed(self) -> None:
        (line,) = await self.feelings()
        await self.act("world-keep", line["id"])
        self.assertEqual(await self.feelings(), [])

    async def test_a_turned_down_row_and_a_merged_holder_are_not_listed(self) -> None:
        (line,) = await self.feelings()
        await self.act("world-not", line["id"])
        self.assertEqual(await self.feelings(), [])
        # (a new read on a holder that is merged away afterwards is not listed either)
        other = await self.add("Old Keep", "place")
        found = [bias_read.Found(other, standing.WARY, lineage="orc")]
        await self.memory.add_read_biases(self.g, self.one.id, found, source="background")
        self.assertEqual([f["name"] for f in await self.feelings()], ["Old Keep"])
        await self.sql(
            "UPDATE memory_entities SET merged_into = %s, status = 'merged' WHERE id = %s",
            self.keep, other,
        )  # fmt: skip
        self.assertEqual(await self.feelings(), [])


class TheFeelingsActions(Feelings):
    async def test_keep_and_undo(self) -> None:
        (line,) = await self.feelings()
        outcome = await self.act("world-keep", line["id"])
        self.assertEqual((outcome.error, outcome.counts), ("", "kept:1"))
        self.assertEqual(await self.feelings(), [])
        (batch,) = outcome.batches
        self.assertEqual((await self.act("world-undo", str(batch))).error, "")
        self.assertEqual([f["id"] for f in await self.feelings()], [line["id"]])

    async def test_not_right_takes_it_out_and_a_second_press_is_gone(self) -> None:
        (line,) = await self.feelings()
        self.assertEqual((await self.act("world-not", line["id"])).error, "")
        self.assertEqual(await self.feelings(), [])
        self.assertEqual((await self.act("world-not", line["id"])).error, wa.GONE)

    async def test_change_keeps_it_at_the_picked_step_with_one_undo(self) -> None:
        (line,) = await self.feelings()
        outcome = await self.act("world-bias", f"{line['id']} {standing.DEVOTED}")
        self.assertEqual(outcome.error, "")
        self.assertEqual(await self.feelings(), [])  # (kept: the DM's own now)
        (batch,) = outcome.batches
        self.assertEqual((await self.act("world-undo", str(batch))).error, "")
        (back,) = await self.feelings()
        self.assertEqual(back["step"], standing.UNFRIENDLY)  # (the read, as it was)

    async def test_change_on_an_answered_feeling_is_gone(self) -> None:
        (line,) = await self.feelings()
        await self.act("world-keep", line["id"])
        outcome = await self.act("world-bias", f"{line['id']} {standing.WARY}")
        self.assertEqual(outcome.error, wa.GONE)

    async def test_only_the_shapes_are_taken(self) -> None:
        (line,) = await self.feelings()
        for text in (
            f"{line['id']} Grumpy", f"{line['id']}", "bias:nope Wary",
            f"place:{line['id'][5:]} Wary", f"{line['id']} wary",
        ):  # fmt: skip
            got = await self.add_action("world-bias", text)
            self.assertEqual(got["status"], "bad_text", text)
        self.assertEqual((await self.add_action("world-not", line["id"]))["status"], "ok")

    async def test_the_queue_helpers_check_the_body(self) -> None:
        (line,) = await self.feelings()
        for body in (
            {"id": line["id"], "step": "Grumpy"}, {"id": "place:" + line["id"][5:], "step": "Wary"},
            {"step": "Wary"}, 5,
        ):  # fmt: skip
            with self.assertRaises(RecapError) as err:
                await world_notes.queue_bias(self.web, self.session, self.cref, body)
            self.assertEqual(err.exception.code, "bad_action")
        queued = await world_notes.queue_bias(
            self.web, self.session, self.cref, {"id": line["id"], "step": standing.WARY}
        )
        self.assertTrue(queued)
