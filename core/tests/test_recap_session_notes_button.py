"""The 🗒️ Session notes button on a recap line (#3026): DM-only, read-only, a numbered list in
private messages that never shortens a beat, PlotBot's notes never shown, the plain reply when
there are none, and a button that survives a restart and fits Discord's row limits. Invented names
and text only; the store is stubbed here (its own tests cover the database)."""

from __future__ import annotations

import re
import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from dmbot.mentions import NO_PINGS
from dmbot.recaps.beats import Beat
from dmbot.ui.decision_lines import DecisionLines
from dmbot.ui.recap_dm import (
    _TEMPLATE,
    GONE,
    NO_NOTES,
    NO_STORE,
    NOT_YOURS,
    NOTES_FAILED,
    RecapButton,
    ready_view,
)

GUILD, DM_ID, PLAYER = 5, 7, 11
CID = "c" * 32
AT = 100


def beat(number: int, text: str, **extra: Any) -> Any:
    return SimpleNamespace(number=number, what=text, **extra)


def sheet_of(*beats: Any) -> Any:
    """A stored notes row as the store hands it over: `sheet()` gives the beats."""
    return SimpleNamespace(sheet=lambda: list(beats))


def press(
    *,
    user: int = DM_ID,
    notes: Any = None,
    campaign_present: bool = True,
    store_error: bool = False,
    recaps_present: bool = True,
) -> Any:
    store_get = AsyncMock(
        side_effect=RuntimeError("down") if store_error else None, return_value=notes
    )
    campaign = SimpleNamespace(guild_id=GUILD, id=CID, dm_user_ids=frozenset({DM_ID}))
    decision_lines = MagicMock(spec=DecisionLines)  # remember_pressed is recorded, not run
    bot = SimpleNamespace(
        campaigns=SimpleNamespace(
            get=AsyncMock(return_value=campaign if campaign_present else None)
        ),
        recaps=SimpleNamespace(db=MagicMock()) if recaps_present else None,
        decision_lines=decision_lines,
    )
    answered = {"done": False}  # a defer answers the interaction, as Discord does

    async def defer(**_: Any) -> None:
        answered["done"] = True

    response = SimpleNamespace(
        is_done=lambda: answered["done"],
        send_message=AsyncMock(),
        defer=AsyncMock(side_effect=defer),
        edit_message=AsyncMock(),
    )
    interaction = SimpleNamespace(
        client=bot,
        guild=SimpleNamespace(id=GUILD),
        channel=object(),
        user=SimpleNamespace(id=user),
        response=response,
        followup=SimpleNamespace(send=AsyncMock()),
        message=SimpleNamespace(id=900),
    )
    return RecapButton("notes", CID, AT), bot, interaction, store_get


def custom_id(item: Any) -> str:
    """A view item's custom id (a link button has none)."""
    return str(getattr(item, "custom_id", "") or "")


def sent_texts(interaction: Any) -> list[str]:
    return [str(c.args[0]) for c in interaction.followup.send.await_args_list]


