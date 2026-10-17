"""Who knows a secret (#1649): the clue kind, the DM's list for a clue only some people know,
the after-session heads-up for someone outside the list, the "They learned it" answer, recall that
keeps such a clue from players, and a backup that brings the list back."""

from __future__ import annotations

import unittest
from typing import Any

from dmbot.campaigns.store import decode_backup, encode_backup
from dmbot.memory.claims import ClaimStore, Extracted
from dmbot.memory.clues import (
    NEEDS_SOMEONE,
    ONLY_CHARACTERS,
    ONLY_CLUES,
    outside_audience,
    says_they_acted_on_it,
)
from dmbot.memory.continuity import (
    CHANGED,
    IGNORE,
    KNOWS_A_SECRET,
    LIKELY,
    NOT_TRUE,
    POSSIBLE,
    ContinuityStore,
    Levels,
)
from dmbot.memory.models import MemoryRuleError
from dmbot.memory.recall import DM_SCREEN, PLAYERS, RecallService
from dmbot.ui import continuity as ui
from dmbot.ui import name_card
from tests.test_memory_store import GUILD_A, GUILD_B, ConfirmedMemoryTest

SMALL = Levels(min_facts=1, min_sessions=1)
AGREED = {7, 8}
LATER, EARLIER = 500, 100
DM_SOURCE = "dm"


def said(subject: str, relationship: str, obj: str) -> Extracted:
    return Extracted(subject, relationship, obj, "", ("1",), "dm_said")


class Words(unittest.TestCase):
    def test_telling_and_using_are_the_stronger_words(self) -> None:
        for yes in ("told", "Tells", "revealed", "used", "whispers to", "spoke of"):
            self.assertTrue(says_they_acted_on_it(yes), yes)
        for no in ("knows", "is near", "dislikes", ""):
            self.assertFalse(says_they_acted_on_it(no), no)

    def test_only_a_name_not_listed_is_outside(self) -> None:
        self.assertTrue(outside_audience("a", {"b"}))
        self.assertFalse(outside_audience("a", {"a", "b"}))


