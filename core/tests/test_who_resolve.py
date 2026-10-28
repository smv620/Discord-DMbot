"""Wiring "who is he?" into a lore read (#2790): the open pronoun a quote leaves (pure), what
`who_resolve.settle` makes of each answer, the cap and the progress, and the whole path into the
waiting connections against Postgres. A scripted stand-in for the AI; invented names; no network."""

from __future__ import annotations

import asyncio
import json
import unittest
from collections import Counter
from typing import Any
from unittest.mock import AsyncMock, patch

from dmbot.ai import Reply
from dmbot.memory import background as bg
from dmbot.memory import who_ask, who_resolve
from dmbot.memory.backstory_links import BackstoryLinks
from dmbot.memory.models import CONFIRMED, NewName
from dmbot.memory.who_is_it import NOT_LISTED, Candidate
from dmbot.ui import name_background as ui_bg
from tests.test_memory_names import GUILD, NamesTest

TEXT = "Mara arrived from the north with Bram. Dusk fell over the road. She was the sister of Ken."


def reply(*lines: dict[str, str]) -> str:
    return json.dumps({"connections": list(lines)})


def sister(quote: str = "She was the sister of Ken") -> dict[str, str]:
    return {"a": "Mara", "how": "family", "b": "Ken", "quote": quote}


def unsettled_of(text: str = TEXT, *lines: dict[str, str]) -> bg.Unsettled:
    held: list[bg.Unsettled] = []
    bg.read_connections(reply(*(lines or (sister(),))), text, unsettled=held)
    (open_,) = held
    return open_


class Scripted:
    """Answers the scoring call with `scores`, and every sample with `pick` (None: they fail)."""

    def __init__(self, scores: list[tuple[str, float]], pick: str | None = None) -> None:
        self.scores, self.pick, self.calls = scores, pick, 0

    async def complete(
        self, system: str, text: str, *, max_tokens: int = 0, schema: dict[str, Any] | None = None
    ) -> Reply:
        self.calls += 1
        body: dict[str, Any]
        if schema == who_ask.scores_schema():
            body = {"candidates": [{"name": n, "confidence": c} for n, c in self.scores]}
        elif self.pick is None:
            raise RuntimeError("no sample")
        else:
            body = {"name": self.pick}
        return Reply(json.dumps(body), cut=False)


def asked(*pairs: tuple[str, float], checked: bool = True) -> who_ask.Asked:
    return who_ask.Asked(
        [Candidate(n, c) for n, c in pairs],
        [c for _, c in pairs],
        [c for _, c in pairs],
        5,
        checked,
    )


class TheOpenPronoun(unittest.TestCase):
    def test_a_pronoun_the_sentence_before_did_not_settle_is_dropped_when_not_held(self) -> None:
        lost: Counter[str] = Counter()
        self.assertEqual(bg.read_connections(reply(sister()), TEXT, dropped=lost), [])
        self.assertEqual(lost["not_in_text"], 1)

    def test_it_is_held_instead_when_asked_to(self) -> None:
        lost: Counter[str] = Counter()
        held: list[bg.Unsettled] = []
        self.assertEqual(
            bg.read_connections(reply(sister()), TEXT, dropped=lost, unsettled=held), []
        )
        self.assertEqual(lost["not_in_text"], 0)  # (the caller counts what became of it)
        (open_,) = held
        self.assertTrue(open_.missing_is_a)  # the quote lacks "Mara"
        self.assertEqual(open_.phrase, "She")
        self.assertTrue(open_.passage.endswith("the sister of Ken"))
        self.assertTrue(open_.passage.startswith("Mara arrived"))

    def test_a_pronoun_the_sentence_before_settles_is_never_held(self) -> None:
        text = "Mara arrived from the north. She was the sister of Ken."
        held: list[bg.Unsettled] = []
        got = bg.read_connections(reply(sister()), text, unsettled=held)
        self.assertEqual((len(got), held), (1, []))  # (the window already holds both names)

    def test_two_different_pronouns_are_not_a_question(self) -> None:
        text = "Mara arrived. Dusk fell. She told him she was the sister of Ken."
        line = sister("She told him she was the sister of Ken")
        lost: Counter[str] = Counter()
        held: list[bg.Unsettled] = []
        bg.read_connections(reply(line), text, dropped=lost, unsettled=held)
        self.assertEqual((held, lost["not_in_text"]), ([], 1))

    def test_a_quote_with_no_pronoun_is_never_held(self) -> None:
        text = "Mara arrived. Dusk fell. The woman was the sister of Ken."
        line = sister("The woman was the sister of Ken")
        lost: Counter[str] = Counter()
        held: list[bg.Unsettled] = []
        bg.read_connections(reply(line), text, dropped=lost, unsettled=held)
        self.assertEqual((held, lost["not_in_text"]), ([], 1))


