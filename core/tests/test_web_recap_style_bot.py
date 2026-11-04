"""The bot's side of the website's Recap style (#3060): each of the five choices applied with the
same store calls and checks as Discord's panel, the DM check again at the moment of use, a typed
note sorted like the note editor sorts one, the words blanked when a row ends, and a row taken
twice applying once. Invented names and words."""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

from dmbot.campaigns import CampaignStore
from dmbot.recaps import style
from dmbot.recaps import web_style as ws
from dmbot.recaps.style import NoteStore, StyleStore
from dmbot.recaps.web_style import StyleQueue
from tests.pg import DatabaseTest
from tests.test_recap_feedback import FakeAI, answer

GUILD = 111
DM, PLAYER = 7, 9
EXAMPLE = "Zedrick crept along the frozen ledge while the wind howled over the broken bridge."
NOTE = "Keep recaps short and warm."


class Applying(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.campaigns = CampaignStore(self.db)
        self.campaign = await self.campaigns.create(GUILD, "Frostmaiden", DM)
        self.other = await self.campaigns.create(GUILD, "Strahd", DM)
        self.styles, self.notes = StyleStore(self.db), NoteStore(self.db)
        self.ai = FakeAI(answer(("style", NOTE)))
        self.gate: str | None = None
        self.bot = SimpleNamespace(
            campaigns=self.campaigns,
            recap_styles=self.styles,
            recap_notes=self.notes,
            recap_feedback_ai=self.ai,
            recap_feedback_gate=AsyncMock(side_effect=lambda _c: self.gate),
        )
        self.queue = StyleQueue(self.db)

    async def add(self, action: str, arg: str = "", *, user: int = DM, campaign: Any = None) -> str:
        now = int(time.time())
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                "INSERT INTO web_recap_style_actions (guild_id, campaign_id, id, user_id, action,"
                " arg, created_at, updated_at) VALUES (%s, %s,"
                " replace(gen_random_uuid()::text, '-', ''), %s, %s, %s, %s, %s) RETURNING id",
                (GUILD, (campaign or self.campaign).id, user, action, arg, now, now),
            )
            row = await cur.fetchone()
        assert row is not None
        return str(row["id"])

    async def row(self, request_id: str) -> dict[str, Any]:
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                "SELECT * FROM web_recap_style_actions WHERE guild_id = %s AND id = %s",
                (GUILD, request_id),
            )
            return dict(await cur.fetchone() or {})

    async def work(self) -> str:
        """Claim the oldest choice, apply it and end it, as the bot's round does."""
        claim = await self.queue.claim_next(GUILD)
        assert claim is not None
        try:
            error = await ws.apply(self.bot, self.queue, GUILD, claim)
        except Exception:
            error = ws.FAILED
        await self.queue.finish(GUILD, claim, error)
        return error

    async def ended(self, request_id: str) -> tuple[str, str, str]:
        done = await self.row(request_id)
        return done["state"], done["error"], done["arg"]

    async def own(self) -> list[str]:
        return [e.text for e in await self.styles.own(GUILD, self.campaign.id)]

    async def kept_notes(self) -> list[str]:
        return [n.text for n in await self.notes.notes(GUILD, self.campaign.id)]


class EachChoice(Applying):
    async def test_an_example_is_added_and_its_words_are_blanked(self) -> None:
        rid = await self.add(ws.ADD_EXAMPLE, EXAMPLE)
        self.assertEqual(await self.work(), "")
        self.assertEqual(await self.ended(rid), ("done", "", ""))
        self.assertEqual(await self.own(), [EXAMPLE])

    async def test_an_example_is_removed(self) -> None:
        made = await self.styles.add(GUILD, self.campaign.id, DM, EXAMPLE)
        rid = await self.add(ws.REMOVE_EXAMPLE, str(made.id))
        await self.work()
        self.assertEqual(await self.ended(rid), ("done", "", ""))
        self.assertEqual(await self.own(), [])

    async def test_use_defaults_removes_all_the_dms_own(self) -> None:
        await self.styles.add(GUILD, self.campaign.id, DM, EXAMPLE)
        await self.styles.add(GUILD, self.campaign.id, DM, EXAMPLE + " Again.")
        rid = await self.add(ws.USE_DEFAULTS)
        await self.work()
        self.assertEqual(await self.ended(rid), ("done", "", ""))
        self.assertEqual(await self.own(), [])

    async def test_a_note_is_sorted_then_kept_as_the_dms_own_words(self) -> None:
        rid = await self.add(ws.ADD_NOTE, f"  {NOTE}  ")
        self.assertEqual(await self.work(), "")
        self.assertEqual(await self.ended(rid), ("done", "", ""))
        self.assertEqual(await self.kept_notes(), [NOTE])
        self.assertEqual(len(self.ai.sent), 1)

    async def test_a_note_is_removed(self) -> None:
        made, _ = await self.notes.add(GUILD, self.campaign.id, DM, NOTE)
        rid = await self.add(ws.REMOVE_NOTE, str(made.id))
        await self.work()
        self.assertEqual(await self.ended(rid), ("done", "", ""))
        self.assertEqual(await self.kept_notes(), [])

    async def test_the_oldest_note_goes_when_an_eleventh_is_saved(self) -> None:
        for n in range(style.MAX_NOTES):
            await self.notes.add(GUILD, self.campaign.id, DM, f"Note number {n}.")
        await self.add(ws.ADD_NOTE, NOTE)
        await self.work()
        kept = await self.kept_notes()
        self.assertEqual(
            (len(kept), kept[-1], "Note number 0." in kept), (style.MAX_NOTES, NOTE, False)
        )


