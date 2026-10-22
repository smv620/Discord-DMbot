"""Names the table has said (#2404, slice 2b): a name the story holds back from the players is
shown to them when someone at the table says or types it. Pure finding, the store, the after-
session step and its Undo button. Invented names; no AI anywhere."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from dmbot.bot import DMBot
from dmbot.memory.models import NewName
from dmbot.memory.revealed_aloud import Unrevealed, said, table_lines, unrevealed_names
from dmbot.transcript.models import Line
from dmbot.ui import revealed_report
from tests.test_memory_store import GUILD_A, MemoryTest

DM_ID, PLAYER, STOPPED, BOT = 7, 11, 12, 99
ORLA = Unrevealed("a" * 32, "Orla Finch", ("Orla Finch", "the Ferrywoman"))


def line(user: int, text: str, **kw: Any) -> Line:
    return Line(1000, user, text, text, **kw)


class Finding(unittest.TestCase):
    def test_only_recorded_people_count_and_the_dm_at_the_table_does(self) -> None:
        lines = [
            line(PLAYER, "met Orla Finch"),
            line(DM_ID, "the ferrywoman waits"),
            line(STOPPED, "Orla Finch again"),
            line(BOT, "Orla Finch", sidebar="answer"),
            line(DM_ID, "Orla Finch?", sidebar="question"),
        ]
        got = table_lines(lines, recorded={PLAYER, DM_ID, BOT})
        self.assertEqual(got, ["met Orla Finch", "the ferrywoman waits"])

    def test_the_name_or_another_of_its_names_said_as_whole_words(self) -> None:
        self.assertEqual(said([ORLA], ["we met ORLA FINCH today"]), [ORLA])
        self.assertEqual(said([ORLA], ["the Ferrywoman's boat"]), [ORLA])
        self.assertEqual(said([ORLA], ["Orla Finchley passed by", "Orla and Finch"]), [])

    def test_a_name_too_short_to_search_for_is_ignored(self) -> None:
        self.assertEqual(said([Unrevealed("b" * 32, "Al", ("Al",))], ["Al went home"]), [])

    def test_the_cleaned_and_the_as_heard_words_are_both_read(self) -> None:
        heard = Line(1000, PLAYER, "or la finch", "Orla Finch")
        self.assertEqual(said([ORLA], table_lines([heard], recorded={PLAYER})), [ORLA])


class WithTheStore(MemoryTest):
    async def held(self, *names: str) -> list[str]:
        made = await self.memory.add_names(
            GUILD_A, self.c, [NewName(n, "npc", "confirmed") for n in names], source="dm"
        )
        return [i for i in made.value if i is not None]

    async def hidden(self, entity: str) -> bool:
        found = await self.memory.entity(GUILD_A, self.c, entity)
        assert found is not None
        return found.hidden_from_players

    async def test_a_held_back_name_is_looked_for_but_never_a_secret_spelling_or_a_clue(
        self,
    ) -> None:
        (orla,) = await self.held("Orla Finch")
        await self.memory.add_alias(
            GUILD_A, self.c, orla, "The Ferrywoman", kind="title", source="dm",
            status="confirmed", secret=True,
        )  # fmt: skip
        await self.memory.add_alias(
            GUILD_A, self.c, orla, "Ferry Orla", kind="nickname", source="dm", status="confirmed"
        )
        shown = await self.add("Rook", type="npc")  # already shown to the players
        got = {u.entity_id: u for u in await unrevealed_names(self.memory, GUILD_A, self.c)}
        self.assertEqual(set(got), {orla})
        self.assertEqual(set(got[orla].spellings), {"Orla Finch", "Ferry Orla"})  # not the 🤫 one
        self.assertNotIn(shown, got)
        self.assertEqual(said(list(got.values()), ["The Ferrywoman was seen"]), [])

    async def test_a_clue_a_player_character_and_the_party_are_never_shown_by_speech(self) -> None:
        clue, hero = await self.held("The Map", "Rook Pell")
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute("UPDATE memory_entities SET type = 'clue' WHERE id = %s", (clue,))
            await conn.execute(
                "UPDATE memory_entities SET hidden_from_players = false, party = true"
                " WHERE id = %s", (hero,)
            )  # fmt: skip
        names = {u.entity_id for u in await unrevealed_names(self.memory, GUILD_A, self.c)}
        self.assertNotIn(clue, names)
        written = await self.memory.reveal_said(GUILD_A, self.c, [clue])
        self.assertEqual(written.value, [])

    async def test_saying_it_shows_it_and_one_undo_hides_it_again(self) -> None:
        orla, rook = await self.held("Orla Finch", "Rook Pell")
        self.assertTrue(await self.hidden(orla))
        written = await self.memory.reveal_said(GUILD_A, self.c, [orla])
        self.assertEqual(written.value, [orla])
        self.assertFalse(await self.hidden(orla))
        self.assertTrue(await self.hidden(rook))  # only what was said
        assert written.batch is not None
        await self.memory.undo(GUILD_A, self.c, written.batch)
        self.assertTrue(await self.hidden(orla))

    async def test_a_name_already_shown_changes_nothing(self) -> None:
        (orla,) = await self.held("Orla Finch")
        await self.memory.reveal_said(GUILD_A, self.c, [orla])
        again = await self.memory.reveal_said(GUILD_A, self.c, [orla])
        self.assertEqual((again.value, again.batch), ([], None))

    async def test_nothing_crosses_to_another_campaign(self) -> None:
        other = (await self.campaigns.create(GUILD_A, "Other", 8)).id
        (orla,) = await self.held("Orla Finch")
        (theirs,) = [
            i
            for i in (
                await self.memory.add_names(
                    GUILD_A, other, [NewName("Orla Finch", "npc", "confirmed")], source="dm"
                )
            ).value
            if i
        ]
        await self.memory.reveal_said(GUILD_A, self.c, [orla, theirs])  # a foreign id is ignored
        found = await self.memory.entity(GUILD_A, other, theirs)
        assert found is not None
        self.assertTrue(found.hidden_from_players)

    async def test_connections_between_shown_names_are_counted_for_the_report(self) -> None:
        orla, rook = await self.held("Orla Finch", "Rook Pell")
        await self.memory.add_relation(
            GUILD_A, self.c, orla, "knows", rook, source="dm", confidence=1.0,
            status="confirmed", unrevealed=True,
        )  # fmt: skip
        self.assertEqual(
            await self.memory.waiting_connections(GUILD_A, self.c, [orla, rook]), 0
        )  # both held
        await self.memory.reveal_said(GUILD_A, self.c, [orla])
        self.assertEqual(
            await self.memory.waiting_connections(GUILD_A, self.c, [orla]), 0
        )  # one held
        await self.memory.reveal_said(GUILD_A, self.c, [rook])
        self.assertEqual(await self.memory.waiting_connections(GUILD_A, self.c, [rook]), 1)


def fake_bot(memory: Any, lines: list[Line], agreed: set[int]) -> Any:
    bot = SimpleNamespace(
        memory=memory,
        lookup=SimpleNamespace(drop=MagicMock()),
        transcripts=SimpleNamespace(
            lines=AsyncMock(return_value=lines),
            sessions=AsyncMock(
                return_value=[SimpleNamespace(started_at=500), SimpleNamespace(started_at=900)]
            ),
        ),
        campaigns=SimpleNamespace(
            get=AsyncMock(return_value=SimpleNamespace(dm_screen_channel_id=55))
        ),
        consent=SimpleNamespace(consenting=AsyncMock(return_value=agreed)),
        post=AsyncMock(return_value=True),
    )

    async def reveal(*args: Any) -> None:
        await DMBot._reveal_said(bot, *args)

    bot._reveal_said = reveal
    return bot


class AfterTheSession(WithTheStore):
    def table(self) -> Any:
        return SimpleNamespace(
            guild_id=GUILD_A, campaign_id=self.c, transcript_session_id="s1", started_at=900
        )

    async def test_a_name_a_player_said_is_shown_and_the_dm_screen_says_which_with_an_undo(
        self,
    ) -> None:
        orla, rook = await self.held("Orla Finch", "Rook Pell")
        bot = fake_bot(self.memory, [line(PLAYER, "We met Orla Finch at the ford")], {PLAYER})
        await DMBot.reveal_names_said(bot, self.table())
        self.assertFalse(await self.hidden(orla))
        self.assertTrue(await self.hidden(rook))
        bot.post.assert_awaited_once()
        self.assertEqual(bot.post.await_args.args[0], 55)
        self.assertEqual(
            bot.post.await_args.args[1],
            "👁️ Revealed at the table: **Orla Finch** (Session 2). Your players can see them now.",
        )
        self.assertIsNotNone(bot.post.await_args.kwargs["view"])
        bot.lookup.drop.assert_called_once_with([self.c])

    async def test_the_sidebar_a_stopped_player_and_the_dm_screen_never_reveal(self) -> None:
        orla, _ = await self.held("Orla Finch", "Rook Pell")
        lines = [
            line(DM_ID, "Orla Finch?", sidebar="question"),
            line(BOT, "Orla Finch is a ferrywoman", sidebar="answer"),
            line(STOPPED, "Orla Finch"),
        ]
        bot = fake_bot(self.memory, lines, {DM_ID, BOT})
        await DMBot.reveal_names_said(bot, self.table())
        self.assertTrue(await self.hidden(orla))
        bot.post.assert_not_awaited()

    async def test_the_dm_speaking_at_the_table_counts(self) -> None:
        orla, _ = await self.held("Orla Finch", "Rook Pell")
        bot = fake_bot(self.memory, [line(DM_ID, "Orla Finch waves from the boat")], {DM_ID})
        await DMBot.reveal_names_said(bot, self.table())
        self.assertFalse(await self.hidden(orla))

    async def test_an_imported_session_is_the_table_too(self) -> None:
        orla, _ = await self.held("Orla Finch", "Rook Pell")
        bot = fake_bot(self.memory, [], set())

        async def texts() -> list[str]:
            return ["Orla Finch spoke"]

        await DMBot._reveal_said(bot, GUILD_A, self.c, texts, "an imported session")
        self.assertFalse(await self.hidden(orla))
        self.assertIn("(an imported session)", bot.post.await_args.args[1])

    async def test_consent_is_asked_last_and_a_person_who_stopped_does_not_reveal(self) -> None:
        orla, _ = await self.held("Orla Finch", "Rook Pell")
        bot = fake_bot(self.memory, [line(PLAYER, "Orla Finch")], {PLAYER})
        order: list[str] = []
        real = bot.memory.reveal_said

        async def held_names(*a: Any) -> Any:
            order.append("write")
            return await real(*a)

        bot.consent.consenting = AsyncMock(side_effect=lambda gid: order.append("consent") or set())
        bot.memory = SimpleNamespace(
            reveal_said=held_names,
            waiting_connections=self.memory.waiting_connections,
            _read=self.memory._read,
        )
        await DMBot.reveal_names_said(bot, self.table())
        self.assertEqual(order, ["consent"])  # asked, and then nothing written for nobody
        self.assertTrue(await self.hidden(orla))

    async def test_a_failing_post_still_leaves_the_names_shown_and_does_not_raise(self) -> None:
        orla, _ = await self.held("Orla Finch", "Rook Pell")
        bot = fake_bot(self.memory, [line(PLAYER, "Orla Finch")], {PLAYER})
        bot.post = AsyncMock(return_value=False)
        await DMBot.reveal_names_said(bot, self.table())
        self.assertFalse(await self.hidden(orla))

    async def test_only_connections_of_the_names_just_shown_are_pointed_to(self) -> None:
        _orla, rook, hana = await self.held("Orla Finch", "Rook Pell", "Hana Dray")
        await self.memory.reveal_said(GUILD_A, self.c, [hana, rook])
        await self.memory.add_relation(
            GUILD_A, self.c, hana, "knows", rook, source="dm", confidence=1.0,
            status="confirmed", unrevealed=True,
        )  # fmt: skip
        bot = fake_bot(self.memory, [line(PLAYER, "Orla Finch")], {PLAYER})
        await DMBot.reveal_names_said(bot, self.table())
        self.assertNotIn("hidden connection", bot.post.await_args.args[1])  # an old, kept one

    async def test_waiting_connections_are_pointed_to_after_the_names(self) -> None:
        orla, rook = await self.held("Orla Finch", "Rook Pell")
        await self.memory.add_relation(
            GUILD_A, self.c, orla, "knows", rook, source="dm", confidence=1.0,
            status="confirmed", unrevealed=True,
        )  # fmt: skip
        bot = fake_bot(self.memory, [line(PLAYER, "Orla Finch and Rook Pell")], {PLAYER})
        await DMBot.reveal_names_said(bot, self.table())
        text = bot.post.await_args.args[1]
        self.assertIn("1 hidden connection joins names your players know now", text)


class TheButton(unittest.IsolatedAsyncioTestCase):
    def press(self, user: int, memory: Any) -> tuple[Any, Any]:
        campaign = SimpleNamespace(guild_id=GUILD_A, id="c" * 32, dm_user_ids=frozenset({DM_ID}))
        bot = SimpleNamespace(
            campaigns=SimpleNamespace(get=AsyncMock(return_value=campaign)),
            memory=memory,
            lookup=SimpleNamespace(drop=MagicMock()),
        )
        response = SimpleNamespace(
            is_done=lambda: False, edit_message=AsyncMock(), send_message=AsyncMock()
        )
        interaction = SimpleNamespace(
            client=bot, guild=SimpleNamespace(id=GUILD_A), user=SimpleNamespace(id=user),
            response=response, followup=SimpleNamespace(send=AsyncMock()),
        )  # fmt: skip
        return revealed_report.UndoRevealedButton("c" * 32, 5), interaction

    async def test_only_a_dm_can_undo_and_it_hides_the_names_again(self) -> None:
        memory = SimpleNamespace(undo=AsyncMock())
        button, it = self.press(PLAYER, memory)
        await button.callback(it)
        memory.undo.assert_not_awaited()
        button, it = self.press(DM_ID, memory)
        await button.callback(it)
        memory.undo.assert_awaited_once_with(GUILD_A, "c" * 32, 5)
        self.assertEqual(
            it.response.edit_message.await_args.kwargs["content"], revealed_report.UNDONE
        )

    def test_the_line_lists_a_few_names_and_counts_the_rest(self) -> None:
        text = revealed_report.report_text([f"Name {n}" for n in range(14)], where="Session 3")
        self.assertIn("and 4 more", text)
        self.assertIn("(Session 3)", text)


if __name__ == "__main__":
    unittest.main()
