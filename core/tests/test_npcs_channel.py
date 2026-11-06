"""The NPC roster channel (#1761): what the list may say to players (each thing it must never
show), how it is laid out and kept in step (edited in place, order, the 200 cap, the minute
between passes), who sees it (it follows the DM screen with the rules channel; a peek opens and a
hide closes all three) and that campaigns and servers never mix. A fake Discord; the roster and
setup tests need the test database."""

from __future__ import annotations

import asyncio
import dataclasses
import unittest
from typing import Any
from unittest import mock
from unittest.mock import AsyncMock, MagicMock

import discord
import psycopg

from dmbot.campaigns import CampaignStore
from dmbot.dm_screen import messages
from dmbot.dm_screen import npcs_channel as nc
from dmbot.dm_screen.buttons import ConfirmPeek, HideButton
from dmbot.memory import recall
from dmbot.memory.models import DEAD, MemoryRuleError
from dmbot.memory.recall import RecallService, RosterEntry
from tests.pg import DatabaseTest
from tests.test_dm_screen_setup import BOT, BOT_PERMS, GENERAL, text_channel
from tests.test_memory_store import GUILD_A, GUILD_B, MemoryTest
from tests.test_rules_channel import ME, Chan

GUILD, DM, PLAYER = 111, 7, 8
NEW = 680000000000000081  # long: a short id turns up inside timestamps (#1213)
RULES = 680000000000000082
SCREEN = 680000000000000083


def entry(n: int, name: str | None = None, **kw: Any) -> RosterEntry:
    fields: dict[str, Any] = {"kind": "NPC", "is_group": False}
    fields.update(kw)
    return RosterEntry(f"{n:032x}", name or f"Person {n:03}", **fields)


class MemMap:
    """The message map in memory (the database one is tested apart)."""

    def __init__(self) -> None:
        self.rows: dict[str, int] = {}

    async def load(self, guild_id: int, campaign_id: str) -> dict[str, int]:
        return dict(self.rows)

    async def save(self, guild_id: int, campaign_id: str, entry_id: str, message_id: int) -> None:
        self.rows[entry_id] = message_id

    async def forget(self, guild_id: int, campaign_id: str, entry_ids: Any) -> None:
        for key in entry_ids:
            self.rows.pop(key, None)


async def sync(chan: Chan, entries: list[RosterEntry], **kw: Any) -> Any:
    """One pass on this channel; the channel keeps its own message map between passes."""
    mapping = chan.__dict__.setdefault("map", MemMap())
    return await nc.sync_roster(
        chan,  # type: ignore[arg-type]
        ME,
        entries,
        mapping,
        guild_id=1,
        campaign_id="c" * 32,
        **kw,
    )


class Saying(unittest.TestCase):
    def test_a_person_says_name_kind_other_names_groups_and_ties(self) -> None:
        text = nc.entry_text(
            entry(
                1, "Mira", also_called=("The Hooded Stranger", "Red"), groups=("Ash Cult",),
                ties=("Ulfgar", "Cerric"), ties_more=3,
            )
        )  # fmt: skip
        self.assertEqual(
            text,
            "👤 **Mira** · NPC\n"
            "Also called: The Hooded Stranger, Red\n"
            "In: Ash Cult\n"
            "Connected to: Ulfgar, Cerric, and 3 more",
        )

    def test_a_group_has_its_own_mark_and_a_bare_entry_is_two_lines(self) -> None:
        text = nc.entry_text(entry(2, "Ash Cult", kind="group", is_group=True))
        self.assertEqual(text, "👥 **Ash Cult** · group")

    def test_names_cannot_ping_or_format_the_message(self) -> None:
        text = nc.entry_text(entry(3, "**@everyone** _x_", groups=("<@123>",)))
        self.assertNotIn("**@everyone**", text)
        self.assertIn("\\*\\*@everyone\\*\\*", text)

    def test_a_message_never_goes_over_discords_limit(self) -> None:
        names = tuple("n" * 100 for _ in range(30))
        text = nc.entry_text(entry(4, "x" * 100, also_called=names, groups=names, ties=names))
        self.assertLessEqual(len(text), 2000)

    def test_a_message_never_carries_an_id(self) -> None:
        text = nc.entry_text(entry(5, "Mira", groups=("Ash Cult",), ties=("Ulfgar",)))
        self.assertNotIn(f"{5:032x}", text)
        self.assertNotIn("ref", text)

    def test_the_names_of_the_channel_are_recognised(self) -> None:
        self.assertTrue(nc.is_npcs_name("dmb-npcs-x"))
        self.assertFalse(nc.is_npcs_name("dmb-rules-x"))
        self.assertFalse(nc.is_npcs_name("general"))

    def test_the_minute_between_passes(self) -> None:
        self.assertEqual(nc.pass_delay(None, 1000.0), nc.SETTLE_S)  # the first: just settle
        self.assertEqual(nc.pass_delay(1000.0, 1010.0), 50.0)  # a pass ten seconds ago
        self.assertEqual(nc.pass_delay(1000.0, 1100.0), nc.SETTLE_S)  # long ago: just settle
        self.assertEqual(nc.pass_delay(1000.0, 1059.0), 3.0)


