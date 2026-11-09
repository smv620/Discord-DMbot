"""Names a recap uses that DMbot's list doesn't have (#2027): a near miss is respelled, a name said
in the session is kept and queued for the DM, an invented one fails, a secret one is never matched,
queued or printed. The names show in the DM's copy only, never in the narration or the table post,
and the DM screen hears of them once."""

from __future__ import annotations

import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import ANY, AsyncMock, patch

from dmbot.bot import DMBot
from dmbot.campaigns.store import CampaignStore
from dmbot.memory.models import MemoryRuleError
from dmbot.recaps import draft as rd
from dmbot.recaps import post
from dmbot.recaps import unknown_names as un
from dmbot.recaps.audio import script
from dmbot.recaps.store import RecapStore
from dmbot.recaps.text import words
from tests import recap_fixtures as fx
from tests.pg import DatabaseTest
from tests.test_recap_jobs import Clock
from tests.test_recaps import TRANSCRIPT, beats

GUILD = 1
DM_ID = 4242
CID = "c" * 32
NAMES = [*fx.NAMES, "Caer-Dineval"]


def respelled(text: str, **kw: Any) -> un.Respelled:
    args: dict[str, Any] = {
        "names": NAMES,
        "secret_names": [],
        "known_words": {w for n in NAMES for w in words(n)},
    }
    return un.respell(text, **{**args, **kw})


class TheNearMiss(unittest.TestCase):
    def test_a_misheard_name_is_put_right_with_and_without_a_possessive(self) -> None:
        got = respelled("《Tense》 They reached Care Dinval at dusk. Care Dinval's gate was shut.")
        self.assertEqual(
            got.text, "《Tense》 They reached Caer-Dineval at dusk. Caer-Dineval's gate was shut."
        )
        self.assertEqual(got.refused, ())

    def test_ordinary_words_are_not_turned_into_names(self) -> None:
        text = "Brave men fought. Stone fell. The Guard waited. Marsh was cold. Which way?"
        got = respelled(
            text,
            names=["Brand", "Stoned", "Guardo", "Marshal", "Whitch"],
            known_words=set(),
            ordinary_words={"brave", "men", "stone", "guard", "marsh", "which", "way"},
        )
        self.assertEqual(got.text, text)
        # a short name only matches one letter away
        self.assertEqual(respelled("Mirra spoke.", names=["Mirael"]).text, "Mirra spoke.")

    def test_a_backslash_in_a_name_is_just_a_letter(self) -> None:
        got = respelled("They reached Care Dinval.", names=["Caer-Dineval\\"])
        self.assertEqual(got.text, "They reached Caer-Dineval\\.")

    def test_a_name_that_is_not_close_is_left_alone(self) -> None:
        for text in ("They reached Harrowmoor.", "They reached Care Dunmore.", "Dinval Care came."):
            self.assertEqual(respelled(text).text, text, text)

    def test_the_first_letter_and_the_word_count_must_match(self) -> None:
        self.assertEqual(respelled("They reached Baer Dineval.").text, "They reached Baer Dineval.")
        self.assertEqual(respelled("They reached Caerdineval.").text, "They reached Caerdineval.")

    def test_a_known_name_is_never_changed(self) -> None:
        self.assertEqual(respelled("Mira and Oskar spoke.").text, "Mira and Oskar spoke.")

    def test_a_secret_name_is_never_matched_and_its_near_miss_is_refused(self) -> None:
        got = respelled("They feared Vaelithar.", secret_names=["Vaelithor"])
        self.assertEqual(got.text, "They feared Vaelithar.")
        self.assertEqual(got.refused, ("vaelithar",))
        # closer to the secret than to a known name: refused, not respelled to the known one
        got = respelled(
            "Care Dinvel rose.",
            secret_names=["Caer-Dinvel"],
        )
        self.assertEqual(got.text, "Care Dinvel rose.")
        self.assertTrue(got.refused)

    def test_the_refused_word_fails_the_draft_without_printing_it(self) -> None:
        prose = rd.Prose(fx.SUMMARY.replace("Nothing answered.", "Vaelithar waited."), ("m",))
        problems = rd.check_prose(
            prose,
            beats=beats(),
            transcript=TRANSCRIPT + " Vaelithar",  # said in the session, and still refused
            allowed_names=fx.NAMES,
            secret_names=["Vaelithor"],
            length=rd.length_for(3.5),
            refused=("vaelithar",),
        )
        self.assertTrue(any("too like one the DM keeps secret" in p for p in problems), problems)
        self.assertNotIn("Vaelithor", " ".join(problems))