class WhyItFails(Applying):
    async def test_a_fourth_example_is_full(self) -> None:
        for n in range(style.MAX_EXAMPLES):
            await self.styles.add(GUILD, self.campaign.id, DM, f"{EXAMPLE} {n}")
        rid = await self.add(ws.ADD_EXAMPLE, EXAMPLE)
        self.assertEqual(await self.work(), ws.FULL)
        self.assertEqual(await self.ended(rid), ("failed", ws.FULL, ""))

    async def test_an_example_or_note_that_is_gone_is_gone(self) -> None:
        for action in (ws.REMOVE_EXAMPLE, ws.REMOVE_NOTE):
            rid = await self.add(action, "999999")
            self.assertEqual(await self.work(), ws.GONE)
            self.assertEqual(await self.ended(rid), ("failed", ws.GONE, ""))

    async def test_a_sender_who_is_no_longer_a_dm_changes_nothing(self) -> None:
        for action, arg in ((ws.ADD_EXAMPLE, EXAMPLE), (ws.ADD_NOTE, NOTE), (ws.USE_DEFAULTS, "")):
            rid = await self.add(action, arg, user=PLAYER)
            self.assertEqual(await self.work(), ws.NOT_A_DM)
            self.assertEqual(await self.ended(rid), ("failed", ws.NOT_A_DM, ""))
        self.assertEqual((await self.own(), await self.kept_notes(), self.ai.sent), ([], [], []))

    async def test_the_stores_own_dm_check_is_mapped_too(self) -> None:
        # (the campaign check passes, then the store refuses: the word is still `not_a_dm`)
        from dmbot.rules.house import HouseRuleError

        self.assertEqual(ws.error_of(HouseRuleError("x")), ws.NOT_A_DM)

    async def test_an_over_long_example_is_too_long_and_an_empty_one_is_empty(self) -> None:
        long = " ".join(["word"] * (style.EXAMPLE_MAX_WORDS + 1))
        rid = await self.add(ws.ADD_EXAMPLE, long)
        self.assertEqual(await self.work(), ws.TOO_LONG)
        self.assertEqual(await self.ended(rid), ("failed", ws.TOO_LONG, ""))
        await self.add(ws.ADD_EXAMPLE, "   ")
        self.assertEqual(await self.work(), ws.EMPTY)
        self.assertEqual(await self.own(), [])

    async def test_a_note_over_the_limit_is_too_long_and_is_never_sorted(self) -> None:
        await self.add(ws.ADD_NOTE, "x " * style.NOTE_CHARS)
        self.assertEqual(await self.work(), ws.TOO_LONG)
        self.assertEqual(self.ai.sent, [])

    async def test_a_note_the_sort_does_not_keep_as_one_style_note_is_not_a_note(self) -> None:
        for reply in (
            answer(("accuracy", "Mira was not at the gate.")),  # a correction
            answer(("style", NOTE), ("style", "And be funny.")),  # two notes
            answer(("story_help", "Add a twist.")),  # a request to invent
            answer(),  # nothing kept
        ):
            self.ai.reply = reply
            rid = await self.add(ws.ADD_NOTE, NOTE)
            self.assertEqual(await self.work(), ws.NOT_A_NOTE, reply)
            self.assertEqual(await self.ended(rid), ("failed", ws.NOT_A_NOTE, ""))
        self.assertEqual(await self.kept_notes(), [])

    async def test_a_note_that_asks_to_make_things_up_is_not_a_note(self) -> None:
        await self.add(ws.ADD_NOTE, "Make things up for drama.")
        self.assertEqual(await self.work(), ws.NOT_A_NOTE)
        self.assertEqual(await self.kept_notes(), [])

    async def test_a_note_with_no_ai_or_a_closed_gate_or_a_broken_sort_fails_and_keeps_nothing(
        self,
    ) -> None:
        self.bot.recap_feedback_ai = None
        await self.add(ws.ADD_NOTE, NOTE)
        self.assertEqual(await self.work(), ws.FAILED)
        self.bot.recap_feedback_ai = self.ai
        self.gate = "Recaps are off."
        await self.add(ws.ADD_NOTE, NOTE)
        self.assertEqual(await self.work(), ws.FAILED)
        self.gate = None
        self.ai.reply = RuntimeError("down")
        await self.add(ws.ADD_NOTE, NOTE)
        self.assertEqual(await self.work(), ws.FAILED)
        self.assertEqual(await self.kept_notes(), [])

    async def test_a_store_that_breaks_ends_the_row_as_failed_with_the_words_blanked(self) -> None:
        self.styles.add = AsyncMock(side_effect=RuntimeError(EXAMPLE))  # type: ignore[method-assign]
        rid = await self.add(ws.ADD_EXAMPLE, EXAMPLE)
        self.assertEqual(await self.work(), ws.FAILED)
        self.assertEqual(await self.ended(rid), ("failed", ws.FAILED, ""))

    async def test_an_action_the_bot_does_not_know_fails(self) -> None:
        claim = ws.Claim("x", self.campaign.id, DM, "sing", "", 0)
        self.assertEqual(await ws.apply(self.bot, self.queue, GUILD, claim), ws.FAILED)