class Keeping(unittest.IsolatedAsyncioTestCase):
    async def test_groups_first_then_people_one_message_each_and_nothing_again(self) -> None:
        chan = Chan()
        entries = [
            entry(1, "Ash Cult", kind="group", is_group=True),
            entry(2, "Cerric"),
            entry(3, "Mira"),
        ]
        done = await sync(chan, entries)
        self.assertEqual((done.sent, done.edited, done.deleted), (3, 0, 0))
        self.assertEqual([t.splitlines()[0] for t in chan.texts()], [
            "👥 **Ash Cult** · group", "👤 **Cerric** · NPC", "👤 **Mira** · NPC",
        ])  # fmt: skip
        again = await sync(chan, entries)
        self.assertEqual((again.sent, again.edited, again.deleted), (0, 0, 0))
        self.assertEqual(chan.sent, 3)

    async def test_a_rename_edits_the_same_message_in_place(self) -> None:
        chan = Chan()
        await sync(chan, [entry(1, "Mira"), entry(2, "Ulfgar")])
        first = chan.messages[0]
        done = await sync(chan, [entry(1, "Mirabel"), entry(2, "Ulfgar")])
        self.assertEqual((done.sent, done.edited, done.deleted), (0, 1, 0))
        self.assertIs(chan.messages[0], first)
        self.assertIn("Mirabel", first.content)

    async def test_a_merged_or_rejected_entry_is_deleted_and_the_rest_left(self) -> None:
        chan = Chan()
        await sync(chan, [entry(1, "Mira"), entry(2, "Ulfgar")])
        done = await sync(chan, [entry(2, "Ulfgar")])
        self.assertEqual((done.deleted, done.sent, done.edited), (1, 0, 0))
        self.assertEqual(len(chan.texts()), 1)
        self.assertIn("Ulfgar", chan.texts()[0])

    def names(self, chan: Chan) -> list[str]:
        return [t.splitlines()[0] for t in chan.texts()]

    async def test_a_new_entry_goes_at_the_end_and_a_start_does_not_reorder_for_one(self) -> None:
        chan = Chan()
        base = [entry(n, f"B{n:02}") for n in range(2, 12)]
        await sync(chan, base)
        both = [entry(1, "Aldric"), *base]
        await sync(chan, both)
        self.assertEqual(self.names(chan)[-1], "👤 **Aldric** · NPC")
        quiet = await sync(chan, both, reorder=True)  # a start: one out of place is left alone
        self.assertEqual((quiet.deleted, quiet.sent, quiet.edited), (0, 0, 0))

    async def test_ten_messages_out_of_place_are_put_in_order_when_reordering(self) -> None:
        chan = Chan()
        base = [entry(n, f"B{n:02}") for n in range(20, 32)]
        await sync(chan, base)
        both = [entry(n, f"A{n:02}") for n in range(1, 13)] + base  # twelve new, sorted first
        await sync(chan, both)  # appended at the end
        before = self.names(chan)
        done = await sync(chan, both, reorder=True)
        after = self.names(chan)
        self.assertNotEqual(before, after)
        self.assertEqual(after, sorted(after))
        self.assertEqual((done.deleted, done.sent), (24, 24))
        again = await sync(chan, both, reorder=True)
        self.assertEqual((again.deleted, again.sent), (0, 0))

    async def test_fewer_than_ten_out_of_place_are_left_alone_even_when_reordering(self) -> None:
        chan = Chan()
        base = [entry(n, f"B{n:02}") for n in range(20, 26)]
        await sync(chan, base)
        both = [entry(1, "Aldric"), *base]
        await sync(chan, both)
        done = await sync(chan, both, reorder=True)
        self.assertEqual((done.deleted, done.sent), (0, 0))

    async def test_a_reorder_uses_one_bulk_delete_for_young_messages(self) -> None:
        import datetime as dt

        chan = Chan()
        bulk: list[int] = []

        async def delete_messages(msgs: Any) -> None:
            bulk.append(len(msgs))
            for m in list(msgs):
                chan.messages.remove(m)

        chan.delete_messages = delete_messages  # type: ignore[attr-defined]
        base = [entry(n, f"B{n:02}") for n in range(20, 32)]
        await sync(chan, base)
        both = [entry(n, f"A{n:02}") for n in range(1, 13)] + base
        await sync(chan, both)
        for m in chan.messages:
            m.created_at = dt.datetime.now(dt.UTC)  # type: ignore[attr-defined]
        await sync(chan, both, reorder=True)
        self.assertEqual(bulk, [24])
        self.assertEqual(self.names(chan), sorted(self.names(chan)))

    async def test_old_messages_are_deleted_one_at_a_time(self) -> None:
        import datetime as dt

        chan = Chan()
        chan.delete_messages = AsyncMock()  # type: ignore[attr-defined]
        await sync(chan, [entry(n) for n in range(1, 4)])
        for m in chan.messages:
            m.created_at = dt.datetime.now(dt.UTC) - dt.timedelta(days=30)  # type: ignore[attr-defined]
        await sync(chan, [])
        chan.delete_messages.assert_not_awaited()  # type: ignore[attr-defined]
        self.assertEqual(chan.texts(), [])

    async def test_a_secret_making_edit_goes_out_before_cosmetic_ones(self) -> None:
        chan = Chan()
        entries = [entry(n, f"P{n:02}", also_called=("Red",)) for n in range(1, 41)]
        await sync(chan, entries)
        await sync(chan, entries)
        changed = [dataclasses.replace(e, kind="npc") for e in entries]  # 40 cosmetic edits
        changed[39] = dataclasses.replace(changed[39], also_called=())  # the last: a name goes
        done = await sync(chan, changed)
        self.assertEqual(done.edited, 25)
        self.assertNotIn("Also called", chan.texts()[39])  # first in line, though last in order

    async def test_other_peoples_messages_and_the_card_are_never_touched(self) -> None:
        chan = Chan()
        chan.say(999, nc.entry_text(entry(1, "Fake")))
        chan.say(ME, "📖 **What's this channel?**\nThe people and groups.")
        await sync(chan, [], reorder=True)
        self.assertEqual(len(chan.messages), 2)
        self.assertEqual((chan.edits, chan.deletes, chan.sent), (0, 0, 0))

    async def test_a_message_with_no_row_is_cleaned_up_and_posted_again(self) -> None:
        chan = Chan()
        text = nc.entry_text(entry(1))
        chan.say(ME, text)  # DMbot's own, but not in the map (a crash, or a lost row)
        done = await sync(chan, [entry(1)])
        self.assertEqual((done.deleted, done.sent), (1, 1))
        self.assertEqual(chan.texts(), [text])
        self.assertIn(f"{1:032x}", chan.map.rows)  # type: ignore[attr-defined]

    async def test_a_message_deleted_by_hand_is_posted_again(self) -> None:
        chan = Chan()
        await sync(chan, [entry(1), entry(2)])
        chan.messages.pop(0)  # an admin deleted it
        done = await sync(chan, [entry(1), entry(2)])
        self.assertEqual(done.sent, 1)
        self.assertEqual(len(chan.texts()), 2)

    async def test_at_most_25_edits_a_pass_and_the_rest_next_time(self) -> None:
        chan = Chan()
        entries = [entry(n) for n in range(1, 31)]
        for _ in range(2):
            await sync(chan, entries)
        changed = [dataclasses.replace(e, kind="npc") for e in entries]
        first = await sync(chan, changed)
        self.assertEqual((first.edited, first.left_for_next_time), (25, 5))
        second = await sync(chan, changed)
        self.assertEqual((second.edited, second.left_for_next_time), (5, 0))

    async def test_a_rebuild_cut_short_by_the_delete_cap_finishes_in_order(self) -> None:
        chan = Chan()
        rest = [entry(n, f"B{n:03}") for n in range(100, 280)]
        for _ in range(8):
            await sync(chan, rest)
        # twelve new entries sort before all of them but were posted last
        everyone = [entry(n, f"A{n:02}") for n in range(1, 13)] + rest
        for _ in range(3):
            await sync(chan, everyone)
        for _ in range(12):
            done = await sync(chan, everyone, reorder=True)
            if not done.left_for_next_time:
                break
        heads = self.names(chan)
        self.assertEqual(heads[0], "👤 **A01** · NPC")
        self.assertEqual(heads, sorted(heads))
        self.assertEqual(len(heads), 192)

    async def test_no_permission_stops_the_pass_after_one_try_of_each_kind(self) -> None:
        chan = Chan()
        await sync(chan, [entry(1), entry(2), entry(3)])
        chan.delete_messages = AsyncMock(  # type: ignore[attr-defined]
            side_effect=discord.Forbidden(MagicMock(status=403), "no")
        )
        denied = discord.Forbidden(MagicMock(status=403), "no")
        for msg in chan.messages:
            msg.delete = AsyncMock(side_effect=denied)  # type: ignore[method-assign]
            msg.edit = AsyncMock(side_effect=denied)  # type: ignore[method-assign]
        done = await sync(chan, [dataclasses.replace(entry(1), kind="npc")])
        self.assertEqual(sum(m.delete.await_count for m in chan.messages), 1)  # type: ignore[attr-defined]
        self.assertEqual(sum(m.edit.await_count for m in chan.messages), 1)  # type: ignore[attr-defined]
        self.assertGreater(done.left_for_next_time, 0)
        empty = Chan()
        empty.send = AsyncMock(side_effect=denied)  # type: ignore[method-assign]
        await sync(empty, [entry(1), entry(9)])
        self.assertEqual(empty.send.await_count, 1)

    async def test_at_most_25_new_messages_a_pass(self) -> None:
        chan = Chan()
        entries = [entry(n) for n in range(1, 31)]
        first = await sync(chan, entries)
        self.assertEqual((first.sent, first.left_for_next_time), (25, 5))
        second = await sync(chan, entries)
        self.assertEqual((second.sent, second.left_for_next_time), (5, 0))
        self.assertEqual(len(chan.texts()), 30)

    async def test_a_message_discord_refuses_is_tried_again_next_pass(self) -> None:
        chan = Chan()
        real = chan.send
        chan.send = AsyncMock(  # type: ignore[method-assign]
            side_effect=discord.HTTPException(MagicMock(status=500), "down")
        )
        failed = await sync(chan, [entry(1)])
        self.assertEqual((failed.sent, failed.left_for_next_time), (0, 1))
        chan.send = real  # type: ignore[method-assign]
        self.assertEqual((await sync(chan, [entry(1)])).sent, 1)

    async def test_a_restart_rebuilds_from_the_channel_without_reposting(self) -> None:
        chan = Chan()
        entries = [entry(1), entry(2)]
        await sync(chan, entries, reorder=True)
        sent = chan.sent
        await sync(chan, entries, reorder=True)  # (a new process reads the same channel)
        self.assertEqual(chan.sent, sent)
        fresh = Chan()  # the channel was emptied by hand
        await sync(fresh, entries, reorder=True)
        self.assertEqual(len(fresh.texts()), 2)


