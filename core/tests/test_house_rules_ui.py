"""`/dmbot houserules` (#865): the list, Add, Edit and Remove (with its confirm), for the
DM and for everyone else. The store is a stand-in with the real one's rules for who may
change what, for numbers that are never used twice, and for changes made meanwhile by
another DM; the real one is tested against the database in test_house_rules_store."""

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
from dmbot.ui.logic import DESCRIPTION_MAX, PHONE_LABEL_MAX, SELECT_OPTIONS_MAX
from tests.test_memory_names import FakeResponse

GUILD, DM, PLAYER = 1, 7, 8
NOW = 1_700_000_000


def campaign(
    cid: str = "c1", name: str = "Frostmaiden", dms: frozenset[int] = frozenset({DM})
) -> Campaign:
    return Campaign(
        cid, GUILD, name, NOW - 86400, NOW - 3600, "2024", "2014", True, dms, None, 50, "peek"
    )


def rule(n: int, text: str = "", instead: str | None = None, version: int = 1) -> HouseRule:
    return HouseRule(
        n, "c1", text or f"Rule number {n}", instead, None, None, DM, NOW + n, NOW + n, version
    )


class FakeStore:
    """Like `HouseRuleStore`, in memory: newest (highest number) first, only DMs change
    anything, a number is never used twice, and a change made from an old view is refused."""

    def __init__(self, dms: dict[str, frozenset[int]]) -> None:
        self.dms = dms
        self.rules: dict[str, list[HouseRule]] = {}
        self.made: dict[str, int] = {}
        self.calls: list[tuple[Any, ...]] = []

    def _next(self, cid: str) -> int:
        self.made[cid] = self.made.get(cid, 0) + 1
        return self.made[cid]

    def seed(self, cid: str, count: int, **kw: Any) -> None:
        for _ in range(count):
            n = self._next(cid)
            self.rules.setdefault(cid, []).append(rule(n, **kw) if kw else rule(n))

    def _dm(self, cid: str, user_id: int) -> None:
        if user_id not in self.dms.get(cid, frozenset()):
            raise HouseRuleError(house.NOT_DM)

    def _find(self, cid: str, number: int, unchanged_since: int | None) -> int:
        for i, existing in enumerate(self.rules.get(cid, [])):
            if existing.number == number:
                if unchanged_since is not None and existing.version != unchanged_since:
                    raise HouseRuleError(house.CHANGED)
                return i
        raise HouseRuleError(house.GONE)

    async def list(self, guild_id: int, cid: str) -> list[HouseRule]:
        return sorted(self.rules.get(cid, []), key=lambda r: -r.number)

    async def add(
        self, guild_id: int, cid: str, user_id: int, text: str, instead: str | None = None
    ) -> HouseRule:
        self.calls.append(("add", cid, user_id, text, instead))
        self._dm(cid, user_id)
        saved = HouseRule(
            self._next(cid),
            cid,
            house.clean_rule(text),
            house.clean_optional(instead, house.INSTEAD_BOX),
            None,
            None,
            user_id,
            NOW,
            NOW,
        )
        self.rules.setdefault(cid, []).append(saved)
        return saved

    async def edit(
        self,
        guild_id: int,
        cid: str,
        user_id: int,
        number: int,
        text: str,
        instead: str = "",
        *,
        unchanged_since: int | None = None,
    ) -> HouseRule:
        self.calls.append(("edit", cid, user_id, number, text, instead))
        self._dm(cid, user_id)
        i = self._find(cid, number, unchanged_since)
        old = self.rules[cid][i]
        changed = HouseRule(
            number,
            cid,
            house.clean_rule(text),
            house.clean_optional(instead, house.INSTEAD_BOX),
            old.scenario,
            old.session_id,
            old.created_by,
            old.created_at,
            NOW + 99,
            old.version + 1,
        )
        self.rules[cid][i] = changed
        return changed

    async def remove(
        self,
        guild_id: int,
        cid: str,
        user_id: int,
        number: int,
        *,
        unchanged_since: int | None = None,
    ) -> HouseRule:
        self.calls.append(("remove", cid, user_id, number))
        self._dm(cid, user_id)
        return self.rules[cid].pop(self._find(cid, number, unchanged_since))


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

    async def mine(self) -> list[HouseRule]:
        return await self.store.list(GUILD, "c1")


