"""AI and copy rules by plan (#437 part 3, #919): the pure rules for every kind of access, the
bot's gate over a real database, and each button that spends tokens or hands out a copy
refused with enforcement on and allowed with it off. The database tests need Postgres
(skipped without it, like the other store tests)."""

from __future__ import annotations

import time
import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

from dmbot import entitlements, plan_rules
from dmbot.bot import DMBot
from dmbot.campaigns.store import encode_backup
from dmbot.config import Settings
from dmbot.consent import ConsentStore
from dmbot.entitlements import Access
from dmbot.sessions import SessionStore
from dmbot.ui import dmbot_commands as cmds
from dmbot.ui import logic, name_lists, transcripts
from dmbot.usage import Meter
from tests import test_usage as base
from tests.test_dmbot_commands import attachment, fake_interaction

SITE = "https://dmbot.example"
FREE = Access("free", "Free access", None, None, True)
GRANT = Access("grant", "Free access", None, None, True)
TABLE = Access("paid", "Table", 18 * 60, 1, True)
TRY_IT = Access("paid", "Try It", 8 * 60, 1, False)  # plans.json: Try It has no backups
ENDED = entitlements.NO_ACCESS


class Rules(unittest.TestCase):
    def test_ai_runs_for_every_working_plan(self) -> None:
        for access in (FREE, GRANT, TABLE, TRY_IT):
            self.assertTrue(plan_rules.can_use_ai(access), access.kind)

    def test_ai_is_off_for_an_ended_plan_or_no_owner(self) -> None:
        self.assertFalse(plan_rules.can_use_ai(ENDED))
        self.assertFalse(plan_rules.can_use_ai(None))

    def test_copies_come_with_paid_plans_grants_and_the_free_list(self) -> None:
        for access in (FREE, GRANT, TABLE):
            self.assertTrue(plan_rules.can_backup(access), access.kind)

    def test_try_it_an_ended_plan_and_no_owner_have_no_copies(self) -> None:
        for access in (TRY_IT, ENDED, None):
            self.assertFalse(plan_rules.can_backup(access))

    def test_allowed_is_the_matching_rule(self) -> None:
        self.assertTrue(plan_rules.allowed("ai", TRY_IT))
        self.assertFalse(plan_rules.allowed("backup", TRY_IT))

    def test_nothing_is_said_when_it_may_go_ahead(self) -> None:
        self.assertIsNone(plan_rules.refusal("ai", TABLE, is_owner=True))
        self.assertIsNone(plan_rules.refusal("backup", FREE, is_owner=False))

    def test_the_owner_of_an_ended_plan_is_told_to_pick_one(self) -> None:
        for rule in ("ai", "backup"):
            self.assertEqual(
                plan_rules.refusal(rule, ENDED, is_owner=True, site_url=SITE),
                f"Your plan has ended. Pick one here: {SITE}/account",
            )

    def test_a_try_it_owner_is_told_copies_need_a_paid_plan(self) -> None:
        self.assertEqual(
            plan_rules.refusal("backup", TRY_IT, is_owner=True, site_url=SITE),
            f"Copies and transcripts come with a paid plan. Pick one here: {SITE}/account",
        )

    def test_words_still_work_before_the_site_address_is_set(self) -> None:
        text = plan_rules.refusal("backup", TRY_IT, is_owner=True) or ""
        self.assertTrue(text.endswith("on DMbot's website"))

    def test_anyone_else_is_told_to_ask_the_owner_and_nothing_about_the_plan(self) -> None:
        self.assertEqual(
            plan_rules.refusal("ai", ENDED, is_owner=False, site_url=SITE), plan_rules.ASK_OWNER_AI
        )
        self.assertEqual(
            plan_rules.refusal("backup", TRY_IT, is_owner=False, site_url=SITE),
            plan_rules.ASK_OWNER_BACKUP,
        )
        for text in (plan_rules.ASK_OWNER_AI, plan_rules.ASK_OWNER_BACKUP):
            self.assertNotIn("plan", text)
            self.assertNotIn("Try It", text)

    def test_a_campaign_with_no_owner_asks_for_a_dm_to_take_it_on(self) -> None:
        for is_owner in (True, False):
            self.assertEqual(
                plan_rules.refusal("ai", None, is_owner=is_owner, owner_known=False),
                plan_rules.NO_OWNER,
            )


