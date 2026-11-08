"""The site's short answer as the route uses it (#3856): the limits, the cache and the call in
the order the owner set (docs/PLAN.md, "Search the website")."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

from dmbot.sidebar.knowledge import Ranker
from dmbot.site_ask import answer as answers
from dmbot.site_ask.answer import CANT, Answer, build_ranker
from dmbot.site_ask.cloudflare import AskFailed, Workers
from dmbot.site_ask.limits import Limit, Limits, visitor_key
from dmbot.site_ask.sources import Passage

log = logging.getLogger(__name__)


class Limited(Exception):
    """A limit was reached: `limit` is "hour" or "day", for the page to pick its words."""

    def __init__(self, limit: Limit) -> None:
        super().__init__(limit)
        self.limit = limit


class SiteAsk:
    def __init__(
        self,
        workers: Workers,
        passages: list[Passage],
        secret: bytes,
        *,
        daily_answers: int = 1500,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._workers = workers
        self._ranker: Ranker = build_ranker(passages)
        self._secret = secret
        self._clock = clock
        self.limits = Limits(daily_answers=daily_answers, clock=clock)
        self.passages = len(passages)

    async def ask(self, address: str, question: str) -> Answer:
        """The answer to a question that was read with `answers.read_question`. Raises `Limited`
        over a limit. When the service cannot answer (no allowance, a refusal, too slow) it gives
        the plain "can't answer right now" line, which is never kept for the next asker."""
        visitor = visitor_key(self._secret, address, self._clock())
        over = self.limits.take_visitor(visitor)
        if over is not None:
            log.info("Site ask refused: visitor %s", over)
            raise Limited(over)
        kept = self.limits.cached(question)
        if kept is not None:
            log.info("Site ask answered from the cache")
            return Answer(*kept)
        over = self.limits.take_site()
        if over is not None:
            log.info("Site ask refused: site %s", over)
            raise Limited(over)
        try:
            result = await answers.answer(self._workers, self._ranker, question)
        except AskFailed as exc:
            self.limits.give_back_site()
            log.warning("Site ask could not answer: %s", exc.kind)
            return Answer(CANT, None)
        self.limits.keep(question, result.answer, result.link)
        log.info("Site ask answered")
        return result
