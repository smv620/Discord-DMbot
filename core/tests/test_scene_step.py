"""Writing the scenes of a session's waiting questions (#3596 slice 1, PR 3): the step, with the
AI, the transcript and the store stood in. Invented names."""

from __future__ import annotations

import json
import unittest
from collections.abc import Awaitable, Callable, Collection
from typing import Any

from dmbot.ai import AIOutOfFunds, Reply
from dmbot.memory import scene_reading as sr
from dmbot.memory import scene_step as step
from dmbot.memory.models import Entity
from dmbot.memory.scenes import ENTITY, Scene
from dmbot.transcript.store import Around

DM_ID, ALICE, BOB = 7, 8, 9
ALL = {DM_ID, ALICE, BOB}


def entity(i: str, *, unnamed: bool = False, status: str = "proposed") -> Entity:
    return Entity(
        id=i, type="character", name=f"Name {i}", description="", status=status,
        merged_into=None, source="scan", created_at=0, unnamed=unnamed,
        unnamed_what="a patron" if unnamed else None,
    )  # fmt: skip


def item(number: int, speakers: tuple[int, ...] = (ALICE,)) -> sr.SceneItem:
    return sr.SceneItem(
        number,
        sr.NAME,
        ENTITY,
        f"e{number}",
        (sr.SceneLine(False, f"line {number}", True),),
        speakers,
    )


class Client:
    """A fake AI: one scene for each item it is sent, or whatever `answer` makes of the text."""

    def __init__(self, fail: Exception | None = None, cut: bool = False) -> None:
        self.sent: list[str] = []
        self.fail = fail
        self.cut = cut

    async def complete(self, system: str, text: str, *, max_tokens: int = 0) -> Reply:
        self.sent.append(text)
        if self.fail is not None:
            raise self.fail
        numbers = [int(n) for n in __import__("re").findall(r"Item (\d+):", text)]
        scenes = [{"item": n, "scene": f"Scene of {n}."} for n in numbers]
        body = json.dumps({"scenes": scenes})
        if self.cut:
            body = body[: body.rindex("{")]  # the last item is broken
        return Reply(body, self.cut, 10, 5, "m")


class Store:
    def __init__(self) -> None:
        self.kept: list[Scene] = []
        self.opted: list[Collection[int]] = []

    async def keep(self, guild_id: int, campaign_id: str, scenes: Any, **kw: Any) -> int:
        self.kept.extend(scenes)
        self.opted.append(kw["opted_in"])
        return len(scenes)


def run(
    client: Client,
    store: Store,
    items: list[sr.SceneItem],
    *,
    recorded: Collection[int] = ALL,
    consenting: Callable[[], Awaitable[Collection[int]]] | None = None,
    max_calls: int = 10,
    count_call: Callable[[], Awaitable[None]] | None = None,
) -> Any:
    async def everyone() -> Collection[int]:
        return recorded

    return step.run(
        client,
        store,  # type: ignore[arg-type]
        items,
        guild_id=1,
        campaign_id="c",
        consenting=consenting or everyone,
        model=lambda: "m",
        session_started_at=500,
        max_calls=max_calls,
        count_call=count_call,
    )


class Asking(unittest.IsolatedAsyncioTestCase):
    async def test_many_questions_are_one_call_and_each_scene_is_kept_with_its_speakers(
        self,
    ) -> None:
        client, store = Client(), Store()
        report = await run(client, store, [item(1), item(2, (ALICE, BOB))])
        self.assertEqual((report.calls, report.made, report.questions), (1, 2, 2))
        self.assertEqual(len(client.sent), 1)
        self.assertEqual(
            [(s.subject_id, s.speaker_ids) for s in store.kept],
            [("e1", (ALICE,)), ("e2", (ALICE, BOB))],
        )
        self.assertEqual(store.kept[0].session_started_at, 500)

    async def test_calls_are_counted_and_held_to_the_limit(self) -> None:
        counted: list[int] = []

        async def count() -> None:
            counted.append(1)

        many = [item(i) for i in range(1, 92)]  # three requests of 40, 40 and 11
        report = await run(Client(), Store(), many, max_calls=2, count_call=count)
        self.assertEqual((report.calls, len(counted), report.stopped), (2, 2, "call limit"))
        self.assertEqual(report.unsent, 11)

    async def test_someone_who_stopped_before_the_call_is_left_out_of_it(self) -> None:
        client, store = Client(), Store()
        report = await run(client, store, [item(1), item(2, (BOB,))], recorded={DM_ID, ALICE})
        self.assertEqual((report.made, report.dropped_for_consent), (1, 1))
        self.assertNotIn("Item 2", client.sent[0])

    async def test_someone_who_stops_during_the_call_loses_that_scene(self) -> None:
        asked: list[int] = []

        async def consenting() -> Collection[int]:
            asked.append(1)
            return ALL if len(asked) == 1 else {DM_ID, ALICE}  # (Bob stops during the call)

        store = Store()
        report = await run(Client(), store, [item(1), item(2, (BOB,))], consenting=consenting)
        self.assertEqual([s.subject_id for s in store.kept], ["e1"])
        self.assertEqual((report.made, report.dropped_for_consent), (1, 1))

    async def test_a_call_that_cannot_be_counted_is_not_made(self) -> None:
        async def broken() -> None:
            raise RuntimeError("down")

        client = Client()
        report = await run(client, Store(), [item(1)], count_call=broken)
        self.assertEqual((report.calls, report.failed, client.sent), (0, 1, []))

    async def test_if_who_is_recorded_cannot_be_known_nothing_is_sent_or_kept(self) -> None:
        async def broken() -> Collection[int]:
            raise RuntimeError("down")

        client, store = Client(), Store()
        report = await run(client, store, [item(1)], consenting=broken)
        self.assertEqual((client.sent, store.kept, report.failed), ([], [], 1))
        answered = [0]

        async def blip() -> Collection[int]:
            answered[0] += 1
            if answered[0] == 2:  # (after the answer)
                raise RuntimeError("down")
            return ALL

        store = Store()
        report = await run(Client(), store, [item(1)], consenting=blip)
        self.assertEqual((store.kept, report.dropped_for_consent), ([], 1))

    async def test_a_failed_call_is_counted_and_the_next_part_goes_on(self) -> None:
        report = await run(Client(fail=RuntimeError("down")), Store(), [item(1)])
        self.assertEqual((report.failed, report.made, report.unsent), (1, 0, 1))

    async def test_out_of_funds_stops_the_step(self) -> None:
        many = [item(i) for i in range(1, 92)]
        report = await run(Client(fail=AIOutOfFunds()), Store(), many)
        self.assertEqual((report.stopped, report.calls), ("out of funds", 1))
        self.assertEqual(report.unsent, 51)  # the two parts after it

    async def test_a_cut_answer_keeps_the_whole_scenes_and_counts_the_rest(self) -> None:
        store = Store()
        report = await run(Client(cut=True), store, [item(1), item(2), item(3)])
        self.assertEqual(report.made, 2)
        self.assertEqual(report.stopped_early, 1)

    async def test_nothing_to_ask_makes_no_call(self) -> None:
        client = Client()
        report = await run(client, Store(), [])
        self.assertEqual((report.calls, client.sent), (0, []))

    async def test_the_report_line_has_counts_only(self) -> None:
        report = await run(Client(), Store(), [item(1)])
        self.assertIn("1 made", report.line())
        self.assertNotIn("Scene of", report.line())


