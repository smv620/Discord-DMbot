"""Where the story is, kept from session to session (#4173 slice 4; Supervisor's ruling,
2026-11-13 04:14), against Postgres: the chain a session leaves is where the next one starts, a
session older than the chain never moves it, a place's assumed parent waits the slice 3 way (never
a question), and while split the DM's line goes to the group whose clock the DM moved last, a
player's to their own group, and Rejoin keeps only what every group shares. Invented names only."""

from __future__ import annotations

from typing import Any

from dmbot.memory.backstory_links import BackstoryLinks
from dmbot.memory.models import CONFIRMED
from dmbot.memory.models import DM as BY_DM
from dmbot.memory.scene_moves import ARRIVE, Move
from dmbot.memory.session_chain import MAIN
from dmbot.memory.story_chain_store import Said, StoryChains, chain_key
from tests.test_memory_names import DM, GUILD, NamesTest

P1, P2 = 801, 802
START = 1_800_000_000
SPLIT = START + 600
REJOIN = START + 1200


def at(seconds_in: int) -> int:
    return (START + seconds_in) * 1000


class Chains(NamesTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.cid = self.campaign.id
        self.links = BackstoryLinks(self.db)
        self.chains = StoryChains(self.db, self.links)
        self.ids: dict[str, str] = {}
        for name, level in (
            ("Icewind Dale", "region"),
            ("Kelvenmoor", "settlement"),
            ("Brindleford", "settlement"),
            ("Gull's Rest", "building"),
            ("Speaker's Hall", "building"),
        ):
            made = await self.memory.add_entity(
                GUILD, self.cid, name=name, type="place", status=CONFIRMED, source=BY_DM
            )
            await self.memory.set_place_level(GUILD, self.cid, made.value.id, level, source=BY_DM)
            self.ids[name] = str(made.value.id)
        self.by_id = {v: k for k, v in self.ids.items()}
        self.agreeing = {DM, P1, P2}
        self.wren = await self.pc("Wren", P1)
        self.ash = await self.pc("Ash", P2)

    async def pc(self, name: str, player: int) -> str:
        made = await self.memory.add_entity(
            GUILD, self.cid, name=name, type="character", role="player_character",
            played_by=player, status=CONFIRMED, source="dm",
        )  # fmt: skip
        return str(made.value.id)

    async def read(self, *lines: tuple[int, int, str], started: int = START) -> Any:
        said = [Said(at(t), who, text) for t, who, text in lines]

        async def agreed() -> set[int]:
            return set(self.agreeing)

        return await self.chains.read_session(
            GUILD, self.cid, started, said, {DM}, "Session 1", agreed
        )

    async def stored(self) -> dict[str, list[str]]:
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                "SELECT chain, entity_id FROM memory_story_chain WHERE campaign_id = %s"
                " AND chain <> 'mark' ORDER BY chain, rank",
                (self.cid,),
            )
            out: dict[str, list[str]] = {}
            for r in await cur.fetchall():
                out.setdefault(str(r["chain"]), []).append(self.by_id[str(r["entity_id"])])
        return out

    async def guesses(self) -> list[tuple[str, str]]:
        waiting = await self.links.waiting(GUILD, self.cid, assumed=True)
        return sorted((self.by_id[w.subject_id], self.by_id[w.object_id]) for w in waiting)

    async def test_the_dm_moves_it_and_the_next_session_starts_there(self) -> None:
        done = await self.read(
            (1, DM, "You reach Icewind Dale."),
            (2, DM, "By dusk you are in Kelvenmoor."),
            (3, P1, "My cousin keeps Speaker's Hall."),  # (a player's place: read, not moved)
        )
        self.assertEqual(done.guessed, 2)
        self.assertEqual(await self.stored(), {MAIN: ["Icewind Dale", "Kelvenmoor"]})
        self.assertEqual(
            await self.guesses(),
            [("Kelvenmoor", "Icewind Dale"), ("Speaker's Hall", "Kelvenmoor")],
        )
        self.assertEqual(await self.links.waiting(GUILD, self.cid), [])  # (never a question)
        await self.read((1, DM, "You take rooms at the Gull's Rest."), started=START + 86_400)
        self.assertIn(("Gull's Rest", "Kelvenmoor"), await self.guesses())

    async def test_an_older_session_never_moves_the_chain(self) -> None:
        await self.read((1, DM, "You reach Icewind Dale."), started=START + 86_400)
        done = await self.read((1, DM, "You ride into Brindleford."), started=START)
        self.assertTrue(done.skipped_older)
        self.assertEqual(await self.stored(), {MAIN: ["Icewind Dale"]})

    async def split(self) -> None:
        async with self.db.guild(GUILD) as conn:
            await conn.execute(
                "INSERT INTO game_clock_log (guild_id, campaign_id, position, at, minute)"
                " VALUES (%s, %s, NULL, %s, 0), (%s, %s, 1, %s, 0), (%s, %s, 2, %s, 0),"
                " (%s, %s, 1, %s, 10), (%s, %s, 2, %s, 10), (%s, %s, NULL, %s, 0)",
                # (at the split every group's clock is written in one second, as the triggers
                # do; then the DM moves group 1's, later group 2's)
                (GUILD, self.cid, START, GUILD, self.cid, SPLIT, GUILD, self.cid, SPLIT,
                 GUILD, self.cid, SPLIT + 5, GUILD, self.cid, SPLIT + 300,
                 GUILD, self.cid, REJOIN),
            )  # fmt: skip
            for character, group in ((self.wren, 1), (self.ash, 2)):
                await conn.execute(
                    "INSERT INTO game_clock_group_log (guild_id, campaign_id, character_id,"
                    " position, at) VALUES (%s, %s, %s, %s, %s)",
                    (GUILD, self.cid, character, group, SPLIT),
                )

    async def test_split_groups_and_rejoin(self) -> None:
        await self.split()
        await self.read(
            (1, DM, "You are in Icewind Dale, in Kelvenmoor."),
            (610, DM, "Wren's group walks into Speaker's Hall."),  # (group 1's clock moved last)
            (620, P2, "Ash looks around the Gull's Rest."),  # (Ash is in group 2)
            (910, DM, "Ash's group crosses to Brindleford."),  # (group 2's clock moved last)
            (1210, DM, "Back together, you rest."),  # (after Rejoin)
        )
        g1, g2 = chain_key(SPLIT, 1), chain_key(SPLIT, 2)
        self.assertNotIn(g1, await self.stored())  # (the split's chains end at Rejoin)
        self.assertEqual(await self.stored(), {MAIN: ["Icewind Dale"]})  # (only what both share)
        self.assertIn(("Speaker's Hall", "Kelvenmoor"), await self.guesses())
        self.assertIn(("Gull's Rest", "Kelvenmoor"), await self.guesses())
        self.assertNotIn(g2, await self.stored())

    async def test_a_session_that_ends_split_is_rejoined_at_the_next(self) -> None:
        await self.split()
        await self.read(
            (1, DM, "You are in Icewind Dale, in Kelvenmoor."),
            (610, DM, "Wren's group walks into Speaker's Hall."),
        )
        self.assertEqual(
            await self.stored(),
            {
                MAIN: ["Icewind Dale", "Kelvenmoor"],
                chain_key(SPLIT, 1): ["Icewind Dale", "Kelvenmoor", "Speaker's Hall"],
            },
        )
        await self.read((1210, DM, "Together again."), started=START + 1200)
        self.assertEqual(await self.stored(), {MAIN: ["Icewind Dale", "Kelvenmoor"]})

    async def test_a_dm_line_at_the_split_instant_goes_to_no_group(self) -> None:
        await self.split()
        await self.read(
            (1, DM, "You are in Icewind Dale, in Kelvenmoor."),
            (602, DM, "Someone walks into Speaker's Hall."),  # (no group's clock moved yet)
        )
        self.assertNotIn(("Speaker's Hall", "Kelvenmoor"), await self.guesses())

    async def test_two_groups_that_reach_one_town_keep_it_at_rejoin(self) -> None:
        await self.split()
        await self.read(
            (1, DM, "You are in Icewind Dale, in Kelvenmoor."),
            (610, DM, "Wren's group rides into Brindleford."),
            (910, DM, "Ash's group rides into Brindleford too."),
            (1210, DM, "Back together."),
        )
        self.assertEqual(await self.stored(), {MAIN: ["Icewind Dale", "Brindleford"]})

    async def test_someone_who_stops_meanwhile_is_read_again_without(self) -> None:
        self.agreeing = {DM, P2}  # (P1 stopped between the first ask and the keep)
        await self.read(
            (1, DM, "You reach Icewind Dale, in Kelvenmoor."),
            (2, P1, "My cousin keeps Speaker's Hall."),
        )
        self.assertNotIn(("Speaker's Hall", "Kelvenmoor"), await self.guesses())

    async def test_an_older_session_is_left_out_even_after_an_empty_chain(self) -> None:
        await self.read((1, P1, "Nothing about places."), started=START + 86_400)
        self.assertEqual(await self.stored(), {})  # (no chain, but the mark is kept)
        done = await self.read((1, DM, "You reach Icewind Dale."), started=START)
        self.assertTrue(done.skipped_older)


