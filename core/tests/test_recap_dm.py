"""The recap on the DM screen (#1455): Read it, Post to the table (asked once, then the website's
own move and the bot's own checks) and Not this one. Only a DM of the campaign, the campaign checked
on every press, a decided recap refusing, the narration attached to Read when there is some, and
the ids that survive a restart. The store's move is tested against a real database."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

from dmbot.mentions import NO_PINGS
from dmbot.recaps.store import RecapStore
from dmbot.ui.decision_lines import DecisionLines
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
BOT_ID = 99
LINE = "📜 Recap ready: **The Silver Lock**"
CID = "c" * 32
AT = 100


def job(**kw: Any) -> Any:
    base = dict(
        state="ready", status="draft", title="The Silver Lock", game_day=4, session_at=AT,
        summary="《Tense》 Mira drew her blade.", housekeeping=("Mira has 4 HP.",),
        last_error="", guild_id=GUILD,
    )  # fmt: skip
    return SimpleNamespace(**{**base, **kw})


def line_message(message_id: int, content: str = LINE) -> Any:
    """A DM-screen line the bot posted: what the buttons were pressed on, or another copy of it."""
    return SimpleNamespace(
        id=message_id, content=content, author=SimpleNamespace(id=BOT_ID), edit=AsyncMock()
    )


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
        user=SimpleNamespace(id=BOT_ID),
        decision_lines=DecisionLines(),
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
        message=line_message(900),
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
            cast(Any, i).custom_id
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
        interaction.response.edit_message.assert_awaited_once_with(
            content=f"{LINE}\nSkipped.", view=None, allowed_mentions=NO_PINGS
        )
        self.assertEqual(interaction.followup.send.await_args.args[0], SKIPPED)
        bot.post_recaps.assert_not_awaited()

    async def test_the_other_copy_of_the_line_loses_its_buttons_when_it_is_known(self) -> None:
        button, bot, interaction = press("drop")
        other = line_message(901, "📜 Last session's recap is still waiting: **The Silver Lock**.")
        bot.decision_lines.remember(("recap", CID, AT), other)  # the re-post at a session start
        await button.callback(interaction)
        other.edit.assert_awaited_once()
        self.assertIsNone(other.edit.await_args.kwargs["view"])
        self.assertTrue(other.edit.await_args.kwargs["content"].endswith("\nSkipped."))

    async def test_a_copy_that_is_not_known_is_left_alone_and_a_failed_edit_changes_nothing(
        self,
    ) -> None:
        button, bot, interaction = press("drop")  # nothing else is known: no other edit
        await button.callback(interaction)
        self.assertEqual(interaction.followup.send.await_args.args[0], SKIPPED)
        button, bot, interaction = press("drop")
        broken = line_message(902)
        broken.edit = AsyncMock(side_effect=RuntimeError("deleted"))
        bot.decision_lines.remember(("recap", CID, AT), broken)
        await button.callback(interaction)  # does not raise; the decision stands
        bot.recaps.decide.assert_awaited_once()
        self.assertEqual(interaction.followup.send.await_args.args[0], SKIPPED)

    async def test_yes_takes_the_buttons_off_the_line_that_asked_and_says_posted(self) -> None:
        post, bot, interaction = press("post")
        await post.callback(interaction)  # the line the DM pressed Post on is now known
        yes, _, confirm = press("yes")
        bot.recaps.get = AsyncMock(side_effect=[job(), job(status="posted")])
        confirm.client = bot
        await yes.callback(confirm)
        line = interaction.message
        line.edit.assert_awaited_once()
        self.assertIsNone(line.edit.await_args.kwargs["view"])
        self.assertTrue(line.edit.await_args.kwargs["content"].endswith("\nPosted."))

    async def test_a_recap_the_checks_refused_keeps_its_buttons(self) -> None:
        button, bot, interaction = press("yes")
        other = line_message(903)
        bot.decision_lines.remember(("recap", CID, AT), other)
        bot.recaps.get = AsyncMock(side_effect=[job(), job(status="draft", last_error="Not now.")])
        await button.callback(interaction)
        other.edit.assert_not_awaited()  # back to a draft: the DM can press Post again

    async def test_read_it_leaves_the_buttons_on(self) -> None:
        button, _bot, interaction = press("read")
        await button.callback(interaction)
        interaction.message.edit.assert_not_awaited()
        interaction.response.edit_message.assert_not_awaited()

    async def test_not_this_one_on_a_decided_recap_changes_nothing(self) -> None:
        button, _bot, interaction = press("drop", decided=False)
        await button.callback(interaction)
        self.assertEqual(told(interaction), DECIDED)
        interaction.response.edit_message.assert_not_awaited()

    def test_the_views_carry_ids_that_survive_a_restart(self) -> None:
        old = [RecapButton(a, CID, AT) for a in ("read", "approve", "post", "drop", "tell")]
        self.assertEqual(  # the old buttons still answer on messages already sent (#2118)
            [b.item.custom_id for b in old],
            [f"dmbot:recap:{a}:{CID}:{AT}" for a in ("read", "approve", "post", "drop", "tell")],
        )
        # (#2420) a new line: the three ways to approve, then Read it with no website address, or
        # the review link and Share a recap with one
        self.assertEqual(len(ready_view(CID, AT).children), 4)
        new = ready_view(CID, AT, "https://site.example.com").children
        self.assertEqual(
            [(getattr(getattr(c, "item", c), "label", ""), getattr(c, "url", None)) for c in new][
                3:
            ],
            [
                ("Review the recap", "https://site.example.com/account/recaps"),
                ("Share a recap", None),
            ],
        )
        self.assertEqual(cast(Any, new[4]).item.custom_id, f"dmbot:recapshare:{CID}")
        self.assertEqual(len(confirm_view(CID, AT).children), 2)
        longest = f"dmbot:recap:post:{CID}:{10**11}"
        self.assertLessEqual(len(longest), 100)
        view = ready_view(CID, AT)
        self.assertIsNone(view.timeout)  # no timeout: a restart finds them by their ids

    def test_the_words(self) -> None:
        text = ready_text("The *Silver* Lock", "https://site.example.com/r")
        self.assertIn("Recap ready: **The \\*Silver\\* Lock**", text)
        self.assertIn("Nothing goes to the players until you post it.", text)
        self.assertIn("post it on your recap page: https://site.example.com/r", text)
        self.assertNotIn("recap page", ready_text("T", ""))
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

    async def test_approve_and_post_hands_it_over_in_the_one_statement(self) -> None:
        # (#2420) the page's "Approve and post text only": approved and 'posting' together
        from dmbot.recaps.approval import text_hash

        await self.store.update(1, self.one.id, AT, state="ready", title="T", summary="S")
        got = await self.store.approve(
            1, self.one.id, AT, fx.DM, "text", expected=text_hash("T", "S"), post=True
        )
        self.assertTrue(got)
        job = await self.store.get(1, self.one.id, AT)
        assert job is not None
        self.assertEqual((job.status, job.approved, job.audio_choice), ("posting", True, "text"))

    async def test_a_changed_text_approves_and_posts_nothing(self) -> None:
        from dmbot.recaps.approval import text_hash

        await self.store.update(1, self.one.id, AT, state="ready", title="T", summary="S")
        self.assertFalse(
            await self.store.approve(
                1, self.one.id, AT, fx.DM, "text", expected=text_hash("T", "old"), post=True
            )
        )
        job = await self.store.get(1, self.one.id, AT)
        assert job is not None
        self.assertEqual((job.status, job.approved), ("draft", False))

    async def test_only_text_only_can_be_posted_on_the_spot(self) -> None:
        with self.assertRaises(ValueError):
            await self.store.approve(1, self.one.id, AT, fx.DM, "voice", expected="x", post=True)

    async def test_approve_but_dont_post_leaves_it_a_draft_the_page_shows_as_approved(self) -> None:
        # The page and Discord share this one store path: what Discord's "Approve but don't post"
        # records is exactly what the page reads as "Approved. Not posted".
        from dmbot.recaps.approval import text_hash

        await self.store.update(1, self.one.id, AT, state="ready", title="T", summary="S")
        self.assertTrue(
            await self.store.approve(
                1, self.one.id, AT, fx.DM, "text", expected=text_hash("T", "S")
            )
        )
        job = await self.store.get(1, self.one.id, AT)
        assert job is not None
        self.assertEqual((job.status, job.approved, job.approved_by), ("draft", True, fx.DM))

    async def test_an_unknown_action_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            await self.store.decide(1, self.one.id, AT, "delete")


_ = RecapStore  # (the store under test is the one the base builds)

if __name__ == "__main__":
    unittest.main()
