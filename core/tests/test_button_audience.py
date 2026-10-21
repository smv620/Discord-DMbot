"""Every button says who may press it, and the "DM only" ones check it (#1840).

CLAUDE.md, "DM authority": DM-screen content goes only to the DM. DMbot has about a hundred
`DynamicItem` buttons and each callback does its own check. A new button that forgot it would show
secret names or notes to a player who pressed it, and nothing would say so until it happened live.
So every button class is listed here with its audience, the test fails for a class with no entry
(or an entry for a class that is gone), and an entry that says the audience is the DM must have a
real DM check on the path from its callback.

What this proves: a named check is reachable from the callback (its own methods, and the functions
they call in the same module, three steps deep) or in a function listed in `CHECKED_ELSEWHERE`,
which is read too. It does not prove the check comes before every line the callback sends. For the
callback's own body it also checks that the first reply that carries content (not a bare constant
message) comes after the check. No Discord and no network.
"""

from __future__ import annotations

import ast
import importlib
import inspect
import sys
import textwrap
import unittest
from collections import Counter

from tests.test_dynamic_items import all_dynamic_items

DM = "dm"  # the campaign's DMs (a few also let a server manager in: `may_change_screen`)
OWNER = "owner"  # the campaign's owner: the one DM who may hand it over, pause it or delete it
ANYONE = "anyone"  # anyone in the server; what it shows holds no DM-screen content
SELF = "self"  # only the person it is for: their own consent, sheet, settings or offer
AUDIENCES = {DM, OWNER, ANYONE, SELF}

# Names whose presence on the path means "this person was checked".
DM_CHECKS = {
    "dm_user_ids",  # `user.id in campaign.dm_user_ids`
    "is_dm",  # a live table's `is_dm(user_id)`
    "_is_dm",
    "require_dm",
    "_dm_campaign",
    "_dm_and_watch",
    "_allowed",  # settings and adventure card: wrap `may_change_screen` / `_dm_campaign`
    "_may",
    "may_change",
    "may_change_screen",
    "may_look_up",
    "may_stop",
    "may_press",
    "can_run",  # DM or server manager
    "sees_secrets",  # the DM only
    "_require_dm",
    "_locked_dm_state",
}
OWNER_CHECKS = {"owner_user_id", "from_user_id", "refusal"}  # from_user_id: the offer's maker
# What a function listed in CHECKED_ELSEWHERE may use: a DM check, an owner check, or the filter
# that keeps secret names out of what a non-DM sees.
ANY_CHECK = DM_CHECKS | OWNER_CHECKS

# Calls that show the presser something. `defer` is not one: it shows nothing.
REPLIES = {"send_message", "edit_message", "send_modal", "edit_original_response"}

