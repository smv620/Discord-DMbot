"""The DM's own words about a name are kept once the name is (#4636 part 2, the owner's "stop
asking the obvious"; Supervisor's ruling): keeping a name keeps the waiting claims the DM's own
lines state about it whose other end is kept too, in the same change, so one Undo takes both back.
A player's line, a plan, a claim whose other end still waits, and one citing someone who stopped
being recorded all stay questions. Invented names. Postgres."""

from __future__ import annotations

from typing import Any

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

    async def ties(self) -> list[str]:
        """Jebidiah's kept connections."""
        rels = await self.memory.relations(GUILD_A, self.c, entity_id=self.jeb, include_secret=True)
        return [r.predicate for r in rels if r.status == "confirmed"]

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
        back = await self.claims.get(GUILD_A, self.c, claim)
        assert back is not None
        self.assertIsNone(back.fact_id)
        self.assertEqual(await self.ties(), [])
        jeb = await self.memory.entity(GUILD_A, self.c, self.jeb)
        assert jeb is not None
        self.assertEqual(jeb.status, "proposed")

    async def test_a_players_words_stay_a_question(self) -> None:
        # (labelled the DM's, but a player spoke the line; with no feeling word, so it is asked)
        claim = await self.ask(speaker=PLAYER, word="")
        await self.keep_jebidiah()
        self.assertEqual(await self.status(claim), CLAIM_PROPOSED)

    async def test_a_claim_labelled_a_players_stays(self) -> None:
        said = await self.ask("player_said")
        await self.keep_jebidiah()
        self.assertEqual(await self.status(said), CLAIM_PROPOSED)

    async def test_a_plan_stays_as_it_was(self) -> None:
        plan = await self.ask("plan")
        before = await self.status(plan)
        await self.keep_jebidiah()
        self.assertEqual(await self.status(plan), before)

    async def test_one_whose_other_end_still_waits_stays_until_that_is_kept(self) -> None:
        keegan = await self.add("Keegan", type="npc", status="proposed")
        claim = await self.ask(obj="Keegan")
        await self.keep_jebidiah()
        self.assertEqual(await self.status(claim), CLAIM_PROPOSED)
        await self.memory.confirm_entity(GUILD_A, self.c, keegan, "npc", source="dm")
        self.assertEqual(await self.status(claim), CLAIM_CONFIRMED)

    async def test_someone_who_stopped_being_recorded_is_never_kept(self) -> None:
        claim = await self.ask()
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "DELETE FROM consent WHERE guild_id = %s AND user_id = %s", (GUILD_A, DM_USER)
            )
        before = await self.status(claim)
        await self.keep_jebidiah()
        self.assertEqual(await self.status(claim), before)
        self.assertNotEqual(before, CLAIM_CONFIRMED)

    async def test_keeping_a_group_of_names_keeps_them_too(self) -> None:
        claim = await self.ask()
        await self.memory.confirm_kinds(GUILD_A, self.c, [self.jeb], "npc", source="dm")
        self.assertEqual(await self.status(claim), CLAIM_CONFIRMED)

    async def test_undo_asks_again_when_the_keep_turned_a_waiting_connection_kept(self) -> None:
        """The connection was waiting already (from an upload): the keep turns it kept, and the
        name's Undo puts it back to waiting, so the claim is asked again too (the reviewer's
        case: the fact was changed, not made, by the keep)."""
        await self.memory.add_relation(
            GUILD_A, self.c, self.jeb, "knows", self.garret, source="extractor", confidence=0.5,
            detail="friendly",
        )  # fmt: skip
        claim = await self.ask()
        rels = await self.memory.relations(GUILD_A, self.c, entity_id=self.jeb, include_secret=True)
        batch = await self.keep_jebidiah()
        self.assertEqual(await self.status(claim), CLAIM_CONFIRMED)
        kept = await self.claims.get(GUILD_A, self.c, claim)
        assert kept is not None
        self.assertEqual(kept.fact_id, rels[0].id)  # (the waiting connection, now kept)
        await self.memory.undo(GUILD_A, self.c, batch, source="dm")
        self.assertEqual(await self.status(claim), CLAIM_PROPOSED)

    async def test_another_campaign_with_the_same_names_is_untouched(self) -> None:
        other = (await self.campaigns.create(GUILD_A, "Other Table", DM_USER)).id
        await self.add("Jebidiah", type="npc", status="confirmed", campaign=other)
        await self.add("Garret", type="npc", status="confirmed", campaign=other)
        claim = await self.ask()
        theirs = await self.claims.propose(
            GUILD_A, other, [Extracted("Jebidiah", "knows", "Garret", "", ("1",), "dm_said")],
            {"1": DM_USER}, opted_in={DM_USER}, dms={DM_USER}, prompt_version="p", model="m",
            session_started_at=START,
        )  # fmt: skip
        del theirs
        waiting = await self.claims.waiting(GUILD_A, other, opted_in={DM_USER})
        await self.keep_jebidiah()
        self.assertEqual(await self.status(claim), CLAIM_CONFIRMED)
        for c in waiting:
            found = await self.claims.get(GUILD_A, other, c.id)
            assert found is not None
            self.assertEqual(found.status, CLAIM_PROPOSED)

    async def test_many_names_with_many_waiting_claims_cost_little_more(self) -> None:
        """Consent is read once for all the claims, not once each (#580's statement budget)."""
        from unittest.mock import patch

        from psycopg import AsyncConnection

        names = [await self.add(f"Guard {n}", type="npc", status="proposed") for n in range(20)]
        await self.claims.propose(
            GUILD_A, self.c,
            [Extracted(f"Guard {n}", "knows", "Garret", "", ("1",), "dm_said") for n in range(20)],
            {"1": DM_USER}, opted_in={DM_USER}, dms={DM_USER}, prompt_version="p", model="m",
            session_started_at=START,
        )  # fmt: skip
        statements = 0
        real = AsyncConnection.execute

        async def counting(conn: Any, *args: Any, **kw: Any) -> Any:
            nonlocal statements
            if "FROM consent" in str(args[0] if args else ""):
                statements += 1
            return await real(conn, *args, **kw)

        with patch.object(AsyncConnection, "execute", counting):
            await self.memory.confirm_kinds(GUILD_A, self.c, names, "npc", source="dm")
        self.assertLessEqual(statements, 1)

    async def test_an_uploads_sure_step_keeps_a_claim_waiting_from_an_earlier_import(self) -> None:
        """Supervisor's follow-up on #4641: the claim waited from an earlier import; a later
        upload's sure step keeps the name (`auto_accept.accept_names`, through `confirm_entity`),
        and the DM's own words about it are kept in that same change."""
        from dmbot.memory import auto_accept as auto

        claim = await self.ask()
        tally = auto.Tally()
        await auto.accept_names(
            self.memory, GUILD_A, self.c, tally, only={self.jeb}, kind_of=lambda e: "npc"
        )
        self.assertEqual(tally.accepted[auto.NAMES], 1)
        self.assertEqual(await self.status(claim), CLAIM_CONFIRMED)
        await self.memory.undo(GUILD_A, self.c, tally.batches[-1], source="dm")
        self.assertEqual(await self.status(claim), CLAIM_PROPOSED)
