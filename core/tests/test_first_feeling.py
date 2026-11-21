"""The "Starting feeling?" line on a first meeting (#4229 slice 4b; Supervisor's ruling on #4229,
2026-11-21, D): from the DM's kept feelings only, worked out when the question is shown, never
stored, never when the two are already connected. Invented names."""

from __future__ import annotations

import unittest

from dmbot.memory import bias_read, first_feeling, standing
from dmbot.memory.claims import ClaimStore, Extracted
from dmbot.memory.first_feeling import Kept, Tie
from dmbot.memory.models import CONFIRMED, SPECIES
from tests.test_memory_store import GUILD_A, ConfirmedMemoryTest

BREW, TALA, KEEP, LEAGUE, ORCS = "b" * 32, "t" * 32, "k" * 32, "l" * 32, "o" * 32
NAMES = {
    BREW: "Brewner", TALA: "Tala", KEEP: "Mithral Keep", LEAGUE: "the Silver League",
    ORCS: "Grey Orcs",
}  # fmt: skip
UNFRIENDLY = standing.SCORE_OF[standing.UNFRIENDLY]


class TheLine(unittest.TestCase):
    def test_a_home_place_toward_the_others_lineage(self) -> None:
        got = first_feeling.line(
            TALA, BREW, {TALA: [Tie("located_in", KEEP, "lives in")]}, {BREW: "dwarf"},
            [Kept(KEEP, "", "dwarf", UNFRIENDLY)], NAMES,
        )  # fmt: skip
        self.assertEqual(
            got,
            "🤔 **Mithral Keep** (Tala's home) is unfriendly toward dwarves, and **Brewner** is a "
            "dwarf. You decide how they feel.",
        )

    def test_a_group_toward_the_others_people(self) -> None:
        ties = {TALA: [Tie("member_of", LEAGUE)], BREW: [Tie("of_people", ORCS)]}
        kept = [Kept(LEAGUE, ORCS, "", standing.SCORE_OF[standing.WARY])]
        got = first_feeling.line(BREW, TALA, ties, {}, kept, NAMES)
        self.assertEqual(
            got,
            "🤔 **the Silver League** (Tala's group) is wary of the Grey Orcs, and **Brewner** is "
            "one of them. You decide how they feel.",
        )

    def test_what_gives_no_line(self) -> None:
        kept = [Kept(KEEP, "", "dwarf", UNFRIENDLY)]
        for ties in (
            {},  # (no home)
            {TALA: [Tie("located_in", KEEP, "was last seen in")]},  # (a visit is no home)
            {TALA: [Tie("located_in", KEEP, "lived in")]},  # (a past home)
            {TALA: [Tie("knows", KEEP)]},  # (not a home tie)
        ):
            with self.subTest(ties):
                self.assertEqual(
                    first_feeling.line(TALA, BREW, ties, {BREW: "dwarf"}, kept, NAMES), ""
                )
        # (no lineage DMbot knows, or a feeling toward another people)
        home = {TALA: [Tie("located_in", KEEP)]}
        self.assertEqual(first_feeling.line(TALA, BREW, home, {}, kept, NAMES), "")
        self.assertEqual(first_feeling.line(TALA, BREW, home, {BREW: "elf"}, kept, NAMES), "")

    def test_both_ways_round(self) -> None:
        ties = {TALA: [Tie("located_in", KEEP)], BREW: [Tie("member_of", LEAGUE)]}
        friendly = standing.SCORE_OF[standing.FRIENDLY]
        kept = [Kept(KEEP, "", "dwarf", UNFRIENDLY), Kept(LEAGUE, "", "elf", friendly)]
        got = first_feeling.line(TALA, BREW, ties, {BREW: "dwarf", TALA: "elf"}, kept, NAMES)
        self.assertEqual(
            got,
            "🤔 **Mithral Keep** (Tala's home) is unfriendly toward dwarves, and **Brewner** is a "
            "dwarf. **the Silver League** (Brewner's group) is friendly toward elves, and **Tala** "
            "is an elf. You decide how they feel.",
        )

    def test_never_the_the_and_names_are_not_formatting(self) -> None:
        names = {**NAMES, ORCS: "The Grey Orcs", BREW: "Brew*ner"}
        ties = {TALA: [Tie("member_of", LEAGUE)], BREW: [Tie("of_people", ORCS)]}
        got = first_feeling.line(TALA, BREW, ties, {}, [Kept(LEAGUE, ORCS, "", UNFRIENDLY)], names)
        self.assertIn("toward the Grey Orcs, and **Brew\\*ner**", got)

    def test_long_names_keep_the_line_short(self) -> None:
        names = {k: v * 30 for k, v in NAMES.items()}
        ties = {TALA: [Tie("located_in", KEEP)], BREW: [Tie("member_of", LEAGUE)]}
        friendly = standing.SCORE_OF[standing.FRIENDLY]
        kept = [Kept(KEEP, "", "dwarf", UNFRIENDLY), Kept(LEAGUE, "", "elf", friendly)]
        got = first_feeling.line(TALA, BREW, ties, {BREW: "dwarf", TALA: "elf"}, kept, names)
        self.assertLessEqual(len(got), first_feeling.LINE_MAX)

    def test_a_name_gone_since_gives_nothing(self) -> None:
        ties = {TALA: [Tie("located_in", KEEP)]}
        names = {k: v for k, v in NAMES.items() if k != BREW}
        kept = [Kept(KEEP, "", "dwarf", UNFRIENDLY)]
        self.assertEqual(first_feeling.line(TALA, BREW, ties, {BREW: "dwarf"}, kept, names), "")

    def test_lineage_of_a_species_name(self) -> None:
        for name, key in (("Hill Dwarf", "dwarf"), ("Elf (Drow)", "elf"), ("Warforged", "")):
            with self.subTest(name):
                self.assertEqual(first_feeling.lineage_key(name), key)


