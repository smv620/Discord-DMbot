"""A player character's icon can be a kept picture (#3623): who may pick one and which, the icon's
address (a path of the website's own, never the picture's code), the fall back to the default
order when the picture goes, the one Undo, the route's redirect for members only, the backup
leaving it out, and a person's data deletion dropping the icons of their pictures. Invented
names."""

from __future__ import annotations

import json
from typing import Any

from dmbot.accounts import delete_person
from dmbot.campaigns.models import CampaignError
from dmbot.memory.pc_icons_backup import DAMAGED, PcIconsSection
from dmbot.web import pc_icons
from tests.test_pc_icons import CAROL, Icons
from tests.test_web_all_names import MEMBER
from tests.test_web_relationships import BOB_USER

CODE = "c" * 43


class Pictures(Icons):
    async def picture(
        self,
        poster: int,
        *,
        code: str | None = None,
        dm_only: bool = False,
        deleted: bool = False,
        campaign: str | None = None,
    ) -> int:
        async with self.db.guild(self.g) as conn:
            cur = await conn.execute(
                "INSERT INTO campaign_pictures (guild_id, campaign_id, poster_id, code, public,"
                " consent_version, right_granted_at, sha256, object_key, preview_key, size_bytes,"
                " made_at, dm_only, deleted_at) VALUES (%s, %s, %s, %s, %s, 7, 1, %s, 'key/a',"
                " 'key/a-preview', 10, 1, %s, %s) RETURNING id",
                (
                    self.g, campaign or self.one.id, poster,
                    code or (CODE[:-3] + f"{abs(hash((poster, dm_only, deleted))) % 1000:03d}"),
                    not dm_only, f"{abs(hash((poster, dm_only, deleted, campaign))):064x}"[:64],
                    dm_only, 1 if deleted else None,
                ),
            )  # fmt: skip
            row = await cur.fetchone()
        assert row is not None
        return int(row["id"])

    async def use(self, picture: int, who: Any = None, entity: str | None = None) -> Any:
        return await pc_icons.set_icon(
            self.web, who or self.session, self.cref, entity or str(self.hero),
            {"choice": "custom", "picture": picture},
        )  # fmt: skip

    async def refused_use(self, picture: Any, who: Any = None) -> str:
        from dmbot.web.recaps import RecapError

        try:
            await self.use(picture, who)
        except RecapError as err:
            return err.code
        raise AssertionError("was not refused")


class Choosing(Pictures):
    async def test_the_dm_picks_a_kept_picture_and_the_icon_is_a_path_not_a_code(self) -> None:
        pic = await self.picture(BOB_USER.id)
        done = await self.use(pic)
        self.assertEqual(done, {"previous": "default", "now": "custom"})
        icon = await self.icon()
        self.assertEqual(
            icon,
            {
                "kind": "picture",
                "url": f"/me/campaigns/{self.g}.{self.one.id}/pc-icons/{self.hero}/picture",
            },
        )
        self.assertNotIn(CODE[:20], json.dumps(await self.rows()))  # (never the picture's code)

    async def test_the_characters_own_player_may_use_only_a_picture_they_posted(self) -> None:
        mine = await self.picture(BOB_USER.id)
        theirs = await self.picture(self.session.user_id)
        await self.use(mine, who=self.player)
        self.assertEqual(await self.refused_use(theirs, who=self.player), "not_allowed")
        self.assertEqual((await self.rows())[str(self.hero)]["choice"], "custom")

    async def test_someone_who_is_neither_the_dm_nor_the_player_may_not(self) -> None:
        pic = await self.picture(BOB_USER.id)
        stranger = await self.signed(CAROL, MEMBER)
        self.assertEqual(await self.refused_use(pic, who=stranger), "not_allowed")

    async def test_a_dm_only_deleted_or_another_campaigns_picture_is_unavailable(self) -> None:
        for pic in (
            await self.picture(self.session.user_id, dm_only=True),
            await self.picture(self.session.user_id, deleted=True),
            await self.picture(self.session.user_id, campaign=self.two.id),
            999_999_999,
        ):
            self.assertEqual(await self.refused_use(pic), "unavailable", pic)

    async def test_bad_words_are_refused_before_the_door(self) -> None:
        for body in (
            {"choice": "custom"},
            {"choice": "custom", "picture": "7"},
            {"choice": "custom", "picture": True},
            {"choice": "custom", "picture": 0},
            {"choice": "hidden", "picture": 5},
        ):
            with self.subTest(body=body):
                from dmbot.web.recaps import RecapError

                with self.assertRaises(RecapError) as caught:
                    await pc_icons.set_icon(self.web, self.session, self.cref, str(self.hero), body)
                self.assertEqual(caught.exception.code, "bad_choice")

    async def test_undo_writes_the_previous_picture_back(self) -> None:
        first = await self.picture(BOB_USER.id)
        second = await self.picture(self.session.user_id)
        await self.use(first)
        replaced = await self.use(second)
        self.assertEqual(replaced["previousPicture"], first)
        again = await self.use(replaced["previousPicture"])
        self.assertEqual(again["previousPicture"], second)
        changed = await self.choose("hidden")
        self.assertEqual((changed["previous"], changed["previousPicture"]), ("custom", first))

    async def test_the_page_is_told_whether_there_is_anything_to_pick(self) -> None:
        self.assertFalse((await self.rows())[str(self.hero)]["has"]["pictures"])
        await self.picture(self.session.user_id)  # (the DM's own: not the player's to pick)
        self.assertTrue((await self.rows())[str(self.hero)]["has"]["pictures"])
        self.assertFalse((await self.rows(self.player))[str(self.hero)]["has"]["pictures"])
        await self.picture(BOB_USER.id)
        self.assertTrue((await self.rows(self.player))[str(self.hero)]["has"]["pictures"])


