"""The line for the players of a name waiting to be checked, on the website (#3833): the DM's
name checks carry it, and nobody else's do. Real Postgres; invented names."""

from __future__ import annotations

from dmbot.web import players_lines
from dmbot.web.recaps import _scoped
from tests.test_scenes import Base

LINE = "The town's one-eyed smith."


class OnTheCards(Base):
    async def line(self, entry: str, text: str | None, status: str = "proposed") -> None:
        async with self.db.guild(self.g) as conn:
            await conn.execute(
                "UPDATE memory_entities SET players_text = %s, status = %s WHERE id = %s",
                (text, status, entry),
            )

    async def read(self, who, ids: list[str], campaign: str | None = None) -> dict[str, str]:  # type: ignore[no-untyped-def]
        async with self.web.user(who.user_id, session=who.id_hash) as conn:
            await _scoped(conn, self.g)
            return await players_lines.read(conn, self.g, campaign or self.one.id, ids)

    async def test_a_dm_gets_the_line_of_a_waiting_name(self) -> None:
        await self.line(self.someone, LINE)
        self.assertEqual(await self.read(self.session, [self.someone]), {self.someone: LINE})

    async def test_a_name_with_no_line_or_a_blank_one_has_no_key(self) -> None:
        await self.line(self.someone, None)
        self.assertEqual(await self.read(self.session, [self.someone]), {})
        await self.line(self.someone, "   ")
        self.assertEqual(await self.read(self.session, [self.someone]), {})

    async def test_a_name_no_longer_waiting_is_not_read(self) -> None:
        await self.line(self.someone, LINE, status="confirmed")
        self.assertEqual(await self.read(self.session, [self.someone]), {})

    async def test_a_member_who_is_not_a_dm_is_given_nothing(self) -> None:
        await self.line(self.someone, LINE)
        self.assertEqual(await self.read(self.player, [self.someone]), {})

    async def test_another_campaign_is_never_read(self) -> None:
        await self.line(self.someone, LINE)
        other = (await self.campaigns.create(self.g, "Elsewhere", 7)).id
        self.assertEqual(await self.read(self.session, [self.someone], other), {})

    async def test_attach_gives_only_the_cards_that_have_a_line(self) -> None:
        await self.line(self.someone, LINE)
        cards = [{"id": self.someone}, {"id": self.mira}]
        async with self.web.user(self.session.user_id, session=self.session.id_hash) as conn:
            await _scoped(conn, self.g)
            await players_lines.attach(conn, self.g, self.one.id, cards)
        self.assertEqual(cards[0].get("playersText"), LINE)
        self.assertNotIn("playersText", cards[1])