class Text(unittest.TestCase):
    def test_a_line_has_the_rules_own_number_and_says_what_it_replaces(self) -> None:
        self.assertEqual(ui.entry_text(rule(12, "Crits double")), "12. Crits double")
        self.assertEqual(
            ui.entry_text(rule(3, "Crits double", "Crits roll once")),
            "3. Crits double (instead of: Crits roll once)",
        )

    def test_the_dms_words_never_format_the_message(self) -> None:
        text = ui.entry_text(rule(1, "**bold** @everyone ||spoiler|| ```", "_x_"))
        self.assertNotIn("**bold**", text)
        self.assertIn("\\*\\*bold\\*\\*", text)
        self.assertIn("\\|\\|spoiler\\|\\|", text)

    def test_a_very_long_rule_is_cut_whole(self) -> None:
        text = ui.entry_text(rule(1, "*" * 500, "*" * 500))
        self.assertLessEqual(len(text), ui.ENTRY_MAX)
        self.assertTrue(text.endswith("…"))
        self.assertFalse(text.endswith("\\…"))  # never half an escape

    def test_pages_hold_ten_at_most_and_what_fits(self) -> None:
        self.assertEqual(ui.pages([]), [[]])
        short = [rule(n) for n in range(25, 0, -1)]
        self.assertEqual([len(p) for p in ui.pages(short)], [10, 10, 5])
        long = [rule(n, "x" * 500, "y" * 500) for n in range(5, 0, -1)]
        for page in ui.pages(long):
            used = sum(len(ui.entry_text(long[i])) + 1 for i in page)
            self.assertLessEqual(used, ui.TEXT_MAX + ui.ENTRY_MAX)
        self.assertEqual([i for p in ui.pages(long) for i in p], [0, 1, 2, 3, 4])  # none lost

    def test_the_message_always_fits_discord(self) -> None:
        # Rules of every size, so that some page is as full as a page can be, with the
        # longest name and note around it (each of them escaped, so they double).
        name, note = "*" * 80, "🗑 Removed house rule 12: " + "*" * 300
        for size in (1, 40, 150, 330, 500):
            worst = [rule(n, "*" * size, "|" * size) for n in range(24, 0, -1)]
            for page in range(len(ui.pages(worst))):
                for is_dm in (True, False):
                    text = ui.list_text(campaign(name=name), worst, page, is_dm=is_dm, note=note)
                    self.assertLessEqual(len(text), 2000, (size, page))

    def test_who_sees_what(self) -> None:
        c = campaign(name="Frost*maiden")
        dm = ui.list_text(c, [], 0, is_dm=True)
        self.assertIn("📜 **House rules: Frost\\*maiden**", dm)
        self.assertIn("No house rules yet. Press **Add a house rule** to write the first one.", dm)
        self.assertIn("DMbot never makes up a house rule: you decide.", dm)
        self.assertIn("Everyone in the server can read them", dm)
        player = ui.list_text(c, [], 0, is_dm=False)
        self.assertIn("No house rules yet. Your DM can add them.", player)
        self.assertNotIn("Add a house rule", player)
        self.assertNotIn("you decide", player)
        self.assertIn("Only the DM can change them. DMbot never makes up a house rule.", player)

    def test_the_note_comes_first_and_pages_say_so(self) -> None:
        rules = [rule(n) for n in range(12, 0, -1)]
        text = ui.list_text(campaign(), rules, 1, is_dm=False, note="🗑 Removed house rule 5.")
        self.assertTrue(text.startswith("🗑 Removed house rule 5.\n📜"))
        self.assertTrue(text.endswith("_Page 2 of 2. Newest first._"))
        self.assertIn("2. Rule number 2", text)
        self.assertNotIn("3. Rule number 3", text)

    def test_numbers_show_gaps_where_rules_were_removed(self) -> None:
        # A removed rule's number is gone for good, so the list has a hole, not a renumbering.
        text = ui.list_text(campaign(), [rule(9), rule(4), rule(1)], 0, is_dm=False)
        self.assertEqual([ln[:2] for ln in text.splitlines()[-3:]], ["9.", "4.", "1."])

    def test_plain_words(self) -> None:
        everything = " ".join(
            [ui.DM_INTRO, ui.READ_INTRO, ui.NONE_YET_DM, ui.NONE_YET_PLAYER, ui.STALE]
        ).lower()
        for hard in ("precedence", "hierarchy", "database", "supersede", "override"):
            self.assertNotIn(hard, everything)

    def test_a_removed_rule_is_given_whole_to_copy_back(self) -> None:
        text = ui.removed_words(rule(12, "A *natural* 20 ```crits```", "Crits roll once"))
        self.assertIn("🗑 Removed house rule 12:", text)
        self.assertIn("A *natural* 20", text)  # as typed: nothing escaped
        self.assertNotIn("```crits```", text)  # no way to end the block early
        self.assertIn("Instead of:\n```\nCrits roll once\n```", text)
        self.assertTrue(text.endswith(ui.CHANGED_MIND))
        self.assertIn("Press **Add a house rule**", ui.CHANGED_MIND)


