"""How two people know each other (#3881): "has met", "has heard of" or plain "knows". The words
read from a quote, the card's wording, and the store's only-up rule. Invented names (Tran Vosk,
Bruno Hale, Shela Marr)."""

from __future__ import annotations

import unittest

from dmbot.campaigns import CampaignStore
from dmbot.memory.backup import MemorySection
from dmbot.memory.knows_how import how_from
from dmbot.memory.link_words import link_words
from dmbot.memory.models import CONFIRMED, KNOWS_AWARE, KNOWS_MET, MemoryRuleError, knows_rank
from dmbot.memory.models import DM as DM_SOURCE
from dmbot.memory.store import MemoryStore
from tests.pg import DatabaseTest


class TheWords(unittest.TestCase):
    def test_a_meeting_line_is_met(self) -> None:
        for said in ("Tran met Bruno at the inn.", "They first crossed paths in Lonelywood.",
                     "Shela spoke with Tran about the map.", "Bruno befriended a sled dog.",
                     "a heated debate about smelting with a dwarf named Bruno"):  # fmt: skip
            with self.subTest(said):
                self.assertEqual(how_from(said), KNOWS_MET)

    def test_a_tale_a_rumour_or_a_vision_is_aware(self) -> None:
        for said in ("Tran had heard tales of Bruno.", "Rumours of Shela reached the town.",
                     "She saw him in a vision.", "He learned of her from a letter from Bruno.",
                     "Bruno knows of the wizard."):  # fmt: skip
            with self.subTest(said):
                self.assertEqual(how_from(said), KNOWS_AWARE)

    def test_never_met_is_aware(self) -> None:
        self.assertEqual(how_from("Tran has never met Bruno, only heard of him."), KNOWS_AWARE)
        self.assertEqual(how_from("They haven't yet met."), KNOWS_AWARE)

    def test_never_met_and_met_together_is_unsure(self) -> None:
        self.assertEqual(how_from("Tran had never met the king, but met Bruno often."), "")

    def test_what_can_be_done_from_afar_is_no_meeting(self) -> None:
        for said in ("Tran hired an assassin to kill Bruno.", "Bruno employs spies to watch Tran.",
                     "Tran sent goblins that attacked Bruno's caravan.",
                     "Bruno saved the town; Tran admires him.", "Tran captured the fort.",
                     "Tran attended a meeting about Bruno.", "Bruno met with resistance.",
                     "Tran met his end in the snow.",
                     "Bruno gave his word of honour."):  # fmt: skip
            with self.subTest(said):
                self.assertEqual(how_from(said, a="Tran Vosk", b="Bruno Hale"), "")

    def test_only_sentences_naming_both_are_read(self) -> None:
        said = "The party met Bruno at dawn. Tran was absent; Tran knows Bruno."
        self.assertEqual(how_from(said, a="Tran Vosk", b="Bruno Hale"), "")
        self.assertEqual(how_from(said, a="Shela", b="Bruno"), KNOWS_MET)  # (no sentence: all)

    def test_no_sign_says_nothing(self) -> None:
        for said in ("Tran is wary of Bruno.", "The metal was cold.", "", "Shela trusts Tran."):
            with self.subTest(said):
                self.assertEqual(how_from(said), "")

    def test_the_rank_only_goes_up_from_not_said(self) -> None:
        self.assertLess(knows_rank(""), knows_rank(KNOWS_AWARE))
        self.assertLess(knows_rank(KNOWS_AWARE), knows_rank(KNOWS_MET))
        self.assertEqual(knows_rank("nonsense"), 0)


class TheCard(unittest.TestCase):
    def test_a_knows_says_whether_they_met(self) -> None:
        self.assertEqual(link_words("knows", "", knows_how="met"), "has met")
        self.assertEqual(link_words("knows", "", knows_how="aware"), "has heard of")
        self.assertEqual(link_words("knows", "", knows_how=""), "knows")

    def test_the_dm_s_feeling_rides_along(self) -> None:
        self.assertEqual(
            link_words("knows", "wary", feeling=True, knows_how="met"), "has met (wary)"
        )

    def test_a_caller_with_no_row_keeps_plain_knows(self) -> None:
        self.assertEqual(link_words("knows", ""), "knows")
        self.assertEqual(link_words("owns", "", knows_how="met"), "owns")


GUILD, DM = 1, 7


