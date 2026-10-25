"""What the party has wrong (#2661): where a point of view's belief differs from The truth, as
candidates for PlotBot's sort. Invented names."""

from __future__ import annotations

from typing import Any

from dmbot.memory import differences as df
from dmbot.memory.models import new_id
from tests.test_memory_store import GUILD_A, ConfirmedMemoryTest


class Finding(ConfirmedMemoryTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.finder = df.Differences(self.memory)
        self.bram = await self.add("Bram")
        self.mira = await self.add("Mira")

    async def view(self, kind: str) -> str:
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute(
                "SELECT id FROM memory_perspectives WHERE guild_id = %s AND campaign_id = %s"
                " AND kind = %s",
                (GUILD_A, self.c, kind),
            )
            row = await cur.fetchone()
        assert row is not None
        return str(row["id"])

    async def believe(
        self, view: str, relation: str, confidence: float, status: str = "theory",
        session_at: int | None = 0,
    ) -> None:  # fmt: skip
        """A belief row as a restored backup brings it."""
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "INSERT INTO memory_beliefs (guild_id, campaign_id, id, perspective_id,"
                " relation_id, confidence, status, session_at, evidence_ids, seq, recorded_at)"
                " SELECT %s, %s, %s, %s, %s, %s, %s, %s, '{}', COALESCE(max(seq), 0) + 1, 1"
                " FROM memory_beliefs WHERE guild_id = %s AND campaign_id = %s",
                (GUILD_A, self.c, new_id(), view, relation, confidence, status, session_at,
                 GUILD_A, self.c),
            )  # fmt: skip

    async def link(self, predicate: str, **kw: Any) -> str:
        made = await self.relate(self.bram, predicate, self.mira, status="confirmed", **kw)
        return str(made.value[0].id)

    async def guess(self, view: str, predicate: str) -> str:
        relation = new_id()
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "INSERT INTO memory_relations (guild_id, campaign_id, id, subject_id, predicate,"
                " object_id, detail, confidence, status, source, mention_ids, secret, created_at)"
                " VALUES (%s, %s, %s, %s, %s, %s, '', 0.5, 'proposed', 'extractor', '{}', true, 1)",
                (GUILD_A, self.c, relation, self.bram, predicate, self.mira),
            )
        await self.believe(view, relation, 0.75, "accusation")
        return relation

    async def character(self, name: str, kind: str = "pc") -> str:
        entity = await self.add(name, type="character", role="player_character", played_by=9)
        made = new_id()
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "INSERT INTO memory_perspectives (guild_id, campaign_id, id, kind, entity_id,"
                " created_at) VALUES (%s, %s, %s, %s, %s, 1)",
                (GUILD_A, self.c, made, kind, entity),
            )
        return made

    async def anchor(self) -> None:
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute("UPDATE memory_beliefs SET session_at = 0 WHERE session_at IS NULL")

    async def test_the_party_believing_what_the_truth_denies_is_a_difference(self) -> None:
        ally = await self.link("ally_of")
        await self.anchor()
        await self.believe(await self.view("truth"), ally, 0.0, "false_evidence")
        (found,) = await self.finder.find(GUILD_A, self.c)
        self.assertEqual(
            (found.view_kind, found.relation_id, found.truth_relation_id, found.kind),
            ("party", ally, None, df.DENIED),
        )

    async def test_a_belief_the_truth_shares_is_no_difference(self) -> None:
        await self.link("ally_of")  # the truth and the party both hold it
        self.assertEqual(await self.finder.find(GUILD_A, self.c), [])

    async def test_a_belief_that_clashes_with_what_the_truth_holds_is_one(self) -> None:
        enemy = await self.link("enemy_of")
        ally = await self.link("ally_of")
        await self.anchor()
        truth = await self.view("truth")
        await self.believe(truth, ally, 0.0, "false_evidence")  # the DM's view: enemies, not allies
        found = await self.finder.find(GUILD_A, self.c)
        (clash,) = [d for d in found if d.kind == df.CLASHES]
        self.assertEqual((clash.relation_id, clash.truth_relation_id), (ally, enemy))
        self.assertTrue(clash.times_unknown)  # no game time on either

    async def test_a_guess_about_something_the_truth_neither_holds_nor_denies_is_none(self) -> None:
        rook = await self.character("Rook")
        await self.guess(rook, "ally_of")
        self.assertEqual(await self.finder.find(GUILD_A, self.c), [])

    async def test_a_characters_guess_against_what_the_truth_holds_is_marked_as_a_guess(
        self,
    ) -> None:
        enemy = await self.link("enemy_of")
        rook = await self.character("Rook")
        ally = await self.guess(rook, "ally_of")
        (found,) = await self.finder.find(GUILD_A, self.c)
        self.assertEqual(
            (found.perspective_id, found.view_kind, found.relation_id, found.truth_relation_id),
            (rook, "pc", ally, enemy),
        )
        self.assertTrue(found.involves_guess)

    async def test_the_truths_own_doubt_is_not_a_denial(self) -> None:
        ally = await self.link("ally_of")
        await self.anchor()
        await self.believe(await self.view("truth"), ally, 0.25, "theory")  # "unlikely"
        self.assertEqual(await self.finder.find(GUILD_A, self.c), [])

    async def test_story_times_that_do_not_overlap_do_not_clash(self) -> None:
        await self.link("enemy_of", from_session_at=10, to_session_at=20)
        rook = await self.character("Rook")
        await self.believe(
            rook, await self.link("ally_of", from_session_at=1, to_session_at=5), 0.9
        )
        self.assertEqual(await self.finder.find(GUILD_A, self.c), [])

    async def test_a_belief_given_up_is_not_a_difference_now(self) -> None:
        ally = await self.link("ally_of")
        await self.anchor()
        truth, party = await self.view("truth"), await self.view("party")
        await self.believe(truth, ally, 0.0, "false_evidence", session_at=1)
        await self.believe(party, ally, 0.0, "false_evidence", session_at=2)  # the party let go
        self.assertEqual(await self.finder.find(GUILD_A, self.c), [])

    async def test_another_campaign_never_shows(self) -> None:
        ally = await self.link("ally_of")
        await self.anchor()
        await self.believe(await self.view("truth"), ally, 0.0, "false_evidence")
        elsewhere = (await self.campaigns.create(GUILD_A, "Elsewhere", 7)).id
        self.assertEqual(await self.finder.find(GUILD_A, elsewhere), [])
        self.assertEqual(len(await self.finder.find(GUILD_A, self.c)), 1)

    async def test_a_non_player_characters_belief_is_not_the_partys_to_correct(self) -> None:
        # only the party and the players' characters are what the DM sorts: an NPC being wrong
        # (or lying) is the DM's own story
        enemy = await self.link("enemy_of")
        npc = await self.character("Thessaly", kind="npc")
        await self.guess(npc, "ally_of")
        await self.believe(npc, enemy, 0.0, "false_evidence")
        self.assertEqual(await self.finder.find(GUILD_A, self.c), [])

    async def test_a_frame_up_the_truth_denies_is_a_denied_difference_marked_as_a_guess(
        self,
    ) -> None:
        # a player's character accuses Bram and Mira of an alliance the DM's view says is not so
        rook = await self.character("Rook")
        accused = await self.guess(rook, "ally_of")
        await self.believe(await self.view("truth"), accused, 0.0, "false_evidence")
        (found,) = await self.finder.find(GUILD_A, self.c)
        self.assertEqual(
            (found.view_kind, found.relation_id, found.truth_relation_id, found.kind),
            ("pc", accused, None, df.DENIED),
        )
        self.assertTrue(found.involves_guess)
