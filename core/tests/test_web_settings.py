"""A DM's choices on the website's Settings page (#2463): who can see the DM screen, how much DMbot
says, how long transcripts are kept. Queued by the website's own database role, applied by the bot
the way Discord's ⚙️ Settings buttons apply them, re-checked when applied, and read back only by the
DM who made them. Invented names throughout."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import discord
import httpx
import psycopg.errors

from dmbot import bot as bot_module
from dmbot.bot import DMBot
from dmbot.campaigns import Campaign, CampaignStore
from dmbot.campaigns.models import DM_SCREEN_LEVELS_OFFERED
from dmbot.config import Settings as BotSettings
from dmbot.dm_screen import messages, setup_dm_screen
from dmbot.dm_screen import web_settings as ws
from dmbot.dm_screen.channel import DMScreenError
from dmbot.dm_screen.transcript_keep import saved_text
from dmbot.rules.optional import OptionalRule
from dmbot.web import campaign_settings, sessions
from dmbot.web.app import create_app
from dmbot.web.discord import DiscordGuild
from dmbot.web.payments import FakeProvider
from dmbot.web.recaps import RecapError
from tests.pg import DatabaseTest
from tests.test_dm_screen_setup import (
    BOT,
    BOT_PERMS,
    GENERAL,
    NEW,
    text_channel,
)
from tests.test_recap_web import BOB, HEADERS, NOW, Base, callers
from tests.test_web_api import ALICE, THURSDAY, FakeDiscord, settings


class FakeBot:
    """The running bot as `apply` sees it. The choices go through the real campaign store, as the
    bot's own methods do; Discord's side is recorded."""

    def __init__(self, store: CampaignStore) -> None:
        self.store = store
        self.calls: list[tuple[str, str]] = []

    async def get_campaign(self, guild_id: int, campaign_id: str) -> Campaign | None:
        return await self.store.get(guild_id, campaign_id)

    async def apply_visibility(self, campaign: Campaign, visibility: str) -> str:
        self.calls.append(("visibility", visibility))
        await self.store.set_dm_screen_visibility(campaign.guild_id, campaign.id, visibility)
        return messages.visibility_changed(visibility, was=campaign.dm_screen_visibility)

    async def apply_level(self, campaign: Campaign, level: str) -> Campaign:
        self.calls.append(("level", level))
        return await self.store.set_dm_screen_level(campaign.guild_id, campaign.id, level)

    async def apply_retention(self, campaign: Campaign, days: int | None) -> Campaign:
        self.calls.append(("retention", str(days)))
        return await self.store.set_transcript_keep(campaign.guild_id, campaign.id, days)

    async def apply_optional_rule(
        self, campaign: Campaign, rule: OptionalRule, turn_on: bool, sender_id: int
    ) -> bool:
        self.calls.append(("optional_rule", f"{rule.id}:{turn_on}"))
        await self.store.set_optional_rule(campaign.guild_id, campaign.id, rule.id, turn_on)
        return True

    async def find_member(self, guild_id: int, user_id: int) -> ws.Member | None:
        return ws.Member("Someone", False)

    async def apply_co_dm(
        self, campaign: Campaign, user_id: int, name: str, add: bool, asker_id: int
    ) -> ws.CoDmDone:
        self.calls.append(("co_dm", f"{user_id}:{add}"))
        if add:
            await self.store.add_dm(campaign.guild_id, campaign.id, user_id)
        else:
            await self.store.remove_dm(campaign.guild_id, campaign.id, user_id)
        return ws.CoDmDone()


class Settings(Base):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.g = THURSDAY.id
        self.cref = f"{self.g}.{self.one.id}"
        self.items = ws.SettingStore(self.db)
        self.bot = FakeBot(self.campaigns)

    async def signed(self, user: Any, *guilds: Any) -> Any:
        token = await sessions.sign_in(self.web, user, list(guilds), now=NOW, days=30)
        found = await sessions.find(self.web, token, now=NOW)
        assert found is not None
        return found

    async def queue(self, who: Any = None, ref: str | None = None, **body: Any) -> str:
        return await campaign_settings.queue_setting(
            self.web, who or self.session, ref or self.cref, body
        )

    async def refused(self, who: Any = None, ref: str | None = None, **body: Any) -> str:
        with self.assertRaises(RecapError) as caught:
            await campaign_settings.queue_setting(
                self.web, who or self.session, ref or self.cref, body
            )
        return caught.exception.code

    async def poll(self, answer: str, who: Any = None, ref: str | None = None) -> dict[str, Any]:
        return await campaign_settings.read_setting(
            self.web, who or self.session, ref or self.cref, answer
        )

    async def run_bot(self) -> list[ws.Outcome]:
        """The bot's loop for the queue: claim, apply, finish, until it is empty."""
        done = []
        while (item := await self.items.claim_next(self.g)) is not None:
            outcome = await ws.apply(self.bot, self.g, item)
            await self.items.finish(self.g, item, outcome)
            done.append(outcome)
        return done

    async def campaign(self, which: Campaign | None = None) -> Campaign:
        found = await self.campaigns.get(self.g, (which or self.one).id)
        assert found is not None
        return found