class TheQueue(Applying):
    async def test_a_row_taken_twice_applies_once(self) -> None:
        rid = await self.add(ws.ADD_EXAMPLE, EXAMPLE)
        first = await self.queue.claim_next(GUILD)
        assert first is not None
        async with self.db.guild(GUILD) as conn:  # the first worker went quiet
            await conn.execute("UPDATE web_recap_style_actions SET updated_at = updated_at - 700")
        await self.queue.sweep(GUILD)
        second = await self.queue.claim_next(GUILD)
        assert second is not None
        # The late first worker no longer holds the claim: it changes nothing and ends nothing.
        self.assertEqual(await ws.apply(self.bot, self.queue, GUILD, first), "")
        self.assertEqual(await self.own(), [])
        self.assertFalse(await self.queue.finish(GUILD, first, ws.FAILED))
        self.assertEqual(await ws.apply(self.bot, self.queue, GUILD, second), "")
        self.assertTrue(await self.queue.finish(GUILD, second))
        self.assertEqual(await self.own(), [EXAMPLE])
        self.assertEqual((await self.ended(rid))[0], "done")

    async def test_the_same_note_twice_is_kept_once(self) -> None:
        await self.add(ws.ADD_NOTE, NOTE)
        await self.add(ws.ADD_NOTE, NOTE)
        await self.work()
        await self.work()
        self.assertEqual(await self.kept_notes(), [NOTE])

    async def test_a_choice_nobody_takes_fails_with_its_words_blanked(self) -> None:
        rid = await self.add(ws.ADD_EXAMPLE, EXAMPLE)
        async with self.db.guild(GUILD) as conn:
            await conn.execute("UPDATE web_recap_style_actions SET updated_at = updated_at - 2000")
        await self.queue.sweep(GUILD)
        self.assertEqual(await self.ended(rid), ("failed", "not_taken", ""))
        self.assertIsNone(await self.queue.claim_next(GUILD))

    async def test_old_rows_go_and_finished_rows_lose_their_words(self) -> None:
        old = await self.add(ws.ADD_EXAMPLE, EXAMPLE)
        done = await self.add(ws.ADD_EXAMPLE, EXAMPLE)
        async with self.db.guild(GUILD) as conn:
            await conn.execute(
                "UPDATE web_recap_style_actions SET created_at = created_at - 100000 WHERE id = %s",
                (old,),
            )
            await conn.execute(
                "UPDATE web_recap_style_actions SET state = 'done' WHERE id = %s", (done,)
            )
        await self.queue.sweep(GUILD)
        self.assertEqual(await self.row(old), {})
        self.assertEqual((await self.row(done))["arg"], "")

    async def test_the_oldest_comes_first_and_another_servers_are_not_taken(self) -> None:
        first = await self.add(ws.USE_DEFAULTS)
        await self.add(ws.USE_DEFAULTS)
        claim = await self.queue.claim_next(GUILD)
        assert claim is not None
        self.assertEqual(claim.id, first)
        self.assertIsNone(await self.queue.claim_next(GUILD + 1))

    async def test_another_campaigns_style_is_left_alone(self) -> None:
        await self.styles.add(GUILD, self.other.id, DM, EXAMPLE)
        await self.add(ws.USE_DEFAULTS)
        await self.work()
        self.assertEqual([e.text for e in await self.styles.own(GUILD, self.other.id)], [EXAMPLE])


class WhatIsLogged(Applying):
    async def test_the_round_logs_the_action_and_the_word_only(self) -> None:
        import logging

        from dmbot.bot import DMBot

        await self.add(ws.ADD_EXAMPLE, EXAMPLE)
        me = SimpleNamespace(
            web_recap_style=self.queue,
            guilds=[SimpleNamespace(id=GUILD)],
            _closing=False,
            _web_style_swept={},
            **self.bot.__dict__,
        )
        with self.assertLogs("dmbot", level=logging.DEBUG) as logs:
            await DMBot.run_web_recap_style(cast(Any, me))
        shown = "\n".join(logs.output)
        self.assertIn("Recap style add_example: done", shown)
        self.assertNotIn("Zedrick", shown)
        self.assertEqual(await self.own(), [EXAMPLE])
