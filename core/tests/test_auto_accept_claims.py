"""An import adds the connections the DM's own lines state (#3897), through the claims review's own
yes, and only those: a player's line, a plan, a session that is not the import's, and someone who
stopped being recorded all stay asked or are dropped. Invented names."""

from __future__ import annotations

from dmbot.memory import auto_accept as auto
from dmbot.memory.claims import ClaimStore, Extracted
from dmbot.memory.models import CLAIM_CONFIRMED
from tests.test_memory_store import GUILD_A, ConfirmedMemoryTest

SPEAKER, STRANGER = 7, 99
START = 1_000


class DmClaims(ConfirmedMemoryTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.claims = ClaimStore(self.memory)
        self.bram = await self.add("Bram", type="character")
        self.bow = await self.add("Heartseeker", type="item")

    async def ask(self, how: str = "dm_said", *, start: int = START) -> str:
        await self.claims.propose(
            GUILD_A,
            self.c,
            [Extracted("Bram", "carries", "Heartseeker", "", ("1",), how)],
            {"1": SPEAKER},
            opted_in={SPEAKER},
            prompt_version="p",
            model="m",
            session_started_at=start,
        )
        found = await self.claims.waiting(GUILD_A, self.c, opted_in={SPEAKER})
        return found[0].id if found else ""

    async def run_rule(
        self,
        *,
        dms: set[int] = frozenset({SPEAKER}),  # type: ignore[assignment]
        opted_in: set[int] = frozenset({SPEAKER}),  # type: ignore[assignment]
        starts: set[int] = frozenset({START}),  # type: ignore[assignment]
        dry_run: bool = False,
    ) -> auto.Tally:
        tally = auto.Tally()
        await auto.accept_dm_claims(
            self.memory, GUILD_A, self.c, tally,
            dm_ids=dms, opted_in=opted_in, sessions=starts, dry_run=dry_run,
        )  # fmt: skip
        return tally

    async def status(self, claim_id: str) -> str:
        found = await self.claims.get(GUILD_A, self.c, claim_id)
        assert found is not None
        return found.status

    async def test_a_connection_the_dms_own_line_states_is_kept_with_an_undo(self) -> None:
        claim = await self.ask()
        tally = await self.run_rule()
        self.assertEqual(tally.accepted[auto.CONNECTIONS], 1)
        self.assertEqual(await self.status(claim), CLAIM_CONFIRMED)
        self.assertEqual(
            len(tally.batches), 1
        )  # (one change number, so Undo this upload covers it)

    async def test_a_players_line_stays_asked_even_when_the_ai_called_it_the_dms(self) -> None:
        claim = await self.ask()  # labelled dm_said, but the cited line is a player's
        tally = await self.run_rule(dms={STRANGER})
        self.assertEqual(tally.accepted[auto.CONNECTIONS], 0)
        self.assertEqual(tally.waiting[auto.CONNECTIONS], 1)
        self.assertEqual(await self.status(claim), "proposed")

    async def test_a_claim_labelled_as_a_players_stays_asked(self) -> None:
        claim = await self.ask("player_said")
        tally = await self.run_rule()
        self.assertEqual(tally.accepted[auto.CONNECTIONS], 0)
        self.assertEqual(await self.status(claim), "proposed")

    async def test_a_plan_is_never_kept(self) -> None:
        await self.ask("plan")
        tally = await self.run_rule()
        self.assertEqual(tally.accepted[auto.CONNECTIONS], 0)

    async def test_another_sessions_claim_is_not_this_imports_to_answer(self) -> None:
        claim = await self.ask(start=START + 5)
        tally = await self.run_rule()
        self.assertEqual(sum(tally.accepted.values()) + sum(tally.waiting.values()), 0)
        self.assertEqual(await self.status(claim), "proposed")

    async def test_someone_who_stopped_being_recorded_adds_nothing(self) -> None:
        claim = await self.ask()
        tally = await self.run_rule(opted_in={STRANGER})
        self.assertEqual(tally.accepted[auto.CONNECTIONS], 0)
        self.assertEqual(await self.status(claim), "dropped")  # (as the review itself drops it)

    async def test_a_dry_run_counts_and_writes_nothing(self) -> None:
        claim = await self.ask()
        tally = await self.run_rule(dry_run=True)
        self.assertEqual(tally.accepted[auto.CONNECTIONS], 1)
        self.assertEqual(await self.status(claim), "proposed")