class WhenThePictureGoes(Pictures):
    async def test_a_deleted_picture_falls_back_to_the_default_order(self) -> None:
        await self.give_avatar()
        pic = await self.picture(BOB_USER.id)
        await self.use(pic)
        self.assertEqual((await self.icon())["kind"], "picture")
        await self.sql("UPDATE campaign_pictures SET deleted_at = 5 WHERE id = %s", pic)
        self.assertEqual(await self.icon(), {"kind": "discord", "url": self.avatar_url()})

    async def test_a_picture_made_the_dms_alone_stops_being_an_icon(self) -> None:
        pic = await self.picture(BOB_USER.id)
        await self.use(pic)
        await self.sql(
            "UPDATE campaign_pictures SET dm_only = TRUE, public = FALSE WHERE id = %s", pic
        )
        self.assertIsNone(await self.icon())
        self.assertIsNone(
            await pc_icons.file_keys(self.web, self.session, self.cref, str(self.hero))
        )

    async def test_a_picture_row_that_is_removed_takes_the_choice_with_it(self) -> None:
        pic = await self.picture(BOB_USER.id)
        await self.use(pic)
        await self.sql("DELETE FROM campaign_pictures WHERE id = %s", pic)
        self.assertEqual((await self.rows())[str(self.hero)]["choice"], "default")

    async def test_a_persons_data_deletion_drops_the_icon_of_their_picture(self) -> None:
        mine = await self.picture(BOB_USER.id)
        await self.use(mine)
        other_hero = await self.picture(self.session.user_id)
        self.assertEqual((await self.rows())[str(self.hero)]["choice"], "custom")
        await delete_person(self.db, BOB_USER.id)
        self.assertEqual((await self.rows())[str(self.hero)]["choice"], "default")
        self.assertGreater(other_hero, 0)  # (a picture of anyone else is untouched)


class TheFile(Pictures):
    async def test_members_get_the_keys_while_the_picture_is_pickable(self) -> None:
        pic = await self.picture(BOB_USER.id)
        await self.use(pic)
        keys = await pc_icons.file_keys(self.web, self.player, self.cref, str(self.hero))
        self.assertEqual(keys, ("key/a", "key/a-preview"))

    async def test_nothing_for_a_character_with_another_kind_of_icon(self) -> None:
        self.assertIsNone(
            await pc_icons.file_keys(self.web, self.session, self.cref, str(self.hero))
        )


class TheBackup(Pictures):
    async def test_a_picture_choice_is_left_out_and_a_file_that_says_custom_is_damaged(
        self,
    ) -> None:
        await self.choose("hidden")
        other = str(self.hero)
        section = PcIconsSection()
        async with self.db.guild(self.g) as conn:
            dumped = await section.dump(conn, self.g, self.one.id)
        self.assertEqual([r["entity_id"] for r in dumped], [other])
        await self.use(await self.picture(BOB_USER.id))
        async with self.db.guild(self.g) as conn:
            self.assertEqual(await section.dump(conn, self.g, self.one.id), [])  # (left out)
        bad = [
            {"entity_id": other, "choice": "custom", "custom_ref": 7, "set_by": 1, "updated_at": 1}
        ]
        with self.assertRaises(CampaignError) as caught:
            section.check(bad)
        self.assertEqual(str(caught.exception), DAMAGED)