class TheStore(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.campaigns = CampaignStore(self.db)
        self.campaigns.register_section(MemorySection())
        self.memory = MemoryStore(self.db)
        self.campaign = await self.campaigns.create(GUILD, "Frostmaiden", DM)
        self.tran = await self.person("Tran Vosk")
        self.bruno = await self.person("Bruno Hale")

    async def person(self, name: str) -> str:
        written = await self.memory.add_entity(
            GUILD, self.campaign.id, type="character", name=name, source=DM_SOURCE,
            status=CONFIRMED,
        )  # fmt: skip
        return written.value.id

    async def knows(self, a: str, b: str, how: str = "", source: str = DM_SOURCE) -> str:
        written = await self.memory.add_relation(
            GUILD, self.campaign.id, a, "knows", b, source=source, confidence=1.0,
            status=CONFIRMED if source == DM_SOURCE else "proposed", knows_how=how,
        )  # fmt: skip
        return written.value[0].id

    async def how(self, relation_id: str) -> str:
        r = await self.memory.relation(GUILD, self.campaign.id, relation_id)
        assert r is not None
        return r.knows_how

    async def test_restating_only_moves_it_up(self) -> None:
        rid = await self.knows(self.tran, self.bruno, KNOWS_AWARE)
        self.assertEqual(await self.knows(self.tran, self.bruno, ""), rid)
        self.assertEqual(await self.how(rid), KNOWS_AWARE)
        await self.knows(self.tran, self.bruno, KNOWS_MET)
        self.assertEqual(await self.how(rid), KNOWS_MET)
        await self.knows(self.tran, self.bruno, KNOWS_AWARE)
        self.assertEqual(await self.how(rid), KNOWS_MET)

    async def test_the_dm_sets_it_either_way_and_undo_puts_it_back(self) -> None:
        rid = await self.knows(self.tran, self.bruno, KNOWS_MET)
        written = await self.memory.set_knows_how(
            GUILD, self.campaign.id, rid, KNOWS_AWARE, source=DM_SOURCE
        )
        self.assertEqual(await self.how(rid), KNOWS_AWARE)
        assert written.batch is not None
        await self.memory.undo(GUILD, self.campaign.id, written.batch)
        self.assertEqual(await self.how(rid), KNOWS_MET)

    async def test_anyone_else_only_moves_it_up(self) -> None:
        rid = await self.knows(self.tran, self.bruno, KNOWS_MET)
        await self.memory.set_knows_how(GUILD, self.campaign.id, rid, "", source="extractor")
        self.assertEqual(await self.how(rid), KNOWS_MET)

    async def test_met_moves_the_other_s_knows_up_but_never_makes_one(self) -> None:
        back = await self.knows(self.bruno, self.tran, KNOWS_AWARE)
        await self.knows(self.tran, self.bruno, KNOWS_MET)
        self.assertEqual(await self.how(back), KNOWS_MET)
        shela = await self.person("Shela Marr")
        await self.knows(self.tran, shela, KNOWS_MET)
        rows = await self.memory.relations(GUILD, self.campaign.id, include_secret=True)
        self.assertFalse([r for r in rows if r.subject_id == shela and r.object_id == self.tran])

    async def test_only_a_knows_carries_it(self) -> None:
        written = await self.memory.add_relation(
            GUILD, self.campaign.id, self.tran, "enemy_of", self.bruno, source=DM_SOURCE,
            confidence=1.0, status=CONFIRMED, knows_how=KNOWS_MET,
        )  # fmt: skip
        self.assertEqual(written.value[0].knows_how, "")
        with self.assertRaises(MemoryRuleError):
            await self.memory.set_knows_how(
                GUILD, self.campaign.id, written.value[0].id, KNOWS_MET, source=DM_SOURCE
            )

    async def test_a_secret_meeting_never_shows_on_a_public_row(self) -> None:
        back = await self.knows(self.bruno, self.tran, KNOWS_AWARE)
        await self.memory.add_relation(
            GUILD, self.campaign.id, self.tran, "knows", self.bruno, source=DM_SOURCE,
            confidence=1.0, status=CONFIRMED, knows_how=KNOWS_MET, secret=True,
        )  # fmt: skip
        self.assertEqual(await self.how(back), KNOWS_AWARE)

    async def test_a_new_feeling_keeps_whether_they_met(self) -> None:
        rid = await self.knows(self.tran, self.bruno, KNOWS_MET)
        moved = await self.memory.change_relation_words(
            GUILD, self.campaign.id, rid, "knows", "wary", source=DM_SOURCE
        )
        self.assertEqual(moved.value.knows_how, KNOWS_MET)

    async def test_a_merge_keeps_the_higher(self) -> None:
        rid = await self.knows(self.tran, self.bruno, KNOWS_AWARE)
        other = await self.person("Tran the Elder")
        await self.knows(other, self.bruno, KNOWS_MET)
        await self.memory.merge(GUILD, self.campaign.id, self.tran, other, source=DM_SOURCE)
        self.assertEqual(await self.how(rid), KNOWS_MET)

    async def test_a_place_or_a_thing_is_never_met(self) -> None:
        inn = await self.memory.add_entity(
            GUILD, self.campaign.id, type="place", name="The Lucky Liar", source=DM_SOURCE,
            status=CONFIRMED,
        )  # fmt: skip
        written = await self.memory.add_relation(
            GUILD, self.campaign.id, self.tran, "knows", inn.value.id, source=DM_SOURCE,
            confidence=1.0, status=CONFIRMED, knows_how=KNOWS_MET,
        )  # fmt: skip
        self.assertEqual(written.value[0].knows_how, "")
        with self.assertRaises(MemoryRuleError):
            await self.memory.set_knows_how(
                GUILD, self.campaign.id, written.value[0].id, KNOWS_MET, source=DM_SOURCE
            )

    async def test_an_old_row_reads_as_not_said(self) -> None:
        rid = await self.knows(self.tran, self.bruno)
        self.assertEqual(await self.how(rid), "")


class TheBackup(unittest.TestCase):
    def test_met_on_another_connection_is_damaged(self) -> None:
        from dmbot.campaigns.models import CampaignError
        from dmbot.memory.backup import _checked_rows

        row = {
            "table": "relation", "id": "a" * 32, "subject_id": "b" * 32, "predicate": "owns",
            "object_id": "c" * 32, "detail": "", "confidence": 1.0, "status": "confirmed",
            "source": "dm", "mention_ids": [], "from_session_at": None, "to_session_at": None,
            "from_game_time": None, "to_game_time": None, "secret": False, "created_at": 1,
            "definition_version": 1, "unrevealed": False, "directional": False, "decided": None,
            "via_alias_id": None, "knows_how": "met",
        }  # fmt: skip
        with self.assertRaises(CampaignError):
            _checked_rows([row])
        _checked_rows([{**row, "predicate": "knows"}])  # (a knows may carry it)


if __name__ == "__main__":
    unittest.main()
