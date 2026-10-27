"""The bot's side of an optional rule chosen on the website's Settings tab (#2755, #2753): applied
as Discord's `/dmbot optionalrules` does, checked again at the moment of use (still a DM, the
campaign still there, the rule still applies to its main rules), a plain outcome word for each
refusal, and the choice never logged. Invented names throughout."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from dmbot.campaigns import Campaign, CampaignError
from dmbot.campaigns.models import DM_SCREEN_LEVELS_OFFERED
from dmbot.dm_screen import web_settings as ws
from dmbot.rules import optional
from dmbot.rules.optional import OptionalRule
from tests.test_web_settings import FakeBot, Settings

BOTH = next(r for r in optional.CATALOG if set(r.applies_to) == {"2024", "2014"})
ONLY_2014 = next(r for r in optional.CATALOG if r.applies_to == ("2014",))


class RuleBot(FakeBot):
    """The bot as `apply` sees it. The optional-rule step is the bot's own method
    (`DMBot.apply_optional_rule`), driven on a stand-in, so these tests cannot drift from it; the
    DM screen's posts are recorded."""

    def __init__(self, store: Any, *, posts: bool = True) -> None:
        super().__init__(store)
        self.posted: list[tuple[int, str]] = []
        self.posts = posts

        async def post(channel_id: int, text: str) -> bool:
            self.posted.append((channel_id, text))
            return self.posts

        self._me: Any = SimpleNamespace(campaigns=store, post=post)

    async def apply_optional_rule(
        self, campaign: Campaign, rule: OptionalRule, turn_on: bool, sender_id: int
    ) -> bool:
        from dmbot.bot import DMBot

        self.calls.append(("optional_rule", f"{rule.id}:{turn_on}"))
        return await DMBot.apply_optional_rule(self._me, campaign, rule, turn_on, sender_id)


class _Screen(Settings):
    bot: RuleBot

    async def sql_screen(self, channel: int) -> None:
        async with self.db.guild(self.g) as conn:
            await conn.execute(
                "UPDATE campaigns SET dm_screen_channel_id = %s WHERE guild_id = %s AND id = %s",
                (channel, self.g, self.one.id),
            )


class TheOptionalRule(_Screen):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.bot = RuleBot(self.campaigns)

    async def rule_choice(self, rule_id: str, on: bool, who: Any = None) -> str:
        return await self.queue(who, setting="optional_rule", value=rule_id, on=on)

    async def overrides(self) -> dict[str, bool]:
        return await self.campaigns.optional_rule_overrides(self.g, self.one.id)

    async def test_a_rule_is_switched_on_and_off_and_the_page_gets_the_words(self) -> None:
        on = await self.rule_choice(BOTH.id, True)
        (done,) = await self.run_bot()
        self.assertEqual((done.error, done.note), ("", f"{BOTH.name} is now on."))
        self.assertEqual(await self.overrides(), {BOTH.id: True})
        self.assertEqual((await self.poll(on))["state"], "done")
        off = await self.rule_choice(BOTH.id, False)
        (done,) = await self.run_bot()
        self.assertEqual(done.note, f"{BOTH.name} is now off.")
        self.assertEqual(await self.overrides(), {BOTH.id: False})
        self.assertEqual((await self.poll(off))["note"], f"{BOTH.name} is now off.")

    async def test_the_dm_screen_gets_the_line_discord_posts_without_a_ping(self) -> None:
        await self.sql_screen(555)
        await self.rule_choice(BOTH.id, True)
        await self.run_bot()
        ((channel, text),) = self.bot.posted
        self.assertEqual(channel, 555)
        self.assertEqual(
            text, f"✅ Optional rule turned on: **{BOTH.name}** (by <@{self.session.user_id}>)"
        )

    async def test_the_off_line_on_the_dm_screen_says_off(self) -> None:
        await self.sql_screen(555)
        await self.rule_choice(BOTH.id, False)
        await self.run_bot()
        ((_, text),) = self.bot.posted
        self.assertEqual(
            text, f"⬜ Optional rule turned off: **{BOTH.name}** (by <@{self.session.user_id}>)"
        )

    async def test_an_id_that_is_not_in_the_catalog_is_bad_value(self) -> None:
        # The website refuses it before queueing; the bot checks again at the moment of use.
        item = ws.Setting(
            "x" * 32, self.one.id, self.session.user_id, "optional_rule", "no-such-rule", True
        )
        self.assertEqual((await ws.apply(self.bot, self.g, item)).error, ws.BAD_VALUE)
        self.assertEqual(await self.overrides(), {})
        self.assertEqual(self.bot.calls, [])

    async def test_a_choice_changes_only_its_own_campaign(self) -> None:
        await self.rule_choice(BOTH.id, True)
        await self.run_bot()
        self.assertEqual(await self.overrides(), {BOTH.id: True})
        self.assertEqual(await self.campaigns.optional_rule_overrides(self.g, self.two.id), {})

    async def test_no_dm_screen_is_no_failure(self) -> None:
        await self.rule_choice(BOTH.id, True)
        (done,) = await self.run_bot()
        self.assertEqual(done.error, "")
        self.assertEqual(self.bot.posted, [])

    async def test_a_dm_removed_between_queueing_and_applying_is_not_allowed(self) -> None:
        await self.rule_choice(BOTH.id, True)
        async with self.db.guild(self.g) as conn:
            await conn.execute(
                "DELETE FROM campaign_dms WHERE guild_id = %s AND campaign_id = %s",
                (self.g, self.one.id),
            )
        (done,) = await self.run_bot()
        self.assertEqual(done.error, ws.NOT_ALLOWED)
        self.assertEqual(await self.overrides(), {})

    async def test_a_campaign_gone_by_then_is_not_found(self) -> None:
        await self.rule_choice(BOTH.id, True)

        async def gone(g: int, c: str) -> None:
            return None

        with patch.object(self.bot, "get_campaign", gone):
            (done,) = await self.run_bot()
        self.assertEqual(done.error, ws.NOT_FOUND)

    async def test_a_campaign_that_goes_while_the_rule_is_saved_is_not_found(self) -> None:
        await self.rule_choice(BOTH.id, True)

        async def vanishing(campaign: Campaign, rule: OptionalRule, on: bool, who: int) -> None:
            raise CampaignError("gone")

        with patch.object(self.bot, "apply_optional_rule", vanishing):
            (done,) = await self.run_bot()
        self.assertEqual(done.error, ws.NOT_FOUND)

    async def test_a_rule_that_does_not_apply_to_the_main_rules_is_bad_value(self) -> None:
        assert self.one.target_ruleset == "2024"  # (the default main rules)
        await self.rule_choice(ONLY_2014.id, True)
        (done,) = await self.run_bot()
        self.assertEqual(done.error, ws.BAD_VALUE)
        self.assertEqual(await self.overrides(), {})
        # and it does apply once the campaign's main rules are 2014
        await self.campaigns.set_rulesets(self.g, self.one.id, "2014", "2024")
        await self.rule_choice(ONLY_2014.id, True)
        (done,) = await self.run_bot()
        self.assertEqual(done.error, "")

    async def test_a_row_without_its_direction_is_bad_value(self) -> None:
        item = ws.Setting("x" * 32, self.one.id, self.session.user_id, "optional_rule", BOTH.id)
        self.assertEqual((await ws.apply(self.bot, self.g, item)).error, ws.BAD_VALUE)

    async def test_the_claim_carries_the_direction(self) -> None:
        await self.rule_choice(BOTH.id, False)
        item = await self.items.claim_next(self.g)
        assert item is not None
        self.assertEqual(
            (item.setting, item.value, item.turn_on), ("optional_rule", BOTH.id, False)
        )

    async def test_the_other_settings_have_no_direction(self) -> None:
        await self.queue(setting="level", value=DM_SCREEN_LEVELS_OFFERED[0])
        item = await self.items.claim_next(self.g)
        assert item is not None
        self.assertIsNone(item.turn_on)

    async def test_an_unexpected_error_is_failed_and_nothing_of_the_choice_is_logged(self) -> None:
        async def broken(campaign: Campaign, rule: OptionalRule, on: bool, who: int) -> None:
            raise RuntimeError(f"boom {rule.name}")

        await self.rule_choice(BOTH.id, True)
        item = await self.items.claim_next(self.g)
        assert item is not None
        with (
            patch.object(self.bot, "apply_optional_rule", broken),
            self.assertLogs("dmbot.dm_screen.web_settings", level="INFO") as logged,
        ):
            await ws.run_one(self.bot, self.items, self.g, item)
        text = "\n".join(logged.output)
        self.assertNotIn(BOTH.id, text)
        self.assertNotIn(BOTH.name, text)
        self.assertIn("RuntimeError", text)
        self.assertEqual((await self.poll(str(item.id)))["error"], "failed")