class Journeys(Chains):
    """#4173 slice 6d part 3: the party's journey is written as the DM's own travelled route, only
    between kept places, once for a session walked twice, and each one Undoes on its own."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.chains = StoryChains(self.db, self.links, self.memory)

    async def go(self, *lines: tuple[int, int, str, str], started: int = START) -> Any:
        """Each line with the town the AI says the DM brings the party to ("" for none)."""
        said = [Said(at(t), who, text, n) for n, (t, who, text, _) in enumerate(lines, 1)]
        moves = {n: (Move(n, ARRIVE, town),) for n, (_, _, _, town) in enumerate(lines, 1) if town}

        async def agreed() -> set[int]:
            return set(self.agreeing)

        return await self.chains.read_session(
            GUILD, self.cid, started, said, {DM}, "Session 1", agreed, moves
        )

    async def legs(self) -> list[tuple[str, str, str, int | None]]:
        return sorted(
            (self.by_id[r.subject_id], self.by_id[r.object_id], r.detail, r.from_session_at)
            for r in await self.memory.relations(GUILD, self.cid)
            if r.predicate == "route_to" and r.route_state == "travelled"
        )

    async def test_a_journey_is_written_with_its_way_and_session(self) -> None:
        done = await self.go(
            (1, DM, "You reach Kelvenmoor.", "Kelvenmoor"),
            (2, DM, "You take the road east and reach Brindleford.", "Brindleford"),
            (3, DM, "Back at Brindleford.", ""),
        )
        self.assertEqual((done.journeys, done.journeys_unkept), (1, 0))
        self.assertEqual(await self.legs(), [("Kelvenmoor", "Brindleford", "road", START)])
        rels = [
            r for r in await self.memory.relations(GUILD, self.cid) if r.predicate == "route_to"
        ]
        self.assertEqual((rels[0].status, rels[0].source), (CONFIRMED, "dm"))

    async def test_no_journey_to_a_place_not_kept(self) -> None:
        made = await self.memory.add_entity(
            GUILD, self.cid, name="Easthaven", type="place", status="proposed", source="extractor"
        )
        await self.memory.set_place_level(
            GUILD, self.cid, made.value.id, "settlement", source=BY_DM
        )
        done = await self.go(
            (1, DM, "You reach Kelvenmoor.", "Kelvenmoor"),
            (2, DM, "You reach Easthaven.", "Easthaven"),
        )
        self.assertEqual((done.journeys, done.journeys_unkept), (0, 1))
        self.assertEqual(await self.legs(), [])

    async def test_the_same_session_walked_again_writes_no_more(self) -> None:
        lines = (
            (1, DM, "You reach Kelvenmoor.", "Kelvenmoor"),
            (2, DM, "You reach Brindleford.", "Brindleford"),
        )
        await self.go(*lines)
        again = await self.go(*lines)  # (an import retried: the chain now ends in Brindleford)
        self.assertEqual(again.journeys, 0)
        self.assertEqual(len(await self.legs()), 1)

    async def test_a_dm_who_stopped_takes_the_journeys_with_them(self) -> None:
        calls = 0

        async def agreed() -> set[int]:
            nonlocal calls
            calls += 1
            return set(self.agreeing) if calls < 2 else self.agreeing - {DM}

        said = [
            Said(at(1), DM, "You reach Kelvenmoor.", 1),
            Said(at(2), DM, "You reach Brindleford.", 2),
        ]
        moves = {1: (Move(1, ARRIVE, "Kelvenmoor"),), 2: (Move(2, ARRIVE, "Brindleford"),)}
        done = await self.chains.read_session(
            GUILD, self.cid, START, said, {DM}, "Session 1", agreed, moves
        )
        self.assertEqual(done.journeys, 0)
        self.assertEqual(await self.legs(), [])

    async def test_one_undo_takes_one_journey_back(self) -> None:
        await self.go(
            (1, DM, "You reach Kelvenmoor.", "Kelvenmoor"),
            (2, DM, "You reach Brindleford.", "Brindleford"),
        )
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                "SELECT max(batch) AS b FROM memory_changes WHERE guild_id = %s"
                " AND campaign_id = %s",
                (GUILD, self.cid),
            )
            row = await cur.fetchone()
        assert row is not None
        await self.memory.undo(GUILD, self.cid, int(row["b"]), source=BY_DM)
        self.assertEqual(await self.legs(), [])
