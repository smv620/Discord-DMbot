"""Campaign memory step 3 (#126): names the DM adds, players' characters, checking the
names DMbot suggests after a session, and names as speech-to-text hints."""

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
from dmbot.ui import logic
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

    async def test_names_are_listed_with_other_and_secret_names(self) -> None:
        # The panel is private to the DM, so it shows secrets (marked as secret).
        await ui.save_name(
            self.memory, self.campaign, "Belleros", "npc", ["Bell"], ["the hooded stranger"]
        )
        text, waiting = await ui.home_text(self.memory, self.campaign)
        self.assertIn("**Belleros**, NPC (also: Bell) 🤫 secret: the hooded stranger", text)
        self.assertEqual(waiting, 0)

    async def test_the_panel_fits_one_message(self) -> None:
        for i in range(40):
            others = [f"Nick{i}x{j}" for j in range(8)]
            await ui.save_name(
                self.memory, self.campaign, f"Name{i} " + "x" * 80, "npc", others, []
            )
        text, _ = await ui.home_text(self.memory, self.campaign)
        self.assertLessEqual(len(text), 2000)
        self.assertIn("more.", text)

    async def test_names_show_as_typed(self) -> None:
        await ui.save_name(self.memory, self.campaign, "*Star*", "npc", [], [])
        text, _ = await ui.home_text(self.memory, self.campaign)
        self.assertIn("**\\*Star\\***", text)


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
        self.assertIn("Kept secret: **the hooded stranger** is really **Belleros**", text)
        self.assertIn("never puts it in the transcript", text)

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

    async def test_a_name_it_already_knows_is_not_added_twice(self) -> None:
        await ui.save_name(self.memory, self.campaign, "Belleros", "npc", ["Bell"], [])
        form = ui.AddNameForm(self.campaign.id)
        form.name = SimpleNamespace(value="bell")  # type: ignore[assignment]
        form.others = SimpleNamespace(value="")  # type: ignore[assignment]
        form.secret = SimpleNamespace(value="")  # type: ignore[assignment]
        it = self.it()
        await form.on_submit(it)
        self.assertIn("already knows **bell**", it.response.sent[0][0])

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
        self.assertIn("Is this a name in your game?", text)
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
        self.assertIn("**Bellaros** is another name for **Belleros**", it.response.edited[0][0])
        aliases = await self.memory.aliases(GUILD, self.campaign.id, entity_id=belleros.id)
        self.assertIn(("Bellaros", CONFIRMED), {(a.text, a.status) for a in aliases})

    async def test_not_a_name_is_never_suggested_again(self) -> None:
        await self.suggest("Wall")
        view, _ = await self.review()
        await view._no(self.it())
        self.assertIn("Wall", await self.names((REJECTED,)))
        self.assertIn("wall", await self.memory.known_keys(GUILD, self.campaign.id))

    async def test_a_suggestion_can_be_a_players_character(self) -> None:
        await self.suggest("Cerric")
        view, _ = await self.review()
        await view._yes(self.it())
        view.kind = SimpleNamespace(values=[ui.PC])  # type: ignore[assignment]
        it = self.it()
        await view._kind_picked(it)
        self.assertIn("**Who plays Cerric?**", it.response.edited[0][0])
        player = SimpleNamespace(id=PLAYER, bot=False, display_name="Mia")
        it = self.it()
        await view._player_picked(it, player)  # type: ignore[arg-type]
        self.assertIn("played by **Mia**", it.response.edited[0][0])
        cerric = (await self.names())["Cerric"]
        self.assertEqual((cerric.type, cerric.played_by), (ui.PC, PLAYER))

    async def test_later_ends_the_round_and_keeps_it_waiting(self) -> None:
        await self.suggest("Hrothgar")
        view, _ = await self.review()
        it = self.it()
        await view._later(it)
        self.assertIn("1 name still waiting", it.response.edited[0][0])
        self.assertIsNone(it.response.edited[0][1])  # the menu is finished
        self.assertTrue(view.is_finished())
        self.assertIn("Hrothgar", await self.names((PROPOSED,)))

    async def test_a_stranger_cannot_press_any_button(self) -> None:
        await self.suggest("Hrothgar")
        known = await ui.save_name(self.memory, self.campaign, "Belleros", "npc", [], [])
        view, _ = await self.review()
        view.kind = SimpleNamespace(values=["npc"])  # type: ignore[assignment]
        view.same = SimpleNamespace(values=[known.id])  # type: ignore[assignment]
        for press in (view._yes, view._same, view._no, view._kind_picked, view._same_picked):
            it = self.it(STRANGER)
            await press(it)
            self.assertEqual(it.response.sent[0][0], logic.NO_CAMPAIGN_ACCESS, press.__name__)
            self.assertEqual(it.response.edited, [], press.__name__)
        self.assertIn("Hrothgar", await self.names((PROPOSED,)))

    async def test_the_button_after_a_session_opens_the_check(self) -> None:
        await self.suggest("Hrothgar")
        button = ui.ReviewButton(self.campaign.id)
        match = ui.ReviewButton.__discord_ui_compiled_template__.fullmatch(
            button.item.custom_id or ""
        )
        assert match is not None
        again = await ui.ReviewButton.from_custom_id(self.it(), button.item, match)
        self.assertEqual(again.campaign_id, self.campaign.id)
        it = self.it()
        await again.callback(it)
        self.assertIn("**Hrothgar**", it.response.sent[0][0])
        self.assertTrue(it.response.sent[0][1]["ephemeral"])
        stranger = self.it(STRANGER)
        await again.callback(stranger)
        self.assertNotIn("Hrothgar", stranger.response.sent[0][0])

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