class TheChoices(Settings):
    async def test_visibility_waits_then_changes_what_discord_changes(self) -> None:
        answer = await self.queue(setting="visibility", value="open")
        self.assertEqual((await self.poll(answer))["state"], "waiting")
        self.assertEqual((await self.campaign()).dm_screen_visibility, "peek")  # nothing yet
        self.assertEqual([o.error for o in await self.run_bot()], [""])
        done = await self.poll(answer)
        self.assertEqual((done["state"], done["error"]), ("done", ""))
        self.assertEqual((await self.campaign()).dm_screen_visibility, "open")
        # The DM is told in Discord's own words.
        self.assertEqual(done["note"], messages.visibility_changed("open", was="peek"))

    async def test_how_much_dmbot_says_follows_the_store_call_of_the_discord_button(self) -> None:
        await self.queue(setting="level", value="quiet")
        await self.run_bot()
        self.assertEqual((await self.campaign()).dm_screen_level, "quiet")
        self.assertEqual(self.bot.calls, [("level", "quiet")])

    async def test_a_longer_transcript_limit_saves_at_once_and_says_discords_words(self) -> None:
        await self.campaigns.set_transcript_keep(self.g, self.one.id, 30)
        answer = await self.queue(setting="retention", value="forever")
        await self.run_bot()
        self.assertIsNone((await self.campaign()).transcript_keep_days)
        self.assertEqual((await self.poll(answer))["note"], saved_text(None))

    async def test_a_shorter_limit_needs_the_yes_the_discord_warning_asks_for(self) -> None:
        answer = await self.queue(setting="retention", value="90")  # no yes sent
        self.assertEqual([o.error for o in await self.run_bot()], [ws.NEEDS_CONFIRM])
        self.assertEqual((await self.poll(answer))["error"], ws.NEEDS_CONFIRM)
        self.assertIsNone((await self.campaign()).transcript_keep_days)  # nothing was deleted-to
        self.assertEqual(self.bot.calls, [])
        answer = await self.queue(setting="retention", value=90, confirm=True)  # (a number is ok)
        self.assertEqual([o.error for o in await self.run_bot()], [""])
        self.assertEqual((await self.campaign()).transcript_keep_days, 90)
        self.assertEqual((await self.poll(answer))["note"], saved_text(90))

    async def test_a_choice_that_is_already_set_changes_no_stored_value(self) -> None:
        await self.queue(setting="level", value="normal")
        await self.queue(setting="retention", value="forever")
        await self.run_bot()
        self.assertEqual(self.bot.calls, [])

    async def test_visibility_is_applied_even_when_it_is_the_saved_value(self) -> None:
        """Discord's button does too: it repairs a channel whose permissions drifted."""
        await self.queue(setting="visibility", value="peek")  # (already peek)
        await self.run_bot()
        self.assertEqual(self.bot.calls, [("visibility", "peek")])

    async def test_choices_are_applied_in_the_order_made(self) -> None:
        await self.queue(setting="visibility", value="private")
        await self.queue(setting="visibility", value="open")
        await self.run_bot()
        self.assertEqual(self.bot.calls, [("visibility", "private"), ("visibility", "open")])
        self.assertEqual((await self.campaign()).dm_screen_visibility, "open")

    async def test_the_rules_words_are_kept_for_the_dm_when_discord_would_refuse(self) -> None:
        async def refuse(campaign: Campaign, visibility: str) -> str:
            raise DMScreenError("I can't change that channel's permissions.")

        self.bot.apply_visibility = refuse  # type: ignore[method-assign]
        answer = await self.queue(setting="visibility", value="private")
        await self.run_bot()
        done = await self.poll(answer)
        self.assertEqual((done["state"], done["error"]), ("failed", ws.REFUSED))
        self.assertEqual(done["note"], "I can't change that channel's permissions.")

    async def test_a_bot_failure_is_a_word_the_page_can_retry(self) -> None:
        answer = await self.queue(setting="level", value="quiet")
        item = await self.items.claim_next(self.g)
        assert item is not None
        self.bot.apply_level = AsyncMock(side_effect=RuntimeError("boom"))  # type: ignore[method-assign]
        self.assertTrue(await ws.run_one(self.bot, self.items, self.g, item))
        done = await self.poll(answer)
        self.assertEqual((done["state"], done["error"]), ("failed", ws.FAILED))
        self.assertEqual((await self.campaign()).dm_screen_level, "normal")

    async def test_a_row_that_cannot_be_marked_stops_the_round(self) -> None:
        await self.queue(setting="level", value="quiet")
        item = await self.items.claim_next(self.g)
        assert item is not None
        broken = SimpleNamespace(finish=AsyncMock(side_effect=RuntimeError("db")))
        self.assertFalse(await ws.run_one(self.bot, broken, self.g, item))  # type: ignore[arg-type]

    async def test_the_level_note_is_the_line_the_screen_gets(self) -> None:
        answer = await self.queue(setting="level", value="quiet")
        await self.run_bot()
        self.assertEqual((await self.poll(answer))["note"], messages.level_changed("quiet"))

    async def test_a_later_choice_waits_for_an_earlier_one_being_worked_on(self) -> None:
        await self.queue(setting="visibility", value="private")
        await self.queue(setting="visibility", value="open")
        first = await self.items.claim_next(self.g)
        assert first is not None and first.value == "private"
        self.assertIsNone(await self.items.claim_next(self.g))  # not past a live claim
        await self.items.finish(self.g, first, ws.Outcome())
        second = await self.items.claim_next(self.g)
        assert second is not None and second.value == "open"

    async def test_old_choices_are_swept(self) -> None:
        await self.queue(setting="level", value="quiet")
        late = ws.SettingStore(self.db, clock=lambda: 10**10)
        self.assertEqual(await late.sweep(self.g), 1)
        self.assertEqual(await late.sweep(self.g), 0)

    async def _claim_after(self, **body: Any) -> ws.Setting:
        await self.queue(**body)
        item = await self.items.claim_next(self.g)
        assert item is not None
        return item


