"""The recap on the DM screen (#1455): Read it, Post to the table (asked once, then the website's
own move and the bot's own checks) and Not this one. Only a DM of the campaign, the campaign checked
on every press, a decided recap refusing, the narration attached to Read when there is some, and
the ids that survive a restart. The store's move is tested against a real database."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from dmbot.recaps.store import RecapStore
from dmbot.ui.recap_dm import (
    ASK,
    DECIDED,
    GONE,
    NOT_NOW,
    NOT_READY,
    NOT_YOURS,
    POSTED,
    SKIPPED,
    RecapButton,
    confirm_view,
    reading_text,
    ready_text,
    ready_view,
)
from tests import recap_fixtures as fx
from tests.test_recap_jobs import Base

GUILD, DM_ID, PLAYER = 5, 7, 11
CID = "c" * 32
AT = 100


def job(**kw: Any) -> Any:
    base = dict(
        state="ready", status="draft", title="The Silver Lock", game_day=4, session_at=AT,
        summary="《Tense》 Mira drew her blade.", housekeeping=("Mira has 4 HP.",),
        last_error="", guild_id=GUILD,
    )  # fmt: skip
    return SimpleNamespace(**{**base, **kw})


def press(act: str, user: int = DM_ID, *, found: Any = "default", decided: bool = True) -> Any:
    recaps = SimpleNamespace(
        get=AsyncMock(return_value=job() if found == "default" else found),
        decide=AsyncMock(return_value=decided),
    )
    campaign = SimpleNamespace(guild_id=GUILD, id=CID, dm_user_ids=frozenset({DM_ID}))
    bot = SimpleNamespace(
        campaigns=SimpleNamespace(get=AsyncMock(return_value=campaign)),
        recaps=recaps,
        settings=SimpleNamespace(site_url="https://site.example.com"),
        post_recaps=AsyncMock(),
        _recap_audio_files=AsyncMock(return_value=([], "")),
    )
    response = SimpleNamespace(
        is_done=lambda: False, send_message=AsyncMock(), defer=AsyncMock(), edit_message=AsyncMock()
    )
    interaction = SimpleNamespace(
        client=bot,
        guild=SimpleNamespace(id=GUILD),
        channel=object(),
        user=SimpleNamespace(id=user),
        response=response,
        followup=SimpleNamespace(send=AsyncMock()),
    )
    return RecapButton(act, CID, AT), bot, interaction


def told(interaction: Any) -> str:
    return str(interaction.response.send_message.await_args.args[0])


class TheButtons(unittest.IsolatedAsyncioTestCase):
    async def test_only_a_dm_of_the_campaign_may_press_any_of_them(self) -> None:
        for act in ("read", "post", "yes", "no", "drop"):
            button, bot, interaction = press(act, PLAYER)
            await button.callback(interaction)
            self.assertEqual(told(interaction), NOT_YOURS)
            bot.recaps.decide.assert_not_awaited()
            bot.post_recaps.assert_not_awaited()

    async def test_a_campaign_or_recap_that_is_gone_is_said_so(self) -> None:
        button, bot, interaction = press("read")
        bot.campaigns.get = AsyncMock(return_value=None)
        await button.callback(interaction)
        self.assertEqual(told(interaction), GONE)
        button, bot, interaction = press("post", found=None)
        await button.callback(interaction)
        self.assertEqual(told(interaction), GONE)

    async def test_a_recap_still_being_written_cannot_be_read_posted_or_skipped(self) -> None:
        for act in ("read", "post", "yes", "drop"):
            button, bot, interaction = press(act, found=job(state="beats"))
            await button.callback(interaction)
            self.assertEqual(told(interaction), NOT_READY)
            bot.recaps.decide.assert_not_awaited()

    async def test_read_it_posts_the_story_and_the_bullets_without_the_posted_by_line(self) -> None:
        button, bot, interaction = press("read")
        await button.callback(interaction)
        interaction.response.defer.assert_awaited_once()
        sent = [c.args[0] for c in interaction.followup.send.await_args_list]
        text = "\n".join(sent)
        self.assertIn("Mira drew her blade.", text)
        self.assertNotIn("《", text)  # the cues are not for reading
        self.assertIn("Mira has 4 HP.", text)
        self.assertNotIn("posted by your DM", text)
        self.assertIn("Want to change it first? Edit it here: https://site.example.com", text)
        self.assertTrue(all(len(part) <= 2000 for part in sent))
        bot.recaps.decide.assert_not_awaited()  # reading decides nothing

    async def test_read_it_attaches_the_narration_to_the_last_message_when_there_is_some(
        self,
    ) -> None:
        button, bot, interaction = press("read")
        files = [MagicMock(), MagicMock()]
        bot._recap_audio_files = AsyncMock(return_value=(files, ""))
        await button.callback(interaction)
        calls = interaction.followup.send.await_args_list
        self.assertEqual(calls[-1].kwargs["files"], files)
        self.assertTrue(all("files" not in c.kwargs for c in calls[:-1]))
        for f in files:
            f.close.assert_called_once()

    async def test_read_it_without_audio_attaches_nothing_and_a_note_is_for_the_dm_alone(
        self,
    ) -> None:
        button, bot, interaction = press("read")
        bot._recap_audio_files = AsyncMock(return_value=([], "The audio was left out: too big."))
        await button.callback(interaction)
        calls = interaction.followup.send.await_args_list
        self.assertTrue(all("files" not in c.kwargs for c in calls))
        self.assertEqual(calls[-1].args[0], "The audio was left out: too big.")
        self.assertTrue(calls[-1].kwargs["ephemeral"])

    async def test_post_to_the_table_asks_once_and_posts_nothing_yet(self) -> None:
        button, bot, interaction = press("post")
        await button.callback(interaction)
        self.assertEqual(told(interaction), ASK)
        ids = [
            i.custom_id
            for i in interaction.response.send_message.await_args.kwargs["view"].children
        ]
        self.assertEqual(ids, [f"dmbot:recap:yes:{CID}:{AT}", f"dmbot:recap:no:{CID}:{AT}"])
        bot.recaps.decide.assert_not_awaited()
        bot.post_recaps.assert_not_awaited()

    async def test_a_decided_recap_is_not_asked_about_again(self) -> None:
        for status in ("posting", "posted", "dropped"):
            button, _bot, interaction = press("post", found=job(status=status))
            await button.callback(interaction)
            self.assertEqual(told(interaction), DECIDED)

    async def test_not_now_changes_nothing(self) -> None:
        button, bot, interaction = press("no")
        await button.callback(interaction)
        self.assertEqual(told(interaction), NOT_NOW)
        bot.recaps.decide.assert_not_awaited()

    async def test_yes_takes_the_websites_move_then_the_bots_own_checks_and_says_it_posted(
        self,
    ) -> None:
        button, bot, interaction = press("yes")
        bot.recaps.get = AsyncMock(side_effect=[job(), job(status="posted")])
        await button.callback(interaction)
        bot.recaps.decide.assert_awaited_once_with(GUILD, CID, AT, "post")
        bot.post_recaps.assert_awaited_once_with(GUILD)  # plan, consent, secret names run there
        self.assertEqual(interaction.followup.send.await_args.args[0], POSTED)

    async def test_when_the_bots_checks_refuse_the_dm_hears_why_and_nothing_reached_the_table(
        self,
    ) -> None:
        button, bot, interaction = press("yes")
        why = "It names something you keep secret. Change the text, then post."
        bot.recaps.get = AsyncMock(side_effect=[job(), job(status="draft", last_error=why)])
        await button.callback(interaction)
        said = interaction.followup.send.await_args.args[0]
        self.assertIn(why, said)
        self.assertIn("Nothing went to the table", said)
        self.assertNotEqual(said, POSTED)

    async def test_yes_on_a_recap_decided_meanwhile_posts_nothing(self) -> None:
        button, bot, interaction = press("yes", decided=False)
        await button.callback(interaction)
        self.assertEqual(told(interaction), DECIDED)
        bot.post_recaps.assert_not_awaited()

    async def test_not_this_one_skips_it_and_removes_the_buttons(self) -> None:
        button, bot, interaction = press("drop")
        await button.callback(interaction)
        bot.recaps.decide.assert_awaited_once_with(GUILD, CID, AT, "drop")
        interaction.response.edit_message.assert_awaited_once_with(view=None)
        self.assertEqual(interaction.followup.send.await_args.args[0], SKIPPED)
        bot.post_recaps.assert_not_awaited()

    async def test_not_this_one_on_a_decided_recap_changes_nothing(self) -> None:
        button, _bot, interaction = press("drop", decided=False)
        await button.callback(interaction)
        self.assertEqual(told(interaction), DECIDED)
        interaction.response.edit_message.assert_not_awaited()

    def test_the_views_carry_ids_that_survive_a_restart(self) -> None:
        ids = [i.custom_id for i in ready_view(CID, AT).children]
        self.assertEqual(ids, [f"dmbot:recap:{a}:{CID}:{AT}" for a in ("read", "post", "drop")])
        self.assertEqual(len(confirm_view(CID, AT).children), 2)
        longest = f"dmbot:recap:post:{CID}:{10**11}"
        self.assertLessEqual(len(longest), 100)
        view = ready_view(CID, AT)
        self.assertIsNone(view.timeout)  # no timeout: a restart finds them by their ids

    def test_the_words(self) -> None:
        text = ready_text("The *Silver* Lock", "https://site.example.com/r")
        self.assertIn("Recap ready: **The \\*Silver\\* Lock**", text)
        self.assertIn("Nothing goes to the players until you press Post.", text)
        self.assertIn("Edit it here: https://site.example.com/r", text)
        self.assertNotIn("Edit it here", ready_text("T", ""))
        self.assertIn("Post it to the table now?", ASK)
        reading = reading_text(job(), "")
        self.assertNotIn("posted by your DM", reading)


class TheStoresMove(Base):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        await self.store.enqueue(1, self.one.id, AT, dm_user_id=fx.DM)

    async def status(self) -> str:
        found = await self.store.get(1, self.one.id, AT)
        assert found is not None
        return found.status

    async def test_post_needs_a_ready_draft_and_works_once(self) -> None:
        self.assertFalse(await self.store.decide(1, self.one.id, AT, "post"))  # still being written
        await self.store.update(1, self.one.id, AT, state="ready")
        self.assertTrue(await self.store.decide(1, self.one.id, AT, "post"))
        self.assertEqual(await self.status(), "posting")
        self.assertFalse(await self.store.decide(1, self.one.id, AT, "post"))
        self.assertFalse(await self.store.decide(1, self.one.id, AT, "drop"))  # no longer a draft

    async def test_drop_works_on_a_draft_once(self) -> None:
        self.assertTrue(await self.store.decide(1, self.one.id, AT, "drop"))
        self.assertEqual(await self.status(), "dropped")
        self.assertFalse(await self.store.decide(1, self.one.id, AT, "drop"))
        await self.store.update(1, self.one.id, AT, state="ready")
        self.assertFalse(await self.store.decide(1, self.one.id, AT, "post"))

    async def test_it_changes_only_that_campaign_and_that_session(self) -> None:
        await self.store.enqueue(1, self.two.id, AT, dm_user_id=fx.DM)
        await self.store.enqueue(1, self.one.id, 200, dm_user_id=fx.DM)
        await self.store.update(1, self.one.id, AT, state="ready")
        await self.store.update(1, self.two.id, AT, state="ready")
        await self.store.update(1, self.one.id, 200, state="ready")
        await self.store.decide(1, self.one.id, AT, "post")
        other = await self.store.get(1, self.two.id, AT)
        later = await self.store.get(1, self.one.id, 200)
        assert other is not None and later is not None
        self.assertEqual((other.status, later.status), ("draft", "draft"))

    async def test_an_unknown_action_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            await self.store.decide(1, self.one.id, AT, "delete")


_ = RecapStore  # (the store under test is the one the base builds)

if __name__ == "__main__":
    unittest.main()