class TheAnswers(unittest.IsolatedAsyncioTestCase):
    def test_a_sure_answer_that_agrees_keeps_the_connection_with_the_window_as_its_quote(
        self,
    ) -> None:
        out = who_resolve.Settled()
        who_resolve._use(unsettled_of(), asked(("Mara", 95), ("Bram", 5)), out)
        (kept,) = out.connections
        self.assertIn("Mara", kept.quote)
        self.assertIn("sister of Ken", kept.quote)
        self.assertEqual(out.counts["accepted"], 1)

    def test_how_each_pronoun_was_settled_is_recorded_in_fixed_words_and_a_rounded_score(
        self,
    ) -> None:
        for answer, want, where in (
            (asked(("Mara", 94.6), ("Bram", 5)), "who-is-it: accepted 95%", "connections"),
            (asked(("Mara", 55), ("Bram", 45)), "who-is-it: asked 55%", "picks"),
            (asked((NOT_LISTED, 90)), "who-is-it: nobody listed 90%", "unnamed"),
            (None, "who-is-it: not checked", "unnamed"),
        ):
            out = who_resolve.Settled()
            who_resolve._use(unsettled_of(), answer, out)
            (got,) = getattr(out, where)
            self.assertEqual(got.decided, want)
            self.assertLessEqual(len(got.decided), 60)
            self.assertNotIn("Mara", got.decided)  # (words and a number: never a name)

    async def test_the_cap_and_the_clock_are_recorded_as_not_checked(self) -> None:
        client = Scripted([("Mara", 95)], "Mara")
        out = await who_resolve.settle(
            [unsettled_of(), unsettled_of()], client, ["Mara", "Ken"], cap=1
        )
        self.assertEqual(out.unnamed[0].decided, "who-is-it: not checked, over the limit")
        late = await who_resolve.settle([unsettled_of()], client, ["Mara"], time_limit_s=-1.0)
        self.assertEqual(late.unnamed[0].decided, "who-is-it: not checked, out of time")

    def test_a_sure_answer_that_names_someone_else_is_a_question_not_a_guess(self) -> None:
        out = who_resolve.Settled()
        who_resolve._use(unsettled_of(), asked(("Bram", 95), ("Mara", 5)), out)
        self.assertEqual(out.connections, [])
        (pick,) = out.picks
        self.assertEqual(
            (pick.fixed, pick.fixed_is_a, pick.candidates), ("Ken", False, ("Bram", "Mara"))
        )
        self.assertEqual(out.counts["asked"], 1)

    def test_a_doubt_asks_the_dm_between_the_names_best_first(self) -> None:
        out = who_resolve.Settled()
        who_resolve._use(unsettled_of(), asked(("Mara", 55), ("Bram", 45), ("Ken", 30)), out)
        (pick,) = out.picks
        self.assertEqual(pick.candidates, ("Mara", "Bram"))  # (never the named end, Ken)
        self.assertEqual(pick.word, "She")

    def test_a_doubt_with_only_one_name_to_offer_asks_with_that_name_and_someone_else(self) -> None:
        out = who_resolve.Settled()
        who_resolve._use(unsettled_of(), asked(("Mara", 50), (NOT_LISTED, 30), checked=False), out)
        (pick,) = out.picks
        self.assertEqual((pick.candidates, out.unresolved, out.unnamed), (("Mara",), 0, []))
        self.assertEqual(out.counts["asked"], 1)

    def test_someone_not_listed_keeps_the_relationship_with_an_unnamed_person(self) -> None:
        out = who_resolve.Settled()
        who_resolve._use(unsettled_of(), asked((NOT_LISTED, 90), ("Mara", 10)), out)
        (link,) = out.unnamed
        self.assertEqual((link.fixed, link.fixed_is_a, link.how), ("Ken", False, "kin_of"))
        self.assertEqual(out.counts["unnamed"], 1)

    def test_a_family_word_makes_the_unnamed_person_ken_s_sister(self) -> None:
        open_ = unsettled_of()
        from dataclasses import replace

        open_ = replace(open_, made=replace(open_.made, words="sister"))
        out = who_resolve.Settled()
        who_resolve._use(open_, asked((NOT_LISTED, 90)), out)
        (link,) = out.unnamed
        self.assertEqual((link.what, link.anchored, link.role), ("sister", True, "sister"))

    def test_with_no_word_the_unnamed_person_hangs_off_nobody(self) -> None:
        out = who_resolve.Settled()
        who_resolve._use(unsettled_of(), asked((NOT_LISTED, 90)), out)
        (link,) = out.unnamed
        self.assertEqual((link.what, link.anchored), (who_resolve.SOMEONE, False))

    def test_a_failed_call_keeps_the_relationship_and_counts_it(self) -> None:
        out = who_resolve.Settled()
        who_resolve._use(unsettled_of(), None, out)
        self.assertEqual((len(out.unnamed), out.counts["ai_failed"]), (1, 1))

    async def test_the_whole_path_with_a_scripted_ai(self) -> None:
        client = Scripted([("Mara", 95), ("Bram", 5)], "Mara")
        out = await who_resolve.settle([unsettled_of()], client, ["Mara", "Ken", "Bram"])
        self.assertEqual(out.counts["accepted"], 1)
        self.assertEqual(client.calls, 1 + who_ask.SAMPLES)

    async def test_only_names_in_the_passage_are_sent(self) -> None:
        seen: list[str] = []

        class Spy(Scripted):
            async def complete(self, system: str, text: str, **kw: Any) -> Reply:
                seen.append(text)
                return await super().complete(system, text, **kw)

        await who_resolve.settle(
            [unsettled_of()], Spy([("Mara", 95)], "Mara"), ["Mara", "Ken", "Zorgul"]
        )
        self.assertTrue(seen)
        self.assertTrue(all("Zorgul" not in t for t in seen))

    async def test_past_the_cap_the_rest_are_kept_unnamed_counted_and_never_asked(self) -> None:
        client = Scripted([("Mara", 95)], "Mara")
        steps: list[tuple[int, int]] = []
        three = [unsettled_of(), unsettled_of(), unsettled_of()]
        out = await who_resolve.settle(
            three, client, ["Mara", "Ken"], cap=1, progress=lambda d, t: steps.append((d, t))
        )
        self.assertEqual((out.counts["accepted"], out.counts["over_cap"]), (1, 2))
        self.assertEqual(len(out.unnamed), 2)
        self.assertEqual(client.calls, 1 + who_ask.SAMPLES)  # (one lookup, not three)
        self.assertEqual(steps, [(0, 1), (1, 1), (1, 1)])  # each lookup moves the count; ends full

    async def test_past_the_time_budget_the_rest_are_kept_unnamed(self) -> None:
        client = Scripted([("Mara", 95)], "Mara")
        out = await who_resolve.settle([unsettled_of()], client, ["Mara", "Ken"], time_limit_s=-1.0)
        self.assertEqual((out.counts["over_time"], client.calls, len(out.unnamed)), (1, 0, 1))

    async def test_a_lookup_in_flight_is_cut_at_the_deadline(self) -> None:
        class Hangs:
            async def complete(self, *a: Any, **k: Any) -> Reply:
                await asyncio.sleep(30)
                return Reply("", cut=False)

        out = await who_resolve.settle(
            [unsettled_of()], Hangs(), ["Mara", "Ken"], time_limit_s=0.05
        )
        self.assertEqual((out.counts["over_time"], len(out.unnamed)), (1, 1))

    async def test_a_deadline_already_passed_means_no_lookups(self) -> None:
        import time

        client = Scripted([("Mara", 95)], "Mara")
        out = await who_resolve.settle(
            [unsettled_of()], client, ["Mara", "Ken"], until=time.monotonic() - 1
        )
        self.assertEqual((client.calls, out.counts["over_time"]), (0, 1))

    async def test_a_failed_scoring_call_is_a_kept_unnamed_person(self) -> None:
        class Down:
            async def complete(self, *a: Any, **k: Any) -> Reply:
                raise RuntimeError("down")

        out = await who_resolve.settle([unsettled_of()], Down(), ["Mara", "Ken"])
        self.assertEqual((out.counts["ai_failed"], len(out.unnamed)), (1, 1))


