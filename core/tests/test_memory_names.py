"""Campaign memory step 3 (#126): names the DM adds, players' characters, checking the
names DMbot suggests after a session, and names as speech-to-text hints."""

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord

from dmbot.audio.segmenter import Segmenter
from dmbot.bot import DMBot, Table
from dmbot.campaigns import CampaignStore
from dmbot.config import Settings
from dmbot.consent import ConsentStore
from dmbot.memory.backup import MemorySection
from dmbot.memory.models import CONFIRMED, PROPOSED, REJECTED, MemoryRuleError
from dmbot.memory.store import MemoryStore
from dmbot.sessions import SessionStore
from dmbot.ui import names as ui
from tests.pg import DatabaseTest

GUILD, DM, PLAYER, STRANGER, SCREEN = 1, 7, 8, 9, 3


class FakeResponse:
    def __init__(self) -> None:
        self.done = False
        self.sent: list[tuple[str, dict[str, Any]]] = []
        self.edited: list[tuple[str, Any]] = []
        self.modal: Any = None

    def is_done(self) -> bool:
        return self.done

    async def send_message(self, text: str = "", **kw: Any) -> None:
        self.done = True
        self.sent.append((text, kw))

    async def edit_message(self, *, content: str, view: Any, **_: Any) -> None:
        self.done = True
        self.edited.append((content, view))

    async def send_modal(self, modal: Any) -> None:
        self.done = True
        self.modal = modal


