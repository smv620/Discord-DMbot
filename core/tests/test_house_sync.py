"""The house-rules file brought together with DMbot's copy (#969, part 2): the offer on the
DM screen, Accept all / Review / Ignore, who may press, the linked file and the upload.
No Discord and no database: stand-ins for both."""

from __future__ import annotations

import ast
import asyncio
import inspect
import unittest
from types import SimpleNamespace
from typing import Any, ClassVar
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from dmbot import bot as bot_module
from dmbot import fetch
from dmbot.campaigns import Campaign
from dmbot.dm_screen import house_sync as hs
from dmbot.dm_screen import settings as screen_settings
from dmbot.rules import house, house_file, house_file_sync
from dmbot.rules.house import HouseRule, HouseRuleError
from dmbot.rules.house_file_link import FileLink
from dmbot.ui import house_file as file_ui
from tests.test_house_file_sync import FakeStore, rule
from tests.test_memory_names import FakeResponse

GUILD, OTHER_GUILD, SCREEN = 1, 2, 30
DM, CO_DM, PLAYER = 7, 70, 8
C1 = "a1" * 16
DOC = "https://docs.google.com/document/d/" + "a" * 30 + "/edit"
FILE = "House rules: Frostmaiden\n1. Keep\n2. New words\n9. Fresh\n"  # vs Keep / Old / Gone


def campaign(dms: frozenset[int] = frozenset({DM})) -> Campaign:
    return Campaign(C1, GUILD, "Frostmaiden", 1, 1, "2024", "2014", True, dms, None, 50, "peek")


class FileLinkRepr(unittest.TestCase):
    def test_the_private_link_is_not_in_its_repr_or_str(self) -> None:
        link = FileLink(DOC, None, DM, 1)
        for text in (repr(link), str(link), f"{link!r}", f"{[link]}"):
            self.assertNotIn(DOC, text)
            self.assertNotIn("a" * 30, text)
            self.assertNotIn("docs.google.com", text)


class FakeLinks:
    def __init__(self, link: str | None = None) -> None:
        self.link = link
        self.ignored: str | None = None
        self.calls: list[tuple[Any, ...]] = []

    async def get(self, guild_id: int, cid: str) -> FileLink | None:
        if self.link is None:
            return None
        return FileLink(self.link, self.ignored, DM, 1)

    async def set_link(self, guild_id: int, cid: str, user_id: int, link: str) -> FileLink:
        self.calls.append(("set", user_id, link))
        if user_id != DM:
            raise HouseRuleError(house.NOT_DM)
        if "docs.google.com" not in link:
            raise HouseRuleError("That doesn't look like a link.")
        self.link, self.ignored = link, None
        return FileLink(link, None, user_id, 1)

    async def clear(self, guild_id: int, cid: str, user_id: int) -> bool:
        self.calls.append(("clear", user_id))
        had, self.link = self.link is not None, None
        return had

    async def ignore(self, guild_id: int, cid: str, user_id: int, fingerprint: str) -> None:
        self.calls.append(("ignore", user_id, fingerprint))
        self.ignored = fingerprint


class SyncTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        file_ui._last.clear()
        file_ui._busy.clear()
        self.campaigns = {C1: campaign()}
        self.store = FakeStore([rule(1, "Keep"), rule(2, "Old"), rule(3, "Gone")])
        self.links = FakeLinks()
        self.bot = SimpleNamespace(
            campaigns=SimpleNamespace(
                get=AsyncMock(side_effect=lambda g, c: self.campaigns.get(c))
            ),
            house_rules=self.store,
            house_file_links=self.links,
            house_syncs=hs.Pendings(),
        )  # fmt: skip
        self.sent: list[tuple[str, discord.ui.View]] = []
        self.send_works = True

    async def send(self, text: str, view: discord.ui.View) -> bool:
        self.sent.append((text, view))
        return self.send_works

    def interaction(self, user_id: int = DM, guild_id: int = GUILD) -> Any:
        user = MagicMock(spec=discord.Member)
        user.id = user_id
        return SimpleNamespace(
            client=self.bot, guild_id=guild_id, guild=SimpleNamespace(id=guild_id), user=user,
            response=FakeResponse(), followup=SimpleNamespace(send=AsyncMock()),
            message=SimpleNamespace(content="offer"),
        )  # fmt: skip

    @staticmethod
    def button(view: Any, label: str) -> Any:
        """The button with this label (or, failing that, one starting with it: Accept all
        says how many removals it makes)."""
        labels = [(str(b.item.label), b) for b in view.children]
        exact = [b for text, b in labels if text == label]
        return (exact or [b for text, b in labels if text.startswith(label)])[0]

    async def offer(self, text: str = FILE, *, from_link: bool = True) -> tuple[str, Any]:
        words = await hs.compare_text(self.bot, campaign(), text, self.send, from_link=from_link)
        self.assertIsNone(words)
        return self.sent[-1]


