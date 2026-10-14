"""`/dmbot migrate` (#1230): the screens around account_move. Fake stores and a fake
interaction: no Discord, no database."""

from __future__ import annotations

import unittest
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from dmbot.account_move import MoveError, Preview
from dmbot.ui import migrate

OLD, NEW = 1111, 2222


def interaction(user: int, bot: Any) -> Any:
    sent: list[tuple[str, Any]] = []
    done = {"v": False}

    async def send(text: str, **kw: Any) -> None:
        sent.append((text, kw.get("view")))

    async def defer(**_: Any) -> None:
        done["v"] = True

    it = MagicMock()
    it.user = SimpleNamespace(id=user)
    it.client = bot
    it.response.is_done = lambda: done["v"]
    it.response.defer = defer
    it.response.send_message = send
    it.followup.send = send
    it.sent = sent
    return it


def bot_with(**moves: Any) -> Any:
    class Conn:
        async def execute(self, *_: Any) -> Any:
            return SimpleNamespace(
                fetchone=AsyncMock(return_value={"payment_email": "p@example.com"})
            )

    @asynccontextmanager
    async def user(_: int) -> Any:
        yield Conn()

    stub = SimpleNamespace(
        can_move=AsyncMock(return_value=None),
        make_code=AsyncMock(return_value="ABCD2345"),
        cancel_code=AsyncMock(),
        find=AsyncMock(return_value=OLD),
        preview=AsyncMock(return_value=Preview("table", ("Frostmaiden", "Sunken *City*"))),
        move=AsyncMock(),
    )
    for name, value in moves.items():
        setattr(stub, name, value)
    return SimpleNamespace(
        account_moves=stub,
        mailer=SimpleNamespace(send=AsyncMock(return_value="sent")),
        meter=SimpleNamespace(db=SimpleNamespace(user=user)),
        after_account_move=AsyncMock(),
    )


class GettingACode(unittest.IsolatedAsyncioTestCase):
    async def test_the_code_is_emailed_and_never_shown(self) -> None:
        bot = bot_with()
        it = interaction(OLD, bot)
        await migrate.get_code(it)
        self.assertEqual(it.sent[0][0], migrate.EMAILED)
        body = bot.mailer.send.await_args.kwargs["body"]
        self.assertIn("ABCD-2345", body)
        self.assertEqual(bot.mailer.send.await_args.kwargs["to"], "p@example.com")
        self.assertNotIn("ABCD", it.sent[0][0])
        # One email for each code, so asking again is never hidden behind the first email.
        kind = bot.mailer.send.await_args.kwargs["kind"]
        self.assertTrue(kind.startswith("move-code:"))
        self.assertNotIn("ABCD", kind)  # stored and logged: the code itself is in neither
        self.assertNotIn("2345", kind)
        from dmbot.account_move import hash_code

        self.assertNotIn(hash_code("ABCD2345")[:8], kind)  # nothing derived from the code
        self.assertNotIn("example.com", it.sent[0][0])

    async def test_an_unpaid_account_is_told_why(self) -> None:
        bot = bot_with(can_move=AsyncMock(return_value="Moving needs an active paid plan."))
        it = interaction(OLD, bot)
        await migrate.get_code(it)
        self.assertEqual(it.sent[0][0], "Moving needs an active paid plan.")
        bot.account_moves.make_code.assert_not_called()

    async def test_no_email_on_file_makes_no_code(self) -> None:
        bot = bot_with()

        class Conn:
            async def execute(self, *_: Any) -> Any:
                return SimpleNamespace(fetchone=AsyncMock(return_value={"payment_email": None}))

        @asynccontextmanager
        async def user(_: int) -> Any:
            yield Conn()

        bot.meter.db.user = user
        it = interaction(OLD, bot)
        await migrate.get_code(it)
        self.assertEqual(it.sent[0][0], migrate.NO_EMAIL)
        bot.account_moves.make_code.assert_not_called()

    async def test_a_failed_email_says_so_and_a_store_error_still_answers(self) -> None:
        bot = bot_with()
        bot.mailer.send = AsyncMock(return_value="failed")
        it = interaction(OLD, bot)
        await migrate.get_code(it)
        self.assertEqual(it.sent[0][0], migrate.EMAIL_FAILED)
        bot.account_moves.cancel_code.assert_awaited_once_with(OLD)  # no code nobody has seen
        bad = bot_with(make_code=AsyncMock(side_effect=RuntimeError("down")))
        it2 = interaction(OLD, bad)
        await migrate.get_code(it2)
        self.assertEqual(it2.sent[0][0], migrate.NOT_READY)