class OnTheQuestion(ConfirmedMemoryTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.claims = ClaimStore(self.memory)
        self.tala = await self.add("Tala", type="character")
        self.brew = await self.add("Brewner", type="character")
        self.keep = await self.add("Mithral Keep", type="place")
        await self.memory.add_relation(
            GUILD_A, self.c, self.tala, "located_in", self.keep, source="dm", confidence=1.0,
            status=CONFIRMED, detail="lives in",
        )  # fmt: skip
        await self.memory.set_rule_link(GUILD_A, self.c, self.brew, SPECIES, "Dwarf", source="dm")

    async def feel(self, *, keep: bool = True) -> None:
        found = [bias_read.Found(self.keep, standing.UNFRIENDLY, lineage="dwarf")]
        await self.memory.add_read_biases(GUILD_A, self.c, found, source="background")
        if keep:
            (note,) = [
                n for n in await self.memory.world_notes(GUILD_A, self.c) if n.kind == "bias"
            ]
            await self.memory.keep_world_notes(GUILD_A, self.c, [note.id], source="dm")

    async def lines(self) -> dict[str, str]:
        await self.claims.propose(
            GUILD_A, self.c, [Extracted("Tala", "knows", "Brewner", "", ("1",), "dm_said")],
            {"1": 7}, opted_in={7}, prompt_version="p", model="m", session_started_at=1,
        )  # fmt: skip
        page = await self.claims.waiting(GUILD_A, self.c, opted_in={7})
        return await self.claims.starting_feelings(GUILD_A, self.c, page)

    async def test_a_kept_feeling_is_cited_on_a_first_meeting(self) -> None:
        await self.feel()
        (line,) = (await self.lines()).values()
        self.assertIn("**Mithral Keep** (Tala's home) is unfriendly toward dwarves", line)

    async def test_one_dmbot_only_read_counts_for_nothing(self) -> None:
        await self.feel(keep=False)
        self.assertEqual(await self.lines(), {})

    async def test_two_already_connected_get_no_line(self) -> None:
        await self.feel()
        await self.memory.add_relation(
            GUILD_A, self.c, self.tala, "knows", self.brew, source="dm", confidence=1.0,
            status=CONFIRMED,
        )  # fmt: skip
        self.assertEqual(await self.lines(), {})

    async def test_two_once_connected_get_no_line(self) -> None:
        await self.feel()
        made = await self.memory.add_relation(
            GUILD_A, self.c, self.tala, "knows", self.brew, source="dm", confidence=1.0,
            status=CONFIRMED,
        )  # fmt: skip
        fact = made.value[0]
        await self.memory.update_relation(
            GUILD_A, self.c, fact.id, source="dm", to_session_at=fact.created_at + 5
        )
        self.assertEqual(await self.lines(), {})

    async def test_a_turned_down_feeling_gives_nothing(self) -> None:
        await self.feel(keep=False)
        (note,) = [n for n in await self.memory.world_notes(GUILD_A, self.c) if n.kind == "bias"]
        await self.memory.turn_down_world_note(GUILD_A, self.c, note.id, source="dm")
        self.assertEqual(await self.lines(), {})

    async def test_toward_a_people_entry_from_a_group(self) -> None:
        league = await self.add("the Silver League", type="faction")
        orcs = await self.add("Grey Orcs", type="people")
        for s, p, o in ((self.tala, "member_of", league), (self.brew, "of_people", orcs)):
            await self.memory.add_relation(
                GUILD_A, self.c, s, p, o, source="dm", confidence=1.0, status=CONFIRMED
            )
        await self.memory.set_attitude(GUILD_A, self.c, league, orcs, standing.WARY, source="dm")
        (line,) = (await self.lines()).values()
        self.assertIn("**the Silver League** (Tala's group) is wary of the Grey Orcs", line)

    async def test_a_state_or_a_place_tie_gives_nothing(self) -> None:
        await self.feel()
        await self.claims.propose(
            GUILD_A, self.c,
            [Extracted("Brewner", "lives in", "Mithral Keep", "", ("1",), "dm_said")],
            {"1": 7}, opted_in={7}, prompt_version="p", model="m", session_started_at=1,
        )  # fmt: skip
        page = await self.claims.waiting(GUILD_A, self.c, opted_in={7})
        self.assertEqual(await self.claims.starting_feelings(GUILD_A, self.c, page), {})

    async def test_nothing_is_written(self) -> None:
        await self.feel()
        before = await self.memory.standing_index(GUILD_A, self.c)
        await self.lines()
        after = await self.memory.standing_index(GUILD_A, self.c)
        self.assertEqual(before.attitudes, after.attitudes)
        self.assertEqual(before.impressions, after.impressions)
        page = await self.claims.waiting(GUILD_A, self.c, opted_in={7})
        self.assertEqual([c.status for c in page], ["proposed"])


class OnTheCard(unittest.TestCase):
    def test_the_line_sits_under_its_question(self) -> None:
        from dmbot.memory.claims import Claim
        from dmbot.ui import claims_review

        claim = Claim(
            "q1", "Tala", "knows", "Brewner", "", "dm_said", ("1",), (7,), "extractor", "p", "m",
            "proposed", "", 1, 1, None, None,
        )  # fmt: skip
        view = claims_review.ClaimsReview("c", GUILD_A)
        view.page = [claim]
        view.feelings = {"q1": "🤔 **Mithral Keep** (Tala's home) is wary of dwarves."}
        text = claims_review.page_text(view.page, 1, {}, view._hints())
        self.assertIn("**Tala** knows **Brewner**", text)
        self.assertIn("\n   🤔 **Mithral Keep** (Tala's home) is wary of dwarves.", text)