class Offering(SyncTest):
    async def test_what_differs_is_offered_with_three_buttons_and_nothing_is_changed(self) -> None:
        text, view = await self.offer()
        self.assertIn("don't match:** 1 to add, 1 to change, 1 to remove.", text)
        self.assertIn("Nothing changes until you press a button.", text)
        labels = [str(b.item.label) for b in view.children]
        self.assertEqual(labels, ["Accept all (1 to remove)", "Review", "Not now"])
        self.assertEqual(len(self.store.rules), 3)
        self.assertEqual(len(self.bot.house_syncs), 1)

    async def test_an_upload_can_only_be_ignored_not_ignored_until_it_changes(self) -> None:
        _, view = await self.offer(from_link=False)
        self.assertEqual([str(b.item.label) for b in view.children][-1], "Close")

    async def test_files_that_match_are_told_so(self) -> None:
        same = "1. Keep\n2. Old\n3. Gone\n"
        upload = await hs.compare_text(self.bot, campaign(), same, self.send, from_link=False)
        link = await hs.compare_text(self.bot, campaign(), same, self.send, from_link=True)
        self.assertEqual((upload, link, self.sent), (hs.SAME, hs.SAME, []))

    async def test_a_file_with_no_rule_is_never_compared(self) -> None:
        words = await hs.compare_text(
            self.bot, campaign(), "just some notes\nnot rules\n", self.send, from_link=True
        )
        assert words is not None
        self.assertIn("couldn't find any house rules", words)
        self.assertEqual((self.sent, len(self.store.rules)), ([], 3))  # (no "remove everything")

    async def test_a_file_too_big_to_be_a_house_rules_file_is_said(self) -> None:
        words = await hs.compare_text(
            self.bot, campaign(), "1. a\n" * 100_000, self.send, from_link=True
        )
        self.assertEqual((words, self.sent), (hs.TOO_BIG, []))

    async def test_unreadable_lines_are_counted_in_the_offer(self) -> None:
        text, _ = await self.offer(FILE + "what is this\nand this\n")
        self.assertIn("2 lines in the file didn't look like rules and were skipped.", text)

    async def test_a_version_the_dm_ignored_stays_quiet_until_the_file_changes(self) -> None:
        self.links.link = DOC
        self.links.ignored = house_file_sync.fingerprint(house_file.parse(FILE).rules)
        self.assertIsNone(
            await hs.compare_text(self.bot, campaign(), FILE, self.send, from_link=True)
        )
        self.assertEqual(self.sent, [])
        await hs.compare_text(self.bot, campaign(), FILE + "10. More\n", self.send, from_link=True)
        self.assertEqual(len(self.sent), 1)

    async def test_an_offer_that_cant_be_shown_is_forgotten(self) -> None:
        self.send_works = False
        await hs.compare_text(self.bot, campaign(), FILE, self.send, from_link=True)
        self.assertEqual(len(self.bot.house_syncs), 0)

    async def test_the_oldest_offers_are_forgotten_past_the_limit(self) -> None:
        pendings = hs.Pendings()
        keys = [
            pendings.add(hs.Pending(GUILD, C1, [], "f" * 64, "s", True))
            for _ in range(hs.MAX_PENDING + 5)
        ]
        self.assertEqual(len(pendings), hs.MAX_PENDING)
        self.assertIsNone(pendings.get(keys[0]))
        self.assertIsNotNone(pendings.get(keys[-1]))


