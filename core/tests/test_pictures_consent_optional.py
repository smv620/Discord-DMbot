"""A picture that did not come from the chat records no chat-capture consent (#4372, #4408): its
consent columns are NULL, never a made-up yes. A picture from the chat still carries the poster's
own. The real stores on a test database; invented ids. Needs Postgres."""

from __future__ import annotations

import psycopg

from dmbot.campaigns import CampaignStore
from dmbot.pictures.keeper import Context, PictureKeeper
from dmbot.pictures.store import MB, PictureStore
from tests.pg import DatabaseTest
from tests.test_map_send import png

GUILD, DM, PLAYER = 1, 7, 8


class Bucket:
    async def put(self, key: str, data: bytes) -> None: ...
    async def delete(self, key: str) -> None: ...


class Optional(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.campaigns = CampaignStore(self.db)
        self.store = PictureStore(self.db, clock=lambda: 1000.0)
        self.keeper = PictureKeeper(self.store, Bucket(), 5)
        self.campaign = await self.campaigns.create(GUILD, "One", DM)

    def context(self, message_id: int, version: int | None, granted: int | None) -> Context:
        return Context(
            guild_id=GUILD, campaign_id=self.campaign.id, poster_id=PLAYER, session_id="",
            line_id=None, message_id=message_id, description="",
            consent_version=version, right_granted_at=granted,
        )  # fmt: skip

    async def consent_of(self, picture_id: int) -> tuple[int | None, int | None]:
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                "SELECT consent_version, right_granted_at FROM campaign_pictures WHERE id = %s",
                (picture_id,),
            )
            row = await cur.fetchone()
        assert row is not None
        return row["consent_version"], row["right_granted_at"]

    async def test_a_picture_sent_by_its_owner_has_no_consent_record(self) -> None:
        got = await self.keeper.keep(self.context(0, None, None), png(1))
        assert got.picture is not None
        self.assertEqual(await self.consent_of(got.picture.id), (None, None))

    async def test_a_picture_from_the_chat_keeps_the_posters_own_yes(self) -> None:
        got = await self.keeper.keep(self.context(11, 7, 900), png(2))
        assert got.picture is not None
        self.assertEqual(await self.consent_of(got.picture.id), (7, 900))

    async def test_a_chat_picture_without_the_yes_is_refused_by_the_keeper(self) -> None:
        with self.assertRaises(ValueError):
            await self.keeper.keep(self.context(11, None, None), png(3))
        with self.assertRaises(ValueError):  # (the two go together)
            await self.keeper.keep(self.context(0, 7, None), png(4))

    async def test_the_database_refuses_a_chat_picture_without_the_yes(self) -> None:
        with self.assertRaises(psycopg.errors.CheckViolation):
            await self.store.add(
                GUILD, self.campaign.id, poster_id=PLAYER, session_id="", line_id=None,
                message_id=11, description="", consent_version=None, right_granted_at=None,
                sha256=f"{5:064x}", object_key="p/5.png", preview_key="", size_bytes=MB,
                preview_bytes=0, storage_mb=5,
            )  # fmt: skip