class TheKeptNames(unittest.TestCase):
    def kept(self, text: str, transcript: str = TRANSCRIPT, **kw: Any) -> list[str]:
        args: dict[str, Any] = {
            "beats": beats(),
            "transcript": transcript,
            "allowed_names": fx.NAMES,
            "secret_names": [],
        }
        return rd.kept_names(text, **{**args, **kw})

    def test_a_name_said_in_the_session_is_kept_and_a_made_up_one_is_not(self) -> None:
        said = TRANSCRIPT + " we reached Zarathon and Kel-Marrow"
        text = "They reached Zarathon. Kel-Marrow's gate was shut. Yrsa fled."
        self.assertEqual(self.kept(text, said), ["Zarathon", "Kel-Marrow"])

    def test_a_stretch_of_kept_words_is_one_name(self) -> None:
        said = TRANSCRIPT + " Lady Brannock"
        self.assertEqual(self.kept("Then Lady Brannock spoke.", said), ["Lady Brannock"])

    def test_known_names_and_ordinary_openers_are_not_kept(self) -> None:
        self.assertEqual(self.kept("Mira drew her blade. Steel rang out."), [])

    def test_a_secret_name_is_never_kept(self) -> None:
        said = TRANSCRIPT + " Vaelithor"
        self.assertEqual(self.kept("Vaelithor waited.", said, secret_names=["Vaelithor"]), [])

    def test_the_draft_passes_the_check_when_the_name_was_said(self) -> None:
        text = fx.SUMMARY.replace("Nothing answered.", "They reached Zarathon.")
        said = TRANSCRIPT + " Zarathon"

        def problems(transcript: str) -> list[str]:
            return rd.check_prose(
                rd.Prose(text, tuple(fx.MUSIC.splitlines())[:2]),
                beats=beats(),
                transcript=transcript,
                allowed_names=fx.NAMES,
                secret_names=[],
                length=rd.length_for(3.5),
            )

        self.assertFalse(any("names that are not allowed" in p for p in problems(said)))
        self.assertTrue(any("Zarathon" in p for p in problems(TRANSCRIPT)))  # invented: fails

    def test_the_list_holds_ten_names_once_each(self) -> None:
        names = [f"Place{chr(97 + i)}" for i in range(14)]
        got = un.to_confirm(names[:3], ["place a", *names])
        self.assertEqual(len(got), un.MAX_TO_CONFIRM)
        self.assertEqual(got[:3], names[:3])
        self.assertEqual(len(set(map(str.casefold, got))), len(got))


class TheDMsCopyOnly(unittest.TestCase):
    def setUp(self) -> None:
        from tests.test_recaps import WhatTheTableReads

        self.job = replace(
            WhatTheTableReads().job(), to_confirm=("Zarathon Keep",), housekeeping=("Mira ran.",)
        )

    def test_the_table_post_and_the_dm_screen_reading_never_hold_the_names(self) -> None:
        self.assertNotIn("Zarathon", post.to_the_table(self.job))
        self.assertNotIn("Zarathon", post.to_the_table(self.job, footer=False))

    def test_the_narration_never_holds_the_names(self) -> None:
        built = script.build(self.job.title, self.job.game_day, self.job.summary)
        self.assertNotIn("Zarathon", built.spoken)

    def test_only_the_dms_pages_read_the_list(self) -> None:
        root = Path(__file__).resolve().parents[1] / "src" / "dmbot"
        for relative in ("recaps/post.py", "recaps/audio", "recaps/notify.py"):
            path = root / relative
            for file in [path] if path.is_file() else sorted(path.rglob("*.py")):
                self.assertNotIn("to_confirm", file.read_text(encoding="utf-8"), str(file))


