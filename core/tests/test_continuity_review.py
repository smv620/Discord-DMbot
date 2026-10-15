"""The DM's heads-ups after a session (#1320): the words, the three answers per heads-up, that
only the campaign's DM can answer, and the DM-screen post that says how many and never what."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from dmbot.bot import DMBot
from dmbot.memory.claims import ClaimStore, Extracted
from dmbot.memory.continuity import Levels
from dmbot.memory.models import ALIVE, DEAD
from dmbot.ui import continuity
from tests.test_memory_store import DM, GUILD_A, MemoryTest

STRANGER = 99
SMALL = Levels(min_facts=1, min_sessions=1)


def said(subject: str, relationship: str, obj: str) -> Extracted:
    return Extracted(subject, relationship, obj, "", ("1",), "dm_said")


class Review(MemoryTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.claims = ClaimStore(self.memory)
        self.ulfgar = await self.add("Ulfgar", type="character")
        self.cerric = await self.add("Cerric", type="character")
        self.kesh = await self.add("Kesh", type="character")
        self.bow = await self.add("Heartseeker", type="item")
        await self.relate(self.cerric, "ally_of", self.kesh, status="confirmed", from_session_at=1)
        await self.memory.set_condition(
            GUILD_A, self.c, self.ulfgar, DEAD, source="dm", from_session_at=1
        )
        await self.claims.propose(
            GUILD_A, self.c, [said("Ulfgar", "carries", "Heartseeker")], {"1": 7},
            opted_in={7}, prompt_version="p", model="m", session_started_at=500,
        )  # fmt: skip

    def interaction(self, user: int = DM) -> Any:
        sent: list[str] = []

        async def reply(text: str = "", **kw: Any) -> None:
            sent.append(str(kw.get("content", text)))

        it = MagicMock()
        it.user = SimpleNamespace(id=user)
        it.guild = SimpleNamespace(id=GUILD_A)
        it.client = SimpleNamespace(
            memory=self.memory,
            campaigns=self.campaigns,
            transcripts=None,
            settings=SimpleNamespace(continuity=SMALL),
            consent=SimpleNamespace(consenting=AsyncMock(return_value={7})),
        )
        it.response.is_done = lambda: False
        it.response.defer = AsyncMock()
        it.response.send_message = reply
        it.response.edit_message = reply
        it.followup.send = reply
        it.edit_original_response = reply
        it.sent = sent
        return it

    async def test_the_heads_up_is_in_plain_words_with_three_answers(self) -> None:
        view = continuity.ContinuityReview(self.c)
        text = await view.current_text(self.interaction())
        assert text is not None
        self.assertIn("⚠️ **Probable:** **Ulfgar** died", text)
        self.assertIn("At the table: **Ulfgar** carries **Heartseeker**.", text)
        self.assertEqual(
            [getattr(b, "label", "") for b in view.children],
            ["1 · Alive after all", "1 · Wasn't real", "1 · Ignore"],
        )

    async def test_alive_after_all_writes_the_fact_and_brings_them_back(self) -> None:
        view = continuity.ContinuityReview(self.c)
        it = self.interaction()
        await view.current_text(it)
        claim_id = view.page[0].claim.id
        await view._decide(it, claim_id, "c")
        self.assertEqual((await self.memory.states(GUILD_A, self.c))[self.ulfgar].condition, ALIVE)
        self.assertTrue(it.sent)

    async def test_a_stranger_changes_nothing(self) -> None:
        view = continuity.ContinuityReview(self.c)
        await view.current_text(self.interaction())
        claim_id = view.page[0].claim.id
        stranger = self.interaction(STRANGER)
        await view._decide(stranger, claim_id, "c")
        self.assertEqual((await self.memory.states(GUILD_A, self.c))[self.ulfgar].condition, DEAD)
        (still,) = await self.claims.waiting(GUILD_A, self.c, opted_in={7})
        self.assertEqual(still.status, "proposed")

    async def test_ignore_keeps_it_quiet_for_the_rest_of_the_list(self) -> None:
        view = continuity.ContinuityReview(self.c)
        it = self.interaction()
        await view.current_text(it)
        claim_id = view.page[0].claim.id
        await view._decide(it, claim_id, "i")
        self.assertEqual(view.ignored, {claim_id})
        self.assertIsNone(await view.current_text(it))  # nothing left to show
        (still,) = await self.claims.waiting(GUILD_A, self.c, opted_in={7})
        self.assertEqual(still.status, "proposed")  # and nothing was written

    def table(self) -> Any:
        return SimpleNamespace(guild_id=GUILD_A, campaign_id=self.c, screen_channel_id=5)

    def bot(self) -> Any:
        return SimpleNamespace(
            memory=self.memory,
            post=AsyncMock(),
            settings=SimpleNamespace(continuity=SMALL),
            consent=SimpleNamespace(consenting=AsyncMock(return_value={7})),
        )

    async def test_the_dm_screen_says_how_many_and_never_what(self) -> None:
        fake = self.bot()
        with self.assertLogs("dmbot.bot", level="INFO") as logged:
            await DMBot.warn_about_continuity(fake, self.table())
        text = fake.post.await_args.args[1]
        self.assertIn("1 heads-up", text)
        for word in ("Ulfgar", "Heartseeker", "died"):
            self.assertNotIn(word, text)
        self.assertNotIn("Ulfgar", " ".join(logged.output))  # the log holds counts only

    async def test_below_the_critical_mass_or_with_nothing_clashing_nothing_is_posted(self) -> None:
        fake = self.bot()
        fake.settings = SimpleNamespace(continuity=Levels(min_facts=50, min_sessions=3))
        await DMBot.warn_about_continuity(fake, self.table())
        fake.post.assert_not_called()

    async def test_a_line_from_someone_who_stopped_leaves_the_screen_on_the_next_press(
        self,
    ) -> None:
        # A second heads-up said by speaker 8; then 8 stops being recorded before 7's is answered.
        kesh_bow = say_two = said("Kesh", "carries", "Heartseeker")
        await self.relate(self.cerric, "carries", self.bow, status="confirmed", from_session_at=1)
        await self.claims.propose(
            GUILD_A, self.c, [kesh_bow], {"1": 8}, opted_in={7, 8}, prompt_version="p",
            model="m", session_started_at=500,
        )  # fmt: skip
        assert say_two
        view = continuity.ContinuityReview(self.c)
        it = self.interaction()
        it.client.consent.consenting = AsyncMock(return_value={7, 8})
        first = await view.current_text(it)
        assert first is not None and "**Kesh** carries" in first
        ulfgar = next(c for c in view.page if c.claim.subject == "Ulfgar")
        it.client.consent.consenting = AsyncMock(return_value={7})  # 8 has stopped
        await view._decide(it, ulfgar.claim.id, "i")
        shown = it.sent[-1]
        self.assertNotIn("Kesh", shown)  # the other row's words are gone, not left on screen
