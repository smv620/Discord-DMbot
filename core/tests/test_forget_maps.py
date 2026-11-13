"""Deleting an account and the maps (#3274, #4344, migration 0480): the maps the person uploaded go
with the change-log rows that name them; the maps they answered "use it?" for stay, with nobody as
the DM who said so and DMbot's use of them unchanged; nobody else's map changes. Needs Postgres."""

from __future__ import annotations

from dmbot import entitlements
from dmbot.campaigns import CampaignStore
from dmbot.maps.store import MapStore, Viewer
from dmbot.memory.store import MemoryStore
from dmbot.pictures.store import MB, PictureStore
from dmbot.web import accounts
from tests.pg import DatabaseTest

ALICE, BOB, CAL = 7001, 7002, 7003
GUILD = 111
NOW = 1_800_000_000


class ForgetMaps(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.addCleanup(entitlements.configure_free_users, ())
        for user in (ALICE, BOB, CAL):
            async with self.db.user(user) as conn:
                await conn.execute(
                    "INSERT INTO web_users (user_id, email, created_at, last_sign_in_at)"
                    " VALUES (%s, NULL, 0, 0)",
                    (user,),
                )
        self.campaign = (await CampaignStore(self.db).create(GUILD, "One", CAL)).id
        self.pictures = PictureStore(self.db, clock=lambda: 1000.0)
        self.maps = MapStore(self.db, MemoryStore(self.db), clock=lambda: 2000.0)
        self.n = 0

    async def a_map(self, uploader: int, *, dm: bool) -> str:
        self.n += 1
        added = await self.pictures.add(
            GUILD, self.campaign, poster_id=uploader, session_id="", line_id=None,
            message_id=self.n, description="", consent_version=7, right_granted_at=900,
            sha256=f"{self.n:064x}", object_key=f"k{self.n}", preview_key="",
            size_bytes=MB, preview_bytes=0, storage_mb=50,
        )  # fmt: skip
        assert added.picture is not None
        made = await self.maps.add_map(
            GUILD, self.campaign, picture_id=added.picture.id, uploader_id=uploader,
            uploader_is_dm=dm, right_to_use=True, kind="region", came_from="website",
            storage_mb=50,
        )  # fmt: skip
        return made.value.id

    async def rows(self) -> dict[str, tuple[object, object, object]]:
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute("SELECT id, right_by, use_ok, use_by FROM campaign_maps")
            return {
                str(r["id"]): (r["right_by"], r["use_ok"], r["use_by"])
                for r in await cur.fetchall()
            }

    async def log_rows(self, map_id: str) -> int:
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                "SELECT count(*) AS n FROM memory_changes"
                " WHERE table_name = 'campaign_maps' AND row_id = %s",
                (map_id,),
            )
            row = await cur.fetchone()
        assert row is not None
        return int(row["n"])

    async def test_the_uploaders_maps_and_their_log_rows_go(self) -> None:
        mine = await self.a_map(ALICE, dm=False)
        bobs = await self.a_map(BOB, dm=False)
        self.assertGreater(await self.log_rows(mine), 0)
        await accounts.delete_person(self.db, ALICE, now=NOW)
        self.assertEqual(set(await self.rows()), {bobs})
        self.assertEqual(await self.log_rows(mine), 0)
        self.assertGreater(await self.log_rows(bobs), 0)  # (nobody else's history is touched)

    async def test_a_dm_who_answered_is_cleared_and_the_map_stays_in_use(self) -> None:
        mid = await self.a_map(BOB, dm=False)
        await self.maps.answer_use(GUILD, self.campaign, mid, dm_id=ALICE, yes=True)
        await accounts.delete_person(self.db, ALICE, now=NOW)
        self.assertEqual((await self.rows())[mid], (BOB, True, 0))  # use_by 0: someone who left
        self.assertEqual(await self.log_rows(mid), 0)  # (its history named the DM)
        shown = await self.maps.maps(GUILD, self.campaign, Viewer(dm=True), usable_only=True)
        self.assertEqual([m.id for m in shown], [mid])  # DMbot may still use it

    async def test_a_dms_own_map_goes_and_nobody_elses_is_changed(self) -> None:
        mine = await self.a_map(ALICE, dm=True)  # (uploaded and answered by the same person)
        bobs = await self.a_map(BOB, dm=True)
        await accounts.delete_person(self.db, ALICE, now=NOW)
        self.assertEqual(await self.rows(), {bobs: (BOB, True, BOB)})
        self.assertNotIn(mine, await self.rows())