class Opening(UITest):
    async def test_the_playing_campaign_is_shown_privately(self) -> None:
        self.store.seed("c1", 2)
        it = await self.command(PLAYER)
        text, view = self.opened(it)
        self.assertTrue(it.followup.send.await_args.kwargs["ephemeral"])
        self.assertIn("1. Rule number 1", text)
        self.assertTrue(text.index("2. Rule number 2") < text.index("1. Rule number 1"))
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

    async def test_up_to_four_rules_each_have_edit_and_remove_with_their_own_number(self) -> None:
        self.store.seed("c1", 4)
        _, view = self.opened(await self.command())
        self.assertEqual(
            self.labels(view),
            [ui.ADD_LABEL, *(f"{w} {n}" for n in (4, 3, 2, 1) for w in ("Edit", "Remove"))],
        )

    async def test_a_number_is_the_same_whatever_is_removed_or_added(self) -> None:
        self.store.seed("c1", 3)
        await self.store.remove(GUILD, "c1", DM, 2)
        await self.store.add(GUILD, "c1", DM, "A new one")
        _, view = self.opened(await self.command())
        self.assertEqual(
            self.labels(view),
            [ui.ADD_LABEL, *(f"{w} {n}" for n in (4, 3, 1) for w in ("Edit", "Remove"))],
        )  # the new one is 4, not 2: a removed rule's number is not used again

    async def test_more_use_a_menu_of_this_pages_rules(self) -> None:
        self.store.seed("c1", 5)
        _, view = self.opened(await self.command())
        self.assertEqual(self.labels(view), [ui.ADD_LABEL])
        labels = [o.label for o in view.pick.options]
        self.assertEqual(len(labels), 5)
        self.assertEqual(labels[0], "5. Rule number 5")  # newest first, by its own number
        self.assertEqual([o.value for o in view.pick.options], ["5", "4", "3", "2", "1"])

    async def test_menu_choices_say_more_in_a_description(self) -> None:
        self.store.seed("c1", 5, text="A rule with quite a lot of words in its first line, " * 4)
        _, view = self.opened(await self.command())
        for option in view.pick.options:
            self.assertTrue(option.description.startswith("A rule with quite"))
            self.assertLessEqual(len(option.description), DESCRIPTION_MAX)
            self.assertLessEqual(len(option.label), PHONE_LABEL_MAX)

    async def test_buttons_are_only_for_the_rules_on_the_page_shown(self) -> None:
        # Four long rules fill a page each: the first page shows one, so only its buttons.
        self.store.seed("c1", 4, text="x" * 500, instead="y" * 500)
        text, view = self.opened(await self.command())
        self.assertIn("Page 1 of 4", text)
        self.assertEqual(self.labels(view), [ui.ADD_LABEL, ui.OLDER_LABEL, "Edit 4", "Remove 4"])
        turn = self.it()
        await self.press(view, ui.OLDER_LABEL).callback(turn)
        _, second = self.shown(turn)
        self.assertEqual(
            self.labels(second),
            [ui.ADD_LABEL, ui.NEWER_LABEL, ui.OLDER_LABEL, "Edit 3", "Remove 3"],
        )

    async def test_every_label_fits_a_phone(self) -> None:
        self.store.seed("c1", 30, text="A rule with quite a lot of words in its first line")
        for page in (0, 1, 2):
            view = ui.ListMenu(campaign(), await self.mine(), page, is_dm=True)
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
        text, view = self.opened(await self.command())
        self.assertIn("Page 1 of 3", text)
        self.assertEqual(self.labels(view), [ui.ADD_LABEL, ui.OLDER_LABEL])
        turn = self.it()
        await self.press(view, ui.OLDER_LABEL).callback(turn)
        text, view = self.shown(turn)
        self.assertIn("Page 2 of 3", text)
        self.assertIn("15. Rule number 15", text)
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
        self.assertEqual([c.label for c in form.children], ["The rule", "Instead of (optional)"])
        for box in form.children:
            self.assertLessEqual(len(box.label), 45)  # Discord's limit for a form's label
        self.assertEqual(
            form.children[0].placeholder, "For example: Drinking a potion is a bonus action"
        )
        self.assertEqual(
            form.children[1].placeholder, "The book rule it replaces. Leave empty if it's new."
        )
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
        self.assertTrue(text.startswith("➕ Added house rule 2.\n"))
        self.assertIn("2. Crits double the dice (instead of: Crits roll once)", text)
        self.assertIn("1. Rule number 1", text)  # the old one keeps its number
        self.assertIn(ui.ADD_LABEL, self.labels(view))
        self.assertEqual(it.response.sent, [])  # the same message, not a new one

    async def test_a_player_cannot_add_even_with_the_form_in_hand(self) -> None:
        form = ui.AddForm(self.campaigns["c1"])
        form.rule._value = "Everyone gets a pony"
        it = await self.submit(form, PLAYER)
        self.assertTrue(self.told(it).startswith(house.NOT_DM))
        self.assertEqual(await self.mine(), [])
        self.assertEqual(it.edited, [])

    async def test_an_empty_rule_names_the_button_to_press(self) -> None:
        form = ui.AddForm(self.campaigns["c1"])
        form.rule._value = "   "
        it = await self.submit(form)
        self.assertEqual(
            self.told(it),
            "The rule box was empty, so nothing was saved. Press **Add a house rule** and "
            "type the rule.",
        )
        self.assertEqual(it.edited, [])  # the list stays; nothing typed to give back

    async def test_a_refusal_gives_both_boxes_back_whole_and_exactly_as_typed(self) -> None:
        # The form is closed by then: a DM who wrote a long rule must not have to retype it.
        async def full(*_: Any, **__: Any) -> HouseRule:
            raise HouseRuleError(house.FULL)

        self.store.add = full  # type: ignore[method-assign]
        form = ui.AddForm(self.campaigns["c1"])
        form.rule._value = "A *natural*  20 doubles " + "the dice " * 50
        form.instead._value = "Only attack rolls get extra"
        told = self.told(await self.submit(form))
        self.assertTrue(told.startswith(house.FULL))
        self.assertIn("Your words, to copy:\n```\nA *natural*  20 doubles the dice", told)
        self.assertIn("the dice\n```", told)  # all of it: nothing cut
        self.assertIn("Instead of:\n```\nOnly attack rolls get extra\n```", told)
        self.assertLessEqual(len(told), 2000)

    async def test_the_longest_boxes_still_fit_in_the_refusal(self) -> None:
        async def full(*_: Any, **__: Any) -> HouseRule:
            raise HouseRuleError(house.FULL)

        self.store.add = full  # type: ignore[method-assign]
        form = ui.AddForm(self.campaigns["c1"])
        form.rule._value = "r" * house.RULE_MAX
        form.instead._value = "i" * house.RULE_MAX
        self.assertLessEqual(len(self.told(await self.submit(form))), 2000)

    async def test_a_deleted_campaign_is_said_not_crashed_on(self) -> None:
        form = ui.AddForm(self.campaigns["c1"])
        form.rule._value = "Crits double"
        self.campaigns.clear()
        it = await self.submit(form)
        self.assertEqual(self.told(it), ui.CAMPAIGN_GONE)

    async def test_if_the_list_message_is_gone_a_new_one_is_sent(self) -> None:
        form = ui.AddForm(self.campaigns["c1"])
        form.rule._value = "Crits double"
        it = self.it(DM, discord.InteractionType.modal_submit)
        it.edit_original_response = AsyncMock(
            side_effect=discord.HTTPException(MagicMock(status=404, reason="x"), "gone")
        )
        await form.on_submit(it)
        self.assertIn("➕ Added house rule 1.", it.followup.send.await_args.args[0])