# One entry per button class: "module.Class" (without the `dmbot.` prefix) → audience. The
# comment on a group says why the ones that are not obvious are what they are.
AUDIENCE: dict[str, str] = {
    # A person's own consent, menu, warning and deletion: each acts on `interaction.user.id` only.
    **dict.fromkeys(
        [
            "consent_dm.CloseButton",
            "consent_dm.ConsentButton",
            "consent_dm.DeclineButton",
            "consent_dm.DeleteButton",
            "consent_dm.DeleteNoButton",
            "consent_dm.DeleteYesButton",
            "consent_dm.KeepButton",
            "consent_dm.MenuButton",
            "consent_dm.SaveVoiceButton",
            "consent_dm.StartButton",
            "consent_dm.StopButton",
            "consent_dm.StopSavingButton",
            "consent_dm.StopYesButton",
            "fightbot.FightButton",  # the presser's own character's reminders (`played_by`)
            "dm_screen.handover.AcceptOfferButton",  # the person the offer is for
            "dm_screen.handover.DeclineOfferButton",
            "dm_screen.handover.NotNowButton",
            "ui.sheets.MySheetButton",  # `characters_of(guild, interaction.user.id)`
            # Only the person the list was made for (`kept.owner_id`, the DM who asked): anyone else
            # gets "not yours" before anything is shown (#1957).
            "ui.looked_at.LookedButton",
            "ui.looked_at.OpenCard",
        ],
        SELF,
    ),
    **dict.fromkeys(
        [
            "dm_screen.delete_campaign.DeleteButton",
            "dm_screen.handover.HandoverButton",
            "dm_screen.handover.WithdrawOfferButton",
            "dm_screen.pause.PauseButton",
        ],
        OWNER,
    ),
    **dict.fromkeys(
        [
            # A player who chose to peek hides again: changes only their own channel overwrite.
            "dm_screen.buttons.HideButton",
            # Peek asks the visibility the DM set (private, opt-in or open) before it shows
            # anything, and warns about spoilers first.
            "dm_screen.buttons.PeekButton",
            "dm_screen.dm_bot_buttons.HelpButton",
            "dm_screen.dm_bot_buttons.OpenButton",  # a card for the presser's role
            "dm_screen.dm_bot_buttons.RuleButton",  # a rule lookup: players get the player view
            "dm_screen.settings.RuleLookupButton",
            # Anyone in the server may download a transcript (CLAUDE.md: transcripts are shared).
            "ui.transcripts.DownloadButton",
            # A name card: `show_card` leaves secret names out unless the presser is a DM.
            "ui.sheets.OpenCardButton",
        ],
        ANYONE,
    ),
    **dict.fromkeys(
        [
            "dm_screen.adventure_card.AddButton",
            "dm_screen.adventure_card.AdventureButton",
            "dm_screen.adventure_card.BackToFilesButton",
            "dm_screen.adventure_card.MatchButton",
            "dm_screen.adventure_card.ReadButton",
            "dm_screen.adventure_card.ReadNoButton",
            "dm_screen.adventure_card.ReadYesButton",
            "dm_screen.adventure_card.RefreshButton",
            "dm_screen.adventure_card.RemoveButton",
            "dm_screen.adventure_card.RemoveNoButton",
            "dm_screen.adventure_card.RemoveYesButton",
            "dm_screen.adventure_card.SeeButton",
            "dm_screen.buttons.StopListeningButton",
            "dm_screen.buttons.VisibilityButton",
            "dm_screen.clock.ClockButton",
            "dm_screen.clock.ClockPicker",
            "dm_screen.clock.ClockUndoButton",
            "dm_screen.clock.CombatUndoButton",
            "dm_screen.clock.SplitUndoButton",
            "dm_screen.clock.SuggestionButton",
            "dm_screen.effects.EffectButton",
            "dm_screen.game_masters.GameMastersButton",
            "dm_screen.handover.TakeOnButton",
            "dm_screen.house_sync.HouseSyncButton",
            "dm_screen.house_voice.HouseVoiceButton",
            "dm_screen.left_out.PutBackButton",
            "dm_screen.name_questions.FixUndoButton",
            "dm_screen.name_questions.NameAnswerUndoButton",
            "dm_screen.name_questions.NameQuestionButton",
            "dm_screen.pack_suggestion.PackSuggestionButton",
            "dm_screen.pack_update.PackUpdateButton",
            "dm_screen.quiet_stop.KeepListeningButton",
            "dm_screen.names_button.NamesButton",
            "dm_screen.recap_panel.RecapsButton",
            "dm_screen.rules_cards.RulesCardButton",
            "dm_screen.set_suggestion.SuggestionButton",
            "dm_screen.set_update.SetUpdateButton",
            "dm_screen.settings.FeaturesButton",
            "dm_screen.settings.HomebrewButton",
            "dm_screen.settings.QuietStopButton",
            "dm_screen.settings.HouseFileButton",
            "dm_screen.settings.InviteButton",
            "dm_screen.settings.LevelButton",
            "dm_screen.settings.RulesCardsButton",
            "dm_screen.settings.SettingsButton",
            "dm_screen.settings.VisibilityMenuButton",
            "dm_screen.settings.ManagementMenuButton",
            "dm_screen.settings.BackToSettingsButton",
            "dm_screen.settings.SettingsVisibilityButton",
            "dm_screen.settings.SharedRulesButton",
            "dm_screen.speaker_question.SpeakerQuestionButton",
            "dm_screen.story_mode.StoryButton",
            "dm_screen.story_mode.StoryChoiceButton",
            "dm_screen.story_mode.StoryNoButton",
            "dm_screen.story_mode.StoryYesButton",
            "dm_screen.table_chat_panel.TableChatButton",
            "dm_screen.time_suggest.TimeJumpButton",
            "dm_screen.transcript_keep.KeepChoiceButton",
            "dm_screen.transcript_keep.KeepNoButton",
            "dm_screen.transcript_keep.KeepYesButton",
            "dm_screen.transcript_keep.TranscriptsButton",
            "ui.claims_review.ClaimsReviewButton",
            "ui.continuity.ContinuityButton",
            "ui.guide.GuideButton",
            "ui.heard_review.HeardReviewButton",
            "ui.line_fix.FixLineButton",
            "ui.line_fix.LineFixButton",
            "ui.name_card.UndoButton",
            "ui.name_card.UndoConnectionButton",
            "ui.name_card.UndoSpellingButton",
            "ui.name_card.FinishPastButton",
            "ui.name_card.UndoRunButton",
            "ui.name_lists.UndoListButton",
            "ui.names.RecapNamesButton",
            "ui.names.ReviewButton",
            "ui.people_review.PeopleReviewButton",
            "ui.live_heads_up.LiveHeadsUpButton",
            "ui.recap_dm.RecapButton",
            "ui.recap_hold.WriteNowButton",
            "ui.recap_share.ShareButton",
            "ui.recap_short.ShortRecapButton",
            "ui.secret_aloud.SecretAloudButton",
            "ui.secret_reveal.RevealButton",
            "ui.story_watch.KnowButton",
            "ui.story_watch.WordsButton",
            "ui.threads.ThreadListButton",
            "ui.threads.ThreadsButton",
        ],
        DM,
    ),
}