class Importing(unittest.IsolatedAsyncioTestCase):
    async def test_a_good_code_shows_what_moves_and_moves_nothing_yet(self) -> None:
        bot = bot_with()
        it = interaction(NEW, bot)
        await migrate.look_up(it, "abcd-2345")
        text, view = it.sent[0]
        self.assertIn("Plan: **Table**", text)
        self.assertIn(f"From: <@{OLD}>", text)
        self.assertIn("card that pays for it moves too", text)
        self.assertIn("Recording agreements don't move", text)
        self.assertIn("• Frostmaiden", text)
        self.assertIn("Sunken \\*City\\*", text)  # a campaign name can't format the message
        self.assertIsNotNone(view)
        bot.account_moves.move.assert_not_called()

    async def test_a_long_list_of_campaigns_is_cut(self) -> None:
        names = tuple(f"Campaign {i}" for i in range(12))
        text = migrate.confirm_text(Preview("table", names), OLD)
        self.assertIn("…and 4 more", text)
        self.assertLess(len(text), 1500)

    async def test_a_wrong_code_says_so(self) -> None:
        bot = bot_with(find=AsyncMock(side_effect=MoveError("That code didn't work.")))
        it = interaction(NEW, bot)
        await migrate.look_up(it, "nope")
        self.assertEqual(it.sent[0][0], "That code didn't work.")

    async def test_confirming_moves_once_and_tells_the_old_account(self) -> None:
        bot = bot_with()
        view = migrate.ConfirmMove(OLD, NEW, "ABCD2345")
        it = interaction(NEW, bot)
        await view._yes(it)
        bot.account_moves.move.assert_awaited_once_with(OLD, NEW, "ABCD2345")
        bot.after_account_move.assert_awaited_once_with(OLD, NEW)
        self.assertIn("Moved.", it.sent[0][0])
        self.assertTrue(view.is_finished())

    async def test_a_refusal_at_the_last_step_is_shown_and_nothing_is_announced(self) -> None:
        bot = bot_with(
            move=AsyncMock(side_effect=MoveError("This account moved less than 30 days ago."))
        )
        it = interaction(NEW, bot)
        await migrate.ConfirmMove(OLD, NEW, "ABCD2345")._yes(it)
        self.assertIn("less than 30 days", it.sent[0][0])
        self.assertIn("Start again with `/dmbot migrate`", it.sent[0][0])
        bot.after_account_move.assert_not_called()

    async def test_only_the_person_who_asked_can_press_the_buttons(self) -> None:
        bot = bot_with()
        view = migrate.ConfirmMove(OLD, NEW, "ABCD2345")
        it = interaction(OLD, bot)  # someone else
        await view._yes(it)
        bot.account_moves.move.assert_not_called()
        self.assertEqual(it.sent[0][0], "This one isn't yours.")
        self.assertFalse(view.is_finished())

    async def test_the_button_and_intro_say_import_my_account(self) -> None:
        self.assertEqual(migrate.IMPORT_LABEL, "📥 Import my account")
        self.assertLessEqual(len(migrate.IMPORT_LABEL), 80)
        self.assertIn("Import my account", migrate.INTRO)

    async def test_the_help_says_how_to_give_campaigns_to_someone_else(self) -> None:
        self.assertIn("Hand over", migrate.INTRO)
        self.assertIn("card", migrate.INTRO)


if __name__ == "__main__":
    unittest.main()