class Gathering(unittest.IsolatedAsyncioTestCase):
    class Transcripts:
        async def around(self, guild: int, session: str, ms: int, user: int, agreed: Any) -> Any:
            if user not in agreed or session == "gone":
                return None
            return Around((user, "before"), (user, f"said at {ms}"), (DM_ID, "after"))

    async def build(
        self,
        entities: list[Entity],
        pointers: dict[str, list[tuple[str, int | None]]],
        agreed: Any = ALL,
    ) -> Any:
        async def mentions(entity_id: str) -> list[tuple[str, int | None]]:
            return pointers.get(entity_id, [])

        return await step.items_for(entities, mentions, self.Transcripts(), 1, {DM_ID}, agreed)  # type: ignore[arg-type]

    async def test_a_question_reads_its_lines_with_the_one_before_and_after(self) -> None:
        items, empty = await self.build([entity("a")], {"a": [("s1:1000:8", 5)]})
        self.assertEqual(empty, 0)
        (one,) = items
        self.assertEqual(
            [(line.by_dm, line.hit) for line in one.lines],
            [(False, False), (False, True), (True, False)],
        )
        self.assertEqual((one.kind, one.speaker_ids, one.number), (sr.NAME, (DM_ID, ALICE), 1))

    async def test_an_unnamed_person_is_a_person_and_the_numbers_run_on(self) -> None:
        items, _ = await self.build(
            [entity("a"), entity("b", unnamed=True, status="confirmed")],
            {"a": [("s1:1000:8", 5)], "b": [("s1:2000:8", 5)]},
        )
        self.assertEqual([(i.number, i.kind) for i in items], [(1, sr.NAME), (2, sr.PERSON)])

    async def test_a_question_with_no_line_left_is_counted_and_not_asked(self) -> None:
        items, empty = await self.build(
            [entity("a"), entity("b"), entity("c")],
            {"a": [], "b": [("gone:1000:8", 5)], "c": [("s1:1000:8", 5), ("junk", 5)]},
        )
        self.assertEqual((len(items), empty), (1, 2))

    async def test_a_line_from_someone_who_does_not_agree_is_not_read(self) -> None:
        items, empty = await self.build([entity("a")], {"a": [("s1:1000:9", 5)]}, agreed={DM_ID})
        self.assertEqual((items, empty), ([], 1))

    def test_what_is_an_open_question(self) -> None:
        self.assertTrue(step.is_open_name(entity("a")))
        self.assertTrue(step.is_open_name(entity("b", unnamed=True, status="confirmed")))
        self.assertFalse(step.is_open_name(entity("c", status="confirmed")))
        self.assertFalse(step.is_open_name(entity("d", status="rejected")))
        merged = entity("e")
        object.__setattr__(merged, "merged_into", "x")
        self.assertFalse(step.is_open_name(merged))

    def test_a_reference_is_read_back_into_its_parts(self) -> None:
        self.assertEqual(step.parse_ref("s-1:1234:56"), ("s-1", 1234, 56))
        self.assertIsNone(step.parse_ref("junk"))
        self.assertIsNone(step.parse_ref("a:b:c"))


class TheSwitch(unittest.IsolatedAsyncioTestCase):
    async def test_off_by_default_nothing_is_read_or_asked(self) -> None:
        from functools import partial
        from types import SimpleNamespace

        from dmbot.bot import DMBot

        me: Any = SimpleNamespace(
            settings=SimpleNamespace(story_scenes_on=False, story_extractor_on=True),
            extractor_ai=object(),
            memory=object(),
            transcripts=object(),
        )
        me.write_scenes = partial(DMBot.write_scenes, me)
        table = SimpleNamespace(guild_id=1, campaign_id="c", transcript_session_id="s")
        await me.write_scenes(table)  # (would fail on `object()` if it read anything)
        me.settings.story_scenes_on = True
        me.settings.story_extractor_on = False  # scenes ride on the extractor
        await me.write_scenes(table)