# Buttons whose callback hands the check to a function outside its own module path, each with the
# functions that make it ("module:Class.method" or "module:function"). The test reads their code
# for a check, so a refactor that drops the check fails here.
CHECKED_ELSEWHERE: dict[str, list[str]] = {
    # The clock's store checks the DM in the same transaction as the change.
    "dm_screen.clock.ClockPicker": ["dmbot.timebot.store:ClockStore.pick"],
    "dm_screen.clock.ClockUndoButton": ["dmbot.timebot.store:ClockStore.change_named"],
    "dm_screen.clock.CombatUndoButton": ["dmbot.timebot.store:ClockStore.change_named"],
    "dm_screen.clock.SplitUndoButton": [
        "dmbot.timebot.store:ClockStore.undo_split",
        "dmbot.timebot.store:ClockStore.undo_rejoin",
    ],
    "dm_screen.effects.EffectButton": [
        "dmbot.timebot.effects:EffectStore.end",
        "dmbot.timebot.effects:EffectStore.extend",
    ],
    # The live table answers: `table.is_dm(user_id)` before anything is changed.
    "dm_screen.left_out.PutBackButton": ["dmbot.bot:DMBot.put_back"],
    "dm_screen.name_questions.FixUndoButton": ["dmbot.bot:DMBot.undo_fix"],
    "dm_screen.name_questions.NameQuestionButton": [
        "dmbot.bot:DMBot.can_type_answer",
        "dmbot.bot:DMBot.answer_name_question",
    ],
    "dm_screen.speaker_question.SpeakerQuestionButton": ["dmbot.bot:DMBot.answer_speaker_question"],
    # The store gives a campaign with no owner only to one of its DMs.
    "dm_screen.handover.TakeOnButton": ["dmbot.campaigns.store:CampaignStore.take_ownership"],
    "dm_screen.rules_cards.RulesCardButton": ["dmbot.dm_screen.rules_cards:press"],
    "ui.sheets.OpenCardButton": ["dmbot.ui.name_card:show_card"],
    # The names home asks `logic.can_run` (the campaign's DMs and server managers) when it opens.
    "dm_screen.names_button.NamesButton": ["dmbot.ui.names:_campaign_for"],
}

# Direct callbacks where the first reply may come before the check, each with why. A reply with a
# bare constant message ("gone", "failed") is never a leak and is not counted at all.
REPLY_BEFORE_CHECK: dict[str, str] = {}


def key(cls: type) -> str:
    return f"{cls.__module__.removeprefix('dmbot.')}.{cls.__qualname__}"


def function_at(path: str) -> object:
    module_name, _, dotted = path.partition(":")
    found: object = importlib.import_module(module_name)
    for part in dotted.split("."):
        found = getattr(found, part)
    return found


def names_in(function: object) -> set[str]:
    """Every name and attribute the function's source mentions."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(function)))  # type: ignore[arg-type]
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            found.add(node.id)
        elif isinstance(node, ast.Attribute):
            found.add(node.attr)
    return found


def reachable_names(cls: type, depth: int = 3) -> set[str]:
    """Names mentioned by the class's methods and by the `dmbot` functions they call (looked up
    in the module each function lives in), `depth` steps out."""
    seen: set[object] = set()
    found: set[str] = set()
    todo: list[tuple[object, int]] = [
        (f, depth) for f in vars(cls).values() if inspect.isfunction(f)
    ]
    while todo:
        function, left = todo.pop()
        if function in seen:
            continue
        seen.add(function)
        try:
            names = names_in(function)
        except (OSError, TypeError):
            continue
        found |= names
        if left:
            home = getattr(function, "__module__", "")
            module = sys.modules.get(home)
            for name in names:
                target = getattr(module, name, None)
                if inspect.isfunction(target) and (
                    target.__module__.startswith("dmbot")
                    or target.__module__ == function.__module__
                ):
                    todo.append((target, left - 1))
    return found


def first_lines(source: str, checks: set[str]) -> tuple[int | None, int | None]:
    """(first line that mentions a check, first line that replies with more than a constant)
    in a function's own body."""
    check_line: int | None = None
    reply_line: int | None = None
    for node in ast.walk(ast.parse(textwrap.dedent(source))):
        if isinstance(node, ast.Name | ast.Attribute):
            name = node.id if isinstance(node, ast.Name) else node.attr
            if name in checks:
                check_line = min(check_line or node.lineno, node.lineno)
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in REPLIES | {"send"}
            and not _constant_reply(node)
        ):
            reply_line = min(reply_line or node.lineno, node.lineno)
    return check_line, reply_line