class TheCap(unittest.IsolatedAsyncioTestCase):
    def many(self, count: int) -> list[RosterEntry]:
        return [entry(n) for n in range(1, count + 1)]

    async def test_200_entries_fit_without_a_more_message(self) -> None:
        chan = Chan()
        for _ in range(8):
            await sync(chan, self.many(200))
        texts = chan.texts()
        self.assertEqual(len(texts), 200)
        self.assertFalse(any(t.startswith(nc.MORE_TITLE) for t in texts))

    async def test_past_200_the_last_message_says_how_many_more(self) -> None:
        chan = Chan()
        for _ in range(9):
            await sync(chan, self.many(213))
        texts = chan.texts()
        self.assertEqual(len(texts), 201)
        self.assertEqual(texts[-1], "➕ **+13 more.** Ask your DM for the rest.")
        self.assertNotIn(f"{201:032x}", " ".join(texts))

    async def test_the_more_message_changes_in_place_and_stays_last(self) -> None:
        chan = Chan()
        for _ in range(9):
            await sync(chan, self.many(205))
        more = chan.messages[-1]
        await sync(chan, self.many(206))
        self.assertIs(chan.messages[-1], more)
        self.assertIn("+6 more", more.content)

    async def test_a_new_message_goes_before_the_more_message_never_after_it(self) -> None:
        chan = Chan()
        for _ in range(9):
            await sync(chan, self.many(205))
        smaller = self.many(199)  # two entries went; the list now fits
        for _ in range(2):
            await sync(chan, smaller)
        self.assertFalse(any(t.startswith(nc.MORE_TITLE) for t in chan.texts()))
        self.assertEqual(len(chan.texts()), 199)


