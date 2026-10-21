"""The DM says a secret name at the table (#2088): DMbot asks at once, privately, whether it is
revealed now. Once for each name in a session, never for a player's line, never on Quiet, Yes
clears the secret as one undoable change, No writes nothing, only a DM can press, and the matcher's
partial words do not ask. Invented names only."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

from dmbot.bot import DMBot
from dmbot.config import Settings
from dmbot.memory.models import DM, MemoryRuleError
from dmbot.memory.secret_aloud import SecretName, secret_names
from dmbot.memory.secret_reveal import RevealSession, revealed_in
from dmbot.transcript.topics import GAME, OFF_TOPIC, TABLE_TALK
from dmbot.ui.secret_reveal import (
    PRIVATE_NOTE,
    RevealButton,
    kept_text,
    question_text,
    reveal_view,
)
from tests.test_memory_store import GUILD_A, MemoryTest

GUILD, CID, DM_ID, PLAYER = 5, "c" * 32, 7, 11
BROTHERHOOD = SecretName("a" * 32, "Arcane Brotherhood")
AURIL = SecretName("b" * 32, "Auril")


class Finding(unittest.TestCase):
    def test_a_line_with_a_secret_asks_once_and_a_second_line_does_not(self) -> None:
        session = RevealSession()
        got = revealed_in("We serve the Arcane Brotherhood", [BROTHERHOOD], [], session)
        assert got is not None
        self.assertEqual((got.text, got.alias_id, got.entries), ("Arcane Brotherhood", "a" * 32, 1))
        self.assertIsNone(revealed_in("The Arcane Brotherhood again", [BROTHERHOOD], [], session))

    def test_partial_words_do_not_ask(self) -> None:
        session = RevealSession()
        for line in ("the brotherhood", "arcane magic", "a brother in arms"):
            self.assertIsNone(revealed_in(line, [BROTHERHOOD], [], session), line)
        self.assertEqual(session.asked, set())  # nothing was claimed

    def test_a_longer_allowed_name_does_not_ask(self) -> None:
        session = RevealSession()
        line = "Arcane Brotherhood Hall is old"
        self.assertIsNone(revealed_in(line, [BROTHERHOOD], ["Arcane Brotherhood Hall"], session))

    def test_other_forms_ask(self) -> None:
        for line in ("the arcane brotherhood", "arcane brotherhood's vault", "ARCANE BROTHERHOOD"):
            self.assertIsNotNone(revealed_in(line, [BROTHERHOOD], [], RevealSession()), line)

    def test_each_name_is_asked_on_its_own_and_entries_sharing_a_spelling_are_one(self) -> None:
        twin = SecretName("c" * 32, "auril")
        session = RevealSession()
        first = revealed_in(
            "Auril and the Arcane Brotherhood", [AURIL, twin, BROTHERHOOD], [], session
        )
        assert first is not None
        self.assertEqual((first.text, first.entries), ("Auril", 2))
        second = revealed_in(
            "Auril and the Arcane Brotherhood", [AURIL, twin, BROTHERHOOD], [], session
        )
        assert second is not None and second.text == "Arcane Brotherhood"
        self.assertIsNone(revealed_in("Auril, Auril", [AURIL, twin, BROTHERHOOD], [], session))

    def test_no_secrets_asks_nothing(self) -> None:
        self.assertIsNone(revealed_in("anything", [], [], RevealSession()))

    def test_the_session_trusts_the_names_it_read_for_a_minute(self) -> None:
        session = RevealSession()
        self.assertFalse(session.fresh(10.0))
        session.names, session.read_at = [AURIL], 10.0
        self.assertTrue(session.fresh(30.0))
        self.assertFalse(session.fresh(100.0))


def fake_table(*, level: str = "normal", dms: frozenset[int] = frozenset({DM_ID})) -> Any:
    table = SimpleNamespace(
        guild_id=GUILD, campaign_id=CID, screen_level=level, screen_channel_id=55,
        dm_user_ids=dms, dm_user_id=None, reveal=RevealSession(),
    )  # fmt: skip
    table.is_dm = lambda u: u in dms
    return table


def fake_bot(table: Any, *, visibility: str = "private", consenting: bool = True) -> Any:
    campaign = SimpleNamespace(
        guild_id=GUILD, id=CID, dm_user_ids=frozenset({DM_ID}), dm_screen_visibility=visibility
    )
    lookup = SimpleNamespace(
        get=AsyncMock(return_value=SimpleNamespace(names=[])), drop=MagicMock()
    )
    bot = SimpleNamespace(
        memory=object(),
        lookup=lookup,
        campaigns=SimpleNamespace(get=AsyncMock(return_value=campaign)),
        tables={GUILD: table},
        consent=SimpleNamespace(has_consent=lambda g, u: consenting),
        post=AsyncMock(return_value=True),
        sidebar_send_dm=AsyncMock(return_value="sent"),
    )
    return bot


async def ask(
    bot: Any, table: Any, line: str, secrets: list[SecretName], user: int = DM_ID
) -> None:
    from unittest import mock

    with mock.patch("dmbot.bot.secret_names", AsyncMock(return_value=secrets)):
        await DMBot._reveal_ask(bot, table, user, line)


class TheQuestion(unittest.IsolatedAsyncioTestCase):
    async def test_a_dms_line_with_a_secret_asks_on_the_dm_screen_once(self) -> None:
        table, bot = fake_table(), None
        bot = fake_bot(table)
        await ask(bot, table, "We are the Arcane Brotherhood", [BROTHERHOOD])
        bot.post.assert_awaited_once()
        self.assertEqual(bot.post.await_args.args[0], 55)
        self.assertEqual(bot.post.await_args.args[1], question_text("Arcane Brotherhood"))
        self.assertIsNotNone(
            bot.post.await_args.kwargs["secret_of"]
        )  # pulled back if opened (#2097)
        labels = [c.item.label for c in bot.post.await_args.kwargs["view"].children]
        self.assertEqual(labels, ["Yes, players know now", "No, keep it secret"])
        await ask(bot, table, "the Arcane Brotherhood again", [BROTHERHOOD])
        self.assertEqual(bot.post.await_count, 1)  # a second line in the session does not ask

    async def test_quiet_asks_nothing(self) -> None:
        table = fake_table(level="quiet")
        bot = fake_bot(table)
        await ask(bot, table, "the Arcane Brotherhood", [BROTHERHOOD])
        bot.post.assert_not_awaited()
        bot.sidebar_send_dm.assert_not_awaited()

    async def test_a_partial_name_asks_nothing(self) -> None:
        table = fake_table()
        bot = fake_bot(table)
        await ask(bot, table, "join the brotherhood, said Arcane", [BROTHERHOOD])
        bot.post.assert_not_awaited()

    async def test_when_players_may_peek_the_question_goes_to_the_dm_alone(self) -> None:
        for visibility in ("peek", "open"):
            table = fake_table()
            bot = fake_bot(table, visibility=visibility)
            await ask(bot, table, "the Arcane Brotherhood", [BROTHERHOOD])
            bot.post.assert_not_awaited()
            ((who, text, view),) = [c.args for c in bot.sidebar_send_dm.await_args_list]
            self.assertEqual(who, DM_ID)
            self.assertTrue(text.endswith(PRIVATE_NOTE))
            self.assertEqual(len(view.children), 2)

    async def test_consent_withdrawn_between_the_waits_asks_nothing(self) -> None:
        table = fake_table()
        bot = fake_bot(table)
        # has_consent is asked twice: at the start (True here), then after the campaign is read
        # and before anything is sent (False here). The second answer is what stops it.
        answers = iter([True, False])
        asked_consent: list[int] = []

        def has_consent(guild: int, user: int) -> bool:
            asked_consent.append(user)
            return next(answers)

        bot.consent = SimpleNamespace(has_consent=has_consent)
        await ask(bot, table, "the Arcane Brotherhood", [BROTHERHOOD])
        self.assertEqual(len(asked_consent), 2)  # it got past the first check and met the second
        bot.post.assert_not_awaited()
        bot.sidebar_send_dm.assert_not_awaited()
        self.assertEqual(table.reveal.asked, set())  # nothing was claimed, so it may ask later

    async def test_a_dm_who_stopped_being_recorded_is_not_asked(self) -> None:
        table = fake_table()
        bot = fake_bot(table, consenting=False)
        await ask(bot, table, "the Arcane Brotherhood", [BROTHERHOOD])
        bot.post.assert_not_awaited()

    async def test_a_question_that_could_not_be_sent_may_be_asked_again(self) -> None:
        table = fake_table()
        bot = fake_bot(table)
        bot.post = AsyncMock(return_value=False)
        await ask(bot, table, "the Arcane Brotherhood", [BROTHERHOOD])
        bot.post = AsyncMock(return_value=True)
        await ask(bot, table, "the Arcane Brotherhood", [BROTHERHOOD])
        bot.post.assert_awaited_once()

    async def test_a_dm_whose_private_messages_are_closed_is_not_tried_again(self) -> None:
        table = fake_table()
        bot = fake_bot(table, visibility="peek")
        bot.sidebar_send_dm = AsyncMock(return_value="forbidden")
        await ask(bot, table, "the Arcane Brotherhood", [BROTHERHOOD])
        await ask(bot, table, "the Arcane Brotherhood again", [BROTHERHOOD])
        self.assertEqual(bot.sidebar_send_dm.await_count, 1)  # the key stays claimed

    async def test_a_failed_private_send_may_be_tried_again(self) -> None:
        table = fake_table()
        bot = fake_bot(table, visibility="peek")
        bot.sidebar_send_dm = AsyncMock(return_value="failed")
        await ask(bot, table, "the Arcane Brotherhood", [BROTHERHOOD])
        await ask(bot, table, "the Arcane Brotherhood again", [BROTHERHOOD])
        self.assertEqual(bot.sidebar_send_dm.await_count, 2)

    async def test_nothing_is_logged_but_a_fixed_line(self) -> None:
        table = fake_table()
        bot = fake_bot(table)
        with self.assertLogs("dmbot.bot", level="INFO") as logs:
            await ask(bot, table, "the Arcane Brotherhood", [BROTHERHOOD])
        self.assertNotIn("brotherhood", " ".join(logs.output).lower())

    async def test_a_session_that_ended_asks_nothing(self) -> None:
        table = fake_table()
        bot = fake_bot(table)
        bot.tables = {}
        await ask(bot, table, "the Arcane Brotherhood", [BROTHERHOOD])
        bot.post.assert_not_awaited()


class AHeldLine(unittest.IsolatedAsyncioTestCase):
    """A DM line the off-topic filter held is asked about once the filter calls it game talk
    (#2101), and only then."""

    async def label(
        self,
        topic: str,
        *,
        user: int = DM_ID,
        stops_after_labelling: bool = False,
        on: bool = True,
    ) -> list[tuple[Any, ...]]:
        """The `_reveal_ask` calls the labelling made, as their arguments."""
        from unittest import mock

        from dmbot.ai import Reply
        from dmbot.transcript.topics import Waiting

        table = fake_table()
        table.held = {(user, 5): "the Arcane Brotherhood"}
        table.held_spelled = {}  # (#2184: the labelling also releases a held spelled-out name)
        table.held_pronounced = {}  # (#2171: and a held sound waiting beside it)
        table.topic_calls, table.topic_failures, table.heard = 0, 0, []
        table.topic_tokens = [0, 0]
        table.vocabulary = MagicMock()
        calls: list[tuple[Any, ...]] = []
        bot = MagicMock()
        bot.topic_ai = object()
        bot.settings = SimpleNamespace(secret_reveal_ask=on)
        # Consent is asked twice by the labelling (before the call, then per line): the DM stops
        # between the two when asked to.
        answers = iter([True, not stops_after_labelling])
        bot.consent = SimpleNamespace(has_consent=lambda g, u: next(answers, True))

        def reveal(*args: Any) -> None:
            calls.append(args)

        bot._reveal_ask = reveal
        bot._track = lambda result, name: None
        bot._suggest_after_label = lambda *a: None
        bot._save_topic = AsyncMock()
        bot._hide_lines = AsyncMock()
        answer = ([topic], Reply("x", False, 1, 1, "m"))
        with mock.patch("dmbot.bot.classify", AsyncMock(return_value=answer)):
            await DMBot._label_topics(bot, table, [Waiting(user, 5, "the Arcane Brotherhood")])
        self.table = table
        return calls

    async def test_game_talk_by_the_dm_asks_with_the_line_as_it_was_held(self) -> None:
        calls = await self.label(GAME)
        self.assertEqual(len(calls), 1)
        self.assertIs(calls[0][0], self.table)
        self.assertEqual(calls[0][1:], (DM_ID, "the Arcane Brotherhood"))

    async def test_table_talk_asks_too_and_off_topic_does_not(self) -> None:
        self.assertEqual(len(await self.label(TABLE_TALK)), 1)
        self.assertEqual(await self.label(OFF_TOPIC), [])

    async def test_a_player_the_switch_and_a_dm_who_stopped_ask_nothing(self) -> None:
        self.assertEqual(await self.label(GAME, user=PLAYER), [])
        self.assertEqual(await self.label(GAME, on=False), [])
        self.assertEqual(await self.label(GAME, stops_after_labelling=True), [])


class TheSwitch(unittest.TestCase):
    def test_zero_turns_it_off_and_a_bad_value_is_refused(self) -> None:
        from dmbot.config import ConfigError, load_settings
        from tests.test_ai_tiers import BASE

        self.assertFalse(load_settings({**BASE, "DMBOT_SECRET_REVEAL_ASK": "0"}).secret_reveal_ask)
        self.assertTrue(load_settings(BASE).secret_reveal_ask)
        with self.assertRaises(ConfigError):
            load_settings({**BASE, "DMBOT_SECRET_REVEAL_ASK": "maybe"})


class TheHook(unittest.TestCase):
    """A line starts the question for a DM and for no one else, and the switch can turn it off."""

    def run_line(self, user: int, *, on: bool = True, held: bool = False) -> list[str]:
        table = fake_table()
        names: list[str] = []
        bot = MagicMock()
        bot.settings = SimpleNamespace(secret_reveal_ask=on)
        bot.clocks = None

        def track(coro: Any, name: str) -> None:
            names.append(name)
            if hasattr(coro, "close"):
                coro.close()

        bot._track = track
        DMBot._act_on_line(bot, table, user, "the Arcane Brotherhood", held=held)
        return names

    def test_a_dms_line_starts_it(self) -> None:
        self.assertIn("secret-reveal-ask", self.run_line(DM_ID))

    def test_a_players_line_does_not(self) -> None:
        self.assertNotIn("secret-reveal-ask", self.run_line(PLAYER))

    def test_the_switch_turns_it_off(self) -> None:
        self.assertNotIn("secret-reveal-ask", self.run_line(DM_ID, on=False))

    def test_a_held_line_does_not(self) -> None:
        self.assertNotIn("secret-reveal-ask", self.run_line(DM_ID, held=True))

    def test_the_switch_is_on_by_default_and_is_named_in_the_settings(self) -> None:
        self.assertTrue(Settings.__dataclass_fields__["secret_reveal_ask"].default)


def press(act: str, user: int, *, in_server: bool = True, guild: int = GUILD) -> Any:
    campaign = SimpleNamespace(
        guild_id=GUILD, id=CID, dm_user_ids=frozenset({DM_ID}), dm_screen_visibility="private"
    )
    written = SimpleNamespace(value=[object()])
    bot = SimpleNamespace(
        campaigns=SimpleNamespace(get=AsyncMock(return_value=campaign)),
        memory=SimpleNamespace(make_name_not_secret=AsyncMock(return_value=written)),
        lookup=SimpleNamespace(drop=MagicMock()),
    )
    interaction = SimpleNamespace(
        client=bot,
        guild=SimpleNamespace(id=guild) if in_server else None,
        user=SimpleNamespace(id=user),
        message=SimpleNamespace(content=question_text("Auril")),
        response=SimpleNamespace(
            is_done=lambda: False, send_message=AsyncMock(), edit_message=AsyncMock()
        ),
        followup=SimpleNamespace(send=AsyncMock()),
    )
    return RevealButton(act, CID, "b" * 32, GUILD), bot, interaction


class TheButtons(unittest.IsolatedAsyncioTestCase):
    async def test_yes_turns_the_secret_off_through_the_change_path(self) -> None:
        button, bot, interaction = press("y", DM_ID)
        await button.callback(interaction)
        bot.memory.make_name_not_secret.assert_awaited_once_with(GUILD, CID, "b" * 32, source=DM)
        bot.lookup.drop.assert_called_once()
        self.assertIsNone(interaction.response.edit_message.await_args.kwargs["view"])

    async def test_no_writes_nothing_and_closes_the_question(self) -> None:
        button, bot, interaction = press("n", DM_ID)
        await button.callback(interaction)
        bot.memory.make_name_not_secret.assert_not_awaited()
        self.assertEqual(
            interaction.response.edit_message.await_args.kwargs["content"], kept_text()
        )

    async def test_a_player_or_a_stranger_is_refused(self) -> None:
        for act in ("y", "n"):
            button, bot, interaction = press(act, PLAYER)
            await button.callback(interaction)
            bot.memory.make_name_not_secret.assert_not_awaited()
            interaction.response.edit_message.assert_not_awaited()
            interaction.response.send_message.assert_awaited_once()

    async def test_it_works_from_a_private_message_but_not_from_another_server(self) -> None:
        button, bot, interaction = press("y", DM_ID, in_server=False)
        await button.callback(interaction)
        bot.memory.make_name_not_secret.assert_awaited_once()
        button, bot, interaction = press("y", DM_ID, guild=999)
        await button.callback(interaction)
        bot.memory.make_name_not_secret.assert_not_awaited()

    async def test_a_rule_the_memory_refuses_is_told_and_the_question_stays(self) -> None:
        button, bot, interaction = press("y", DM_ID)
        bot.memory.make_name_not_secret.side_effect = MemoryRuleError("That name is gone.")
        await button.callback(interaction)
        interaction.response.edit_message.assert_not_awaited()

    def test_the_ids_survive_a_restart_and_fit_the_limit(self) -> None:
        view = reveal_view(CID, "b" * 32, 2**63 - 1)
        ids = [c.item.custom_id for c in view.children]  # type: ignore[attr-defined]
        self.assertTrue(all(len(i) <= 100 for i in ids), ids)
        self.assertTrue(
            all(RevealButton.__discord_ui_compiled_template__.fullmatch(i) for i in ids)
        )


class YesClearsItAndUndoRestoresIt(MemoryTest):
    async def test_yes_clears_every_entry_as_one_batch_and_undo_puts_it_back(self) -> None:
        mira = await self.add(
            "Mira", campaign=self.c, type="character", role="npc", status="confirmed"
        )
        alias = await self.memory.add_alias(
            GUILD_A, self.c, mira, "The Hooded One", kind="nickname", source="dm",
            status="confirmed", secret=True,
        )  # fmt: skip
        found = await secret_names(self.memory, GUILD_A, self.c)
        got = revealed_in("then the hooded one spoke", found, [], RevealSession())
        assert got is not None
        self.assertEqual(got.alias_id, alias.value.id)
        done = await self.memory.make_name_not_secret(GUILD_A, self.c, got.alias_id, source=DM)
        self.assertEqual(await secret_names(self.memory, GUILD_A, self.c), [])
        assert done.batch is not None
        await self.memory.undo(GUILD_A, self.c, done.batch)
        self.assertEqual(len(await secret_names(self.memory, GUILD_A, self.c)), 1)

    async def test_no_writes_nothing(self) -> None:
        mira = await self.add(
            "Mira", campaign=self.c, type="character", role="npc", status="confirmed"
        )
        await self.memory.add_alias(
            GUILD_A, self.c, mira, "The Hooded One", kind="nickname", source="dm",
            status="confirmed", secret=True,
        )  # fmt: skip
        before = await secret_names(self.memory, GUILD_A, self.c)
        revealed_in("the hooded one", before, [], RevealSession())
        self.assertEqual(await secret_names(self.memory, GUILD_A, self.c), before)

    async def test_two_entries_sharing_a_spelling_are_both_cleared_and_both_come_back(self) -> None:
        aliases = []
        for name in ("Mira", "Mara"):
            entry = await self.add(
                name, campaign=self.c, type="character", role="npc", status="confirmed"
            )
            aliases.append(
                await self.memory.add_alias(
                    GUILD_A, self.c, entry, "The Hooded One", kind="nickname", source="dm",
                    status="confirmed", secret=True,
                )
            )  # fmt: skip
        found = await secret_names(self.memory, GUILD_A, self.c)
        got = revealed_in("then the hooded one spoke", found, [], RevealSession())
        assert got is not None
        done = await self.memory.make_name_not_secret(GUILD_A, self.c, got.alias_id, source=DM)
        wanted = {a.value.id for a in aliases}
        self.assertEqual({n.alias_id for n in found}, wanted)  # two entries, one spelling
        self.assertEqual(got.entries, 2)  # asked about once, for both
        self.assertEqual({r.id for r in done.value}, wanted)  # both entries, by id
        self.assertEqual(await secret_names(self.memory, GUILD_A, self.c), [])
        assert done.batch is not None
        await self.memory.undo(GUILD_A, self.c, done.batch)  # one undo brings both back
        restored = await secret_names(self.memory, GUILD_A, self.c)
        self.assertEqual({n.alias_id for n in restored}, wanted)  # both, by id

    async def test_pressing_no_writes_nothing(self) -> None:
        mira = await self.add(
            "Mira", campaign=self.c, type="character", role="npc", status="confirmed"
        )
        alias = await self.memory.add_alias(
            GUILD_A, self.c, mira, "The Hooded One", kind="nickname", source="dm",
            status="confirmed", secret=True,
        )  # fmt: skip
        before = await secret_names(self.memory, GUILD_A, self.c)
        campaign = SimpleNamespace(
            guild_id=GUILD_A, id=self.c, dm_user_ids=frozenset({DM_ID}),
            dm_screen_visibility="private",
        )  # fmt: skip
        bot = SimpleNamespace(
            campaigns=SimpleNamespace(get=AsyncMock(return_value=campaign)),
            memory=self.memory,
            lookup=SimpleNamespace(drop=MagicMock()),
        )
        interaction = SimpleNamespace(
            client=bot,
            guild=SimpleNamespace(id=GUILD_A),
            user=SimpleNamespace(id=DM_ID),
            message=SimpleNamespace(content=question_text("The Hooded One")),
            response=SimpleNamespace(
                is_done=lambda: False, send_message=AsyncMock(), edit_message=AsyncMock()
            ),
            followup=SimpleNamespace(send=AsyncMock()),
        )  # fmt: skip
        await RevealButton("n", self.c, alias.value.id, GUILD_A).callback(cast(Any, interaction))
        self.assertEqual(await secret_names(self.memory, GUILD_A, self.c), before)
        bot.lookup.drop.assert_not_called()  # nothing changed, so nothing to refresh
        # The question was closed: edited once, its buttons gone (the wording is the button's).
        edit = interaction.response.edit_message
        edit.assert_awaited_once()
        self.assertIsNone(edit.await_args.kwargs["view"])
        self.assertIsInstance(edit.await_args.kwargs["content"], str)