class Editing(UITest):
    async def test_the_form_starts_with_the_rule_as_it_is(self) -> None:
        self.store.seed("c1", 2)
        self.store.rules["c1"][1] = rule(2, "Crits double", "Crits roll once")
        _, view = self.opened(await self.command())
        it = self.it()
        await self.press(view, "Edit 2").callback(it)  # the newest has the highest number
        form = it.response.modal
        self.assertEqual(form.title, "Edit a house rule")
        self.assertEqual(form.children[0].default, "Crits double")
        self.assertEqual(form.children[1].default, "Crits roll once")

    async def test_saving_changes_that_rule_only(self) -> None:
        self.store.seed("c1", 3)
        existing = (await self.mine())[1]  # number 2
        form = ui.EditForm(self.campaigns["c1"], existing, 0)
        form.rule._value = "Crits max the dice"
        form.instead._value = ""
        it = await self.submit(form)
        text, _ = self.shown(it)
        self.assertTrue(text.startswith("✏️ Changed house rule 2.\n"))
        self.assertIn("\n2. Crits max the dice\n", text + "\n")
        self.assertIn("3. Rule number 3", text)
        self.assertIn("1. Rule number 1", text)

    async def test_another_dm_changing_it_meanwhile_is_not_overwritten(self) -> None:
        self.store.seed("c1", 1)
        (existing,) = await self.mine()
        form = ui.EditForm(self.campaigns["c1"], existing, 0)  # opened with version 1
        await self.store.edit(GUILD, "c1", DM, 1, "The other DM's words")  # now version 2
        form.rule._value = "My words, written from the old view"
        it = await self.submit(form)
        told = self.told(it)
        self.assertTrue(told.startswith("Another DM changed that house rule while you were"))
        self.assertIn("**Edit 1**", told)  # what to press next
        self.assertIn("My words, written from the old view", told)  # and the words back
        self.assertEqual((await self.mine())[0].rule, "The other DM's words")
        self.assertEqual(it.edited, [])

    async def test_a_rule_removed_meanwhile_says_what_to_do(self) -> None:
        self.store.seed("c1", 1)
        (existing,) = await self.mine()
        self.store.rules["c1"].clear()
        form = ui.EditForm(self.campaigns["c1"], existing, 0)
        form.rule._value = "Too late"
        told = self.told(await self.submit(form))
        self.assertTrue(told.startswith("Another DM removed that house rule while you were"))
        self.assertIn("press **Add a house rule** and paste your words", told)
        self.assertIn("Your words, to copy:\n```\nToo late\n```", told)

    async def test_an_empty_edit_names_the_button(self) -> None:
        self.store.seed("c1", 1)
        (existing,) = await self.mine()
        form = ui.EditForm(self.campaigns["c1"], existing, 0)
        form.rule._value = " "
        told = self.told(await self.submit(form))
        self.assertTrue(told.endswith("Press **Edit 1** again and type the rule."))

    async def test_a_player_cannot_edit(self) -> None:
        self.store.seed("c1", 1)
        (existing,) = await self.mine()
        form = ui.EditForm(self.campaigns["c1"], existing, 0)
        form.rule._value = "Mine now"
        it = await self.submit(form, PLAYER)
        self.assertTrue(self.told(it).startswith(house.NOT_DM))
        self.assertEqual((await self.mine())[0].rule, "Rule number 1")

    async def test_the_list_comes_back_on_the_same_page(self) -> None:
        self.store.seed("c1", 25)
        _, view = self.opened(await self.command())
        turn = self.it()
        await self.press(view, ui.OLDER_LABEL).callback(turn)  # page 2: rules 15 to 6
        menu = self.shown(turn)[1]
        menu.pick._values = ["12"]
        picked = self.it()
        await menu._picked(picked)
        edit = self.it()
        await self.press(self.shown(picked)[1], ui.EDIT_LABEL).callback(edit)
        form = edit.response.modal
        form.rule._value = "Changed on page two"
        text = self.shown(await self.submit(form))[0]
        self.assertIn("Page 2 of 3", text)
        self.assertIn("12. Changed on page two", text)