class TheTestRunFiles(unittest.TestCase):
    def test_recap_txt_and_the_review_page_end_with_the_names_and_read_back(self) -> None:
        import tempfile

        from dmbot.devtools.recap_run import review as rv

        text = rv.recap_text(
            "The Gate", fx.SUMMARY, ["Mira ran."], ["m"], voice_tags=True,
            to_confirm=["Zarathon Keep"],
        )  # fmt: skip
        self.assertTrue(text.rstrip().endswith("== Facts to be confirmed ==\n- Zarathon Keep"))
        with tempfile.TemporaryDirectory() as raw:
            folder = Path(raw)
            (folder / rv.RECAP_FILE).write_text(text, encoding="utf-8")
            review = rv.read_folder(folder)
            self.assertEqual(review.to_confirm, ["Zarathon Keep"])
            self.assertIn("Facts to be confirmed", rv.page(review, folder))
            plain = rv.recap_text("The Gate", fx.SUMMARY, [], ["m"], voice_tags=True)
            self.assertNotIn("Facts to be confirmed", plain)


class TheStoredList(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.cid = (await CampaignStore(self.db).create(GUILD, "One", fx.DM)).id
        self.store = RecapStore(self.db, clock=Clock())
        await self.store.enqueue(GUILD, self.cid, 100, dm_user_id=fx.DM)

    async def test_the_names_are_kept_with_the_job_and_the_notice_moves_on_once(self) -> None:
        await self.store.update(
            GUILD, self.cid, 100, state="ready", to_confirm=["Zarathon Keep", "Yrsa"]
        )
        job = await self.store.get(GUILD, self.cid, 100)
        assert job is not None
        self.assertEqual((job.to_confirm, job.names_state), (("Zarathon Keep", "Yrsa"), 0))
        self.assertTrue(await self.store.set_names_state(GUILD, self.cid, 100, 1, only_from=0))
        self.assertFalse(await self.store.set_names_state(GUILD, self.cid, 100, 1, only_from=0))
        self.assertEqual(
            await self.store.names_waiting(GUILD, self.cid), [(100, ["Zarathon Keep", "Yrsa"])]
        )
        self.assertTrue(await self.store.set_names_state(GUILD, self.cid, 100, 2, only_from=1))
        self.assertEqual(await self.store.names_waiting(GUILD, self.cid), [])


def bot_with(**kw: Any) -> Any:
    memory = SimpleNamespace(
        known_keys=AsyncMock(return_value=["mira"]), add_entity=AsyncMock(), entities=AsyncMock()
    )
    recaps = SimpleNamespace(
        set_names_state=AsyncMock(return_value=True),
        names_waiting=AsyncMock(return_value=[]),
    )
    sent = SimpleNamespace(send=AsyncMock())
    bot = SimpleNamespace(
        dm=sent,
        fetch_user=AsyncMock(return_value=sent),
        memory=memory,
        recaps=recaps,
        lookup=None,
        tables={},
        settings=SimpleNamespace(site_url=""),
        post=AsyncMock(return_value=True),
        recap_secret_names=AsyncMock(return_value=[]),
        **kw,
    )
    bot._private_names_note = DMBot._private_names_note.__get__(bot)
    bot._upkeep_channel = DMBot._upkeep_channel.__get__(bot)
    bot._link_recap_names = AsyncMock()  # the website's check list has its own tests
    return bot


class TheDMScreenNotice(unittest.IsolatedAsyncioTestCase):
    CAMPAIGN: Any = SimpleNamespace(dm_screen_channel_id=77, dm_bot_channel_id=None)

    def job(self) -> Any:
        return SimpleNamespace(
            session_at=100, dm_user_id=DM_ID, to_confirm=("Mira", "Zarathon Keep", "Yrsa")
        )

    async def test_the_notice_goes_to_the_dm_bot_channel_when_there_is_one(self) -> None:
        bot = bot_with()
        camp = SimpleNamespace(dm_screen_channel_id=77, dm_bot_channel_id=88)
        await DMBot._queue_recap_names(cast(Any, bot), GUILD, CID, self.job(), camp)
        args = bot.post.await_args.args
        self.assertEqual(args[0], 88)
        self.assertNotIn("Zarathon", args[1])  # counts only

    async def test_only_a_dm_can_open_the_check_from_the_notice(self) -> None:
        from dmbot.ui import names as names_ui

        camp = SimpleNamespace(
            id=CID,
            dm_user_ids=frozenset({DM_ID}),
            owner_user_id=DM_ID,
            dm_accounts=frozenset({DM_ID}),
        )
        bot = SimpleNamespace(campaigns=SimpleNamespace(get=AsyncMock(return_value=camp)))
        told = AsyncMock()
        button = names_ui.ReviewButton(CID)
        for user_id, opens in ((DM_ID + 1, False), (DM_ID, True)):
            interaction = SimpleNamespace(
                guild=SimpleNamespace(id=GUILD), user=SimpleNamespace(id=user_id), client=bot
            )
            with (
                patch.object(names_ui, "_bot", return_value=bot),
                patch.object(names_ui, "_tell", told),
                patch.object(names_ui, "start_review", AsyncMock()) as started,
            ):
                await button.callback(cast(Any, interaction))
            self.assertEqual(started.await_count, 1 if opens else 0)
        told.assert_awaited_once_with(ANY, "Only the DM can answer this.")

    async def test_new_names_are_proposed_and_the_dm_screen_is_told_once(self) -> None:
        bot = bot_with()
        await DMBot._queue_recap_names(cast(Any, bot), GUILD, CID, self.job(), self.CAMPAIGN)
        added = [c.kwargs["name"] for c in bot.memory.add_entity.await_args_list]
        self.assertEqual(added, ["Zarathon Keep", "Yrsa"])  # "Mira" is known already
        for call in bot.memory.add_entity.await_args_list:
            self.assertEqual((call.kwargs["type"], call.kwargs["source"]), ("concept", "scan"))
        (call,) = bot.post.await_args_list
        self.assertEqual(call.args[0], 77)
        self.assertIn("2 new names in the recap", call.args[1])
        self.assertNotIn("Zarathon", call.args[1])  # the names stay on the DM's page
        # asked again (a second round, a retry): nothing more happens
        bot.recaps.set_names_state.return_value = False
        bot.post.reset_mock()
        await DMBot._queue_recap_names(cast(Any, bot), GUILD, CID, self.job(), self.CAMPAIGN)
        bot.post.assert_not_awaited()

    async def test_the_dm_also_gets_a_private_message_with_the_check(self) -> None:
        bot = bot_with()
        await DMBot._queue_recap_names(cast(Any, bot), GUILD, CID, self.job(), self.CAMPAIGN)
        bot.fetch_user.assert_awaited_once_with(DM_ID)
        (call,) = bot.dm.send.await_args_list
        self.assertIn("2 new names in the recap", call.args[0])
        self.assertNotIn("Zarathon", call.args[0])
        (button,) = call.kwargs["view"].children
        self.assertEqual(button.custom_id, f"dmbot:names:recap:{GUILD}:{CID}")

    async def test_closed_private_messages_cost_nothing_and_the_dm_screen_still_has_it(
        self,
    ) -> None:
        import discord

        bot = bot_with()
        bot.dm.send.side_effect = discord.Forbidden(
            SimpleNamespace(status=403, reason=""), "closed"
        )
        await DMBot._queue_recap_names(cast(Any, bot), GUILD, CID, self.job(), self.CAMPAIGN)
        (call,) = bot.post.await_args_list  # the DM screen note was sent
        self.assertEqual(call.args[0], 77)
        # and a dead lookup of the user is no worse
        bot.fetch_user.side_effect = RuntimeError("gone")
        bot.recaps.set_names_state.return_value = True
        await DMBot._queue_recap_names(cast(Any, bot), GUILD, CID, self.job(), self.CAMPAIGN)

    async def test_a_name_made_secret_since_is_never_queued(self) -> None:
        bot = bot_with()
        bot.recap_secret_names.return_value = ["Yrsa"]
        await DMBot._queue_recap_names(cast(Any, bot), GUILD, CID, self.job(), self.CAMPAIGN)
        added = [c.kwargs["name"] for c in bot.memory.add_entity.await_args_list]
        self.assertEqual(added, ["Zarathon Keep"])
        bot.recap_secret_names.return_value = None  # secrets unreadable: nothing is queued
        bot.memory.add_entity.reset_mock()
        bot.recaps.set_names_state.return_value = True
        await DMBot._queue_recap_names(cast(Any, bot), GUILD, CID, self.job(), self.CAMPAIGN)
        bot.memory.add_entity.assert_not_awaited()

    async def test_a_refused_name_is_skipped_and_nothing_is_said_when_none_were_added(self) -> None:
        bot = bot_with()
        bot.memory.add_entity.side_effect = MemoryRuleError("no")
        await DMBot._queue_recap_names(cast(Any, bot), GUILD, CID, self.job(), self.CAMPAIGN)
        bot.post.assert_not_awaited()

    async def test_a_failure_costs_the_notice_not_the_recap(self) -> None:
        bot = bot_with()
        bot.recaps.set_names_state.side_effect = RuntimeError("down")
        await DMBot._queue_recap_names(cast(Any, bot), GUILD, CID, self.job(), self.CAMPAIGN)

    async def test_the_next_session_reminds_once_with_the_unanswered_count(self) -> None:
        table: Any = SimpleNamespace(
            guild_id=GUILD, campaign_id=CID, screen_channel_id=66, resumed=False
        )
        bot = bot_with()
        bot.tables = {GUILD: table}
        bot.recaps.names_waiting.return_value = [(100, ["Zarathon Keep", "Yrsa"])]
        bot.memory.entities.return_value = [SimpleNamespace(name="Yrsa")]  # one was answered
        await DMBot._waiting_recap_names(cast(Any, bot), table)
        (call,) = bot.post.await_args_list
        self.assertEqual(call.args[0], 66)
        self.assertIn("1 new name** from the last recap is still unchecked", call.args[1])
        # a resumed session says nothing
        bot.post.reset_mock()
        table.resumed = True
        await DMBot._waiting_recap_names(cast(Any, bot), table)
        bot.post.assert_not_awaited()

    async def test_all_answered_means_no_reminder(self) -> None:
        table: Any = SimpleNamespace(
            guild_id=GUILD, campaign_id=CID, screen_channel_id=66, resumed=False
        )
        bot = bot_with()
        bot.tables = {GUILD: table}
        bot.recaps.names_waiting.return_value = [(100, ["Zarathon Keep"])]
        bot.memory.entities.return_value = []
        await DMBot._waiting_recap_names(cast(Any, bot), table)
        bot.post.assert_not_awaited()
        bot.recaps.set_names_state.assert_awaited()  # and it is not asked again


if __name__ == "__main__":
    unittest.main()


class TheLinks(unittest.IsolatedAsyncioTestCase):
    """The private names note ends with the website button, and edits itself once to point at
    its own message (#3663)."""

    async def test_the_note_carries_the_names_page_and_its_own_discord_link(self) -> None:
        bot = bot_with()
        bot.settings = SimpleNamespace(site_url="https://dmbot.example.com")
        bot.dm.send.return_value = SimpleNamespace(
            jump_url="https://discord.com/channels/@me/2/3", edit=AsyncMock()
        )
        await DMBot._private_names_note(cast(Any, bot), GUILD, CID, DM_ID, 2)
        (call,) = bot.dm.send.await_args_list
        labels = [getattr(c, "label", "") for c in call.kwargs["view"].children]
        self.assertIn("Open on the website", labels)
        urls = [getattr(c, "url", None) for c in call.kwargs["view"].children]
        self.assertIn(f"https://dmbot.example.com/account/names/{GUILD}.{CID}", urls)
        bot.dm.send.return_value.edit.assert_awaited_once()  # the one edit adding Open in Discord