class WhatPlayersMayKnow(MemoryTest):
    """`RecallService.roster` is recall's players audience, with the same filters; each thing the
    list must never show has a test."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.mira = await self.add("Mira", type="character", role="npc", description="Hides a vow")
        self.cult = await self.add("Ash Cult", type="faction")
        self.ulfgar = await self.add("Ulfgar", type="character", role="npc")
        self.add_calls = 0

    async def add(self, name: str, **kw: Any) -> str:
        kw.setdefault("status", "confirmed")
        return await super().add(name, **kw)

    async def tie(self, a: str, pred: str, b: str, **kw: Any) -> str:
        kw.setdefault("status", "confirmed")
        written = await self.relate(a, pred, b, **kw)
        return str(written.value[0].id)

    async def roster(self, campaign: str | None = None, guild: int = GUILD_A) -> list[RosterEntry]:
        return await RecallService(self.memory).roster(guild, campaign or self.c)

    async def names(self, **kw: Any) -> list[str]:
        return [e.name for e in await self.roster(**kw)]

    # ---- what it shows ---------------------------------------------------------------

    async def test_groups_first_then_people_by_name(self) -> None:
        await self.add("Brindle", type="character", role="npc")
        await self.add("Zed's Guild", type="faction")
        self.assertEqual(
            await self.names(), ["Ash Cult", "Zed's Guild", "Brindle", "Mira", "Ulfgar"]
        )

    async def test_kind_other_names_groups_and_ties(self) -> None:
        await self.memory.add_alias(
            GUILD_A, self.c, self.mira, "Red", kind="nickname", source="dm", status="confirmed"
        )
        await self.tie(self.mira, "member_of", self.cult)
        await self.tie(self.mira, "kin_of", self.ulfgar)
        got = {e.name: e for e in await self.roster()}
        mira = got["Mira"]
        self.assertEqual(
            (mira.kind, mira.is_group, mira.also_called, mira.groups, mira.ties),
            ("NPC", False, ("Red",), ("Ash Cult",), ("Ulfgar",)),
        )
        self.assertEqual((got["Ash Cult"].kind, got["Ash Cult"].is_group), ("group", True))
        self.assertEqual(got["Ulfgar"].ties, ("Mira",))  # a tie goes both ways
        self.assertEqual(got["Ulfgar"].groups, ())

    async def test_at_most_five_ties_then_the_rest_counted(self) -> None:
        for n in range(7):
            other = await self.add(f"Friend {n}", type="character", role="npc")
            await self.tie(self.mira, "ally_of", other)
        mira = next(e for e in await self.roster() if e.name == "Mira")
        self.assertEqual((len(mira.ties), mira.ties_more), (5, 2))

    async def test_a_god_is_called_a_god(self) -> None:
        await self.add("Tymora", type="character", role="npc")
        got = next(e for e in await self.roster() if e.name == "Tymora")
        self.assertEqual(got.kind, "NPC")

    # ---- what it never shows ---------------------------------------------------------

    async def test_never_an_entry_the_dm_has_not_confirmed(self) -> None:
        await self.add("Stray Wolf", type="character", role="npc", status="proposed")
        self.assertNotIn("Stray Wolf", await self.names())

    async def test_never_a_player_character_or_the_party(self) -> None:
        await self.add("Kesh", type="character", role="player_character", played_by=11)
        party = await self.memory.ensure_party(GUILD_A, self.c, source="dm")
        names = await self.names()
        self.assertNotIn("Kesh", names)
        self.assertNotIn(party.name, names)

    async def test_never_a_place_an_item_or_an_idea(self) -> None:
        for kind in ("place", "item", "event", "concept"):
            await self.add(f"A {kind}", type=kind)
        self.assertEqual(await self.names(), ["Ash Cult", "Mira", "Ulfgar"])

    async def test_never_an_entry_whose_own_name_is_secret(self) -> None:
        await self.memory.add_alias(
            GUILD_A, self.c, self.mira, "Mira", kind="full", source="dm", status="confirmed",
            secret=True,
        )  # fmt: skip
        self.assertNotIn("Mira", await self.names())

    async def test_never_a_secret_other_name_nor_one_that_is_only_a_mishearing(self) -> None:
        await self.memory.add_alias(
            GUILD_A, self.c, self.mira, "The Hooded Stranger", kind="nickname", source="dm",
            status="confirmed", secret=True,
        )  # fmt: skip
        await self.memory.add_alias(
            GUILD_A, self.c, self.mira, "Meera", kind="misheard", source="dm", status="confirmed"
        )
        await self.memory.add_alias(
            GUILD_A, self.c, self.mira, "Mirra", kind="nickname", source="dm", status="proposed"
        )
        mira = next(e for e in await self.roster() if e.name == "Mira")
        self.assertEqual(mira.also_called, ())

    async def test_never_a_secret_tie_or_one_that_has_ended(self) -> None:
        await self.tie(self.mira, "ally_of", self.ulfgar, secret=True)
        await self.tie(self.mira, "member_of", self.cult, from_session_at=1, to_session_at=2)
        mira = next(e for e in await self.roster() if e.name == "Mira")
        self.assertEqual((mira.ties, mira.groups), ((), ()))

    async def test_never_a_tie_nobody_confirmed(self) -> None:
        await self.tie(self.mira, "ally_of", self.ulfgar, status="proposed")
        mira = next(e for e in await self.roster() if e.name == "Mira")
        self.assertEqual(mira.ties, ())

    async def test_never_a_tie_that_comes_from_the_story_plan(self) -> None:
        await self.tie(self.mira, "ally_of", self.ulfgar)
        with mock.patch.object(recall, "PLAN_SOURCES", frozenset({"dm"})):
            mira = next(e for e in await self.roster() if e.name == "Mira")
        self.assertEqual(mira.ties, ())

    async def test_never_a_tie_to_someone_the_list_does_not_show(self) -> None:
        secret = await self.add("The Mask", type="character", role="npc")
        await self.memory.add_alias(
            GUILD_A, self.c, secret, "The Mask", kind="full", source="dm", status="confirmed",
            secret=True,
        )  # fmt: skip
        await self.tie(self.mira, "ally_of", secret)
        await self.tie(self.mira, "member_of", secret)
        proposed = await self.add("Maybe", type="character", role="npc", status="proposed")
        await self.tie(self.mira, "enemy_of", proposed, status="confirmed")
        mira = next(e for e in await self.roster() if e.name == "Mira")
        self.assertEqual((mira.ties, mira.groups), ((), ()))
        self.assertNotIn("The Mask", await self.names())

    async def test_never_a_story_thread_of_any_kind(self) -> None:
        for kind in ("thread", "quest", "mystery", "hook", "goal", "promise"):
            await self.add(f"The {kind}", type=kind)
        self.assertEqual(await self.names(), ["Ash Cult", "Mira", "Ulfgar"])

    async def test_never_a_clue_only_some_know_nor_what_points_to_it(self) -> None:
        clue = await self.add("The Hidden Ledger", type="clue")
        await self.tie(self.mira, "knows", clue)
        await self.memory.set_audience_limited(GUILD_A, self.c, clue, True, source="dm")
        got = await self.roster()
        self.assertNotIn("The Hidden Ledger", [e.name for e in got])
        self.assertNotIn(
            "The Hidden Ledger", repr([(e.ties, e.groups, e.also_called) for e in got])
        )

    async def test_never_a_deed_nor_standing(self) -> None:
        kesh = await self.add("Kesh", type="character", role="player_character", played_by=11)
        await self.memory.add_deed(
            GUILD_A, self.c, kesh, "Saved the mill", [self.mira], "Helped them", source="dm"
        )
        got = await self.roster()
        self.assertEqual([e.name for e in got], ["Ash Cult", "Mira", "Ulfgar"])
        self.assertNotIn("mill", repr(got))
        self.assertNotIn("Kesh", repr(got))  # no tie to a player's character either

    async def test_never_the_dms_note_a_condition_or_a_description(self) -> None:
        await self.memory.set_condition(GUILD_A, self.c, self.mira, DEAD, source="dm")
        got = await self.roster()
        self.assertIn("Mira", [e.name for e in got])  # still listed: the table may not know yet
        self.assertNotIn("vow", repr(got))
        self.assertNotIn("dead", repr(got).lower())
        self.assertFalse(hasattr(got[0], "description"))
        self.assertNotIn("vow", nc.entry_text(next(e for e in got if e.name == "Mira")))

    async def test_never_a_second_copy_of_a_merged_entry(self) -> None:
        twin = await self.add("Mirra", type="character", role="npc")
        await self.memory.merge(GUILD_A, self.c, self.mira, twin, source="dm", dm_said_same=True)
        names = await self.names()
        self.assertEqual(names.count("Mira"), 1)
        self.assertNotIn("Mirra", names)

    # ---- changes show up -------------------------------------------------------------

    async def test_confirm_rename_and_undo_change_the_list(self) -> None:
        proposed = await self.add("Wren", type="character", role="npc", status="proposed")
        self.assertNotIn("Wren", await self.names())
        await self.memory.confirm_entity(GUILD_A, self.c, proposed, "character", source="dm")
        self.assertIn("Wren", await self.names())
        renamed = await self.memory.rename_entity(GUILD_A, self.c, proposed, "Wrenna", source="dm")
        self.assertNotIn("Wren", await self.names())
        self.assertIn("Wrenna", await self.names())
        assert renamed.batch is not None
        await self.memory.undo(GUILD_A, self.c, renamed.batch)
        self.assertIn("Wren", await self.names())

    async def test_adding_and_undoing_an_entry(self) -> None:
        written = await self.memory.add_entity(
            GUILD_A, self.c, name="Pip", type="character", role="npc", source="dm",
            status="confirmed",
        )  # fmt: skip
        self.assertIn("Pip", await self.names())
        assert written.batch is not None
        await self.memory.undo(GUILD_A, self.c, written.batch)
        self.assertNotIn("Pip", await self.names())

    # ---- isolation -------------------------------------------------------------------

    async def test_two_campaigns_never_mix(self) -> None:
        other = (await self.campaigns.create(GUILD_A, "Other table", DM)).id
        await self.add("Zorn", type="character", role="npc", campaign=other)
        await self.add("Zorn's Gang", type="faction", campaign=other)
        self.assertEqual(await self.names(campaign=other), ["Zorn's Gang", "Zorn"])
        self.assertNotIn("Zorn", await self.names())
        self.assertNotIn("Mira", await self.names(campaign=other))

    async def test_two_servers_never_mix(self) -> None:
        far = (await self.campaigns.create(GUILD_B, "Far away", DM)).id
        await self.memory.add_entity(
            GUILD_B, far, name="Yara", type="character", role="npc", source="dm",
            status="confirmed",
        )  # fmt: skip
        self.assertEqual(await self.names(campaign=far, guild=GUILD_B), ["Yara"])
        self.assertNotIn("Yara", await self.names())
        self.assertNotIn("Mira", await self.names(campaign=far, guild=GUILD_B))
        # A campaign asked for through another server is not found at all.
        with self.assertRaises(MemoryRuleError):
            await self.roster(campaign=far, guild=GUILD_A)


class Fake(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.store = CampaignStore(self.db)
        self.campaign = await self.store.create(GUILD, "Rime of the Frostmaiden", DM)
        me = MagicMock(spec=discord.Member)
        me.id = BOT
        me.guild_permissions = BOT_PERMS
        guild = MagicMock(spec=discord.Guild)
        guild.id = GUILD
        guild.me = me
        guild.get_role = lambda _id: None
        guild.get_member = lambda _id: None
        self.general = text_channel(GENERAL, "general", guild)
        self.new = text_channel(NEW, "dmb-npcs-rmfthfrstmdn", guild)
        self.new.pins = AsyncMock(return_value=[])
        self.rules = text_channel(RULES, "dmb-rules-rmfthfrstmdn", guild)
        self.screen = text_channel(SCREEN, "dmb-dm-screen-rime-of-the-frostmaiden", guild)
        guild.fetch_channel = AsyncMock(return_value=self.general)
        guild.create_text_channel = AsyncMock(return_value=self.new)
        guild.get_channel = lambda cid: {NEW: self.new, RULES: self.rules, SCREEN: self.screen}.get(
            cid
        )
        self.guild = guild

    def created(self) -> dict[Any, discord.PermissionOverwrite]:
        return self.guild.create_text_channel.call_args.kwargs["overwrites"]  # type: ignore[no-any-return]


class Setup(Fake):
    async def test_a_new_channel_is_named_saved_with_a_topic_and_a_pinned_card(self) -> None:
        channel = await nc.setup_npcs_channel(self.guild, self.campaign.id, self.store)
        assert channel is self.new
        call = self.guild.create_text_channel.call_args
        self.assertEqual(call.args[0], "dmb-npcs-rmfthfrstmdn")
        self.assertEqual(call.kwargs["topic"], messages.NPCS_TOPIC)
        saved = await self.store.get(GUILD, self.campaign.id)
        assert saved is not None and saved.npcs_channel_id == NEW
        self.assertIsNone(saved.rules_channel_id)  # each channel keeps its own id
        send: Any = self.new.send
        self.assertEqual(
            send.call_args.args[0],
            "📖 **What's this channel?**\n"
            "The people and groups DMbot remembers for **Rime of the Frostmaiden**, as your DM "
            "confirmed them. Only DMbot posts here, so you can read but not write. Nothing "
            "secret is shown. If something looks wrong, ask your DM.",
        )

    async def test_it_follows_the_visibility_when_made_and_nobody_posts(self) -> None:
        for visibility, seen in (("private", False), ("open", True)):
            await self.store.set_dm_screen_visibility(GUILD, self.campaign.id, visibility)
            self.guild.create_text_channel.reset_mock()
            await self.store.set_npcs_channel(GUILD, self.campaign.id, None)
            await nc.setup_npcs_channel(self.guild, self.campaign.id, self.store)
            everyone = next(ow for key, ow in self.created().items() if key.id == GUILD)
            self.assertEqual(bool(everyone.view_channel), seen, visibility)
            self.assertFalse(everyone.send_messages)  # read only
            self.assertIsNot(everyone.use_application_commands, False)  # slash commands work

    async def test_peekers_of_the_screen_see_it_in_peek_mode(self) -> None:
        await self.store.set_dm_screen_visibility(GUILD, self.campaign.id, "peek")
        peeker = MagicMock(spec=discord.Member, id=PLAYER)
        self.screen.overwrites = {
            peeker: discord.PermissionOverwrite(view_channel=True, send_messages=False)
        }
        await nc.setup_npcs_channel(self.guild, self.campaign.id, self.store, screen=self.screen)
        seen = next(ow for key, ow in self.created().items() if key.id == PLAYER)
        self.assertTrue(seen.view_channel)
        self.assertFalse(seen.send_messages)

    async def test_the_name_follows_the_clash_number(self) -> None:
        self.assertEqual(nc.npcs_channel_name(self.campaign), "dmb-npcs-rmfthfrstmdn")
        second = dataclasses.replace(self.campaign, channel_number=2)
        self.assertEqual(nc.npcs_channel_name(second), "dmb-npcs-2rmfthfrstmdn")

    async def test_a_channel_that_is_not_an_npc_one_is_never_edited(self) -> None:
        await self.store.set_npcs_channel(GUILD, self.campaign.id, GENERAL)
        await nc.setup_npcs_channel(self.guild, self.campaign.id, self.store)
        self.general.edit.assert_not_awaited()
        self.guild.create_text_channel.assert_awaited_once()

    async def test_a_channel_deleted_by_hand_is_made_again_at_the_next_start(self) -> None:
        await self.store.set_npcs_channel(GUILD, self.campaign.id, 12345)
        self.guild.fetch_channel = AsyncMock(
            side_effect=discord.NotFound(MagicMock(status=404), "gone")
        )
        await nc.setup_npcs_channel(self.guild, self.campaign.id, self.store)
        self.guild.create_text_channel.assert_awaited_once()
        saved = await self.store.get(GUILD, self.campaign.id)
        assert saved is not None and saved.npcs_channel_id == NEW

    async def test_the_screens_change_reaches_an_existing_npc_channel(self) -> None:
        await self.store.set_dm_screen_visibility(GUILD, self.campaign.id, "private")
        await self.store.set_npcs_channel(GUILD, self.campaign.id, NEW)
        self.guild.fetch_channel = AsyncMock(return_value=self.new)
        self.new.topic = messages.NPCS_TOPIC
        self.new.overwrites = {}
        campaign = await self.store.get(GUILD, self.campaign.id)
        assert campaign is not None
        await nc.follow_screen(self.guild, campaign, self.screen)
        self.new.edit.assert_awaited()
        everyone = next(
            ow
            for key, ow in self.new.edit.call_args.kwargs["overwrites"].items()
            if key.id == GUILD
        )
        self.assertFalse(everyone.view_channel)

    async def test_nothing_happens_for_a_campaign_with_no_npc_channel(self) -> None:
        await nc.follow_screen(self.guild, self.campaign, self.screen)
        self.new.edit.assert_not_awaited()
        self.assertIsNone(await nc.sync_campaign(self.guild, self.campaign, mock.Mock(), MemMap()))

    async def test_a_backup_never_holds_the_channel_id(self) -> None:
        await self.store.set_npcs_channel(GUILD, self.campaign.id, NEW)
        exported = await self.store.export(GUILD, self.campaign.id)
        self.assertNotIn(str(NEW), repr(exported))
        self.assertNotIn("npcs_channel", repr(exported))

    async def test_it_is_never_picked_as_the_dms_extra_table_chat(self) -> None:
        await self.store.set_npcs_channel(GUILD, self.campaign.id, NEW)
        self.assertIn(NEW, await self.store.special_channel_ids(GUILD))

    async def test_the_dm_screen_setup_hands_the_change_to_both_channels(self) -> None:
        from dmbot.dm_screen import rules_channel as rc
        from dmbot.dm_screen.channel import setup_dm_screen

        self.guild.create_text_channel = AsyncMock(return_value=self.screen)
        with (
            mock.patch.object(nc, "follow_screen", AsyncMock()) as npcs,
            mock.patch.object(rc, "follow_screen", AsyncMock()) as rules,
        ):
            await setup_dm_screen(self.guild, self.campaign.id, self.store)
        npcs.assert_awaited_once()
        rules.assert_awaited_once()


class FromRecall(Fake):
    async def test_the_channel_shows_what_recall_gives_and_asks_for_this_campaign_only(
        self,
    ) -> None:
        mine = await self.store.set_npcs_channel(GUILD, self.campaign.id, NEW)
        chan = Chan()
        shown = text_channel(NEW, "dmb-npcs-rmfthfrstmdn", self.guild)
        shown.history, shown.send = chan.history, chan.send
        self.guild.fetch_channel = AsyncMock(return_value=shown)
        service = MagicMock()
        service.roster = AsyncMock(return_value=[entry(1, "Mira"), entry(2, "Ulfgar")])
        done = await nc.sync_campaign(self.guild, mine, service, MemMap())
        assert done is not None
        service.roster.assert_awaited_once_with(GUILD, mine.id)
        self.assertEqual(done.sent, 2)
        self.assertEqual(len(chan.texts()), 2)

    async def test_a_pass_for_one_campaign_does_not_wait_for_the_rules_channels_pass(self) -> None:
        from dmbot.dm_screen import rules_channel as rc

        self.assertIsNot(nc.sync_lock("a" * 32), rc.sync_lock("a" * 32))
        self.assertIs(nc.sync_lock("a" * 32), nc.sync_lock("a" * 32))


class Peeking(Fake):
    """A peek opens the DM screen, the rules channel and the NPC list together; a hide closes all
    three; and the warning names all three."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        await self.store.set_dm_screen(GUILD, self.campaign.id, SCREEN)
        await self.store.set_rules_channel(GUILD, self.campaign.id, RULES)
        await self.store.set_npcs_channel(GUILD, self.campaign.id, NEW)
        await self.store.set_dm_screen_visibility(GUILD, self.campaign.id, "peek")
        found = {NEW: self.new, RULES: self.rules, SCREEN: self.screen}
        self.guild.fetch_channel = AsyncMock(side_effect=lambda cid: found[cid])
        for channel in (self.screen, self.rules, self.new):
            channel.set_permissions = AsyncMock()
        self.screen.send = AsyncMock()
        for channel in (self.screen, self.rules, self.new):
            channel.mention = f"<#{channel.id}>"
        self.member = MagicMock(spec=discord.Member)
        self.member.id = PLAYER
        self.member.display_name = "Pip"
        self.interaction = MagicMock()
        self.interaction.guild = self.guild
        self.interaction.user = self.member
        self.interaction.client.campaigns = self.store
        self.interaction.response.defer = AsyncMock()
        self.interaction.response.edit_message = AsyncMock()
        self.interaction.response.send_message = AsyncMock()
        self.interaction.response.is_done = MagicMock(return_value=True)  # (the press is deferred)
        self.interaction.followup.send = AsyncMock()
        self.interaction.edit_original_response = AsyncMock()

    def test_the_warning_names_every_channel_it_opens(self) -> None:
        both = messages.peek_warning("<#99>", "<#77>")
        self.assertIn("<#99>", both)
        self.assertIn("• House rules: <#99>", both)
        self.assertIn("• People and groups: <#77>", both)
        self.assertIn("DM screen", both)
        self.assertIn("<#77>", messages.peek_warning(None, "<#77>"))
        self.assertNotIn("<#99>", messages.peek_warning(None, "<#77>"))
        self.assertEqual(messages.peek_warning(None, None), messages.PEEK_WARNING)
        self.assertEqual(messages.peek_warning(None), messages.PEEK_WARNING)

    async def test_the_peek_button_shows_a_warning_naming_all_three(self) -> None:
        from dmbot.dm_screen.buttons import PeekButton

        await PeekButton(self.campaign.id).callback(self.interaction)
        said = self.interaction.followup.send.call_args.args[0]
        self.assertIn(self.rules.mention, said)
        self.assertIn(self.new.mention, said)

    async def test_yes_grants_the_peek_in_all_three(self) -> None:
        view = ConfirmPeek(self.campaign.id, self.interaction)
        await view.yes.callback(self.interaction)
        for channel in (self.screen, self.rules, self.new):
            channel.set_permissions.assert_awaited_once()
        granted = self.new.set_permissions.call_args.kwargs["overwrite"]
        self.assertTrue(granted.view_channel)
        self.assertFalse(granted.send_messages)

    async def test_a_failure_in_the_npc_list_does_not_undo_the_rest(self) -> None:
        self.new.set_permissions = AsyncMock(
            side_effect=discord.HTTPException(MagicMock(status=500), "down")
        )
        view = ConfirmPeek(self.campaign.id, self.interaction)
        await view.yes.callback(self.interaction)
        self.screen.set_permissions.assert_awaited_once()
        self.rules.set_permissions.assert_awaited_once()
        text = self.interaction.edit_original_response.call_args.kwargs["content"]
        self.assertEqual(text, messages.peek_done(self.screen.mention))

    async def test_hide_closes_all_three(self) -> None:
        overwrite = discord.PermissionOverwrite(view_channel=True, send_messages=False)
        self.screen.overwrites = {self.member: overwrite}
        self.guild.get_member = lambda _id: self.member
        await HideButton(self.campaign.id).callback(self.interaction)
        for channel in (self.screen, self.rules, self.new):
            channel.set_permissions.assert_awaited_once()
        self.assertIsNone(self.new.set_permissions.call_args.kwargs["overwrite"])
        self.assertIsNone(self.rules.set_permissions.call_args.kwargs["overwrite"])

    async def test_a_failed_hide_in_the_npc_list_tells_the_dm_screen_only(self) -> None:
        overwrite = discord.PermissionOverwrite(view_channel=True, send_messages=False)
        self.screen.overwrites = {self.member: overwrite}
        self.new.set_permissions = AsyncMock(
            side_effect=discord.HTTPException(MagicMock(status=500), "down")
        )
        await HideButton(self.campaign.id).callback(self.interaction)
        self.new.send.assert_not_awaited()  # nothing in the players' channel
        self.assertTrue(self.screen.send.await_count >= 1)

    async def test_every_press_is_deferred_before_any_channel_work(self) -> None:
        order: list[str] = []
        self.interaction.response.defer = AsyncMock(side_effect=lambda **_: order.append("defer"))
        real = self.guild.fetch_channel.side_effect

        def fetch(cid: int) -> Any:
            order.append("fetch")
            return real(cid)

        self.guild.fetch_channel = AsyncMock(side_effect=fetch)
        from dmbot.dm_screen.buttons import PeekButton

        await PeekButton(self.campaign.id).callback(self.interaction)
        self.assertEqual(order[0], "defer")
        order.clear()
        await ConfirmPeek(self.campaign.id, self.interaction).yes.callback(self.interaction)
        self.assertEqual(order[0], "defer")
        order.clear()
        self.screen.overwrites = {
            self.member: discord.PermissionOverwrite(view_channel=True, send_messages=False)
        }
        await HideButton(self.campaign.id).callback(self.interaction)
        self.assertEqual(order[0], "defer")

    async def test_a_channel_that_is_not_ours_is_never_touched_by_a_peek(self) -> None:
        await self.store.set_npcs_channel(GUILD, self.campaign.id, GENERAL)
        found = {GENERAL: self.general, RULES: self.rules, SCREEN: self.screen}
        self.guild.fetch_channel = AsyncMock(side_effect=lambda cid: found[cid])
        self.general.set_permissions = AsyncMock()
        view = ConfirmPeek(self.campaign.id, self.interaction)
        await view.yes.callback(self.interaction)
        self.general.set_permissions.assert_not_awaited()