class Removing(UITest):
    async def test_remove_asks_first_and_changes_nothing(self) -> None:
        self.store.seed("c1", 2)
        _, view = self.opened(await self.command())
        it = self.it()
        await self.press(view, "Remove 1").callback(it)
        text, confirm = self.shown(it)
        self.assertIn("Remove this house rule?", text)
        self.assertIn("1. Rule number 1", text)
        self.assertIn("This can't be undone.", text)
        self.assertEqual(self.labels(confirm), [ui.YES_REMOVE_LABEL, ui.KEEP_LABEL])
        self.assertEqual(len(await self.mine()), 2)  # nothing yet
        self.assertEqual([c for c in self.store.calls if c[0] == "remove"], [])

    async def test_yes_removes_it_and_gives_its_words_back(self) -> None:
        self.store.seed("c1", 2)
        self.store.rules["c1"][0] = rule(1, "A *rule* to be removed", "The book rule")
        _, view = self.opened(await self.command())
        ask = self.it()
        await self.press(view, "Remove 1").callback(ask)
        yes = self.it()
        await self.press(self.shown(ask)[1], ui.YES_REMOVE_LABEL).callback(yes)
        text, after = self.shown(yes)
        self.assertTrue(text.startswith("🗑 Removed house rule 1.\n"))
        self.assertNotIn("1. A", text)
        self.assertEqual([r.rule for r in await self.mine()], ["Rule number 2"])
        self.assertEqual(self.labels(after), [ui.ADD_LABEL, "Edit 2", "Remove 2"])
        told = self.told(yes)  # the words in full, as typed, in a private follow-up
        self.assertIn("A *rule* to be removed", told)
        self.assertIn("Instead of:\n```\nThe book rule\n```", told)
        self.assertTrue(told.endswith(ui.CHANGED_MIND))

    async def test_keep_it_leaves_everything(self) -> None:
        self.store.seed("c1", 1)
        _, view = self.opened(await self.command())
        ask = self.it()
        await self.press(view, "Remove 1").callback(ask)
        keep = self.it()
        await self.press(self.shown(ask)[1], ui.KEEP_LABEL).callback(keep)
        self.assertIn("1. Rule number 1", self.shown(keep)[0])
        self.assertEqual(len(await self.mine()), 1)

    async def test_a_rule_changed_since_it_was_shown_is_not_removed(self) -> None:
        self.store.seed("c1", 1)
        _, view = self.opened(await self.command())
        ask = self.it()
        await self.press(view, "Remove 1").callback(ask)  # shows the words as they are now
        await self.store.edit(GUILD, "c1", DM, 1, "Something else entirely")
        yes = self.it()
        await self.press(self.shown(ask)[1], ui.YES_REMOVE_LABEL).callback(yes)
        text = self.shown(yes)[0]
        self.assertTrue(text.startswith(ui.CHANGED_REMOVE))
        self.assertIn("1. Something else entirely", text)  # it stays, and the list is fresh
        self.assertEqual(len(await self.mine()), 1)
        yes.followup.send.assert_not_awaited()  # nothing was removed, so no words to give back

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
        (existing,) = await self.mine()
        confirm = ui.ConfirmRemove(self.campaigns["c1"], existing, 0)
        it = self.it(PLAYER)
        await self.press(confirm, ui.YES_REMOVE_LABEL).callback(it)
        self.assertTrue(self.shown(it)[0].startswith(house.NOT_DM))
        self.assertEqual(len(await self.mine()), 1)

    async def test_the_list_comes_back_on_the_same_page_after_remove_and_keep(self) -> None:
        self.store.seed("c1", 25)
        _, view = self.opened(await self.command())
        turn = self.it()
        await self.press(view, ui.OLDER_LABEL).callback(turn)
        menu = self.shown(turn)[1]
        menu.pick._values = ["12"]
        picked = self.it()
        await menu._picked(picked)
        ask = self.it()
        await self.press(self.shown(picked)[1], ui.REMOVE_LABEL).callback(ask)
        keep = self.it()
        await self.press(self.shown(ask)[1], ui.KEEP_LABEL).callback(keep)
        self.assertIn("Page 2 of 3", self.shown(keep)[0])
        yes = self.it()
        await self.press(self.shown(ask)[1], ui.YES_REMOVE_LABEL).callback(yes)
        self.assertIn("Page 2 of 3", self.shown(yes)[0])


