"""The split kept once for a session (#3298): read first, else made by the one call and kept,
anything unsure is all play and kept for nobody. Invented lines; no database (a fake store)."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any

from dmbot.ai import Reply
from dmbot.recaps import split as sp
from dmbot.recaps.pipeline import Inputs
from dmbot.recaps.session_split import SessionSplitter
from tests import recap_fixtures as fx
from tests import test_recap_split as st

G, C, SESSION, AT = 1, "c" * 32, "s1", 1_700_000_000
SPEAKER = fx.DM
EVERYONE = {fx.DM, fx.ALICE, fx.BOB}


class FakeStore:
    def __init__(self, kept: dict[str, Any] | None = None, session: str = SESSION) -> None:
        self.kept, self.saves, self.session = kept, 0, session

    async def session_at(self, guild_id: int, campaign_id: str, started_at: int) -> Any:
        return SimpleNamespace(id=self.session)

    async def session_split(self, guild_id: int, session_id: str) -> dict[str, Any] | None:
        return self.kept

    async def save_session_split(
        self, guild_id: int, session_id: str, split: Any, *, only_if_empty: bool = True
    ) -> bool:
        self.saves += 1
        if self.kept is not None and only_if_empty:
            return False
        self.kept = dict(split)
        return True


class FakeClient:
    def __init__(self, *answers: str | Exception) -> None:
        self.answers = list(answers)
        self.sent: list[str] = []

    async def complete(
        self, system: str, text: str, *, max_tokens: int = 0, schema: Any = None
    ) -> Reply:
        self.sent.append(text)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return Reply(text=answer, cut=False)


def inputs(earlier: sp.Earlier | None = st.EARLIER) -> Inputs:
    return Inputs(
        lines=st.reading([st.TALK, st.READ_ALOUD, [t for _, t in fx.LINES]]),
        dm_ids={fx.DM},
        allowed_names=[],
        secret_names=[],
        previous="",
        day=None,
        earlier=earlier,
    )


class TheSplitForASession(unittest.IsolatedAsyncioTestCase):
    def splitter(
        self, store: FakeStore, fast: FakeClient, found: Inputs | Exception | None = None
    ) -> SessionSplitter:
        async def load(_g: int, _c: str, _at: int) -> Inputs | None:
            if isinstance(found, Exception):
                raise found
            return inputs() if found is None else found

        return SessionSplitter(store, load, fast, FakeClient())

    async def ask(self, splitter: SessionSplitter, agreed: set[int] | None = None) -> Any:
        async def consenting() -> set[int]:
            return EVERYONE if agreed is None else agreed

        return await splitter.split_for(G, C, SESSION, AT, consenting)

    async def test_a_kept_split_is_read_and_no_call_is_made(self) -> None:
        kept = sp.Split((sp.Part(sp.RECAP, 1, 3), sp.Part(sp.PLAY, 4, 9)), False, "ai").to_json()
        fast = FakeClient()
        got = await self.ask(self.splitter(FakeStore(kept), fast))
        assert got is not None
        self.assertEqual([p.label for p in got.parts], [sp.RECAP, sp.PLAY])
        self.assertEqual(fast.sent, [])

    async def test_without_one_the_call_is_made_once_and_kept(self) -> None:
        store, fast = FakeStore(), FakeClient(st.SPLIT_ANSWER)
        splitter = self.splitter(store, fast)
        first = await self.ask(splitter)
        assert first is not None
        self.assertIn("recap", [p.label for p in first.parts])
        self.assertEqual((len(fast.sent), store.saves), (1, 1))
        again = await self.ask(splitter)  # the next read finds it kept: still one call
        assert again is not None
        self.assertEqual(again.parts, first.parts)
        self.assertEqual(len(fast.sent), 1)

    async def test_a_failed_call_is_nothing_and_nothing_is_kept(self) -> None:
        store, fast = FakeStore(), FakeClient(RuntimeError("boom"))
        self.assertIsNone(await self.ask(self.splitter(store, fast)))
        self.assertEqual((store.kept, store.saves), (None, 0))

    async def test_an_answer_that_cannot_be_read_is_used_once_and_never_kept(self) -> None:
        store = FakeStore()
        got = await self.ask(self.splitter(store, FakeClient("I cannot tell")))
        assert got is not None
        self.assertEqual(got.method, "fallback")
        self.assertEqual((store.kept, store.saves), (None, 0))  # asked again next time

    async def test_a_doubtful_retelling_is_used_once_and_never_kept(self) -> None:
        answer = '{"parts": [{"label": "recap", "last": 1}]}'
        store = FakeStore()
        got = await self.ask(self.splitter(store, FakeClient(answer)))
        assert got is not None
        self.assertTrue(got.unsure)
        self.assertEqual((store.kept, store.saves), (None, 0))

    async def test_another_campaigns_session_is_never_read_or_written(self) -> None:
        store, fast = FakeStore(session="someone-elses"), FakeClient(st.SPLIT_ANSWER)
        self.assertIsNone(await self.ask(self.splitter(store, fast)))
        self.assertEqual((fast.sent, store.saves), ([], 0))

    async def test_a_session_with_no_earlier_session_makes_no_call(self) -> None:
        store, fast = FakeStore(), FakeClient()
        got = await self.ask(self.splitter(store, fast, inputs(earlier=None)))
        assert got is not None
        self.assertEqual(([p.label for p in got.parts], fast.sent), ([sp.PLAY], []))

    async def test_someone_who_stopped_being_recorded_during_the_call_keeps_nothing(self) -> None:
        store, fast = FakeStore(), FakeClient(st.SPLIT_ANSWER)
        calls = {"n": 0}

        async def consenting() -> set[int]:
            calls["n"] += 1
            return EVERYONE if calls["n"] == 1 else set()  # gone by the re-check

        got = await self.splitter(store, fast).split_for(G, C, SESSION, AT, consenting)
        self.assertIsNone(got)
        self.assertEqual(store.saves, 0)

    async def test_no_inputs_or_a_broken_loader_is_nothing_never_an_error(self) -> None:
        async def none_load(_g: int, _c: str, _at: int) -> Inputs | None:
            return None

        store, fast = FakeStore(), FakeClient()
        splitter = SessionSplitter(store, none_load, fast, FakeClient())
        self.assertIsNone(await self.ask(splitter))
        self.assertIsNone(await self.ask(self.splitter(store, fast, RuntimeError("db"))))
        self.assertEqual(fast.sent, [])

    async def test_a_short_previously_in_the_middle_of_play_is_play(self) -> None:
        lines = st.reading(
            [st.TALK, ["Previously, the dragon burned it down, I think!"], [t for _, t in fx.LINES]]
        )
        answer = '{"parts": [{"label": "talk", "last": 2}]}'
        got = await self.ask(
            self.splitter(FakeStore(), FakeClient(answer), Inputs(
                lines=lines, dm_ids={fx.DM}, allowed_names=[], secret_names=[],
                previous="", day=None, earlier=st.EARLIER,
            ))
        )  # fmt: skip
        assert got is not None
        self.assertFalse(got.has(sp.RECAP))