class ThroughTheRealBot(Settings):
    """The bot's own loop, not a stand-in: `run_web_settings` claims each queued choice, applies it
    with the bot's real methods and leaves a `done` row, with the stored setting changed."""

    async def test_every_setting_is_applied_by_the_running_bot(self) -> None:
        bot = DMBot(
            BotSettings(discord_token="t", ears_secret="s"),
            SimpleNamespace(),  # type: ignore[arg-type]
            self.campaigns,
            MagicMock(),
        )
        bot.web_settings = self.items

        async def fake_screen(guild: Any, campaign_id: str, store: Any, *, visibility: str) -> Any:
            saved = await store.set_dm_screen_visibility(self.g, campaign_id, visibility)
            return SimpleNamespace(
                channel=MagicMock(send=AsyncMock()), campaign=saved, warning=None
            )

        cast(Any, bot).get_guild = lambda guild_id: SimpleNamespace(id=guild_id)
        bot.after_screen_change = AsyncMock()  # type: ignore[method-assign]
        bot.post = AsyncMock()  # type: ignore[method-assign]
        waiting = {
            "visibility": await self.queue(setting="visibility", value="open"),
            "level": await self.queue(setting="level", value="quiet"),
            "retention": await self.queue(setting="retention", value=30, confirm=True),
        }
        with (
            patch.object(
                DMBot,
                "guilds",
                new_callable=PropertyMock,
                return_value=[SimpleNamespace(id=self.g)],
            ),
            patch.object(bot_module, "setup_dm_screen", fake_screen),
        ):
            await bot.run_web_settings()
        for setting, answer in waiting.items():
            done = await self.poll(answer)
            self.assertEqual((done["state"], done["error"]), ("done", ""), setting)
        saved = await self.campaign()
        self.assertEqual(
            (saved.dm_screen_visibility, saved.dm_screen_level, saved.transcript_keep_days),
            ("open", "quiet", 30),
        )
        bot.after_screen_change.assert_awaited_once()  # a live session catches up, as in Discord


