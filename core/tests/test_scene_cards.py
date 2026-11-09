"""The scene on the cards (#3596 slice 1, PR 4): a DM sees DMbot's summary of who, where and what
under the question, a manager who is not a DM does not, and a card without a scene is unchanged.
Invented names."""

from __future__ import annotations

import unittest

from dmbot.memory.lookup import LookupCache
from dmbot.memory.models import Entity
from dmbot.memory.scenes import ENTITY, Scene, Scenes
from dmbot.ui import name_card
from dmbot.ui import names as ui
from tests.test_memory_names import DM, GUILD, MANAGER, PLAYER, NamesTest

SCENE = "The patron at the bar who put 5 copper down for Marge, to buy Claude a drink."


class TheLine(unittest.TestCase):
    def test_no_scene_is_no_line(self) -> None:
        self.assertEqual(ui.scene_line(""), "")

    def test_the_scene_is_marked_as_dmbots_summary_and_cannot_format_or_ping(self) -> None:
        line = ui.scene_line("*loud* @everyone <@123>")
        self.assertTrue(line.startswith(f"\n{ui.SCENE_TITLE} "))
        self.assertIn("\\*loud\\*", line)
        self.assertNotIn("@everyone", line.replace("@​everyone", ""))

    def test_the_question_shows_it_above_the_places_and_is_unchanged_without_one(self) -> None:
        entity = Entity(
            id="a" * 32, type="character", name="Pell", description="heard 3 times",
            status="proposed", merged_into=None, source="scan", created_at=0,
        )  # fmt: skip
        plain = ui.suggestion_text(entity, 1, places="Places here")
        with_scene = ui.suggestion_text(entity, 1, places="Places here", scene=SCENE)
        self.assertIn(SCENE, with_scene)
        self.assertLess(with_scene.index(SCENE), with_scene.index("Places here"))
        self.assertEqual(with_scene.replace(ui.scene_line(SCENE), ""), plain)

    def test_a_scene_that_would_make_a_long_question_too_long_is_left_out(self) -> None:
        entity = Entity(
            id="a" * 32, type="character", name="Pell", description="", status="proposed",
            merged_into=None, source="scan", created_at=0,
        )  # fmt: skip
        long_places = "p" * 1300
        text = ui.suggestion_text(entity, 1, places=long_places, scene="s" * 600)
        self.assertLessEqual(len(text), ui.SUGGESTION_MAX)
        self.assertNotIn("DMbot's summary", text)


class OnTheReview(NamesTest):
    async def test_a_manager_who_is_not_a_dm_is_given_no_scene_by_the_review(self) -> None:
        await self.consent.grant(GUILD, PLAYER)
        made = await self.memory.add_unnamed(GUILD, self.campaign.id, what="a patron")
        await Scenes(self.memory).keep(
            GUILD, self.campaign.id, [Scene(ENTITY, made.entity.id, SCENE, 2, (PLAYER,), 1)],
            opted_in={PLAYER}, prompt_version="scenes-1", model="m",
        )  # fmt: skip
        entity = made.entity
        self.assertEqual(await ui.scene_of(self.it(DM), self.campaign, entity), SCENE)
        self.assertEqual(await ui.scene_of(self.it(MANAGER), self.campaign, entity), "")


class OnTheCard(NamesTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.bot.lookup = LookupCache(self.memory)
        made = await self.memory.add_unnamed(
            GUILD, self.campaign.id, what="a patron at the bar", anchor_id=None
        )
        self.someone = made.entity.id
        async with self.db.guild(GUILD) as conn:
            await conn.execute(
                "UPDATE memory_entities SET status = 'confirmed' WHERE id = %s", (self.someone,)
            )

    async def card(self, user: int = DM) -> str:
        self.bot.lookup.mark_all_stale()  # type: ignore[union-attr]
        it = self.it(user)
        await name_card.show_card(it, self.campaign.id, self.someone)
        return str(it.response.sent[0][0])

    async def keep(self, speakers: tuple[int, ...] = (PLAYER,)) -> None:
        await Scenes(self.memory).keep(
            GUILD, self.campaign.id, [Scene(ENTITY, self.someone, SCENE, 4, speakers, 1)],
            opted_in={PLAYER, DM}, prompt_version="scenes-1", model="m",
        )  # fmt: skip

    async def test_the_dm_sees_the_scene_on_the_card_of_an_unnamed_person(self) -> None:
        await self.consent.grant(GUILD, PLAYER)
        await self.keep()
        self.assertIn(SCENE, await self.card())

    async def test_a_scene_from_someone_who_stopped_is_gone_before_it_is_shown(self) -> None:
        await self.consent.grant(GUILD, PLAYER)
        await self.keep()
        await self.consent.revoke(GUILD, PLAYER)
        self.assertNotIn(SCENE, await self.card())

    async def test_a_manager_who_is_not_a_dm_is_not_shown_it(self) -> None:
        await self.consent.grant(GUILD, PLAYER)
        await self.keep()
        self.assertNotIn(SCENE, await self.card(MANAGER))

    async def test_a_card_without_a_scene_is_unchanged(self) -> None:
        self.assertNotIn(ui.SCENE_TITLE, await self.card())

    async def test_naming_the_person_takes_the_scene_off_the_card(self) -> None:
        await self.consent.grant(GUILD, PLAYER)
        await self.keep()
        async with self.db.guild(GUILD) as conn:
            await conn.execute(
                "UPDATE memory_entities SET unnamed = false, unnamed_what = NULL,"
                " unnamed_anchor = NULL, unnamed_role = NULL, name = 'Pell' WHERE id = %s",
                (self.someone,),
            )
        self.assertNotIn(SCENE, await self.card())
