"""A DM's hide masks a group's reach for player characters, as common-knowledge rows are masked
(Supervisor's ruling on #4806, split out for the website's fix #4807;
`docs/design/party_knowledge.md` §9 rule 1). A faction's who-knows row on a statement hidden from
the players or not yet confirmed never reaches its player-character members. The faction's own row
and its NPC members keep it, and what the DM gave to some (a secret connection) still reaches them.
Invented names and ids only."""

from __future__ import annotations

from dmbot.memory import gate
from dmbot.memory.models import CONFIRMED, DM
from tests.test_party_join import GUILD, Party


class AFactionsParty(Party):
    """Vell and Tuck are both of the Gray Hand; the Hand knows the vault."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.hand = await self.made("The Gray Hand", "faction")
        for pc in (self.vell, self.tuck):
            await self.memory.add_relation(
                GUILD, self.c, pc, "member_of", self.hand, source=DM, confidence=1.0,
                status=CONFIRMED,
            )  # fmt: skip
        self.vault = await self.made("The vault", "place")
        await self.memory.set_statement_knowers(
            GUILD, self.c, "entry", self.vault, [self.hand], source=DM, dm_chose_secret=True
        )

    async def made(self, name: str, kind: str) -> str:
        done = await self.memory.add_entity(
            GUILD, self.c, name=name, type=kind, status=CONFIRMED, source=DM
        )
        return str(done.value.id)

    async def sure(self, viewer: gate.Viewer, kind: str = gate.ENTRY, i: str = "") -> float | None:
        seen = await self.memory.visible_to(GUILD, self.c, viewer)
        return seen.how_sure(kind, i or self.vault)

    async def hide_the_vault(self, hidden: bool = True) -> None:
        await self.memory.set_hidden_from_players(
            GUILD, self.c, self.vault, hidden, source=DM, dm_chose_secret=True
        )

    async def test_a_hidden_statement_doesnt_reach_the_factions_members(self) -> None:
        self.assertIsNotNone(await self.sure(gate.Viewer.the_party()))  # (shown: the Hand's)
        await self.hide_the_vault()
        self.assertIsNone(await self.sure(gate.Viewer.the_party()))
        self.assertIsNone(await self.sure(gate.Viewer.player([self.vell])))
        self.assertIsNotNone(await self.sure(gate.Viewer.player([self.hand])))  # (its own row)
        await self.hide_the_vault(False)
        self.assertIsNotNone(await self.sure(gate.Viewer.the_party()))  # (shown again)

    async def test_an_npc_member_still_gets_it_and_a_pc_who_left_does_not(self) -> None:
        made = await self.memory.add_entity(
            GUILD, self.c, name="Ash", type="character", role="npc", status=CONFIRMED, source=DM
        )
        ash = str(made.value.id)
        await self.memory.add_relation(
            GUILD, self.c, ash, "member_of", self.hand, source=DM, confidence=1.0,
            status=CONFIRMED,
        )  # fmt: skip
        await self.hide_the_vault()
        self.assertIsNotNone(await self.sure(gate.Viewer.character(ash)))  # (the DM seeing as Ash)
        await self.memory.leave_party(GUILD, self.c, self.tuck, "left")
        self.assertIsNone(await self.sure(gate.Viewer.character(self.tuck)))

    async def test_a_connection_to_a_hidden_entry_doesnt_reach_them(self) -> None:
        rel = await self.memory.add_relation(
            GUILD, self.c, self.hand, "located_in", self.vault, source=DM, confidence=1.0,
            status=CONFIRMED, known_by=[self.hand],
        )  # fmt: skip
        tie = str(rel.value[0].id)
        vell = gate.Viewer.player([self.vell])
        self.assertIsNotNone(await self.sure(vell, gate.CONNECTION, tie))
        await self.hide_the_vault()
        self.assertIsNone(await self.sure(vell, gate.CONNECTION, tie))

    async def test_a_secret_the_dm_gave_the_faction_still_reaches_its_members(self) -> None:
        # (a connection given to some, a group among them, is not a hide: "Only some people know
        # this" is how the DM gives it to the faction)
        mine = await self.made("The old mine", "place")
        rel = await self.memory.add_relation(
            GUILD, self.c, self.hand, "located_in", mine, source=DM, confidence=1.0,
            status=CONFIRMED, secret=True, known_by=[self.hand],
        )  # fmt: skip
        tie = str(rel.value[0].id)
        self.assertIsNotNone(await self.sure(gate.Viewer.player([self.vell]), gate.CONNECTION, tie))
