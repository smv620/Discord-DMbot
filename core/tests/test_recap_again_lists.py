"""Making a recap again warns first when the DM saved "who was there" lists for its scenes, as the
website's question does (#2727, #2684): the count in plain words, "no" changes nothing, a recap
without lists is asked as before, an unreadable one warns anyway, and only a DM of the campaign
reads or answers it. Real database for the read; a stand-in for Discord's side. Invented names."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from dmbot.recaps.discord import RecapFeatures
from dmbot.recaps.store import SavedLists
from dmbot.ui import recap_again as ra
from tests import recap_fixtures as fx
from tests.test_recap_again import SESSION, Again

STRANGER = 4242


class Lists(Again):
    async def beats(self, *saved: bool, at: int = SESSION) -> None:
        sheet = [
            {"number": n, "what": "x", "who": [], "lines": [], "kind": "event"}
            | ({"saved": True, "present": ["Mira"]} if flag else {})
            for n, flag in enumerate(saved, 1)
        ]
        await self.sql(
            "UPDATE recap_jobs SET beats = %s::jsonb WHERE guild_id = 1 AND session_at = %s",
            json.dumps(sheet), at,
        )  # fmt: skip

    def me(self, *, again: str = "queued") -> Any:
        """The bot as the question sees it: the real read, a recorded start."""
        me: Any = SimpleNamespace(recaps=self.store, campaigns=self.campaigns, started=[])

        async def recap_again(
            campaign: Any, at: int, user: int, *, replace_ok: bool = False
        ) -> str:
            me.started.append((at, user, replace_ok))
            return again

        me.recap_again = recap_again

        async def saved(campaign: Any, at: int, user: int) -> Any:
            return await RecapFeatures.recap_saved_lists(me, campaign, at, user)

        me.recap_saved_lists = saved
        return me

    async def campaign(self) -> Any:
        found = await self.campaigns.get(1, self.one.id)
        assert found is not None
        return found

    def interaction(self, user: int = fx.DM) -> Any:
        return SimpleNamespace(
            user=SimpleNamespace(id=user), client=None, guild=SimpleNamespace(id=1)
        )

    async def asked(self, me: Any, user: int = fx.DM) -> tuple[list[tuple[Any, Any]], Any]:
        """Run the question; what was said (text, view) and the interaction."""
        said: list[tuple[Any, Any]] = []

        async def tell(interaction: Any, text: str, *, view: Any = None) -> None:
            said.append((text, view))

        interaction = self.interaction(user)
        interaction.client = me
        with patch.object(ra, "_tell", tell):
            await ra.start(interaction, await self.campaign(), SESSION)
        return said, interaction


class TheRead(Lists):
    async def test_no_recap_job_has_no_lists_to_lose(self) -> None:
        self.assertIsNone(await self.store.saved_lists(1, self.one.id, SESSION))

    async def test_the_scenes_with_a_saved_list_are_counted_and_nothing_else(self) -> None:
        await self.lines(SESSION)
        await self.written(SESSION)
        await self.beats(True, False, True)
        self.assertEqual(await self.store.saved_lists(1, self.one.id, SESSION), SavedLists(2, True))

    async def test_a_recap_not_finished_says_so(self) -> None:
        await self.lines(SESSION)
        await self.store.enqueue(1, self.one.id, SESSION, dm_user_id=fx.DM)
        await self.beats(True)
        self.assertEqual(
            await self.store.saved_lists(1, self.one.id, SESSION), SavedLists(1, False)
        )

    async def test_odd_stored_data_counts_as_none(self) -> None:
        await self.lines(SESSION)
        await self.written(SESSION)
        await self.sql(
            'UPDATE recap_jobs SET beats = \'[5, "x", {"saved": "true"}, {"saved": false}]\''
            " WHERE guild_id = 1 AND session_at = %s",
            SESSION,
        )
        self.assertEqual(await self.store.saved_lists(1, self.one.id, SESSION), SavedLists(0, True))
        await self.sql(
            "UPDATE recap_jobs SET beats = '{}' WHERE guild_id = 1 AND session_at = %s", SESSION
        )
        self.assertEqual(await self.store.saved_lists(1, self.one.id, SESSION), SavedLists(0, True))

    async def test_only_a_dm_of_the_campaign_is_told_anything(self) -> None:
        await self.lines(SESSION)
        await self.written(SESSION)
        await self.beats(True)
        me = self.me()
        self.assertIsNone(
            await RecapFeatures.recap_saved_lists(me, await self.campaign(), SESSION, STRANGER)
        )
        self.assertEqual(
            await RecapFeatures.recap_saved_lists(me, await self.campaign(), SESSION, fx.DM),
            SavedLists(1, True),
        )


class TheQuestion(Lists):
    async def test_a_recap_with_saved_lists_warns_before_anything_starts(self) -> None:
        await self.lines(SESSION)
        await self.written(SESSION)
        await self.beats(True, True, False)
        me = self.me()
        said, _ = await self.asked(me)
        ((text, view),) = said
        self.assertIn(
            "You saved who was in 2 scenes of this recap. Making it again deletes those lists.",
            text,
        )
        self.assertEqual(me.started, [])  # (nothing is started by asking)
        self.assertTrue(view.lists and view.finished)
        self.assertTrue(text.endswith(ra.OLD_STAYS))  # (a finished recap stands: said once)

    async def test_one_scene_is_said_in_the_singular(self) -> None:
        self.assertIn("1 scene of this recap", ra.lists_warning(1))
        self.assertIn("deletes that list.", ra.lists_warning(1))

    async def test_no_changes_nothing_and_yes_starts_it_once(self) -> None:
        await self.lines(SESSION)
        await self.written(SESSION)
        await self.beats(True)
        me = self.me()
        said, interaction = await self.asked(me)
        view = said[0][1]
        told: list[str] = []

        async def tell(i: Any, text: str, *, view: Any = None) -> None:
            told.append(text)

        with patch.object(ra, "_tell", tell):
            await view.no.callback(interaction)
        self.assertEqual((told, me.started), ([ra.KEPT_OLD], []))
        with patch.object(ra, "_tell", tell), patch.object(ra, "_bot", lambda i: me):
            await view.yes.callback(interaction)
        # the same yes covers the finished recap: one question, not two
        self.assertEqual(me.started, [(SESSION, fx.DM, True)])
        self.assertEqual(told[-1], ra.QUEUED_KEEPING)

    async def test_a_recap_that_failed_but_has_lists_does_not_claim_an_old_one_stays(self) -> None:
        await self.lines(SESSION)
        await self.store.enqueue(1, self.one.id, SESSION, dm_user_id=fx.DM)
        await self.store.update(1, self.one.id, SESSION, state="failed")
        await self.beats(True)
        me = self.me()
        said, interaction = await self.asked(me)
        view = said[0][1]
        self.assertFalse(view.finished)
        self.assertNotIn(ra.OLD_STAYS, said[0][0])
        self.assertEqual((view.yes.label, view.no.label), ("Make it again", "Leave it"))
        told: list[str] = []

        async def tell(i: Any, text: str, *, view: Any = None) -> None:
            told.append(text)

        with patch.object(ra, "_tell", tell):
            await view.no.callback(interaction)
        self.assertEqual((told, me.started), ([ra.NOTHING_CHANGED], []))
        with patch.object(ra, "_tell", tell), patch.object(ra, "_bot", lambda i: me):
            await view.yes.callback(interaction)
        self.assertEqual(me.started, [(SESSION, fx.DM, False)])
        self.assertEqual(told[-1], ra.QUEUED)  # (no "the recap you had stays")

    async def test_a_recap_without_saved_lists_is_asked_as_before(self) -> None:
        await self.lines(SESSION)
        await self.written(SESSION)
        await self.beats(False, False)
        me = self.me(again="needs_confirm")
        said, _ = await self.asked(me)
        ((text, view),) = said
        self.assertEqual(text, ra.ASK)
        self.assertFalse(view.lists)
        self.assertEqual(me.started, [(SESSION, fx.DM, False)])

    async def test_a_session_with_no_recap_goes_straight_ahead(self) -> None:
        await self.lines(SESSION)
        me = self.me(again="queued")
        said, _ = await self.asked(me)
        self.assertEqual(said, [(ra.QUEUED, None)])

    async def test_a_recap_that_cannot_be_read_warns_anyway(self) -> None:
        await self.lines(SESSION)
        await self.written(SESSION)
        me = self.me()

        async def broken(*args: Any) -> Any:
            raise RuntimeError("down")

        me.recaps = SimpleNamespace(saved_lists=broken)
        said, _ = await self.asked(me)
        ((text, view),) = said
        self.assertIn(ra.LISTS_UNREADABLE, text)
        self.assertEqual(me.started, [])
        self.assertIsNone(view.finished)  # (not known whether a recap stands)
        told: list[str] = []

        async def tell(i: Any, text: str, *, view: Any = None) -> None:
            told.append(text)

        me.recaps = self.store  # (readable again by the time the DM answers)
        with patch.object(ra, "_tell", tell), patch.object(ra, "_bot", lambda i: me):
            await view.yes.callback(self.interaction())
        # one question, not two: the yes also allows replacing, promising nothing about an old one
        self.assertEqual(me.started, [(SESSION, fx.DM, True)])
        self.assertEqual(told[-1], ra.QUEUED)

    async def test_only_a_dm_can_answer_the_question(self) -> None:
        await self.lines(SESSION)
        await self.written(SESSION)
        await self.beats(True)
        me = self.me()
        said, _ = await self.asked(me)
        view = said[0][1]
        told: list[str] = []

        async def tell(i: Any, text: str, *, view: Any = None) -> None:
            told.append(text)

        stranger = self.interaction(STRANGER)
        stranger.client = me
        with patch.object(ra, "_tell", tell):
            await view.yes.callback(stranger)
        self.assertEqual((told, me.started), ([ra.NOT_YOURS], []))

    async def test_the_confirmed_start_does_not_ask_the_lists_question_again(self) -> None:
        await self.lines(SESSION)
        await self.written(SESSION)
        await self.beats(True)
        me = self.me()
        told: list[str] = []

        async def tell(i: Any, text: str, *, view: Any = None) -> None:
            told.append(text)

        interaction = self.interaction()
        interaction.client = me
        with patch.object(ra, "_tell", tell):
            await ra.start(
                interaction, await self.campaign(), SESSION, replace_ok=True, lists_ok=True
            )
        self.assertEqual(me.started, [(SESSION, fx.DM, True)])
