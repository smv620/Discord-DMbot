"""`/dmbot houserules` (#865): the list, Add, Edit and Remove (with its confirm), for the
DM and for everyone else. The store is a stand-in with the real one's rules for who may
change what; the real one is tested against the database in test_house_rules_store."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord

from dmbot.campaigns import Campaign
from dmbot.rules import house
from dmbot.rules.house import HouseRule, HouseRuleError
from dmbot.ui import house_rules as ui
from dmbot.ui.logic import PHONE_LABEL_MAX, SELECT_OPTIONS_MAX
from tests.test_memory_names import FakeResponse

GUILD, DM, PLAYER = 1, 7, 8
NOW = 1_700_000_000


def campaign(
    cid: str = "c1", name: str = "Frostmaiden", dms: frozenset[int] = frozenset({DM})
) -> Campaign:
    return Campaign(
        cid,
        GUILD,
        name,
        NOW - 86400,
        NOW - 3600,
        "2024",
        "2014",
        True,
        dms,
        None,
        50,
        "peek",
    )


def rule(n: int, text: str = "", instead: str | None = None) -> HouseRule:
    return HouseRule(n, "c1", text or f"Rule number {n}", instead, None, None, DM, NOW + n, NOW + n)


class FakeStore:
    """Like `HouseRuleStore`, in memory: newest first, only DMs change anything."""

    def __init__(self, dms: dict[str, frozenset[int]]) -> None:
        self.dms = dms
        self.rules: dict[str, list[HouseRule]] = {}
        self.next_id = 1
        self.calls: list[tuple[Any, ...]] = []

    def seed(self, cid: str, count: int, **kw: Any) -> None:
        for _ in range(count):
            self.rules.setdefault(cid, []).append(rule(self.next_id, **kw))
            self.next_id += 1

    def _dm(self, cid: str, user_id: int) -> None:
        if user_id not in self.dms.get(cid, frozenset()):
            raise HouseRuleError(house.NOT_DM)

    async def list(self, guild_id: int, cid: str) -> list[HouseRule]:
        return list(reversed(self.rules.get(cid, [])))

    async def add(
        self, guild_id: int, cid: str, user_id: int, text: str, instead: str | None = None
    ) -> HouseRule:
        self.calls.append(("add", cid, user_id, text, instead))
        self._dm(cid, user_id)
        saved = HouseRule(
            self.next_id,
            cid,
            house.clean_rule(text),
            house.clean_optional(instead, "that"),
            None,
            None,
            user_id,
            NOW,
            NOW,
        )
        self.next_id += 1
        self.rules.setdefault(cid, []).append(saved)
        return saved

    async def edit(
        self, guild_id: int, cid: str, user_id: int, rule_id: int, text: str, instead: str = ""
    ) -> HouseRule:
        self.calls.append(("edit", cid, user_id, rule_id, text, instead))
        self._dm(cid, user_id)
        for i, existing in enumerate(self.rules.get(cid, [])):
            if existing.id == rule_id:
                changed = HouseRule(
                    rule_id,
                    cid,
                    house.clean_rule(text),
                    house.clean_optional(instead, "that"),
                    existing.scenario,
                    existing.session_id,
                    existing.created_by,
                    existing.created_at,
                    NOW + 99,
                )
                self.rules[cid][i] = changed
                return changed
        raise HouseRuleError(house.GONE)

    async def remove(
        self,
        guild_id: int,
        cid: str,
        user_id: int,
        rule_id: int,
        *,
        unchanged_since: int | None = None,
    ) -> HouseRule:
        self.calls.append(("remove", cid, user_id, rule_id))
        self._dm(cid, user_id)
        for i, existing in enumerate(self.rules.get(cid, [])):
            if existing.id == rule_id:
                if unchanged_since is not None and existing.updated_at != unchanged_since:
                    raise HouseRuleError(house.CHANGED)
                return self.rules[cid].pop(i)
        raise HouseRuleError(house.GONE)


class UITest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.campaigns = {"c1": campaign()}
        self.store = FakeStore({"c1": frozenset({DM})})
        self.playing: str | None = "c1"
        self.bot = SimpleNamespace(
            campaigns=SimpleNamespace(
                get=AsyncMock(side_effect=lambda g, cid: self.campaigns.get(cid)),
                list_campaigns=AsyncMock(side_effect=lambda g: list(self.campaigns.values())),
            ),
            house_rules=self.store,
            active_campaign_id=lambda guild_id: self.playing,
        )

    def it(self, user_id: int = DM, kind: discord.InteractionType | None = None) -> Any:
        user = MagicMock(spec=discord.Member)
        user.id = user_id
        edited: list[tuple[str, Any]] = []

        async def edit_original_response(*, content: str = "", view: Any = None, **_: Any) -> None:
            edited.append((content, view))

        return SimpleNamespace(
            client=self.bot,
            guild=SimpleNamespace(id=GUILD),
            guild_id=GUILD,
            user=user,
            type=kind or discord.InteractionType.component,
            response=FakeResponse(),
            followup=SimpleNamespace(send=AsyncMock()),
            edit_original_response=AsyncMock(side_effect=edit_original_response),
            edited=edited,
        )

    async def command(self, user_id: int = DM) -> Any:
        it = self.it(user_id, discord.InteractionType.application_command)
        await ui.dmbot_house_rules.callback(it)  # type: ignore[call-arg]
        return it

    @staticmethod
    def opened(it: Any) -> tuple[str, Any]:
        """What the command showed: it answers Discord first, so it comes as a follow-up."""
        call = it.followup.send.await_args
        return str(call.args[0]), call.kwargs.get("view")

    @staticmethod
    def labels(view: Any) -> list[str]:
        return [str(c.label) for c in view.children if isinstance(c, discord.ui.Button)]

    @staticmethod
    def shown(it: Any) -> tuple[str, Any]:
        """What the message was changed to: after answering first, through the webhook;
        otherwise as the answer itself."""
        text, view = (it.edited or it.response.edited)[0]
        return str(text), view

    async def submit(self, form: Any, user_id: int = DM) -> Any:
        it = self.it(user_id, discord.InteractionType.modal_submit)
        await form.on_submit(it)
        return it

    @staticmethod
    def press(view: Any, label: str) -> Any:
        return next(c for c in view.children if getattr(c, "label", None) == label)

    @staticmethod
    def told(it: Any) -> str:
        return str(it.followup.send.await_args.args[0])


class Text(unittest.TestCase):
    def test_a_line_is_numbered_and_says_what_it_replaces(self) -> None:
        self.assertEqual(ui.entry_text(3, rule(1, "Crits double")), "3. Crits double")
        self.assertEqual(
            ui.entry_text(1, rule(1, "Crits double", "Crits roll once")),
            "1. Crits double (instead of: Crits roll once)",
        )

    def test_the_dms_words_never_format_the_message(self) -> None:
        text = ui.entry_text(1, rule(1, "**bold** @everyone ||spoiler|| ```", "_x_"))
        self.assertNotIn("**bold**", text)
        self.assertIn("\\*\\*bold\\*\\*", text)
        self.assertIn("\\|\\|spoiler\\|\\|", text)

    def test_a_very_long_rule_is_cut_whole(self) -> None:
        text = ui.entry_text(1, rule(1, "*" * 500, "*" * 500))
        self.assertLessEqual(len(text), ui.ENTRY_MAX)
        self.assertTrue(text.endswith("…"))
        self.assertFalse(text.endswith("\\…"))  # never half an escape

    def test_pages_hold_ten_at_most_and_what_fits(self) -> None:
        self.assertEqual(ui.pages([]), [[]])
        short = [rule(n) for n in range(1, 26)]
        self.assertEqual([len(p) for p in ui.pages(short)], [10, 10, 5])
        long = [rule(n, "x" * 500, "y" * 500) for n in range(1, 6)]
        for page in ui.pages(long):
            used = sum(len(ui.entry_text(i + 1, long[i])) + 1 for i in page)
            self.assertLessEqual(used, ui.TEXT_MAX + ui.ENTRY_MAX)
        self.assertEqual([i for p in ui.pages(long) for i in p], [0, 1, 2, 3, 4])  # none lost

    def test_the_message_always_fits_discord(self) -> None:
        # Rules of every size, so that some page is as full as a page can be, with the
        # longest name and note around it (each of them escaped, so they double).
        name, note = "*" * 80, "🗑 Removed house rule: " + "*" * 300
        for size in (1, 40, 150, 330, 500):
            worst = [rule(n, "*" * size, "|" * size) for n in range(1, 25)]
            for page in range(len(ui.pages(worst))):
                for is_dm in (True, False):
                    text = ui.list_text(campaign(name=name), worst, page, is_dm=is_dm, note=note)
                    self.assertLessEqual(len(text), 2000, (size, page))

    def test_who_sees_what(self) -> None:
        c = campaign(name="Frost*maiden")
        dm = ui.list_text(c, [], 0, is_dm=True)
        self.assertIn("📜 **House rules: Frost\\*maiden**", dm)
        self.assertIn("No house rules yet. Press **Add a house rule**", dm)
        self.assertIn("You decide: DMbot never makes up a house rule.", dm)
        player = ui.list_text(c, [], 0, is_dm=False)
        self.assertIn("No house rules yet.", player)
        self.assertNotIn("Add a house rule", player)
        self.assertNotIn("You decide", player)
        self.assertIn("Everyone in the server can read this list", player)
        self.assertIn("DMbot never makes up a house rule.", player)

    def test_the_note_comes_first_and_pages_say_so(self) -> None:
        rules = [rule(n) for n in range(1, 13)]
        text = ui.list_text(campaign(), rules, 1, is_dm=False, note="🗑 Removed: x")
        self.assertTrue(text.startswith("🗑 Removed: x\n📜"))
        self.assertTrue(text.endswith("_Page 2 of 2. Newest first._"))
        self.assertIn("11. Rule number 11", text)
        self.assertNotIn("10. Rule number 10", text)

    def test_plain_words(self) -> None:
        everything = " ".join(
            [ui.DM_INTRO, ui.READ_INTRO, ui.ADD_HINT, ui.NONE_YET, ui.NOT_READY, ui.STALE]
        ).lower()
        for hard in ("precedence", "hierarchy", "database", "supersede", "override"):
            self.assertNotIn(hard, everything)


class Opening(UITest):
    async def test_the_playing_campaign_is_shown_privately(self) -> None:
        self.store.seed("c1", 2)
        it = await self.command(PLAYER)
        text, view = self.opened(it)
        self.assertTrue(it.followup.send.await_args.kwargs["ephemeral"])
        self.assertIn("2. Rule number 1", text)
        self.assertTrue(text.index("1. Rule number 2") < text.index("2. Rule number 1"))
        self.assertIsNone(view)  # a player has nothing to press

    async def test_the_dm_of_the_only_campaign_without_a_session(self) -> None:
        self.playing = None
        it = await self.command()
        self.assertIn("House rules: Frostmaiden", self.opened(it)[0])

    async def test_a_dm_of_one_of_several_gets_theirs(self) -> None:
        self.playing = None
        self.campaigns["c2"] = campaign("c2", "Strahd", frozenset({PLAYER}))
        self.store.dms["c2"] = frozenset({PLAYER})
        self.assertIn("Frostmaiden", self.opened(await self.command(DM))[0])
        self.assertIn("Strahd", self.opened(await self.command(PLAYER))[0])

    async def test_someone_with_no_campaign_picks_which_to_read(self) -> None:
        self.playing = None
        self.campaigns["c2"] = campaign("c2", "Strahd", frozenset({PLAYER}))
        self.store.seed("c2", 1, text="Strahd's own rule")
        it = await self.command(user_id=99)
        text, view = self.opened(it)
        self.assertIn("Which campaign's house rules?", text)
        view.pick._values = ["c2"]
        pick = self.it(99)
        await view._picked(pick)
        shown, after = self.shown(pick)
        self.assertIn("House rules: Strahd", shown)
        self.assertIn("Strahd's own rule", shown)
        self.assertIsNone(after)  # read only

    async def test_the_command_answers_discord_before_it_reads_anything(self) -> None:
        order: list[str] = []
        it = self.it(kind=discord.InteractionType.application_command)
        it.response.defer = AsyncMock(side_effect=lambda **_: order.append("answered"))
        real = self.bot.campaigns.list_campaigns

        async def slow(guild_id: int) -> list[Campaign]:
            order.append("read")
            return await real(guild_id)  # type: ignore[no-any-return]

        self.bot.campaigns.list_campaigns = slow
        await ui.dmbot_house_rules.callback(it)  # type: ignore[call-arg]
        self.assertEqual(order[:2], ["answered", "read"])
        self.assertTrue(it.response.defer.await_args.kwargs["ephemeral"])  # private

    async def test_more_than_a_menu_holds_says_which_are_shown(self) -> None:
        self.playing = None
        for n in range(30):
            self.campaigns[f"x{n}"] = campaign(f"x{n}", f"Campaign {n}", frozenset({PLAYER}))
        text, view = self.opened(await self.command(user_id=99))
        self.assertIn(ui.MORE_CAMPAIGNS, text)
        self.assertEqual(len(view.pick.options), SELECT_OPTIONS_MAX)

    async def test_no_campaign_at_all(self) -> None:
        self.campaigns.clear()
        it = await self.command()
        self.assertIn("no campaign in this server yet", self.told(it))

    async def test_without_a_database_it_says_so(self) -> None:
        self.bot.house_rules = None
        it = await self.command()
        self.assertEqual(self.told(it), ui.NOT_READY)

    async def test_in_a_direct_message_it_says_to_use_a_server(self) -> None:
        it = self.it(kind=discord.InteractionType.application_command)
        it.guild = None
        await ui.dmbot_house_rules.callback(it)  # type: ignore[call-arg]
        self.assertEqual(it.response.sent[0][0], "Use this in a server.")

    async def test_one_campaigns_rules_never_show_in_anothers_list(self) -> None:
        self.campaigns["c2"] = campaign("c2", "Strahd", frozenset({DM}))
        self.store.dms["c2"] = frozenset({DM})
        self.store.seed("c2", 1, text="Only Strahd's table plays this")
        self.assertNotIn("Only Strahd", self.opened(await self.command())[0])


class Buttons(UITest):
    async def test_the_dm_of_a_new_campaign_can_only_add(self) -> None:
        text, view = self.opened(await self.command())
        self.assertIn("Press **Add a house rule**", text)
        self.assertEqual(self.labels(view), [ui.ADD_LABEL])

    async def test_up_to_four_rules_each_have_edit_and_remove(self) -> None:
        self.store.seed("c1", 4)
        _, view = self.opened(await self.command())
        self.assertEqual(
            self.labels(view),
            [ui.ADD_LABEL, *(f"{w} {n}" for n in (1, 2, 3, 4) for w in ("Edit", "Remove"))],
        )

    async def test_more_use_a_menu(self) -> None:
        self.store.seed("c1", 5)
        _, view = self.opened(await self.command())
        self.assertEqual(self.labels(view), [ui.ADD_LABEL])
        labels = [o.label for o in view.pick.options]
        self.assertEqual(len(labels), 5)
        self.assertEqual(labels[0], "1. Rule number 5")  # newest first

    async def test_every_label_fits_a_phone(self) -> None:
        self.store.seed("c1", 30, text="A rule with quite a lot of words in its first line")
        for page in (0, 1, 2):
            view = ui.ListMenu(campaign(), await self.store.list(GUILD, "c1"), page, is_dm=True)
            children: list[Any] = list(view.children)
            for child in children:
                if isinstance(child, discord.ui.Button):
                    self.assertLessEqual(len(child.label or ""), PHONE_LABEL_MAX, child.label)
                else:
                    self.assertLessEqual(len(child.options), SELECT_OPTIONS_MAX)
                    for option in child.options:
                        self.assertLessEqual(len(option.label), PHONE_LABEL_MAX, option.label)
        for label in (ui.YES_REMOVE_LABEL, ui.KEEP_LABEL, ui.BACK_LABEL, ui.PICK_PLACEHOLDER):
            self.assertLessEqual(len(label), PHONE_LABEL_MAX, label)

    async def test_a_long_list_turns_pages(self) -> None:
        self.store.seed("c1", 25)
        it = await self.command()
        text, view = self.opened(it)
        self.assertIn("Page 1 of 3", text)
        self.assertEqual(self.labels(view), [ui.ADD_LABEL, ui.OLDER_LABEL])
        turn = self.it()
        await self.press(view, ui.OLDER_LABEL).callback(turn)
        text, view = self.shown(turn)
        self.assertIn("Page 2 of 3", text)
        self.assertIn("11. Rule number 15", text)
        self.assertEqual(self.labels(view), [ui.ADD_LABEL, ui.NEWER_LABEL, ui.OLDER_LABEL])
        self.assertEqual(len(view.pick.options), 10)  # this page's rules
        back = self.it()
        await self.press(view, ui.NEWER_LABEL).callback(back)
        self.assertIn("Page 1 of 3", self.shown(back)[0])

    async def test_a_player_sees_page_buttons_but_no_changes(self) -> None:
        self.store.seed("c1", 25)
        _, view = self.opened(await self.command(PLAYER))
        self.assertEqual(self.labels(view), [ui.OLDER_LABEL])
        self.assertFalse(hasattr(view, "pick"))


class Adding(UITest):
    async def test_add_opens_a_form_with_two_boxes(self) -> None:
        _, view = self.opened(await self.command())
        it = self.it()
        await self.press(view, ui.ADD_LABEL).callback(it)
        form = it.response.modal
        self.assertEqual(form.title, "Add a house rule")
        self.assertEqual(
            [c.label for c in form.children],
            ["The rule", "Which rule does it change? (optional)"],
        )
        for box in form.children:
            self.assertLessEqual(len(box.label), 45)  # Discord's limit for a form's label
        self.assertTrue(form.children[0].required)
        self.assertFalse(form.children[1].required)
        self.assertEqual(form.children[0].max_length, house.RULE_MAX)

    async def test_a_new_rule_is_saved_and_the_list_redrawn(self) -> None:
        self.store.seed("c1", 1)
        form = ui.AddForm(self.campaigns["c1"])
        form.rule._value = "  Crits double the dice  "
        form.instead._value = "Crits roll once"
        it = await self.submit(form)
        self.assertEqual(self.store.calls[-1][:3], ("add", "c1", DM))
        text, view = self.shown(it)
        self.assertTrue(text.startswith("➕ Added house rule: Crits double the dice\n"))
        self.assertIn("1. Crits double the dice (instead of: Crits roll once)", text)
        self.assertIn("2. Rule number 1", text)
        self.assertIn(ui.ADD_LABEL, self.labels(view))
        self.assertEqual(it.response.sent, [])  # the same message, not a new one

    async def test_a_player_cannot_add_even_with_the_form_in_hand(self) -> None:
        form = ui.AddForm(self.campaigns["c1"])
        form.rule._value = "Everyone gets a pony"
        it = await self.submit(form, PLAYER)
        self.assertTrue(self.told(it).startswith(house.NOT_DM))
        self.assertEqual(await self.store.list(GUILD, "c1"), [])
        self.assertEqual(it.edited, [])

    async def test_a_bad_rule_says_what_to_fix_and_keeps_the_list(self) -> None:
        form = ui.AddForm(self.campaigns["c1"])
        form.rule._value = "   "
        it = await self.submit(form)
        self.assertEqual(self.told(it), house.EMPTY)
        self.assertEqual(it.edited, [])

    async def test_a_refusal_gives_the_words_back_to_copy(self) -> None:
        # The form is closed by then: a DM who wrote a long rule must not have to retype it.
        async def full(*_: Any, **__: Any) -> HouseRule:
            raise HouseRuleError(house.FULL)

        self.store.add = full  # type: ignore[method-assign]
        form = ui.AddForm(self.campaigns["c1"])
        form.rule._value = "  A *natural*  20 doubles   the dice  "
        it = await self.submit(form)
        told = self.told(it)
        self.assertTrue(told.startswith(house.FULL))
        self.assertIn("Your words, to copy: A \\*natural\\* 20 doubles the dice", told)

    async def test_a_deleted_campaign_is_said_not_crashed_on(self) -> None:
        form = ui.AddForm(self.campaigns["c1"])
        form.rule._value = "Crits double"
        self.campaigns.clear()
        it = await self.submit(form)
        self.assertEqual(self.told(it), ui.GONE)

    async def test_if_the_list_message_is_gone_a_new_one_is_sent(self) -> None:
        form = ui.AddForm(self.campaigns["c1"])
        form.rule._value = "Crits double"
        it = self.it(DM, discord.InteractionType.modal_submit)
        it.edit_original_response = AsyncMock(
            side_effect=discord.HTTPException(MagicMock(status=404, reason="x"), "gone")
        )
        await form.on_submit(it)
        self.assertIn("➕ Added house rule: Crits double", it.followup.send.await_args.args[0])


class Editing(UITest):
    async def test_the_form_starts_with_the_rule_as_it_is(self) -> None:
        self.store.seed("c1", 2)
        self.store.rules["c1"][1] = rule(2, "Crits double", "Crits roll once")
        _, view = self.opened(await self.command())
        it = self.it()
        await self.press(view, "Edit 1").callback(it)  # the newest is number 1
        form = it.response.modal
        self.assertEqual(form.title, "Edit a house rule")
        self.assertEqual(form.children[0].default, "Crits double")
        self.assertEqual(form.children[1].default, "Crits roll once")

    async def test_saving_changes_that_rule_only(self) -> None:
        self.store.seed("c1", 3)
        existing = (await self.store.list(GUILD, "c1"))[1]  # number 2
        form = ui.EditForm(self.campaigns["c1"], existing, 1)
        form.rule._value = "Crits max the dice"
        form.instead._value = ""
        it = await self.submit(form)
        text, _ = self.shown(it)
        self.assertTrue(text.startswith("✏️ Changed house rule: Crits max the dice\n"))
        self.assertIn("2. Crits max the dice\n", text + "\n")
        self.assertIn("1. Rule number 3", text)
        self.assertIn("3. Rule number 1", text)

    async def test_a_rule_removed_meanwhile_is_said(self) -> None:
        self.store.seed("c1", 1)
        (existing,) = await self.store.list(GUILD, "c1")
        self.store.rules["c1"].clear()
        form = ui.EditForm(self.campaigns["c1"], existing, 0)
        form.rule._value = "Too late"
        it = await self.submit(form)
        self.assertTrue(self.told(it).startswith(ui.SOMEONE_REMOVED))
        self.assertIn("Your words, to copy: Too late", self.told(it))

    async def test_a_player_cannot_edit(self) -> None:
        self.store.seed("c1", 1)
        (existing,) = await self.store.list(GUILD, "c1")
        form = ui.EditForm(self.campaigns["c1"], existing, 0)
        form.rule._value = "Mine now"
        it = await self.submit(form, PLAYER)
        self.assertTrue(self.told(it).startswith(house.NOT_DM))
        self.assertEqual((await self.store.list(GUILD, "c1"))[0].rule, "Rule number 1")


class Removing(UITest):
    async def test_remove_asks_first_and_changes_nothing(self) -> None:
        self.store.seed("c1", 2)
        _, view = self.opened(await self.command())
        it = self.it()
        await self.press(view, "Remove 2").callback(it)
        text, confirm = self.shown(it)
        self.assertIn("Remove this house rule?", text)
        self.assertIn("2. Rule number 1", text)
        self.assertIn("This can't be undone.", text)
        self.assertEqual(self.labels(confirm), [ui.YES_REMOVE_LABEL, ui.KEEP_LABEL])
        self.assertEqual(len(await self.store.list(GUILD, "c1")), 2)  # nothing yet
        self.assertEqual([c for c in self.store.calls if c[0] == "remove"], [])

    async def test_yes_removes_it_and_says_which(self) -> None:
        self.store.seed("c1", 2)
        _, view = self.opened(await self.command())
        ask = self.it()
        await self.press(view, "Remove 2").callback(ask)
        _, confirm = self.shown(ask)
        yes = self.it()
        await self.press(confirm, ui.YES_REMOVE_LABEL).callback(yes)
        text, after = self.shown(yes)
        self.assertTrue(text.startswith("🗑 Removed house rule: Rule number 1\n"))
        self.assertNotIn("2. Rule number 1", text)
        self.assertEqual([r.rule for r in await self.store.list(GUILD, "c1")], ["Rule number 2"])
        self.assertEqual(self.labels(after), [ui.ADD_LABEL, "Edit 1", "Remove 1"])

    async def test_keep_it_leaves_everything(self) -> None:
        self.store.seed("c1", 1)
        _, view = self.opened(await self.command())
        ask = self.it()
        await self.press(view, "Remove 1").callback(ask)
        keep = self.it()
        await self.press(self.shown(ask)[1], ui.KEEP_LABEL).callback(keep)
        self.assertIn("1. Rule number 1", self.shown(keep)[0])
        self.assertEqual(len(await self.store.list(GUILD, "c1")), 1)

    async def test_a_rule_changed_since_it_was_shown_is_not_removed(self) -> None:
        self.store.seed("c1", 1)
        _, view = self.opened(await self.command())
        ask = self.it()
        await self.press(view, "Remove 1").callback(ask)  # shows the words as they are now
        (existing,) = await self.store.list(GUILD, "c1")
        await self.store.edit(GUILD, "c1", DM, existing.id, "Something else entirely")
        yes = self.it()
        await self.press(self.shown(ask)[1], ui.YES_REMOVE_LABEL).callback(yes)
        text = self.shown(yes)[0]
        self.assertTrue(text.startswith(house.CHANGED))
        self.assertIn("1. Something else entirely", text)  # it stays, and the list is fresh
        self.assertEqual(len(await self.store.list(GUILD, "c1")), 1)

    async def test_removing_twice_is_said_not_crashed_on(self) -> None:
        self.store.seed("c1", 1)
        _, view = self.opened(await self.command())
        ask = self.it()
        await self.press(view, "Remove 1").callback(ask)
        confirm = self.shown(ask)[1]
        await self.press(confirm, ui.YES_REMOVE_LABEL).callback(self.it())
        again = self.it()
        await self.press(confirm, ui.YES_REMOVE_LABEL).callback(again)
        self.assertTrue(self.shown(again)[0].startswith(ui.ALREADY_GONE))

    async def test_a_player_cannot_remove(self) -> None:
        self.store.seed("c1", 1)
        (existing,) = await self.store.list(GUILD, "c1")
        confirm = ui.ConfirmRemove(self.campaigns["c1"], existing, 0)
        it = self.it(PLAYER)
        await self.press(confirm, ui.YES_REMOVE_LABEL).callback(it)
        self.assertTrue(self.shown(it)[0].startswith(house.NOT_DM))
        self.assertEqual(len(await self.store.list(GUILD, "c1")), 1)


class PickingFromTheMenu(UITest):
    async def picked(self, number: int) -> tuple[Any, Any]:
        self.store.seed("c1", 6)
        _, view = self.opened(await self.command())
        wanted = (await self.store.list(GUILD, "c1"))[number - 1]
        view.pick._values = [str(wanted.id)]
        it = self.it()
        await view._picked(it)
        return it, view

    async def test_a_picked_rule_has_edit_remove_and_back(self) -> None:
        it, _ = await self.picked(2)
        text, menu = self.shown(it)
        self.assertIn("📜 **House rule 2**", text)
        self.assertIn("2. Rule number 5", text)
        self.assertEqual(self.labels(menu), [ui.EDIT_LABEL, ui.REMOVE_LABEL, ui.BACK_LABEL])

    async def test_edit_and_remove_work_from_there(self) -> None:
        it, _ = await self.picked(2)
        menu = self.shown(it)[1]
        edit = self.it()
        await self.press(menu, ui.EDIT_LABEL).callback(edit)
        self.assertEqual(edit.response.modal.children[0].default, "Rule number 5")
        ask = self.it()
        await self.press(menu, ui.REMOVE_LABEL).callback(ask)
        self.assertIn("Remove this house rule?", self.shown(ask)[0])

    async def test_back_returns_to_the_list(self) -> None:
        it, _ = await self.picked(1)
        back = self.it()
        await self.press(self.shown(it)[1], ui.BACK_LABEL).callback(back)
        self.assertIn("House rules: Frostmaiden", self.shown(back)[0])

    async def test_a_choice_that_is_not_on_the_list_draws_it_again(self) -> None:
        self.store.seed("c1", 6)
        _, view = self.opened(await self.command())
        view.pick._values = ["9999"]
        it = self.it()
        await view._picked(it)
        self.assertTrue(self.shown(it)[0].startswith(ui.STALE))

    async def test_a_rule_removed_meanwhile_is_said_when_changed(self) -> None:
        it, _ = await self.picked(1)
        menu = self.shown(it)[1]
        self.store.rules["c1"].pop()  # the newest one is removed while the menu is open
        ask = self.it()
        await self.press(menu, ui.REMOVE_LABEL).callback(ask)
        yes = self.it()
        await self.press(self.shown(ask)[1], ui.YES_REMOVE_LABEL).callback(yes)
        self.assertTrue(self.shown(yes)[0].startswith(ui.ALREADY_GONE))


class Registered(unittest.TestCase):
    def test_the_command_is_registered_and_described_in_plain_words(self) -> None:
        self.assertEqual(ui.dmbot_house_rules.name, "houserules")
        self.assertLessEqual(len(ui.dmbot_house_rules.description), 100)
        from dmbot.ui.logic import HELP_TEXT

        self.assertIn("/dmbot houserules", HELP_TEXT)


if __name__ == "__main__":
    unittest.main()