def _constant_reply(call: ast.Call) -> bool:
    """A reply whose text is a bare constant (an UPPER_CASE name or a `messages.X`): a refusal
    or a "that's gone", never campaign data."""
    if not call.args:
        return False
    first = call.args[0]
    if isinstance(first, ast.Name):
        return first.id.isupper()
    return isinstance(first, ast.Attribute) and first.attr.isupper()


def problems() -> list[str]:
    classes = {key(c): c for c in all_dynamic_items()}
    out = [f"{k}: no audience listed" for k in sorted(classes.keys() - AUDIENCE.keys())]
    out += [f"{k}: listed but no such button" for k in sorted(AUDIENCE.keys() - classes.keys())]
    out += [
        f"{k}: listed in CHECKED_ELSEWHERE but no such button"
        for k in sorted(CHECKED_ELSEWHERE.keys() - classes.keys())
    ]
    for k, audience in sorted(AUDIENCE.items()):
        if audience not in AUDIENCES:
            out.append(f"{k}: unknown audience {audience!r}")
    for k, audience in sorted(AUDIENCE.items()):
        cls = classes.get(k)
        if cls is None or audience not in (DM, OWNER):
            continue
        checks = DM_CHECKS if audience == DM else OWNER_CHECKS
        if not (reachable_names(cls) & checks):
            elsewhere = CHECKED_ELSEWHERE.get(k, [])
            if not elsewhere:
                out.append(f"{k}: audience {audience}, but no check on its path")
        for path in CHECKED_ELSEWHERE.get(k, []):
            try:
                names = names_in(function_at(path))
            except (AttributeError, ImportError, OSError, TypeError):
                out.append(f"{k}: CHECKED_ELSEWHERE {path} can't be read")
                continue
            if not names & ANY_CHECK:
                out.append(f"{k}: {path} has no check in it")
        callback = vars(cls).get("callback")
        if callback is not None and k not in REPLY_BEFORE_CHECK:
            check, reply = first_lines(inspect.getsource(callback), checks)
            if check is not None and reply is not None and reply < check:
                out.append(f"{k}: replies (line {reply}) before its check (line {check})")
    return out


class ButtonAudienceTest(unittest.TestCase):
    def test_every_button_has_an_audience_and_the_dm_ones_check(self) -> None:
        self.assertEqual(problems(), [])

    def test_the_lists_hold_no_stale_entries(self) -> None:
        for k in REPLY_BEFORE_CHECK:
            self.assertIn(k, AUDIENCE, f"{k} is no longer a button")

    def test_a_check_on_the_path_is_found(self) -> None:
        class Sample:
            async def callback(self) -> None:
                await helper()

        async def helper() -> None:
            ok = 1 in campaign.dm_user_ids  # type: ignore[name-defined]  # noqa: F821
            return None if not ok else None  # noqa: RUF034

        Sample.callback.__globals__["helper"] = helper
        self.assertIn("dm_user_ids", reachable_names(Sample))

    def test_a_button_with_no_check_is_seen(self) -> None:
        class Bare:
            async def callback(self) -> None:
                await interaction.response.send_message(secret)  # type: ignore[name-defined]  # noqa: F821

        self.assertFalse(reachable_names(Bare) & DM_CHECKS)

    def test_a_reply_before_the_check_is_seen(self) -> None:
        source = (
            "async def callback(self, interaction):\n"
            "    await interaction.response.send_message(card_text(campaign))\n"
            "    if interaction.user.id not in campaign.dm_user_ids:\n"
            "        return\n"
        )
        check, reply = first_lines(source, DM_CHECKS)
        assert check is not None and reply is not None
        self.assertLess(reply, check)

    def test_a_constant_refusal_before_the_check_is_fine(self) -> None:
        source = (
            "async def callback(self, interaction):\n"
            "    await interaction.response.send_message(GONE, ephemeral=True)\n"
            "    if interaction.user.id not in campaign.dm_user_ids:\n"
            "        return\n"
        )
        check, reply = first_lines(source, DM_CHECKS)
        self.assertIsNone(reply)
        self.assertIsNotNone(check)

    def test_counts(self) -> None:
        counts = Counter(AUDIENCE.values())
        self.assertEqual(sum(counts.values()), len(AUDIENCE))