class TheDoor(Settings):
    async def test_words_the_door_does_not_know_are_refused_before_anything_is_queued(self) -> None:
        self.assertEqual(await self.refused(setting="delete", value="yes"), "bad_setting")
        self.assertEqual(await self.refused(setting="visibility", value="chatty"), "bad_value")
        self.assertEqual(await self.refused(setting="level", value="chatty"), "bad_value")
        self.assertEqual(await self.refused(setting="retention", value="7"), "bad_value")
        self.assertEqual(await self.refused(setting="retention", value=True), "bad_value")
        self.assertIsNone(await self.items.claim_next(self.g))

    async def test_the_door_and_the_page_accept_exactly_what_discord_offers(self) -> None:
        offered = {
            "visibility": ws.VISIBILITIES,
            "level": tuple(DM_SCREEN_LEVELS_OFFERED),
            "retention": ws.RETENTION_CHOICES,
        }
        self.assertEqual(campaign_settings.CHOICES, offered)
        async with self.web.user(self.session.user_id, session=self.session.id_hash) as conn:
            await conn.execute("SELECT set_config('dmbot.guild_id', %s, true)", (str(self.g),))
            for setting, values in offered.items():
                for value in values:
                    cur = await conn.execute(
                        "SELECT dmbot_web_setting_add(%s, %s, %s, %s, false) AS r",
                        (self.g, self.one.id, setting, value),
                    )
                    row = await cur.fetchone()
                    assert row is not None
                    self.assertEqual(row["r"]["status"], "ok", (setting, value))

    async def test_the_website_role_cannot_touch_the_table_itself(self) -> None:
        await self.queue(setting="level", value="quiet")
        async with self.web.user(self.session.user_id, session=self.session.id_hash) as conn:
            await conn.execute("SELECT set_config('dmbot.guild_id', %s, true)", (str(self.g),))
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                await conn.execute("SELECT * FROM setting_actions")

    async def test_the_database_door_refuses_what_the_page_check_missed(self) -> None:
        async with self.web.user(self.session.user_id, session=self.session.id_hash) as conn:
            await conn.execute("SELECT set_config('dmbot.guild_id', %s, true)", (str(self.g),))
            for setting, value in (("visibility", "chatty"), ("pause", "on")):
                cur = await conn.execute(
                    "SELECT dmbot_web_setting_add(%s, %s, %s, %s, false) AS r",
                    (self.g, self.one.id, setting, value),
                )
                row = await cur.fetchone()
                assert row is not None
                self.assertIn(row["r"]["status"], ("bad_setting", "bad_value"))

    async def test_only_the_campaigns_own_dm_can_queue_or_read(self) -> None:
        bob = await self.signed(BOB, THURSDAY)  # DMs Two, not One
        self.assertEqual(await self.refused(bob, setting="level", value="quiet"), "not_allowed")
        manager = await self.signed(BOB, DiscordGuild(id=self.g, name="T", manage=True))
        self.assertEqual(await self.refused(manager, setting="level", value="quiet"), "not_allowed")
        outsider = await self.signed(BOB, DiscordGuild(id=999, name="E", manage=True))
        self.assertEqual(await self.refused(outsider, setting="level", value="quiet"), "not_found")
        self.assertIsNone(await self.items.claim_next(self.g))  # none of them queued anything
        answer = await self.queue(setting="level", value="quiet")
        with self.assertRaises(RecapError) as caught:
            await self.poll(answer, bob)
        self.assertEqual(caught.exception.code, "not_found")

    async def test_a_dm_cannot_use_another_campaigns_page_or_read_its_answers(self) -> None:
        two_ref = f"{self.g}.{self.two.id}"
        self.assertEqual(
            await self.refused(self.session, two_ref, setting="level", value="quiet"),
            "not_allowed",
        )
        bob = await self.signed(BOB, THURSDAY)
        theirs = await self.queue(bob, two_ref, setting="level", value="quiet")
        with self.assertRaises(RecapError) as caught:  # Alice, DM of One, reading Bob's answer
            await self.poll(theirs, self.session, two_ref)
        self.assertEqual(caught.exception.code, "not_found")
        with self.assertRaises(RecapError) as caught:  # through her own campaign's page
            await self.poll(theirs)
        self.assertEqual(caught.exception.code, "not_found")

    async def test_a_change_for_one_campaign_never_touches_another(self) -> None:
        await self.queue(setting="level", value="quiet")
        await self.run_bot()
        self.assertEqual((await self.campaign()).dm_screen_level, "quiet")
        self.assertEqual((await self.campaign(self.two)).dm_screen_level, "normal")

    async def test_the_queue_is_capped_per_campaign(self) -> None:
        for _ in range(10):
            await self.queue(setting="level", value="quiet")
        self.assertEqual(await self.refused(setting="level", value="normal"), "too_soon")


