"""The step that works out who a waiting name is (#3602, slice 2c). A fake AI client and a fake
store, so what is asserted is the step's own rules: consent, the call limit, the floor, one
campaign, the prompt version, and which names a choice may carry. Invented names."""

from __future__ import annotations

import dataclasses
import json
import unittest
from collections.abc import Collection
from typing import Any

from dmbot.ai import AIOutOfFunds, Reply
from dmbot.memory import who_reading as reading
from dmbot.memory import who_step as step
from dmbot.memory.lookup import CampaignLookup, LookupData
from dmbot.memory.models import CONFIRMED, PROPOSED, Alias, Entity, name_key
from dmbot.memory.who_answers import ENTITY, NEW, StoredAnswer

MIRA, VALE, GHOST, WAITING, OTHER = (c * 32 for c in "abcde")


def entity(eid: str, name: str, status: str = CONFIRMED, **kw: Any) -> Entity:
    return dataclasses.replace(Entity(eid, "character", name, "", status, None, "dm", 0), **kw)


def alias(eid: str, text: str, *, secret: bool = False) -> Alias:
    aid = eid[:16] + name_key(text).replace(" ", "").ljust(16, "0")[:16]
    return Alias(aid, eid, text, name_key(text), "full", None, secret, CONFIRMED, (), "dm", 0)


def lookup() -> CampaignLookup:
    entities = (
        entity(MIRA, "Mira Vale"),
        entity(VALE, "Vale the baker"),
        entity(GHOST, "The Grey Fox", hidden_from_players=True),
        entity(WAITING, "Vale", PROPOSED),
    )
    aliases = (
        alias(MIRA, "Mira Vale"),
        alias(MIRA, "the Hollow Knife", secret=True),  # (a visible entry's secret name)
        alias(VALE, "Vale the baker"),
        alias(GHOST, "The Grey Fox"),
        alias(GHOST, "Old Rook", secret=True),
        alias(WAITING, "Vale"),
    )
    return CampaignLookup.build(LookupData(1, entities, aliases, (), ()))


class Candidates(unittest.TestCase):
    def test_a_visible_entrys_secret_alias_never_reaches_its_names(self) -> None:
        for hidden in (False, True):
            got = step.candidates_from(lookup(), include_hidden=hidden, leave_out=[WAITING])
            mira = next(c for c in got if c.id == MIRA)
            self.assertEqual(mira.names, ("Mira Vale",), hidden)
            self.assertNotIn("the Hollow Knife", {n for c in got if not c.hidden for n in c.names})

    def test_a_hidden_entry_is_a_choice_only_for_the_dms_ranking_with_all_its_names(self) -> None:
        got = step.candidates_from(lookup(), include_hidden=False, leave_out=[WAITING])
        self.assertEqual({c.id for c in got}, {MIRA, VALE})
        dm = step.candidates_from(lookup(), include_hidden=True, leave_out=[WAITING])
        ghost = next(c for c in dm if c.id == GHOST)
        self.assertTrue(ghost.hidden)
        self.assertEqual(set(ghost.names), {"The Grey Fox", "Old Rook"})

    def test_the_waiting_name_and_unconfirmed_entries_are_never_choices(self) -> None:
        got = step.candidates_from(lookup(), include_hidden=True)  # (no leave_out: still not it)
        self.assertNotIn(WAITING, {c.id for c in got})


def waiting(number: int = 1, speakers: tuple[int, ...] = (8,), eid: str = WAITING) -> step.Pending:
    e = entity(eid, "Vale", PROPOSED)
    cands = step.candidates_from(lookup(), include_hidden=False, leave_out=[eid])
    built = step.build(
        e, cands, number=number, scene="Mira argued with the baker.", lines=("DM: Vale waves.",),
        speaker_ids=speakers, scene_ids={MIRA}, include_hidden=False,
    )  # fmt: skip
    assert built is not None
    return built


class Building(unittest.TestCase):
    def test_choices_are_numbered_for_the_item_and_map_back_to_entries(self) -> None:
        p = waiting()
        self.assertEqual(p.item.choices[0].ref, "c1")
        self.assertEqual(set(p.refs.values()), {MIRA, VALE})
        self.assertNotIn(WAITING, p.refs.values())
        text = reading.items_text([p.item])
        self.assertNotIn(MIRA, text)  # (the model never sees an id)
        self.assertNotIn("Hollow Knife", text)

    def test_nothing_to_offer_is_no_item(self) -> None:
        e = entity(WAITING, "Zorblax", PROPOSED)
        self.assertIsNone(step.build(e, [], number=1))

    def test_the_answers_become_entries_and_new(self) -> None:
        p = waiting()
        got = step.to_stored(
            [reading.Answer("c1", "likely"), reading.Answer("new", "maybe", "place"),
             reading.Answer("c9", "sure")],
            p.refs,
        )  # fmt: skip
        self.assertEqual(
            got,
            [
                StoredAnswer(ENTITY, "likely", target_id=p.refs["c1"]),
                StoredAnswer(NEW, "maybe", new_kind="place"),
            ],
        )


class FakeClient:
    def __init__(self, *replies: Any) -> None:
        self.replies = list(replies)
        self.calls: list[dict[str, Any]] = []

    async def complete(self, system: str, text: str, *, max_tokens: int = 0) -> Reply:
        self.calls.append({"system": system, "text": text, "max_tokens": max_tokens})
        nxt = self.replies.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt  # type: ignore[no-any-return]