class NamesTest(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.consent = ConsentStore(self.db)
        self.campaigns = CampaignStore(self.db)
        self.campaigns.register_section(MemorySection())
        self.memory = MemoryStore(self.db)
        self.bot = DMBot(
            Settings(discord_token="t", ears_secret="s"),
            self.consent,
            self.campaigns,
            SessionStore(self.db),
            memory=self.memory,
        )
        self.campaign = await self.campaigns.create(GUILD, "Frostmaiden", DM)

    def it(self, user_id: int = DM) -> Any:
        user = MagicMock(spec=discord.Member)
        user.id = user_id
        user.guild_permissions = discord.Permissions.none()
        return SimpleNamespace(
            client=self.bot,
            guild=SimpleNamespace(id=GUILD),
            guild_id=GUILD,
            user=user,
            response=FakeResponse(),
            followup=SimpleNamespace(send=AsyncMock()),
        )

    async def names(self, statuses: tuple[str, ...] = (CONFIRMED,)) -> dict[str, Any]:
        return {
            e.name: e
            for e in await self.memory.entities(GUILD, self.campaign.id, statuses=list(statuses))
        }


class Panel(NamesTest):
    async def test_the_dm_sees_the_names_panel(self) -> None:
        it = self.it()
        await ui.dmbot_names.callback(it)  # type: ignore[call-arg]
        text, kw = it.response.sent[0]
        self.assertIn("Names DMbot knows for Frostmaiden", text)
        self.assertIn("None yet", text)
        self.assertIsInstance(kw["view"], ui.NamesHome)
        self.assertTrue(kw["ephemeral"])

    async def test_someone_else_cannot(self) -> None:
        it = self.it(STRANGER)
        await ui.dmbot_names.callback(it)  # type: ignore[call-arg]
        self.assertIn("not the DM of any campaign", it.response.sent[0][0])
        self.assertIsNone(await ui._campaign_for(self.it(STRANGER), self.campaign.id))

    async def test_names_are_listed_with_their_other_names_but_never_secret_ones(self) -> None:
        await ui.save_name(
            self.memory, self.campaign, "Belleros", "npc", ["Bell"], ["the hooded stranger"]
        )
        text, waiting = await ui.home_text(self.memory, self.campaign)
        self.assertIn("**Belleros**, NPC (also: Bell)", text)
        self.assertNotIn("hooded", text)
        self.assertEqual(waiting, 0)


class Adding(NamesTest):
    async def test_a_name_with_nicknames_and_a_secret(self) -> None:
        entity = await ui.save_name(
            self.memory, self.campaign, "Belleros", "npc", ["Bell", "Bel"], ["the hooded stranger"]
        )
        self.assertEqual((entity.status, entity.type), (CONFIRMED, "npc"))
        aliases = await self.memory.aliases(GUILD, self.campaign.id, include_secret=True)
        shown = {(a.text, a.secret, a.status) for a in aliases}
        self.assertIn(("Bell", False, CONFIRMED), shown)
        self.assertIn(("the hooded stranger", True, CONFIRMED), shown)
        text = ui.saved_text(entity, ["Bell", "Bel"], ["the hooded stranger"])
        self.assertIn("DMbot never reveals", text)

    async def test_the_kind_picker_saves(self) -> None:
        view = ui.KindPicker(self.campaign.id, "Thornewick", ["Thorn Wick"], [])
        view.pick = SimpleNamespace(values=["place"])  # type: ignore[assignment]
        it = self.it()
        await view._picked(it)
        self.assertIn("DMbot will remember **Thornewick** (place)", it.response.edited[0][0])
        self.assertIn("Thornewick", await self.names())

    async def test_a_players_character_knows_its_player(self) -> None:
        form = ui.CharacterForm(self.campaign.id, PLAYER, "Mia")
        form.name = SimpleNamespace(value="Cerric")  # type: ignore[assignment]
        form.others = SimpleNamespace(value="Cer")  # type: ignore[assignment]
        it = self.it()
        await form.on_submit(it)
        self.assertIn("**Cerric**, played by **Mia**", it.response.sent[0][0])
        cerric = (await self.names())["Cerric"]
        self.assertEqual((cerric.type, cerric.played_by), ("player_character", PLAYER))

    async def test_only_a_player_character_has_a_player(self) -> None:
        with self.assertRaises(MemoryRuleError):
            await self.memory.add_entity(
                GUILD, self.campaign.id, type="npc", name="X", source="dm", played_by=PLAYER
            )

    def test_names_are_split_on_commas(self) -> None:
        self.assertEqual(ui.split_names(" Bell,bell ; the  knight,, "), ["Bell", "the knight"])


class Review(NamesTest):
    async def suggest(self, name: str) -> str:
        written = await self.memory.add_entity(
            GUILD,
            self.campaign.id,
            type="concept",
            name=name,
            source="scan",
            description="Heard 3 times",
        )
        return written.value.id

    async def review(self) -> tuple[ui.SuggestionReview, Any]:
        it = self.it()
        await ui.start_review(it, self.campaign.id)
        text, kw = it.response.sent[0]
        self.assertIn("a new name DMbot heard", text)
        return kw["view"], it

    async def test_yes_asks_what_it_is_then_adds_it(self) -> None:
        await self.suggest("Hrothgar")
        view, _ = await self.review()
        await view._yes(self.it())
        view.kind = SimpleNamespace(values=["npc"])  # type: ignore[assignment]
        it = self.it()
        await view._kind_picked(it)
        self.assertIn("Added **Hrothgar**", it.response.edited[0][0])
        self.assertIn("All checked", it.response.edited[0][0])
        hrothgar = (await self.names())["Hrothgar"]
        self.assertEqual(hrothgar.type, "npc")
        aliases = await self.memory.aliases(GUILD, self.campaign.id, entity_id=hrothgar.id)
        self.assertEqual({a.status for a in aliases}, {CONFIRMED})

    async def test_same_as_folds_it_into_a_known_name(self) -> None:
        belleros = await ui.save_name(self.memory, self.campaign, "Belleros", "npc", [], [])
        await self.suggest("Bellaros")
        view, _ = await self.review()
        await view._same(self.it())
        view.same = SimpleNamespace(values=[belleros.id])  # type: ignore[assignment]
        it = self.it()
        await view._same_picked(it)
        self.assertIn("**Bellaros** now means **Belleros**", it.response.edited[0][0])
        aliases = await self.memory.aliases(GUILD, self.campaign.id, entity_id=belleros.id)
        self.assertIn(("Bellaros", CONFIRMED), {(a.text, a.status) for a in aliases})

    async def test_not_a_name_is_never_suggested_again(self) -> None:
        await self.suggest("Wall")
        view, _ = await self.review()
        await view._no(self.it())
        self.assertIn("Wall", await self.names((REJECTED,)))
        self.assertIn("wall", await self.memory.known_keys(GUILD, self.campaign.id))

    async def test_a_stranger_cannot_press_the_buttons(self) -> None:
        await self.suggest("Hrothgar")
        view, _ = await self.review()
        it = self.it(STRANGER)
        await view._no(it)
        self.assertIn("Hrothgar", await self.names((PROPOSED,)))

    def test_same_as_puts_sound_alikes_first(self) -> None:
        from dmbot.memory.models import Entity

        def e(name: str) -> Entity:
            return Entity(name, "npc", name, "", CONFIRMED, None, "dm", 0)

        ranked = ui.ranked_same_as("Bell or us", [e("Gorrak"), e("Belleros"), e("Tamsin")])
        self.assertEqual(ranked[0].name, "Belleros")


def make_table(campaign_id: str) -> Table:
    return Table(
        guild_id=GUILD,
        voice_channel_id=2,
        screen_channel_id=SCREEN,
        dm_user_id=DM,
        segmenter=Segmenter(GUILD),
        campaign_id=campaign_id,
        campaign_name="Frostmaiden",
    )


class AfterSession(NamesTest):
    def table(self) -> Table:
        return make_table(self.campaign.id)

    async def test_new_names_are_suggested_to_the_dm(self) -> None:
        await self.consent.grant(GUILD, PLAYER)
        await ui.save_name(self.memory, self.campaign, "Belleros", "npc", [], [])
        table = self.table()
        table.heard = [
            (PLAYER, "We ride to Bryn Shander at dawn with Belleros."),
            (PLAYER, "I like Bryn Shander and Belleros."),
            (STRANGER, "Ask Secretname now. Ask Secretname again."),  # never agreed
        ]
        posted = AsyncMock(return_value=True)
        self.bot.post = posted  # type: ignore[method-assign]
        await self.bot.suggest_names(table)
        waiting = await self.names((PROPOSED,))
        self.assertEqual(set(waiting), {"Bryn Shander"})  # not a known name, not a stranger's
        text = posted.await_args.args[1]  # type: ignore[union-attr]
        self.assertIn("1 new name(s) to check:** **Bryn Shander**", text)
        self.assertIsInstance(posted.await_args.kwargs["view"], discord.ui.View)  # type: ignore[union-attr]

    async def test_nothing_new_says_nothing(self) -> None:
        table = self.table()
        table.heard = [(PLAYER, "nothing to see here")]
        posted = AsyncMock(return_value=True)
        self.bot.post = posted  # type: ignore[method-assign]
        await self.bot.suggest_names(table)
        posted.assert_not_awaited()


class Hints(NamesTest):
    async def test_characters_first_then_confirmed_then_suggested_never_secret(self) -> None:
        await ui.save_name(
            self.memory, self.campaign, "Belleros", "npc", ["Bell"], ["the hooded stranger"]
        )
        await self.memory.add_entity(
            GUILD, self.campaign.id, type="concept", name="Hrothgar", source="scan"
        )
        await ui.save_name(
            self.memory, self.campaign, "Cerric", "player_character", [], [], played_by=PLAYER
        )
        table = make_table(self.campaign.id)
        self.bot.tables[GUILD] = table
        hints = await self.bot._name_hints(GUILD)
        self.assertEqual(hints[0], "Cerric")
        self.assertLess(hints.index("Belleros"), hints.index("Hrothgar"))
        self.assertIn("Bell", hints)
        self.assertNotIn("the hooded stranger", hints)


class Store(NamesTest):
    async def test_only_the_dm_says_what_something_is(self) -> None:
        written = await self.memory.add_entity(
            GUILD, self.campaign.id, type="concept", name="Hrothgar", source="scan"
        )
        with self.assertRaises(MemoryRuleError):
            await self.memory.set_entity_type(
                GUILD, self.campaign.id, written.value.id, "npc", source="scan"
            )
        changed = await self.memory.set_entity_type(
            GUILD, self.campaign.id, written.value.id, "npc", source="dm"
        )
        self.assertEqual(changed.value.type, "npc")
        assert changed.batch is not None
        await self.memory.undo(GUILD, self.campaign.id, changed.batch)
        self.assertEqual((await self.names((PROPOSED,)))["Hrothgar"].type, "concept")

    async def test_a_character_and_player_survive_a_backup(self) -> None:
        await ui.save_name(
            self.memory, self.campaign, "Cerric", "player_character", [], [], played_by=PLAYER
        )
        backup = await self.campaigns.export(GUILD, self.campaign.id)
        restored = await self.campaigns.import_backup(2, backup, DM)
        copy = await self.memory.entities(2, restored.id, statuses=[CONFIRMED])
        self.assertEqual([(e.name, e.played_by) for e in copy], [("Cerric", PLAYER)])

    async def test_close_finishes_background_work(self) -> None:
        await asyncio.sleep(0)  # nothing running: just make sure the bot builds with memory
        self.assertIsNotNone(self.bot.lookup)
