"""A map sent to DMbot by its owner (#4372): `/dmbot map` and a picture in a private chat.
Nothing is stored before the sender's yes (the bytes wait in memory, bounded); a no, a time-out or
another person's press keeps nothing. The real stores on a test database; Discord is a stand-in.
Invented ids. The memory rules are pure; the flows need Postgres."""

from __future__ import annotations

import io
import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import discord
from PIL import Image

from dmbot.campaigns import CampaignStore
from dmbot.maps import words
from dmbot.maps.pending import Busy, PendingMaps
from dmbot.maps.store import MapStore, Viewer, may_use_map
from dmbot.memory.store import MemoryStore
from dmbot.pictures.keeper import PictureKeeper
from dmbot.pictures.store import PictureStore
from dmbot.ui import map_send
from tests.pg import DatabaseTest

GUILD = 1
DM, PLAYER, STRANGER = 7, 8, 9


def png(colour: int = 0) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (8, 8), (colour, 0, 0)).save(out, "PNG")
    return out.getvalue()


class Clock:
    now = 1000.0

    def __call__(self) -> float:
        return self.now


class PendingRules(unittest.TestCase):
    def test_one_picture_a_person_and_a_new_one_replaces_the_old(self) -> None:
        held = PendingMaps(Clock())
        first = held.put(PLAYER, b"a" * 10)
        second = held.put(PLAYER, b"b" * 10)
        self.assertIsNone(held.get(PLAYER, first.code))
        self.assertIsNotNone(held.get(PLAYER, second.code))
        self.assertEqual(held.total(), 10)

    def test_only_the_owner_with_the_code_finds_it(self) -> None:
        held = PendingMaps(Clock())
        one = held.put(PLAYER, b"a")
        self.assertIsNone(held.get(STRANGER, one.code))
        self.assertIsNone(held.get(PLAYER, "0" * 12))
        self.assertFalse(held.drop(STRANGER, one.code))
        self.assertTrue(held.drop(PLAYER, one.code))
        self.assertIsNone(held.get(PLAYER, one.code))

    def test_it_is_dropped_after_fifteen_minutes(self) -> None:
        clock = Clock()
        held = PendingMaps(clock)
        one = held.put(PLAYER, b"a")
        clock.now += 15 * 60 - 1
        self.assertIsNotNone(held.get(PLAYER, one.code))
        clock.now += 2
        self.assertIsNone(held.get(PLAYER, one.code))
        self.assertEqual(held.total(), 0)

    def test_the_total_is_capped_and_a_replacement_does_not_count_twice(self) -> None:
        held = PendingMaps(Clock(), cap=100)
        held.put(PLAYER, b"a" * 60)
        with self.assertRaises(Busy):
            held.put(STRANGER, b"b" * 60)
        held.put(PLAYER, b"c" * 90)  # (their own old one goes, so this fits)
        self.assertEqual(held.total(), 90)

    def test_files_that_cannot_be_kept_are_refused_in_words(self) -> None:
        self.assertIsNone(map_send.check_picture("image/png", 1000))
        self.assertEqual(map_send.check_picture("application/pdf", 10), words.WRONG_TYPE)
        self.assertEqual(map_send.check_picture(None, 10), words.WRONG_TYPE)
        big = 26 * 1024 * 1024
        self.assertEqual(map_send.check_picture("image/png", big), words.too_big(25))


class Bucket:
    def __init__(self) -> None:
        self.puts: list[str] = []

    async def put(self, key: str, data: bytes) -> None:
        self.puts.append(key)

    async def delete(self, key: str) -> None: ...


