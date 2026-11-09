"""The DM's own Reputation wins on a `knows` line (#3642): a standing the DM set for the pair
replaces the reader's word on the line, on the DM's card only; a player's card never shows it.
Invented names; the store reader's tests need Postgres."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any

from dmbot.memory import standing
from dmbot.memory.lookup import CampaignLookup
from dmbot.memory.models import BACKGROUND_SOURCE, CONFIRMED, NewName, Relation
from dmbot.ui.name_card import _rows, card_text, sections
from tests.test_memory_names import GUILD, NamesTest

MIRA, KESH = "m" * 32, "k" * 32


class TheWord(unittest.TestCase):
    def test_each_step_reads_in_the_readers_words(self) -> None:
        said = {
            step: standing.dm_line_word({("a", "b"): score}, "a", "b")
            for step, score in standing.SCORE_OF.items()
        }
        self.assertEqual(
            said,
            {
                standing.HOSTILE: "hostile",
                standing.UNFRIENDLY: "distrusts",
                standing.WARY: "wary",
                standing.FRIENDLY: "friendly",
                standing.DEVOTED: "devoted",
            },
        )

    def test_the_line_s_own_way_is_preferred(self) -> None:
        both = {("a", "b"): -80, ("b", "a"): 80}
        self.assertEqual(standing.dm_line_word(both, "a", "b"), "hostile")
        self.assertEqual(standing.dm_line_word(both, "b", "a"), "devoted")

    def test_the_other_way_is_used_when_that_is_all_there_is(self) -> None:
        self.assertEqual(standing.dm_line_word({("b", "a"): 40}, "a", "b"), "friendly")

    def test_the_line_s_own_word_stays_when_it_is_on_the_dm_s_step(self) -> None:
        self.assertEqual(standing.dm_line_word({("a", "b"): 40}, "a", "b", "trusts"), "trusts")
        self.assertEqual(standing.dm_line_word({("a", "b"): 80}, "a", "b", "Close"), "Close")
        self.assertEqual(standing.dm_line_word({("a", "b"): -80}, "a", "b", "trusts"), "hostile")

    def test_none_when_the_dm_set_nothing_for_the_pair(self) -> None:
        self.assertIsNone(standing.dm_line_word({("a", "c"): 40}, "a", "b"))
        self.assertIsNone(standing.dm_line_word({}, "a", "b"))


def _lookup() -> Any:
    def entity(eid: str, name: str) -> Any:
        return SimpleNamespace(id=eid, name=name, type="character", status=CONFIRMED)

    mira, kesh = entity(MIRA, "Mira"), entity(KESH, "Kesh")
    return SimpleNamespace(entities={MIRA: mira, KESH: kesh}, names=(), guarded=frozenset())


def _knows(detail: str = "friendly", predicate: str = "knows") -> Relation:
    return Relation(
        "x" * 32, MIRA, predicate, KESH, detail, 1.0, CONFIRMED, "dm", (),
        None, None, None, None, False, 0,
    )  # fmt: skip


class TheCardRows(unittest.TestCase):
    """The rows the card, Show all and the Edit menu read (`name_card._rows`)."""

    def words(self, *, secrets: bool, scores: dict[tuple[str, str], int], r: Relation) -> list[str]:
        return [row.words for row in _rows(_lookup(), MIRA, [r], secrets, scores)]

    def test_the_dm_s_word_wins_on_the_dm_s_card(self) -> None:
        said = self.words(secrets=True, scores={(MIRA, KESH): -80}, r=_knows("friendly"))
        self.assertEqual(said, ["knows of (hostile)"])

    def test_either_direction_counts(self) -> None:
        said = self.words(secrets=True, scores={(KESH, MIRA): -40}, r=_knows(""))
        self.assertEqual(said, ["knows of (distrusts)"])

    def test_a_player_never_sees_it(self) -> None:
        said = self.words(secrets=False, scores={(MIRA, KESH): -80}, r=_knows(""))
        self.assertEqual(said, ["knows of"])

    def test_no_standing_keeps_the_line_s_own_word(self) -> None:
        self.assertEqual(self.words(secrets=True, scores={}, r=_knows()), ["knows of (friendly)"])

    def test_only_a_knows_line_takes_it(self) -> None:
        said = self.words(secrets=True, scores={(MIRA, KESH): -80}, r=_knows("", "rival_of"))
        self.assertNotIn("hostile", " ".join(said))

    def test_the_row_keeps_the_stored_connection(self) -> None:
        r = _knows("friendly")
        (row,) = _rows(_lookup(), MIRA, [r], True, {(MIRA, KESH): -80})
        self.assertEqual(row.relation.detail, "friendly")  # (the Edit menu changes what is stored)

    def test_the_other_end_s_card_reads_is_known_to(self) -> None:
        (row,) = _rows(_lookup(), KESH, [_knows()], True, {(KESH, MIRA): -80})
        self.assertEqual(row.words, "is known to")  # (no feeling there, before or after #3642)

    def test_show_all_reads_the_same(self) -> None:
        lines = sections(
            _lookup(), MIRA, [_knows()], secrets=True, standing={(MIRA, KESH): 80}
        ).get("links")
        self.assertEqual(lines, ["knows of (devoted) **Kesh**"])


class ThePlayerGuard(unittest.IsolatedAsyncioTestCase):
    async def test_a_player_s_press_never_reads_the_dm_s_standings(self) -> None:
        from dmbot.ui.name_card import _dm_standing

        class NoReads:
            async def dm_line_scores(self, *_: Any) -> dict[tuple[str, str], int]:
                raise AssertionError("read for a player")

        campaign: Any = SimpleNamespace(guild_id=1, id="c")
        self.assertEqual(await _dm_standing(NoReads(), campaign, MIRA, False), {})  # type: ignore[arg-type]


class TheStoreReader(NamesTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        made = await self.memory.add_names(
            GUILD, self.campaign.id,
            [NewName("Belleros", "npc", CONFIRMED), NewName("Cerric", "npc", CONFIRMED),
             NewName("Oakmere", "npc", CONFIRMED)],
            source="dm",
        )  # fmt: skip
        bel, cer, oak = made.value
        assert bel and cer and oak
        self.bel: str = bel
        self.cer: str = cer
        self.oak: str = oak

    async def scores(self, entity_id: str) -> dict[tuple[str, str], int]:
        return await self.memory.dm_line_scores(GUILD, self.campaign.id, entity_id)

    async def test_the_dm_s_open_standings_either_way(self) -> None:
        await self.memory.set_impression(
            GUILD, self.campaign.id, self.bel, self.cer, standing.HOSTILE, source="dm"
        )
        await self.memory.set_impression(
            GUILD, self.campaign.id, self.oak, self.bel, standing.FRIENDLY, source="dm"
        )
        self.assertEqual(
            await self.scores(self.bel), {(self.bel, self.cer): -80, (self.oak, self.bel): 40}
        )
        self.assertEqual(await self.scores(self.cer), {(self.bel, self.cer): -80})

    async def test_only_the_latest_one_counts(self) -> None:
        for step in (standing.HOSTILE, standing.DEVOTED):
            await self.memory.set_impression(
                GUILD, self.campaign.id, self.bel, self.cer, step, source="dm"
            )
        self.assertEqual(await self.scores(self.bel), {(self.bel, self.cer): 80})

    async def test_a_standing_from_the_story_is_not_the_dm_s(self) -> None:
        await self.memory.add_feeling(GUILD, self.campaign.id, self.bel, self.cer, -80)
        rows = await self.memory.opinion_history(
            GUILD, self.campaign.id, self.bel, self.cer, attitude=False
        )
        self.assertEqual([r.source for r in rows], [BACKGROUND_SOURCE])  # (it was written)
        self.assertEqual(await self.scores(self.bel), {})

    async def test_the_whole_card_dm_and_player(self) -> None:
        """End to end: the stored line says "friendly", the DM set Hostile; the DM's card reads the
        DM's word, a player's reads plain "knows" and nothing of either."""
        made = await self.memory.add_relation(
            GUILD,
            self.campaign.id,
            self.bel,
            "knows",
            self.cer,
            source="dm",
            confidence=1.0,
            status=CONFIRMED,
            detail="friendly",
        )
        self.assertIsNotNone(made.value)
        await self.memory.set_impression(
            GUILD, self.campaign.id, self.bel, self.cer, standing.HOSTILE, source="dm"
        )
        for eid in (self.bel, self.cer):  # (players know both, so a player is shown the card)
            await self.memory.set_hidden_from_players(
                GUILD, self.campaign.id, eid, False, source="dm"
            )
        lookup = CampaignLookup.build(await self.memory.lookup_data(GUILD, self.campaign.id))
        links = await self.memory.relations(GUILD, self.campaign.id, entity_id=self.bel)
        scores = await self.scores(self.bel)
        dm = card_text(lookup.for_viewer(True), self.bel, links, secrets=True, standing=scores)
        seen = lookup.for_viewer(False)
        players = card_text(seen, self.bel, links, secrets=False, standing=scores)
        self.assertIn("knows of (hostile)", dm or "")
        self.assertNotIn("friendly", dm or "")
        self.assertIn("knows of **Cerric**", players or "")
        for word in ("hostile", "friendly"):
            self.assertNotIn(word, players or "")


class ThePicker(NamesTest):
    """The DM's feeling pick on a `knows` line writes their Reputation in the same change (#3642),
    so the line never seems to ignore the pick, and one Undo puts both back."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        made = await self.memory.add_names(
            GUILD, self.campaign.id,
            [NewName("Belleros", "npc", CONFIRMED), NewName("Cerric", "npc", CONFIRMED)],
            source="dm",
        )  # fmt: skip
        bel, cer = made.value
        assert bel and cer
        self.bel: str = bel
        self.cer: str = cer
        link = await self.memory.add_relation(
            GUILD, self.campaign.id, self.bel, "knows", self.cer, source="dm", confidence=1.0,
            status=CONFIRMED, detail="wary",
        )  # fmt: skip
        assert link.value is not None
        self.link = link.value[0]

    async def pick(self, word: str) -> Any:
        """As the website's picker sends it: "feeling:<word>" read as "knows (<word>)"."""
        from dmbot.memory.link_edits import change_words
        from dmbot.memory.link_words import predicate_for

        predicate, detail = predicate_for(f"knows ({word})")
        return await change_words(
            self.memory, GUILD, self.campaign.id, self.link, predicate, detail, dm_feeling=True
        )

    async def scores(self) -> dict[tuple[str, str], int]:
        return await self.memory.dm_line_scores(GUILD, self.campaign.id, self.bel)

    async def test_a_pick_over_a_dm_standing_moves_both_and_one_undo_puts_both_back(self) -> None:
        await self.memory.set_impression(
            GUILD, self.campaign.id, self.bel, self.cer, standing.HOSTILE, source="dm"
        )
        done = await self.pick("trusts")
        assert done is not None and done.batch is not None
        self.assertEqual(done.value.detail, "trusts")
        self.assertEqual(await self.scores(), {(self.bel, self.cer): 40})
        self.assertEqual(standing.dm_line_word(await self.scores(), self.bel, self.cer, "trusts"),
                         "trusts")  # fmt: skip
        await self.memory.undo(GUILD, self.campaign.id, done.batch)
        self.assertEqual(await self.scores(), {(self.bel, self.cer): -80})
        (line,) = await self.memory.relations(GUILD, self.campaign.id, entity_id=self.bel)
        self.assertEqual(line.detail, "wary")

    async def test_picking_the_line_s_own_word_again_still_sets_the_standing(self) -> None:
        await self.memory.set_impression(
            GUILD, self.campaign.id, self.bel, self.cer, standing.DEVOTED, source="dm"
        )
        done = await self.pick("wary")
        assert done is not None and done.batch is not None  # (an Undo to offer)
        self.assertEqual(await self.scores(), {(self.bel, self.cer): 0})
        await self.memory.undo(GUILD, self.campaign.id, done.batch)
        self.assertEqual(await self.scores(), {(self.bel, self.cer): 80})

    async def test_a_pick_that_changes_nothing_writes_nothing(self) -> None:
        await self.memory.set_impression(
            GUILD, self.campaign.id, self.bel, self.cer, standing.WARY, source="dm"
        )
        self.assertIsNone(await self.pick("wary"))

    async def test_neutral_closes_the_dm_s_standing(self) -> None:
        await self.memory.set_impression(
            GUILD, self.campaign.id, self.bel, self.cer, standing.HOSTILE, source="dm"
        )
        done = await self.pick("neutral")
        assert done is not None
        self.assertEqual(done.value.detail, "")  # (neutral is stored as no word)
        self.assertEqual(await self.scores(), {})
        self.assertIsNone(standing.dm_line_word(await self.scores(), self.bel, self.cer, ""))

    async def test_neutral_leaves_a_standing_from_the_story(self) -> None:
        await self.memory.add_feeling(GUILD, self.campaign.id, self.bel, self.cer, -80)
        await self.pick("neutral")
        rows = await self.memory.opinion_history(
            GUILD, self.campaign.id, self.bel, self.cer, attitude=False
        )
        self.assertEqual([(r.source, r.closed_at) for r in rows], [(BACKGROUND_SOURCE, None)])

    async def test_a_reader_s_change_of_words_sets_no_standing(self) -> None:
        from dmbot.memory.link_edits import change_words

        await change_words(self.memory, GUILD, self.campaign.id, self.link, "knows", "trusts")
        self.assertEqual(await self.scores(), {})
