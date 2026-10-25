"""The DM places a reading as a belief (#2412, slice 2b): a proposed connection to hold what
nobody has kept, the right view for each rule, nothing changed on a connection that is already
there, one Undo. Needs the test database; invented names throughout."""

from __future__ import annotations

from typing import Any

from dmbot.memory import readings
from dmbot.memory.claims import ClaimStore
from dmbot.memory.models import MemoryRuleError
from tests.test_memory_store import DM, GUILD_A, ConfirmedMemoryTest

PLAYER = 777


def belief(view: str, status: str, confidence: float, **kw: Any) -> readings.BeliefProposal:
    return readings.BeliefProposal(
        view, "Bram", "carries", "Heartseeker", confidence, status, ("1",), **kw
    )


class Placing(ConfirmedMemoryTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.claims = ClaimStore(self.memory)
        self.seen: set[str] = set()
        self.bram = await self.add("Bram")
        self.sword = await self.add("Heartseeker", type="item")
        self.mira = await self.add(
            "Mira", type="character", role="player_character", played_by=PLAYER
        )

    async def waiting_reading(self, proposal: readings.BeliefProposal, how: str = "dm_said") -> str:
        await self.claims.propose(
            GUILD_A, self.c, [readings.as_claim(proposal, how)], {"1": DM},
            opted_in={DM}, dms={DM}, prompt_version="r1", model="m1", session_started_at=1_000,
        )  # fmt: skip
        rows = await self.rows("memory_claims")
        (fresh,) = [r["id"] for r in rows if r["id"] not in self.seen]
        self.seen.add(str(fresh))
        return str(fresh)

    async def place(self, claim_id: str) -> Any:
        return await self.claims.place(GUILD_A, self.c, claim_id, opted_in={DM})

    async def rows(self, table: str) -> list[dict[str, Any]]:
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute(f"SELECT * FROM {table} ORDER BY created_at, id")
            return [dict(r) for r in await cur.fetchall()]

    async def connections(self) -> list[dict[str, Any]]:
        """The connections the test made (a player character's own link to the person who plays
        them is not one of them)."""
        return [r for r in await self.rows("memory_relations") if r["predicate"] != "plays"]

    async def beliefs_of(self, relation_id: str) -> list[dict[str, Any]]:
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute(
                "SELECT b.*, p.kind, p.entity_id FROM memory_beliefs b JOIN memory_perspectives p"
                " ON p.guild_id = b.guild_id AND p.campaign_id = b.campaign_id"
                " AND p.id = b.perspective_id WHERE b.relation_id = %s ORDER BY b.seq",
                (relation_id,),
            )
            return [dict(r) for r in await cur.fetchall()]

    async def test_a_characters_theory_is_a_proposed_secret_connection_and_their_belief(
        self,
    ) -> None:
        claim_id = await self.waiting_reading(belief("pc:Mira", "accusation", 0.75), "player_said")
        placed = await self.place(claim_id)
        (relation,) = await self.connections()
        self.assertEqual(
            (relation["status"], relation["secret"], relation["id"]),
            ("proposed", True, placed.relation_id),
        )
        (mine,) = await self.beliefs_of(relation["id"])
        self.assertEqual(
            (mine["kind"], mine["status"], mine["confidence"]), ("pc", "accusation", 0.75)
        )
        self.assertEqual(mine["session_at"], 1_000)  # belief time: the session it was formed in
        self.assertEqual(mine["evidence_ids"], [claim_id])
        self.assertEqual((await self.rows("memory_claims"))[0]["fact_id"], relation["id"])
        self.assertEqual(await self.claims.waiting(GUILD_A, self.c, opted_in={DM}), [])

    async def test_nothing_of_it_reaches_what_the_players_hold(self) -> None:
        await self.place(await self.waiting_reading(belief("party", "theory", 0.5), "player_said"))
        (host,) = await self.connections()
        held = {b.relation_id for b in await self.memory.party_view(GUILD_A, self.c)}
        self.assertNotIn(host["id"], held)  # not held as fact
        guesses = await self.memory.party_view(GUILD_A, self.c, known_only=False)
        self.assertEqual([b.confidence for b in guesses if b.relation_id == host["id"]], [])
        truth = {b.relation_id for b in await self.memory.truth_view(GUILD_A, self.c)}
        self.assertNotIn(host["id"], truth)  # and not the DM's either

    async def test_a_connection_already_there_is_left_exactly_as_it_is(self) -> None:
        kept = await self.relate(self.bram, "carries", self.sword, status="confirmed")
        before = (await self.connections())[0]
        await self.place(await self.waiting_reading(belief("pc:Mira", "theory", 0.25)))
        after = await self.connections()
        self.assertEqual(after, [before])  # status, secret flag and words untouched
        mine = [b for b in await self.beliefs_of(kept.value[0].id) if b["kind"] == "pc"]
        self.assertEqual([b["confidence"] for b in mine], [0.25])
        self.assertEqual(await self.memory.party_view(GUILD_A, self.c) != [], True)  # still shown

    async def test_a_connection_the_dm_turned_down_is_not_brought_back(self) -> None:
        old = await self.relate(self.bram, "carries", self.sword, status="proposed")
        await self.memory.update_relation(
            GUILD_A, self.c, old.value[0].id, source="dm", status="rejected"
        )
        await self.place(await self.waiting_reading(belief("pc:Mira", "theory", 0.5)))
        by_status = sorted((r["status"], r["secret"]) for r in await self.connections())
        self.assertEqual(by_status, [("proposed", True), ("rejected", False)])

    async def test_a_reveal_is_kept_shown_with_the_claim_as_its_evidence(self) -> None:
        claim_id = await self.waiting_reading(
            belief("party", "revealed_fact", 1.0, because="the ledger"), "dm_said"
        )
        placed = await self.place(claim_id)
        (relation,) = await self.connections()
        self.assertEqual((relation["status"], relation["secret"]), ("confirmed", False))
        latest = (await self.beliefs_of(placed.relation_id))[-1]
        self.assertEqual((latest["kind"], latest["status"]), ("party", "revealed_fact"))
        self.assertEqual(latest["evidence_ids"], [claim_id])
        held = {b.relation_id for b in await self.memory.party_view(GUILD_A, self.c)}
        self.assertIn(placed.relation_id, held)

    async def test_a_reveal_of_a_secret_connection_is_refused_and_writes_nothing(self) -> None:
        await self.relate(self.bram, "carries", self.sword, status="confirmed", secret=True)
        before = await self.snapshot()
        claim_id = await self.waiting_reading(belief("party", "revealed_fact", 1.0))
        with self.assertRaises(MemoryRuleError):
            await self.place(claim_id)
        self.assertEqual(await self.rows("memory_claims") != [], True)
        self.assertEqual(
            [r for r in (await self.snapshot())["memory_relations"] if r["predicate"] != "plays"],
            [r for r in before["memory_relations"] if r["predicate"] != "plays"],
        )
        self.assertEqual((await self.snapshot())["memory_beliefs"], before["memory_beliefs"])

    async def test_a_false_clue_is_false_evidence_in_the_dms_view_and_the_plot_is_a_secret(
        self,
    ) -> None:
        false = await self.waiting_reading(belief("truth", "false_evidence", 0.0, dm_confirms=True))
        placed = await self.place(false)
        (truth,) = await self.beliefs_of(placed.relation_id)
        self.assertEqual(
            (truth["kind"], truth["status"], truth["confidence"]), ("truth", "false_evidence", 0.0)
        )
        plot = readings.BeliefProposal(
            "truth", "Bram", "framed", "Mira", 1.0, "secret_plot", ("1",),
            new_assertion=True, dm_confirms=True,
        )  # fmt: skip
        again = await self.place(await self.waiting_reading(plot))
        rel = next(r for r in await self.connections() if r["id"] == again.relation_id)
        self.assertEqual((rel["status"], rel["secret"]), ("confirmed", True))
        self.assertEqual((await self.beliefs_of(again.relation_id))[0]["status"], "secret_plot")

    async def test_an_unknown_character_or_a_state_is_refused_before_anything_is_written(
        self,
    ) -> None:
        for view in ("pc:Nobody", "pc:Heartseeker"):  # not known; known but not a character
            with self.subTest(view):
                claim_id = await self.waiting_reading(belief(view, "theory", 0.5))
                with self.assertRaises(MemoryRuleError):
                    await self.place(claim_id)
        self.assertEqual(await self.connections(), [])

    async def test_one_undo_puts_the_question_and_everything_back(self) -> None:
        claim_id = await self.waiting_reading(belief("pc:Mira", "theory", 0.5))
        placed = await self.place(claim_id)
        await self.memory.undo(
            GUILD_A, self.c, placed.batch, after=lambda w: self.claims.reopen_in(w, claim_id)
        )
        self.assertEqual(await self.connections(), [])
        self.assertEqual(
            len(
                await self.claims.waiting(
                    GUILD_A,
                    self.c,
                    opted_in={DM},
                )
                or []
            ),
            0,
        )  # (readings stay out of lists)
        self.assertEqual((await self.rows("memory_claims"))[0]["status"], "proposed")

    async def test_someone_who_stopped_being_recorded_deletes_the_question(self) -> None:
        claim_id = await self.waiting_reading(belief("pc:Mira", "theory", 0.5))
        with self.assertRaises(MemoryRuleError):
            await self.claims.place(GUILD_A, self.c, claim_id, opted_in=set())
        self.assertEqual(await self.rows("memory_claims"), [])
        self.assertEqual(await self.connections(), [])

    async def test_a_non_player_character_holds_a_view_of_their_own_kind(self) -> None:
        placed = await self.place(await self.waiting_reading(belief("pc:Bram", "theory", 0.5)))
        (mine,) = [b for b in await self.beliefs_of(placed.relation_id) if b["kind"] != "truth"]
        self.assertEqual(mine["kind"], "npc")

    async def test_a_placed_question_is_gone(self) -> None:
        claim_id = await self.waiting_reading(belief("pc:Mira", "theory", 0.5))
        await self.place(claim_id)
        with self.assertRaises(MemoryRuleError):
            await self.place(claim_id)


class ReadersAndWriters(Placing):
    async def test_the_players_recall_and_the_dm_screen_do_not_hear_a_theory_as_fact(self) -> None:
        from dmbot.memory.recall import DM_SCREEN, PLAYERS, RecallService

        await self.place(await self.waiting_reading(belief("party", "theory", 0.5), "player_said"))
        service = RecallService(self.memory)
        for audience, confirmed_only in ((PLAYERS, False), (DM_SCREEN, True)):
            with self.subTest(audience):
                got = await service.recall(
                    GUILD_A, self.c, seeds=[self.bram], audience=audience,
                    opted_in={DM}, confirmed_only=confirmed_only,
                )  # fmt: skip
                text = " ".join(f"{i.text}" for i in got.items)
                self.assertNotIn("Heartseeker", text)

    async def test_the_dm_keeping_the_same_words_takes_the_secret_flag_with_them(self) -> None:
        placed = await self.place(await self.waiting_reading(belief("pc:Mira", "theory", 0.5)))
        kept = await self.relate(self.bram, "carries", self.sword, status="confirmed")
        self.assertEqual(kept.value[0].id, placed.relation_id)  # the same connection
        (relation,) = await self.connections()
        self.assertEqual((relation["status"], relation["secret"]), ("confirmed", False))
        self.assertIn(  # the players now hold it, and Mira's guess is still hers
            placed.relation_id,
            {b.relation_id for b in await self.memory.party_view(GUILD_A, self.c)},
        )
        mine = [b for b in await self.beliefs_of(placed.relation_id) if b["kind"] == "pc"]
        self.assertEqual([b["confidence"] for b in mine], [0.5])

    async def test_a_theory_does_not_raise_flags_on_facts_and_facts_do_not_flag_it(self) -> None:
        # a place can be inside only one place; a theory of a second is no clash with the fact
        keep, inn, hall = [await self.add(n, type="place") for n in ("Keep", "Inn", "Hall")]
        await self.relate(inn, "located_in", keep, status="confirmed")
        reading = readings.BeliefProposal(
            "pc:Mira", "Inn", "located in", "Hall", 0.5, "theory", ("1",)
        )
        placed = await self.place(await self.waiting_reading(reading, "player_said"))
        self.assertEqual(placed.flags, ())
        self.assertEqual(len(await self.connections()), 2)  # the fact and the waiting theory
        self.assertNotEqual(hall, keep)

    async def test_merging_two_names_keeps_what_was_believed_of_the_folded_theory(self) -> None:
        twin = await self.add("Bramwell")
        await self.relate(twin, "carries", self.sword, status="confirmed")
        placed = await self.place(await self.waiting_reading(belief("pc:Mira", "theory", 0.25)))
        await self.memory.merge(GUILD_A, self.c, twin, self.bram, source="dm", dm_said_same=True)
        (relation,) = await self.connections()
        self.assertNotEqual(relation["id"], placed.relation_id)  # the twin stands for it
        mine = [b for b in await self.beliefs_of(relation["id"]) if b["kind"] == "pc"]
        self.assertEqual([b["confidence"] for b in mine], [0.25])

    async def test_a_shared_guess_never_hides_a_connection_the_players_know(self) -> None:
        known = await self.relate(self.bram, "carries", self.sword, status="confirmed")
        for status, confidence in (("theory", 0.5), ("evasion_under_magic", 0.7)):
            with self.subTest(status):
                claim_id = await self.waiting_reading(belief("party", status, confidence))
                with self.assertRaises(MemoryRuleError):
                    await self.place(claim_id)
        held = {b.relation_id for b in await self.memory.party_view(GUILD_A, self.c)}
        self.assertIn(known.value[0].id, held)

    async def test_a_merge_never_lowers_what_the_players_know_to_a_guess(self) -> None:
        twin = await self.add("Bramwell")
        known = await self.relate(twin, "carries", self.sword, status="confirmed")
        await self.place(await self.waiting_reading(belief("party", "theory", 0.5)))
        await self.memory.merge(GUILD_A, self.c, twin, self.bram, source="dm", dm_said_same=True)
        (one,) = await self.connections()  # the two are one fact now, and the players hold it
        held = {b.relation_id for b in await self.memory.party_view(GUILD_A, self.c)}
        self.assertIn(one["id"], held)
        self.assertEqual((one["status"], one["secret"]), ("confirmed", False))
        self.assertIsNotNone(known)

    async def test_stop_recording_me_takes_a_placed_theory_with_it(self) -> None:
        claim_id = await self.waiting_reading(belief("pc:Mira", "accusation", 0.75), "player_said")
        placed = await self.place(claim_id)
        self.assertEqual(await self.claims.forget_speaker(GUILD_A, DM), 1)
        self.assertEqual(await self.rows("memory_claims"), [])  # their words are gone
        (host,) = await self.connections()
        self.assertEqual(host["detail"], "")  # and no wording of theirs sits on the connection
        (mine,) = [b for b in await self.beliefs_of(placed.relation_id) if b["kind"] == "pc"]
        self.assertEqual(mine["status"], "accusation")  # what the character believed stays

    async def test_a_reading_from_a_hostile_file_is_refused_not_a_crash(self) -> None:
        from psycopg.types.json import Jsonb

        claim_id = await self.waiting_reading(belief("pc:Mira", "theory", 0.5))
        (stored,) = [c for c in await self.rows("memory_claims") if c["id"] == claim_id]
        hostile: list[dict[str, Any]] = [
            {"confidence": "x"}, {"confidence": None}, {"confidence": [1]},
            {"confidence": 10**400}, {"confidence": 2}, {"confidence": True},
            {"status": "secret_plot", "confidence": 1.0},  # a plot is the DM's view only
            {"view": "truth", "status": "revealed_fact", "confidence": 1.0},
            {"view": "party", "status": "false_evidence", "confidence": 0.0},
            {"view": "pc:", "status": "theory", "confidence": 0.5}, {"view": 5},
        ]  # fmt: skip
        for change in hostile:
            with self.subTest(str(change)[:60]):
                async with self.db.guild(GUILD_A) as conn:
                    await conn.execute(
                        "UPDATE memory_claims SET reading = %s WHERE id = %s",
                        (Jsonb({**stored["reading"], **change}), claim_id),
                    )
                with self.assertRaises(MemoryRuleError):
                    await self.place(claim_id)
        self.assertEqual(await self.connections(), [])

    async def test_another_proposed_secret_connection_is_not_made_public_by_a_restatement(
        self,
    ) -> None:
        theirs = await self.memory.add_relation(
            GUILD_A, self.c, self.bram, "carries", self.sword,
            source="extractor", status="proposed", secret=True, confidence=0.5,
        )  # fmt: skip
        await self.relate(self.bram, "carries", self.sword, status="confirmed")
        (relation,) = await self.connections()
        self.assertEqual((relation["id"], relation["secret"]), (theirs.value[0].id, True))

    async def test_keeping_a_waiting_theory_judges_it_as_a_fact_then(self) -> None:
        keep, inn, hall = [await self.add(n, type="place") for n in ("Keep", "Inn", "Hall")]
        await self.relate(inn, "located_in", keep, status="confirmed")
        reading = readings.BeliefProposal(
            "pc:Mira", "Inn", "located in", "Hall", 0.5, "theory", ("1",)
        )
        placed = await self.place(await self.waiting_reading(reading, "player_said"))
        self.assertEqual(await self.memory.flags(GUILD_A, self.c), [])
        await self.memory.update_relation(
            GUILD_A, self.c, placed.relation_id, source="dm", status="confirmed"
        )
        self.assertNotEqual(await self.memory.flags(GUILD_A, self.c), [])
        self.assertNotEqual(hall, keep)