class Sending(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.campaigns = CampaignStore(self.db)
        self.pictures = PictureStore(self.db, clock=lambda: 1000.0)
        self.memory = MemoryStore(self.db)
        self.maps = MapStore(self.db, self.memory, clock=lambda: 2000.0)
        self.bucket = Bucket()
        self.one = await self.campaigns.create(GUILD, "One", DM)
        self.plays: dict[str, set[int]] = {self.one.id: {PLAYER}}  # (who plays a character where)

        async def characters_of(guild: int, campaign: str, user: int) -> list[str]:
            return ["c"] if user in self.plays.get(campaign, set()) else []

        self.bot: Any = SimpleNamespace(
            picture_store=self.pictures,
            picture_keeper=PictureKeeper(self.pictures, self.bucket, 5),
            map_store=self.maps,
            memory=SimpleNamespace(characters_of=characters_of),
            campaigns=self.campaigns,
            settings=SimpleNamespace(picture_storage_mb=5),
            pending_maps=PendingMaps(),
            consent=SimpleNamespace(
                # (everyone has agreed to be recorded: that is not being in the campaign)
                has_consent=lambda guild, user: True,
                agreed_under=AsyncMock(return_value=None),
            ),
            get_guild=lambda gid: SimpleNamespace(name="The Server"),
        )

    def press(self, user: int, **extra: Any) -> Any:
        return SimpleNamespace(
            user=SimpleNamespace(id=user),
            client=self.bot,
            type=discord.InteractionType.component,
            response=SimpleNamespace(is_done=lambda: True, defer=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
            edit_original_response=AsyncMock(),
            **extra,
        )

    @staticmethod
    def shown(it: Any) -> str:
        last = it.edit_original_response.await_args
        assert last is not None
        return str(last.kwargs["content"])

    async def kept(self) -> list[Any]:
        return await self.maps.maps(GUILD, self.one.id, Viewer(dm=True))

    async def asked(self, user: int = PLAYER, data: bytes | None = None) -> tuple[str, Any, str]:
        text, view = await map_send.offer(self.bot, user, data or png(), "Mere", GUILD)
        code = self.bot.pending_maps._held[user].code
        return text, view, code

    async def test_one_campaign_asks_the_question_and_stores_nothing_yet(self) -> None:
        text, view, _ = await self.asked()
        self.assertEqual(text, words.question(from_chat=False))
        self.assertEqual([b.item.label for b in view.children], [words.YES_LABEL, words.NO_LABEL])
        self.assertEqual(self.bucket.puts, [])
        self.assertEqual(await self.kept(), [])

    async def test_a_players_yes_keeps_the_map_for_the_dms_and_it_waits(self) -> None:
        _, _, code = await self.asked(PLAYER)
        it = self.press(PLAYER)
        await map_send.YesButton(GUILD, self.one.id, code).callback(it)
        [kept] = await self.kept()
        self.assertEqual(
            (kept.right_by, kept.waiting, kept.audience, kept.title),
            (PLAYER, True, "dm", "Mere"),
        )
        self.assertFalse(may_use_map(kept))
        self.assertEqual(self.shown(it), words.said_yes(False, False))
        self.assertIsNone(self.bot.pending_maps.get(PLAYER, code))  # (nothing held any more)
        async with self.db.guild(GUILD) as conn:  # (no made-up chat-capture yes on the picture)
            cur = await conn.execute(
                "SELECT consent_version, right_granted_at FROM campaign_pictures"
            )
            self.assertEqual(
                [(r["consent_version"], r["right_granted_at"]) for r in await cur.fetchall()],
                [(None, None)],
            )

    async def test_a_dms_own_map_is_usable_at_once(self) -> None:
        _, _, code = await self.asked(DM)
        await map_send.YesButton(GUILD, self.one.id, code).callback(self.press(DM))
        [kept] = await self.kept()
        self.assertTrue(may_use_map(kept))

    async def test_a_no_keeps_nothing_and_drops_the_bytes(self) -> None:
        _, _, code = await self.asked()
        it = self.press(PLAYER)
        await map_send.NoButton(code).callback(it)
        self.assertEqual(self.shown(it), words.said_no(False))
        self.assertEqual(await self.kept(), [])
        self.assertEqual(self.bucket.puts, [])
        self.assertIsNone(self.bot.pending_maps.get(PLAYER, code))

    async def test_another_persons_press_finds_nothing_and_stores_nothing(self) -> None:
        _, _, code = await self.asked(PLAYER)
        it = self.press(STRANGER)
        await map_send.YesButton(GUILD, self.one.id, code).callback(it)
        self.assertEqual(self.shown(it), words.EXPIRED)
        self.assertEqual(await self.kept(), [])
        self.assertEqual(self.bucket.puts, [])
        self.assertIsNotNone(self.bot.pending_maps.get(PLAYER, code))  # (still theirs)

    async def test_after_the_time_out_nothing_is_stored(self) -> None:
        clock = Clock()
        self.bot.pending_maps = PendingMaps(clock)
        _, _, code = await self.asked()
        clock.now += 16 * 60
        it = self.press(PLAYER)
        await map_send.YesButton(GUILD, self.one.id, code).callback(it)
        self.assertEqual(self.shown(it), words.EXPIRED)
        self.assertEqual(self.bucket.puts, [])

    async def test_a_press_for_a_campaign_the_person_is_not_in_stores_nothing(self) -> None:
        held = self.bot.pending_maps.put(STRANGER, png(), "")  # (a card made up by hand)
        it = self.press(STRANGER)
        await map_send.YesButton(GUILD, self.one.id, held.code).callback(it)
        self.assertEqual(self.shown(it), words.EXPIRED)
        self.assertEqual(self.bucket.puts, [])
        self.assertEqual(await self.kept(), [])

    async def test_a_consenting_person_who_is_not_in_the_campaign_is_not_offered_it(self) -> None:
        # STRANGER agreed to be recorded in the server, but neither runs nor plays in any campaign
        text, view = await map_send.offer(self.bot, STRANGER, png(), "", GUILD)
        self.assertEqual((text, view), (words.NO_CAMPAIGN_HERE, None))
        self.assertEqual(self.bot.pending_maps.total(), 0)

    async def test_a_member_of_one_campaign_is_not_offered_the_other(self) -> None:
        two = await self.campaigns.create(GUILD, "Two", DM)  # (PLAYER plays only in One)
        text, _, code = await self.asked(PLAYER)
        self.assertEqual(text, words.question(from_chat=False))  # (no choice: only One)
        it = self.press(PLAYER)
        await map_send.YesButton(GUILD, two.id, code).callback(it)  # (a made-up press for Two)
        self.assertEqual(self.shown(it), words.EXPIRED)
        self.assertEqual(await self.maps.maps(GUILD, two.id, Viewer(dm=True)), [])

    async def test_two_quick_yes_presses_keep_one_map_only(self) -> None:
        import asyncio

        _, _, code = await self.asked(PLAYER)
        first, second = self.press(PLAYER), self.press(PLAYER)
        yes = map_send.YesButton(GUILD, self.one.id, code)
        await asyncio.gather(yes.callback(first), yes.callback(second))
        self.assertEqual(len(await self.kept()), 1)
        self.assertEqual(len(self.bucket.puts), 2)  # (the file and its preview, once)
        self.assertEqual(sorted([self.shown(first), self.shown(second)]).count(words.EXPIRED), 1)

    async def test_a_failed_save_puts_the_picture_back_and_the_question_stays(self) -> None:
        _, _, code = await self.asked(PLAYER)
        real = self.bot.picture_keeper.keep
        self.bot.picture_keeper.keep = AsyncMock(side_effect=RuntimeError("boom"))
        it = self.press(PLAYER)
        await map_send.YesButton(GUILD, self.one.id, code).callback(it)
        self.assertIn(words.FAILED, self.shown(it))
        self.assertIsNotNone(self.bot.pending_maps.get(PLAYER, code))
        self.bot.picture_keeper.keep = real
        await map_send.YesButton(GUILD, self.one.id, code).callback(self.press(PLAYER))
        self.assertEqual(len(await self.kept()), 1)

    async def test_no_campaign_says_so_and_holds_nothing(self) -> None:
        text, view = await map_send.offer(self.bot, STRANGER, png(), "", GUILD)
        self.assertEqual((text, view), (words.NO_CAMPAIGN_HERE, None))
        text, view = await map_send.offer(self.bot, STRANGER, png(), "", None)
        self.assertEqual((text, view), (words.NO_CAMPAIGN_PRIVATE, None))
        self.assertEqual(self.bot.pending_maps.total(), 0)

    async def test_a_file_that_is_not_a_picture_is_never_asked_about(self) -> None:
        text, view = await map_send.offer(self.bot, PLAYER, b"not a picture", "", GUILD)
        self.assertEqual((text, view), (words.WRONG_TYPE, None))
        self.assertEqual(self.bot.pending_maps.total(), 0)

    async def test_a_full_memory_says_busy(self) -> None:
        self.bot.pending_maps = PendingMaps(cap=10)
        text, view = await map_send.offer(self.bot, PLAYER, png(), "", GUILD)
        self.assertEqual((text, view), (words.BUSY, None))

    async def test_several_campaigns_ask_which_first_then_the_question(self) -> None:
        two = await self.campaigns.create(GUILD, "Two", DM)
        text, view, code = await self.asked(DM)
        self.assertEqual(text, words.WHICH_CAMPAIGN)
        self.assertEqual(
            sorted(b.item.label for b in view.children), sorted([self.one.name, two.name])
        )
        it = self.press(DM)
        await map_send.PickButton(GUILD, two.id, code).callback(it)
        self.assertEqual(self.shown(it), words.question(from_chat=False))
        self.assertEqual(self.bucket.puts, [])  # (still nothing stored)
        await map_send.YesButton(GUILD, two.id, code).callback(self.press(DM))
        self.assertEqual(await self.maps.maps(GUILD, self.one.id, Viewer(dm=True)), [])
        self.assertEqual(len(await self.maps.maps(GUILD, two.id, Viewer(dm=True))), 1)

    async def test_many_campaigns_use_a_menu_and_say_when_names_are_cut(self) -> None:
        for n in range(6):
            await self.campaigns.create(GUILD, f"A very long campaign name number {n}", DM)
        text, view, _ = await self.asked(DM)
        [menu] = view.children
        self.assertIsInstance(menu.item, discord.ui.Select)
        self.assertIn("A very long campaign name number", text)  # (the full names are listed)
        self.assertTrue(all(len(o.label) <= map_send.SHORT for o in menu.item.options))

    async def test_a_second_picture_replaces_the_first_one_held(self) -> None:
        _, _, first = await self.asked(PLAYER, png(1))
        _, _, second = await self.asked(PLAYER, png(2))
        it = self.press(PLAYER)
        await map_send.YesButton(GUILD, self.one.id, first).callback(it)
        self.assertEqual(self.shown(it), words.EXPIRED)  # (the old card is dead)
        self.assertEqual(await self.kept(), [])
        self.assertIsNotNone(self.bot.pending_maps.get(PLAYER, second))

    async def test_the_command_works_with_private_messages_closed(self) -> None:
        """It answers in place (a reply only they see), so no private message is sent at all."""
        sent: list[Any] = []

        async def tell(interaction: Any, text: str, *, view: Any = None) -> None:
            sent.append((text, view))

        picture = SimpleNamespace(
            content_type="image/png", size=100, read=AsyncMock(return_value=png())
        )
        it = SimpleNamespace(
            user=SimpleNamespace(id=PLAYER), client=self.bot, guild_id=GUILD,
            type=discord.InteractionType.application_command,
            response=SimpleNamespace(is_done=lambda: True, defer=AsyncMock()),
        )  # fmt: skip
        command: Any = map_send.dmbot_map
        with patch.object(map_send, "_tell", tell):
            await command.callback(it, picture, "Mere")
        [(text, view)] = sent
        self.assertEqual(text, words.question(from_chat=False))
        self.assertEqual(len(view.children), 2)


class PrivateChat(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def message(attachments: list[Any], content: str = "", guild: Any = None) -> Any:
        sent = AsyncMock()
        return SimpleNamespace(
            guild=guild, author=SimpleNamespace(bot=False, id=1), attachments=attachments,
            content=content, channel=SimpleNamespace(send=sent),
        )  # fmt: skip

    async def test_only_one_real_picture_with_no_words_is_taken(self) -> None:
        bot: Any = SimpleNamespace()
        png_file = SimpleNamespace(content_type="image/png", size=1)
        cases = [
            self.message([]),
            self.message([png_file, png_file]),  # (two files)
            self.message([SimpleNamespace(content_type="audio/ogg", size=1)]),
            self.message([SimpleNamespace(content_type="image/svg+xml", size=1)]),  # (not kept)
            self.message([png_file], content="what does this rule say?"),  # (a DM's question)
            self.message([png_file], guild=object()),  # (a server's message)
        ]
        for message in cases:
            self.assertFalse(await map_send.from_private_message(bot, message))
            message.channel.send.assert_not_awaited()

    async def test_a_picture_too_big_is_refused_in_words(self) -> None:
        bot: Any = SimpleNamespace()
        big = SimpleNamespace(content_type="image/png", size=26 * 1024 * 1024)
        message = self.message([big])
        self.assertTrue(await map_send.from_private_message(bot, message))
        self.assertEqual(message.channel.send.await_args.args[0], words.too_big(25))


class SidebarHook(unittest.IsolatedAsyncioTestCase):
    async def test_a_picture_taken_as_a_map_gets_no_other_note(self) -> None:
        from dmbot.sidebar.service import SidebarService

        taken = AsyncMock(return_value=True)
        host: Any = SimpleNamespace(take_private_map=taken)
        service = SidebarService(host)
        send = AsyncMock()
        message = SimpleNamespace(
            author=SimpleNamespace(bot=False, id=PLAYER),
            guild=None,
            flags=SimpleNamespace(voice=False),
            attachments=[SimpleNamespace(content_type="image/png", size=1)],
            content="",
            channel=SimpleNamespace(send=send),
        )
        dm: Any = message
        await service.on_dm_message(dm)
        taken.assert_awaited_once()
        send.assert_not_awaited()