class TheNotesButton(unittest.IsolatedAsyncioTestCase):
    async def run_with(self, notes: Any, **kw: Any) -> Any:
        """Press the button as a DM whose campaign is there and return the interaction."""
        button, _bot, interaction, store_get = press(notes=notes, **kw)
        with patch("dmbot.ui.recap_dm.SessionNotesStore") as store_cls:
            store_cls.return_value.get = store_get
            await button.callback(interaction)
        return interaction

    async def test_only_a_dm_of_the_campaign_may_press_it_and_the_store_is_not_read(self) -> None:
        button, _bot, interaction, store_get = press(user=PLAYER, notes=sheet_of(beat(1, "x")))
        with patch("dmbot.ui.recap_dm.SessionNotesStore") as store_cls:
            store_cls.return_value.get = store_get
            await button.callback(interaction)

        self.assertEqual(interaction.response.send_message.await_args.args[0], NOT_YOURS)
        store_get.assert_not_awaited()
        interaction.followup.send.assert_not_awaited()

    async def test_a_campaign_that_is_gone_is_said_so(self) -> None:
        button, _bot, interaction, store_get = press(
            notes=sheet_of(beat(1, "x")), campaign_present=False
        )
        with patch("dmbot.ui.recap_dm.SessionNotesStore") as store_cls:
            store_cls.return_value.get = store_get
            await button.callback(interaction)

        self.assertEqual(interaction.response.send_message.await_args.args[0], GONE)
        store_get.assert_not_awaited()

    async def test_a_dm_gets_the_numbered_list_privately_with_no_mentions(self) -> None:
        interaction = await self.run_with(
            sheet_of(beat(1, "The gate opened."), beat(2, "Mira fled north."))
        )

        interaction.response.defer.assert_awaited_once_with(ephemeral=True, thinking=False)
        self.assertEqual(len(sent_texts(interaction)), 1)
        text = sent_texts(interaction)[0]
        self.assertTrue(text.startswith("**Session notes**\n"))
        self.assertIn("1. The gate opened.\n2. Mira fled north.", text)
        for call in interaction.followup.send.await_args_list:
            self.assertTrue(call.kwargs["ephemeral"])
            self.assertIs(call.kwargs["allowed_mentions"], NO_PINGS)

    async def test_a_long_sheet_comes_back_in_parts_of_at_most_2000_characters_nothing_shortened(
        self,
    ) -> None:
        beats = [beat(i, f"Beat {i}. " + "word " * 46) for i in range(1, 121)]
        interaction = await self.run_with(sheet_of(*beats))

        parts = sent_texts(interaction)
        self.assertGreater(len(parts), 1)
        for part in parts:
            self.assertLessEqual(len(part), 2000)
        joined = "\n".join(parts)
        for b in beats:
            self.assertIn(f"{b.number}. {b.what.strip()}", joined)  # (the cut drops only a space)
        self.assertTrue(parts[0].startswith("**Session notes**"))
        self.assertFalse(any(p.startswith("**Session notes**") for p in parts[1:]))

    async def test_no_notes_or_an_empty_sheet_gets_the_plain_reply(self) -> None:
        for notes in (None, sheet_of()):
            with self.subTest(empty=notes is None):
                interaction = await self.run_with(notes)
                self.assertEqual(sent_texts(interaction), [NO_NOTES])

    async def test_plotbots_notes_and_keeps_are_never_shown(self) -> None:
        real = Beat(
            number=1, what="Mira fled north.", who=(), lines=(), kind="scene",
            note="SECRET NOTE TEXT", keep=True,
        )  # fmt: skip
        interaction = await self.run_with(sheet_of(real))

        text = "\n".join(sent_texts(interaction))
        self.assertIn("Mira fled north.", text)
        self.assertNotIn("SECRET NOTE TEXT", text)
        self.assertNotIn("KEEP THIS", text)

    async def test_the_button_survives_a_restart_and_the_view_fits_discords_limits(self) -> None:
        view = ready_view(CID, AT, site="https://site.example.com")
        rows: dict[int, int] = {}
        for child in view.children:
            row = child.row or 0  # (None means the first row)
            rows[row] = rows.get(row, 0) + 1
        self.assertLessEqual(max(rows.values()), 5)
        self.assertLessEqual(len(view.children), 25)

        notes = next(c for c in view.children if custom_id(c).startswith("dmbot:recap:notes:"))
        match = re.fullmatch(_TEMPLATE, custom_id(notes))
        self.assertIsNotNone(match)
        revived = await RecapButton.from_custom_id(None, None, match)  # type: ignore[arg-type]
        self.assertEqual((revived.act, revived.campaign_id, revived.session_at), ("notes", CID, AT))

    async def test_the_button_is_on_the_line_without_a_website_too(self) -> None:
        view = ready_view(CID, AT, site="")
        customs = [custom_id(c) for c in view.children]
        self.assertTrue(any(c.startswith("dmbot:recap:notes:") for c in customs))
        self.assertLessEqual(len(view.children), 25)


class TheNotesErrors(unittest.IsolatedAsyncioTestCase):
    async def test_a_store_that_fails_says_so_and_nothing_else_happens(self) -> None:
        button, bot, interaction, store_get = press(store_error=True)
        with patch("dmbot.ui.recap_dm.SessionNotesStore") as store_cls:
            store_cls.return_value.get = store_get
            await button.callback(interaction)

        self.assertEqual(sent_texts(interaction), [NOTES_FAILED])
        bot.decision_lines.remember_pressed.assert_not_called()

    async def test_without_a_recap_store_the_notes_say_so_before_any_read(self) -> None:
        button, _bot, interaction, store_get = press(recaps_present=False)
        with patch("dmbot.ui.recap_dm.SessionNotesStore") as store_cls:
            store_cls.return_value.get = store_get
            await button.callback(interaction)

        self.assertEqual(interaction.response.send_message.await_args.args[0], NO_STORE)
        store_get.assert_not_awaited()

    async def test_the_read_asks_for_this_campaign_and_this_session_only(self) -> None:
        button, bot, interaction, store_get = press(notes=sheet_of(beat(1, "x")))
        with patch("dmbot.ui.recap_dm.SessionNotesStore") as store_cls:
            store_cls.return_value.get = store_get
            await button.callback(interaction)

        store_get.assert_awaited_once_with(GUILD, CID, AT)
        bot.decision_lines.remember_pressed.assert_not_called()
