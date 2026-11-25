"""The website's "Names that look alike" (#5259, migration 0623): the groups are the bot's own
(the same finder), only for a DM of the campaign, never another campaign's, and a pair the DM said
is "Not the same" is left apart. Invented names throughout."""

from __future__ import annotations

from typing import Any, cast

from dmbot.memory.models import CONFIRMED
from dmbot.memory.store import MemoryStore
from dmbot.web import look_alikes, sessions
from dmbot.web.recaps import RecapError
from tests.test_recap_web import BOB, NOW, Base
from tests.test_web_api import ALICE, THURSDAY


class TheLookAlikes(Base):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.memory = MemoryStore(self.db)
        self.g = THURSDAY.id
        self.ref = f"{self.g}.{self.one.id}"

    async def name(self, name: str, kind: str = "place", *, campaign: str | None = None,
                   role: str | None = None) -> str:  # fmt: skip
        written = await self.memory.add_entity(
            self.g, campaign or self.one.id, type=kind, name=name, source="dm",
            status=CONFIRMED, role=role,
        )  # fmt: skip
        return written.value.id

    async def listed(self, who: Any = None, ref: str | None = None) -> dict[str, Any]:
        return await look_alikes.read_groups(self.web, who or self.session, ref or self.ref)

    async def test_names_one_letter_apart_are_a_group_with_their_kind_and_heard_count(self) -> None:
        a = await self.name("Belleros", "character", role="npc")
        b = await self.name("Belerros", "character", role="npc")
        await self.name("Oakmere")
        got = await self.listed()
        self.assertEqual(got["count"], 1)
        (group,) = got["groups"]
        self.assertEqual({n["id"] for n in group}, {a, b})
        self.assertEqual({n["kind"] for n in group}, {"NPC"})
        self.assertTrue(all(isinstance(n["heard"], int) for n in group))

    async def test_the_list_is_the_bots_own_groups(self) -> None:
        await self.name("Belleros", "character", role="npc")
        await self.name("Belerros", "character", role="npc")
        bot = await self.memory.look_alikes(self.g, self.one.id)
        got = await self.listed()
        self.assertEqual(
            [[n["id"] for n in group] for group in got["groups"]],
            [[n.id for n in group] for group in bot],
        )

    async def test_a_pair_said_not_to_be_the_same_is_left_apart(self) -> None:
        a = await self.name("Belleros", "character", role="npc")
        b = await self.name("Belerros", "character", role="npc")
        await self.memory.not_the_same(self.g, self.one.id, [a, b], user_id=ALICE.id)
        self.assertEqual((await self.listed())["groups"], [])

    async def test_another_campaigns_names_are_never_listed(self) -> None:
        await self.name("Belleros", "character", campaign=self.two.id)
        await self.name("Belerros", "character", campaign=self.two.id)
        self.assertEqual((await self.listed())["groups"], [])

    async def test_a_player_and_a_stranger_get_not_found(self) -> None:
        await self.name("Belleros")
        await self.name("Belerros")
        bob = await sessions.find(
            self.web,
            await sessions.sign_in(self.web, cast(Any, BOB), [THURSDAY], now=NOW, days=30),
            now=NOW,
        )
        assert bob is not None  # (Bob runs Two, and only plays in One)
        with self.assertRaises(RecapError) as refused:
            await self.listed(bob)
        self.assertEqual(refused.exception.code, "not_found")
        with self.assertRaises(RecapError):
            await self.listed(ref="nonsense")

    async def test_a_person_at_the_table_and_a_story_thread_are_not_compared(self) -> None:
        await self.name("Belleros", "character", role="npc")
        await self.name("Belerros", "quest")
        self.assertEqual((await self.listed())["groups"], [])