class TheBotChecksAgain(Settings):
    async def test_a_dm_who_handed_over_before_it_was_applied_changes_nothing(self) -> None:
        await self.queue(setting="level", value="quiet")
        async with self.db.guild(self.g) as conn:  # Alice is no longer a DM when the bot gets to it
            await conn.execute(
                "UPDATE campaign_dms SET user_id = %s WHERE guild_id = %s AND campaign_id = %s",
                (BOB.id, self.g, self.one.id),
            )
        self.assertEqual([o.error for o in await self.run_bot()], [ws.NOT_ALLOWED])
        self.assertEqual((await self.campaign()).dm_screen_level, "normal")
        self.assertEqual(self.bot.calls, [])

    async def test_a_campaign_deleted_meanwhile_is_a_word_not_a_crash(self) -> None:
        item = ws.Setting("a" * 32, "0" * 32, ALICE.id, "level", "quiet")
        self.assertEqual((await ws.apply(self.bot, self.g, item)).error, ws.NOT_FOUND)

    async def test_a_value_discord_does_not_offer_is_refused_even_if_queued_directly(self) -> None:
        item = ws.Setting("a" * 32, self.one.id, ALICE.id, "level", "chatty")
        self.assertEqual((await ws.apply(self.bot, self.g, item)).error, ws.BAD_VALUE)
        item = ws.Setting("a" * 32, self.one.id, ALICE.id, "delete", "yes")
        self.assertEqual((await ws.apply(self.bot, self.g, item)).error, ws.BAD_VALUE)

    async def test_a_stalled_claim_is_taken_again_and_only_the_holder_can_finish(self) -> None:
        await self.queue(setting="level", value="quiet")
        first = await self.items.claim_next(self.g)
        assert first is not None
        self.assertIsNone(await self.items.claim_next(self.g))  # being worked on
        late = ws.SettingStore(self.db, clock=lambda: 10**10)  # a bot long after the first stalled
        second = await late.claim_next(self.g)
        assert second is not None
        self.assertFalse(await self.items.finish(self.g, first, ws.Outcome()))
        self.assertTrue(await late.finish(self.g, second, ws.Outcome()))