class ThePath(NamesTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.store = BackstoryLinks(self.db)
        self.bot.backstory_links = self.store
        made = await self.memory.add_names(
            GUILD, self.campaign.id,
            [NewName("Mara", "npc", CONFIRMED), NewName("Ken", "npc", CONFIRMED)],
            source="dm",
        )  # fmt: skip
        self.mara, self.ken = made.value

    async def run_links(self, client: Any) -> tuple[int, Counter[str]]:
        lost: Counter[str] = Counter()
        queued = await ui_bg.queue_links(
            self.bot, self.memory, self.campaign, [], [[]], set(), dropped=lost,
            unsettled=[unsettled_of()], who_ai=client,
        )  # fmt: skip
        return queued, lost

    async def test_how_it_was_decided_waits_with_the_connection_and_shows_on_its_card(self) -> None:
        await self.run_links(Scripted([("Mara", 95), ("Bram", 5)], "Mara"))
        (w,) = await self.store.waiting(GUILD, self.campaign.id)
        self.assertRegex(w.decided, r"^who-is-it: accepted \d+%$")
        view = ui_bg.ConnectionOffer(self.campaign.id)
        self.assertTrue(await view.load(self.it()))
        self.assertIn("DMbot's check of who it means: accepted", view.text())

    async def test_how_it_was_decided_goes_with_a_question_through_its_answer(self) -> None:
        await self.run_links(Scripted([("Mara", 90), (NOT_LISTED, 5)], None))
        (q,) = await self.store.which_waiting(GUILD, self.campaign.id)
        self.assertEqual(q.decided, "who-is-it: asked 90%")
        view = ui_bg.WhichOffer(self.campaign.id)
        await view.load(self.it())
        self.assertIn("DMbot's check of who it means: asked 90%.", view.text())
        with patch("dmbot.ui.name_background.offer_ready", new=AsyncMock()):
            await view.children[0].callback(self.it())  # Mara
        (w,) = await self.store.waiting(GUILD, self.campaign.id)
        self.assertEqual(w.decided, "who-is-it: asked 90%")

    async def test_an_ordinary_connection_has_no_check_line(self) -> None:
        plain = [[bg.Connection("Mara", "knows", "Ken", "q")]]
        await ui_bg.queue_links(self.bot, self.memory, self.campaign, [], plain, set())
        view = ui_bg.ConnectionOffer(self.campaign.id)
        await view.load(self.it())
        self.assertNotIn("DMbot's check", view.text())

    async def test_off_the_line_is_left_out_and_counted_as_before(self) -> None:
        queued, lost = await self.run_links(None)
        self.assertEqual((queued, lost["not_in_text"]), (0, 1))

    async def test_a_sure_answer_queues_the_connection_between_the_two_names(self) -> None:
        queued, _ = await self.run_links(Scripted([("Mara", 95)], "Mara"))
        self.assertEqual(queued, 1)
        (w,) = await self.store.waiting(GUILD, self.campaign.id)
        self.assertEqual({w.subject_id, w.object_id}, {self.mara, self.ken})

    async def test_a_doubt_queues_a_question_for_the_dm_and_counts_it(self) -> None:
        made = await self.memory.add_names(
            GUILD, self.campaign.id, [NewName("Bram", "npc", CONFIRMED)], source="dm"
        )
        queued, lost = await self.run_links(Scripted([("Mara", 55), ("Bram", 45)], "Bram"))
        self.assertEqual((queued, lost["which_waiting"]), (0, 1))
        self.assertEqual(await self.store.count_which(GUILD, self.campaign.id), 1)
        self.assertTrue(made.value)

    async def test_nobody_to_name_keeps_the_relationship_with_an_unnamed_person(self) -> None:
        queued, lost = await self.run_links(Scripted([(NOT_LISTED, 95)], NOT_LISTED))
        self.assertEqual((queued, lost["not_in_text"]), (1, 0))
        ((entity, label),) = await self.memory.unnamed(GUILD, self.campaign.id)
        self.assertEqual(label, "someone (name unknown)")
        self.assertFalse(entity.hidden_from_players)

    async def test_two_pronouns_for_nobody_are_two_people_never_one_invented_one(self) -> None:
        await ui_bg.queue_links(
            self.bot, self.memory, self.campaign, [], [[]], set(),
            unsettled=[unsettled_of(), unsettled_of()],
            who_ai=Scripted([(NOT_LISTED, 95)], NOT_LISTED),
        )  # fmt: skip
        self.assertEqual(len(await self.memory.unnamed(GUILD, self.campaign.id)), 2)

    async def test_the_same_real_descriptor_in_one_read_is_one_person(self) -> None:
        link = who_resolve.UnnamedLink(
            "Ken", False, "knows", "q", "", "a hooded figure", False, "knows"
        )
        rows = await ui_bg._unnamed_rows(
            self.memory, self.campaign, [link, link], lambda n: self.ken, False, "", Counter()
        )
        self.assertEqual(len({r.subject_id for r in rows}), 1)
        self.assertEqual(len(await self.memory.unnamed(GUILD, self.campaign.id)), 1)

    async def test_the_check_off_changes_nothing_but_the_one_count(self) -> None:
        queued, lost = await self.run_links(None)
        self.assertEqual((queued, dict(lost)), (0, {"not_in_text": 1}))

    async def test_a_doubt_with_one_name_is_a_question_with_someone_else(self) -> None:
        await self.run_links(Scripted([("Mara", 90), (NOT_LISTED, 5)], None))
        (q,) = await self.store.which_waiting(GUILD, self.campaign.id)
        self.assertEqual([name for _, name in q.candidates], ["Mara"])
        view = ui_bg.WhichOffer(self.campaign.id)
        self.assertTrue(await view.load(self.it()))
        labels = [getattr(b, "label", "") for b in view.children]
        self.assertIn("🙋 Someone else", labels)

    async def test_a_second_press_on_someone_else_leaves_one_person_and_one_connection(
        self,
    ) -> None:
        await self.run_links(Scripted([("Mara", 90), (NOT_LISTED, 5)], None))
        first, second = ui_bg.WhichOffer(self.campaign.id), ui_bg.WhichOffer(self.campaign.id)
        await first.load(self.it())
        await second.load(self.it())
        with patch("dmbot.ui.name_background.offer_ready", new=AsyncMock()):
            await first.children[-2].callback(self.it())
            await second.children[-2].callback(self.it())
        self.assertEqual(len(await self.memory.unnamed(GUILD, self.campaign.id)), 1)
        self.assertEqual(len(await self.store.waiting(GUILD, self.campaign.id)), 1)

    async def test_a_full_queue_leaves_no_orphan_person_and_the_question_waits(self) -> None:
        await self.run_links(Scripted([("Mara", 90), (NOT_LISTED, 5)], None))
        view = ui_bg.WhichOffer(self.campaign.id)
        await view.load(self.it())
        with (
            patch("dmbot.memory.backstory_links.MAX_WAITING", 0),
            patch("dmbot.ui.name_background.offer_ready", new=AsyncMock()),
        ):
            await view.children[-2].callback(self.it())
        self.assertEqual(await self.memory.unnamed(GUILD, self.campaign.id), [])
        self.assertEqual(len(await self.store.which_waiting(GUILD, self.campaign.id)), 1)

    async def test_none_of_these_for_a_pronoun_keeps_the_connection_with_an_unnamed_person(
        self,
    ) -> None:
        await self.memory.add_names(
            GUILD, self.campaign.id, [NewName("Bram", "npc", CONFIRMED)], source="dm"
        )
        await self.run_links(Scripted([("Mara", 55), ("Bram", 45)], "Bram"))
        view = ui_bg.WhichOffer(self.campaign.id)
        self.assertTrue(await view.load(self.it()))
        self.assertEqual([getattr(b, "label", "") for b in view.children][-2], "🙋 Someone else")
        with patch("dmbot.ui.name_background.offer_ready", new=AsyncMock()):
            await view.children[-2].callback(self.it())
        self.assertEqual(await self.store.which_waiting(GUILD, self.campaign.id), [])
        (w,) = await self.store.waiting(GUILD, self.campaign.id)
        self.assertIn(self.ken, (w.subject_id, w.object_id))
        self.assertEqual(len(await self.memory.unnamed(GUILD, self.campaign.id)), 1)

    async def test_a_secret_spelling_as_the_named_end_keeps_the_link_secret(self) -> None:
        await self.memory.add_names(
            GUILD, self.campaign.id,
            [NewName("Vecnor", "npc", CONFIRMED, secrets=("Brother Aldric",))],
            source="dm",
        )  # fmt: skip
        text = "Mara arrived with Bram. Dusk fell. She was the sister of Brother Aldric."
        line = {"a": "Mara", "how": "family", "b": "Brother Aldric",
                "quote": "She was the sister of Brother Aldric"}  # fmt: skip
        await ui_bg.queue_links(
            self.bot, self.memory, self.campaign, [], [[]], set(),
            unsettled=[unsettled_of(text, line)],
            who_ai=Scripted([(NOT_LISTED, 95)], NOT_LISTED),
        )  # fmt: skip
        (w,) = await self.store.waiting(GUILD, self.campaign.id)
        self.assertTrue(w.secret)

    async def test_an_unnamed_person_that_cannot_be_made_is_counted_not_fatal(self) -> None:
        lost: Counter[str] = Counter()
        link = who_resolve.UnnamedLink("Ken", False, "knows", "q", "", " ", False, "knows")
        rows = await ui_bg._unnamed_rows(
            self.memory, self.campaign, [link], lambda n: self.ken, False, "", lost
        )
        self.assertEqual((rows, lost["name_not_found"]), ([], 1))

    async def test_no_more_unnamed_people_than_the_read_may_offer(self) -> None:
        lost: Counter[str] = Counter()
        many = [unsettled_of() for _ in range(3)]
        with patch.object(bg, "MAX_CONNECTIONS", 1):
            await ui_bg.queue_links(
                self.bot, self.memory, self.campaign, [], [[]], set(), dropped=lost,
                unsettled=many, who_ai=Scripted([(NOT_LISTED, 95)], NOT_LISTED),
            )  # fmt: skip
        self.assertLessEqual(len(await self.store.waiting(GUILD, self.campaign.id)), 1)
        self.assertEqual(lost["over_cap"], 2)


if __name__ == "__main__":
    unittest.main()