class Clues(ConfirmedMemoryTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.claims = ClaimStore(self.memory)
        self.store = ContinuityStore(self.memory)
        self.secret = await self.add("The baron's secret", type="clue")
        self.cerric = await self.add("Cerric", type="character")
        self.kesh = await self.add("Kesh", type="character")
        self.sword = await self.add("Heartseeker", type="item")

    async def limit(self, *who: str) -> None:
        for person in who:
            await self.memory.add_knower(
                GUILD_A, self.c, self.secret, person, source=DM_SOURCE, from_session_at=EARLIER
            )  # (dated, so the story has a session to count)
        await self.memory.set_audience_limited(GUILD_A, self.c, self.secret, True, source=DM_SOURCE)

    async def claim(self, *found: Extracted) -> None:
        await self.claims.propose(
            GUILD_A, self.c, list(found), {"1": 7}, opted_in=AGREED, prompt_version="p1",
            model="m1", session_started_at=LATER,
        )  # fmt: skip

    async def check(self) -> Any:
        return await self.store.check(GUILD_A, self.c, opted_in=AGREED, levels=SMALL)

    async def audience(self) -> list[str]:
        return [e.name for e in await self.memory.clue_audience(GUILD_A, self.c, self.secret)]

    # ---- the data and the DM's list -------------------------------------------------------

    async def test_a_clue_is_a_kind_of_idea_and_starts_open_to_everyone(self) -> None:
        clue = await self.memory.entity(GUILD_A, self.c, self.secret)
        assert clue is not None
        self.assertEqual((clue.type, clue.audience_limited), ("clue", False))
        onto = await self.memory.ontology(GUILD_A, self.c)
        self.assertTrue(onto.is_a("clue", "concept"))

    async def test_the_switch_needs_a_person_first_and_only_the_dm_and_only_a_clue(self) -> None:
        with self.assertRaises(MemoryRuleError) as nobody:
            await self.memory.set_audience_limited(
                GUILD_A, self.c, self.secret, True, source=DM_SOURCE
            )
        self.assertEqual(str(nobody.exception), NEEDS_SOMEONE)
        await self.memory.add_knower(GUILD_A, self.c, self.secret, self.cerric, source=DM_SOURCE)
        with self.assertRaises(MemoryRuleError):
            await self.memory.set_audience_limited(
                GUILD_A, self.c, self.secret, True, source="extractor"
            )
        with self.assertRaises(MemoryRuleError) as not_a_clue:
            await self.memory.set_audience_limited(
                GUILD_A, self.c, self.sword, True, source=DM_SOURCE
            )
        self.assertEqual(str(not_a_clue.exception), ONLY_CLUES)
        written = await self.memory.set_audience_limited(
            GUILD_A, self.c, self.secret, True, source=DM_SOURCE
        )
        self.assertTrue(written.value.audience_limited)

    async def test_only_a_character_can_be_listed_and_only_for_a_clue(self) -> None:
        with self.assertRaises(MemoryRuleError) as item:
            await self.memory.add_knower(GUILD_A, self.c, self.secret, self.sword, source="dm")
        self.assertEqual(str(item.exception), ONLY_CHARACTERS)
        with self.assertRaises(MemoryRuleError) as other:
            await self.memory.add_knower(GUILD_A, self.c, self.sword, self.cerric, source="dm")
        self.assertEqual(str(other.exception), ONLY_CLUES)
        with self.assertRaises(MemoryRuleError):
            await self.memory.add_knower(GUILD_A, self.c, self.secret, self.cerric, source="player")

    async def test_listing_and_unlisting_a_person_and_the_last_one_turns_the_switch_off(
        self,
    ) -> None:
        await self.limit(self.cerric, self.kesh)
        self.assertEqual(await self.audience(), ["Cerric", "Kesh"])
        first = await self.memory.remove_knower(
            GUILD_A, self.c, self.secret, self.kesh, source=DM_SOURCE
        )
        self.assertEqual(first.value[1], False)
        self.assertEqual(await self.audience(), ["Cerric"])
        last = await self.memory.remove_knower(
            GUILD_A, self.c, self.secret, self.cerric, source=DM_SOURCE
        )
        clue, turned_off = last.value
        self.assertEqual((clue.audience_limited, turned_off), (False, True))
        self.assertEqual(await self.audience(), [])
        with self.assertRaises(MemoryRuleError):  # not on the list any more
            await self.memory.remove_knower(
                GUILD_A, self.c, self.secret, self.cerric, source=DM_SOURCE
            )

    async def test_a_clue_made_another_kind_loses_its_switch(self) -> None:
        await self.limit(self.cerric)
        written = await self.memory.set_entity_type(
            GUILD_A, self.c, self.secret, "concept", source=DM_SOURCE
        )
        self.assertFalse(written.value.audience_limited)

    async def test_the_list_belongs_to_one_campaign(self) -> None:
        await self.limit(self.cerric)
        other = (await self.campaigns.create(GUILD_A, "Elsewhere", 7)).id
        self.assertEqual(await self.memory.clue_audience(GUILD_A, other, self.secret), [])
        with self.assertRaises(MemoryRuleError):  # another server cannot even open this one
            await self.memory.clue_audience(GUILD_B, self.c, self.secret)

    # ---- the heads-up ---------------------------------------------------------------------

    async def test_someone_outside_the_list_speaking_of_it_is_possible(self) -> None:
        await self.limit(self.cerric)
        await self.claim(said("Kesh", "knows", "The baron's secret"))
        (conflict,) = (await self.check()).conflicts
        self.assertEqual((conflict.kind, conflict.level), (KNOWS_A_SECRET, POSSIBLE))
        self.assertEqual(
            (conflict.entity_name, conflict.learner_name, conflict.earlier_words),
            ("The baron's secret", "Kesh", "Cerric"),
        )

    async def test_telling_or_using_it_is_likely(self) -> None:
        await self.limit(self.cerric)
        await self.claim(said("Kesh", "told", "The baron's secret"))
        (conflict,) = (await self.check()).conflicts
        self.assertEqual((conflict.kind, conflict.level), (KNOWS_A_SECRET, LIKELY))

    async def test_someone_on_the_list_makes_no_warning(self) -> None:
        await self.limit(self.cerric)
        await self.claim(said("Cerric", "told", "The baron's secret"))
        self.assertEqual((await self.check()).conflicts, ())

    async def test_a_clue_open_to_everyone_never_warns(self) -> None:
        # No switch, even with someone listed: a missing "knows" means DMbot doesn't know.
        await self.memory.add_knower(GUILD_A, self.c, self.secret, self.cerric, source="dm")
        await self.claim(said("Kesh", "told", "The baron's secret"))
        self.assertEqual((await self.check()).conflicts, ())

    async def test_an_ordinary_idea_never_warns(self) -> None:
        await self.add("The old prophecy", type="concept")
        await self.claim(said("Kesh", "told", "The old prophecy"))
        self.assertEqual((await self.check()).conflicts, ())

    async def test_a_name_still_waiting_to_be_confirmed_gives_no_warning(self) -> None:
        await self.limit(self.cerric)
        await self.add("Mira", type="character", status="proposed")
        await self.claim(said("Mira", "told", "The baron's secret"))
        self.assertEqual((await self.check()).conflicts, ())

    async def test_a_listed_fact_the_dm_made_secret_still_counts_as_listed(self) -> None:
        await self.limit(self.cerric)
        (fact,) = await self.memory.relations(
            GUILD_A, self.c, entity_id=self.cerric, confirmed_only=True
        )
        await self.memory.update_relation(GUILD_A, self.c, fact.id, source=DM_SOURCE, secret=True)
        await self.claim(said("Cerric", "told", "The baron's secret"))
        self.assertEqual((await self.check()).conflicts, ())

    async def test_they_learned_it_writes_a_dated_fact_and_stops_the_warning(self) -> None:
        await self.limit(self.cerric)
        await self.claim(said("Kesh", "told", "The baron's secret"))
        (conflict,) = (await self.check()).conflicts
        self.assertEqual(ui.changed_label(conflict), "They learned it")
        await self.store.answer(
            GUILD_A, self.c, conflict.claim.id, CHANGED, opted_in=AGREED, levels=SMALL
        )
        self.assertEqual(await self.audience(), ["Cerric", "Kesh"])
        facts = [
            r
            for r in await self.memory.relations(GUILD_A, self.c, confirmed_only=True)
            if r.predicate == "knows" and r.subject_id == self.kesh
        ]
        self.assertEqual([f.from_session_at for f in facts], [LATER])
        await self.claim(said("Kesh", "told", "The baron's secret"))
        self.assertEqual((await self.check()).conflicts, ())

    async def test_wasnt_real_and_ignore_do_not_add_anyone(self) -> None:
        await self.limit(self.cerric)
        await self.claim(said("Kesh", "told", "The baron's secret"))
        (conflict,) = (await self.check()).conflicts
        for action in (NOT_TRUE, IGNORE):
            await self.store.answer(
                GUILD_A, self.c, conflict.claim.id, action, opted_in=AGREED, levels=SMALL
            )
        self.assertEqual(await self.audience(), ["Cerric"])

    async def test_the_line_names_the_clue_the_person_and_the_list(self) -> None:
        await self.limit(self.cerric)
        await self.claim(said("Kesh", "told", "The baron's secret"))
        (conflict,) = (await self.check()).conflicts
        line = ui.line_text(conflict, {})
        self.assertIn("**Check: Kesh** spoke of **The baron's secret** and isn't on its list", line)
        self.assertIn("(Cerric)", line)
        self.assertTrue(line.endswith("(likely)"))

    # ---- who sees it ----------------------------------------------------------------------

    async def test_players_never_hear_of_a_limited_clue_but_the_dm_does(self) -> None:
        await self.limit(self.cerric)
        service = RecallService(self.memory)
        players = await service.recall(
            GUILD_A, self.c, seeds=["Cerric"], audience=PLAYERS, opted_in=AGREED
        )
        self.assertNotIn("baron's secret", " ".join(i.text for i in players.items))
        by_name = await service.recall(
            GUILD_A, self.c, seeds=["The baron's secret"], audience=PLAYERS, opted_in=AGREED
        )
        self.assertEqual((by_name.items, by_name.seeds), ((), ()))
        dm = await service.recall(
            GUILD_A, self.c, seeds=["Cerric"], audience=DM_SCREEN, opted_in=AGREED
        )
        self.assertIn("baron's secret", " ".join(i.text for i in dm.items))

    async def test_a_clue_open_to_everyone_is_ordinary_for_players(self) -> None:
        await self.memory.add_knower(GUILD_A, self.c, self.secret, self.cerric, source="dm")
        players = await RecallService(self.memory).recall(
            GUILD_A, self.c, seeds=["Cerric"], audience=PLAYERS, opted_in=AGREED
        )
        self.assertIn("baron's secret", " ".join(i.text for i in players.items))

    async def test_the_card_of_a_limited_clue_is_the_dms_alone(self) -> None:
        await self.limit(self.cerric, self.kesh)
        data = await self.memory.lookup_data(GUILD_A, self.c)
        from dmbot.memory.lookup import CampaignLookup

        lookup = CampaignLookup.build(data)
        connections = await self.memory.relations(GUILD_A, self.c, entity_id=self.secret)
        dm = name_card.card_text(lookup, self.secret, connections, secrets=True)
        assert dm is not None
        self.assertIn("🔒 **Only some people know this:** Cerric, Kesh", dm)
        self.assertIsNone(name_card.card_text(lookup, self.secret, connections, secrets=False))

    async def view(self, secrets: bool) -> Any:
        from dmbot.memory.lookup import CampaignLookup

        return CampaignLookup.build(await self.memory.lookup_data(GUILD_A, self.c)).for_viewer(
            secrets
        )

    async def test_a_manager_who_is_not_a_dm_finds_the_clue_nowhere(self) -> None:
        from dmbot.memory.search import find, said_lately
        from dmbot.ui import name_lists
        from dmbot.ui import names as names_ui

        await self.limit(self.cerric)
        dm, other = await self.view(True), await self.view(False)
        self.assertIn(self.secret, dm.entities)
        self.assertNotIn(self.secret, other.entities)
        self.assertEqual([m.entity_id for m in find(other, "baron", secrets=False)], [])
        self.assertEqual([m.entity_id for m in find(dm, "baron", secrets=True)], [self.secret])
        self.assertNotIn(self.secret, said_lately(other))
        campaign = await self.campaigns.get(GUILD_A, self.c)
        assert campaign is not None
        text, shown = names_ui.home_text(other, campaign, 0)
        self.assertNotIn("baron", text + " ".join(n for _, n, _ in shown))
        self.assertEqual([k for k, _ in name_lists.kinds_in(other)].count("clue"), 0)
        files, _count = name_lists.download_files(other, campaign, secrets=False)
        self.assertNotIn("baron", "".join(files))
        dm_files, _ = name_lists.download_files(dm, campaign, secrets=True)
        self.assertIn("baron", "".join(dm_files))

    async def test_a_knowers_card_does_not_mention_a_limited_clue_to_a_non_dm(self) -> None:
        await self.limit(self.cerric)
        connections = await self.memory.relations(GUILD_A, self.c, entity_id=self.cerric)
        other = name_card.card_text(await self.view(False), self.cerric, connections, secrets=False)
        dm = name_card.card_text(await self.view(True), self.cerric, connections, secrets=True)
        assert other is not None and dm is not None
        self.assertNotIn("baron", other)
        self.assertIn("baron", dm)
        clue_card = name_card.card_text(
            await self.view(True),
            self.secret,
            await self.memory.relations(GUILD_A, self.c, entity_id=self.secret),
            secrets=True,
        )
        assert clue_card is not None
        self.assertNotIn("Secret:", clue_card)  # its own name is the title, not a second line

    async def test_the_cleaner_never_writes_a_limited_clue_into_a_transcript(self) -> None:
        await self.limit(self.cerric)
        lookup = await self.view(True)
        self.assertEqual(lookup.sounds_like("The barons secret"), ())
        self.assertTrue(
            all(n.secret for n in lookup.names if n.entity_id == self.secret),
            "its names are kept as secret names",
        )

    async def test_a_switch_left_on_with_nobody_listed_warns_about_no_one(self) -> None:
        await self.limit(self.cerric)
        (fact,) = [
            r
            for r in await self.memory.relations(GUILD_A, self.c, entity_id=self.secret)
            if r.subject_id == self.cerric
        ]
        # Removed some other way than the clue's own screen (a connection removed on a card).
        await self.memory.update_relation(
            GUILD_A, self.c, fact.id, source=DM_SOURCE, status="rejected"
        )
        clue = await self.memory.entity(GUILD_A, self.c, self.secret)
        assert clue is not None
        self.assertTrue(clue.audience_limited)
        await self.relate(self.cerric, "ally_of", self.kesh, status="confirmed",
                          from_session_at=EARLIER)  # fmt: skip
        await self.claim(said("Kesh", "told", "The baron's secret"))
        self.assertEqual((await self.check()).conflicts, ())

    # ---- backups --------------------------------------------------------------------------

    async def test_the_switch_and_the_list_come_back_from_a_backup(self) -> None:
        await self.limit(self.cerric)
        raw = encode_backup(await self.campaigns.export(GUILD_A, self.c))
        restored = await self.campaigns.import_backup(GUILD_B, decode_backup(raw), 7)
        entities = {e.name: e for e in await self.memory.entities(GUILD_B, restored.id)}
        clue = entities["The baron's secret"]
        self.assertEqual((clue.type, clue.audience_limited), ("clue", True))
        who = await self.memory.clue_audience(GUILD_B, restored.id, clue.id)
        self.assertEqual([w.name for w in who], ["Cerric"])

    async def test_an_older_backup_without_the_switch_restores_as_open(self) -> None:
        data = await self.campaigns.export(GUILD_A, self.c)
        for row in data["sections"]["memory"]:
            if row.get("table") == "entity":
                row.pop("audience_limited", None)
        restored = await self.campaigns.import_backup(GUILD_B, data, 7)
        entities = await self.memory.entities(GUILD_B, restored.id)
        self.assertFalse(any(e.audience_limited for e in entities))

    async def test_a_switch_on_something_that_is_not_a_clue_is_refused_as_damaged(self) -> None:
        from dmbot.campaigns.models import CampaignError

        data = await self.campaigns.export(GUILD_A, self.c)
        for row in data["sections"]["memory"]:
            if row.get("table") == "entity" and row["type"] == "item":
                row["audience_limited"] = True
        with self.assertRaises(CampaignError):
            await self.campaigns.import_backup(GUILD_B, data, 7)


if __name__ == "__main__":
    unittest.main()
