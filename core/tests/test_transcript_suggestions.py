"""A player's suggested fix to their own line (#1491): the rules (pure), the table (a real
Postgres), and the buttons on the DM screen. Made-up names and numbers throughout."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from dmbot.campaigns import CampaignStore
from dmbot.memory.lookup import CampaignLookup, LookupData
from dmbot.transcript import suggestions as sg
from dmbot.transcript.models import SIDEBAR_ANSWER, Line, Lineage
from dmbot.transcript.store import TranscriptStore
from dmbot.transcript.suggestions import SuggestionStore
from dmbot.ui import line_fix
from dmbot.ui.decision_lines import DecisionLines
from tests.answer_first import Timeline
from tests.pg import DatabaseTest
from tests.test_transcript_cleaner import BELLEROS, alias
from tests.test_transcript_cleaner import lookup as names_with

GUILD, OTHER, DM, PLAYER, ANOTHER = 1, 2, 7, 8, 9
START = 1_700_000_000
MS = START * 1000


class Tidy(unittest.TestCase):
    def test_one_line_of_plain_text(self) -> None:
        self.assertEqual(sg.tidy("  She   said\nthis\t "), "She said this")
        self.assertEqual(sg.tidy("a‮b\x00c"), "abc")  # direction and control characters go

    def test_empty_or_too_long_cannot_be_used(self) -> None:
        self.assertIsNone(sg.tidy("   "))
        self.assertIsNone(sg.tidy("​"))
        self.assertIsNone(sg.tidy("x" * (sg.MAX_CHARS + 1)))
        self.assertEqual(sg.tidy("x" * sg.MAX_CHARS), "x" * sg.MAX_CHARS)


class Table(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.campaigns = CampaignStore(self.db)
        self.transcripts = TranscriptStore(self.db)
        self.store = SuggestionStore(self.db, clock=lambda: float(START))
        self.campaign = await self.campaigns.create(GUILD, "Frostmaiden", DM)
        self.session = await self.transcripts.open_session(GUILD, self.campaign.id, START)
        asked = Line(MS + 7000, DM, "find flanking", "find flanking", 0, "game", "question",
                     Lineage(ref="a1b2c3", via="voice-memo"))  # fmt: skip
        answer = Line(MS + 8000, DM, "Optional.", "Optional.", 0, "game", SIDEBAR_ANSWER,
                      Lineage(reply_to="a1b2c3", model="m", prompt="p"))  # fmt: skip
        typed = Line(MS + 9000, PLAYER, "typed words", "typed words", typed=True)
        await self.transcripts.add_lines(
            GUILD,
            self.session,
            [
                Line(MS + 1000, PLAYER, "I saw Beleros", "I saw Belleros"),
                Line(MS + 2000, PLAYER, "then we ran", "then we ran"),
                Line(MS + 3000, ANOTHER, "not yours", "not yours"),
                asked,
                answer,
                typed,
            ],
        )

    async def made(self, words: str = "I saw Belleros there") -> sg.Suggestion:
        got = await self.store.suggest(
            GUILD, self.campaign.id, self.session, PLAYER, MS + 1000, words
        )
        assert isinstance(got, sg.Suggestion)
        return got

    async def test_a_player_sees_their_own_spoken_lines_only_newest_first(self) -> None:
        mine = await self.store.own_lines(GUILD, self.session, PLAYER)
        self.assertEqual([x.heard for x in mine], ["then we ran", "I saw Beleros"])  # no typed
        self.assertEqual(mine[1].text, "I saw Belleros")  # the cleaned words, heard kept apart
        self.assertEqual(await self.store.own_lines(GUILD, self.session, DM), [])  # sidebar only
        self.assertEqual(await self.store.own_lines(OTHER, self.session, PLAYER), [])

    async def test_a_dm_sees_any_speakers_spoken_lines_newest_first_and_no_sidebar_or_typed(
        self,
    ) -> None:
        got = await self.store.recent_lines(GUILD, self.session)
        self.assertEqual(
            [(x.user_id, x.heard) for x in got],
            [(ANOTHER, "not yours"), (PLAYER, "then we ran"), (PLAYER, "I saw Beleros")],
        )
        self.assertEqual(len(await self.store.recent_lines(GUILD, self.session, limit=2)), 2)
        self.assertEqual(await self.store.recent_lines(OTHER, self.session), [])  # another server

    async def test_only_the_speaker_may_suggest_for_a_line(self) -> None:
        got = await self.store.suggest(
            GUILD, self.campaign.id, self.session, ANOTHER, MS + 1000, "mine now"
        )
        self.assertEqual(got, "none")  # that line is PLAYER's

    async def test_a_dm_screen_line_and_a_typed_line_cannot_be_suggested_for(self) -> None:
        for at, who in ((MS + 7000, DM), (MS + 8000, DM), (MS + 9000, PLAYER)):
            got = await self.store.suggest(GUILD, self.campaign.id, self.session, who, at, "x")
            self.assertEqual(got, "none")

    async def test_another_campaign_or_server_finds_nothing(self) -> None:
        other = await self.campaigns.create(GUILD, "Elsewhere", DM)
        got = await self.store.suggest(GUILD, other.id, self.session, PLAYER, MS + 1000, "x")
        self.assertEqual(got, "none")
        got = await self.store.suggest(
            OTHER, self.campaign.id, self.session, PLAYER, MS + 1000, "x"
        )
        self.assertEqual(got, "none")

    async def test_one_suggestion_for_a_line_at_a_time(self) -> None:
        first = await self.made()
        again = await self.store.suggest(
            GUILD, self.campaign.id, self.session, PLAYER, MS + 1000, "other words"
        )
        self.assertEqual(again, "open")
        # Another line is fine; and once the first is answered, the line may have a new one.
        second = await self.store.suggest(
            GUILD, self.campaign.id, self.session, PLAYER, MS + 2000, "then we ran away"
        )
        self.assertIsInstance(second, sg.Suggestion)
        self.assertTrue(await self.store.decide(GUILD, first.id, sg.DECLINED, DM))
        self.assertIsInstance(await self.made("better words"), sg.Suggestion)

    async def test_a_person_may_have_only_so_many_waiting(self) -> None:
        more = [Line(MS + 20_000 + i, PLAYER, f"line {i}", f"line {i}") for i in range(4)]
        await self.transcripts.add_lines(GUILD, self.session, more)
        results = [
            await self.store.suggest(
                GUILD, self.campaign.id, self.session, PLAYER, MS + 20_000 + i, "better"
            )
            for i in range(4)
        ]
        self.assertEqual([isinstance(r, sg.Suggestion) for r in results], [True] * 3 + [False])
        self.assertEqual(results[3], "many")
        # An answer frees a place.
        first = results[0]
        assert isinstance(first, sg.Suggestion)
        assert await self.store.decide(GUILD, first.id, sg.DECLINED, DM)
        again = await self.store.suggest(
            GUILD, self.campaign.id, self.session, PLAYER, MS + 20_003, "better"
        )
        self.assertIsInstance(again, sg.Suggestion)

    async def test_the_lines_words_at_that_time_are_kept_with_it(self) -> None:
        made = await self.made()
        self.assertEqual(made.base_text, "I saw Belleros")

    async def test_a_reopened_one_gives_way_to_a_newer_one_for_the_same_line(self) -> None:
        first = await self.made("first words")
        assert await self.store.decide(GUILD, first.id, sg.ACCEPTED, DM)
        newer = await self.made("second words")  # the player asked again meanwhile
        await self.store.reopen(GUILD, first.id)  # the write failed
        old = await self.store.get(GUILD, first.id)
        assert old is not None
        self.assertEqual(old.state, sg.DROPPED)  # no clash, only one waits
        current = await self.store.get(GUILD, newer.id)
        assert current is not None
        self.assertEqual(current.state, sg.OPEN)

    async def test_two_at_once_cannot_pass_the_limit(self) -> None:
        import asyncio

        more = [Line(MS + 30_000 + i, PLAYER, f"line {i}", f"line {i}") for i in range(6)]
        await self.transcripts.add_lines(GUILD, self.session, more)
        results = await asyncio.gather(
            *(
                self.store.suggest(
                    GUILD, self.campaign.id, self.session, PLAYER, MS + 30_000 + i, "better"
                )
                for i in range(6)
            )
        )
        self.assertEqual(len([r for r in results if isinstance(r, sg.Suggestion)]), sg.MAX_OPEN)

    async def test_an_answer_counts_once(self) -> None:
        made = await self.made()
        self.assertTrue(await self.store.decide(GUILD, made.id, sg.ACCEPTED, DM))
        self.assertFalse(await self.store.decide(GUILD, made.id, sg.DECLINED, DM))
        got = await self.store.get(GUILD, made.id)
        assert got is not None
        self.assertEqual(got.state, sg.ACCEPTED)
        await self.store.reopen(GUILD, made.id)  # a write that failed goes back to waiting
        got = await self.store.get(GUILD, made.id)
        assert got is not None
        self.assertEqual(got.state, sg.OPEN)

    async def test_other_servers_cannot_read_or_answer_it(self) -> None:
        made = await self.made()
        self.assertIsNone(await self.store.get(OTHER, made.id))
        self.assertFalse(await self.store.decide(OTHER, made.id, sg.ACCEPTED, DM))

    async def test_a_suggestion_past_the_end_of_the_next_session_is_dropped(self) -> None:
        made = await self.made()
        later = await self.transcripts.open_session(GUILD, self.campaign.id, START + 86400)
        # The session it was made in ended: it stays. The next one ended: it goes.
        self.assertEqual(await self.store.drop_old(GUILD, self.campaign.id, [self.session]), 0)
        self.assertEqual(await self.store.drop_old(GUILD, self.campaign.id, [later]), 1)
        got = await self.store.get(GUILD, made.id)
        assert got is not None
        self.assertEqual(got.state, sg.DROPPED)
        self.assertFalse(await self.store.decide(GUILD, made.id, sg.ACCEPTED, DM))

    async def test_accepting_changes_the_cleaned_words_and_never_what_was_heard(self) -> None:
        made = await self.made("I saw Belleros there")
        assert await self.store.decide(GUILD, made.id, sg.ACCEPTED, DM)
        changed = await self.transcripts.relabel_line(
            GUILD, self.session, PLAYER, made.started_ms, made.suggested
        )
        self.assertEqual(changed, 1)
        lines = {x.started_ms: x for x in await self.transcripts.lines(GUILD, self.session)}
        self.assertEqual(lines[MS + 1000].heard, "I saw Beleros")
        self.assertEqual(lines[MS + 1000].text, "I saw Belleros there")
        self.assertEqual(lines[MS + 2000].text, "then we ran")  # nothing else moved

    async def test_the_table_is_closed_to_the_other_server(self) -> None:
        await self.made()
        async with self.db.guild(OTHER) as conn:
            cur = await conn.execute("SELECT count(*) AS n FROM transcript_suggestions")
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual(row["n"], 0)

    async def test_the_line_is_the_speakers_own(self) -> None:
        own = await self.store.line(GUILD, self.session, PLAYER, MS + 1000)
        assert own is not None
        self.assertEqual((own.heard, own.text), ("I saw Beleros", "I saw Belleros"))
        self.assertIsNone(await self.store.line(GUILD, self.session, ANOTHER, MS + 1000))
        self.assertIsNone(await self.store.line(GUILD, self.session, DM, MS + 8000))  # sidebar


# ---- the DM-screen buttons and the speaker's steps, with stand-ins ---------------------------

CID = "c" * 32


def lookup() -> CampaignLookup:
    return CampaignLookup.build(LookupData(1, (), (), (), ()))


BOT_ID = 99


def copy_of_line(message_id: int) -> Any:
    """A DM-screen line the bot posted for suggestion 5 (the original, or a re-post)."""
    return SimpleNamespace(
        id=message_id,
        content="✏️ **Mia** suggests a fix to their line:",
        author=SimpleNamespace(id=BOT_ID),
        edit=AsyncMock(),
    )


def found(**kw: Any) -> Any:
    base = dict(id=5, guild_id=GUILD, campaign_id=CID, session_id="s" * 32, started_ms=MS + 1000,
                user_id=PLAYER, suggested="I saw Belleros there", state="open",
                base_text="I saw Belleros")  # fmt: skip
    return SimpleNamespace(**{**base, **kw})


def press(act: str, user: int = DM, *, suggestion: Any = "default", consent: bool = True) -> Any:
    store = SimpleNamespace(
        get=AsyncMock(return_value=found() if suggestion == "default" else suggestion),
        decide=AsyncMock(return_value=True),
        reopen=AsyncMock(),
        line=AsyncMock(return_value=sg.Own(MS + 1000, "I saw Beleros", "I saw Belleros", PLAYER)),
    )
    campaign = SimpleNamespace(guild_id=GUILD, id=CID, dm_user_ids=frozenset({DM}))
    bot = SimpleNamespace(
        suggestions=store,
        campaigns=SimpleNamespace(get=AsyncMock(return_value=campaign)),
        consent=SimpleNamespace(has_consent=lambda g, u: consent),
        lookup=SimpleNamespace(get=AsyncMock(return_value=lookup())),
        apply_line_fix=AsyncMock(return_value=True),
        tell_user=AsyncMock(return_value=True),
        user=SimpleNamespace(id=BOT_ID),
        decision_lines=DecisionLines(),
    )
    message = copy_of_line(900)
    done = {"yes": False}

    async def defer(**kw: Any) -> None:
        done["yes"] = True

    response = SimpleNamespace(
        is_done=lambda: done["yes"], send_message=AsyncMock(), defer=AsyncMock(side_effect=defer)
    )
    interaction = SimpleNamespace(
        client=bot,
        guild=SimpleNamespace(id=GUILD),
        user=SimpleNamespace(id=user, display_name="Mia"),
        response=response,
        followup=SimpleNamespace(send=AsyncMock()),
        message=message,
    )
    return line_fix.LineFixButton(act, 5), bot, interaction


def said(interaction: Any) -> str:
    if interaction.response.send_message.await_args is not None:
        return str(interaction.response.send_message.await_args.args[0])
    return str(interaction.followup.send.await_args.args[0])


class TheButtons(unittest.IsolatedAsyncioTestCase):
    async def test_only_a_dm_of_the_campaign_may_answer(self) -> None:
        for act in ("yes", "no"):
            button, bot, interaction = press(act, PLAYER)
            await button.callback(interaction)
            self.assertEqual(said(interaction), line_fix.NOT_YOURS)
            bot.suggestions.decide.assert_not_awaited()
            bot.apply_line_fix.assert_not_awaited()

    async def test_the_button_answers_discord_before_the_database(self) -> None:
        for act in ("yes", "no"):
            line = Timeline()
            button, bot, interaction = press(act)
            interaction.response, interaction.followup = line.response, line.followup
            bot.suggestions.get = line.slow("get", found())
            bot.campaigns.get = line.slow("campaigns", bot.campaigns.get.return_value)
            await button.callback(interaction)
            line.assert_answered_first()
            self.assertIn("slow:get", line.events)

    async def test_a_missing_or_answered_suggestion_changes_nothing(self) -> None:
        button, bot, interaction = press("yes", suggestion=None)
        await button.callback(interaction)
        self.assertEqual(said(interaction), line_fix.GONE)
        button, bot, interaction = press("yes", suggestion=found(state="accepted"))
        await button.callback(interaction)
        self.assertEqual(said(interaction), line_fix.DECIDED)
        bot.apply_line_fix.assert_not_awaited()

    async def test_accept_writes_the_words_once_and_tells_the_player(self) -> None:
        button, bot, interaction = press("yes")
        await button.callback(interaction)
        bot.apply_line_fix.assert_awaited_once_with(
            GUILD, CID, "s" * 32, PLAYER, MS + 1000, "I saw Belleros there"
        )
        self.assertEqual(bot.suggestions.decide.await_args.args[2], sg.ACCEPTED)
        bot.tell_user.assert_awaited_once_with(PLAYER, line_fix.ANSWER_YES)
        edited = interaction.message.edit.await_args.kwargs
        self.assertIsNone(edited["view"])  # the buttons are gone
        self.assertIn("Changed to", edited["content"])

    async def test_the_other_copy_of_the_line_is_cleared_when_known_and_left_when_not(self) -> None:
        for act, word in (("yes", "Accepted."), ("no", "Kept as is.")):
            button, bot, interaction = press(act)
            other = copy_of_line(901)  # the re-post at a session start, or the original
            bot.decision_lines.remember(("fix", 5), other)
            await button.callback(interaction)
            other.edit.assert_awaited_once()
            self.assertIsNone(other.edit.await_args.kwargs["view"])
            self.assertTrue(other.edit.await_args.kwargs["content"].endswith(f"\n{word}"))
            interaction.message.edit.assert_awaited_once()  # the pressed one, edited once
        button, bot, interaction = press("yes")  # no other copy known: nothing else to edit
        await button.callback(interaction)
        interaction.message.edit.assert_awaited_once()

    async def test_a_suggestion_that_could_not_be_used_says_closed_on_the_other_copy(self) -> None:
        button, bot, interaction = press("yes", consent=False)  # the player stopped being recorded
        other = copy_of_line(903)
        bot.decision_lines.remember(("fix", 5), other)
        await button.callback(interaction)
        self.assertTrue(other.edit.await_args.kwargs["content"].endswith("\nClosed."))

    async def test_a_failed_edit_of_either_line_never_fails_the_decision(self) -> None:
        button, bot, interaction = press("yes")
        other = copy_of_line(902)
        other.edit = AsyncMock(side_effect=RuntimeError("deleted"))
        bot.decision_lines.remember(("fix", 5), other)
        await button.callback(interaction)
        bot.tell_user.assert_awaited_once_with(PLAYER, line_fix.ANSWER_YES)

    async def test_no_changes_nothing_and_the_player_hears_it(self) -> None:
        button, bot, interaction = press("no")
        await button.callback(interaction)
        bot.apply_line_fix.assert_not_awaited()
        self.assertEqual(bot.suggestions.decide.await_args.args[2], sg.DECLINED)
        bot.tell_user.assert_awaited_once_with(PLAYER, line_fix.ANSWER_NO)

    async def test_a_player_who_stopped_being_recorded_is_not_written_or_told(self) -> None:
        button, bot, interaction = press("yes", consent=False)
        await button.callback(interaction)
        bot.apply_line_fix.assert_not_awaited()
        self.assertEqual(bot.suggestions.decide.await_args.args[2], sg.DROPPED)
        bot.tell_user.assert_not_awaited()

    async def test_a_failed_write_goes_back_to_waiting(self) -> None:
        button, bot, interaction = press("yes")
        bot.apply_line_fix = AsyncMock(return_value=False)
        await button.callback(interaction)
        bot.suggestions.reopen.assert_awaited_once_with(GUILD, 5)
        bot.tell_user.assert_not_awaited()
        self.assertEqual(said(interaction), line_fix.NOT_WRITTEN)

    async def test_a_secret_name_is_never_written(self) -> None:
        button, bot, interaction = press("yes")
        with patch.object(line_fix, "text_names_a_secret", return_value=True):
            await button.callback(interaction)
        bot.apply_line_fix.assert_not_awaited()
        self.assertEqual(bot.suggestions.decide.await_args.args[2], sg.DECLINED)
        # The player hears the same words as for a plain No, so a guess can't be told apart.
        bot.tell_user.assert_awaited_once_with(PLAYER, line_fix.ANSWER_NO)

    async def test_a_line_that_is_gone_is_said_and_not_tried_again(self) -> None:
        button, bot, interaction = press("yes")
        bot.suggestions.line = AsyncMock(return_value=None)
        await button.callback(interaction)
        bot.apply_line_fix.assert_not_awaited()
        self.assertEqual(bot.suggestions.decide.await_args.args[2], sg.DROPPED)
        bot.suggestions.reopen.assert_not_awaited()
        self.assertIn(line_fix.LINE_GONE, interaction.message.edit.await_args.kwargs["content"])

    async def test_a_line_that_changed_since_is_not_overwritten(self) -> None:
        button, bot, interaction = press("yes")
        bot.suggestions.line = AsyncMock(
            return_value=sg.Own(MS + 1000, "I saw Beleros", "I saw Belleros, the Smith", PLAYER)
        )
        await button.callback(interaction)
        bot.apply_line_fix.assert_not_awaited()
        self.assertEqual(bot.suggestions.decide.await_args.args[2], sg.DECLINED)
        self.assertIn(line_fix.LINE_CHANGED, interaction.message.edit.await_args.kwargs["content"])
        bot.tell_user.assert_awaited_once_with(PLAYER, line_fix.ANSWER_NO)

    async def test_a_second_press_that_loses_says_so_and_changes_nothing_more(self) -> None:
        button, bot, interaction = press("yes", consent=False)
        bot.suggestions.decide = AsyncMock(return_value=False)  # someone else answered first
        await button.callback(interaction)
        self.assertEqual(said(interaction), line_fix.DECIDED)
        interaction.message.edit.assert_not_awaited()
        bot.tell_user.assert_not_awaited()

    async def test_the_ids_survive_a_restart(self) -> None:
        for act in ("yes", "no"):
            button = line_fix.LineFixButton(act, 123)
            self.assertEqual(button.item.custom_id, f"dmbot:linefix:{act}:123")
        view = line_fix.FixLineButton(GUILD, "a" * 32)
        self.assertEqual(view.item.custom_id, f"dmbot:fixline:{GUILD}:{'a' * 32}")


class TheSpeakersSteps(unittest.IsolatedAsyncioTestCase):
    def setup(self, **kw: Any) -> tuple[Any, Any]:
        _, bot, interaction = press("yes")
        interaction.user.id = PLAYER
        campaign = SimpleNamespace(guild_id=GUILD, id=CID, dm_screen_channel_id=77)
        bot.campaigns.get = AsyncMock(return_value=campaign)
        bot.post_message = AsyncMock(return_value=MagicMock())
        bot.suggestions.suggest = AsyncMock(
            return_value=kw.get("made", found(id=9, user_id=PLAYER))
        )
        return bot, interaction

    LINE = sg.Own(MS + 1000, "I saw Beleros", "I saw Belleros", PLAYER)

    async def go(self, bot: Any, interaction: Any, words: str) -> str:
        await line_fix.submit(interaction, GUILD, CID, "s" * 32, self.LINE, words)
        return said(interaction)

    async def test_the_dm_gets_one_short_line_with_two_buttons_and_the_player_hears_sent(
        self,
    ) -> None:
        bot, interaction = self.setup()
        self.assertEqual(await self.go(bot, interaction, "I saw Belleros there"), line_fix.SENT)
        channel, text, view = bot.post_message.await_args.args
        self.assertEqual(channel, 77)  # the DM screen, nothing public
        self.assertIn("Now: “I saw Belleros”", text)
        self.assertIn("Heard: “I saw Beleros”", text)  # shown because the cleaned words differ
        self.assertIn("They'd like: “I saw Belleros there”", text)
        self.assertEqual([c.item.label for c in view.children], ["Accept", "Keep as is"])

    async def test_bad_words_the_same_words_and_no_dm_screen_are_said(self) -> None:
        bot, interaction = self.setup()
        self.assertEqual(await self.go(bot, interaction, "   "), line_fix.BAD)
        self.assertEqual(await self.go(bot, interaction, "I saw Belleros"), line_fix.SAME)
        bot.suggestions.suggest.assert_not_awaited()
        bot2, interaction2 = self.setup()
        bot2.campaigns.get = AsyncMock(
            return_value=SimpleNamespace(guild_id=GUILD, id=CID, dm_screen_channel_id=None)
        )
        self.assertEqual(await self.go(bot2, interaction2, "Fine words"), line_fix.NO_DM_SCREEN)
        bot2.suggestions.suggest.assert_not_awaited()

    async def test_one_waiting_for_that_line_is_said_and_nothing_is_posted(self) -> None:
        bot, interaction = self.setup(made="open")
        self.assertEqual(await self.go(bot, interaction, "Other words"), line_fix.OPEN)
        bot.post_message.assert_not_awaited()

    async def test_someone_not_recorded_cannot_suggest(self) -> None:
        bot, interaction = self.setup()
        bot.consent = SimpleNamespace(has_consent=lambda g, u: False)
        self.assertEqual(await self.go(bot, interaction, "Other words"), line_fix.NOT_RECORDED)
        bot.suggestions.suggest.assert_not_awaited()

    async def test_a_secret_guess_is_not_refused_when_it_is_sent(self) -> None:
        # Refusing here would tell a player whether their words were a secret name. The words go
        # to the DM as any others; the check is made only when the DM presses Accept.
        bot, interaction = self.setup()
        with patch.object(line_fix, "text_names_a_secret", return_value=True) as check:
            self.assertEqual(await self.go(bot, interaction, "Other words"), line_fix.SENT)
        check.assert_not_called()

    async def test_if_the_dm_cannot_be_reached_it_is_not_left_waiting(self) -> None:
        bot, interaction = self.setup()
        bot.post_message = AsyncMock(return_value=None)
        self.assertEqual(await self.go(bot, interaction, "Other words"), line_fix.NO_DM_SCREEN)
        self.assertEqual(bot.suggestions.decide.await_args.args[2], sg.DROPPED)

    def test_everything_from_a_player_is_escaped_on_the_dm_screen(self) -> None:
        text = line_fix.dm_text("Mia *x*", "a [link](http://x) @everyone", "cleaned", "`code` _it_")
        self.assertIn("\\[link\\]", text)  # no link or formatting can start
        self.assertNotIn("http://", text)
        self.assertNotIn("@everyone", text)
        self.assertIn("\\*x\\*", text)
        self.assertIn("Heard:", text)  # the cleaned words differ from what was heard


class TheDmsOwnEdit(unittest.IsolatedAsyncioTestCase):
    """A DM fixes any line at once (#1499): DM only, written through the same path, and every
    refusal in plain words. Stand-ins for the bot."""

    LINE = sg.Own(MS + 1000, "I saw Beleros", "I saw Belleros", PLAYER)

    def setup(self, user: int = DM, **kw: Any) -> tuple[Any, Any]:
        _, bot, interaction = press("yes", user, consent=kw.get("consent", True))
        interaction.user.id = user
        bot.apply_line_fix = AsyncMock(return_value=kw.get("written", True))
        bot.tables = {}
        return bot, interaction

    async def go(self, interaction: Any, words: str) -> str:
        await line_fix.submit_dm(interaction, GUILD, CID, "s" * 32, self.LINE, words)
        return said(interaction)

    async def test_only_a_dm_of_the_campaign_may_edit(self) -> None:
        for user in (PLAYER, ANOTHER, 99):  # a player, a speaker, a server manager who isn't a DM
            bot, interaction = self.setup(user)
            self.assertEqual(await self.go(interaction, "better words"), line_fix.NOT_YOURS)
            bot.apply_line_fix.assert_not_awaited()

    async def test_it_is_written_at_once_noted_and_nobody_is_told(self) -> None:
        bot, interaction = self.setup()
        out = await self.go(interaction, "I saw Belleros there")
        bot.apply_line_fix.assert_awaited_once_with(
            GUILD, CID, "s" * 32, PLAYER, MS + 1000, "I saw Belleros there", noted=True
        )
        self.assertIn("Changed to", out)
        bot.tell_user.assert_not_awaited()  # nothing to the player
        sent = interaction.followup.send.await_args or interaction.response.send_message.await_args
        self.assertTrue(sent.kwargs.get("ephemeral"))  # the one reply is private

    async def test_the_refusals_are_plain(self) -> None:
        bot, interaction = self.setup()
        self.assertEqual(await self.go(interaction, "   "), line_fix.BAD)
        self.assertEqual(await self.go(interaction, "I saw Belleros"), line_fix.SAME)
        bot.apply_line_fix.assert_not_awaited()
        bot2, interaction2 = self.setup(consent=False)
        self.assertEqual(await self.go(interaction2, "words"), line_fix.PLAYER_STOPPED_DM)
        bot3, interaction3 = self.setup()
        bot3.suggestions.line = AsyncMock(return_value=None)
        self.assertEqual(await self.go(interaction3, "words"), line_fix.LINE_GONE)
        bot4, interaction4 = self.setup()
        bot4.suggestions.line = AsyncMock(
            return_value=sg.Own(MS + 1000, "I saw Beleros", "changed since", PLAYER)
        )
        self.assertEqual(await self.go(interaction4, "words"), line_fix.LINE_CHANGED)
        for b in (bot2, bot3, bot4):
            b.apply_line_fix.assert_not_awaited()

    async def test_a_secret_name_is_refused_with_the_same_words_as_for_a_suggestion(self) -> None:
        bot, interaction = self.setup()
        with patch.object(line_fix, "text_names_a_secret", return_value=True):
            self.assertEqual(await self.go(interaction, "words"), line_fix.SECRET)
        bot.apply_line_fix.assert_not_awaited()

    async def test_a_write_that_does_not_happen_is_said_with_the_next_step(self) -> None:
        _, interaction = self.setup(written=False)
        out = await self.go(interaction, "words")
        self.assertEqual(out, line_fix.NOT_WRITTEN_DM)
        self.assertIn("press Undo there first", out)

    async def test_while_the_session_runs_the_answer_points_at_undo(self) -> None:
        bot, interaction = self.setup()
        bot.tables = {GUILD: SimpleNamespace(transcript_session_id="s" * 32)}
        self.assertIn(line_fix.UNDO_HERE, await self.go(interaction, "words"))


class ThePickers(unittest.IsolatedAsyncioTestCase):
    """`start`: a DM sees any speaker's lines, a player only their own, and the DM need not be
    recorded."""

    def setup(self, user: int) -> tuple[Any, Any]:
        _, bot, interaction = press("yes", user)
        interaction.user.id = user
        interaction.response.is_done = lambda: True  # start() defers first
        interaction.guild = SimpleNamespace(
            id=GUILD, get_member=lambda uid: SimpleNamespace(display_name=f"Name{uid}")
        )
        session = SimpleNamespace(id="s" * 32, campaign_id=CID, started_at=START)
        bot.transcripts = SimpleNamespace(
            session=AsyncMock(return_value=session), sessions=AsyncMock(return_value=[session])
        )
        bot.campaigns.list_campaigns = AsyncMock(
            return_value=[SimpleNamespace(id=CID, guild_id=GUILD)]
        )
        bot.suggestions.recent_lines = AsyncMock(
            return_value=[sg.Own(MS + 2000, "h2", "t2", PLAYER), sg.Own(MS + 1000, "h1", "t1", DM)]
        )
        bot.suggestions.own_lines = AsyncMock(return_value=[sg.Own(MS + 2000, "h2", "t2", PLAYER)])
        return bot, interaction

    def sent_view(self, interaction: Any) -> Any:
        return interaction.followup.send.await_args.kwargs["view"]

    async def test_a_dm_sees_any_speakers_lines_without_being_recorded(self) -> None:
        bot, interaction = self.setup(DM)
        bot.consent = SimpleNamespace(has_consent=lambda g, u: False)
        await line_fix.start(interaction, session_id="s" * 32)
        view = self.sent_view(interaction)
        bot.suggestions.recent_lines.assert_awaited_once()
        bot.suggestions.own_lines.assert_not_awaited()
        self.assertTrue(view.dm)
        self.assertEqual(len(view.lines), 2)
        labels = [o.description for o in view.children[0].options]
        self.assertEqual(labels, ["Name8: t2", "Name7: t1"])  # whose line, so the DM can tell

    async def test_a_player_sees_only_their_own_lines(self) -> None:
        bot, interaction = self.setup(PLAYER)
        await line_fix.start(interaction, session_id="s" * 32)
        bot.suggestions.own_lines.assert_awaited_once()
        bot.suggestions.recent_lines.assert_not_awaited()
        self.assertFalse(self.sent_view(interaction).dm)

    async def test_a_person_not_recorded_and_not_a_dm_is_shown_no_lines_of_anyones(self) -> None:
        bot, interaction = self.setup(ANOTHER)
        bot.consent = SimpleNamespace(has_consent=lambda g, u: False)
        await line_fix.start(interaction, session_id="s" * 32)
        bot.suggestions.recent_lines.assert_not_awaited()
        bot.suggestions.own_lines.assert_not_awaited()
        self.assertEqual(said(interaction), line_fix.NO_LINES)  # same words as an empty list


class TheRealSecretCheck(unittest.TestCase):
    """`text_names_a_secret` against a real lookup: a secret anywhere in the words, or a
    sound-alike of one, and nothing else."""

    def names(self) -> CampaignLookup:
        return names_with(more_aliases=(alias(BELLEROS, "Silas Vane", secret=True),))

    def test_a_secret_name_anywhere_in_the_words(self) -> None:
        from dmbot.transcript.cleaner import text_names_a_secret

        for words in ("the hooded stranger waits", "then I met Silas Vane today", "silas vane"):
            self.assertTrue(text_names_a_secret(self.names(), words), words)

    def test_the_shared_matcher_catches_a_title_swap_a_plural_and_run_together(self) -> None:
        """#2087: "Mister White" for the secret "Mr. White", "the White Deaths" for "The White
        Death", and the name run together are secret names too, in a suggested line."""
        from dmbot.transcript.cleaner import text_names_a_secret

        lookup = names_with(
            more_aliases=(
                alias(BELLEROS, "Mr. White", secret=True),
                alias(BELLEROS, "The White Death", secret=True),
            )
        )
        for words in (
            "I met Mister White today",
            "the White Deaths came",
            "WhiteDeath rides",
            "mr white's cellar",
        ):
            self.assertTrue(text_names_a_secret(lookup, words), words)
        for words in ("Mr. Weaton waits", "an icy death", "white lady"):
            self.assertFalse(text_names_a_secret(lookup, words), words)

    def test_a_fix_that_writes_a_new_secret_is_taken_back_though_the_line_names_another(
        self,
    ) -> None:
        """#2087: the heard line already says one secret; a fix that adds a different one is still
        taken back (compared secret by secret, not "does the heard line name any")."""
        from dmbot.transcript.cleaner import SPELLING, Fix, _SecretChecks, _without_secrets

        lookup = names_with(
            more_aliases=(
                alias(BELLEROS, "Silas Vane", secret=True),
                alias(BELLEROS, "Mr. White", secret=True),
            )
        )
        heard = "Silas Vane met Mister Bright"
        fix = Fix(15, 28, "Mister Bright", "Mister White", "x", SPELLING)
        text, kept = _without_secrets(lookup, heard, [fix], _SecretChecks())
        self.assertEqual((text, kept), (heard, []))

    def test_a_sound_alike_of_a_secret_name_inside_a_longer_line(self) -> None:
        from dmbot.transcript.cleaner import text_names_a_secret

        self.assertTrue(text_names_a_secret(self.names(), "I met Silas Vain yesterday"))

    def test_ordinary_words_and_known_names_pass(self) -> None:
        from dmbot.transcript.cleaner import text_names_a_secret

        for words in ("I saw Belleros there", "then we ran away", "Cerric draws his bow"):
            self.assertFalse(text_names_a_secret(self.names(), words), words)

    def test_a_campaign_with_no_secrets_passes_everything(self) -> None:
        from dmbot.transcript.cleaner import text_names_a_secret

        self.assertFalse(text_names_a_secret(names_with(hooded=False), "Silas Vane"))


if __name__ == "__main__":
    unittest.main()