class VisibilityOnDiscord(DatabaseTest):
    """Visibility through the real `setup_dm_screen`, with a fake Discord server: the same
    permissions as the Discord button gives, and the same warning."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.store = CampaignStore(self.db)
        self.campaign = await self.store.create(1, "Frostmaiden", 3)
        me = MagicMock(spec=discord.Member)
        me.id = BOT
        me.guild_permissions = BOT_PERMS
        guild = MagicMock(spec=discord.Guild)
        guild.id = 1
        guild.me = me
        guild.channels = []
        guild.get_role = lambda _id: None
        guild.get_member = lambda _id: None
        self.general = text_channel(GENERAL, "general", guild)
        self.new = text_channel(NEW, "dmb-dm-screen-frostmaiden", guild)
        guild.fetch_channel = AsyncMock(return_value=self.general)
        guild.create_text_channel = AsyncMock(return_value=self.new)
        guild.get_channel = lambda cid: self.new if cid == NEW else None
        self.guild = guild
        self.posted: list[str] = []

    async def test_the_website_choice_applies_the_channel_permissions_like_the_button(self) -> None:
        class Bot(FakeBot):
            async def apply_visibility(inner, campaign: Campaign, visibility: str) -> str:
                result = await setup_dm_screen(
                    self.guild, campaign.id, self.store, visibility=visibility
                )
                return messages.visibility_changed(
                    visibility, was=campaign.dm_screen_visibility
                ) + (("\n" + result.warning) if result.warning else "")

        bot = Bot(self.store)
        await self.store.set_dm_screen(1, self.campaign.id, NEW)  # the screen exists
        self.guild.fetch_channel = AsyncMock(return_value=self.new)
        item = ws.Setting("a" * 32, self.campaign.id, 3, "visibility", "open")
        outcome = await ws.apply(bot, 1, item)
        self.assertEqual(outcome.error, "")
        saved = await self.store.get(1, self.campaign.id)
        assert saved is not None
        self.assertEqual(saved.dm_screen_visibility, "open")
        self.assertTrue(self.new.edit.await_count)  # the channel's permissions were rewritten
        overwrites = self.new.edit.call_args.kwargs["overwrites"]
        everyone = next(ow for key, ow in overwrites.items() if key.id == 1)
        self.assertTrue(everyone.view_channel)  # open: the server can look
        self.assertIn("Done.", outcome.note)

    async def test_a_screen_that_cannot_be_changed_gives_the_dm_discords_words(self) -> None:
        class Bot(FakeBot):
            async def apply_visibility(inner, campaign: Campaign, visibility: str) -> str:
                raise DMScreenError(messages.FORBIDDEN_HERE)

        item = ws.Setting("a" * 32, self.campaign.id, 3, "visibility", "open")
        outcome = await ws.apply(Bot(self.store), 1, item)
        self.assertEqual((outcome.error, outcome.note), (ws.REFUSED, messages.FORBIDDEN_HERE))


class TheRoutes(Settings):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.app = create_app(
            settings(), self.web, FakeDiscord(), payments=FakeProvider(b"h" * 32, "https://s.example"),
            clock=lambda: NOW,
        )  # fmt: skip
        token = await sessions.sign_in(self.web, ALICE, [THURSDAY], now=NOW, days=30)
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app),
            base_url="https://web-api.internal.test",
            follow_redirects=False,
        )
        self.client.cookies.set("__Host-dmbot_session", token, domain="web-api.internal.test")
        self.addAsyncCleanup(self.client.aclose)

    async def test_post_then_poll_over_http(self) -> None:
        url = f"/me/campaigns/{self.cref}/settings"
        sent = await self.client.post(
            url, json={"setting": "level", "value": "quiet"}, headers=HEADERS
        )
        self.assertEqual(sent.status_code, 202)
        answer = sent.json()["answer"]
        waiting = await self.client.get(f"{url}/answers/{answer}")
        self.assertEqual((waiting.status_code, waiting.json()["state"]), (200, "waiting"))
        await self.run_bot()
        done = await self.client.get(f"{url}/answers/{answer}")
        self.assertEqual((done.json()["state"], done.json()["error"]), ("done", ""))
        self.assertEqual((await self.campaign()).dm_screen_level, "quiet")

    async def test_refusals_over_http(self) -> None:
        url = f"/me/campaigns/{self.cref}/settings"
        for body, status, word in (
            ({"setting": "delete", "value": "yes"}, 400, "bad_setting"),
            ({"setting": "level", "value": "chatty"}, 400, "bad_value"),
            ({}, 400, "bad_setting"),
        ):
            got = await self.client.post(url, json=body, headers=HEADERS)
            self.assertEqual((got.status_code, got.json()), (status, {"error": word}))
        other = f"/me/campaigns/{self.g}.{self.two.id}/settings"  # Bob's campaign
        got = await self.client.post(
            other, json={"setting": "level", "value": "quiet"}, headers=HEADERS
        )
        self.assertEqual((got.status_code, got.json()), (403, {"error": "not_allowed"}))
        anonymous = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app),
            base_url="https://web-api.internal.test",
            follow_redirects=False,
        )
        self.addAsyncCleanup(anonymous.aclose)
        self.assertEqual((await anonymous.post(url, json={}, headers=HEADERS)).status_code, 401)


class TheDoors(Settings):
    async def test_the_doors_have_one_caller(self) -> None:
        for door in ("dmbot_web_setting_add(", "dmbot_web_setting_read("):
            self.assertEqual(callers(door), {"schema.py", "web/campaign_settings.py"})
