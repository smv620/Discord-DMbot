"""Deleting an account and the maps (#3274, #4344, migration 0480): the maps the person uploaded go
with the change-log rows that name them; the maps they answered "use it?" for stay, with nobody as
the DM who said so and DMbot's use of them unchanged; nobody else's map changes. Needs Postgres."""

from __future__ import annotations

import time
import uuid
from typing import Any

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


class MapsBase(DatabaseTest):
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


class ForgetMaps(MapsBase):
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

    async def test_the_log_rows_of_a_map_already_removed_go_too(self) -> None:
        mine = await self.a_map(ALICE, dm=False)  # (removed by the DM before she leaves)
        await self.maps.remove_map(GUILD, self.campaign, mine)
        gone_with_picture = await self.a_map(ALICE, dm=False)
        picture = await self.maps.get(GUILD, self.campaign, gone_with_picture, Viewer(dm=True))
        assert picture is not None
        await self.pictures.delete(GUILD, self.campaign, picture.picture_id)
        self.assertEqual(await self.rows(), {})  # (no map row is left to find them through)
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                "SELECT count(*) AS n FROM memory_changes WHERE table_name = 'campaign_maps'"
            )
            before = await cur.fetchone()
        assert before is not None
        self.assertGreater(int(before["n"]), 0)  # (the removed map's log rows are still there)
        await accounts.delete_person(self.db, ALICE, now=NOW)
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                "SELECT count(*) AS n FROM memory_changes WHERE table_name = 'campaign_maps'"
                " AND (before::text LIKE %s OR after::text LIKE %s)",
                (f'%"right_by": {ALICE}%', f'%"right_by": {ALICE}%'),
            )
            left = await cur.fetchone()
        assert left is not None
        self.assertEqual(int(left["n"]), 0)

    async def test_it_reaches_every_campaign_and_server_and_only_this_persons_maps(self) -> None:
        other = (await CampaignStore(self.db).create(GUILD, "Two", CAL)).id
        elsewhere = (await CampaignStore(self.db).create(222, "Far", CAL)).id
        mine = await self.a_map(ALICE, dm=False)
        bobs = await self.a_map(BOB, dm=False)
        for campaign in (other,):
            self.campaign = campaign
            await self.a_map(ALICE, dm=False)
        del elsewhere  # (a second server needs its own member rows: covered by the one sweep)
        await accounts.delete_person(self.db, ALICE, now=NOW)
        self.assertEqual({r[0] for r in (await self.rows()).values()}, {BOB})
        self.assertIn(bobs, await self.rows())
        self.assertNotIn(mine, await self.rows())