class FakeAnswers:
    def __init__(self) -> None:
        self.kept: list[dict[str, Any]] = []

    async def keep(self, guild: int, campaign: str, entity: str, answers: Any, **kw: Any) -> int:
        if not set(kw["speaker_ids"]) <= set(kw["opted_in"]):
            return 0
        self.kept.append({"entity": entity, "answers": list(answers), **kw})
        return len(answers)


def reply(p: step.Pending, *picks: tuple[str, str], cut: bool = False) -> Reply:
    ranked = [{"pick": a, "confidence": b} for a, b in picks]
    body = json.dumps({"answers": [{"item": p.item.number, "ranked": ranked}]})
    return Reply(body, cut, 10, 10, "model-x")


async def run(
    client: FakeClient, answers: FakeAnswers, pending: list[step.Pending], **kw: Any
) -> step.Report:
    agreed: Collection[int] = kw.pop("agreed", {8, 9})
    after = kw.pop("after", None)
    calls = {"n": 0}

    async def consenting() -> Collection[int]:
        calls["n"] += 1
        return after if (after is not None and calls["n"] > 1) else agreed

    async def count() -> None:
        kw.setdefault("counted", []).append(1)

    return await step.run(
        client, answers,  # type: ignore[arg-type]
        pending, guild_id=1, campaign_id="c", consenting=consenting,
        model=lambda: "m", session_started_at=500, max_calls=kw.pop("max_calls", 5),
        count_call=count,
    )  # fmt: skip


class Running(unittest.IsolatedAsyncioTestCase):
    async def test_a_name_is_asked_with_the_floor_and_its_answers_are_kept_with_the_version(
        self,
    ) -> None:
        p = waiting()
        client, answers = FakeClient(reply(p, ("c1", "likely"), ("new", "maybe"))), FakeAnswers()
        # "new" with no kind is dropped by the reader; the choice stays
        report = await run(client, answers, [p])
        self.assertEqual(client.calls[0]["max_tokens"], step.BACKGROUND_FLOOR)
        self.assertEqual(client.calls[0]["system"], reading.WHO_SYSTEM)
        (kept,) = answers.kept
        self.assertEqual(kept["prompt_version"], reading.WHO_PROMPT_VERSION)
        self.assertEqual(kept["answers"], [StoredAnswer(ENTITY, "likely", target_id=p.refs["c1"])])
        self.assertEqual((report.ranked, report.calls, report.failed), (1, 1, 0))

    async def test_a_name_from_someone_not_recorded_is_never_sent(self) -> None:
        p = waiting(speakers=(8, 77))
        client = FakeClient()
        report = await run(client, FakeAnswers(), [p])
        self.assertEqual((client.calls, report.dropped_for_consent, report.calls), ([], 1, 0))

    async def test_consent_is_asked_again_after_the_answer(self) -> None:
        p = waiting(speakers=(8,))
        client, answers = FakeClient(reply(p, ("c1", "sure"))), FakeAnswers()
        report = await run(client, answers, [p], after={9})  # (8 stopped meanwhile)
        self.assertEqual((answers.kept, report.dropped_for_consent, report.ranked), ([], 1, 0))

    async def test_the_call_limit_stops_the_step_and_the_rest_wait_for_next_time(self) -> None:
        one, two = waiting(1), waiting(2, eid=OTHER)
        client = FakeClient(reply(one, ("c1", "sure")))
        big = [
            dataclasses.replace(
                p, item=dataclasses.replace(p.item, scene="s" * 300, lines=("l" * 300,) * 3)
            )
            for p in (one, two)
        ]
        report = await run(client, FakeAnswers(), big * 40, max_calls=1)  # (many parts)
        self.assertEqual(report.calls, 1)
        self.assertEqual(report.stopped, "call limit")
        self.assertGreater(report.unsent, 0)

    async def test_out_of_funds_stops_and_a_failed_call_is_counted_not_raised(self) -> None:
        p = waiting()
        report = await run(FakeClient(AIOutOfFunds("x")), FakeAnswers(), [p])
        self.assertEqual((report.stopped, report.failed), ("out of funds", 1))
        report = await run(FakeClient(RuntimeError("boom")), FakeAnswers(), [p])
        self.assertEqual((report.failed, report.stopped), (1, ""))

    async def test_a_cut_answer_keeps_its_whole_items_and_says_it_stopped_early(self) -> None:
        one, two = waiting(1), waiting(2, eid=OTHER)
        whole = json.dumps({"item": 1, "ranked": [{"pick": "c1", "confidence": "sure"}]})
        text = '{"answers": [' + whole + ', {"item": 2, "ranked": [{"pick": "c'
        cut = Reply(text, True, 1, 1, "m")
        answers = FakeAnswers()
        report = await run(FakeClient(cut), answers, [one, two])
        self.assertEqual((len(answers.kept), report.stopped_early, report.ranked), (1, 1, 1))

    async def test_an_answer_with_nothing_usable_is_counted_and_keeps_nothing(self) -> None:
        p = waiting()
        answers = FakeAnswers()
        report = await run(FakeClient(reply(p, ("c9", "sure"))), answers, [p])
        self.assertEqual((answers.kept, report.empty, report.dropped), ([], 1, 1))

    async def test_the_report_line_has_counts_only(self) -> None:
        line = step.Report(names=3, ranked=2).line()
        self.assertIn("3 name(s), 2 ranked", line)