class Pressing(SyncTest):
    async def test_accept_all_does_everything_and_sends_the_new_file(self) -> None:
        _, view = await self.offer()
        it = self.interaction()
        await self.button(view, "Accept all").callback(it)
        self.assertEqual(sorted(r.number for r in self.store.rules), [1, 2, 9])
        content, new_view = it.response.edited[0]
        self.assertIsNone(new_view)
        for words in ("Added house rule 9.", "Changed house rule 2.", "Removed house rule 3."):
            self.assertIn(words, content)
        self.assertEqual(len(self.bot.house_syncs), 0)
        files = [c for c in it.followup.send.await_args_list if "file" in c.kwargs]
        self.assertEqual(len(files), 1)  # the updated file, privately (part 1)

    async def test_ignore_remembers_this_version_of_the_file(self) -> None:
        _, view = await self.offer()
        it = self.interaction()
        await self.button(view, "Not now").callback(it)
        fingerprint = house_file_sync.fingerprint(house_file.parse(FILE).rules)
        self.assertEqual(self.links.calls, [("ignore", DM, fingerprint)])
        self.assertEqual(len(self.store.rules), 3)
        self.assertIn("Left as it is", it.response.edited[0][0])

    async def test_ignoring_an_upload_remembers_nothing(self) -> None:
        _, view = await self.offer(from_link=False)
        await self.button(view, "Close").callback(self.interaction())
        self.assertEqual(self.links.calls, [])

    async def test_review_goes_one_at_a_time_accept_or_skip(self) -> None:
        _, view = await self.offer()
        it = self.interaction()
        await self.button(view, "Review").callback(it)
        text, step = it.response.edited[0]
        self.assertIn("Review 1 of 3", text)
        self.assertIn("Add as a new rule: 9. Fresh", text)
        self.assertEqual(
            [str(b.item.label) for b in step.children],
            ["Accept", "Skip", "Accept this and the rest"],
        )
        accepted = self.interaction()
        await self.button(step, "Accept").callback(accepted)
        self.assertIn(9, [r.number for r in self.store.rules])
        text, step = accepted.response.edited[0]
        self.assertIn("Review 2 of 3", text)
        self.assertIn("House rule 2 is different", text)
        skipped = self.interaction()
        await self.button(step, "Skip").callback(skipped)
        self.assertEqual({r.number: r.rule for r in self.store.rules}[2], "Old")  # left alone
        text, step = skipped.response.edited[0]
        self.assertIn("Review 3 of 3", text)
        self.assertEqual([str(b.item.label) for b in step.children], ["Accept", "Skip"])
        last = self.interaction()
        await self.button(step, "Skip").callback(last)
        content, done_view = last.response.edited[0]
        self.assertIsNone(done_view)
        self.assertIn("Added house rule 9.", content)
        self.assertEqual(sorted(r.number for r in self.store.rules), [1, 2, 3, 9])
        self.assertEqual(len(self.bot.house_syncs), 0)

    async def test_accept_the_rest_does_what_is_left(self) -> None:
        _, view = await self.offer()
        it = self.interaction()
        await self.button(view, "Review").callback(it)
        step = it.response.edited[0][1]
        skip = self.interaction()
        await self.button(step, "Skip").callback(skip)  # skips the new rule
        rest = self.interaction()
        await self.button(skip.response.edited[0][1], "Accept this and the rest").callback(rest)
        self.assertEqual(sorted(r.number for r in self.store.rules), [1, 2])

    async def test_a_press_on_an_old_step_is_told_it_is_done(self) -> None:
        _, view = await self.offer()
        review = self.interaction()
        await self.button(view, "Review").callback(review)
        step = review.response.edited[0][1]
        await self.button(step, "Skip").callback(self.interaction())
        again = self.interaction()
        await self.button(step, "Accept").callback(again)  # the first step's button, again
        self.assertEqual(again.response.sent[0][0], hs.STALE)
        self.assertEqual(sorted(r.number for r in self.store.rules), [1, 2, 3])

    async def test_a_player_or_a_dm_who_stopped_being_one_changes_nothing(self) -> None:
        _, view = await self.offer()
        for who in (PLAYER, CO_DM):
            it = self.interaction(who)
            await self.button(view, "Accept all").callback(it)
            self.assertEqual(it.response.sent[0][0], hs.ONLY_DMS)
        self.campaigns[C1] = campaign(frozenset({CO_DM}))
        it = self.interaction(DM)
        await self.button(view, "Accept all").callback(it)
        self.assertEqual(it.response.sent[0][0], hs.ONLY_DMS)
        self.assertEqual((len(self.store.rules), len(self.bot.house_syncs)), (3, 1))

    async def test_an_offer_from_before_a_restart_or_another_server_says_it_has_ended(self) -> None:
        _, view = await self.offer()
        other = self.interaction(DM, OTHER_GUILD)
        await self.button(view, "Accept all").callback(other)
        self.assertEqual(other.response.sent[0][0], hs.CLOSED)
        self.bot.house_syncs = hs.Pendings()  # a restart
        later = self.interaction()
        await self.button(view, "Accept all").callback(later)
        self.assertEqual(later.response.sent[0][0], hs.CLOSED)
        self.assertEqual(len(self.store.rules), 3)

    async def test_a_campaign_deleted_meanwhile_says_it_has_ended(self) -> None:
        _, view = await self.offer()
        self.campaigns.clear()
        it = self.interaction()
        await self.button(view, "Accept all").callback(it)
        self.assertEqual(it.response.sent[0][0], hs.CLOSED)

    async def test_two_presses_at_once_do_it_once(self) -> None:
        _, view = await self.offer()
        first, second = self.interaction(), self.interaction(CO_DM)
        self.campaigns[C1] = campaign(frozenset({DM, CO_DM}))
        await asyncio.gather(
            self.button(view, "Accept all").callback(first),
            self.button(view, "Accept all").callback(second),
        )
        self.assertEqual(len(first.response.edited) + len(second.response.edited), 1)
        self.assertEqual(sorted(r.number for r in self.store.rules), [1, 2, 9])

    async def test_a_rule_another_dm_changed_meanwhile_is_left_and_said(self) -> None:
        _, view = await self.offer()
        self.store.rules[1] = rule(2, "Their words", None, version=2)
        it = self.interaction()
        await self.button(view, "Accept all").callback(it)
        self.assertEqual(
            self.store.rules[1].rule if self.store.rules[1].number == 2 else "", "Their words"
        )
        self.assertIn("Rule 2 was left as it is", it.response.edited[0][0])

    async def test_a_store_that_breaks_is_said_and_nothing_more_is_changed(self) -> None:
        _, view = await self.offer()
        self.store.add = AsyncMock(side_effect=RuntimeError("down"))  # type: ignore[method-assign]
        it = self.interaction()
        await self.button(view, "Accept all").callback(it)
        content = it.response.edited[0][0]
        self.assertIn("Rule 9 couldn't be done just now; it was left as it is.", content)
        self.assertIn("Changed house rule 2.", content)  # the rest still went through

    async def test_ids_survive_a_restart_and_fit_a_phone(self) -> None:
        _, view = await self.offer()
        template = hs.HouseSyncButton.__discord_ui_compiled_template__
        pending = next(iter(self.bot.house_syncs._items.values()))
        pending.index = 0
        for item in [*view.children, *hs.review_view(GUILD, "abcdef01", pending).children]:
            self.assertIsNotNone(template.fullmatch(str(item.item.custom_id)))
            self.assertLessEqual(len(str(item.item.label)), 80)
            self.assertLessEqual(len(str(item.item.custom_id)), 100)


