"""The DM places a reading as a belief (#2412, slice 2b): a proposed connection to hold what
nobody has kept, the right view for each rule, nothing changed on a connection that is already
there, one Undo. Needs the test database; invented names throughout."""

from __future__ import annotations

import json
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
            cur = await conn.execute(f"SELECT * FROM {table} ORDER BY id")
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

    async def links_of(self, belief_id: str) -> list[tuple[str, str | None]]:
        rows = await self.rows("memory_evidence")
        return sorted((e["kind"], e["target_id"]) for e in rows if e["belief_id"] == belief_id)

    async def test_the_clue_a_reveal_names_becomes_a_link_on_its_belief(self) -> None:
        ledger = await self.add("The Ledger", type="clue")
        claim_id = await self.waiting_reading(
            belief("party", "revealed_fact", 1.0, because="the ledger"), "dm_said"
        )
        placed = await self.place(claim_id)
        latest = (await self.beliefs_of(placed.relation_id))[-1]
        self.assertEqual(
            await self.links_of(latest["id"]), sorted([("claim", claim_id), ("clue", ledger)])
        )

    async def test_a_frame_up_links_the_event_that_faked_it(self) -> None:
        ledger = await self.add("The Ledger", type="clue")
        planted = await self.add("The Planted Ledger", type="event")
        await self.relate(self.bram, "carries", self.sword, status="confirmed")
        proposal = belief(
            "truth",
            "false_evidence",
            0.0,
            because="the ledger",
            manipulated_by="the planted ledger",
        )
        placed = await self.place(await self.waiting_reading(proposal, "dm_said"))
        latest = (await self.beliefs_of(placed.relation_id))[-1]
        links = await self.links_of(latest["id"])
        self.assertIn(("event", planted), links)
        self.assertIn(("clue", ledger), links)  # both of its reasons

    async def test_a_name_that_finds_nothing_or_is_not_a_clue_or_event_adds_no_link(self) -> None:
        await self.add("Heartseeker II", type="item")
        await self.add("Old Tom", type="clue", status="proposed")  # still waiting for the DM
        for n, reason in enumerate(("the missing ledger", "Mira", "Old Tom", "Heartseeker II")):
            with self.subTest(reason):
                await self.add(f"Trinket {n}", type="item")  # (a fresh guess each time)
                proposal = readings.BeliefProposal(
                    "pc:Mira", "Bram", "carries", f"Trinket {n}", 0.5, "theory", ("1",),
                    because=reason,
                )  # fmt: skip
                claim_id = await self.waiting_reading(proposal, "player_said")
                placed = await self.place(claim_id)
                latest = (await self.beliefs_of(placed.relation_id))[-1]
                self.assertEqual(await self.links_of(latest["id"]), [("claim", claim_id)])

    async def test_a_name_that_fits_two_entries_adds_no_link(self) -> None:
        letter = await self.add("Letter", type="clue")
        await self.memory.add_alias(
            GUILD_A, self.c, letter, "the note", kind="nickname", source="dm", status="confirmed"
        )
        await self.add("The Note", type="event")
        claim_id = await self.waiting_reading(belief("pc:Mira", "theory", 0.5, because="the note"))
        placed = await self.place(claim_id)
        latest = (await self.beliefs_of(placed.relation_id))[-1]
        self.assertEqual(await self.links_of(latest["id"]), [("claim", claim_id)])

    async def test_a_secret_plot_links_its_reason_on_the_connection(self) -> None:
        ledger = await self.add("The Ledger", type="clue")
        claim_id = await self.waiting_reading(
            belief("truth", "secret_plot", 1.0, because="the ledger"), "dm_said"
        )
        placed = await self.place(claim_id)
        links = [
            e for e in await self.rows("memory_evidence") if e["relation_id"] == placed.relation_id
        ]
        self.assertIn(("clue", ledger), [(e["kind"], e["target_id"]) for e in links])

    async def test_a_placed_reading_whose_clue_found_no_entry_is_counted_on_the_health_line(
        self,
    ) -> None:
        from dmbot.memory import health

        async def unlinked() -> int:
            return (await health.read(self.memory, GUILD_A, self.c, since=0)).readings_unlinked

        await self.add("The Ledger", type="clue")
        self.assertEqual(await unlinked(), 0)
        await self.place(
            await self.waiting_reading(belief("pc:Mira", "theory", 0.4, because="the ledger"))
        )
        await self.place(  # (named, but nothing by that name: placed, not linked, counted)
            await self.waiting_reading(
                readings.BeliefProposal(
                    "pc:Mira", "Mira", "carries", "Heartseeker", 0.6, "theory", ("1",),
                    because="the lost map",
                )
            )
        )  # fmt: skip
        self.assertEqual(await unlinked(), 1)
        line = (await health.read(self.memory, GUILD_A, self.c, since=0)).line()
        self.assertIn("readings_unlinked=1", line)
        self.assertNotIn("map", line)  # a count: never a name

    async def test_a_plot_on_a_connection_that_was_there_takes_no_links_so_undo_leaves_none(
        self,
    ) -> None:
        ledger = await self.add("The Ledger", type="clue")
        await self.relate(self.bram, "carries", self.sword, status="confirmed", secret=True)
        claim_id = await self.waiting_reading(
            belief("truth", "secret_plot", 1.0, because="the ledger"), "dm_said"
        )
        placed = await self.place(claim_id)
        links = [
            e for e in await self.rows("memory_evidence") if e["relation_id"] == placed.relation_id
        ]
        self.assertNotIn(("clue", ledger), [(e["kind"], e["target_id"]) for e in links])

    async def test_a_reason_that_is_not_text_is_ignored(self) -> None:
        await self.add("The Ledger", type="clue")
        for n, bad in enumerate((5, ["the ledger"], "", None, {"a": 1})):
            with self.subTest(bad):
                await self.add(f"Gem {n}", type="item")
                proposal = readings.BeliefProposal(
                    "pc:Mira", "Bram", "carries", f"Gem {n}", 0.5, "theory", ("1",)
                )  # fmt: skip
                claim_id = await self.waiting_reading(proposal, "player_said")
                async with self.db.guild(GUILD_A) as conn:
                    await conn.execute(
                        "UPDATE memory_claims SET reading = reading || jsonb_build_object("
                        "'because', %s::jsonb) WHERE id = %s",
                        (json.dumps(bad), claim_id),
                    )
                placed = await self.place(claim_id)
                latest = (await self.beliefs_of(placed.relation_id))[-1]
                self.assertEqual(await self.links_of(latest["id"]), [("claim", claim_id)])

    async def test_an_entry_that_goes_away_before_the_write_does_not_stop_the_placing(
        self,
    ) -> None:
        from unittest import mock

        from dmbot.memory import evidence

        await self.add("The Ledger", type="clue")
        claim_id = await self.waiting_reading(
            belief("pc:Mira", "theory", 0.5, because="the ledger"), "player_said"
        )
        real = evidence.add_in

        async def gone(w: Any, **kw: Any) -> Any:
            if kw["kind"] == evidence.CLUE:
                raise MemoryRuleError(evidence.NOT_THIS_CAMPAIGN)
            return await real(w, **kw)

        with mock.patch.object(evidence, "add_in", gone):
            placed = await self.place(claim_id)
        latest = (await self.beliefs_of(placed.relation_id))[-1]
        self.assertEqual(await self.links_of(latest["id"]), [("claim", claim_id)])
        self.assertEqual((await self.rows("memory_claims"))[0]["status"], "confirmed")

    async def test_the_cap_on_links_refuses_a_reason_without_stopping_the_placing(self) -> None:
        from unittest import mock

        from dmbot.memory import evidence

        await self.add("The Ledger", type="clue")
        claim_id = await self.waiting_reading(
            belief("pc:Mira", "theory", 0.5, because="the ledger"), "player_said"
        )
        with mock.patch.object(evidence, "PER_OWNER_MAX", 1):  # the claim's own link fills it
            placed = await self.place(claim_id)
        latest = (await self.beliefs_of(placed.relation_id))[-1]
        self.assertEqual(await self.links_of(latest["id"]), [("claim", claim_id)])

    async def test_a_link_to_an_entry_turned_down_since_the_names_were_read_is_refused(
        self,
    ) -> None:
        from unittest import mock

        from dmbot.memory import placing

        ledger = await self.add("The Ledger", type="clue")
        claim_id = await self.waiting_reading(
            belief("pc:Mira", "theory", 0.5, because="the ledger"), "player_said"
        )
        # the names were read while it stood; then the DM turned it down before the write
        await self.memory.set_entity_status(GUILD_A, self.c, ledger, "rejected", source="dm")
        with mock.patch.object(placing, "_reasons", return_value=[("clue", ledger)]):
            placed = await self.place(claim_id)
        latest = (await self.beliefs_of(placed.relation_id))[-1]
        self.assertEqual(await self.links_of(latest["id"]), [("claim", claim_id)])

    async def test_a_merge_carries_the_links_to_the_kept_name_and_undo_gives_them_back(
        self,
    ) -> None:
        keep = await self.add("The Ledger", type="clue")
        gone = await self.add("The Old Ledger", type="clue")
        claim_id = await self.waiting_reading(
            belief("pc:Mira", "theory", 0.5, because="the old ledger"), "player_said"
        )
        placed = await self.place(claim_id)
        latest = (await self.beliefs_of(placed.relation_id))[-1]
        self.assertIn(("clue", gone), await self.links_of(latest["id"]))
        merged = await self.memory.merge(
            GUILD_A, self.c, keep, gone, source="dm", dm_said_same=True
        )
        links = await self.links_of(latest["id"])
        self.assertIn(("clue", keep), links)
        self.assertNotIn(("clue", gone), links)  # nothing points at the name that went
        await self.memory.undo(GUILD_A, self.c, merged.batch, source="dm")  # type: ignore[arg-type]
        links = await self.links_of(latest["id"])
        self.assertIn(("clue", gone), links)
        self.assertNotIn(("clue", keep), links)

    async def test_a_merge_drops_a_twin_link_and_one_the_kept_kind_cannot_hold(self) -> None:
        keep = await self.add("The Ledger", type="clue")
        gone = await self.add("The Old Ledger", type="clue")
        thing = await self.add("The Ledger Fire", type="event")
        claim_id = await self.waiting_reading(
            belief("pc:Mira", "theory", 0.5, because="the old ledger", manipulated_by="the ledger"),
            "player_said",
        )
        placed = await self.place(claim_id)
        latest = (await self.beliefs_of(placed.relation_id))[-1]
        await self.memory.merge(GUILD_A, self.c, keep, gone, source="dm", dm_said_same=True)
        clues = [x for x in await self.links_of(latest["id"]) if x[0] == "clue"]
        self.assertEqual(clues, [("clue", keep)])  # the twin is one link, not two
        await self.memory.merge(GUILD_A, self.c, thing, keep, source="dm", dm_said_same=True)
        self.assertEqual([x for x in await self.links_of(latest["id"]) if x[0] == "clue"], [])

    async def test_undoing_one_of_two_guesses_on_a_shared_connection_keeps_the_other_link(
        self,
    ) -> None:
        await self.add("Rook", type="character", role="player_character", played_by=5)
        first = await self.waiting_reading(belief("pc:Mira", "theory", 0.5), "player_said")
        second = await self.waiting_reading(belief("pc:Rook", "theory", 0.5), "player_said")
        await self.place(first)
        placed = await self.place(second)
        links = [
            e for e in await self.rows("memory_evidence") if e["relation_id"] == placed.relation_id
        ]
        self.assertEqual(sorted(e["target_id"] for e in links), sorted([first, second]))
        await self.memory.undo(
            GUILD_A, self.c, placed.batch, source="dm",
            after=lambda c: ClaimStore.reopen_in(c, second),
        )  # fmt: skip
        links = [
            e for e in await self.rows("memory_evidence") if e["relation_id"] == placed.relation_id
        ]
        self.assertEqual([e["target_id"] for e in links], [first])  # the first guess still stands
        (claim,) = [r for r in await self.rows("memory_claims") if r["id"] == second]
        self.assertEqual((claim["status"], claim["fact_id"]), ("proposed", None))

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

    async def test_a_pinned_connection_is_not_taken_back_by_a_zero_reading(self) -> None:
        made = await self.relate(self.bram, "carries", self.sword, status="confirmed", secret=False)
        rel = made.value[0].id
        await self.memory.pin(GUILD_A, self.c, rel, True, source="dm")
        before = await self.beliefs_of(rel)
        false = await self.waiting_reading(belief("truth", "false_evidence", 0.0, dm_confirms=True))
        with self.assertRaises(MemoryRuleError) as why:
            await self.place(false)
        self.assertIn("Unpin it first", str(why.exception))
        self.assertEqual(await self.beliefs_of(rel), before)
        self.assertIn(rel, await self.memory.pinned_connections(GUILD_A, self.c))

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

    async def test_stop_recording_me_takes_an_unkept_theory_and_what_only_it_made(self) -> None:
        claim_id = await self.waiting_reading(belief("pc:Mira", "accusation", 0.75), "player_said")
        placed = await self.place(claim_id)
        self.assertEqual(await self.claims.forget_speaker(GUILD_A, DM), 1)
        self.assertEqual(await self.rows("memory_claims"), [])  # their words are gone
        self.assertEqual(await self.connections(), [])  # the place made only to hold the guess
        self.assertEqual(await self.beliefs_of(placed.relation_id), [])  # and the guess itself

    async def test_stop_recording_me_leaves_what_the_dm_kept_with_a_placeholder(self) -> None:
        claim_id = await self.waiting_reading(belief("party", "revealed_fact", 1.0), "dm_said")
        placed = await self.place(claim_id)
        await self.claims.forget_speaker(GUILD_A, DM)
        (kept,) = await self.connections()
        self.assertEqual((kept["id"], kept["status"]), (placed.relation_id, "confirmed"))
        latest = (await self.beliefs_of(placed.relation_id))[-1]
        self.assertEqual(latest["status"], "revealed_fact")
        links = [e for e in await self.rows("memory_evidence") if e["belief_id"] == latest["id"]]
        self.assertEqual([e["target_id"] for e in links], [None])  # a placeholder, no words
        self.assertEqual(len(await self.rows("memory_claims")), 1)  # as a confirmed fact's stays

    async def test_a_guess_with_other_evidence_stays_and_loses_only_the_link(self) -> None:
        from psycopg.types.json import Jsonb  # noqa: F401

        mine = await self.waiting_reading(belief("pc:Mira", "theory", 0.5), "player_said")
        placed = await self.place(mine)
        elsewhere = readings.BeliefProposal(
            "pc:Mira", "Bram", "owns", "Heartseeker", 0.25, "theory", ("1",)
        )
        other = await self.waiting_reading(elsewhere, "player_said")
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "UPDATE memory_claims SET speaker_ids = ARRAY[%s] WHERE id = %s", (999, other)
            )
            await conn.execute(
                "UPDATE memory_beliefs SET evidence_ids = ARRAY[%s, %s] WHERE relation_id = %s"
                " AND confidence = 0.5",
                (mine, other, placed.relation_id),
            )
        await self.claims.forget_speaker(GUILD_A, DM)
        (kept,) = [b for b in await self.beliefs_of(placed.relation_id) if b["kind"] == "pc"]
        self.assertEqual(kept["evidence_ids"], [other])  # another speaker's claim still stands
        self.assertEqual(len(await self.connections()), 1)

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

    async def test_the_guess_connection_is_the_extractors_and_a_kept_one_is_the_dms(self) -> None:
        theory = await self.place(await self.waiting_reading(belief("pc:Mira", "theory", 0.5)))
        reveal_words = readings.BeliefProposal(
            "party", "Bram", "owns", "Heartseeker", 1.0, "revealed_fact", ("1",)
        )
        revealed = await self.place(await self.waiting_reading(reveal_words))
        by_id = {r["id"]: r for r in await self.connections()}
        self.assertEqual(by_id[theory.relation_id]["source"], "extractor")
        self.assertEqual(by_id[revealed.relation_id]["source"], "dm")

    async def test_a_restatement_makes_a_guess_connection_public_but_not_another_proposal(
        self,
    ) -> None:
        placed = await self.place(await self.waiting_reading(belief("pc:Mira", "theory", 0.5)))
        await self.relate(self.bram, "carries", self.sword, status="confirmed")
        (host,) = await self.connections()
        self.assertEqual((host["id"], host["secret"]), (placed.relation_id, False))
        other = await self.memory.add_relation(
            GUILD_A, self.c, self.bram, "owns", self.sword,
            source="scan", status="proposed", secret=True, confidence=0.5,
        )  # fmt: skip
        await self.relate(self.bram, "owns", self.sword, status="confirmed")
        row = next(r for r in await self.connections() if r["id"] == other.value[0].id)
        self.assertEqual(row["secret"], True)  # another proposal's secret is not the guess's

    async def test_a_theory_on_someone_elses_proposal_adds_a_belief_and_leaves_it_alone(
        self,
    ) -> None:
        theirs = await self.memory.add_relation(
            GUILD_A, self.c, self.bram, "carries", self.sword,
            source="scan", status="proposed", confidence=0.5,
        )  # fmt: skip
        before = (await self.connections())[0]
        claim_id = await self.waiting_reading(belief("pc:Mira", "theory", 0.5), "player_said")
        placed = await self.place(claim_id)
        self.assertEqual(placed.relation_id, theirs.value[0].id)
        self.assertEqual((await self.connections())[0], before)
        await self.claims.forget_speaker(GUILD_A, DM)
        (survivor,) = await self.connections()  # the proposal was not the placement's to delete
        self.assertEqual(survivor["id"], theirs.value[0].id)
        self.assertEqual(await self.beliefs_of(survivor["id"]), [])  # only the guess went

    async def test_a_reveal_or_plot_will_not_take_over_someone_elses_question(self) -> None:
        await self.memory.add_relation(
            GUILD_A, self.c, self.bram, "carries", self.sword,
            source="scan", status="proposed", confidence=0.5,
        )  # fmt: skip
        for status, confidence in (("revealed_fact", 1.0), ("magically_verified", 1.0)):
            with self.subTest(status):
                claim_id = await self.waiting_reading(belief("party", status, confidence))
                with self.assertRaises(MemoryRuleError):
                    await self.place(claim_id)
        (row,) = await self.connections()
        self.assertEqual((row["status"], row["secret"]), ("proposed", False))

    async def test_a_secret_plot_over_a_connection_the_players_know_is_refused(self) -> None:
        await self.relate(self.bram, "carries", self.sword, status="confirmed")
        plot = readings.BeliefProposal(
            "truth", "Bram", "carries", "Heartseeker", 1.0, "secret_plot", ("1",),
            new_assertion=True, dm_confirms=True,
        )  # fmt: skip
        with self.assertRaises(MemoryRuleError):
            await self.place(await self.waiting_reading(plot))

    async def test_forgetting_also_clears_the_undo_log(self) -> None:
        claim_id = await self.waiting_reading(belief("pc:Mira", "accusation", 0.75), "player_said")
        placed = await self.place(claim_id)
        await self.claims.forget_speaker(GUILD_A, DM)
        gone = {claim_id, placed.relation_id}
        gone |= {b["id"] for b in await self.beliefs_of(placed.relation_id)}
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute("SELECT row_id FROM memory_changes")
            log = [dict(r) for r in await cur.fetchall()]
        self.assertEqual([r for r in log if r["row_id"] in gone], [])

    async def test_the_other_forget_path_follows_the_same_ruling(self) -> None:
        from dmbot.transcript import store as transcript_store

        unkept = await self.waiting_reading(belief("pc:Mira", "theory", 0.5), "player_said")
        shown = readings.BeliefProposal(
            "party", "Bram", "owns", "Heartseeker", 1.0, "revealed_fact", ("1",)
        )
        kept = await self.waiting_reading(shown, "dm_said")
        a, b = await self.place(unkept), await self.place(kept)
        async with self.db.guild(GUILD_A) as conn:
            await transcript_store._forget_memory_traces(conn, GUILD_A, DM, [])
        self.assertEqual(await self.beliefs_of(a.relation_id), [])
        self.assertEqual([r["id"] for r in await self.connections()], [b.relation_id])
        self.assertEqual([c["id"] for c in await self.rows("memory_claims")], [kept])

    async def test_a_guess_that_also_stands_on_a_dm_line_stays(self) -> None:
        mine = await self.waiting_reading(belief("pc:Mira", "theory", 0.5), "player_said")
        placed = await self.place(mine)
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "UPDATE memory_beliefs SET evidence_ids = ARRAY[%s, %s] WHERE relation_id = %s"
                " AND confidence = 0.5",
                (mine, "s1:123:456", placed.relation_id),
            )
        await self.claims.forget_speaker(GUILD_A, DM)
        (kept,) = [b for b in await self.beliefs_of(placed.relation_id) if b["kind"] == "pc"]
        self.assertEqual(kept["evidence_ids"], ["s1:123:456"])

    async def test_a_no_on_the_guess_connection_leaves_the_belief_standing(self) -> None:
        placed = await self.place(await self.waiting_reading(belief("pc:Mira", "theory", 0.5)))
        await self.memory.update_relation(
            GUILD_A, self.c, placed.relation_id, source="dm", status="rejected"
        )
        (mine,) = [b for b in await self.beliefs_of(placed.relation_id) if b["kind"] == "pc"]
        self.assertEqual((mine["status"], mine["confidence"]), ("theory", 0.5))

    async def test_keeping_an_ordinary_proposal_adds_no_second_flag(self) -> None:
        keep, inn, hall = [await self.add(n, type="place") for n in ("Keep", "Inn", "Hall")]
        await self.relate(inn, "located_in", keep, status="confirmed")
        waiting = await self.relate(inn, "located_in", hall, status="proposed")
        before = len(await self.memory.flags(GUILD_A, self.c))
        self.assertGreater(before, 0)  # judged once, when it was proposed
        await self.memory.update_relation(
            GUILD_A, self.c, waiting.value[0].id, source="dm", status="confirmed"
        )
        self.assertEqual(len(await self.memory.flags(GUILD_A, self.c)), before)

    async def test_lists_that_count_connections_do_not_count_a_guess_connection(self) -> None:
        await self.place(await self.waiting_reading(belief("pc:Mira", "theory", 0.5)))
        known = {(r.subject_id, r.object_id) for r in await self.memory.relations(
            GUILD_A, self.c, include_secret=True
        )}  # fmt: skip
        self.assertNotIn((self.bram, self.sword), known)

    async def test_a_reading_is_never_quoted_as_the_source_of_a_fact(self) -> None:
        from dmbot.memory.continuity import ContinuityStore
        from dmbot.memory.recall import RecallService, _Fact

        shown = readings.BeliefProposal(
            "party", "Bram", "carries", "Heartseeker", 1.0, "revealed_fact", ("1",)
        )
        placed = await self.place(await self.waiting_reading(shown, "dm_said"))
        fact = _Fact(
            placed.relation_id, self.bram, self.sword, "carries", "", None, False, (), 0,
            self.bram, self.sword,
        )  # fmt: skip
        async with self.memory._read(GUILD_A, self.c) as scope:
            self.assertEqual(await RecallService(self.memory)._sources(scope, [fact]), {})
        world = await ContinuityStore(self.memory)._world(GUILD_A, self.c, {DM})
        self.assertNotIn(placed.relation_id, world.said)

    async def test_every_guess_on_a_connection_is_linked_so_the_last_one_out_takes_it(
        self,
    ) -> None:
        other = 4242
        a = await self.waiting_reading(belief("pc:Mira", "theory", 0.5), "player_said")
        placed = await self.place(a)
        b = await self.waiting_reading(belief("pc:Bram", "theory", 0.25), "player_said")
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "UPDATE memory_claims SET speaker_ids = ARRAY[%s] WHERE id = %s", (other, b)
            )
        await self.claims.place(GUILD_A, self.c, b, opted_in={DM, other})
        links = [
            e for e in await self.rows("memory_evidence") if e["relation_id"] == placed.relation_id
        ]
        self.assertEqual(sorted(e["target_id"] for e in links), sorted([a, b]))
        await self.claims.forget_speaker(GUILD_A, DM)  # A leaves: B's guess keeps the connection
        self.assertEqual(len(await self.connections()), 1)
        await self.claims.forget_speaker(GUILD_A, other)  # B leaves: nothing is left of it
        self.assertEqual(await self.connections(), [])

    async def test_a_placement_racing_stop_recording_me_cannot_survive_it(self) -> None:
        import asyncio

        for n in range(3):
            claim_id = await self.waiting_reading(
                readings.BeliefProposal(
                    "pc:Mira", "Bram", f"keeps{n}", "Heartseeker", 0.5, "theory", ("1",)
                ),
                "player_said",
            )
            await asyncio.gather(
                self.place(claim_id),
                self.claims.forget_speaker(GUILD_A, DM),
                return_exceptions=True,  # (the loser is told the question is gone)
            )
            await self.claims.forget_speaker(GUILD_A, DM)  # and a later one finds nothing to do
        self.assertEqual(await self.rows("memory_claims"), [])
        self.assertEqual(await self.connections(), [])
        self.assertEqual(
            [b for b in await self.rows("memory_beliefs") if b["status"] == "theory"], []
        )

    async def test_who_is_recorded_is_asked_again_inside_the_write(self) -> None:
        claim_id = await self.waiting_reading(belief("pc:Mira", "theory", 0.5), "player_said")
        asked: list[int] = []

        def recorded() -> set[int]:
            asked.append(1)
            return {DM} if len(asked) == 1 else set()  # fine at first, gone by the write

        with self.assertRaises(MemoryRuleError):
            await self.claims.place(GUILD_A, self.c, claim_id, opted_in=recorded)
        self.assertEqual(len(asked), 2)
        self.assertEqual(await self.connections(), [])
        self.assertEqual(await self.rows("memory_claims"), [])  # the question went with them

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
