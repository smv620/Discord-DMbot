"""A visit is an event, not where someone is now (#4530, Supervisor's ruling): the question asks
whether the visit happened, with when; the newest visit by the session (and the later line in one
session) is where they were last seen, every older one is history ("was in"), a visit confirmed
late never moves the mover back, and a "not true" ends nothing else. Invented names."""

from __future__ import annotations

import calendar
import unittest
from typing import Any

from dmbot.memory import place_words
from dmbot.memory.claims import Claim, ClaimStore
from dmbot.ui import claims_review
from tests.test_memory_claims import said
from tests.test_memory_store import GUILD_A, ConfirmedMemoryTest

NOV_15 = calendar.timegm((2026, 11, 15, 20, 0, 0))


def claim(relationship: str, *, session: int | None = NOV_15, details: str = "") -> Claim:
    return Claim(
        id="c" * 32, subject="the party", relationship=relationship, object="Bryn Shander",
        details=details, how="dm_said", lines=("4",), speaker_ids=(), source="dm",
        prompt_version="p", model="m", status="proposed", reason="",
        session_started_at=session, created_at=1, decided_at=None, fact_id=None,
    )  # fmt: skip


class TheQuestion(unittest.TestCase):
    def test_a_visit_asks_whether_it_happened_with_its_session(self) -> None:
        text = claims_review.question_text(
            claim("was last seen in", details="reached the town by the road")
        )
        self.assertTrue(
            text.startswith(
                "🗺️ **the party** went to **Bryn Shander** (reached the town by the road): "
                "session of 15 Nov?"
            ),
            text,
        )
        self.assertNotIn("last seen", text)  # (never where they are now)

    def test_the_new_word_and_the_old_word_ask_the_same(self) -> None:
        self.assertEqual(
            claims_review.question_text(claim("went to")),
            claims_review.question_text(claim("was last seen in")),
        )

    def test_with_no_time_it_asks_at_some_point(self) -> None:
        text = claims_review.question_text(claim("went to", session=None))
        self.assertTrue(text.startswith("🗺️ **the party** was in **Bryn Shander** at some point?"))

    def test_an_upload_names_its_label(self) -> None:
        asked = place_words.visit_question("Bram", "Veldmoor", "", "in the March notes")
        self.assertEqual(asked, "🗺️ **Bram** went to **Veldmoor**: in the March notes?")

    def test_the_lore_reader_is_offered_went_to_never_last_seen_or_was_in(self) -> None:
        self.assertIn("went to", place_words.WORDS_OFFERED)
        self.assertNotIn("was last seen in", place_words.WORDS_OFFERED)
        self.assertNotIn("was in", place_words.WORDS_OFFERED)

    def test_was_in_is_a_visit_word_only_for_something_that_moves(self) -> None:
        self.assertEqual(place_words.forward("was in", "event"), "took place in")
        self.assertEqual(place_words.forward("was in", "place"), "is in")
        self.assertEqual(place_words.forward("was in", "character"), "was in")

    def test_other_place_words_are_not_visits(self) -> None:
        for words in ("lives in", "works in", "comes from", "was in", "carries"):
            with self.subTest(words):
                self.assertFalse(place_words.is_visit(words))
        self.assertTrue(claims_review.question_text(claim("lives in")).startswith("**the party"))


