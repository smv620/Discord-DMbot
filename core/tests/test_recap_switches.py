"""The DM's recap choices on the campaign (#1421): write a recap, make the narration and music,
email them. On by default, kept per campaign, in backups, read by the three job paths, and only
ever able to turn things off: the plan gate still wins. The panel's buttons are DM-only."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from dmbot.bot import DMBot
from dmbot.campaigns.models import CampaignError
from dmbot.campaigns.store import CampaignStore, decode_backup, encode_backup
from dmbot.dm_screen import recap_panel as panel
from dmbot.dm_screen.settings import settings_view
from dmbot.transcript.models import Line
from tests.pg import DatabaseTest

DM, OTHER = 7, 9
GUILD = 1


class Switches(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.store = CampaignStore(self.db)
        self.c = await self.store.create(GUILD, "Frozen Wastes", DM)

    async def test_all_three_are_on_by_default_and_each_turns_off_and_on(self) -> None:
        self.assertEqual(
            (self.c.recaps_text, self.c.recaps_audio, self.c.recaps_email), (True,) * 3
        )
        for which in ("text", "audio", "email"):
            off = await self.store.set_recap_switch(GUILD, self.c.id, which, False)
            self.assertFalse(getattr(off, f"recaps_{which}"))
            others = {w for w in ("text", "audio", "email") if w != which}
            self.assertTrue(all(getattr(off, f"recaps_{w}") for w in others))  # only that one
            back = await self.store.set_recap_switch(GUILD, self.c.id, which, True)
            self.assertTrue(getattr(back, f"recaps_{which}"))

    async def test_only_the_three_choices_exist(self) -> None:
        for bad in ("posted", "text; DROP TABLE campaigns", ""):
            with self.assertRaises(ValueError):
                await self.store.set_recap_switch(GUILD, self.c.id, bad, False)

    async def test_a_choice_stays_with_its_campaign(self) -> None:
        other = await self.store.create(GUILD, "Other", DM)
        await self.store.set_recap_switch(GUILD, self.c.id, "email", False)
        again = await self.store.get(GUILD, other.id)
        assert again is not None
        self.assertTrue(again.recaps_email)

    async def test_the_choices_go_in_a_backup_and_a_restored_campaign_keeps_them(self) -> None:
        await self.store.set_recap_switch(GUILD, self.c.id, "audio", False)
        await self.store.set_recap_switch(GUILD, self.c.id, "email", False)
        raw = encode_backup(await self.store.export(GUILD, self.c.id))
        copy = await self.store.import_backup(2, decode_backup(raw), DM)
        self.assertEqual(
            (copy.recaps_text, copy.recaps_audio, copy.recaps_email), (True, False, False)
        )
        # Restoring over a campaign sets them back too.
        await self.store.set_recap_switch(GUILD, self.c.id, "audio", True)
        await self.store.import_backup(GUILD, decode_backup(raw), DM, replace_campaign_id=self.c.id)
        now = await self.store.get(GUILD, self.c.id)
        assert now is not None
        self.assertEqual((now.recaps_audio, now.recaps_email), (False, False))

    async def test_an_older_backup_without_them_has_them_on_and_a_damaged_one_is_refused(
        self,
    ) -> None:
        backup = await self.store.export(GUILD, self.c.id)
        for key in ("recaps_text", "recaps_audio", "recaps_email"):
            backup["campaign"].pop(key)
        copy = await self.store.import_backup(2, decode_backup(encode_backup(backup)), DM)
        self.assertEqual((copy.recaps_text, copy.recaps_audio, copy.recaps_email), (True,) * 3)
        backup["campaign"]["recaps_text"] = "no"
        with self.assertRaises(CampaignError):
            await self.store.import_backup(2, decode_backup(encode_backup(backup)), DM)


def campaign(**kw: Any) -> Any:
    base = {
        "id": "c" * 32, "guild_id": GUILD, "name": "Frozen Wastes", "dm_user_ids": frozenset({DM}),
        "owner_user_id": DM, "recaps_text": True, "recaps_audio": True, "recaps_email": True,
        "dm_screen_channel_id": 66,
    }  # fmt: skip
    return SimpleNamespace(**{**base, **kw})


ALICE_ID = 7


def long_session() -> list[Line]:
    """Two hours of game talk by one person who agreed: never a short session."""
    return [Line(i * 120_000, ALICE_ID, "x", "words said", duration_ms=110_000) for i in range(60)]


def fake_bot(camp: Any, *, plan: dict[str, bool] | None = None, audio_on: bool = True) -> Any:
    """A bot with just what the three job paths read, and the real methods bound to it."""
    allowed = {"recap": True, "recap_audio": True, **(plan or {})}

    async def plan_allows(action: str, *_: Any, **__: Any) -> bool:
        return allowed[action]

    bot = SimpleNamespace(
        settings=SimpleNamespace(
            recaps_on=True,
            recap_audio_on=audio_on,
            site_url="https://site.example.com",
            enforce_plans=False,
            recap_audio_per_month=5,
            recaps_per_month=5,
        ),
        campaigns=SimpleNamespace(get=AsyncMock(return_value=camp)),
        plan_allows=plan_allows,
        recap_worker=object(),
        recap_holds=None,  # no hold store: queued at once, as before #2169
        recaps=SimpleNamespace(
            enqueue=AsyncMock(return_value=True),
            used_in_month=AsyncMock(return_value=SimpleNamespace(narrated=0, recaps=0)),
        ),
        transcripts=SimpleNamespace(
            session=AsyncMock(return_value=SimpleNamespace(id="s", started_at=100, lines=5)),
            lines=AsyncMock(return_value=long_session()),
        ),
        consent=SimpleNamespace(consenting=AsyncMock(return_value={ALICE_ID})),
        post=AsyncMock(),
        post_message=AsyncMock(),
        _short_asks={},
        mailer=object(),
        meter=None,
    )
    for name in (
        "recap_session", "_queue_recap", "_say_writing", "_hold_or_queue", "recap_allowed",
        "recap_told", "recap_audio_wanted", "recap_audio_switched", "recap_audio_allowed",
        "recap_plan_facts", "recap_audio_why", "_recap_allowance", "_recap_refusal",
    ):  # fmt: skip
        setattr(bot, name, getattr(DMBot, name).__get__(bot))
    return bot


def table() -> Any:
    return SimpleNamespace(
        campaign_id="c" * 32, guild_id=GUILD, transcript_session_id="s", screen_channel_id=66
    )


class WhenTheSessionEnds(unittest.IsolatedAsyncioTestCase):
    async def test_a_recap_is_queued_when_everything_is_on(self) -> None:
        bot = fake_bot(campaign())
        await bot.recap_session(table())
        bot.recaps.enqueue.assert_awaited_once()
        self.assertIn("email", bot.post.await_args.args[1])

    async def test_no_recap_is_queued_and_nothing_is_said_when_the_dm_turned_the_text_off(
        self,
    ) -> None:
        bot = fake_bot(campaign(recaps_text=False))
        await bot.recap_session(table())
        bot.recaps.enqueue.assert_not_awaited()
        bot.post.assert_not_awaited()

    async def test_the_plan_still_wins_over_every_switch(self) -> None:
        bot = fake_bot(campaign(), plan={"recap": False})
        await bot.recap_session(table())
        bot.recaps.enqueue.assert_not_awaited()
        self.assertFalse(await bot.recap_allowed(GUILD, "c" * 32, SimpleNamespace()))

    async def test_with_the_email_off_the_line_says_where_to_find_it(self) -> None:
        bot = fake_bot(campaign(recaps_email=False))
        await bot.recap_session(table())
        said = bot.post.await_args.args[1]
        self.assertIn("Find it on your recap page", said)
        self.assertNotIn("email", said)

    async def test_a_job_stops_if_the_text_is_switched_off_while_it_waits(self) -> None:
        self.assertTrue(await fake_bot(campaign()).recap_allowed(GUILD, "c", SimpleNamespace()))
        off = fake_bot(campaign(recaps_text=False))
        self.assertFalse(await off.recap_allowed(GUILD, "c", SimpleNamespace()))


class TheAudioAndTheEmail(unittest.IsolatedAsyncioTestCase):
    async def test_the_audio_is_wanted_only_with_the_switch_the_plan_and_the_key(self) -> None:
        job = SimpleNamespace()
        self.assertTrue(await fake_bot(campaign()).recap_audio_wanted(GUILD, "c", job))
        self.assertFalse(
            await fake_bot(campaign(recaps_audio=False)).recap_audio_wanted(GUILD, "c", job)
        )
        # The switch can only turn it off: a plan without it, or no key, is still a no.
        self.assertFalse(
            await fake_bot(campaign(), plan={"recap_audio": False}).recap_audio_wanted(
                GUILD, "c", job
            )
        )
        self.assertFalse(
            await fake_bot(campaign(), audio_on=False).recap_audio_wanted(GUILD, "c", job)
        )

    async def test_the_email_is_skipped_when_off_and_sent_when_on(self) -> None:
        job = SimpleNamespace(state="ready", session_at=100, title="T", to_confirm=())
        with patch("dmbot.recaps.discord.tell_the_dm", AsyncMock()) as sent:
            await fake_bot(campaign(recaps_email=False)).recap_told(GUILD, "c" * 32, job)
            sent.assert_not_awaited()
            await fake_bot(campaign()).recap_told(GUILD, "c" * 32, job)
            sent.assert_awaited_once()

    async def test_a_ready_recap_is_on_the_dm_screen_with_its_buttons_email_or_not(self) -> None:
        for email in (False, True):
            bot = fake_bot(campaign(recaps_email=email))
            ready = SimpleNamespace(
                state="ready", session_at=100, title="The Silver Lock", to_confirm=()
            )
            with patch("dmbot.recaps.discord.tell_the_dm", AsyncMock()) as sent:
                await bot.recap_told(GUILD, "c" * 32, ready)
            self.assertEqual(sent.await_count, 1 if email else 0)
            (channel, line), kwargs = bot.post.await_args.args, bot.post.await_args.kwargs
            self.assertEqual(channel, 66)
            self.assertIn("Recap ready: **The Silver Lock**", line)
            self.assertIn("on your recap page: https://site.example.com", line)
            # the three ways to approve (#2420), then the review link to this recap's page and
            # Share a recap (#2118)
            children = kwargs["view"].children
            self.assertEqual(
                [c.item.custom_id.split(":")[2] for c in children[:3]], ["ap", "aa", "ak"]
            )
            self.assertTrue(children[3].url.startswith("https://site.example.com/"))
            self.assertEqual(children[4].item.custom_id, f"dmbot:recapshare:{'c' * 32}")
            self.assertFalse(children[1].item.disabled)  # audio is in this campaign's plan

    async def test_the_audio_button_is_greyed_with_its_reason_for_each_reason(self) -> None:
        # (#2420) not set up here, the DM's switch off, the plan, and the month's allowance used up
        from dmbot.ui.recap_dm import AUDIO_WHY

        used_up = fake_bot(campaign())
        used_up.recaps.used_in_month = AsyncMock(return_value=SimpleNamespace(narrated=5, recaps=0))
        cases = [
            (fake_bot(campaign(), audio_on=False), "not_set_up"),
            (fake_bot(campaign(recaps_audio=False)), "not_set_up"),
            (fake_bot(campaign(), plan={"recap_audio": False}), "unavailable"),
            (used_up, "unavailable"),
        ]
        for bot, why in cases:
            ready = SimpleNamespace(
                state="ready", session_at=100, title="The Silver Lock", to_confirm=()
            )
            with patch("dmbot.recaps.discord.tell_the_dm", AsyncMock()):
                await bot.recap_told(GUILD, "c" * 32, ready)
            (_channel, line), kwargs = bot.post.await_args.args, bot.post.await_args.kwargs
            children = kwargs["view"].children
            self.assertTrue(children[1].item.disabled, why)  # the audio button
            self.assertFalse(children[0].item.disabled)
            self.assertFalse(children[2].item.disabled)
            self.assertIn(AUDIO_WHY[why], line)
            # asking why must not post the used-up refusal on top of the recap line (#2504)
            self.assertEqual(bot.post.await_count, 1, why)

    async def test_a_failure_in_telling_why_audio_is_off_still_posts_the_recap(self) -> None:
        bot = fake_bot(campaign())
        bot.recap_audio_why = AsyncMock(side_effect=OSError("down"))
        ready = SimpleNamespace(
            state="ready", session_at=100, title="The Silver Lock", to_confirm=()
        )
        with (
            patch("dmbot.recaps.discord.tell_the_dm", AsyncMock()),
            self.assertLogs("dmbot.bot", "ERROR"),
        ):
            await bot.recap_told(GUILD, "c" * 32, ready)
        self.assertTrue(bot.post.await_args.kwargs["view"].children[1].item.disabled)

    async def test_a_failed_recap_is_said_on_the_dm_screen_only_when_the_email_is_off(self) -> None:
        failed = SimpleNamespace(state="failed", session_at=100, title="")
        off = fake_bot(campaign(recaps_email=False))
        with patch("dmbot.recaps.discord.tell_the_dm", AsyncMock()) as sent:
            await off.recap_told(GUILD, "c" * 32, failed)
        sent.assert_not_awaited()
        channel, line = off.post.await_args.args
        self.assertEqual(channel, 66)
        self.assertIn("couldn't write this session", line)
        self.assertIn("https://site.example.com", line)
        on = fake_bot(campaign())
        with patch("dmbot.recaps.discord.tell_the_dm", AsyncMock()):
            await on.recap_told(GUILD, "c" * 32, failed)
        on.post.assert_not_awaited()  # the email says it

    async def test_the_panel_shows_what_the_plan_allows_and_how(self) -> None:
        facts = await fake_bot(campaign()).recap_plan_facts(campaign())
        self.assertEqual((facts.text, facts.audio), (True, True))
        none = await fake_bot(campaign(), plan={"recap": False}).recap_plan_facts(campaign())
        self.assertEqual((none.text, none.audio), (False, False))
        no_audio = await fake_bot(campaign(), plan={"recap_audio": False}).recap_plan_facts(
            campaign()
        )
        self.assertEqual((no_audio.text, no_audio.audio), (True, False))
        unset = await fake_bot(campaign(), audio_on=False).recap_plan_facts(campaign())
        self.assertFalse(unset.audio)


class ThePanel(unittest.IsolatedAsyncioTestCase):
    def test_how_the_plan_has_spoken_recaps_is_said_in_one_line(self) -> None:
        plans = frozenset({"guild", "pro"})
        self.assertEqual(panel.audio_how("Guild", "paid", plans), "part of your plan")
        self.assertEqual(panel.audio_how("Table", "paid", plans), "an add-on")
        self.assertEqual(panel.audio_how("Free access", "free", plans), "part of your plan")
        self.assertEqual(panel.audio_how(None, "grant", plans), "part of your plan")

    def test_the_text_lists_each_choice_and_hides_the_audio_one_without_the_plan(self) -> None:
        full = panel.panel_text(
            campaign(recaps_email=False), panel.PlanFacts(True, True, "an add-on")
        )
        self.assertIn("✅ On: Write a recap after each session", full)
        self.assertIn("✅ On: Make the narration and music (an add-on)", full)
        self.assertIn("⬜ Off: Email me when it is ready", full)
        self.assertIn("The recap will be on your recap page.", full)
        self.assertIn("Nothing goes to the table until you say so.", full)
        self.assertTrue(
            panel.panel_text(campaign(), panel.PlanFacts(True, True), "Saved. X").startswith(
                "Saved. X"
            )
        )
        self.assertEqual(panel.panel_text(campaign(), None), panel.CANT_TELL)  # not "not in plan"
        plain = panel.panel_text(campaign(), panel.PlanFacts(True, False))
        self.assertNotIn("narration", plain)
        self.assertEqual(
            panel.panel_text(campaign(), panel.PlanFacts(False, False)), panel.NOT_IN_PLAN
        )

    def test_the_buttons_follow_the_plan(self) -> None:
        three = panel.RecapsPanel(campaign(), panel.PlanFacts(True, True, "part of your plan"))
        self.assertEqual(len(three.children), 5)  # and ✍️ Recap style and Share a recap
        two = panel.RecapsPanel(campaign(), panel.PlanFacts(True, False))
        self.assertEqual(len(two.children), 4)
        self.assertEqual(
            len(panel.RecapsPanel(campaign(), panel.PlanFacts(False, False)).children), 0
        )

    def test_the_settings_card_offers_the_button_to_the_campaigns_dms_only(self) -> None:
        camp = campaign(
            dm_screen_level="normal", dm_screen_visibility="peek", rules_cards=False,
            side_initiative=False, dndbeyond_invite=None, paused=False, quiet_stop_minutes=30,
        )  # fmt: skip

        def ids(viewer: int) -> list[str]:
            return [
                str(getattr(getattr(c, "item", c), "custom_id", ""))
                for c in settings_view(camp, None, viewer).children
            ]

        self.assertIn(f"dmbot:recaps:{'c' * 32}", ids(DM))
        self.assertNotIn(f"dmbot:recaps:{'c' * 32}", ids(OTHER))

    async def test_a_server_manager_who_is_not_a_dm_cannot_change_it(self) -> None:
        camp = campaign()
        store = SimpleNamespace(
            get=AsyncMock(return_value=camp), set_recap_switch=AsyncMock(return_value=camp)
        )
        it = MagicMock()
        it.user = SimpleNamespace(id=OTHER, guild_permissions=SimpleNamespace(manage_guild=True))
        it.guild, it.guild_id = SimpleNamespace(id=GUILD), GUILD
        it.client = SimpleNamespace(campaigns=store)
        it.response.send_message = AsyncMock()
        await panel.RecapsPanel(camp, panel.PlanFacts(True, True, "x")).save(it, "email", False)
        store.set_recap_switch.assert_not_awaited()
        self.assertEqual(it.response.send_message.await_args.args[0], panel.ONLY_DM)

    async def test_only_the_campaigns_dm_can_open_or_change_it(self) -> None:
        camp = campaign()
        store = SimpleNamespace(
            get=AsyncMock(return_value=camp), set_recap_switch=AsyncMock(return_value=camp)
        )
        guild = SimpleNamespace(id=GUILD)

        def interaction(user: int) -> Any:
            it = MagicMock()
            it.user = SimpleNamespace(
                id=user, guild_permissions=SimpleNamespace(manage_guild=False)
            )
            it.guild, it.guild_id = guild, GUILD
            it.client = SimpleNamespace(
                campaigns=store,
                recap_plan_facts=AsyncMock(return_value=panel.PlanFacts(True, True, "x")),
            )
            it.response.send_message = AsyncMock()
            it.response.edit_message = AsyncMock()
            it.response.defer = AsyncMock()
            it.followup.send = AsyncMock()
            return it

        view = panel.RecapsPanel(camp, panel.PlanFacts(True, True, "x"))
        stranger = interaction(OTHER)
        await view.save(stranger, "audio", False)
        store.set_recap_switch.assert_not_awaited()
        self.assertEqual(stranger.response.send_message.await_args.args[0], panel.ONLY_DM)
        opener = panel.RecapsButton("c" * 32)
        await opener.callback(stranger)
        stranger.followup.send.assert_not_awaited()
        mine = interaction(DM)
        await view.save(mine, "audio", False)
        store.set_recap_switch.assert_awaited_once_with(GUILD, "c" * 32, "audio", False)
        mine.response.edit_message.assert_awaited_once()
        self.assertTrue(
            mine.response.edit_message.await_args.kwargs["content"].startswith(
                "Saved. No narration and music."
            )
        )
        again = interaction(DM)
        await opener.callback(again)
        again.followup.send.assert_awaited_once()
        self.assertTrue(again.followup.send.await_args.kwargs["ephemeral"])