def clip(table: Table) -> Any:
    """A piece of speech from this table's session, as the pipeline asks hints for it."""
    from dmbot.audio.segmenter import Utterance

    return Utterance(GUILD, PLAYER, 0, 0, bytes(32000), table.segmenter.session)


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
        self.assertIn("1 new name to check** from this session: **Bryn Shander**", text)
        self.assertIsInstance(posted.await_args.kwargs["view"], discord.ui.View)  # type: ignore[union-attr]

    async def test_nothing_from_someone_who_stopped(self) -> None:
        await self.consent.grant(GUILD, PLAYER)
        table = self.table()
        self.bot.tables[GUILD] = table
        table.heard = [(PLAYER, "We ride to Bryn Shander. I like Bryn Shander.")]
        self.bot.stop_recording(GUILD, PLAYER)
        self.assertEqual(table.heard, [])
        table.heard = [(PLAYER, "We ride to Bryn Shander. I like Bryn Shander.")]
        await self.consent.revoke(GUILD, PLAYER)  # e.g. stopped after the session ended
        posted = AsyncMock(return_value=True)
        self.bot.post = posted  # type: ignore[method-assign]
        await self.bot.suggest_names(table)
        self.assertEqual(await self.names((PROPOSED,)), {})
        posted.assert_not_awaited()

    async def test_a_name_the_dm_said_no_to_is_not_suggested_again(self) -> None:
        await self.consent.grant(GUILD, PLAYER)
        wall = await self.memory.add_entity(
            GUILD, self.campaign.id, type="concept", name="Wall", source="scan"
        )
        await self.memory.set_entity_status(
            GUILD, self.campaign.id, wall.value.id, REJECTED, source="dm"
        )
        table = self.table()
        table.heard = [(PLAYER, "Hit the Wall. Then the Wall again.")]
        posted = AsyncMock(return_value=True)
        self.bot.post = posted  # type: ignore[method-assign]
        await self.bot.suggest_names(table)
        self.assertEqual(await self.names((PROPOSED,)), {})
        posted.assert_not_awaited()

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
        hints = await self.bot._name_hints(clip(table))
        self.assertEqual(hints[0], "Cerric")
        self.assertLess(hints.index("Belleros"), hints.index("Hrothgar"))
        self.assertIn("Bell", hints)
        self.assertNotIn("the hooded stranger", hints)

    async def test_names_said_at_the_table_move_to_the_front(self) -> None:
        from dmbot.audio.segmenter import Utterance

        for name in ("Aldric", "Bryn Shander", "Zephyr"):
            await ui.save_name(self.memory, self.campaign, name, "npc", [], [])
        table = make_table(self.campaign.id)
        self.bot.tables[GUILD] = table
        before = await self.bot._name_hints(clip(table))  # also loads the names to match
        self.assertLess(before.index("Aldric"), before.index("Zephyr"))
        said = Utterance(GUILD, PLAYER, 0, 0, bytes(32000), table.segmenter.session)
        self.bot._deliver_transcript(said, "Let's ask Zephyr about it.")
        after = await self.bot._name_hints(clip(table))
        self.assertEqual(after[0], "Zephyr")  # in the scene now
        self.bot.stop_recording(GUILD, PLAYER)  # their lines stop counting at once
        self.assertEqual(table.scene.said, {})
        moved = await self.bot._name_hints(clip(table))
        self.assertLess(moved.index("Aldric"), moved.index("Zephyr"))


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