class TheRealBot(_Screen):
    async def test_the_bots_own_step_saves_the_rule_and_posts_the_line_without_a_ping(self) -> None:
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        from dmbot.bot import DMBot

        await self.sql_screen(777)
        campaign = await self.campaign()
        me: Any = SimpleNamespace(campaigns=self.campaigns, post=AsyncMock(return_value=True))
        noted = await DMBot.apply_optional_rule(me, campaign, BOTH, True, 4242)
        self.assertTrue(noted)
        self.assertEqual(
            await self.campaigns.optional_rule_overrides(self.g, self.one.id), {BOTH.id: True}
        )
        me.post.assert_awaited_once_with(
            777, f"✅ Optional rule turned on: **{BOTH.name}** (by <@4242>)"
        )

    async def test_a_campaign_with_no_screen_posts_nothing(self) -> None:
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        from dmbot.bot import DMBot

        me: Any = SimpleNamespace(campaigns=self.campaigns, post=AsyncMock())
        await DMBot.apply_optional_rule(me, await self.campaign(), BOTH, False, 1)
        me.post.assert_not_awaited()

    async def test_a_line_that_cannot_be_posted_is_said_and_the_rule_stays_saved(self) -> None:
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        from dmbot.bot import DMBot

        await self.sql_screen(777)
        campaign = await self.campaign()
        for post in (AsyncMock(return_value=False), AsyncMock(side_effect=RuntimeError("down"))):
            me: Any = SimpleNamespace(campaigns=self.campaigns, post=post)
            self.assertFalse(await DMBot.apply_optional_rule(me, campaign, BOTH, True, 1))
            self.assertEqual(
                await self.campaigns.optional_rule_overrides(self.g, self.one.id), {BOTH.id: True}
            )


class TheMissedLine(_Screen):
    async def test_the_page_is_told_when_the_dm_screen_missed_its_line(self) -> None:
        self.bot = RuleBot(self.campaigns, posts=False)
        await self.sql_screen(555)
        await self.queue(setting="optional_rule", value=BOTH.id, on=True)
        (done,) = await self.run_bot()
        self.assertEqual(done.error, "")
        self.assertEqual(done.note, f"{BOTH.name} is now on. {optional.NO_SCREEN_NOTE}")
        self.assertEqual(
            await self.campaigns.optional_rule_overrides(self.g, self.one.id), {BOTH.id: True}
        )