class ForgetMapReads(MapsBase):
    """Migration 0534 (#4693): what DMbot read from a departing person's maps follows the deletion
    rule of a removed map (design 7.5): its own read goes, a read the text also stated becomes the
    text's, the DM's word and the DM's no stay without the pointer."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        memory = MemoryStore(self.db)
        placed = await memory.add_entity(
            GUILD, self.campaign, type="place", name="Targos", source="dm", status="confirmed"
        )
        self.place = placed.value.id

    async def fact(self, map_id: str, value: str, *, status: str, source: str, text: bool) -> str:
        fact_id = uuid.uuid4().hex
        async with self.db.guild(GUILD) as conn:
            await conn.execute(
                "INSERT INTO memory_map_facts (guild_id, campaign_id, id, entity_id, layer, value,"
                " status, source, also_text, created_at, map_id)"
                " VALUES (%s, %s, %s, %s, 'terrain', %s, %s, %s, %s, %s, %s)",
                (GUILD, self.campaign, fact_id, self.place, value, status, source, text,
                 int(time.time()), map_id),
            )  # fmt: skip
        return fact_id

    async def row(self, fact_id: str) -> dict[str, Any] | None:
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                "SELECT status, source, also_text, map_id FROM memory_map_facts WHERE id = %s",
                (fact_id,),
            )
            got = await cur.fetchone()
        return dict(got) if got is not None else None

    async def test_each_kind_of_row_follows_the_rule(self) -> None:
        mine = await self.a_map(ALICE, dm=False)
        only_map = await self.fact(mine, "arctic", status="read", source="map", text=False)
        text_too = await self.fact(mine, "mountain", status="read", source="map", text=True)
        dms_word = await self.fact(mine, "forest", status="confirmed", source="map", text=False)
        dms_no = await self.fact(mine, "swamp", status="rejected", source="map", text=False)
        await accounts.delete_person(self.db, ALICE, now=NOW)
        self.assertIsNone(await self.row(only_map))  # (rested only on the map: gone)
        self.assertEqual(
            await self.row(text_too),
            {"status": "read", "source": "text", "also_text": False, "map_id": None},
        )
        self.assertEqual(
            await self.row(dms_word),
            {"status": "confirmed", "source": "map", "also_text": False, "map_id": None},
        )
        self.assertEqual(
            await self.row(dms_no),
            {"status": "rejected", "source": "map", "also_text": False, "map_id": None},
        )

    async def test_a_pin_on_a_deleted_read_loses_only_its_pointer(self) -> None:
        mine = await self.a_map(ALICE, dm=False)
        bobs = await self.a_map(BOB, dm=False)
        gone = await self.fact(mine, "arctic", status="read", source="map", text=False)
        feature = uuid.uuid4().hex
        async with self.db.guild(GUILD) as conn:  # (a pin on Bob's map that pointed at the read)
            await conn.execute(
                "INSERT INTO map_features (guild_id, campaign_id, id, map_id, shape, points,"
                " fact_id, status, source, created_at) VALUES (%s, %s, %s, %s, 'area',"
                " ARRAY[0.1, 0.1, 0.2, 0.1, 0.2, 0.2], %s, 'read', 'read', %s)",
                (GUILD, self.campaign, feature, bobs, gone, int(time.time())),
            )
        await accounts.delete_person(self.db, ALICE, now=NOW)
        self.assertIsNone(await self.row(gone))
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute("SELECT fact_id FROM map_features WHERE id = %s", (feature,))
            pin = await cur.fetchone()
        assert pin is not None
        self.assertIsNone(pin["fact_id"])

    async def test_nobody_elses_reads_change(self) -> None:
        mine = await self.a_map(ALICE, dm=False)
        bobs = await self.a_map(BOB, dm=False)
        await self.fact(mine, "arctic", status="read", source="map", text=False)
        theirs = await self.fact(bobs, "glacier", status="read", source="map", text=False)
        theirs_text = await self.fact(bobs, "plain", status="read", source="map", text=True)
        await accounts.delete_person(self.db, ALICE, now=NOW)
        self.assertEqual(
            await self.row(theirs),
            {"status": "read", "source": "map", "also_text": False, "map_id": bobs},
        )
        self.assertEqual(
            await self.row(theirs_text),
            {"status": "read", "source": "map", "also_text": True, "map_id": bobs},
        )

    async def test_the_dm_who_only_answered_keeps_the_map_and_its_reads(self) -> None:
        mid = await self.a_map(BOB, dm=False)
        await self.maps.answer_use(GUILD, self.campaign, mid, dm_id=ALICE, yes=True)
        kept = await self.fact(mid, "arctic", status="read", source="map", text=False)
        await accounts.delete_person(self.db, ALICE, now=NOW)
        self.assertEqual(
            await self.row(kept),
            {"status": "read", "source": "map", "also_text": False, "map_id": mid},
        )

    # ---- the memory version moves once for each campaign whose facts changed (#4711, 0536) ----

    async def version(self, campaign: str) -> int:
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                "SELECT memory_version FROM campaigns WHERE id = %s", (campaign,)
            )
            row = await cur.fetchone()
        assert row is not None
        return int(row["memory_version"])

    async def test_it_moves_once_however_many_rows_changed(self) -> None:
        mine = await self.a_map(ALICE, dm=False)
        await self.fact(mine, "arctic", status="read", source="map", text=False)
        await self.fact(mine, "mountain", status="read", source="map", text=True)
        await self.fact(mine, "forest", status="confirmed", source="map", text=False)
        before = await self.version(self.campaign)
        await accounts.delete_person(self.db, ALICE, now=NOW)
        self.assertEqual(await self.version(self.campaign), before + 1)

    async def test_a_campaign_whose_facts_did_not_change_keeps_its_number(self) -> None:
        await self.a_map(ALICE, dm=False)  # (no facts rest on it)
        bobs = await self.a_map(BOB, dm=False)
        await self.fact(bobs, "arctic", status="read", source="map", text=False)
        before = await self.version(self.campaign)
        await accounts.delete_person(self.db, ALICE, now=NOW)
        self.assertEqual(await self.version(self.campaign), before)

    async def test_every_campaign_with_changed_facts_moves_and_nobody_elses(self) -> None:
        other = (await CampaignStore(self.db).create(GUILD, "Two", CAL)).id
        untouched = (await CampaignStore(self.db).create(GUILD, "Three", CAL)).id
        first = await self.a_map(ALICE, dm=False)
        await self.fact(first, "arctic", status="read", source="map", text=False)
        self.campaign = other
        placed = await MemoryStore(self.db).add_entity(
            GUILD, other, type="place", name="Neverwinter", source="dm", status="confirmed"
        )
        self.place = placed.value.id
        second = await self.a_map(ALICE, dm=False)
        await self.fact(second, "forest", status="read", source="map", text=False)
        moved = {c: await self.version(c) for c in (other, untouched)}
        first_before = await self.version(await self.first_campaign())
        await accounts.delete_person(self.db, ALICE, now=NOW)
        self.assertEqual(await self.version(other), moved[other] + 1)
        self.assertEqual(await self.version(untouched), moved[untouched])
        self.assertEqual(await self.version(await self.first_campaign()), first_before + 1)

    async def first_campaign(self) -> str:
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                "SELECT id FROM campaigns WHERE name = 'One' AND guild_id = %s", (GUILD,)
            )
            row = await cur.fetchone()
        assert row is not None
        return str(row["id"])

    # ---- a fact another usable map still shows is kept, on the oldest such map (#4696) ----

    async def pin(self, map_id: str, fact_id: str, *, status: str = "read") -> None:
        async with self.db.guild(GUILD) as conn:
            await conn.execute(
                "INSERT INTO map_features (guild_id, campaign_id, id, map_id, shape, points,"
                " fact_id, status, source, created_at) VALUES (%s, %s, %s, %s, 'area',"
                " ARRAY[0.1, 0.1, 0.2, 0.1, 0.2, 0.2], %s, %s, 'read', %s)",
                (GUILD, self.campaign, uuid.uuid4().hex, map_id, fact_id, status, int(time.time())),
            )  # fmt: skip

    async def test_a_fact_another_usable_map_shows_is_kept_on_that_map(self) -> None:
        mine = await self.a_map(ALICE, dm=False)
        bobs = await self.a_map(BOB, dm=True)  # (usable: its DM said yes)
        fact = await self.fact(mine, "arctic", status="read", source="map", text=False)
        await self.pin(bobs, fact)
        before = await self.version(self.campaign)
        await accounts.delete_person(self.db, ALICE, now=NOW)
        self.assertEqual(
            await self.row(fact),
            {"status": "read", "source": "map", "also_text": False, "map_id": bobs},
        )
        self.assertEqual(await self.version(self.campaign), before + 1)  # (and it moved once)

    async def test_the_oldest_such_map_keeps_it(self) -> None:
        mine = await self.a_map(ALICE, dm=False)
        older = await self.a_map(BOB, dm=True)
        newer = await self.a_map(CAL, dm=True)
        fact = await self.fact(mine, "arctic", status="read", source="map", text=False)
        await self.pin(newer, fact)
        await self.pin(older, fact)
        await accounts.delete_person(self.db, ALICE, now=NOW)
        kept = await self.row(fact)
        assert kept is not None
        self.assertEqual(kept["map_id"], older)

    async def test_an_unusable_map_or_a_turned_down_area_keeps_nothing(self) -> None:
        mine = await self.a_map(ALICE, dm=False)
        waiting = await self.a_map(BOB, dm=False)  # (no DM yes yet: not usable)
        usable = await self.a_map(CAL, dm=True)
        on_waiting = await self.fact(mine, "arctic", status="read", source="map", text=False)
        turned_down = await self.fact(mine, "forest", status="read", source="map", text=False)
        await self.pin(waiting, on_waiting)
        await self.pin(usable, turned_down, status="rejected")
        await accounts.delete_person(self.db, ALICE, now=NOW)
        self.assertIsNone(await self.row(on_waiting))
        self.assertIsNone(await self.row(turned_down))