class TheCards(unittest.IsolatedAsyncioTestCase):
    """#4569: an upload's waiting visit asks about the visit, with the file it came from; an
    ended old sighting reads "was in" on the connection card (words only)."""

    def card(self, detail: str, upload: str = "Bram's backstory, 3 Nov") -> str:
        from dmbot.memory.backstory_links import Waiting
        from dmbot.ui import name_background as ui_bg

        view = ui_bg.ConnectionOffer("c1")
        view.current = Waiting(
            subject_id="a" * 32, predicate="located_in", object_id="b" * 32, quote="He went.",
            secret=False, subject="Bram", object="Veldmoor", ready=True, detail=detail,
            upload=upload,
        )  # fmt: skip
        view.total = 1
        return view._card()

    async def test_an_uploads_visit_asks_with_its_file(self) -> None:
        text = self.card("went to")
        self.assertIn("🗺️ **Bram** went to **Veldmoor**: in Bram's backstory, 3 Nov?", text)
        self.assertNotIn("last seen", text)

    async def test_with_no_file_it_asks_at_some_point(self) -> None:
        self.assertIn("**Bram** was in **Veldmoor** at some point?", self.card("went to", ""))

    async def test_a_home_is_asked_as_before(self) -> None:
        self.assertIn("**Bram** lives in **Veldmoor**", self.card("lives in"))

    def test_an_ended_sighting_reads_was_in(self) -> None:
        from dmbot.memory.link_words import link_words

        self.assertEqual(link_words("located_in", "was last seen in", ended=True), "was in")
        self.assertEqual(link_words("located_in", "was last seen in", subject_kind="character"),
                         "was last seen in")  # fmt: skip
        self.assertEqual(link_words("located_in", "lives in", ended=True), "lives in")

    def test_the_connection_card_says_was_in_for_an_old_sighting(self) -> None:
        from types import SimpleNamespace

        from dmbot.ui.name_card import fact_words

        old: Any = SimpleNamespace(
            predicate="located_in", detail="was last seen in", directional=False, knows_how="",
            route_state="", to_session_at=900,
        )  # fmt: skip
        self.assertEqual(fact_words(old, "character"), "was in")


class InTheStore(ConfirmedMemoryTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.claims = ClaimStore(self.memory)
        self.bram = await self.add("Bram", type="character")
        self.town = await self.add("Veldmoor", type="place")
        self.chapel = await self.add("Chapel", type="place")

    async def visit(self, place: str, line: str, *, session: int = 500) -> Any:
        """One claim that Bram went to `place`, said on `line` of the session, waiting."""
        await self.claims.propose(
            GUILD_A, self.c, [said("Bram", "went to", place, "dm_said", line)],
            {"1": 7, "2": 8, "3": 7}, opted_in={7}, prompt_version="p1", model="m1",
            session_started_at=session,
        )  # fmt: skip
        (waiting,) = [
            w for w in await self.claims.waiting(GUILD_A, self.c, opted_in={7, 8})
            if w.object == place
        ]  # fmt: skip
        return waiting

    async def places(self) -> dict[str, tuple[str, int | None]]:
        """Bram's place ties: place id -> (its words, its end)."""
        return {
            r.object_id: (r.detail, r.to_session_at)
            for r in await self.memory.relations(GUILD_A, self.c, include_secret=True)
            if r.subject_id == self.bram and r.predicate == "located_in"
        }

    async def test_in_one_session_the_later_line_is_last_seen_whatever_the_order_kept(
        self,
    ) -> None:
        later = await self.visit("Veldmoor", "3")
        earlier = await self.visit("Chapel", "1")
        await self.claims.confirm(GUILD_A, self.c, later.id, opted_in={7})
        await self.claims.confirm(GUILD_A, self.c, earlier.id, opted_in={7})  # (kept second)
        got = await self.places()
        self.assertEqual(got[self.town], ("was last seen in", None))  # the newest
        self.assertEqual(got[self.chapel][0], "was in")  # history, though kept last

    async def test_a_visit_confirmed_late_never_moves_the_mover_back(self) -> None:
        newer = await self.visit("Veldmoor", "1", session=900)
        older = await self.visit("Chapel", "1", session=500)
        await self.claims.confirm(GUILD_A, self.c, newer.id, opted_in={7})
        await self.claims.confirm(GUILD_A, self.c, older.id, opted_in={7})
        got = await self.places()
        self.assertEqual(got[self.town], ("was last seen in", None))
        self.assertEqual(got[self.chapel], ("was in", 900))

    async def test_not_true_on_a_visit_ends_nothing(self) -> None:
        kept = await self.visit("Veldmoor", "1", session=500)
        await self.claims.confirm(GUILD_A, self.c, kept.id, opted_in={7})
        before = await self.places()
        wrong = await self.visit("Chapel", "1", session=900)
        await self.claims.reject(GUILD_A, self.c, wrong.id)
        self.assertEqual(await self.places(), before)

    async def test_two_visits_are_never_flagged_for_the_dm(self) -> None:
        # (the same line: nothing orders them, the case the ruling's B is about)
        for place, line in (("Veldmoor", "1"), ("Chapel", "1")):
            await self.claims.confirm(GUILD_A, self.c, (await self.visit(place, line)).id,
                                      opted_in={7})  # fmt: skip
        self.assertEqual(await self.memory.flags(GUILD_A, self.c), [])


if __name__ == "__main__":
    unittest.main()
