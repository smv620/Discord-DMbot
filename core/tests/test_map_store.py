"""Maps kept as maps (#4344, MapBot layers slice 3a): a map exists only after its uploader's yes,
nothing is used before a DM's yes, the one `may_use_map` answers, the audience rules, a picture
kept permanent while it is a map, a deleted picture taking its map, and isolation by campaign and
server. Invented ids only. Needs Postgres."""

from __future__ import annotations

from dmbot.campaigns import CampaignStore
from dmbot.maps.store import MapError, MapStore, Viewer, may_use_map
from dmbot.memory.store import MemoryStore
from dmbot.pictures.store import MB, PictureStore
from tests.pg import DatabaseTest

GUILD_A, GUILD_B = 1, 2
DM, PLAYER, OTHER_DM = 7, 8, 9
DM_VIEW = Viewer(dm=True)
TABLE_VIEW = Viewer(table=True)
NOBODY = Viewer()


def sha(n: int) -> str:
    return f"{n:064x}"


class Maps(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.campaigns = CampaignStore(self.db)
        self.pictures = PictureStore(self.db, clock=lambda: 1000.0)
        self.memory = MemoryStore(self.db)
        self.maps = MapStore(self.db, self.memory, clock=lambda: 2000.0)
        self.one = await self.campaigns.create(GUILD_A, "One", DM)
        self.two = await self.campaigns.create(GUILD_A, "Two", DM)
        self.far = await self.campaigns.create(GUILD_B, "Far", DM)

    async def picture(self, campaign: str, n: int, mb: int = 1, guild: int = GUILD_A) -> int:
        added = await self.pictures.add(
            guild, campaign, poster_id=PLAYER, session_id="", line_id=None, message_id=n,
            description="", consent_version=7, right_granted_at=900, sha256=sha(n),
            object_key=f"{guild}/{campaign}/{sha(n)}.png", preview_key="",
            size_bytes=mb * MB, preview_bytes=0, storage_mb=10,
        )  # fmt: skip
        assert added.picture is not None
        return added.picture.id

    async def add(self, pic: int, *, dm: bool, campaign: str | None = None, **more: object) -> str:
        made = await self.maps.add_map(
            GUILD_A, campaign or self.one.id, picture_id=pic,
            uploader_id=DM if dm else PLAYER, uploader_is_dm=dm, right_to_use=True,
            kind="region", came_from=str(more.pop("came_from", "website")), storage_mb=10,
            **more,  # type: ignore[arg-type]
        )  # fmt: skip
        return made.value.id

    async def test_nothing_is_kept_as_a_map_without_the_uploaders_yes(self) -> None:
        pic = await self.picture(self.one.id, 1)
        with self.assertRaises(MapError):
            await self.maps.add_map(
                GUILD_A, self.one.id, picture_id=pic, uploader_id=PLAYER, uploader_is_dm=False,
                right_to_use=False, kind="region", came_from="website", storage_mb=10,
            )  # fmt: skip
        self.assertEqual(await self.maps.maps(GUILD_A, self.one.id, DM_VIEW), [])

    async def test_a_dms_own_upload_has_both_yeses_and_may_be_used(self) -> None:
        mid = await self.add(await self.picture(self.one.id, 1), dm=True)
        got = await self.maps.get(GUILD_A, self.one.id, mid, DM_VIEW)
        assert got is not None
        self.assertEqual((got.right_by, got.use_ok, got.use_by), (DM, True, DM))
        self.assertTrue(may_use_map(got))

    async def test_a_players_map_is_kept_but_waits_for_a_dm_and_is_used_by_nobody(self) -> None:
        mid = await self.add(await self.picture(self.one.id, 1), dm=False)
        got = await self.maps.get(GUILD_A, self.one.id, mid, DM_VIEW)
        assert got is not None
        self.assertEqual((got.right_by, got.use_ok), (PLAYER, None))
        self.assertTrue(got.waiting)
        self.assertFalse(may_use_map(got))
        self.assertEqual(await self.maps.maps(GUILD_A, self.one.id, DM_VIEW, usable_only=True), [])
        self.assertEqual(
            [m.id for m in await self.maps.waiting_for_a_dm(GUILD_A, self.one.id)], [mid]
        )

    async def test_a_dms_yes_lets_dmbot_use_it_and_a_no_leaves_it_kept_and_unused(self) -> None:
        pic = await self.picture(self.one.id, 1)
        mid = await self.add(pic, dm=False)
        yes = await self.maps.answer_use(GUILD_A, self.one.id, mid, dm_id=DM, yes=True)
        self.assertTrue(may_use_map(yes.value))
        self.assertEqual((yes.value.use_by, yes.value.use_at), (DM, 2000))
        self.assertEqual(
            [m.id for m in await self.maps.maps(GUILD_A, self.one.id, DM_VIEW, usable_only=True)],
            [mid],
        )
        no = await self.maps.answer_use(GUILD_A, self.one.id, mid, dm_id=OTHER_DM, yes=False)
        self.assertFalse(may_use_map(no.value))
        self.assertFalse(no.value.waiting)  # (answered: it leaves "Questions for you")
        self.assertEqual(await self.maps.waiting_for_a_dm(GUILD_A, self.one.id), [])
        self.assertIsNotNone(await self.pictures.get(GUILD_A, self.one.id, pic))  # still kept

    async def test_an_answer_for_a_map_that_is_not_here_is_refused(self) -> None:
        with self.assertRaises(MapError):
            await self.maps.answer_use(GUILD_A, self.one.id, "0" * 32, dm_id=DM, yes=True)

    async def test_only_a_map_from_the_tables_chat_starts_shown_to_the_table(self) -> None:
        chat = await self.add(await self.picture(self.one.id, 1), dm=False, came_from="discord",
                              audience="table")  # fmt: skip
        private = await self.add(await self.picture(self.one.id, 2), dm=True)
        await self.maps.answer_use(GUILD_A, self.one.id, chat, dm_id=DM, yes=True)
        seen = {m.id for m in await self.maps.maps(GUILD_A, self.one.id, TABLE_VIEW)}
        self.assertEqual(seen, {chat})
        self.assertEqual(
            {m.id for m in await self.maps.maps(GUILD_A, self.one.id, DM_VIEW)}, {chat, private}
        )
        self.assertEqual(await self.maps.maps(GUILD_A, self.one.id, NOBODY), [])
        self.assertIsNone(await self.maps.get(GUILD_A, self.one.id, private, TABLE_VIEW))
        with self.assertRaises(MapError):  # a website map is never born shown to the table
            await self.add(await self.picture(self.one.id, 3), dm=True, audience="table")

    async def test_a_dm_may_show_any_map_to_the_table_but_none_is_born_shown(self) -> None:
        web = await self.add(await self.picture(self.one.id, 1), dm=True)
        shown = await self.maps.set_audience(GUILD_A, self.one.id, web, "table")
        self.assertEqual(shown.value.audience, "table")
        self.assertEqual(
            [m.id for m in await self.maps.maps(GUILD_A, self.one.id, TABLE_VIEW)], [web]
        )
        back = await self.maps.set_audience(GUILD_A, self.one.id, web, "dm")
        self.assertEqual(back.value.audience, "dm")
        with self.assertRaises(MapError):
            await self.maps.set_audience(GUILD_A, self.one.id, web, "everyone")

    async def test_the_table_sees_only_the_maps_dmbot_may_use(self) -> None:
        waiting = await self.add(await self.picture(self.one.id, 1), dm=False, came_from="discord",
                                 audience="table")  # fmt: skip
        self.assertEqual(await self.maps.maps(GUILD_A, self.one.id, TABLE_VIEW), [])
        await self.maps.answer_use(GUILD_A, self.one.id, waiting, dm_id=DM, yes=True)
        self.assertEqual([m.id for m in await self.maps.maps(GUILD_A, self.one.id, TABLE_VIEW)],
                         [waiting])  # fmt: skip
        await self.maps.answer_use(GUILD_A, self.one.id, waiting, dm_id=DM, yes=False)
        self.assertEqual(await self.maps.maps(GUILD_A, self.one.id, TABLE_VIEW), [])

    async def test_a_maps_picture_cannot_be_unmarked_while_it_is_a_map(self) -> None:
        pic = await self.picture(self.one.id, 1)
        await self.add(pic, dm=True)
        said = await self.pictures.set_permanent(GUILD_A, self.one.id, pic, False, 10)
        self.assertIn("Remove the map first", said or "")
        kept = await self.pictures.get(GUILD_A, self.one.id, pic)
        assert kept is not None
        self.assertTrue(kept.permanent)

    async def test_a_bad_size_or_a_place_that_is_not_here_is_refused_in_words(self) -> None:
        pic = await self.picture(self.one.id, 1)
        for more in ({"width": 0}, {"height": -5}, {"covers": "f" * 32}):
            with self.assertRaises(MapError, msg=str(more)):
                await self.add(pic, dm=True, **more)

    async def test_the_picture_is_permanent_while_it_is_a_map_and_not_after(self) -> None:
        pic = await self.picture(self.one.id, 1)
        before = await self.pictures.get(GUILD_A, self.one.id, pic)
        assert before is not None
        self.assertFalse(before.permanent)
        mid = await self.add(pic, dm=True)
        during = await self.pictures.get(GUILD_A, self.one.id, pic)
        assert during is not None
        self.assertTrue(during.permanent)
        gone = await self.maps.remove_map(GUILD_A, self.one.id, mid)
        assert gone.value is not None
        after = await self.pictures.get(GUILD_A, self.one.id, pic)
        assert after is not None
        self.assertFalse(after.permanent)
        self.assertIsNone(await self.maps.get(GUILD_A, self.one.id, mid, DM_VIEW))
        self.assertIsNone((await self.maps.remove_map(GUILD_A, self.one.id, mid)).value)

    async def test_a_map_is_refused_when_permanent_pictures_are_full(self) -> None:
        for n in (1, 2, 3, 4, 5, 6, 7, 8):  # 8 MB of 10 is the 80% share
            await self.add(await self.picture(self.one.id, n), dm=True)
        with self.assertRaises(MapError):
            await self.add(await self.picture(self.one.id, 9), dm=True)

    async def test_a_picture_is_one_map_at_most(self) -> None:
        pic = await self.picture(self.one.id, 1)
        await self.add(pic, dm=True)
        with self.assertRaises(MapError):
            await self.add(pic, dm=True)

    async def test_deleting_the_picture_takes_its_map_with_it(self) -> None:
        pic = await self.picture(self.one.id, 1)
        await self.add(pic, dm=False)
        await self.pictures.delete(GUILD_A, self.one.id, pic)
        self.assertEqual(await self.maps.maps(GUILD_A, self.one.id, DM_VIEW), [])

    async def test_a_map_cannot_point_at_another_campaigns_picture(self) -> None:
        pic = await self.picture(self.two.id, 1)
        with self.assertRaises(MapError):
            await self.add(pic, dm=True, campaign=self.one.id)

    async def test_another_campaign_or_server_never_sees_it(self) -> None:
        mid = await self.add(await self.picture(self.one.id, 1), dm=True)
        self.assertEqual(await self.maps.maps(GUILD_A, self.two.id, DM_VIEW), [])
        self.assertEqual(await self.maps.maps(GUILD_B, self.far.id, DM_VIEW), [])
        self.assertIsNone(await self.maps.get(GUILD_A, self.two.id, mid, DM_VIEW))
        self.assertIsNone((await self.maps.remove_map(GUILD_A, self.two.id, mid)).value)
        with self.assertRaises(MapError):
            await self.maps.answer_use(GUILD_A, self.two.id, mid, dm_id=DM, yes=False)

    async def test_deleting_the_campaign_deletes_its_maps(self) -> None:
        await self.add(await self.picture(self.one.id, 1), dm=True)
        await self.campaigns.delete(GUILD_A, self.one.id)
        async with self.db.unscoped() as conn:
            cur = await conn.execute("SELECT count(*) AS n FROM campaign_maps")
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual(row["n"], 0)

    async def test_the_words_are_checked_in_the_store(self) -> None:
        pic = await self.picture(self.one.id, 1)
        with self.assertRaises(MapError):
            await self.add(pic, dm=True, came_from="elsewhere")
        with self.assertRaises(MapError):
            await self.maps.add_map(
                GUILD_A, self.one.id, picture_id=pic, uploader_id=DM, uploader_is_dm=True,
                right_to_use=True, kind="castle", came_from="website", storage_mb=10,
            )  # fmt: skip
        with self.assertRaises(MapError):  # an import names its tool, and nothing else does
            await self.add(pic, dm=True, came_from="import")

    # A map added on the website goes through the same keeper (design section 3.1), with the
    # uploader's own yes as its right to use. The consent-v7 check stays on what the bot captures
    # in the table's chat (`test_table_images`): the keeper takes the context its caller proves.
    async def test_a_website_upload_is_kept_by_the_keeper_and_becomes_a_map(self) -> None:
        from dmbot.consent import IMAGE_TERMS_VERSION
        from dmbot.pictures.keeper import Context, PictureKeeper
        from tests.test_pictures_keeper import FakeBucket, picture

        bucket = FakeBucket()
        keeper = PictureKeeper(self.pictures, bucket, 10)
        got = await keeper.keep(
            Context(
                GUILD_A,
                self.one.id,
                PLAYER,
                "",
                None,
                0,
                "",
                IMAGE_TERMS_VERSION,
                right_granted_at=1500,
            ),
            picture(),
        )
        assert got.picture is not None
        self.assertEqual(got.picture.message_id, 0)  # (no Discord message: it came from the site)
        mid = await self.add(got.picture.id, dm=False, came_from="website")
        kept = await self.maps.get(GUILD_A, self.one.id, mid, DM_VIEW)
        assert kept is not None
        self.assertEqual((kept.came_from, kept.audience, kept.waiting), ("website", "dm", True))

    async def pinned(self, pic: int) -> bool:
        kept = await self.pictures.get(GUILD_A, self.one.id, pic)
        assert kept is not None
        return kept.permanent

    async def test_every_write_is_one_undo(self) -> None:
        pic = await self.picture(self.one.id, 1)
        made = await self.maps.add_map(
            GUILD_A, self.one.id, picture_id=pic, uploader_id=PLAYER, uploader_is_dm=False,
            right_to_use=True, kind="region", came_from="discord", audience="table",
            storage_mb=10,
        )  # fmt: skip
        mid = made.value.id
        self.assertIsNotNone(made.batch)
        answered = await self.maps.answer_use(GUILD_A, self.one.id, mid, dm_id=DM, yes=True)
        shown = await self.maps.set_audience(GUILD_A, self.one.id, mid, "dm")

        async def now() -> object:
            return await self.maps.get(GUILD_A, self.one.id, mid, DM_VIEW)

        assert shown.batch is not None and answered.batch is not None and made.batch is not None
        await self.memory.undo(GUILD_A, self.one.id, shown.batch)
        got = await now()
        assert got is not None
        self.assertEqual(got.audience, "table")  # type: ignore[attr-defined]
        await self.memory.undo(GUILD_A, self.one.id, answered.batch)
        got = await now()
        assert got is not None
        self.assertTrue(got.waiting)  # type: ignore[attr-defined]
        removed = await self.maps.remove_map(GUILD_A, self.one.id, mid)
        assert removed.batch is not None
        self.assertFalse(await self.pinned(pic))
        await self.memory.undo(GUILD_A, self.one.id, removed.batch)  # the map and its pin return
        self.assertIsNotNone(await now())
        self.assertTrue(await self.pinned(pic))
        await self.memory.undo(GUILD_A, self.one.id, made.batch)  # (the add: gone, unpinned)
        self.assertIsNone(await now())
        self.assertFalse(await self.pinned(pic))

    async def test_a_removal_of_a_map_that_is_not_here_writes_nothing(self) -> None:
        gone = await self.maps.remove_map(GUILD_A, self.one.id, "0" * 32)
        self.assertEqual((gone.value, gone.batch), (None, None))

    async def test_deleting_the_picture_leaves_no_change_log_rows_of_its_map(self) -> None:
        pic = await self.picture(self.one.id, 1)
        mid = await self.add(pic, dm=True)
        await self.maps.set_audience(GUILD_A, self.one.id, mid, "table")

        async def rows() -> int:
            async with self.db.guild(GUILD_A) as conn:
                cur = await conn.execute(
                    "SELECT count(*) AS n FROM memory_changes WHERE table_name = 'campaign_maps'"
                )
                row = await cur.fetchone()
            assert row is not None
            return int(row["n"])

        self.assertGreater(await rows(), 0)
        await self.pictures.delete(GUILD_A, self.one.id, pic)
        self.assertEqual(await rows(), 0)

    async def test_the_waiting_list_holds_only_unanswered_maps_of_this_campaign(self) -> None:
        mine = await self.add(await self.picture(self.one.id, 1), dm=False)
        theirs = await self.add(await self.picture(self.two.id, 2), dm=False, campaign=self.two.id)
        self.assertEqual(
            [m.id for m in await self.maps.waiting_for_a_dm(GUILD_A, self.one.id)], [mine]
        )
        await self.maps.answer_use(GUILD_A, self.one.id, mine, dm_id=DM, yes=False)
        self.assertEqual(await self.maps.waiting_for_a_dm(GUILD_A, self.one.id), [])
        self.assertEqual(
            [m.id for m in await self.maps.waiting_for_a_dm(GUILD_A, self.two.id)], [theirs]
        )

    async def test_the_poster_takes_their_map_back_and_the_batch_is_theirs_alone(self) -> None:
        pic = await self.picture(self.one.id, 1)
        mid = await self.add(pic, dm=False)
        with self.assertRaises(MapError):  # (a DM or anyone else is refused)
            await self.maps.take_back(GUILD_A, self.one.id, pic, DM)
        self.assertEqual(len(await self.maps.maps(GUILD_A, self.one.id, DM_VIEW)), 1)
        back = await self.maps.take_back(GUILD_A, self.one.id, pic, PLAYER)
        assert back.value is not None and back.batch is not None
        self.assertEqual(back.value.id, mid)
        self.assertEqual(await self.maps.maps(GUILD_A, self.one.id, DM_VIEW), [])
        kept = await self.pictures.by_id(GUILD_A, pic)
        assert kept is not None
        self.assertFalse(kept.gone)  # (the picture stays a picture)
        taken = self.pictures.taken_back_by
        self.assertTrue(await taken(GUILD_A, self.one.id, back.batch, PLAYER))
        self.assertFalse(await taken(GUILD_A, self.one.id, back.batch, DM))
        self.assertFalse(await taken(GUILD_A, self.two.id, back.batch, PLAYER))
        # nothing left to take back: no change, no batch
        again = await self.maps.take_back(GUILD_A, self.one.id, pic, PLAYER)
        self.assertEqual((again.value, again.batch), (None, None))

    async def test_a_dms_removal_is_not_a_take_back_batch(self) -> None:
        pic = await self.picture(self.one.id, 1)
        mid = await self.add(pic, dm=False)
        gone = await self.maps.remove_map(GUILD_A, self.one.id, mid)
        assert gone.batch is not None
        self.assertFalse(
            await self.pictures.taken_back_by(GUILD_A, self.one.id, gone.batch, PLAYER)
        )

    async def test_a_player_viewer_never_sees_a_dm_only_map_even_when_it_is_usable(self) -> None:
        mid = await self.add(await self.picture(self.one.id, 1), dm=True)
        got = await self.maps.get(GUILD_A, self.one.id, mid, DM_VIEW)
        assert got is not None
        self.assertTrue(may_use_map(got))
        self.assertEqual(
            await self.maps.maps(GUILD_A, self.one.id, TABLE_VIEW, usable_only=True), []
        )
        self.assertIsNone(await self.maps.get(GUILD_A, self.one.id, mid, TABLE_VIEW))
        await self.maps.set_audience(GUILD_A, self.one.id, mid, "table")
        self.assertEqual(
            [
                m.id
                for m in await self.maps.maps(GUILD_A, self.one.id, TABLE_VIEW, usable_only=True)
            ],
            [mid],
        )
        self.assertIsNotNone(await self.maps.get(GUILD_A, self.one.id, mid, TABLE_VIEW))

    async def test_a_removed_map_can_be_added_again_as_a_new_map(self) -> None:
        pic = await self.picture(self.one.id, 1)
        first = await self.add(pic, dm=True)
        await self.maps.remove_map(GUILD_A, self.one.id, first)
        second = await self.add(pic, dm=True)
        self.assertNotEqual(first, second)
        self.assertEqual(
            [m.id for m in await self.maps.maps(GUILD_A, self.one.id, DM_VIEW)], [second]
        )

    async def test_a_title_is_cut_to_120_characters(self) -> None:
        # a prefix and a suffix that differ, so a cut from the wrong end would fail
        mid = await self.add(await self.picture(self.one.id, 1), dm=True,
                             title="a" * 120 + "b" * 80)  # fmt: skip
        got = await self.maps.get(GUILD_A, self.one.id, mid, DM_VIEW)
        assert got is not None
        self.assertEqual(got.title, "a" * 120)

    async def test_an_import_names_its_tool_and_starts_as_dm_only(self) -> None:
        mid = await self.add(
            await self.picture(self.one.id, 1), dm=True, came_from="import", import_tool="azgaar"
        )
        got = await self.maps.get(GUILD_A, self.one.id, mid, DM_VIEW)
        assert got is not None
        self.assertEqual((got.came_from, got.import_tool, got.audience), ("import", "azgaar", "dm"))
