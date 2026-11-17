"""MapBot's "how far" and "which way" answers in the sidebar (#4670, MapBot layers slice 4b;
docs/design/mapbot_layers.md §9; Supervisor's ruling on #4670), behind DMBOT_MAP_MEASURE.

A thin layer in front of the answer engine: a question in one of `maps.measure_phrase`'s strict
shapes, about two kept places, is answered by code from the kept maps (no AI call, no cost); any
other question, or one about names that aren't both kept places, goes to the engine as before.
Because the answer comes back as an ordinary `Answer`, every path the sidebar already has applies
unchanged: the table's chat or the DM's private chat, the transcript's lines, consent asked again.

Who sees what (the ruling's condition): a question asked at the table (`table=True`) is answered
from the table's maps and the places shown to the players only, whoever asked it, so a DM-only
map's pins, scale and distances never reach a player; a DM's private question uses every map
DMbot may use, and carries the Maps page's link."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from dmbot.maps import measure_phrase
from dmbot.maps import measure_words as words
from dmbot.sidebar.answer import Answer

log = logging.getLogger(__name__)

PROMPT_VERSION = "map-measure-1"  # no prompt: the words are code's (`maps/measure_words.py`)

Measure = Callable[[Any, measure_phrase.Asked, bool], Awaitable[str | None]]


def _restated(asked: measure_phrase.Asked) -> str:
    """The question as the transcript keeps it."""
    if asked.kind == measure_phrase.FAR:
        return f"how far from {asked.a} to {asked.b}"
    return f"which way is {asked.a} from {asked.b}"


class MapMeasuring:
    """An `Answerer` that answers map questions itself and passes the rest on."""

    def __init__(
        self,
        inner: Any,
        measure: Measure,
        *,
        on: Callable[[], bool],
        maps_page: Callable[[], str] = lambda: "",
    ) -> None:
        self.inner = inner
        self._measure = measure
        self._on = on
        self._maps_page = maps_page

    async def answer(
        self, campaign: Any, question: str, *, asker_id: int, scene: str, table: bool = False
    ) -> Any:
        asked = measure_phrase.read(question) if self._on() else None
        if asked is not None:
            # The plan's check comes first, as for any answer (an engine without one has none).
            check = getattr(self.inner, "_gate", None)
            refused = await check(campaign, asker_id) if check is not None else None
            text = None
            if refused is None:
                try:
                    text = await self._measure(campaign, asked, not table)
                    if (
                        table
                        and text is not None
                        and not words.measured(text)
                        and asker_id in getattr(campaign, "dm_user_ids", ())
                    ):
                        # (#4676: the DM asked at the table and the table's maps can't answer;
                        # if a DM-only map can, the answer is the DM's alone. The sidebar sends
                        # it privately, as for any question that needs the DM's side, and the
                        # table hears only "Sent to the DM privately", never that a map exists.)
                        theirs = await self._measure(campaign, asked, True)
                        if theirs is not None and words.measured(theirs):
                            return Answer(
                                text="", in_game=False, question=_restated(asked), model="",
                                prompt_version=PROMPT_VERSION, one_for_dm=True,
                            )  # fmt: skip
                except Exception as exc:  # (never fatal: the usual answer goes on)
                    log.warning("Map answer failed (%s)", type(exc).__name__)
            if text is not None:
                page = self._maps_page() if not table else ""
                shown = f"{text}\n{words.LINK}: {page}" if page else text
                restated = _restated(asked)
                # A DM's private answer may come from a DM-only map: it is never written to the
                # raw transcript, which the players may read (`in_game` False: the question line
                # is kept, the answer is not). The table's answer is from the table's maps.
                return Answer(
                    text=shown, in_game=table, question=restated, model="",
                    prompt_version=PROMPT_VERSION, sources=("a kept map",), parts=(shown,),
                )  # fmt: skip
        return await self.inner.answer(
            campaign, question, asker_id=asker_id, scene=scene, table=table
        )

    def __getattr__(self, name: str) -> Any:
        # (anything else the sidebar reads from its engine is the engine's own; never `inner`
        # itself, which would loop before it is set)
        if name == "inner":
            raise AttributeError(name)
        return getattr(self.inner, name)
