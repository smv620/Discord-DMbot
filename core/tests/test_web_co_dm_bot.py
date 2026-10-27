"""The bot's side of a game master added or removed on the website (#2776, #2768): applied by the
rules of Discord's 👑 Game masters (the store's own, and its words), checked again at the moment of
use (the asker still a DM, the person still in the server and not a bot), access following, and the
one private message to a new game master. Invented names throughout."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, patch

import discord

from dmbot.bot import DMBot
from dmbot.campaigns import Campaign
from dmbot.campaigns.store import LAST_DM, MAX_DMS, TOO_MANY_DMS
from dmbot.dm_screen import game_masters
from dmbot.dm_screen import web_settings as ws
from tests.test_recap_web import BOB
from tests.test_web_api import ALICE
from tests.test_web_settings import FakeBot, Settings

DANA, ELI, BOT_ID, GONE_ID = 3003, 4004, 5005, 6006


class CoDmBot(FakeBot):
    """The bot as `apply` sees it. The two game-master steps are the bot's own methods
    (`DMBot.find_member`, `DMBot.apply_co_dm`), driven on a stand-in, so these tests cannot drift
    from them; Discord's side (the server's people, the private message, the access follow-up) is
    recorded."""

    def __init__(self, store: Any, guild_id: int) -> None:
        super().__init__(store)
        self.people: dict[int, ws.Member] = {
            ALICE.id: ws.Member("Alice", False),
            BOB.id: ws.Member("Bob", False),
            DANA: ws.Member("Dana", False),
            ELI: ws.Member("Eli", False),
            BOT_ID: ws.Member("Helper", True),
        }
        self.lookup_fails = False
        self.told: list[int] = []
        self.tell_works = True
        self.late = ""
        self.followed: list[frozenset[int]] = []

        async def fetch(user_id: int) -> Any:
            if self.lookup_fails:
                raise discord.HTTPException(SimpleNamespace(status=500, reason="x"), "boom")
            found = self.people.get(user_id)
            if found is None:
                raise discord.NotFound(SimpleNamespace(status=404, reason="x"), "gone")
            return SimpleNamespace(display_name=found.name, bot=found.bot)

        guild = SimpleNamespace(
            id=guild_id,
            name="Thursday",
            get_member=lambda uid: None,
            fetch_member=fetch,
        )
        self._me: Any = SimpleNamespace(campaigns=store, get_guild=lambda gid: guild)

    async def find_member(self, guild_id: int, user_id: int) -> ws.Member | None:
        return await DMBot.find_member(self._me, guild_id, user_id)

    async def apply_co_dm(
        self, campaign: Campaign, user_id: int, name: str, add: bool, asker_id: int
    ) -> ws.CoDmDone:
        self.calls.append(("co_dm", f"{user_id}:{add}"))

        async def follow(client: Any, guild: Any, changed: Campaign) -> str:
            self.followed.append(changed.dm_user_ids)
            return self.late

        async def tell(guild: Any, changed: Campaign, user_id: int, by: str) -> bool:
            self.told.append(user_id)
            return self.tell_works

        with (
            patch.object(game_masters, "follow_access_on", follow),
            patch.object(game_masters, "tell_new", tell),
        ):
            return await DMBot.apply_co_dm(self._me, campaign, user_id, name, add, asker_id)


class TheCoDm(Settings):
    bot: CoDmBot

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.bot = CoDmBot(self.campaigns, self.g)

    async def dms(self) -> frozenset[int]:
        return (await self.campaign()).dm_user_ids

    async def change(
        self, person: int | str, add: bool | None, asker: int = ALICE.id
    ) -> ws.Outcome:
        item = ws.Setting("a", self.one.id, asker, "co_dm", str(person), False, 1, add)
        return await ws.apply(self.bot, self.g, item)

    async def test_an_add_saves_follows_and_tells_the_new_game_master_once(self) -> None:
        done = await self.change(DANA, True)
        self.assertEqual((done.error, done.note), ("", "**Dana** is now a game master."))
        self.assertEqual(await self.dms(), {ALICE.id, DANA})
        self.assertEqual(self.bot.followed, [frozenset({ALICE.id, DANA})])
        self.assertEqual(self.bot.told, [DANA])

    async def test_a_removal_saves_and_follows_without_a_message(self) -> None:
        await self.campaigns.add_dm(self.g, self.one.id, DANA)
        done = await self.change(DANA, False)
        self.assertEqual((done.error, done.note), ("", "**Dana** is no longer a game master."))
        self.assertEqual(await self.dms(), {ALICE.id})
        self.assertEqual(self.bot.followed, [frozenset({ALICE.id})])
        self.assertEqual(self.bot.told, [])

    async def test_a_game_master_can_remove_themselves(self) -> None:
        await self.campaigns.add_dm(self.g, self.one.id, DANA)
        done = await self.change(DANA, False, asker=DANA)
        self.assertEqual(done.error, "")
        self.assertEqual(await self.dms(), {ALICE.id})

    async def test_the_new_game_master_not_reachable_is_said_and_the_change_stays(self) -> None:
        self.bot.tell_works = False
        done = await self.change(DANA, True)
        self.assertEqual(done.error, "")
        self.assertEqual(
            done.note,
            "**Dana** is now a game master. " + game_masters.NOT_TOLD.strip(),
        )
        self.assertEqual(await self.dms(), {ALICE.id, DANA})

    async def test_access_not_updated_yet_is_said_and_the_change_stays(self) -> None:
        self.bot.late = game_masters.ACCESS_LATE
        done = await self.change(DANA, True)
        self.assertEqual(done.error, "")
        self.assertIn("couldn't update who can see the DM screen yet", done.note)
        self.assertNotIn("\n", done.note)
        self.assertEqual(await self.dms(), {ALICE.id, DANA})

    async def test_already_a_game_master_is_refused_in_discords_words(self) -> None:
        await self.campaigns.add_dm(self.g, self.one.id, DANA)
        done = await self.change(DANA, True)
        self.assertEqual(
            (done.error, done.note), (ws.REFUSED, "**Dana** is already a game master.")
        )
        self.assertEqual(self.bot.told, [])

    async def test_a_bot_is_refused(self) -> None:
        done = await self.change(BOT_ID, True)
        self.assertEqual((done.error, done.note), (ws.REFUSED, game_masters.NOT_A_PERSON))
        self.assertEqual(await self.dms(), {ALICE.id})

    async def test_a_person_who_left_the_server_is_refused(self) -> None:
        done = await self.change(GONE_ID, True)
        self.assertEqual((done.error, done.note), (ws.REFUSED, game_masters.NOT_IN_SERVER))
        self.assertEqual(await self.dms(), {ALICE.id})

    async def test_a_lookup_that_fails_is_a_failed_try_never_a_person_who_left(self) -> None:
        self.bot.lookup_fails = True
        with self.assertRaises(discord.HTTPException):
            await self.change(DANA, True)
        self.assertEqual(await self.dms(), {ALICE.id})

    async def test_a_removal_still_works_when_the_name_cannot_be_looked_up(self) -> None:
        await self.campaigns.add_dm(self.g, self.one.id, DANA)
        self.bot.lookup_fails = True
        done = await self.change(DANA, False)
        self.assertEqual(done.error, "")
        self.assertEqual(await self.dms(), {ALICE.id})

    async def test_a_game_master_who_left_the_server_can_still_be_removed(self) -> None:
        await self.campaigns.add_dm(self.g, self.one.id, GONE_ID)
        done = await self.change(GONE_ID, False)
        self.assertEqual(
            (done.error, done.note), ("", f"**{game_masters.LEFT}** is no longer a game master.")
        )

    async def test_the_limit_is_the_stores_words(self) -> None:
        for extra in (BOB.id, DANA, 7001, 7002):
            await self.campaigns.add_dm(self.g, self.one.id, extra)
        self.assertEqual(len(await self.dms()), MAX_DMS)
        done = await self.change(ELI, True)
        self.assertEqual((done.error, done.note), (ws.REFUSED, TOO_MANY_DMS))

    async def test_the_plan_holder_is_refused_in_the_voice_that_fits_who_asked(self) -> None:
        await self.campaigns.add_dm(self.g, self.one.id, DANA)
        other = await self.change(ALICE.id, False, asker=DANA)
        self.assertEqual(other.error, ws.REFUSED)
        self.assertEqual(other.note, game_masters.PLAN_HOLDER_OTHER.format(name="Alice"))
        own = await self.change(ALICE.id, False)
        self.assertEqual(own.note, game_masters.PLAN_HOLDER_SELF)
        self.assertEqual(await self.dms(), {ALICE.id, DANA})

    async def test_the_last_game_master_cannot_go(self) -> None:
        # Alice holds the plan, so give the plan to a campaign whose only DM is someone else.
        lone = await self.campaigns.create(self.g, "Lone", DANA)
        item = ws.Setting("b", lone.id, DANA, "co_dm", str(DANA), False, 1, False)
        done = await ws.apply(self.bot, self.g, item)
        self.assertEqual(done.error, ws.REFUSED)
        self.assertIn(done.note, (LAST_DM, game_masters.PLAN_HOLDER_SELF))

    async def test_removing_someone_who_is_not_a_game_master_says_so(self) -> None:
        done = await self.change(ELI, False)
        self.assertEqual((done.error, done.note), (ws.REFUSED, game_masters.GONE_FROM_LIST))

    async def test_an_asker_who_stopped_being_a_dm_is_not_allowed(self) -> None:
        done = await self.change(DANA, True, asker=ELI)
        self.assertEqual(done.error, ws.NOT_ALLOWED)
        self.assertEqual(await self.dms(), {ALICE.id})
        self.assertEqual(self.bot.calls, [])

    async def test_a_campaign_that_is_gone_is_not_found(self) -> None:
        item = ws.Setting("c", "nope", ALICE.id, "co_dm", str(DANA), False, 1, True)
        self.assertEqual((await ws.apply(self.bot, self.g, item)).error, ws.NOT_FOUND)

    async def test_an_id_that_is_not_a_person_or_a_missing_yes_no_is_a_bad_value(self) -> None:
        for value in ("abc", "", "-5", "0", "1" * 20, "١٢٣", "12 3"):
            self.assertEqual((await self.change(value, True)).error, ws.BAD_VALUE, value)
        self.assertEqual((await self.change(DANA, None)).error, ws.BAD_VALUE)
        self.assertEqual(self.bot.calls, [])

    async def test_a_double_add_after_a_half_done_try_changes_nothing_more(self) -> None:
        await self.change(DANA, True)
        again = await self.change(DANA, True)
        self.assertEqual(again.error, ws.REFUSED)
        self.assertEqual(self.bot.told, [DANA])


class TheFollowUp(Settings):
    """`follow_access_on` is what Discord's menu and the website both use after a change."""

    async def test_the_running_session_takes_the_new_set_and_the_screen_is_set_up_again(
        self,
    ) -> None:
        taken: list[Campaign] = []
        result = SimpleNamespace(campaign=self.one, channel=SimpleNamespace(id=9))
        client = SimpleNamespace(
            campaigns=self.campaigns,
            after_game_masters_change=taken.append,
            after_screen_change=AsyncMock(),
        )
        with patch.object(game_masters, "setup_dm_screen", AsyncMock(return_value=result)):
            words = await game_masters.follow_access_on(
                client, cast(Any, SimpleNamespace()), self.one
            )
        self.assertEqual(words, "")
        self.assertEqual(taken, [self.one])
        client.after_screen_change.assert_awaited_once()

    async def test_a_screen_that_cannot_be_updated_says_late_and_the_session_still_has_the_set(
        self,
    ) -> None:
        taken: list[Campaign] = []
        client = SimpleNamespace(campaigns=self.campaigns, after_game_masters_change=taken.append)
        with patch.object(game_masters, "setup_dm_screen", AsyncMock(side_effect=RuntimeError())):
            words = await game_masters.follow_access_on(
                client, cast(Any, SimpleNamespace()), self.one
            )
        self.assertEqual(words, game_masters.ACCESS_LATE)
        self.assertEqual(taken, [self.one])