class Gate(base.UsageTest):
    """The bot's gate: the campaign's owner's plan decides, for everyone at the table."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.bot = self.make_bot(enforce=True)

    def make_bot(self, *, enforce: bool) -> DMBot:
        return DMBot(
            Settings(discord_token="t", ears_secret="s", enforce_plans=enforce, site_url=SITE),
            ConsentStore(self.db),
            self.campaigns,
            SessionStore(self.db),
            meter=Meter(self.db),
        )

    async def plan(self, owner: int = base.OWNER, **values: object) -> None:
        await self.give_plan(
            owner, period_start=base.NOW - 1000, period_end=int(time.time()) + 5000, **values
        )

    async def gate(self, rule: Any, user: int = base.OWNER, bot: DMBot | None = None) -> str | None:
        campaign = await self.campaigns.get(base.GUILD_A, self.a.id)
        assert campaign is not None
        return await (bot or self.bot).plan_gate(rule, base.GUILD_A, campaign, user)

    async def test_off_by_default_nothing_is_refused(self) -> None:
        off = self.make_bot(enforce=False)
        self.assertIsNone(await self.gate("ai", bot=off))
        self.assertIsNone(await self.gate("backup", bot=off))
        self.assertIsNone(await off.restore_gate(base.GUILD_A, base.OTHER))

    async def test_a_paid_owner_may_use_the_ai_and_make_copies(self) -> None:
        await self.plan()
        self.assertIsNone(await self.gate("ai"))
        self.assertIsNone(await self.gate("backup"))

    async def test_a_try_it_owner_may_use_the_ai_but_is_refused_a_copy(self) -> None:
        await self.plan(plan="try-it")
        self.assertIsNone(await self.gate("ai"))
        self.assertEqual(
            await self.gate("backup"),
            f"Copies and transcripts come with a paid plan. Pick one here: {SITE}/account",
        )

    async def test_an_ended_plan_is_refused_both(self) -> None:
        self.assertEqual(
            await self.gate("ai"), f"Your plan has ended. Pick one here: {SITE}/account"
        )
        self.assertEqual(
            await self.gate("backup"), f"Your plan has ended. Pick one here: {SITE}/account"
        )

    async def test_a_co_dm_is_shown_no_reason(self) -> None:
        await self.plan(plan="try-it")
        text = await self.gate("backup", user=base.NEW_OWNER)
        self.assertEqual(text, plan_rules.ASK_OWNER_BACKUP)

    async def test_the_free_list_and_a_grant_may_do_both(self) -> None:
        entitlements.configure_free_users([base.OWNER])
        self.assertIsNone(await self.gate("ai"))
        self.assertIsNone(await self.gate("backup"))

    async def test_a_campaign_with_no_owner_is_refused_both(self) -> None:
        async with self.db.guild(base.GUILD_A) as conn:
            await conn.execute(
                "UPDATE campaigns SET owner_user_id = NULL WHERE id = %s", (self.a.id,)
            )
        self.assertEqual(await self.gate("ai"), plan_rules.NO_OWNER)
        self.assertEqual(await self.gate("backup", user=base.NEW_OWNER), plan_rules.NO_OWNER)

    async def test_a_database_failure_lets_it_through(self) -> None:
        broken = patch.object(Meter, "access", side_effect=RuntimeError("down"))
        with broken, self.assertLogs("dmbot.bot", "ERROR"):
            self.assertIsNone(await self.gate("ai"))
            self.assertIsNone(await self.bot.restore_gate(base.GUILD_A, base.OWNER))

    async def test_restoring_needs_the_restorers_own_plan_to_include_copies(self) -> None:
        # The restorer becomes the owner, so it is their plan that counts, not the old one's.
        self.assertEqual(
            await self.bot.restore_gate(base.GUILD_A, base.OTHER),
            f"Your plan has ended. Pick one here: {SITE}/account",
        )
        await self.plan(base.OTHER, plan="try-it")
        self.assertEqual(
            await self.bot.restore_gate(base.GUILD_A, base.OTHER),
            f"Copies and transcripts come with a paid plan. Pick one here: {SITE}/account",
        )
        await self.plan(base.NEW_OWNER)
        self.assertIsNone(await self.bot.restore_gate(base.GUILD_A, base.NEW_OWNER))


class Buttons(base.UsageTest):
    """Each place that spends tokens or hands out a copy, with enforcement on and off."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.bot = self.make_bot(enforce=True)

    def make_bot(self, *, enforce: bool) -> DMBot:
        bot = DMBot(
            Settings(discord_token="t", ears_secret="s", enforce_plans=enforce, site_url=SITE),
            ConsentStore(self.db),
            self.campaigns,
            SessionStore(self.db),
            meter=Meter(self.db),
        )
        bot.ai = SimpleNamespace(complete=AsyncMock())  # type: ignore[assignment]
        return bot

    def it(self, bot: DMBot, user: int = base.OWNER) -> Any:
        it = fake_interaction(bot, user)
        it.guild = SimpleNamespace(id=base.GUILD_A, filesize_limit=10 * 1024 * 1024)
        it.guild_id = base.GUILD_A
        it.response.edit_message = AsyncMock()
        return it

    async def paid(self, owner: int = base.OWNER, **values: object) -> None:
        await self.give_plan(
            owner, period_start=base.NOW - 1000, period_end=int(time.time()) + 5000, **values
        )

    def said(self, it: Any) -> str:
        return str(it.response.sent[0][0])

    # -- the copy -------------------------------------------------------------------------

    async def test_a_try_it_owner_is_refused_a_copy_and_nothing_is_made(self) -> None:
        await self.paid(plan="try-it")
        it = self.it(self.bot)
        with patch.object(self.campaigns, "export", new=AsyncMock()) as export:
            await cmds.send_backup(it, self.a.id)
        self.assertIn("Copies and transcripts come with a paid plan", self.said(it))
        export.assert_not_awaited()
        it.followup.send.assert_not_awaited()

    async def test_a_player_is_told_to_ask_the_owner(self) -> None:
        await self.paid(plan="try-it")
        it = self.it(self.bot, base.OTHER)
        await cmds.send_backup(it, self.a.id)
        self.assertEqual(self.said(it), plan_rules.ASK_OWNER_BACKUP)

    async def test_a_paid_campaign_is_downloaded_by_anyone(self) -> None:
        await self.paid()
        it = self.it(self.bot, base.OTHER)
        await cmds.send_backup(it, self.a.id)
        self.assertTrue(it.followup.send.await_args.kwargs["file"].filename.endswith(".gz"))

    async def test_enforcement_off_downloads_a_try_it_copy_as_before(self) -> None:
        await self.paid(plan="try-it")
        it = self.it(self.make_bot(enforce=False))
        await cmds.send_backup(it, self.a.id)
        self.assertIn("file", it.followup.send.await_args.kwargs)

    # -- restoring a copy -----------------------------------------------------------------

    async def backup_file(self) -> Any:
        return attachment(encode_backup(await self.campaigns.export(base.GUILD_A, self.a.id)))

    async def test_restore_is_refused_to_someone_without_a_plan_with_copies(self) -> None:
        file = await self.backup_file()
        it = self.it(self.bot, base.OTHER)
        await cmds.dmbot_restore.callback(it, file)  # type: ignore[call-arg]
        self.assertIn("Your plan has ended", it.followup.send.await_args.args[0])
        file.read.assert_not_awaited()  # the file isn't even downloaded

    async def test_restore_goes_ahead_for_a_paid_restorer(self) -> None:
        await self.paid(base.OTHER)
        it = self.it(self.bot, base.OTHER)
        await cmds.dmbot_restore.callback(it, await self.backup_file())  # type: ignore[call-arg]
        self.assertIsInstance(it.followup.send.await_args.kwargs["view"], cmds.RestoreChoice)

    async def test_restore_is_asked_again_when_the_button_is_pressed(self) -> None:
        # Their plan ended while the buttons waited on screen.
        data = await self.campaigns.export(base.GUILD_A, self.a.id)
        choice = cmds.RestoreChoice(data, "Frostmaiden", [])
        it = self.it(self.bot, base.OTHER)
        it.edit_original_response = AsyncMock()
        with patch.object(self.campaigns, "import_backup", new=AsyncMock()) as restore:
            await choice.restore(it, None)
        restore.assert_not_awaited()
        self.assertIn("Your plan has ended", it.response.edit_message.await_args.kwargs["content"])

    async def test_enforcement_off_restores_for_anyone_as_before(self) -> None:
        it = self.it(self.make_bot(enforce=False), base.OTHER)
        await cmds.dmbot_restore.callback(it, await self.backup_file())  # type: ignore[call-arg]
        self.assertIsInstance(it.followup.send.await_args.kwargs["view"], cmds.RestoreChoice)

    # -- the transcript -------------------------------------------------------------------

    def transcript_bot(self, gate: str | None) -> Any:
        session = SimpleNamespace(id="s" * 32, campaign_id=self.a.id)
        store = SimpleNamespace(session=AsyncMock(return_value=session), lines=AsyncMock())
        campaign = SimpleNamespace(id=self.a.id, name="Frostmaiden")
        return SimpleNamespace(
            transcripts=store,
            campaigns=SimpleNamespace(get=AsyncMock(return_value=campaign)),
            plan_gate=AsyncMock(return_value=gate),
            tables={},
        )

    async def test_a_transcript_download_is_refused_with_the_reason(self) -> None:
        bot = self.transcript_bot("Ask the owner.")
        got = await transcripts.make_file(bot, None, base.GUILD_A, "s" * 32, base.OTHER)
        self.assertEqual(got, "Ask the owner.")
        bot.transcripts.lines.assert_not_awaited()  # nothing is read or built
        self.assertEqual(bot.plan_gate.await_args.args[0], "backup")
        self.assertEqual(bot.plan_gate.await_args.args[1], base.GUILD_A)
        self.assertEqual(bot.plan_gate.await_args.args[3], base.OTHER)

    async def test_the_real_gate_refuses_a_try_it_campaigns_transcript(self) -> None:
        await self.paid(plan="try-it")
        bot = self.transcript_bot(None)
        bot.plan_gate = self.bot.plan_gate
        bot.transcripts.session = AsyncMock(
            return_value=SimpleNamespace(id="s" * 32, campaign_id=self.a.id)
        )
        bot.campaigns.get = self.campaigns.get
        got = await transcripts.make_file(bot, None, base.GUILD_A, "s" * 32, base.OTHER)
        self.assertEqual(got, plan_rules.ASK_OWNER_BACKUP)

    # -- the AI ---------------------------------------------------------------------------

    async def test_find_names_is_refused_to_an_ended_plan_and_nothing_is_confirmed(self) -> None:
        offer = name_lists.AIOffer(
            self.a.id, name_lists.Upload("Some names", "list.txt", True), 0, False
        )
        it = self.it(self.bot)
        with patch.object(self.campaigns, "record_confirmation", new=AsyncMock()) as confirm:
            await offer._read(it)
        self.assertIn("Your plan has ended", self.said(it))
        confirm.assert_not_awaited()  # no right-to-use record for something that never ran
        self.bot.ai.complete.assert_not_awaited()  # type: ignore[union-attr]
        self.assertFalse(offer.is_finished())  # the menu stays, so the lines that fit can go in

    async def test_a_player_pressing_find_names_never_hears_about_the_plan(self) -> None:
        # The access check comes first: someone who may not manage names gets that answer,
        # not a message about the owner's plan.
        offer = name_lists.AIOffer(
            self.a.id, name_lists.Upload("Some names", "list.txt", True), 0, False
        )
        it = self.it(self.bot, base.OTHER)
        await offer._read(it)
        self.assertEqual(self.said(it), logic.NO_CAMPAIGN_ACCESS)

    async def test_find_names_is_not_refused_to_try_it(self) -> None:
        await self.paid(plan="try-it")
        offer = name_lists.AIOffer(
            self.a.id, name_lists.Upload("Some names", "list.txt", True), 0, False
        )
        it = self.it(self.bot)
        with (
            patch.object(name_lists, "ai_names_list", new=AsyncMock(return_value=("Auril", False))),
            patch.object(name_lists, "show_ai_list", new=AsyncMock()) as shown,
        ):
            await offer._read(it)
        shown.assert_awaited_once()

    async def test_enforcement_off_reads_for_anyone_as_before(self) -> None:
        offer = name_lists.AIOffer(
            self.a.id, name_lists.Upload("Some names", "list.txt", True), 0, False
        )
        it = self.it(self.make_bot(enforce=False))
        with (
            patch.object(name_lists, "ai_names_list", new=AsyncMock(return_value=("Auril", False))),
            patch.object(name_lists, "show_ai_list", new=AsyncMock()) as shown,
        ):
            await offer._read(it)
        shown.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
