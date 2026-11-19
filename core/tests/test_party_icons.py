"""Who knows it, in one line, against the current party (#4537, party knowledge slice 3a;
`docs/design/party_knowledge.md` §7 and §10; the ux-critic's words). Invented names and ids only."""

from __future__ import annotations

import unittest

from dmbot.campaigns import CampaignStore
from dmbot.memory import party_icons
from dmbot.memory.models import CONFIRMED, DM, PLAYER_CHARACTER
from dmbot.memory.party_icons import Outsider, line
from dmbot.memory.store import MemoryStore
from dmbot.ui import who_knows
from tests.pg import DatabaseTest

PARTY = [("c", "Cerric"), ("b", "Belleros"), ("m", "Mira")]
GUILD = 700_000_000_000_000_021
DM_ID, ANNA, BEN = 700_000_000_000_000_201, 700_000_000_000_000_202, 700_000_000_000_000_203


class TheLine(unittest.TestCase):
    def test_each_icon_by_its_rule(self) -> None:
        self.assertEqual(line([], PARTY, common=True), "🌍 Everyone knows")
        self.assertEqual(line(["c", "b", "m"], PARTY), "✅ The whole party knows")
        self.assertEqual(line(["m", "c"], PARTY), "👥 Known to: Cerric and Mira")  # (party order)
        self.assertEqual(line(["b"], PARTY), "👤 Known to: Belleros")
        self.assertEqual(line([], PARTY), "🤫 No one in the party knows. Only the DM does.")

    def test_a_party_of_one_is_the_whole_party_named(self) -> None:
        self.assertEqual(line(["c"], PARTY[:1]), "✅ The whole party knows (Cerric)")

    def test_everyone_comes_before_no_party(self) -> None:
        self.assertEqual(line([], [], common=True), "🌍 Everyone knows")
        self.assertEqual(line(["x"], []), "No player characters yet. Add one under Names.")

    def test_no_one_in_the_party_names_who_else_does_those_who_left_first(self) -> None:
        others = party_icons.ordered_outsiders(
            {"v": ("Vell", "died"), "t": ("Tuck", "left")}, {"a": "Brother Ansel"}
        )
        self.assertEqual([o.id for o in others], ["t", "v", "a"])  # (left first, by name)
        self.assertEqual(
            line(["a", "v"], PARTY, outsiders=others),
            "🤫 No one in the party knows. Known to: Vell (died) and Brother Ansel",
        )
        many = [Outsider(str(i), f"Npc{i}") for i in range(5)]
        self.assertEqual(
            line([o.id for o in many], PARTY, outsiders=many),
            "🤫 No one in the party knows. Known to: Npc0, Npc1, Npc2 and 2 others",
        )

    def test_outsiders_stay_off_a_party_line_and_names_are_escaped(self) -> None:
        others = [Outsider("a", "Brother Ansel")]
        self.assertEqual(line(["c", "a"], PARTY, outsiders=others), "👤 Known to: Cerric")
        self.assertEqual(
            line(["c"], [("c", "*Cerric*")], escape=lambda s: s.replace("*", "\\*")),
            "✅ The whole party knows (\\*Cerric\\*)",
        )

    def test_no_pronoun_in_any_line(self) -> None:
        texts = [line([], PARTY), line(["c"], PARTY), line(["c", "b"], PARTY), line([], PARTY[:0])]
        for text in texts:
            for word in ("he", "she", "him", "her", "his", "they"):
                self.assertNotIn(f" {word} ", f" {text} ")


class ThePicker(unittest.TestCase):
    def test_the_party_first_then_who_left_with_why_and_no_npcs(self) -> None:
        from dmbot.memory.store import PartyAndOthers

        found = PartyAndOthers(
            [("c", "Cerric"), ("b", "Belleros")],
            {"v": ("Vell", "retired"), "t": ("Tuck", "away")},
            {"a": "Ansel"},
        )
        picks, notes = who_knows.pick_choices(found)
        self.assertEqual([p[0] for p in picks], ["c", "b", "t", "v"])  # (who left: by name)
        self.assertEqual(notes, {"v": "Retired", "t": "Away"})

    def test_the_cut_drops_who_left_never_the_party(self) -> None:
        from dmbot.memory.store import PartyAndOthers

        party = [(f"p{i}", f"P{i}") for i in range(20)]
        left = {f"l{i}": (f"L{i}", "left") for i in range(10)}
        picks, notes = who_knows.pick_choices(PartyAndOthers(party, left, {}))
        self.assertEqual(len(picks), who_knows.MAX_PICK)
        self.assertEqual(picks[:20], party)
        self.assertEqual(set(notes.values()), {"Left the party"})
        self.assertEqual(len(notes), 5)


class TheStore(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.memory = MemoryStore(self.db)
        self.c = (await CampaignStore(self.db).create(GUILD, "Saltmarsh", DM_ID)).id

    async def pc(self, name: str, player: int) -> str:
        made = await self.memory.add_entity(
            GUILD, self.c, name=name, type="character", role=PLAYER_CHARACTER, played_by=player,
            status=CONFIRMED, source=DM,
        )  # fmt: skip
        return str(made.value.id)

    async def test_the_party_who_left_and_npcs_with_a_point_of_view(self) -> None:
        vell = await self.pc("Vell", ANNA)
        tuck = await self.pc("Tuck", BEN)
        npc = str(
            (await self.memory.add_entity(
                GUILD, self.c, name="Brother Ansel", type="character", status=CONFIRMED,
                source=DM,
            )).value.id
        )  # fmt: skip
        place = str(
            (await self.memory.add_entity(
                GUILD, self.c, name="Gull Rock", type="place", status=CONFIRMED, source=DM
            )).value.id
        )  # fmt: skip
        await self.memory.set_statement_knowers(  # (gives the NPC a point of view)
            GUILD, self.c, "entry", place, {npc: 1.0}, source=DM, dm_chose_secret=True
        )
        await self.memory.leave_party(GUILD, self.c, vell, "died")
        found = await self.memory.party_and_others(GUILD, self.c)
        self.assertEqual(found.party, [(tuck, "Tuck")])
        self.assertEqual(found.left, {vell: ("Vell", "died")})
        self.assertEqual(found.npcs, {npc: "Brother Ansel"})

    async def test_common_knowledge_lists_the_party_now_not_who_left(self) -> None:
        vell = await self.pc("Vell", ANNA)
        tuck = await self.pc("Tuck", BEN)
        await self.memory.leave_party(GUILD, self.c, vell, "retired")  # (before it is known)
        place = str(
            (await self.memory.add_entity(
                GUILD, self.c, name="Ten-Towns", type="place", status=CONFIRMED, source=DM
            )).value.id
        )  # fmt: skip
        await self.memory.add_common_knowledge(GUILD, self.c, "entry", [place], source=DM)
        listed = await self.memory.statement_knowers(GUILD, self.c, "entry", [place])
        self.assertEqual(listed.get(place), [tuck])  # (rule 13.1: the party, not every role)


if __name__ == "__main__":
    unittest.main()