class MessageTable(DatabaseTest):
    """The message map in the database: scoped to one campaign of one server."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.store = CampaignStore(self.db)
        self.a = await self.store.create(GUILD, "Table A", DM)
        self.b = await self.store.create(GUILD, "Table B", DM)
        self.far = await self.store.create(222, "Far away", DM)
        self.map = nc.NpcMessageStore(self.db)

    async def test_save_load_replace_and_forget(self) -> None:
        await self.map.save(GUILD, self.a.id, "e1", 11)
        await self.map.save(GUILD, self.a.id, "e2", 12)
        await self.map.save(GUILD, self.a.id, "e1", 13)  # the entry's message was posted again
        self.assertEqual(await self.map.load(GUILD, self.a.id), {"e1": 13, "e2": 12})
        await self.map.forget(GUILD, self.a.id, ["e1", "nope"])
        self.assertEqual(await self.map.load(GUILD, self.a.id), {"e2": 12})
        await self.map.forget(GUILD, self.a.id, [])

    async def test_two_campaigns_and_two_servers_never_mix(self) -> None:
        await self.map.save(GUILD, self.a.id, "e1", 11)
        await self.map.save(GUILD, self.b.id, "e1", 21)
        await self.map.save(222, self.far.id, "e1", 31)
        self.assertEqual(await self.map.load(GUILD, self.a.id), {"e1": 11})
        self.assertEqual(await self.map.load(GUILD, self.b.id), {"e1": 21})
        self.assertEqual(await self.map.load(222, self.far.id), {"e1": 31})
        self.assertEqual(await self.map.load(GUILD, self.far.id), {})  # wrong server
        await self.map.forget(GUILD, self.a.id, ["e1"])
        self.assertEqual(await self.map.load(GUILD, self.b.id), {"e1": 21})
        with self.assertRaises(psycopg.Error):  # a campaign of another server is refused
            await self.map.save(GUILD, self.far.id, "e1", 1)

    async def test_it_goes_with_the_campaign_and_is_not_in_a_backup(self) -> None:
        await self.map.save(GUILD, self.a.id, "e1", 11)
        self.assertNotIn("npcs_channel_messages", repr(await self.store.export(GUILD, self.a.id)))
        await self.store.delete(GUILD, self.a.id)
        self.assertEqual(await self.map.load(GUILD, self.a.id), {})


class Passes(unittest.IsolatedAsyncioTestCase):
    """The bot's refresh: bursts become one pass, and a campaign is not looked at more than once
    a minute."""

    async def test_a_burst_of_changes_is_one_pass(self) -> None:
        from dmbot.bot import DMBot

        bot = MagicMock(spec=DMBot)
        bot._npcs_sync, bot._npcs_dirty, bot._npcs_last, bot._npcs_guilds = {}, set(), {}, {"c": 1}
        tasks: list[Any] = []

        def track(work: Any, name: str) -> Any:
            work.close()
            task = MagicMock()
            task.done.return_value = False
            tasks.append(task)
            return task

        bot._track = track
        for _ in range(5):
            DMBot._npcs_changed(bot, "c")
        self.assertEqual(len(tasks), 1)  # one pass is scheduled...
        self.assertEqual(bot._npcs_dirty, {"c"})  # ...which will look again once

    async def test_a_campaign_with_no_list_is_ignored(self) -> None:
        from dmbot.bot import DMBot

        bot = MagicMock(spec=DMBot)
        bot._npcs_sync, bot._npcs_dirty, bot._npcs_last, bot._npcs_guilds = {}, set(), {}, {}
        bot._track = MagicMock()
        DMBot._npcs_changed(bot, "unknown")
        bot._track.assert_not_called()

    async def test_passes_are_a_minute_apart(self) -> None:
        from dmbot.bot import DMBot

        bot = MagicMock(spec=DMBot)
        bot._npcs_dirty, bot._npcs_guilds = set(), {"c": 1}
        bot._npcs_sync = {"c": MagicMock()}
        bot._npcs_last = {"c": 1000.0}
        slept: list[float] = []
        results = [mock.Mock(left_for_next_time=0)]

        async def fake_sleep(seconds: float) -> None:
            slept.append(seconds)

        async def fake_sync(_campaign: str) -> Any:
            bot._npcs_dirty.add("c")  # another change arrives during the pass
            return results[0] if len(slept) == 1 else mock.Mock(left_for_next_time=0)

        bot._sync_npcs = fake_sync
        with (
            mock.patch("dmbot.bot.asyncio.sleep", fake_sleep),
            mock.patch(
                "dmbot.bot.time.monotonic",
                side_effect=[1010.0, 1010.0, 1011.0, 1011.0] + [1100.0] * 20,
            ),
            mock.patch("dmbot.bot.NPCS_PASSES", 2),
        ):
            await DMBot._npcs_run(bot, "c")
        self.assertEqual(len(slept), 2)
        self.assertAlmostEqual(slept[0], 50.0)  # ten seconds after the last pass: wait 50 more
        self.assertGreaterEqual(slept[1], 3.0)
        self.assertEqual(bot._npcs_sync, {})  # cleaned up
        _ = asyncio

    async def test_only_changes_to_names_and_facts_refresh_the_list(self) -> None:
        from dmbot.bot import DMBot

        bot = MagicMock(spec=DMBot)
        told: list[str] = []
        bot._npcs_changed = told.append

        async def stream() -> Any:
            yield f"{'a' * 32}:5:0"  # a mention, a flag: not on the list
            yield f"{'b' * 32}:6:1"
            raise asyncio.CancelledError

        bot.memory = MagicMock()
        bot.memory.listen = lambda _channel: stream()
        with self.assertRaises(asyncio.CancelledError):
            await DMBot._follow_npcs(bot)
        self.assertEqual(told, ["b" * 32])


if __name__ == "__main__":
    unittest.main()
