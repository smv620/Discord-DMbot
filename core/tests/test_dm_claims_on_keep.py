"""The DM's own words about a name are kept once the name is (#4636 part 2, the owner's "stop
asking the obvious"; Supervisor's ruling): keeping a name keeps the waiting claims the DM's own
lines state about it whose other end is kept too, in the same change, so one Undo takes both back.
A player's line, a plan, a claim whose other end still waits, and one citing someone who stopped
being recorded all stay questions. Invented names. Postgres."""

from __future__ import annotations

from dmbot.memory._changes import CLAIMS
from dmbot.memory.claims import ClaimStore, Extracted
from dmbot.memory.models import CLAIM_CONFIRMED, CLAIM_PROPOSED
from tests.agreed import agree
from tests.test_memory_store import DM as DM_USER
from tests.test_memory_store import GUILD_A, MemoryTest

PLAYER = 31
START = 1_000


class KeepingANameKeepsTheDmsWords(MemoryTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.claims = ClaimStore(self.memory)
        await agree(self.db, GUILD_A, DM_USER, PLAYER)
        # (Jebidiah is new, waiting for the DM's answer; Garret is kept already)
        self.jeb = await self.add("Jebidiah", type="npc", status="proposed")
        self.garret = await self.add("Garret", type="npc", status="confirmed")

    async def ask(
        self,
        how: str = "dm_said",
        speaker: int = DM_USER,
        obj: str = "Garret",
        word: str = "friendly",
    ) -> str:
        await self.claims.propose(
            GUILD_A, self.c, [Extracted("Jebidiah", "knows", obj, word, ("1",), how)],
            {"1": speaker}, opted_in={DM_USER, PLAYER}, dms={DM_USER}, prompt_version="p",
            model="m", session_started_at=START,
        )  # fmt: skip
        found = await self.claims.waiting(GUILD_A, self.c, opted_in={DM_USER, PLAYER})
        mine = [c for c in found if c.object == obj and c.how == how]
        if mine:
            return mine[0].id
        async with self.memory._read(GUILD_A, self.c) as scope:  # (held while its name waits)
            rows = await scope.select(CLAIMS, " ORDER BY created_at")
        return str(rows[-1]["id"])

    async def status(self, claim_id: str) -> str:
        found = await self.claims.get(GUILD_A, self.c, claim_id)
        assert found is not None
        return found.status

    async def keep_jebidiah(self) -> int:
        done = await self.memory.confirm_entity(GUILD_A, self.c, self.jeb, "npc", source="dm")
        assert done.batch is not None
        return done.batch

    async def test_the_dms_own_words_are_kept_with_the_name_and_one_undo_takes_both(self) -> None:
        claim = await self.ask()
        batch = await self.keep_jebidiah()
        self.assertEqual(await self.status(claim), CLAIM_CONFIRMED)
        await self.memory.undo(GUILD_A, self.c, batch, source="dm")
        self.assertEqual(await self.status(claim), CLAIM_PROPOSED)
        jeb = await self.memory.entity(GUILD_A, self.c, self.jeb)
        assert jeb is not None
        self.assertEqual(jeb.status, "proposed")

    async def test_a_players_words_stay_a_question(self) -> None:
        # (labelled the DM's, but a player spoke the line; with no feeling word, so it is asked)
        claim = await self.ask(speaker=PLAYER, word="")
        await self.keep_jebidiah()
        self.assertEqual(await self.status(claim), CLAIM_PROPOSED)

    async def test_a_claim_labelled_a_players_or_a_plan_stays(self) -> None:
        said = await self.ask("player_said")
        await self.keep_jebidiah()
        self.assertEqual(await self.status(said), CLAIM_PROPOSED)

    async def test_one_whose_other_end_still_waits_stays_until_that_is_kept(self) -> None:
        await self.add("Keegan", type="npc", status="proposed")
        claim = await self.ask(obj="Keegan")
        await self.keep_jebidiah()
        self.assertEqual(await self.status(claim), CLAIM_PROPOSED)

    async def test_someone_who_stopped_being_recorded_is_never_kept(self) -> None:
        claim = await self.ask()
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "DELETE FROM consent WHERE guild_id = %s AND user_id = %s", (GUILD_A, DM_USER)
            )
        await self.keep_jebidiah()
        self.assertNotEqual(await self.status(claim), CLAIM_CONFIRMED)

    async def test_keeping_a_group_of_names_keeps_them_too(self) -> None:
        claim = await self.ask()
        await self.memory.confirm_kinds(GUILD_A, self.c, [self.jeb], "npc", source="dm")
        self.assertEqual(await self.status(claim), CLAIM_CONFIRMED)