class PickingFromTheMenu(UITest):
    async def picked(self, number: int) -> tuple[Any, Any]:
        self.store.seed("c1", 6)
        _, view = self.opened(await self.command())
        view.pick._values = [str(number)]
        it = self.it()
        await view._picked(it)
        return it, view

    async def test_a_picked_rule_has_edit_remove_and_back_and_its_number_once(self) -> None:
        it, _ = await self.picked(5)
        text, menu = self.shown(it)
        self.assertEqual(text, "📜 **House rule 5**\nRule number 5")  # not "5. Rule number 5"
        self.assertEqual(self.labels(menu), [ui.EDIT_LABEL, ui.REMOVE_LABEL, ui.BACK_LABEL])

    async def test_edit_and_remove_work_from_there(self) -> None:
        it, _ = await self.picked(5)
        menu = self.shown(it)[1]
        edit = self.it()
        await self.press(menu, ui.EDIT_LABEL).callback(edit)
        self.assertEqual(edit.response.modal.children[0].default, "Rule number 5")
        ask = self.it()
        await self.press(menu, ui.REMOVE_LABEL).callback(ask)
        self.assertIn("Remove this house rule?", self.shown(ask)[0])
        self.assertIn("5. Rule number 5", self.shown(ask)[0])

    async def test_back_returns_to_the_list(self) -> None:
        it, _ = await self.picked(6)
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
        it, _ = await self.picked(6)
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
        self.assertEqual(
            ui.dmbot_house_rules.description,
            "See this campaign's house rules (its DM can add, edit or remove them)",
        )
        self.assertLessEqual(len(ui.dmbot_house_rules.description), 100)
        from dmbot.ui.logic import HELP_TEXT

        self.assertIn("/dmbot houserules", HELP_TEXT)


if __name__ == "__main__":
    unittest.main()