class Safeguards(SyncTest):
    """Mistakes a file can make must not cost the campaign its rules or its numbers."""

    MANY: ClassVar[list[HouseRule]] = [rule(n, f"Rule {n}") for n in range(1, 9)]

    async def test_a_file_that_removes_many_rules_has_no_accept_all(self) -> None:
        self.store.rules = list(self.MANY)
        text, view = await self.offer("1. Rule 1\n")  # 7 of 8 would go
        self.assertIn("Accept all** is off", text)
        self.assertEqual([str(b.item.label) for b in view.children], ["Review", "Not now"])
        it = self.interaction()
        await self.button(view, "Review").callback(it)
        steps = [str(b.item.label) for b in it.response.edited[0][1].children]
        self.assertEqual(steps, ["Accept", "Skip"])  # not even Accept the rest
        self.assertEqual(len(self.store.rules), 8)

    async def test_a_few_removals_make_review_the_easy_choice_and_accept_all_says_so(self) -> None:
        _, view = await self.offer()
        styles = {str(b.item.label): b.item.style for b in view.children}
        self.assertEqual(styles["Review"], discord.ButtonStyle.primary)
        self.assertEqual(styles["Accept all (1 to remove)"], discord.ButtonStyle.secondary)
        _, clean = await self.offer("1. Keep\n2. Old\n3. Gone\n4. Extra\n")
        label = {str(b.item.label): b.item.style for b in clean.children}["Accept all changes"]
        self.assertEqual(label, discord.ButtonStyle.primary)  # nothing to remove

    async def test_a_newer_offer_replaces_the_older_one(self) -> None:
        _, old = await self.offer()
        await self.offer(FILE + "10. More\n")
        self.assertEqual(len(self.bot.house_syncs), 1)
        it = self.interaction()
        await self.button(old, "Accept all").callback(it)
        self.assertEqual(it.response.sent[0][0], hs.CLOSED)
        self.assertEqual(len(self.store.rules), 3)

    async def test_accepting_once_retires_the_other_offers_so_nothing_is_added_twice(self) -> None:
        _, view = await self.offer()
        other = hs.Pending(
            GUILD, C1, next(iter(self.bot.house_syncs._items.values())).items, "x" * 64, "s", False
        )
        other_key = self.bot.house_syncs.add(other)
        await self.button(view, "Accept all").callback(self.interaction())
        self.assertIsNone(self.bot.house_syncs.get(other_key))
        self.assertEqual(len(self.bot.house_syncs), 0)

    async def test_the_same_offer_done_twice_adds_no_copies(self) -> None:
        items = house_file_sync.items_of(
            house_file.compare(self.store.rules, house_file.parse(FILE).rules)
        )
        for _ in range(2):
            await house_file_sync.apply(self.store, GUILD, C1, DM, items)
        self.assertEqual(sorted(r.rule for r in self.store.rules).count("Fresh"), 1)

    async def test_a_failure_part_way_is_said_and_the_rest_still_done(self) -> None:
        self.store.rules = [rule(1, "Keep"), rule(2, "Old")]
        _, view = await self.offer("1. Keep\n2. New\n5. Five\n6. Six\n")
        real = self.store.add
        calls = {"n": 0}

        async def flaky(*args: Any, **kwargs: Any) -> Any:
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("database went away")
            return await real(*args, **kwargs)

        self.store.add = flaky  # type: ignore[method-assign]
        it = self.interaction()
        await self.button(view, "Accept all").callback(it)
        content = it.response.edited[0][0]
        self.assertIn("1 left alone", content)
        self.assertIn("couldn't be done just now", content)
        self.assertIn("Added house rule 6.", content)
        self.assertIn("Changed house rule 2.", content)
        self.assertEqual(len(self.bot.house_syncs), 0)

    async def test_many_changes_answer_discord_first(self) -> None:
        self.store.rules = []
        text = "".join(f"{n}. Rule {n}\n" for n in range(1, 9))
        _, view = await self.offer(text)
        order: list[str] = []
        real = self.store.add

        async def slow(*args: Any, **kwargs: Any) -> Any:
            order.append("add")
            return await real(*args, **kwargs)

        self.store.add = slow  # type: ignore[method-assign]
        it = self.interaction()
        it.edit_original_response = AsyncMock()
        real_defer = it.response.defer

        async def defer(**kw: Any) -> None:
            order.append("defer")
            await real_defer(**kw)

        it.response.defer = defer
        await self.button(view, "Accept all").callback(it)
        self.assertEqual(order[0], "defer")
        self.assertEqual(order.count("add"), 8)
        it.edit_original_response.assert_awaited()
        self.assertIn("Done: 8 changed.", it.edit_original_response.await_args.kwargs["content"])

    async def test_a_few_changes_are_answered_in_one_step(self) -> None:
        _, view = await self.offer()
        it = self.interaction()
        await self.button(view, "Accept all").callback(it)
        self.assertEqual(len(it.response.edited), 1)


class Reading(SyncTest):
    async def check(self) -> None:
        await hs.check_linked(self.bot, campaign(), self.send, quiet_if_same=True)

    async def test_no_link_says_nothing(self) -> None:
        await self.check()
        self.assertEqual(self.sent, [])

    async def test_a_linked_file_that_differs_is_offered(self) -> None:
        self.links.link = DOC
        got = fetch.Fetched(FILE.encode(), "link.txt")
        with patch.object(fetch, "fetch", AsyncMock(return_value=got)) as fetched:
            await self.check()
        fetched.assert_awaited_once_with(DOC)
        self.assertIn("1 to add", self.sent[0][0])

    async def test_a_linked_file_that_matches_says_nothing_at_a_session_start(self) -> None:
        self.links.link = DOC
        got = fetch.Fetched(b"1. Keep\n2. Old\n3. Gone\n", "link.txt")
        with patch.object(fetch, "fetch", AsyncMock(return_value=got)):
            await self.check()
            await hs.check_linked(self.bot, campaign(), self.send, quiet_if_same=False)
        self.assertEqual([t for t, _ in self.sent], [hs.SAME])  # only when the DM asked

    async def test_an_unreadable_link_gets_one_short_note_and_never_shows_the_link(self) -> None:
        self.links.link = DOC
        error = fetch.LinkError("That file isn't shared. Share it with anyone who has the link.")
        with patch.object(fetch, "fetch", AsyncMock(side_effect=error)):
            await self.check()
        ((text, view),) = self.sent
        self.assertIn("I couldn't read your house-rules file", text)
        self.assertIn("The session goes on with DMbot's copy", text)
        self.assertNotIn("docs.google.com", text)
        self.assertNotIn("a" * 30, text)
        self.assertEqual(len(view.children), 0)

    async def test_a_file_too_big_is_refused_before_it_is_decoded(self) -> None:
        self.links.link = DOC
        huge = fetch.Fetched(b"1. a\n" * (hs.READ_MAX_BYTES // 4), "link.txt")
        with patch.object(fetch, "fetch", AsyncMock(return_value=huge)):
            await self.check()
        self.assertEqual(self.sent[0][0], hs.TOO_BIG)

    async def test_a_file_that_is_not_plain_text_is_said(self) -> None:
        self.links.link = DOC
        with patch.object(
            fetch, "fetch", AsyncMock(return_value=fetch.Fetched(b"%PDF", "link.pdf"))
        ):
            await self.check()
        self.assertEqual(self.sent[0][0], hs.NOT_TEXT)

    async def test_nothing_ever_raises_into_the_session(self) -> None:
        self.links.link = DOC
        with patch.object(fetch, "fetch", AsyncMock(side_effect=RuntimeError("boom"))):
            await self.check()
        self.assertEqual(self.sent, [])

    async def test_the_link_is_never_logged(self) -> None:
        self.links.link = DOC
        error = fetch.LinkError("It isn't shared.")
        with (
            patch.object(fetch, "fetch", AsyncMock(side_effect=error)),
            self.assertNoLogs(hs.log, level="DEBUG"),
        ):
            await self.check()


class Menu(SyncTest):
    async def test_a_dm_sees_how_to_link_and_a_player_is_refused(self) -> None:
        it = self.interaction()
        await file_ui.open_menu(it, campaign())
        text, kw = it.followup.send.await_args.args[0], it.followup.send.await_args.kwargs
        self.assertEqual(text, file_ui.NOT_LINKED)
        labels = [str(c.label) for c in kw["view"].children]
        self.assertEqual(labels, [file_ui.LINK_LABEL, file_ui.UPLOAD_LABEL])
        player = self.interaction(PLAYER)
        await file_ui.open_menu(player, campaign())
        self.assertEqual(player.followup.send.await_args.args[0], file_ui.ONLY_DMS)

    async def test_a_linked_file_shows_the_site_only_and_more_buttons(self) -> None:
        self.links.link = DOC
        it = self.interaction()
        await file_ui.open_menu(it, campaign())
        text = it.followup.send.await_args.args[0]
        self.assertIn("**docs.google.com**", text)
        self.assertNotIn("a" * 30, text)
        labels = [str(c.label) for c in it.followup.send.await_args.kwargs["view"].children]
        self.assertEqual(
            labels,
            [file_ui.CHANGE_LABEL, file_ui.CHECK_LABEL, file_ui.UNLINK_LABEL, file_ui.UPLOAD_LABEL],
        )

    async def test_linking_checks_the_file_at_once_and_offers_what_differs(self) -> None:
        it = self.interaction()
        got = fetch.Fetched(FILE.encode(), "link.txt")
        with patch.object(fetch, "fetch", AsyncMock(return_value=got)):
            await file_ui.link_file(it, campaign(), DOC)
        self.assertEqual(self.links.calls, [("set", DM, DOC)])
        first, offer = it.followup.send.await_args_list[0], it.followup.send.await_args_list[-1]
        self.assertIn("Connected: **docs.google.com**", first.args[0])
        self.assertNotIn("a" * 30, first.args[0])
        self.assertIn("1 to add", offer.args[0])
        self.assertTrue(offer.kwargs["ephemeral"])

    async def test_check_it_now_also_looks_at_a_version_that_was_ignored(self) -> None:
        self.links.link = DOC
        self.links.ignored = house_file_sync.fingerprint(house_file.parse(FILE).rules)
        got = fetch.Fetched(FILE.encode(), "link.txt")
        with patch.object(fetch, "fetch", AsyncMock(return_value=got)):
            await hs.check_linked(self.bot, campaign(), self.send, quiet_if_same=True)
            self.assertEqual(self.sent, [])  # a session start stays quiet
            await hs.check_linked(self.bot, campaign(), self.send, quiet_if_same=False)
        self.assertIn("1 to add", self.sent[0][0])  # asked for: shown

    async def test_a_player_or_a_bad_link_links_nothing(self) -> None:
        player = self.interaction(PLAYER)
        await file_ui.link_file(player, campaign(), DOC)
        self.assertEqual(self.links.calls, [])
        bad = self.interaction()
        await file_ui.link_file(bad, campaign(), "nonsense")
        self.assertIn("doesn't look like a link", bad.followup.send.await_args.args[0])
        self.assertIsNone(self.links.link)

    @staticmethod
    def unlink_button(menu: file_ui.FileMenu) -> Any:
        return next(c for c in menu.children if getattr(c, "label", "") == file_ui.UNLINK_LABEL)

    async def test_unlinking(self) -> None:
        self.links.link = DOC
        menu = file_ui.FileMenu(campaign(), linked=True)
        it = self.interaction()
        await self.unlink_button(menu).callback(it)
        self.assertIsNone(self.links.link)
        self.assertIn("Done. DMbot won't read", it.followup.send.await_args.args[0])
        player = self.interaction(PLAYER)
        self.links.link = DOC
        await self.unlink_button(menu).callback(player)
        self.assertEqual(self.links.link, DOC)  # a player cannot

    async def test_comparing_by_hand_waits_a_few_seconds_and_never_twice_at_once(self) -> None:
        self.links.link = DOC
        got = fetch.Fetched(FILE.encode(), "link.txt")
        fetched = AsyncMock(return_value=got)
        with patch.object(fetch, "fetch", fetched):
            first, second = self.interaction(), self.interaction()
            await file_ui.check_now(first, campaign())
            await file_ui.check_now(second, campaign())
        fetched.assert_awaited_once()  # the second came a moment after the first
        self.assertEqual(second.followup.send.await_args.args[0], file_ui.TOO_OFTEN)
        file_ui._last.clear()
        gate = asyncio.Event()

        async def slow(link: str) -> fetch.Fetched:
            await gate.wait()
            return got

        with patch.object(fetch, "fetch", slow):
            running = asyncio.create_task(file_ui.check_now(self.interaction(), campaign()))
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            file_ui._last.clear()
            busy = self.interaction()
            await file_ui.check_now(busy, campaign())
            self.assertEqual(busy.followup.send.await_args.args[0], file_ui.ALREADY)
            gate.set()
            await running
        self.assertEqual(file_ui._busy, set())  # the turn is given back

    async def test_the_slash_link_option_without_a_campaign_says_what_to_do(self) -> None:
        self.assertIn("pick the campaign", file_ui.PICK_FIRST)

    async def test_the_slash_link_option_is_there(self) -> None:
        from dmbot.ui.house_rules import dmbot_house_rules

        self.assertIn("link", [p.name for p in dmbot_house_rules.parameters])


class Uploading(SyncTest):
    def form(self, data: bytes | None, size: int | None = None) -> Any:
        form = file_ui.UploadForm(campaign())
        attachment = SimpleNamespace(
            size=size or len(data or b""), read=AsyncMock(return_value=data)
        )
        form.file.component._values = [attachment] if data is not None else []  # type: ignore[attr-defined]
        form.attachment = attachment  # type: ignore[attr-defined]
        return form

    async def test_a_file_is_compared_and_offered_privately(self) -> None:
        form = self.form(FILE.encode())
        it = self.interaction()
        await form.on_submit(it)
        offer = it.followup.send.await_args
        self.assertIn("1 to add", offer.args[0])
        self.assertTrue(offer.kwargs["ephemeral"])
        self.assertEqual(self.sent, [])  # not on the DM screen
        self.assertEqual(len(self.store.rules), 3)

    async def test_a_player_is_refused_before_anything_is_read(self) -> None:
        form = self.form(FILE.encode())
        it = self.interaction(PLAYER)
        await form.on_submit(it)
        self.assertEqual(it.followup.send.await_args.args[0], file_ui.ONLY_DMS)
        form.attachment.read.assert_not_awaited()

    async def test_no_file_or_a_huge_one_is_told(self) -> None:
        none = self.interaction()
        await self.form(None).on_submit(none)
        self.assertIn("No file was added", none.followup.send.await_args.args[0])
        big = self.form(b"x", size=hs.READ_MAX_BYTES + 1)
        it = self.interaction()
        await big.on_submit(it)
        self.assertEqual(it.followup.send.await_args.args[0], hs.TOO_BIG)
        big.attachment.read.assert_not_awaited()

    async def test_a_matching_file_and_a_junk_file_are_told(self) -> None:
        same = self.interaction()
        await self.form(b"1. Keep\n2. Old\n3. Gone\n").on_submit(same)
        self.assertEqual(same.followup.send.await_args.args[0], hs.SAME)
        file_ui._last.clear()  # (a comparison by hand waits a few seconds after the last)
        junk = self.interaction()
        await self.form(b"\x00\x01 not rules").on_submit(junk)
        self.assertIn("couldn't find any house rules", junk.followup.send.await_args.args[0])


class SessionStart(SyncTest):
    """At a session start the bot reads the linked file in the background and puts what
    differs on the DM screen, and only there."""

    async def asyncSetUp(self) -> None:
        from dmbot.bot import DMBot
        from dmbot.config import Settings

        consent = SimpleNamespace(has_consent=lambda g, u: True)
        stores = SimpleNamespace(get=AsyncMock(return_value=campaign()))
        settings = Settings(discord_token="t", ears_secret="s")
        self.real = DMBot(settings, consent, stores, MagicMock())  # type: ignore[arg-type]
        self.real.house_rules = self.store  # type: ignore[assignment]
        self.real.house_file_links = self.links  # type: ignore[assignment]
        self.posts: list[tuple[int, str, Any]] = []

        async def post_message(channel_id: int, text: str, view: Any = None) -> object:
            self.posts.append((channel_id, text, view))
            return object()

        self.real.post_message = post_message  # type: ignore[method-assign,assignment]

    async def settle(self) -> None:
        for task in list(asyncio.all_tasks() - {asyncio.current_task()}):
            if task.get_name() == "house-file-check":
                await task

    async def test_what_differs_goes_to_the_dm_screen_only(self) -> None:
        self.links.link = DOC
        got = fetch.Fetched(FILE.encode(), "link.txt")
        with patch.object(fetch, "fetch", AsyncMock(return_value=got)):
            self.real._check_house_file(campaign(), SCREEN)
            await self.settle()
        ((channel, text, view),) = self.posts
        self.assertEqual(channel, SCREEN)
        self.assertIn("1 to add", text)
        self.assertEqual(len(view.children), 3)
        self.assertEqual(len(self.store.rules), 3)  # nothing changed by itself

    async def test_no_link_and_no_difference_post_nothing(self) -> None:
        self.real._check_house_file(campaign(), SCREEN)
        await self.settle()
        self.links.link = DOC
        same = fetch.Fetched(b"1. Keep\n2. Old\n3. Gone\n", "link.txt")
        with patch.object(fetch, "fetch", AsyncMock(return_value=same)):
            self.real._check_house_file(campaign(), SCREEN)
            await self.settle()
        self.assertEqual(self.posts, [])

    async def test_without_the_store_nothing_happens(self) -> None:
        self.real.house_file_links = None
        self.real._check_house_file(campaign(), SCREEN)
        await self.settle()
        self.assertEqual(self.posts, [])

    async def test_an_unreadable_link_posts_one_note_without_the_link(self) -> None:
        self.links.link = DOC
        error = fetch.LinkError("It isn't shared.")
        with patch.object(fetch, "fetch", AsyncMock(side_effect=error)):
            self.real._check_house_file(campaign(), SCREEN)
            await self.settle()
        ((_, text, view),) = self.posts
        self.assertIn("I couldn't read your house-rules file", text)
        self.assertNotIn(DOC, text)
        self.assertIsNone(view)


class Wiring(unittest.TestCase):
    def test_the_buttons_are_registered_for_after_a_restart(self) -> None:
        tree = ast.parse(inspect.getsource(bot_module))
        registered = {
            arg.attr if isinstance(arg, ast.Attribute) else arg.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "add_dynamic_items"
            for arg in node.args
            if isinstance(arg, (ast.Name, ast.Attribute))
        }
        self.assertLessEqual({"HouseSyncButton", "HouseFileButton"}, registered)

    def test_the_settings_button_is_for_dms_and_fits(self) -> None:
        view = screen_settings.settings_view(campaign(), None, DM)
        built = {type(i).__name__ for i in view.children}
        self.assertIn("HouseFileButton", built)
        player = screen_settings.settings_view(campaign(), None, PLAYER)
        self.assertNotIn("HouseFileButton", {type(i).__name__ for i in player.children})
        button = screen_settings.HouseFileButton(C1)
        template = screen_settings.HouseFileButton.__discord_ui_compiled_template__
        self.assertIsNotNone(template.fullmatch(str(button.item.custom_id)))


if __name__ == "__main__":
    unittest.main()
