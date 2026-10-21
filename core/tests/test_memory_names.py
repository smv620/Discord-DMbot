"""Campaign memory step 3 (#126): names the DM adds, players' characters, checking the
names DMbot suggests after a session, and names as speech-to-text hints."""

import asyncio
import hashlib
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from dmbot.ai import Reply
from dmbot.audio.segmenter import Segmenter
from dmbot.bot import DMBot, Table
from dmbot.campaigns import CampaignStore
from dmbot.config import Settings
from dmbot.consent import ConsentStore
from dmbot.discord_limits import MESSAGE_MAX
from dmbot.memory.backup import MemorySection
from dmbot.memory.models import CONFIRMED, PROPOSED, REJECTED, MemoryRuleError
from dmbot.memory.models import DM as DM_SOURCE
from dmbot.memory.store import MemoryStore
from dmbot.sessions import SessionStore
from dmbot.ui import logic
from dmbot.ui import names as ui
from tests.pg import DatabaseTest

GUILD, DM, PLAYER, STRANGER, SCREEN, MANAGER = 1, 7, 8, 9, 3, 10


class FakeResponse:
    def __init__(self) -> None:
        self.done = False
        self.sent: list[tuple[str, dict[str, Any]]] = []
        self.edited: list[tuple[str, Any]] = []
        self.modal: Any = None

    def is_done(self) -> bool:
        return self.done

    def _answer(self) -> None:
        # Like discord.py: an interaction is answered once (InteractionResponded).
        assert not self.done, "this interaction was already answered"
        self.done = True

    async def send_message(self, text: str = "", **kw: Any) -> None:
        self._answer()
        self.sent.append((text, kw))

    async def edit_message(self, *, content: str = "", view: Any = None, **_: Any) -> None:
        self._answer()
        self.edited.append((content, view))

    async def send_modal(self, modal: Any) -> None:
        self._answer()
        self.modal = modal

    async def defer(self, **_: Any) -> None:
        self._answer()


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

    async def forget_old_undo(self) -> None:
        """Jump past the days Undo works for, then run the after-session cleanup (#164)."""
        self.memory._clock = lambda: time.time() + 31 * 24 * 60 * 60
        with self.assertLogs("dmbot.bot", "INFO"):
            await self.bot.prune_old_changes(make_table(self.campaign.id))

    def it(self, user_id: int = DM) -> Any:
        user = MagicMock(spec=discord.Member)
        user.id = user_id
        user.guild_permissions = (
            discord.Permissions(manage_guild=True)
            if user_id == MANAGER
            else discord.Permissions.none()
        )
        response = FakeResponse()

        async def edit_original_response(*, content: str = "", view: Any = None, **_: Any) -> None:
            response.edited.append((content, view))  # after a defer: the same message

        async def follow_up(text: str = "", **kw: Any) -> None:
            response.sent.append((text, kw))  # after a defer: the private reply (#537)

        return SimpleNamespace(
            client=self.bot,
            type=discord.InteractionType.component,
            guild=SimpleNamespace(id=GUILD),
            guild_id=GUILD,
            user=user,
            response=response,
            followup=SimpleNamespace(send=AsyncMock(side_effect=follow_up)),
            edit_original_response=AsyncMock(side_effect=edit_original_response),
        )

    def slow(self, method: str, it: Any) -> list[str]:
        """Make memory.<method> take a while; returns, per call, what the message said
        when it started: Discord must have been answered already (#351)."""
        real = getattr(self.memory, method)
        shown: list[str] = []

        async def slow_call(*args: Any, **kw: Any) -> Any:
            shown.append(it.response.edited[-1][0] if it.response.edited else "")
            await asyncio.sleep(0.05)  # stands in for a merge of thousands of rows
            return await real(*args, **kw)

        patcher = patch.object(self.memory, method, side_effect=slow_call)
        patcher.start()
        self.addCleanup(patcher.stop)
        return shown

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

    async def overview(self) -> tuple[str, list[tuple[str, str, str]]]:
        from dmbot.memory.lookup import CampaignLookup

        names = CampaignLookup.build(await self.memory.lookup_data(GUILD, self.campaign.id))
        waiting = len(await self.memory.entities(GUILD, self.campaign.id, statuses=[PROPOSED]))
        return ui.home_text(names, self.campaign, waiting)

    async def test_the_overview_stays_inside_one_message(self) -> None:
        for n in range(30):
            await ui.save_name(self.memory, self.campaign, f"{n:02d}" + "N" * 98, "npc", [], [])
        text, _ = await self.overview()
        self.assertLessEqual(len(text), MESSAGE_MAX)

    async def test_the_overview_counts_kinds_and_never_shows_secrets(self) -> None:
        await ui.save_name(
            self.memory, self.campaign, "Belleros", "npc", ["Bell"], ["the hooded stranger"]
        )
        await ui.save_name(self.memory, self.campaign, "Ulfgar", "npc", [], [])
        await ui.save_name(self.memory, self.campaign, "Bryn Shander", "place", [], [])
        text, shown = await self.overview()
        self.assertIn("**3 names:** 2 NPCs, 1 place", text)
        self.assertIn("🆕 **Added lately:**", text)
        self.assertNotIn("hooded", text)  # secrets are on the card, for DMs only
        self.assertEqual(len(shown), 3)  # for the Open a name… menu
        self.assertTrue(text.endswith(ui.EDIT_HINT))  # how to edit or remove (#353)
        view = ui.NamesHome(self.campaign.id, 0, shown)
        self.assertEqual(view.open.placeholder, "✏️ Fix, change or remove a name…")

    async def test_names_heard_last_session_come_first(self) -> None:
        from dmbot.memory.models import Heard

        said = await ui.save_name(self.memory, self.campaign, "Zephyr", "npc", [], [])
        await ui.save_name(self.memory, self.campaign, "Aldric", "npc", [], [])
        await self.memory.add_session_heard(GUILD, self.campaign.id, 1_000, [Heard(said.id, 8, 3)])
        text, shown = await self.overview()
        self.assertIn("👂 **Heard last session:** **Zephyr**", text)
        self.assertEqual([item[1] for item in shown], ["Zephyr", "Aldric"])  # no repeats

    async def test_the_panel_fits_one_message(self) -> None:
        for i in range(40):
            await ui.save_name(self.memory, self.campaign, f"Name{i} " + "x" * 80, "npc", [], [])
        text, shown = await self.overview()
        self.assertLessEqual(len(text), 2000)
        self.assertIn("… and 30 more", text)
        self.assertEqual(len(shown), 10)

    async def test_names_show_as_typed(self) -> None:
        await ui.save_name(self.memory, self.campaign, "*Star*", "npc", [], [])
        text, _ = await self.overview()
        self.assertIn("**\\*Star\\***", text)


class NameCards(NamesTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        from dmbot.memory.lookup import LookupCache

        self.bot.lookup = LookupCache(self.memory)
        self.bell = await ui.save_name(
            self.memory, self.campaign, "Belleros", "npc", ["Bell"], ["the hooded stranger"]
        )

    def fresh(self) -> None:
        self.bot.lookup.mark_all_stale()  # type: ignore[union-attr]

    async def card(self, user: int = DM) -> str:
        from dmbot.ui import name_card

        self.fresh()
        it = self.it(user)
        await name_card.show_card(it, self.campaign.id, self.bell.id)
        return str(it.response.sent[0][0])

    async def test_the_card_shows_other_names_and_secrets_only_to_the_dm(self) -> None:
        text = await self.card()
        self.assertIn("🪪 **Belleros** · character · NPC · Not heard in a session yet", text)
        self.assertIn("**Also called:** Bell", text)
        self.assertIn("**🤫 Secret:** the hooded stranger", text)
        manager = await self.card(MANAGER)
        self.assertNotIn("hooded", manager)  # a server manager may be at the table

    async def test_an_item_card_has_one_button_for_one_of_a_kind_and_says_so(self) -> None:
        from dmbot.ui import name_card

        sword = await ui.save_name(self.memory, self.campaign, "Heartseeker", "item", [], [])
        self.fresh()
        it = self.it()
        await name_card.show_card(it, self.campaign.id, sword.id)
        text, _kw = it.response.sent[0]
        view = _kw["view"]
        self.assertIn(name_card.ONLY_ONE_GUESS, text)  # a proper name: taken as one of a kind
        self.assertEqual(
            [getattr(c, "label", "") for c in view.children if c.row == 3],
            [name_card.MORE_THAN_ONE_BUTTON],
        )
        bow = await ui.save_name(self.memory, self.campaign, "a longbow", "item", [], [])
        self.fresh()
        it = self.it()
        await name_card.show_card(it, self.campaign.id, bow.id)
        text, _kw = it.response.sent[0]
        self.assertNotIn("only one", text.lower())
        (button,) = [c for c in _kw["view"].children if c.row == 3]
        self.assertEqual(button.label, name_card.ONLY_ONE_BUTTON)
        it = self.it()
        await button.callback(it)  # the DM says there is only one
        text, view = it.response.edited[0]
        self.assertIn(name_card.ONLY_ONE, text)  # now the DM's word, not a guess
        self.assertTrue((await self.memory.entity(GUILD, self.campaign.id, bow.id)).is_unique)  # type: ignore[union-attr]
        (back,) = [c for c in view.children if c.row == 3]
        self.assertEqual(back.label, name_card.MORE_THAN_ONE_BUTTON)
        it = self.it()
        await back.callback(it)  # and says it again the other way
        entity = await self.memory.entity(GUILD, self.campaign.id, bow.id)
        assert entity is not None
        self.assertEqual(entity.unique_item, False)

    async def test_a_kind_a_campaign_added_under_item_gets_the_button_too(self) -> None:
        from dmbot.memory.ontology import TypeTerm
        from dmbot.ui import name_card

        await self.memory.add_type(
            GUILD,
            self.campaign.id,
            TypeTerm("potion", "item", "potion", "A drink.", core=False),
            examples=["healing potion"],
            reason="r",
            source="entitybot",
        )
        potion = (
            await self.memory.add_entity(
                GUILD,
                self.campaign.id,
                type="potion",
                name="Elixir of Dawn",
                source="dm",
                status=CONFIRMED,
            )
        ).value
        self.fresh()
        it = self.it()
        await name_card.show_card(it, self.campaign.id, potion.id)
        text, kw = it.response.sent[0]
        self.assertIn(name_card.ONLY_ONE_GUESS, text)
        self.assertEqual([c.label for c in kw["view"].children if c.row == 3],
                         [name_card.MORE_THAN_ONE_BUTTON])  # fmt: skip

    async def test_only_the_dm_can_press_it(self) -> None:
        from dmbot.ui import name_card

        bow = await ui.save_name(self.memory, self.campaign, "a longbow", "item", [], [])
        for user in (MANAGER, STRANGER, PLAYER):
            it = self.it(user)
            await name_card.NameCard(self.campaign.id, bow.id, item=True)._one_of_a_kind(it)
            self.assertRegex(it.response.sent[0][0], r"Only (the|this campaign's) DM")
        entity = await self.memory.entity(GUILD, self.campaign.id, bow.id)
        assert entity is not None
        self.assertIsNone(entity.unique_item)

    async def test_connecting_a_place_inside_itself_says_so_in_plain_words(self) -> None:
        from dmbot.ui import name_card

        inn = await ui.save_name(self.memory, self.campaign, "Rusty Anchor", "place", [], [])
        town = await ui.save_name(self.memory, self.campaign, "Port Dunmere", "place", [], [])
        self.fresh()
        await name_card.connect(self.it(), self.campaign.id, inn.id, "located_in", town.id)
        it = self.it()
        await name_card.connect(it, self.campaign.id, town.id, "located_in", inn.id)
        text, _view = it.response.edited[0]
        self.assertIn("That would put a place inside itself", text)
        self.assertNotIn("odd pair", text)

    async def test_only_an_item_card_has_that_button(self) -> None:
        from dmbot.ui import name_card

        card = name_card.NameCard(
            self.campaign.id, self.bell.id, others=True, cut=[("names", "Other names (5)")]
        )
        self.assertFalse([c for c in card.children if c.row == 3])
        self.assertTrue(
            all(
                len(getattr(c, "label", "")) <= 25
                for c in name_card.NameCard(self.campaign.id, self.bell.id, item=True).children
            )
        )

    async def test_fix_spelling_changes_the_name_it_listens_for(self) -> None:
        from dmbot.ui import name_card

        form = name_card.FixSpellingForm(self.campaign.id, self.bell.id, "Belleros")
        form.name = SimpleNamespace(value="Bellaros")  # type: ignore[assignment]
        it = self.it()
        await form.on_submit(it)
        text = it.response.edited[0][0]  # the card, redrawn in place, read fresh
        self.assertIn("Now spelled **Bellaros**", text)
        self.assertIn("🪪 **Bellaros**", text)
        self.assertIn("Belleros (old spelling)", text)  # the card says what it is
        keys = {a.key for a in await self.memory.aliases(GUILD, self.campaign.id)}
        self.assertIn("bellaros", keys)
        self.assertIn("belleros", keys)  # kept as an "old spelling" (#2170)
        old = [a for a in await self.memory.aliases(GUILD, self.campaign.id) if a.key == "belleros"]
        self.assertEqual([a.kind for a in old], ["old"])

    async def test_the_narrators_spelling_is_asked_only_when_spoken_recaps_are_on(self) -> None:
        from dmbot.ui import name_card

        plain = name_card.FixSpellingForm(self.campaign.id, self.bell.id, "Belleros")
        self.assertNotIn(plain.said_as, plain.children)  # not in the campaign's plan: no field
        spoken = name_card.FixSpellingForm(
            self.campaign.id, self.bell.id, "Belleros", spoken=True, said_as="Bell-air-os"
        )
        self.assertIn(spoken.said_as, spoken.children)
        self.assertEqual(spoken.said_as.default, "Bell-air-os")
        self.assertIn("narration", spoken.said_as.label or "")

    async def test_a_spelling_for_the_narrator_is_saved_changed_and_cleared_on_the_card(
        self,
    ) -> None:
        from dmbot.ui import name_card

        before: str | None = None
        for typed, told in (
            ("Bell-air-os", "say it as **Bell-air-os**"),
            ("", "say it as written"),
        ):
            form = name_card.FixSpellingForm(
                self.campaign.id, self.bell.id, "Belleros", spoken=True, said_as=before
            )
            form.name = SimpleNamespace(value="Belleros")  # type: ignore[assignment]
            form.said_as = SimpleNamespace(value=typed)  # type: ignore[assignment]
            it = self.it()
            await form.on_submit(it)
            self.assertIn(told, it.response.edited[0][0])
            self.assertEqual(
                await self.memory.said_as(GUILD, self.campaign.id, self.bell.id), typed or None
            )
            before = typed or None

    async def card_labels(self, user: int = DM, *, spoken: bool = True, name: Any = None) -> Any:
        from dmbot.ui import name_card

        self.fresh()
        it = self.it(user)
        with patch.object(name_card, "_spoken_recaps", AsyncMock(return_value=spoken)):
            await name_card.show_card(it, self.campaign.id, (name or self.bell).id)
        return [getattr(c, "label", "") for c in it.response.sent[0][1]["view"].children]

    async def test_the_say_button_is_for_dms_with_spoken_recaps(self) -> None:
        from dmbot.ui import name_card

        self.assertIn(name_card.SAY_BUTTON, await self.card_labels())
        self.assertNotIn(name_card.SAY_BUTTON, await self.card_labels(MANAGER))  # not a DM
        self.assertNotIn(name_card.SAY_BUTTON, await self.card_labels(spoken=False))

    async def test_the_say_button_is_hidden_for_a_secret_main_name(self) -> None:
        from dmbot.ui import name_card

        async with self.db.guild(GUILD) as conn:  # (the store refuses to make a main name secret)
            await conn.execute(
                "UPDATE memory_aliases SET secret = true WHERE guild_id = %s AND campaign_id = %s"
                " AND entity_id = %s AND key = 'belleros'",
                (GUILD, self.campaign.id, self.bell.id),
            )
        self.assertNotIn(name_card.SAY_BUTTON, await self.card_labels())

    async def test_the_say_form_sets_and_clears_how_the_narrator_says_a_name(self) -> None:
        from dmbot.ui import name_card

        for typed, told in (("Bell-air-os", "say it as **Bell-air-os**"), ("", "as written")):
            form = name_card.SayForm(self.campaign.id, self.bell.id, "Belleros", None)
            form.said_as = SimpleNamespace(value=typed)  # type: ignore[assignment]
            it = self.it()
            with patch.object(name_card, "_spoken_recaps", AsyncMock(return_value=True)):
                await form.on_submit(it)
            self.assertIn(told, it.response.edited[0][0])
            self.assertEqual(
                await self.memory.said_as(GUILD, self.campaign.id, self.bell.id), typed or None
            )

    async def test_the_say_form_rechecks_the_dm_and_the_plan_after_its_waits(self) -> None:
        from dmbot.ui import name_card

        form = name_card.SayForm(self.campaign.id, self.bell.id, "Belleros", None)
        form.said_as = SimpleNamespace(value="Bell-air-os")  # type: ignore[assignment]
        it = self.it()
        with patch.object(name_card, "_spoken_recaps", AsyncMock(return_value=False)):
            await form.on_submit(it)  # a DM, but spoken recaps lapsed since the press
        self.assertIn("Spoken recaps aren't on", it.response.sent[0][0])
        gone = SimpleNamespace(dm_user_ids=frozenset({999}), guild_id=GUILD, id=self.campaign.id)
        it = self.it()
        with (
            patch.object(name_card, "_current", AsyncMock(return_value=(gone, "Belleros"))),
            patch.object(name_card, "_spoken_recaps", AsyncMock(return_value=True)),
        ):
            await form.on_submit(it)  # no longer a DM of the campaign
        self.assertIn("Only a DM", it.response.sent[0][0])
        self.assertIsNone(await self.memory.said_as(GUILD, self.campaign.id, self.bell.id))

    async def test_the_say_form_refuses_a_non_dm_and_says_when_spoken_recaps_are_off(self) -> None:
        from dmbot.ui import name_card

        form = name_card.SayForm(self.campaign.id, self.bell.id, "Belleros", None)
        form.said_as = SimpleNamespace(value="Bell-air-os")  # type: ignore[assignment]
        card = name_card.NameCard(self.campaign.id, self.bell.id, say=True)
        manager_press = self.it(MANAGER)
        await card._say(manager_press)
        self.assertIn("Only a DM", manager_press.response.sent[0][0])
        # The manager's submit too, with spoken recaps on: refused for who they are.
        manager_submit = self.it(MANAGER)
        with patch.object(name_card, "_spoken_recaps", AsyncMock(return_value=True)):
            await form.on_submit(manager_submit)
        self.assertIn("Only a DM", manager_submit.response.sent[0][0])
        self.assertIsNone(await self.memory.said_as(GUILD, self.campaign.id, self.bell.id))

    async def test_a_form_without_the_field_never_touches_the_spelling(self) -> None:
        from dmbot.ui import name_card

        await self.memory.set_said_as(
            GUILD, self.campaign.id, self.bell.id, "Bell-air-os", source="dm"
        )
        form = name_card.FixSpellingForm(self.campaign.id, self.bell.id, "Belleros")
        form.name = SimpleNamespace(value="Belleros")  # type: ignore[assignment]
        await form.on_submit(self.it())
        self.assertEqual(
            await self.memory.said_as(GUILD, self.campaign.id, self.bell.id), "Bell-air-os"
        )

    async def test_another_name_and_a_secret_one(self) -> None:
        from dmbot.ui import name_card

        form = name_card.AnotherNameForm(self.campaign.id, self.bell.id, "Belleros", secrets=True)
        form.other = SimpleNamespace(value="Old Bell")  # type: ignore[assignment]
        form.secret = SimpleNamespace(value="the grey knight")  # type: ignore[assignment]
        it = self.it()
        await form.on_submit(it)
        aliases = await self.memory.aliases(GUILD, self.campaign.id, include_secret=True)
        shown = {(a.text, a.secret) for a in aliases}
        self.assertIn(("Old Bell", False), shown)
        self.assertIn(("the grey knight", True), shown)

    async def test_a_manager_can_never_add_a_secret_name(self) -> None:
        from dmbot.ui import name_card

        form = name_card.AnotherNameForm(self.campaign.id, self.bell.id, "Belleros", secrets=False)
        form.other = SimpleNamespace(value="")  # type: ignore[assignment]
        form.secret = SimpleNamespace(value="the grey knight")  # type: ignore[assignment]
        it = self.it(MANAGER)
        await form.on_submit(it)
        aliases = await self.memory.aliases(GUILD, self.campaign.id, include_secret=True)
        self.assertNotIn("the grey knight", {a.text for a in aliases})

    async def test_remove_sits_with_the_edits_and_still_asks_first(self) -> None:
        from dmbot.ui import name_card

        card = name_card.NameCard(self.campaign.id, self.bell.id, others=True)
        first_row = [getattr(c, "label", "") for c in card.children if c.row in (None, 0)]
        # The three edits the panel names, in its words (#353).
        self.assertEqual(first_row, ["✏️ Fix spelling", "Change what it is", "🗑 Remove this name"])
        longer = name_card.NameCard(
            self.campaign.id, self.bell.id, others=True, cut=[("names", "Other names (5)")]
        )
        rows: dict[int, list[str]] = {}
        for c in longer.children:
            rows.setdefault(c.row or 0, []).append(getattr(c, "label", ""))
        # Three short buttons a row at most, so nothing clips on a phone (#393).
        self.assertEqual(rows[1], [name_card.ALSO_CALLED, "Edit other names", "🔗 Same as…"])
        self.assertEqual(rows[2], ["🧭 Connect to…"])
        (picker,) = [c for c in longer.children if c.row == 4]  # one select, on the last row
        self.assertEqual(getattr(picker, "placeholder", None), name_card.SHOW_ALL)
        self.assertTrue(all(len(getattr(c, "label", "")) <= 25 for c in card.children))
        (remove,) = [c for c in card.children if getattr(c, "label", "") == "🗑 Remove this name"]
        it = self.it()
        await remove.callback(it)
        text, view = it.response.edited[0]
        self.assertIsInstance(view, name_card.ConfirmRemove)  # asks first
        self.assertIn("Belleros", text)

    async def test_remove_asks_first_and_can_be_undone(self) -> None:
        from dmbot.ui import name_card

        view = name_card.ConfirmRemove(self.campaign.id, self.bell.id)
        it = self.it()
        await view._forget(it)
        content, undo_view = it.response.edited[0]
        self.assertIn("Forgot **Belleros**", content)
        self.assertNotIn("Belleros", await self.names())
        (dynamic,) = undo_view.children
        custom_id = dynamic.item.custom_id
        template = name_card.UndoButton.__discord_ui_compiled_template__
        match = template.fullmatch(custom_id)
        assert match is not None
        undo = await name_card.UndoButton.from_custom_id(self.it(), dynamic.item, match)
        it = self.it()
        await undo.callback(it)
        self.assertIn("↩️ **Belleros** is back", it.response.edited[-1][0])
        self.assertIn("Belleros", await self.names())

    async def test_undo_says_when_it_is_too_late(self) -> None:
        from dmbot.ui import name_card

        view = name_card.ConfirmRemove(self.campaign.id, self.bell.id)
        it = self.it()
        await view._forget(it)
        content, undo_view = it.response.edited[0]
        self.assertIn("Press **Undo** within 30 days to bring it back.", content)
        (undo,) = undo_view.children
        assert isinstance(undo, name_card.UndoButton)
        await self.forget_old_undo()
        it = self.it()
        await undo.callback(it)
        self.assertEqual(
            it.response.edited[-1][0],  # in place (#351)
            "Too late to undo: Undo works for 30 days, and not from before a backup was loaded. "
            "Add it again with ➕ Add a name on the names panel (its other names and "
            "connections need adding again too).",
        )
        self.assertNotIn("Belleros", await self.names())

    async def test_change_what_it_is_shows_at_once(self) -> None:
        from dmbot.ui import name_card

        await self.card()  # loads the copy
        view = name_card.KindChange(self.campaign.id, self.bell.id, "Belleros")
        view.pick = SimpleNamespace(values=["place"])  # type: ignore[assignment]
        it = self.it()
        await view._picked(it)
        self.assertIn("🪪 **Belleros** · place", it.response.edited[0][0])

    async def test_a_manager_adding_a_name_never_learns_of_a_secret_one(self) -> None:
        form = ui.AddNameForm(self.campaign.id, secrets=False)
        self.assertNotIn(form.secret, form.children)  # no secret-name field
        form.name = SimpleNamespace(value="the hooded stranger")  # type: ignore[assignment]
        form.others = SimpleNamespace(value="")  # type: ignore[assignment]
        it = self.it(MANAGER)
        await form.on_submit(it)
        self.assertNotIn("already knows", it.response.sent[0][0])  # just "What is …?"

    async def test_find_opens_the_card_when_one_name_matches(self) -> None:
        from dmbot.ui import name_card

        it = self.it()
        await name_card.show_matches(it, self.campaign.id, "bell or us")  # how it sounds
        self.assertIn("🪪 **Belleros**", it.response.sent[0][0])
        it = self.it()
        await name_card.show_matches(it, self.campaign.id, "zzz")
        self.assertIn("No name like **zzz**", it.response.sent[0][0])

    async def alias(self, text: str) -> Any:
        aliases = await self.memory.aliases(
            GUILD, self.campaign.id, entity_id=self.bell.id, include_secret=True
        )
        return next(a for a in aliases if a.text == text)

    async def test_another_name_can_become_the_main_name(self) -> None:
        from dmbot.ui import name_card

        view = name_card.OneName(
            self.campaign.id, self.bell.id, await self.alias("Bell"), secrets=True
        )
        it = self.it()
        await view._main(it)
        self.assertIn("⭐ **Bell** is the main name now", it.response.edited[0][0])
        text = await self.card()
        self.assertIn("🪪 **Bell** · character · NPC", text)
        self.assertIn("**Also called:** Belleros", text)

    async def test_a_secret_name_never_becomes_the_main_name(self) -> None:
        from dmbot.ui import name_card

        secret = await self.alias("the hooded stranger")
        view = name_card.OneName(self.campaign.id, self.bell.id, secret, secrets=True)
        buttons = [c for c in view.children if isinstance(c, discord.ui.Button)]
        main = next(b for b in buttons if (b.label or "").startswith("⭐"))
        self.assertTrue(main.disabled)
        with self.assertRaises(MemoryRuleError):
            await self.memory.set_main_name(
                GUILD, self.campaign.id, self.bell.id, secret.id, source="dm"
            )

    async def test_keep_a_name_secret_and_a_manager_never_finds_it(self) -> None:
        from dmbot.ui import name_card

        bell = await self.alias("Bell")
        view = name_card.OneName(self.campaign.id, self.bell.id, bell, secrets=True)
        it = self.it()
        await view._secret(it)
        self.assertIn("🤫 **Bell** is secret now", it.response.edited[0][0])
        self.assertTrue((await self.alias("Bell")).secret)
        it = self.it(MANAGER)  # may be at the table: the secret name is "gone" for them
        await name_card.OneName(self.campaign.id, self.bell.id, bell, secrets=False)._not_this(it)
        self.assertIn("isn't there any more", it.response.sent[0][0])
        self.assertTrue((await self.alias("Bell")).secret)

    async def test_not_this_name(self) -> None:
        from dmbot.ui import name_card

        view = name_card.OneName(
            self.campaign.id, self.bell.id, await self.alias("Bell"), secrets=True
        )
        it = self.it()
        await view._not_this(it)
        self.assertIn("stops listening for **Bell** as **Belleros**", it.response.edited[0][0])
        self.assertNotIn("Also called", await self.card())

    async def test_same_as_joins_two_names_and_can_be_undone(self) -> None:
        from dmbot.ui import name_card

        eros = await ui.save_name(self.memory, self.campaign, "Bell Eros", "npc", [], [])
        view = name_card.SameConfirm(
            self.campaign.id, eros.id, self.bell.id, "Bell Eros", "Belleros"
        )
        it = self.it()
        await view._keep_other(it)
        content, _ = it.response.edited[-1]
        self.assertIn("🔗 Done: **Bell Eros** is now another name for **Belleros**", content)
        self.assertNotIn("Bell Eros", await self.names())
        self.assertIn("Bell Eros", await self.card())  # now one of its other names
        self.assertIn("Press **Undo** within 30 days to split", it.followup.send.call_args.args[0])
        lasting = it.followup.send.call_args.kwargs["view"]  # outlives the card's menu
        self.assertIsNone(lasting.timeout)
        (undo,) = [c for c in lasting.children if isinstance(c, name_card.UndoButton)]
        it = self.it()
        await undo.callback(it)
        self.assertIn("↩️ **Bell Eros** is back", it.response.edited[-1][0])
        self.assertIn("Bell Eros", await self.names())

    async def test_a_slow_join_and_its_undo_answer_discord_first(self) -> None:
        """#351: Discord gives 3 seconds. A big merge or undo takes longer, so the
        buttons answer first and the card still arrives after."""
        from dmbot.ui import name_card

        eros = await ui.save_name(self.memory, self.campaign, "Bell Eros", "npc", [], [])
        view = name_card.SameConfirm(
            self.campaign.id, eros.id, self.bell.id, "Bell Eros", "Belleros"
        )
        it = self.it()
        merged = self.slow("merge", it)
        await view._keep_other(it)
        # Answered in place, buttons gone (no second press), then the card.
        self.assertEqual(merged, ["🔗 Joining **Bell Eros** into **Belleros**…"])
        self.assertIsNone(it.response.edited[0][1])
        self.assertIn("🔗 Done", it.response.edited[-1][0])
        it.edit_original_response.assert_awaited()  # the card arrived after the answer
        (undo,) = [
            c
            for c in it.followup.send.call_args.kwargs["view"].children
            if isinstance(c, name_card.UndoButton)
        ]
        it = self.it()
        undone = self.slow("undo", it)
        await undo.callback(it)
        self.assertEqual(undone, ["↩️ Undoing… bringing back **Bell Eros**."])
        self.assertIn("↩️ **Bell Eros** is back", it.response.edited[-1][0])
        it = self.it()
        await undo.callback(it)  # pressed again later: nothing to undo
        self.assertIn("Nothing to undo", it.response.sent[0][0])

    async def test_a_join_that_is_refused_says_so_in_place(self) -> None:
        from dmbot.ui import name_card

        eros = await ui.save_name(self.memory, self.campaign, "Bell Eros", "npc", [], [])
        view = name_card.SameConfirm(
            self.campaign.id, eros.id, self.bell.id, "Bell Eros", "Belleros"
        )
        it = self.it()
        with patch.object(
            self.memory, "merge", side_effect=MemoryRuleError("Those are two players.")
        ):
            await view._keep_other(it)
        self.assertEqual(
            it.response.edited[-1],
            ("Couldn't join them. Those are two players. Nothing was changed.", None),
        )
        it = self.it()
        broken = patch.object(self.memory, "merge", side_effect=RuntimeError("database gone"))
        with broken, self.assertLogs("dmbot.ui.name_card", "ERROR"):
            await view._keep_other(it)
        self.assertEqual(it.response.edited[-1], (name_card.NOTHING_CHANGED, None))

    async def test_same_as_never_offers_the_name_itself(self) -> None:
        from dmbot.ui import name_card

        self.fresh()
        it = self.it()
        await name_card.show_picks(it, self.campaign.id, self.bell.id, name_card.SAME, "Belleros")
        self.assertIn("No other name like **Belleros**", it.response.edited[0][0])

    async def test_carries_reads_in_plain_words_from_both_sides(self) -> None:
        from dmbot.ui import name_card

        bow = await ui.save_name(self.memory, self.campaign, "Heartseeker", "item", [], [])
        bram = await ui.save_name(self.memory, self.campaign, "Bram", "npc", [], [])
        # The choice is there from the being's side and, read the other way, from the item's.
        values = {v: label for v, label, _ in name_card.connect_choices("Bram")}
        self.assertEqual(values["carries"], "Bram carries…")
        self.assertEqual(values["carries" + name_card.BACK], "Bram is carried by…")
        it = self.it()
        await name_card.connect(it, self.campaign.id, bram.id, "carries", bow.id)
        self.assertIn("🧭 Saved: **Bram** carries **Heartseeker**.", it.response.edited[0][0])
        self.fresh()
        it = self.it()
        await name_card.show_card(it, self.campaign.id, bram.id)
        self.assertIn("**Connections:** carries **Heartseeker**", it.response.sent[0][0])
        self.fresh()
        it = self.it()
        await name_card.show_card(it, self.campaign.id, bow.id)
        self.assertIn("**Connections:** is carried by **Bram**", it.response.sent[0][0])
        self.assertNotIn("is connected to", it.response.sent[0][0])

    async def test_a_second_holder_of_a_one_of_a_kind_item_gets_the_plain_note(self) -> None:
        from dmbot.ui import name_card

        bow = await ui.save_name(self.memory, self.campaign, "Heartseeker", "item", [], [])
        bram = await ui.save_name(self.memory, self.campaign, "Bram", "npc", [], [])
        hesper = await ui.save_name(self.memory, self.campaign, "Hesper", "npc", [], [])
        await self.memory.set_unique(GUILD, self.campaign.id, bow.id, True, source=DM_SOURCE)
        await name_card.connect(self.it(), self.campaign.id, bram.id, "carries", bow.id)
        it = self.it()
        await name_card.connect(it, self.campaign.id, hesper.id, "carries", bow.id)
        text = it.response.edited[0][0]
        self.assertIn("Someone else already has that, and there is only one", text)
        self.assertIn("DMbot will ask you about it after the session", text)
        self.assertNotIn("odd pair", text)

    async def test_connect_to_reads_both_ways_and_can_be_removed(self) -> None:
        from dmbot.ui import name_card

        tribe = await ui.save_name(self.memory, self.campaign, "Frostwolf tribe", "faction", [], [])
        ulfgar = await ui.save_name(self.memory, self.campaign, "Ulfgar", "npc", [], [])
        it = self.it()
        await name_card.connect(
            it, self.campaign.id, tribe.id, "member_of" + name_card.BACK, ulfgar.id
        )
        self.assertIn(
            "🧭 Saved: **Ulfgar** is a member of **Frostwolf tribe**", it.response.edited[0][0]
        )
        self.fresh()
        it = self.it()
        await name_card.show_card(it, self.campaign.id, tribe.id)
        self.assertIn("**Connections:** members include **Ulfgar**", it.response.sent[0][0])
        self.fresh()
        it = self.it()
        await name_card.show_card(it, self.campaign.id, ulfgar.id)
        self.assertIn("**Connections:** is a member of **Frostwolf tribe**", it.response.sent[0][0])
        (relation,) = await self.memory.relations(GUILD, self.campaign.id, entity_id=ulfgar.id)
        view = name_card.Connect(self.campaign.id, ulfgar.id, "Ulfgar", [(relation.id, "x")])
        view.remove = SimpleNamespace(values=[relation.id])  # type: ignore[assignment]
        it = self.it()
        await view._remove_picked(it)
        self.assertIn("Removed: Ulfgar is a member of Frostwolf tribe.", it.response.edited[0][0])
        self.assertEqual(
            await self.memory.relations(GUILD, self.campaign.id, entity_id=ulfgar.id), []
        )

    async def removed_connection(self, *, secret: bool) -> tuple[Any, Any, Any]:
        """Ulfgar is a member of the tribe (secret or not); the DM removes it. Returns the
        relation as it was, the entry, and the Undo button on the redrawn card."""
        from dmbot.ui import name_card

        tribe = await ui.save_name(self.memory, self.campaign, "Frostwolf tribe", "faction", [], [])
        ulfgar = await ui.save_name(self.memory, self.campaign, "Ulfgar", "npc", [], [])
        await name_card.connect(
            self.it(), self.campaign.id, tribe.id, "member_of" + name_card.BACK, ulfgar.id,
            secret=secret,
        )  # fmt: skip
        self.fresh()
        (before,) = await self.memory.relations(
            GUILD, self.campaign.id, entity_id=ulfgar.id, include_secret=True
        )
        view = name_card.Connect(
            self.campaign.id, ulfgar.id, "Ulfgar", [(before.id, "x")], secrets=True
        )
        view.remove = SimpleNamespace(values=[before.id])  # type: ignore[assignment]
        it = self.it()
        await view._remove_picked(it)
        note, card = it.response.edited[0]
        self.assertIn("Removed: Ulfgar is a member of Frostwolf tribe. Wrong? Press **Undo**", note)
        (undo,) = [c for c in card.children if isinstance(c, name_card.UndoConnectionButton)]
        return before, ulfgar, undo

    async def test_undo_puts_a_removed_connection_back_exactly_as_it_was(self) -> None:
        before, ulfgar, undo = await self.removed_connection(secret=False)
        it = self.it()
        await undo.callback(it)
        self.assertIn("Put back: Ulfgar is a member of Frostwolf tribe.", it.response.edited[0][0])
        (after,) = await self.memory.relations(GUILD, self.campaign.id, entity_id=ulfgar.id)
        self.assertEqual(after, before)

    async def test_a_secret_connection_comes_back_secret_and_only_a_dm_can_undo_it(self) -> None:
        before, ulfgar, undo = await self.removed_connection(secret=True)
        self.assertTrue(before.secret)
        manager = self.it(MANAGER)
        await undo.callback(manager)
        from dmbot.ui import name_card

        self.assertEqual(manager.response.sent[0][0], f"Not undone. {name_card.NOTHING_MORE}")
        self.assertEqual(  # nothing written
            await self.memory.relations(
                GUILD, self.campaign.id, entity_id=ulfgar.id, include_secret=True
            ),
            [],
        )
        await undo.callback(self.it())
        (after,) = await self.memory.relations(
            GUILD, self.campaign.id, entity_id=ulfgar.id, include_secret=True
        )
        self.assertEqual(after, before)
        self.assertTrue(after.secret)

    async def test_undo_after_the_connection_changed_says_so_and_does_nothing(self) -> None:
        from dmbot.ui import name_card

        before, ulfgar, undo = await self.removed_connection(secret=False)
        await self.memory.update_relation(
            GUILD, self.campaign.id, before.id, status=CONFIRMED, source=DM_SOURCE
        )
        await self.memory.update_relation(
            GUILD, self.campaign.id, before.id, secret=True, source=DM_SOURCE
        )
        it = self.it()
        await undo.callback(it)
        self.assertEqual(it.response.sent[0][0], f"Not undone. {name_card.NOTHING_MORE}")
        (now,) = await self.memory.relations(
            GUILD, self.campaign.id, entity_id=ulfgar.id, include_secret=True
        )
        self.assertTrue(now.secret)  # the later change stands

    async def test_someone_who_cannot_manage_names_cannot_undo(self) -> None:
        _, ulfgar, undo = await self.removed_connection(secret=False)
        it = self.it(PLAYER)
        await undo.callback(it)
        self.assertEqual(
            await self.memory.relations(GUILD, self.campaign.id, entity_id=ulfgar.id), []
        )  # refused, nothing put back

    async def test_the_store_only_undoes_one_removal_by_the_dm(self) -> None:
        from dmbot.memory.models import TooLateToUndo

        before, _ulfgar, _ = await self.removed_connection(secret=False)
        # The removal's batch is the newest change: find it, then try other kinds of batch.
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                "SELECT batch, op FROM memory_changes WHERE table_name = 'memory_relations'"
                " ORDER BY version"
            )
            rows = await cur.fetchall()
        insert_batch = rows[0]["batch"]  # the connection being saved: an insert, not a removal
        with self.assertRaises(MemoryRuleError):
            await self.memory.undo_connection(
                GUILD, self.campaign.id, insert_batch, within=600, secrets=True
            )
        with self.assertRaises(TooLateToUndo):  # a batch number that is not there
            await self.memory.undo_connection(
                GUILD, self.campaign.id, 10**9, within=600, secrets=True
            )
        other = await self.campaigns.create(GUILD, "Other", DM)
        with self.assertRaises(TooLateToUndo):  # another campaign's log has no such batch
            await self.memory.undo_connection(
                GUILD, other.id, rows[-1]["batch"], within=600, secrets=True
            )
        self.memory._clock = lambda: time.time() + 590  # just inside the window
        back = await self.memory.undo_connection(
            GUILD, self.campaign.id, rows[-1]["batch"], within=600, secrets=True
        )
        self.assertEqual(back.value, before)

    async def test_undo_stops_after_ten_minutes(self) -> None:
        _, ulfgar, undo = await self.removed_connection(secret=False)
        self.memory._clock = lambda: time.time() + 601
        it = self.it()
        await undo.callback(it)
        self.assertIn("Too late to undo", it.response.sent[0][0])
        self.assertEqual(
            await self.memory.relations(GUILD, self.campaign.id, entity_id=ulfgar.id), []
        )

    async def test_undo_cannot_be_pressed_twice(self) -> None:
        from dmbot.ui import name_card

        _, ulfgar, undo = await self.removed_connection(secret=False)
        await undo.callback(self.it())
        again = self.it()
        await undo.callback(again)
        self.assertEqual(again.response.sent[0][0], f"Not undone. {name_card.NOTHING_MORE}")
        self.assertEqual(
            len(await self.memory.relations(GUILD, self.campaign.id, entity_id=ulfgar.id)), 1
        )

    async def test_the_undo_button_survives_a_restart(self) -> None:
        import re

        from dmbot.ui import name_card

        _, _, undo = await self.removed_connection(secret=False)
        cid = undo.item.custom_id
        assert cid is not None
        self.assertLess(len(cid), 100)
        match = name_card.UndoConnectionButton.__discord_ui_compiled_template__.fullmatch(cid)
        assert isinstance(match, re.Match)
        again = await name_card.UndoConnectionButton.from_custom_id(self.it(), undo.item, match)
        self.assertEqual(again.batch, undo.batch)

    async def two_names(self) -> tuple[Any, Any]:
        brother = await ui.save_name(self.memory, self.campaign, "Corin", "npc", [], [])
        stranger = await ui.save_name(
            self.memory, self.campaign, "Vessa", "npc", [], ["the veiled one"]
        )
        return brother, stranger

    async def test_the_secret_toggle_is_for_dms_only_and_flips(self) -> None:
        from dmbot.ui import name_card

        dm_view = name_card.Connect(self.campaign.id, self.bell.id, "Belleros", [], secrets=True)
        toggle = dm_view.toggle
        it = self.it()
        await dm_view._toggle_secret(it)
        self.assertTrue(dm_view.secret)
        self.assertEqual(toggle.label, name_card.SECRET_ON)
        self.assertEqual(toggle.style, discord.ButtonStyle.success)
        await dm_view._toggle_secret(self.it())
        self.assertFalse(dm_view.secret)
        self.assertEqual(toggle.label, name_card.SECRET_OFF)
        manager_view = name_card.Connect(self.campaign.id, self.bell.id, "Belleros", [])
        self.assertFalse([c for c in manager_view.children if "secret" in getattr(c, "label", "")])

    async def test_connect_to_offers_the_toggle_only_to_a_dm(self) -> None:
        from dmbot.ui import name_card

        for who, expect in ((DM, True), (MANAGER, False)):
            it = self.it(who)
            card = name_card.NameCard(self.campaign.id, self.bell.id)
            await card._connect(it)
            view = it.response.edited[0][1]
            labels = [getattr(c, "label", "") for c in view.children]
            self.assertEqual(name_card.SECRET_OFF in labels, expect)

    async def test_a_secret_connection_is_saved_secret_and_says_so(self) -> None:
        from dmbot.ui import name_card

        corin, _ = await self.two_names()
        it = self.it()
        await name_card.connect(it, self.campaign.id, self.bell.id, "kin_of", corin.id, secret=True)
        note = it.response.edited[0][0]
        self.assertIn(
            "🧭 Saved: **Belleros** is family of **Corin** 🤫 Secret: players can't see it.", note
        )
        (rel,) = await self.memory.relations(
            GUILD, self.campaign.id, entity_id=corin.id, include_secret=True
        )
        self.assertTrue(rel.secret)
        self.assertEqual(
            await self.memory.relations(GUILD, self.campaign.id, entity_id=corin.id), []
        )  # the default read, which everything else uses, never sees it

    async def test_a_press_by_someone_who_is_no_longer_a_dm_saves_it_not_secret(self) -> None:
        from dmbot.ui import name_card

        corin, _ = await self.two_names()
        it = self.it(MANAGER)
        await name_card.connect(it, self.campaign.id, self.bell.id, "kin_of", corin.id, secret=True)
        self.assertIn(name_card.SECRET_DENIED_SAVE, it.response.edited[0][0])
        (rel,) = await self.memory.relations(GUILD, self.campaign.id, entity_id=corin.id)
        self.assertFalse(rel.secret)

    async def test_a_secret_connection_saved_again_as_not_secret_stays_secret_and_says_so(
        self,
    ) -> None:
        from dmbot.ui import name_card

        corin, _ = await self.two_names()
        await name_card.connect(
            self.it(), self.campaign.id, self.bell.id, "kin_of", corin.id, secret=True
        )
        it = self.it()
        await name_card.connect(it, self.campaign.id, self.bell.id, "kin_of", corin.id)
        self.assertIn(name_card.SECRET_ALREADY, it.response.edited[0][0])
        (rel,) = await self.memory.relations(
            GUILD, self.campaign.id, entity_id=corin.id, include_secret=True
        )
        self.assertTrue(rel.secret)

    async def test_a_forced_secret_is_not_saved_in_the_open_by_someone_no_longer_a_dm(
        self,
    ) -> None:
        from dmbot.ui import name_card

        _, vessa = await self.two_names()
        it = self.it(MANAGER)
        await name_card.connect(
            it, self.campaign.id, self.bell.id, "kin_of", vessa.id, secret=True, forced=True
        )
        self.assertEqual(it.response.sent[0][0], name_card.SECRET_DENIED_PRESS)
        self.assertEqual(
            await self.memory.relations(
                GUILD, self.campaign.id, entity_id=vessa.id, include_secret=True
            ),
            [],
        )

    async def test_the_toggle_comes_after_back(self) -> None:
        from dmbot.ui import name_card

        view = name_card.Connect(self.campaign.id, self.bell.id, "Belleros", [], secrets=True)
        row = [getattr(c, "label", "") for c in view.children if c.row == 2]
        self.assertEqual(row, ["Back", name_card.SECRET_OFF])

    async def test_a_name_found_through_a_secret_name_makes_it_secret_by_itself(self) -> None:
        from dmbot.ui import name_card

        _, vessa = await self.two_names()
        it = self.it()
        await name_card.show_picks(it, self.campaign.id, self.bell.id, "kin_of", "veiled")
        view = it.response.edited[0][1]
        self.assertEqual(view.hidden, frozenset({vessa.id}))
        view.pick = SimpleNamespace(values=[vessa.id])
        it = self.it()
        await view._picked(it)
        self.assertIn(name_card.SECRET_FORCED, it.response.edited[0][0])
        (rel,) = await self.memory.relations(
            GUILD, self.campaign.id, entity_id=vessa.id, include_secret=True
        )
        self.assertTrue(rel.secret)

    async def test_the_toggle_state_travels_through_the_steps(self) -> None:
        from dmbot.ui import name_card

        view = name_card.Connect(self.campaign.id, self.bell.id, "Belleros", [], secrets=True)
        await view._toggle_secret(self.it())
        view.how = SimpleNamespace(values=["kin_of"])  # type: ignore[assignment]
        it = self.it()
        await view._how_picked(it)
        self.assertTrue(it.response.modal.secret)

    async def test_only_a_dm_sees_secret_connections_to_remove_and_nothing_else_reads_them(
        self,
    ) -> None:
        from dmbot.ui import name_card

        corin, _ = await self.two_names()
        await name_card.connect(
            self.it(), self.campaign.id, self.bell.id, "kin_of", corin.id, secret=True
        )
        for who, listed in ((DM, True), (MANAGER, False)):
            it = self.it(who)
            await name_card.NameCard(self.campaign.id, self.bell.id)._connect(it)
            view = it.response.edited[0][1]
            options = [o.label for o in view.remove.options] if hasattr(view, "remove") else []
            self.assertEqual(any("🤫" in label for label in options), listed)
        # The copy the overview, the type-ahead, the scans and the recall read leaves secret
        # connections out altogether.
        data = await self.memory.lookup_data(GUILD, self.campaign.id)
        self.assertEqual(data.relations, ())
        player_card = self.it(PLAYER)
        await name_card.show_card(player_card, self.campaign.id, corin.id)
        self.assertNotIn("Belleros", player_card.response.sent[0][0])

    async def test_a_long_note_and_card_fit_one_message(self) -> None:
        from dmbot.ui import name_card

        for n in range(30):
            await self.memory.add_alias(
                GUILD, self.campaign.id, self.bell.id, f"Bell {n} " + "x" * 80, kind="nickname",
                source="dm", status=CONFIRMED,
            )  # fmt: skip
        self.fresh()
        it = self.it()
        note = "✅ " + "y" * 1500
        await name_card.show_card(it, self.campaign.id, self.bell.id, note=note)
        self.assertLessEqual(len(it.response.sent[0][0]), 2000)

    async def test_a_manager_never_gets_the_secret_button(self) -> None:
        from dmbot.ui import name_card

        view = name_card.OneName(
            self.campaign.id, self.bell.id, await self.alias("Bell"), secrets=False
        )
        labels = [b.label or "" for b in view.children if isinstance(b, discord.ui.Button)]
        self.assertFalse(any("secret" in label for label in labels))
        it = self.it(MANAGER)
        await view._secret(it)
        self.assertIn("Only the campaign's DMs", it.response.sent[0][0])
        self.assertFalse((await self.alias("Bell")).secret)

    async def add_names(self, n: int, *, secret: bool = False) -> None:
        for k in range(n):
            await self.memory.add_alias(
                GUILD, self.campaign.id, self.bell.id, f"{'Hush' if secret else 'Bell'} {k:02d}",
                kind="nickname", source="dm", status=CONFIRMED, secret=secret,
            )  # fmt: skip
        self.fresh()

    @staticmethod
    def select(kw: dict[str, Any]) -> Any:
        found = [c for c in kw["view"].children if isinstance(c, discord.ui.Select)]
        return found[0] if found else None

    async def test_show_all_is_one_select_with_a_choice_for_each_cut_section(self) -> None:
        from dmbot.ui import name_card

        await self.add_names(5)  # and the name's own "Bell": six in all
        it = self.it()
        await name_card.show_card(it, self.campaign.id, self.bell.id)
        text, kw = it.response.sent[0]
        self.assertIn("… and 3 more", text)
        select = self.select(kw)
        self.assertEqual(select.placeholder, name_card.SHOW_ALL)
        self.assertEqual([(o.value, o.label) for o in select.options],
                         [("names", "Other names (6)")])  # fmt: skip
        self.assertFalse(
            [c for c in kw["view"].children if getattr(c, "label", None) == "Show all"]
        )  # no button any more

    async def test_nothing_cut_means_no_select(self) -> None:
        from dmbot.ui import name_card

        await self.add_names(2)  # with its own "Bell", three: exactly what the card shows
        it = self.it()
        await name_card.show_card(it, self.campaign.id, self.bell.id)
        self.assertIsNone(self.select(it.response.sent[0][1]))

    async def test_choosing_a_section_sends_every_entry_in_order_and_leaves_the_card(self) -> None:
        from dmbot.ui import name_card

        await self.add_names(5)
        it = self.it()
        await name_card.show_section(it, self.campaign.id, self.bell.id, "names")
        text, kw = it.response.sent[0]
        self.assertIn("other names (6)", text)
        lines = text.splitlines()[1:]
        self.assertEqual(sorted(lines), sorted(["Bell", *[f"Bell {k:02d}" for k in range(5)]]))
        card = self.it()
        await name_card.show_card(card, self.campaign.id, self.bell.id)
        shown = card.response.sent[0][0].split("**Also called:** ")[1].split(" …")[0].split(", ")
        self.assertEqual(lines[:3], shown)  # the card's first three, then the rest, same order
        self.assertIsNone(kw.get("view"))  # one page: no Older / Newer
        self.assertEqual(it.response.edited, [])  # the card is not changed

    async def test_a_long_section_is_paged_by_25_lines(self) -> None:
        from dmbot.ui import name_card

        await self.add_names(60)
        it = self.it()
        await name_card.show_section(it, self.campaign.id, self.bell.id, "names")
        text, kw = it.response.sent[0]
        self.assertEqual(len(text.splitlines()), 1 + 25 + 1)  # head, 25 lines, the page line
        self.assertIn("Page 1 of 3", text)
        view = kw["view"]
        self.assertEqual([c.label for c in view.children], ["Later ▶"])
        it = self.it()
        await view._later(it)
        text, kw = it.response.edited[0]
        self.assertIn("Page 2 of 3", text)
        self.assertEqual(len(text.splitlines()), 1 + 25 + 1)
        self.assertEqual([c.label for c in kw.children], ["◀ Earlier", "Later ▶"])
        it = self.it()
        await name_card.show_section(it, self.campaign.id, self.bell.id, "names", page=9)
        self.assertIn("Page 3 of 3", it.response.sent[0][0])  # past the end: the last page

    async def test_a_player_or_manager_never_gets_the_secret_names(self) -> None:
        from dmbot.ui import name_card

        await self.add_names(5, secret=True)
        dm_it = self.it()
        await name_card.show_card(dm_it, self.campaign.id, self.bell.id)
        values = [o.value for o in self.select(dm_it.response.sent[0][1]).options]
        self.assertIn("secret", values)
        asked = self.it(MANAGER)
        await name_card.show_card(asked, self.campaign.id, self.bell.id)
        text, kw = asked.response.sent[0]
        self.assertNotIn("Hush", text)
        self.assertIsNone(self.select(kw))  # nothing visible is cut
        sneaky = self.it(MANAGER)  # an old card, or a made-up press
        await name_card.show_section(sneaky, self.campaign.id, self.bell.id, "secret")
        self.assertEqual(sneaky.response.sent[0][0], name_card.NOTHING_MORE)
        self.assertNotIn("Hush", str(sneaky.response.sent))

    async def test_connections_are_listed_in_full_and_nothing_is_written(self) -> None:
        from dmbot.ui import name_card

        for k in range(5):
            other = await ui.save_name(self.memory, self.campaign, f"Friend {k}", "npc", [], [])
            await self.memory.add_relation(
                GUILD, self.campaign.id, self.bell.id, "knows", other.id,
                source="dm", confidence=1.0, status=CONFIRMED,
            )  # fmt: skip
        self.fresh()
        before = await self.memory.aliases(GUILD, self.campaign.id, include_secret=True)
        it = self.it()
        await name_card.show_card(it, self.campaign.id, self.bell.id)
        select = self.select(it.response.sent[0][1])
        self.assertEqual([o.label for o in select.options], ["Connections (5)"])
        it = self.it()
        await name_card.show_section(it, self.campaign.id, self.bell.id, "links")
        text = it.response.sent[0][0]
        for k in range(5):
            self.assertIn(f"knows **Friend {k}**", text)
        after = await self.memory.aliases(GUILD, self.campaign.id, include_secret=True)
        self.assertEqual(len(before), len(after))

    async def friends(self, n: int, *, secret: bool = False, predicate: str = "knows") -> None:
        for k in range(n):
            other = await ui.save_name(
                self.memory,
                self.campaign,
                f"{'Hidden' if secret else 'Pal'} {predicate} {k}",
                "npc",
                [],
                [],
            )
            await self.memory.add_relation(
                GUILD, self.campaign.id, self.bell.id, predicate, other.id,
                source="dm", confidence=1.0, status=CONFIRMED, secret=secret,
            )  # fmt: skip
        self.fresh()

    async def test_a_secret_connection_is_listed_for_a_dm_and_never_for_anyone_else(self) -> None:
        from dmbot.ui import name_card

        await self.friends(5, secret=True)
        dm_it = self.it()
        await name_card.show_section(dm_it, self.campaign.id, self.bell.id, "links")
        self.assertIn("🤫", dm_it.response.sent[0][0])
        for who in (MANAGER, STRANGER):
            it = self.it(who)
            await name_card.show_section(it, self.campaign.id, self.bell.id, "links")
            self.assertNotIn("Hidden", str(it.response.sent))

    async def test_a_group_of_three_is_not_cut_and_a_group_of_four_is(self) -> None:
        from dmbot.ui import name_card

        await self.friends(3)
        names = await self.bot.lookup.get(GUILD, self.campaign.id)  # type: ignore[union-attr]
        rel = await self.memory.relations(GUILD, self.campaign.id, entity_id=self.bell.id)
        self.assertEqual(name_card.cut_sections(names, self.bell.id, rel, secrets=False), [])
        await self.friends(1, predicate="serves")  # a second group; each is still three or fewer
        names = await self.bot.lookup.get(GUILD, self.campaign.id)  # type: ignore[union-attr]
        rel = await self.memory.relations(GUILD, self.campaign.id, entity_id=self.bell.id)
        self.assertEqual(name_card.cut_sections(names, self.bell.id, rel, secrets=False), [])
        await self.friends(1, predicate="serves")
        await self.friends(1, predicate="serves")
        await self.friends(1, predicate="serves")  # now one group holds four
        names = await self.bot.lookup.get(GUILD, self.campaign.id)  # type: ignore[union-attr]
        rel = await self.memory.relations(GUILD, self.campaign.id, entity_id=self.bell.id)
        self.assertEqual(
            [label for _, label in name_card.cut_sections(names, self.bell.id, rel, secrets=False)],
            ["Connections (7)"],  # the count covers every connection, not only the cut group
        )

    async def test_an_unknown_section_or_a_gone_name_says_so(self) -> None:
        from dmbot.ui import name_card

        it = self.it()
        await name_card.show_section(it, self.campaign.id, self.bell.id, "bogus")
        self.assertEqual(it.response.sent[0][0], name_card.NOTHING_MORE)
        it = self.it()
        await name_card.show_section(it, self.campaign.id, "no-such-name", "names")
        self.assertEqual(it.response.sent[0][0], ui.GONE)

    async def test_the_earlier_button_goes_back(
        self,
    ) -> None:
        from dmbot.ui import name_card

        await self.add_names(30)
        it = self.it()
        await name_card.show_section(it, self.campaign.id, self.bell.id, "names", page=1)
        view = it.response.sent[0][1]["view"]
        self.assertEqual([c.label for c in view.children], ["◀ Earlier"])  # the last page
        back = self.it()
        await view._earlier(back)
        self.assertIn("Page 1 of 2", back.response.edited[0][0])

    async def test_the_type_ahead_suggests_nothing_to_strangers(self) -> None:
        from dmbot.ui import name_card

        self.assertEqual(await name_card.find_typeahead(self.it(STRANGER), "bell"), [])
        choices = await name_card.find_typeahead(self.it(), "bell")
        self.assertEqual([c.value for c in choices], [self.bell.id])
        hidden = await name_card.find_typeahead(self.it(MANAGER), "hooded")
        self.assertEqual(hidden, [])  # secret names only for the campaign's DMs
        plain = await name_card.find_typeahead(self.it(MANAGER), "bell")
        self.assertEqual([c.value for c in plain], [self.bell.id])  # the rest, yes

    async def test_the_type_ahead_never_waits_past_discords_limit(self) -> None:
        # #581: a copy still loading gives nothing this time, and the next keystroke has it.
        from dmbot.ui import name_card

        cache = self.bot.lookup
        assert cache is not None
        cache.mark_all_stale()
        with patch.object(name_card, "TYPEAHEAD_WAIT_S", 0):
            self.assertEqual(await name_card.find_typeahead(self.it(), "bell"), [])
            for _ in range(50):  # the load carries on without anyone waiting
                if await cache.get_within(self.campaign.guild_id, self.campaign.id, 0.05):
                    break
            else:
                self.fail("the names never finished loading")
            choices = await name_card.find_typeahead(self.it(), "bell")
        self.assertEqual([c.value for c in choices], [self.bell.id])


class Lists(NamesTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        from dmbot.memory.lookup import LookupCache

        self.bot.lookup = LookupCache(self.memory)
        self.bell = await ui.save_name(
            self.memory, self.campaign, "Belleros", "npc", [], ["the stranger"]
        )

    def fresh(self) -> None:
        self.bot.lookup.mark_all_stale()  # type: ignore[union-attr]

    async def card(self) -> str:
        from dmbot.ui import name_card

        self.fresh()
        it = self.it()
        await name_card.show_card(it, self.campaign.id, self.bell.id)
        return str(it.response.sent[0][0])

    async def test_add_many_saves_skips_and_asks_about_unclear_ones(self) -> None:
        from dmbot.ui import name_lists

        self.fresh()
        it = self.it()
        text = "# note\nBryn Shander | place | Bryn\nBelleros | npc\nUlfgar\nBell Eros | npc"
        await name_lists.import_list(it, self.campaign.id, text)
        sent = it.followup.send.call_args_list
        summary = sent[0].args[0]
        self.assertIn(
            "📥 **Added 3 names** · 1 already known · 1 looks like a known name. Questions below.",
            summary,
        )
        self.assertIn("Only one name wrong? Fix or remove it with 🔍 Find a name", summary)
        self.assertIn("1 name needs you to check it", summary)  # Ulfgar: no kind
        self.assertIn("1. **Bell Eros** → **Belleros**?", sent[1].args[0])  # asked, not joined
        self.assertIn("Bryn Shander", await self.names())
        self.assertIn("Ulfgar", await self.names((PROPOSED,)))
        self.assertIn("Bell Eros", await self.names((PROPOSED,)))
        view = sent[0].kwargs["view"]
        (undo,) = [c for c in view.children if isinstance(c, name_lists.UndoListButton)]
        it = self.it()
        it.edit_original_response = AsyncMock()
        await undo.callback(it)
        self.assertIn(
            "Took the whole list back", it.edit_original_response.call_args.kwargs["content"]
        )
        self.assertNotIn("Bryn Shander", await self.names())

    async def test_undo_for_a_list_says_when_it_is_too_late(self) -> None:
        from dmbot.ui import name_lists

        self.fresh()
        it = self.it()
        await name_lists.import_list(it, self.campaign.id, "Bryn Shander | place")
        self.assertIn("stops working after 30 days, or once", it.followup.send.call_args.args[0])
        view = it.followup.send.call_args.kwargs["view"]
        (undo,) = [c for c in view.children if isinstance(c, name_lists.UndoListButton)]
        await self.forget_old_undo()
        it = self.it()
        await undo.callback(it)
        self.assertEqual(
            it.followup.send.call_args.args[0],
            "Too late to undo: Undo works for 30 days, and not from before a backup was loaded. "
            "Remove the wrong names from their cards (`/dmbot names`).",
        )
        self.assertIn("Bryn Shander", await self.names())

    async def add_many(self, text: str) -> list[Any]:
        from dmbot.ui import name_lists

        self.fresh()
        it = self.it()
        await name_lists.import_list(it, self.campaign.id, text)
        return list(it.followup.send.call_args_list)

    async def press(self, view: Any, label: str) -> Any:
        (button,) = [c for c in view.children if getattr(c, "label", "") == label][:1]
        it = self.it()
        it.edit_original_response = AsyncMock()
        await button.callback(it)
        return it

    async def test_a_known_name_gets_its_new_other_names_and_undo_takes_them_back(self) -> None:
        from dmbot.ui import name_lists

        sent = await self.add_many("Belleros | NPC | the old knight, Bell")
        summary = sent[0].args[0]
        self.assertIn(
            "No new names were added** · 1 already known (1 got new other names)", summary
        )
        self.assertIn("the old knight", await self.card())
        (undo,) = [
            c for c in sent[0].kwargs["view"].children if isinstance(c, name_lists.UndoListButton)
        ]
        it = self.it()
        it.edit_original_response = AsyncMock()
        await undo.callback(it)
        self.assertNotIn("the old knight", await self.card())
        self.assertIn("Belleros", await self.names())  # the known name stays

    async def test_a_swapped_name_and_other_name_changes_nothing(self) -> None:
        await self.add_many("Belleros | NPC | Bell")
        sent = await self.add_many("Bell | NPC | Belleros")
        self.assertIn(
            "**Bell** is already another name for **Belleros**. Nothing changed.", sent[0].args[0]
        )
        self.assertEqual(len(sent), 1)

    async def test_a_near_name_is_asked_and_same_joins_it(self) -> None:
        sent = await self.add_many("Beleros | NPC")
        view = sent[1].kwargs["view"]
        self.assertIn("Beleros", await self.names((PROPOSED,)))  # not joined on its own
        it = await self.press(view, "1. Same")
        self.assertIn(
            "now another name for **Belleros**",
            it.edit_original_response.call_args.kwargs["content"],
        )
        self.assertNotIn("Beleros", await self.names((PROPOSED,)))
        self.assertIn("Beleros", await self.card())  # one of Belleros's other names now

    async def test_different_keeps_both_and_remove_removes(self) -> None:
        sent = await self.add_many("Beleros | NPC\nBellros | NPC")
        view = sent[1].kwargs["view"]
        await self.press(view, "1. Different")
        self.assertIn("Beleros", await self.names())  # confirmed: both kept
        it = await self.press(view, "2. Remove")
        self.assertNotIn("Bellros", await self.names((PROPOSED,)))
        self.assertIn("All checked.", it.edit_original_response.call_args.kwargs["content"])
        undo_message = it.followup.send.call_args
        self.assertIn("Removed **Bellros**. Wrong? **Undo**", undo_message.args[0])

    async def test_a_manager_folding_in_a_secret_name_sees_what_any_name_gives(self) -> None:
        from dmbot.ui import name_lists

        def first_line(it: Any) -> str:
            return str(it.followup.send.call_args_list[0].args[0].splitlines()[0])

        results = []
        for other in ("the stranger", "the old knight"):  # a secret name, then a new one
            self.fresh()
            it = self.it(MANAGER)
            await name_lists.import_list(it, self.campaign.id, f"Belleros | NPC | {other}")
            results.append(first_line(it))
        self.assertEqual(results[0], results[1])
        self.assertNotIn("the stranger", await self.card_for(MANAGER))

    async def card_for(self, user: int) -> str:
        from dmbot.ui import name_card

        self.fresh()
        it = self.it(user)
        await name_card.show_card(it, self.campaign.id, self.bell.id)
        return str(it.response.sent[0][0])

    async def test_different_for_all_and_more(self) -> None:
        text = "\n".join(
            f"{n} | NPC" for n in ("Beleros", "Bellros", "Bellerose", "Bellleros", "Belleross")
        )
        sent = await self.add_many(text)
        view = sent[1].kwargs["view"]
        self.assertIn("1 more after these: press **More ▶**.", sent[1].args[0])
        it = await self.press(view, "More ▶")
        self.assertIn("5. **Belleross** → **Belleros**?", it.response.edited[0][0])
        it = await self.press(view, "Different for all 5")
        self.assertIn("All checked.", it.edit_original_response.call_args.kwargs["content"])
        names = await self.names()
        for name in ("Beleros", "Bellros", "Bellerose", "Bellleros", "Belleross"):
            self.assertIn(name, names)

    async def test_keep_all_and_change_all(self) -> None:
        from dmbot.ui import names as panel

        await panel.save_name(self.memory, self.campaign, "Auril", "deity", [], [])
        await panel.save_name(self.memory, self.campaign, "Ulfgar", "npc", [], [])
        sent = await self.add_many("Auril | place\nUlfgar | place")
        view = sent[1].kwargs["view"]
        self.assertIn(panel.KIND_SHORT["deity"], sent[1].args[0])
        it = await self.press(view, "Change all")
        content = it.edit_original_response.call_args.kwargs["content"]
        self.assertEqual(content.count("✅ Changed 1 to place."), 2)
        self.assertIn("All checked.", content)

    async def test_a_long_summary_still_fits_discord(self) -> None:
        from dmbot.ui import name_lists

        long = ["x" * 100 + str(i) for i in range(20)]
        text = name_lists.summary_text(
            3, 0, 20, 0, 0, [(i, "a" * 300) for i in range(5)],
            swapped=[(n, n + "y") for n in long],
        )  # fmt: skip
        self.assertLessEqual(len(text), 2000)

    async def test_same_for_all(self) -> None:
        sent = await self.add_many("Beleros | NPC\nBellros | NPC\nBellerose | NPC")
        view = sent[1].kwargs["view"]
        self.assertIn("3 names look like names DMbot already knows", sent[1].args[0])
        it = await self.press(view, "Same for all 3")
        self.assertIn(
            "Joined 3 names. Wrong? Open the known name", it.followup.send.call_args.args[0]
        )
        card = await self.card()
        for name in ("Beleros", "Bellros", "Bellerose"):
            self.assertIn(name, card)

    async def test_a_kind_that_differs_is_kept_unless_changed(self) -> None:
        sent = await self.add_many("Belleros | place")
        self.assertIn("1 kind differs. Questions below.", sent[0].args[0])
        self.assertIn("1. **Belleros**: DMbot has NPC, your list says place.", sent[1].args[0])
        self.assertIn("🪪 **Belleros** · character · NPC", await self.card())  # kept by default
        await self.press(sent[1].kwargs["view"], "1. Change to place")
        self.assertIn("🪪 **Belleros** · place", await self.card())

    async def test_the_owners_bulk_import_test_reads_as_its_setup_says(self) -> None:
        """docs/test-scripts/bakeoff-story-names-setup.md, step 1 then step 2 (#368)."""
        first = script_file("bakeoff-story-names-setup.md").split("```")[1].strip()
        await self.memory.set_entity_status(
            GUILD, self.campaign.id, self.bell.id, REJECTED, source="dm"
        )  # a new campaign: only the setup's names
        sent = await self.add_many(first)
        self.assertEqual(sent[0].args[0].splitlines()[0], "📥 **Added 10 names.**")
        sent = await self.add_many(script_file("bakeoff-story-names.txt"))
        self.assertEqual(
            sent[0].args[0].splitlines()[:3],
            [
                "📥 **Added 22 names** · 4 already known · 3 look like known names · 2 kinds "
                "differ. Questions below.",
                "• **Bell** is already another name for **Belleros**. Nothing changed.",
                "• **Vane** is already another name for **Oskar Vane**. Nothing changed.",
            ],
        )
        near = sent[1].args[0]
        for question in ("**Quilon** → **Quillon**?", "**Brynnwater** → **Brynwater**?",
                         "**Gorrack** → **Gorrak**?"):  # fmt: skip
            self.assertIn(question, near)
        self.assertIn("**Varrow**: DMbot has god, your list says place.", sent[2].args[0])
        self.assertIn("**Ashen Crown**: DMbot has group, your list says place.", sent[2].args[0])

    async def test_an_unknown_kind_is_asked_once_for_all_its_names(self) -> None:
        from dmbot.ui import name_lists

        self.fresh()
        it = self.it()
        lines = "\n".join(f"Mage {n} | wizard" for n in range(3)) + "\nTarn | wizard"
        await name_lists.import_list(it, self.campaign.id, lines)
        sent = it.followup.send.call_args
        self.assertIn("**wizard** (4)", sent.args[0])
        (select,) = [
            c for c in sent.kwargs["view"].children if isinstance(c, name_lists.KindSelect)
        ]
        it = self.it()
        it.message = SimpleNamespace(content=sent.args[0])
        await sent.kwargs["view"].picked(
            SimpleNamespace(values=["npc"], ids=select.ids, word=select.word),
            it,
        )
        saving, saved = it.response.edited  # menus away while saving, then the answer (#537)
        self.assertEqual((saving[0].endswith(name_lists.SAVING), saving[1]), (True, None))
        self.assertIn("Every **wizard**: 4 names set to", saved[0])
        self.assertEqual(
            {f"Mage {n}" for n in range(3)} | {"Tarn"},
            set(await self.names()) - {"Belleros"},
        )

    async def test_a_list_that_fits_is_added_without_the_ai(self) -> None:
        from dmbot.ui import name_lists

        fake: Any = SimpleNamespace(complete=AsyncMock())
        self.bot.ai = fake
        self.fresh()
        it = self.it()
        await name_lists.take_list(it, self.campaign.id, name_lists.Upload("Ulfgar | NPC", "x"))
        self.assertIn("Added 1 name", it.followup.send.call_args.args[0])
        fake.complete.assert_not_called()

    async def test_a_document_goes_to_the_ai_and_the_dm_sees_the_list_first(self) -> None:
        from dmbot.ui import name_lists

        reply = (
            "Here you go:\nUlfgar | NPC | Ulf\nBryn Shander | place\nKesh | NPC | | the stranger"
        )
        fake: Any = SimpleNamespace(complete=AsyncMock(return_value=Reply(reply, cut=False)))
        self.bot.ai = fake
        self.fresh()
        it = self.it(MANAGER)  # not one of the campaign's DMs: no secret names
        upload = name_lists.Upload(
            "Ulfgar, chief of the Frostwolves, lives in Bryn Shander.", "npcs.pdf", True
        )
        await name_lists.take_list(it, self.campaign.id, upload)
        text, kw = it.response.sent[0]
        self.assertIn("goes to Anthropic", text)
        self.assertIn("right to use", text)
        offer = kw["view"]
        store = self.bot.campaigns
        self.assertEqual(await store.confirmations(GUILD, self.campaign.id), [])  # not yet
        it = self.it(MANAGER)
        it.edit_original_response = AsyncMock()
        await offer._read(it)
        # #252: who confirmed the right to use it, when, and what for, kept with the
        # campaign; a fingerprint of the text, never the text or the file's name.
        from dmbot.campaigns.models import fingerprint

        (row,) = await store.confirmations(GUILD, self.campaign.id)
        self.assertEqual(
            (row["user"], row["purpose"], row["fingerprint"]),
            (str(MANAGER), "names_list", fingerprint(upload.text)),
        )
        self.assertNotIn("npcs", str(row))
        elsewhere = await store.create(GUILD, "Strahd", DM)
        self.assertEqual(await store.confirmations(GUILD, elsewhere.id), [])  # this one only
        system, sent = fake.complete.call_args.args
        self.assertIn("<document>", sent)
        self.assertIn("Leave out disguises", system)
        preview = it.edit_original_response.call_args.kwargs
        self.assertIn("found 2 names", preview["content"])  # the secret line was refused
        self.assertIn("Nothing is added until you press", preview["content"])
        self.assertNotIn("Ulfgar", await self.names())  # nothing saved yet
        it = self.it(MANAGER)
        await preview["view"]._add(it)
        self.assertIn("Added 2 names", it.followup.send.call_args.args[0])
        self.assertIn("Ulfgar", await self.names())

    async def test_nothing_is_recorded_without_the_press(self) -> None:
        from dmbot.ui import name_lists

        self.bot.ai = SimpleNamespace(complete=AsyncMock())  # type: ignore[assignment]
        self.fresh()
        upload = name_lists.Upload("Ulfgar lives here.", "npcs.pdf", True)
        for way_out in ("_cancel", "_as_is"):
            with self.subTest(way_out=way_out):
                it = self.it()
                await name_lists.take_list(it, self.campaign.id, upload)
                offer = it.response.sent[0][1]["view"]
                it = self.it()
                await getattr(offer, way_out)(it)
        self.assertEqual(await self.bot.campaigns.confirmations(GUILD, self.campaign.id), [])

    async def test_no_ai_reading_without_a_saved_confirmation(self) -> None:
        from dmbot.ui import name_lists

        fake: Any = SimpleNamespace(complete=AsyncMock())
        self.bot.ai = fake
        self.fresh()
        it = self.it()
        upload = name_lists.Upload("Ulfgar lives here.", "npcs.pdf", True)
        await name_lists.take_list(it, self.campaign.id, upload)
        offer = it.response.sent[0][1]["view"]
        it = self.it()
        it.edit_original_response = AsyncMock()
        with (
            patch.object(
                self.bot.campaigns, "record_confirmation", AsyncMock(side_effect=OSError("db"))
            ),
            self.assertLogs("dmbot.ui.name_lists", "INFO") as logs,
        ):
            await offer._read(it)
        fake.complete.assert_not_called()  # the IP rule: no record, no reading
        self.assertIn("Nothing was added", it.edit_original_response.call_args.kwargs["content"])
        self.assertFalse(any("confirmed" in line for line in logs.output))  # log agrees
        # The campaign was deleted meanwhile: its own words, and still no reading.
        from dmbot.campaigns.models import CampaignError

        it = self.it()
        await name_lists.take_list(it, self.campaign.id, upload)
        offer = it.response.sent[0][1]["view"]
        it = self.it()
        it.edit_original_response = AsyncMock()
        gone = CampaignError("That campaign doesn't exist in this server.")
        with patch.object(self.bot.campaigns, "record_confirmation", AsyncMock(side_effect=gone)):
            await offer._read(it)
        fake.complete.assert_not_called()
        self.assertEqual(it.edit_original_response.call_args.kwargs["content"], str(gone))

    async def test_ai_problems_say_nothing_was_added(self) -> None:
        from dmbot.ai import BUSY, AIError
        from dmbot.ui import name_lists

        fake: Any = SimpleNamespace(complete=AsyncMock(side_effect=AIError(BUSY)))
        self.bot.ai = fake
        self.fresh()
        it = self.it()
        upload = name_lists.Upload("Ulfgar lives here.", "npcs.pdf", True)
        await name_lists.take_list(it, self.campaign.id, upload)
        offer = it.response.sent[0][1]["view"]
        it = self.it()
        it.edit_original_response = AsyncMock()
        await offer._read(it)
        self.assertIn("Nothing was added", it.edit_original_response.call_args.kwargs["content"])
        self.assertEqual(name_lists._ai_busy, set())  # free for the next one

    async def test_an_empty_list_or_a_managers_secret_line_never_goes_to_the_ai(self) -> None:
        from dmbot.ui import name_lists

        fake: Any = SimpleNamespace(complete=AsyncMock())
        self.bot.ai = fake
        self.fresh()
        it = self.it()
        await name_lists.take_list(it, self.campaign.id, name_lists.Upload("# only notes", "x"))
        self.assertIn("There are no names", it.response.sent[0][0])
        it = self.it(MANAGER)
        text = "Kesh | NPC\nTarn | NPC | | a disguise"
        await name_lists.take_list(it, self.campaign.id, name_lists.Upload(text, "x"))
        self.assertIn("Added 1 name", it.followup.send.call_args.args[0])
        fake.complete.assert_not_called()

    async def test_a_list_with_errors_goes_to_the_ai_with_the_option_to_add_what_fits(self) -> None:
        from dmbot.ui import name_lists

        fake: Any = SimpleNamespace(complete=AsyncMock())
        self.bot.ai = fake
        self.fresh()
        it = self.it()
        text = "Ulfgar | NPC\nBryn Shander | place | Bryn | x | notes about the town"
        await name_lists.take_list(it, self.campaign.id, name_lists.Upload(text, "your list"))
        message, kw = it.response.sent[0]
        self.assertIn("1 line in your list doesn't fit", message)
        labels = [getattr(c, "label", "") for c in kw["view"].children]
        self.assertIn("Add the 1 that fit", labels)
        self.bot.ai = None
        it = self.it()  # no AI switched on: what fits is added, the rest explained
        await name_lists.take_list(it, self.campaign.id, name_lists.Upload(text, "your list"))
        self.assertIn("1 line wasn't added", it.followup.send.call_args.args[0])

    async def test_a_manager_never_learns_of_or_adds_secret_names(self) -> None:
        from dmbot.ui import name_lists

        self.fresh()
        it = self.it(MANAGER)
        await name_lists.import_list(
            it, self.campaign.id, "the stranger | npc\nKesh | npc | | a secret"
        )
        summary = it.followup.send.call_args.args[0]
        # A clash with a secret name looks exactly like no clash: saved like any other.
        self.assertIn("Added 1 name.", summary)
        self.assertEqual(it.followup.send.call_count, 1)  # nothing asked about it
        self.assertNotIn("need you to check", summary)
        self.assertIn("only the campaign's DM can add secret names", summary)
        self.fresh()
        it = self.it(MANAGER)
        await name_lists.send_download(it, self.campaign.id)
        text = it.followup.send.call_args.args[0]
        body = it.followup.send.call_args.kwargs["files"][0].fp.getvalue()
        self.assertNotIn("secret", text)
        self.assertNotIn(b"secret names", body)  # not even the column's instructions
        self.assertNotIn(b"Belleros | NPC | the stranger", body)

    async def test_download_has_the_secrets_for_the_dm_and_reads_back(self) -> None:
        from dmbot.memory.name_list import parse
        from dmbot.ui import name_lists

        self.fresh()
        it = self.it()
        await name_lists.send_download(it, self.campaign.id)
        text = it.followup.send.call_args.args[0]
        self.assertIn("Includes secret names", text)
        body = it.followup.send.call_args.kwargs["files"][0].fp.getvalue().decode()
        (line,) = parse(body, secrets=True).lines
        self.assertEqual((line.name, line.secrets), ("Belleros", ("the stranger",)))

    # ---- players and DMs in lists (#1559) -------------------------------------------------

    MIA_ID = 111111111111111234
    BEN_ID = 222222222222225678

    def with_members(self, it: Any, members: dict[int, bool]) -> Any:
        """A guild where these ids are members (True: a bot)."""
        import discord as d

        async def fetch_member(user_id: int) -> Any:
            if user_id not in members:
                raise d.NotFound(MagicMock(status=404, reason="x"), "Unknown Member")
            return SimpleNamespace(id=user_id, bot=members[user_id])

        it.guild = SimpleNamespace(id=GUILD, fetch_member=fetch_member, get_member=lambda _id: None)
        return it

    async def person_names(self, user_id: int) -> list[str]:
        rows = await self.memory.table_people(GUILD, self.campaign.id)
        return next((also for u, _, also in rows if u == user_id), [])

    async def test_a_dms_download_lists_people_with_ids_and_a_players_does_not(self) -> None:
        from dmbot.memory.name_list import parse
        from dmbot.ui import name_lists

        cerric = await ui.save_name(self.memory, self.campaign, "Cerric", "npc", [], [])
        await self.memory.confirm_entity(
            GUILD, self.campaign.id, cerric.id, "player_character", source="dm",
            played_by=self.MIA_ID,
        )  # fmt: skip
        await self.memory.add_person_names(GUILD, self.campaign.id, self.MIA_ID, ["Mike", "Mikey"])
        self.fresh()
        it = self.it()
        await name_lists.send_download(it, self.campaign.id)
        body = it.followup.send.call_args.kwargs["files"][0].fp.getvalue().decode()
        self.assertIn(f"Mike | player or DM | Mikey |  | {self.MIA_ID} | Cerric", body)
        self.assertIn(
            "who: their Discord id, a long number", body
        )  # the header says what the column is
        parsed = parse(body, secrets=True)
        self.assertEqual(
            [(r.who, r.names, r.plays) for r in parsed.people],
            [(self.MIA_ID, ("Mike", "Mikey"), ("Cerric",))],
        )
        self.assertEqual(parsed.refused, [])
        player = self.it(MANAGER)  # someone who may download but is not a DM
        await name_lists.send_download(player, self.campaign.id)
        text = player.followup.send.call_args.kwargs["files"][0].fp.getvalue().decode()
        self.assertNotIn(str(self.MIA_ID), text)
        self.assertNotIn("player or DM", text)
        self.assertNotIn("who: their Discord", text)

    async def test_a_reloaded_file_round_trips_a_person_without_doubling_names(self) -> None:
        from dmbot.ui import name_lists

        await self.memory.add_person_names(GUILD, self.campaign.id, self.MIA_ID, ["Mike"])
        self.fresh()
        it = self.it()
        await name_lists.send_download(it, self.campaign.id)
        body = it.followup.send.call_args.kwargs["files"][0].fp.getvalue().decode()
        again = self.with_members(self.it(), {self.MIA_ID: False})
        await name_lists.import_list(
            again, self.campaign.id, body + f"\nMike | player or DM | Mikey | | {self.MIA_ID}\n"
        )
        self.assertEqual(sorted(await self.person_names(self.MIA_ID)), ["Mike", "Mikey"])
        again = self.with_members(self.it(), {self.MIA_ID: False})
        await name_lists.import_list(again, self.campaign.id, body)
        self.assertEqual(
            sorted(await self.person_names(self.MIA_ID)), ["Mike", "Mikey"]
        )  # no doubles

    async def test_a_bot_an_outsider_and_a_row_without_who_are_refused_by_row(self) -> None:
        from dmbot.ui import name_lists

        bot_id, outsider = 333333333333339999, 444444444444448888
        text = (
            f"Mike | player or DM | | | {self.MIA_ID}\n"
            f"Robo | player or DM | | | {bot_id}\n"
            f"Ghost | player or DM | | | {outsider}\n"
            "Nobody | player or DM\n"
            "Bryn Shander | place\n"
        )
        it = self.with_members(self.it(), {self.MIA_ID: False, bot_id: True})
        await name_lists.import_list(it, self.campaign.id, text)
        summary = it.followup.send.call_args_list[0].args[0]
        self.assertIn("Players and DMs:** 1 updated", summary)
        self.assertIn("line 2: that is a bot, not a person", summary)
        self.assertIn("line 3: that Discord id isn't in this server", summary)
        self.assertIn("line 4: a player or DM needs their Discord id", summary)
        self.assertEqual(await self.person_names(self.MIA_ID), ["Mike"])
        self.assertIn("Bryn Shander", await self.names())  # the rest still loaded

    async def test_people_rows_from_someone_who_is_not_a_dm_are_refused(self) -> None:
        from dmbot.ui import name_lists

        it = self.with_members(self.it(MANAGER), {self.MIA_ID: False})
        await name_lists.import_list(
            it, self.campaign.id, f"Mike | player or DM | | | {self.MIA_ID}"
        )
        self.assertEqual(await self.person_names(self.MIA_ID), [])

    async def test_a_character_without_a_player_is_linked_and_others_are_flagged(self) -> None:
        from dmbot.ui import name_lists

        cerric = await ui.save_name(
            self.memory, self.campaign, "Cerric", "player_character", [], []
        )
        taken = await ui.save_name(self.memory, self.campaign, "Maren", "npc", [], [])
        await self.memory.confirm_entity(
            GUILD, self.campaign.id, taken.id, "player_character", source="dm",
            played_by=self.BEN_ID,
        )  # fmt: skip
        it = self.with_members(self.it(), {self.MIA_ID: False})
        await name_lists.import_list(
            it,
            self.campaign.id,
            f"Mike | player or DM | | | {self.MIA_ID} | Cerric; Maren; Nobody Known",
        )
        summary = it.followup.send.call_args_list[0].args[0]
        self.assertIn("Linked to Cerric", summary)
        self.assertIn("Not linked", summary)
        self.assertIn("Maren, Nobody Known", summary)
        rows = await self.memory.table_people(GUILD, self.campaign.id)
        self.assertEqual([c for u, c, _ in rows if u == self.MIA_ID], [["Cerric"]])
        ben = await self.memory.entity(GUILD, self.campaign.id, taken.id)
        self.assertEqual(ben.played_by if ben else None, self.BEN_ID)  # not taken from Ben
        mia = next(
            e for e in await self.memory.entities(GUILD, self.campaign.id)
            if e.type == "person" and e.played_by == self.MIA_ID
        )  # fmt: skip
        self.assertTrue(mia.needs_look)  # the flag the learner uses
        self.assertIsNotNone(cerric)

    async def test_an_npc_is_never_turned_into_a_player_character_by_a_list(self) -> None:
        from dmbot.ui import name_lists

        bell = await ui.save_name(self.memory, self.campaign, "Kesh", "npc", [], [])
        it = self.with_members(self.it(), {self.MIA_ID: False})
        await name_lists.import_list(
            it, self.campaign.id, f"Mike | player or DM | | | {self.MIA_ID} | Kesh"
        )
        self.assertIn("Not linked: Kesh", it.followup.send.call_args_list[0].args[0])
        entity = await self.memory.entity(GUILD, self.campaign.id, bell.id)
        self.assertEqual((entity.role, entity.played_by), ("npc", None))  # type: ignore[union-attr]

    async def test_a_name_dmbot_only_guessed_is_confirmed_when_the_dm_lists_it(self) -> None:
        from dmbot.memory.models import PROPOSED

        written = await self.memory.add_person_names(GUILD, self.campaign.id, self.MIA_ID, ["Mike"])
        self.assertEqual(written.value.names_added, 1)
        person = next(
            e for e in await self.memory.entities(GUILD, self.campaign.id) if e.type == "person"
        )
        alias = await self.memory.add_alias(
            GUILD, self.campaign.id, person.id, "Mikey", kind="nickname", source="scan",
            status=PROPOSED,
        )  # fmt: skip
        self.assertIsNotNone(alias)
        again = await self.memory.add_person_names(
            GUILD, self.campaign.id, self.MIA_ID, ["Mike", "Mikey"]
        )
        self.assertEqual(again.value.names_added, 1)  # Mikey, now the DM's word
        self.assertEqual(sorted(await self.person_names(self.MIA_ID)), ["Mike", "Mikey"])

    async def test_a_list_with_people_never_goes_to_the_ai(self) -> None:
        from dmbot.ui import name_lists

        self.bot.ai = MagicMock()
        it = self.with_members(self.it(), {self.MIA_ID: False})
        upload = name_lists.Upload(
            f"Mike | player or DM | | | {self.MIA_ID}\n"
            "this line | has | too | many | parts | here | now",
            "names.txt",
        )
        await name_lists.take_list(it, self.campaign.id, upload)
        self.bot.ai.complete.assert_not_called()
        summary = it.followup.send.call_args_list[0].args[0]
        self.assertIn("Players and DMs", summary)
        self.assertNotIn(str(self.MIA_ID), summary)

    async def test_player_rows_in_an_ai_reply_are_not_read(self) -> None:
        from dmbot.memory.name_list import parse

        parsed = parse(f"Mike | player or DM | | | {self.MIA_ID}", secrets=True, people=False)
        self.assertEqual(parsed.people, [])
        self.assertEqual(len(parsed.refused), 1)

    async def test_too_many_other_names_for_one_name_adds_nothing(self) -> None:
        from dmbot.memory.name_list import lines_for

        others = [f"Kesh{i}" for i in range(51)]  # on three lines: still one name
        sent = await self.add_many("\n".join(lines_for("Kesh", "npc", others, [])))
        self.assertIn("Nothing was added. Kesh has more than 50 other names", sent[0].args[0])
        self.assertNotIn("Kesh", await self.names())

    async def test_a_campaign_over_the_cap_takes_its_own_download_back(self) -> None:
        from dmbot.ui import name_lists

        # More than a list may add (saved another way): Download all, then upload it.
        await ui.save_name(
            self.memory, self.campaign, "Kesh", "npc", [f"Kesh{i}" for i in range(60)], []
        )
        self.fresh()
        it = self.it()
        await name_lists.send_download(it, self.campaign.id)
        body = it.followup.send.call_args.kwargs["files"][0].fp.getvalue().decode()
        sent = await self.add_many(body)
        self.assertNotIn("Nothing was added", sent[0].args[0])
        self.assertIn("already known", sent[0].args[0])

    async def test_a_small_download_is_one_file_with_no_number(self) -> None:
        from dmbot.ui import name_lists

        self.fresh()
        it = self.it()
        await name_lists.send_download(it, self.campaign.id)
        self.assertEqual(it.followup.send.call_count, 1)
        sent = it.followup.send.call_args
        (file,) = sent.kwargs["files"]
        self.assertRegex(file.filename, r"^names-.*-\d{4}-\d{2}-\d{2}\.txt$")
        self.assertIn("All 1 name for", sent.args[0])
        self.assertNotIn("files", sent.args[0])

    async def test_a_big_download_comes_as_files_that_each_add_back(self) -> None:
        from unittest.mock import patch

        from dmbot.memory.name_list import parse
        from dmbot.ui import name_lists

        for n in range(40):
            await ui.save_name(
                self.memory, self.campaign, f"Place {n:02}", "place", [f"Spot {n:02}"], []
            )
        self.fresh()
        it = self.it()
        # A small line limit makes 41 names too many for one file, as 10,000 are for 2,000.
        with patch("dmbot.memory.name_list.MAX_LINES", 70):
            await name_lists.send_download(it, self.campaign.id)
        self.assertEqual(it.followup.send.call_count, 1)  # fits one message
        sent = it.followup.send.call_args
        files = sent.kwargs["files"]
        self.assertGreater(len(files), 1)
        self.assertIn(f"All 41 names for {self.campaign.name}", sent.args[0])
        self.assertIn(f"in {len(files)} files.**", sent.args[0])
        self.assertIn("upload them one at a time", sent.args[0])
        self.assertIn("in any order", sent.args[0])
        self.assertNotIn("too small", sent.args[0])
        self.assertIn("Includes secret names: don't share these files", sent.args[0])
        self.assertEqual(len({f.filename for f in files}), len(files))
        bodies = [f.fp.getvalue().decode() for f in files]
        restored = [line.name for body in bodies for line in parse(body, secrets=True).lines]
        self.assertEqual(len(restored), 41)
        self.assertEqual(len(set(restored)), 41)  # each name in one file only
        for body in reversed(bodies):  # every file adds back, in any order
            sent_back = await self.add_many(body)
            self.assertNotIn("Nothing was added", sent_back[0].args[0])
            self.assertIn("already known", sent_back[0].args[0])

    async def test_the_files_add_every_name_into_an_empty_campaign(self) -> None:
        from unittest.mock import patch

        from dmbot.ui import name_lists

        # Names unlike each other: Add many joins look-alikes ("Place 01", "Place 02"), as it
        # should, which would hide what this test is for.
        def word(n: int, tag: str) -> str:
            return (
                hashlib.sha256(f"{tag}{n}".encode())
                .hexdigest()[:10]
                .translate(str.maketrans("0123456789", "qzxwvkjyrp"))
            )

        for n in range(40):
            await ui.save_name(
                self.memory,
                self.campaign,
                word(n, "name"),
                "place",
                [word(n, "other")],
                [word(n, "secret")],
            )
        before = await self.names()
        self.fresh()
        it = self.it()
        with patch("dmbot.memory.name_list.MAX_LINES", 70):
            await name_lists.send_download(it, self.campaign.id)
        files = it.followup.send.call_args.kwargs["files"]
        self.assertGreater(len(files), 1)
        second = await self.campaigns.create(GUILD, "Second", DM)
        for f in reversed(files):  # any order
            into = self.it()
            await name_lists.import_list(into, second.id, f.fp.getvalue().decode())
            self.assertIn("Added", into.followup.send.call_args_list[0].args[0])
        got = {
            e.name: e for e in await self.memory.entities(GUILD, second.id, statuses=[CONFIRMED])
        }
        self.assertEqual(set(got), set(before))

        def pairs(aliases: list[Any]) -> set[tuple[str, bool]]:
            return {(a.text, a.secret) for a in aliases}

        self.assertEqual(
            pairs(await self.memory.aliases(GUILD, second.id, include_secret=True)),
            pairs(await self.memory.aliases(GUILD, self.campaign.id, include_secret=True)),
        )
        self.assertEqual(await self.names(), before)  # the source is unchanged

    async def test_a_full_campaigns_files_all_pass_the_uploads_own_checks(self) -> None:
        from dmbot.memory.name_list import MAX_NAMES, OutName, render_files
        from dmbot.ui import name_lists

        names = [OutName(f"Name {i:05}", "npc", (f"Nick {i:05}",), ()) for i in range(MAX_NAMES)]
        files = render_files(names, campaign="Frostmaiden", secrets=True)
        self.assertGreater(len(files), 1)
        for text in files:
            read, problem = name_lists.read_upload(text.encode("utf-8"))
            self.assertIsNone(problem)
            self.assertEqual(read, text)

    async def test_a_later_batch_that_fails_to_send_says_which_files_are_missing(self) -> None:
        from unittest.mock import patch

        from dmbot.ui import name_lists

        texts = [f"### part {i}\nName {i:02}\n" for i in range(23)]
        self.fresh()
        it = self.it()
        sent: list[Any] = []

        async def send(text: str = "", **kw: Any) -> None:
            sent.append((text, kw))
            if len(sent) == 2:  # the second batch (files 11 to 20)
                raise discord.HTTPException(MagicMock(status=500, reason="x"), "boom")

        it.followup.send = AsyncMock(side_effect=send)
        with (
            patch.object(name_lists, "download_files", return_value=(texts, 23)),
            self.assertLogs("dmbot.ui.name_lists", "WARNING"),
        ):
            await name_lists.send_download(it, self.campaign.id)
        self.assertEqual(len(sent), 3)  # first batch, the failed one, then the warning
        self.assertIn("Files 11 to 23 of 23 didn't arrive", sent[2][0])
        self.assertIn("Press 📤 Download all again", sent[2][0])
        self.assertIn("Files 1 to 10 are fine.", sent[2][0])
        self.assertNotIn("files", sent[2][1])

    async def test_one_missing_last_file_is_named_in_the_singular(self) -> None:
        from unittest.mock import patch

        from dmbot.ui import name_lists

        texts = [f"### part {i}\nName {i:02}\n" for i in range(11)]
        self.fresh()
        it = self.it()
        sent: list[Any] = []

        async def send(text: str = "", **kw: Any) -> None:
            sent.append(text)
            if len(sent) == 2:
                raise discord.HTTPException(MagicMock(status=500, reason="x"), "boom")

        it.followup.send = AsyncMock(side_effect=send)
        with (
            patch.object(name_lists, "download_files", return_value=(texts, 11)),
            self.assertLogs("dmbot.ui.name_lists", "WARNING"),
        ):
            await name_lists.send_download(it, self.campaign.id)
        self.assertIn("File 11 of 11 didn't arrive", sent[2])

    async def test_more_than_ten_files_go_in_more_than_one_message(self) -> None:
        from unittest.mock import patch

        from dmbot.ui import name_lists

        texts = [f"### part {i}\nName {i:02}\n" for i in range(23)]
        self.fresh()
        it = self.it()
        with patch.object(name_lists, "download_files", return_value=(texts, 23)):
            await name_lists.send_download(it, self.campaign.id)
        calls = it.followup.send.call_args_list
        self.assertEqual([len(c.kwargs["files"]) for c in calls], [10, 10, 3])
        self.assertIn("in 23 files (3 messages).**", calls[0].args[0])
        self.assertIn("**Files 11 to 20 of 23**", calls[1].args[0])
        self.assertIn("**Files 21 to 23 of 23**", calls[2].args[0])
        for later in calls[1:]:  # each carries the secrets warning in full
            self.assertIn("Includes secret names: don't share these files", later.args[0])
        names = [f.filename for c in calls for f in c.kwargs["files"]]
        self.assertEqual(names, sorted(names))  # numbered the same width: they sort in order
        self.assertTrue(names[0].startswith("names-01-of-23-"))
        self.assertTrue(names[-1].startswith("names-23-of-23-"))

    async def test_browse_by_kind_pages_and_opens_names(self) -> None:
        from dmbot.ui import name_lists

        for n in range(25):
            await ui.save_name(self.memory, self.campaign, f"Place {n:02}", "place", [], [])
        self.fresh()
        it = self.it()
        await name_lists.show_browse(it, self.campaign.id)
        text, kw = it.response.sent[0]
        self.assertIn("pick a kind", text)
        view = kw["view"]
        view.pick = SimpleNamespace(values=["place"])
        it = self.it()
        await view._kind_picked(it)
        text, page = it.response.edited[0]
        self.assertIn("page 1 of 2", text)
        self.assertEqual(len(page.shown), 20)
        it = self.it()
        await page._sort(it)  # A to Z
        text, page = it.response.edited[0]
        self.assertIn("**Place 00**", text)
        it = self.it()
        await page._next_page(it)
        text, _ = it.response.edited[0]
        self.assertIn("page 2 of 2", text)
        self.assertIn("**Place 24**", text)

    async def test_add_many_offers_a_link_and_an_upload(self) -> None:
        from dmbot.ui import name_lists

        menu = name_lists.AddMany(self.campaign.id)
        labels = [getattr(c, "label", "") for c in menu.children]
        self.assertEqual(
            labels,
            ["📋 Paste a list", "🔗 Paste a link", "📎 Upload a file", "📄 Get the template"],
        )
        it = self.it()
        await menu._link(it)
        self.assertIsInstance(it.response.modal, name_lists.LinkForm)
        it = self.it()
        await menu._upload(it)
        self.assertIsInstance(it.response.modal, name_lists.UploadForm)
        self.assertIn("file** box", name_lists.format_help(secrets=True))  # the fallback

    async def test_a_link_pasted_as_a_list_is_read_and_offered_to_the_ai(self) -> None:
        from dmbot.fetch import Fetched
        from dmbot.ui import name_lists

        fake: Any = SimpleNamespace(complete=AsyncMock())
        self.bot.ai = fake
        self.fresh()
        before = await self.names()
        link = "https://docs.google.com/document/d/1lHeFJAheOyz-suXZS644R86dLMt/edit?usp=drivesdk"
        form = name_lists.PasteForm(self.campaign.id, secrets=True)
        form.lines = SimpleNamespace(value=f"  {link}\n")  # type: ignore[assignment]
        got = Fetched(b"Rime of the Frostmaiden. Auril the Frostmaiden rules.", "link.txt")
        it = self.it()
        with patch("dmbot.fetch.fetch", AsyncMock(return_value=got)) as fetched:
            await form.on_submit(it)
        fetched.assert_awaited_once_with(link)
        message = it.followup.send.call_args.args[0]
        self.assertIn("Find the names in the linked page or file", message)
        self.assertIn("right to use", message)
        self.assertIsInstance(it.followup.send.call_args.kwargs["view"], name_lists.AIOffer)
        self.assertEqual(await self.names(), before)  # nothing added: never one long name
        fake.complete.assert_not_called()  # only after the DM presses Find names

    async def test_a_link_that_cant_be_read_says_why_and_adds_nothing(self) -> None:
        from dmbot import fetch
        from dmbot.ui import name_lists

        self.bot.ai = SimpleNamespace(complete=AsyncMock())  # type: ignore[assignment]
        self.fresh()
        before = await self.names()
        form = name_lists.LinkForm(self.campaign.id)
        form.link = SimpleNamespace(value="https://example.com/private")  # type: ignore[assignment]
        it = self.it()
        failed = AsyncMock(side_effect=fetch.LinkError(fetch.NOT_SHARED))
        with patch("dmbot.fetch.fetch", failed):
            await form.on_submit(it)
        self.assertEqual(it.followup.send.call_args.args[0], fetch.NOT_SHARED)
        self.assertEqual(await self.names(), before)

    async def test_a_link_without_the_ai_is_refused_before_fetching(self) -> None:
        from dmbot.ui import name_lists

        self.bot.ai = None
        self.fresh()
        before = await self.names()
        it = self.it()
        with patch("dmbot.fetch.fetch", AsyncMock()) as fetched:
            await name_lists.take_link(it, self.campaign.id, "https://example.com/list.txt")
        fetched.assert_not_called()  # nothing downloaded for nobody to read
        self.assertIn("can't read documents or links here", it.response.sent[0][0])
        self.assertEqual(await self.names(), before)

    async def test_one_link_at_a_time_per_server(self) -> None:
        from dmbot.ui import name_lists

        self.bot.ai = SimpleNamespace(complete=AsyncMock())  # type: ignore[assignment]
        self.fresh()
        name_lists._link_busy.add(GUILD)
        self.addCleanup(name_lists._link_busy.discard, GUILD)
        it = self.it()
        with patch("dmbot.fetch.fetch", AsyncMock()) as fetched:
            await name_lists.take_link(it, self.campaign.id, "https://example.com/a")
        fetched.assert_not_called()
        self.assertEqual(it.response.sent[0][0], name_lists.LINK_BUSY)
        # Every way in shares the guard, the slash command's link box too.
        with patch("dmbot.fetch.fetch", AsyncMock()) as fetched:
            got = await name_lists.read_link_once(GUILD, "https://example.com/b")
        self.assertEqual(got, (None, name_lists.LINK_BUSY))
        fetched.assert_not_called()
        # The place is freed even when reading fails.
        name_lists._link_busy.discard(GUILD)
        with (
            patch("dmbot.fetch.fetch", AsyncMock(side_effect=RuntimeError("boom"))),
            self.assertRaises(RuntimeError),
        ):
            await name_lists.read_link_once(GUILD, "https://example.com/c")
        self.assertEqual(name_lists._link_busy, set())

    async def test_a_web_page_reaches_the_ai_offer_as_text(self) -> None:
        from dmbot.fetch import Fetched
        from dmbot.ui import name_lists

        self.bot.ai = SimpleNamespace(complete=AsyncMock())  # type: ignore[assignment]
        self.fresh()
        it = self.it()
        page = Fetched(b"<html><script>x</script><body><p>Auril the Frostmaiden</p>", "link.html")
        with patch("dmbot.fetch.fetch", AsyncMock(return_value=page)):
            await name_lists.take_link(it, self.campaign.id, "https://example.com/lore")
        offer = it.followup.send.call_args.kwargs["view"]
        self.assertEqual(offer.upload.text, "Auril the Frostmaiden")
        self.assertTrue(offer.upload.document)
        self.assertEqual(name_lists._link_busy, set())  # free for the next one

    async def test_an_uploaded_list_is_added_at_once(self) -> None:
        from dmbot.ui import name_lists

        self.fresh()
        form = name_lists.UploadForm(self.campaign.id)
        form.file = SimpleNamespace(component=SimpleNamespace(values=["npcs.txt"]))  # type: ignore[assignment]
        it = self.it()
        upload = name_lists.Upload("Ulfgar | NPC", "npcs.txt")
        reader = AsyncMock(return_value=(upload, None))
        with patch.object(name_lists, "read_attachment", reader):
            await form.on_submit(it)
        reader.assert_awaited_once_with("npcs.txt")
        self.assertIn("Added 1 name", it.followup.send.call_args.args[0])
        form.file = SimpleNamespace(component=SimpleNamespace(values=[]))  # type: ignore[assignment]
        it = self.it()
        await form.on_submit(it)
        self.assertIn("No file was added", it.response.sent[0][0])

    async def test_the_template_file(self) -> None:
        from dmbot.ui import name_lists

        it = self.it()
        await name_lists.AddMany(self.campaign.id)._template(it)
        _, kw = it.response.sent[0]
        self.assertTrue(kw["file"].fp.getvalue().startswith(b"### DMbot names list"))

    async def test_browse_with_no_names_says_so(self) -> None:
        from dmbot.ui import name_lists

        other = await self.campaigns.create(GUILD, "Empty", DM)
        it = self.it()
        await name_lists.show_browse(it, other.id)
        text, kw = it.response.sent[0]
        self.assertIn("doesn't know any names yet", text)
        self.assertNotIn("view", kw)


class Adding(NamesTest):
    async def test_a_name_with_nicknames_and_a_secret(self) -> None:
        entity = await ui.save_name(
            self.memory, self.campaign, "Belleros", "npc", ["Bell", "Bel"], ["the hooded stranger"]
        )
        self.assertEqual((entity.status, entity.kind_key), (CONFIRMED, "npc"))
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
        text, card = it.response.edited[0]
        # What was saved, then the hint, then the rest (#393).
        self.assertIn(
            "✅ DMbot will remember **Thornewick** (place).\n"
            f"{ui.MISTAKE_HINT}\nDMbot also listens for: Thorn Wick.",
            text,
        )
        self.assertIn("🪪 **Thornewick**", text)
        labels = {getattr(c, "label", None) for c in card.children}
        self.assertLessEqual({"✏️ Fix spelling", "🗑 Remove this name", "🏷️ Also called…"}, labels)
        self.assertIn("Thornewick", await self.names())

    async def test_a_players_character_knows_its_player(self) -> None:
        form = ui.CharacterForm(self.campaign.id, PLAYER, "Mia")
        form.name = SimpleNamespace(value="Cerric")  # type: ignore[assignment]
        form.others = SimpleNamespace(value="Cer")  # type: ignore[assignment]
        it = self.it()
        await form.on_submit(it)
        self.assertIn("**Cerric**, played by **Mia**", it.response.sent[0][0])
        cerric = (await self.names())["Cerric"]
        self.assertEqual((cerric.kind_key, cerric.played_by), ("player_character", PLAYER))

    async def test_only_a_player_character_has_a_player(self) -> None:
        with self.assertRaises(MemoryRuleError):
            await self.memory.add_entity(
                GUILD, self.campaign.id, type="npc", name="X", source="dm", played_by=PLAYER
            )

    async def test_a_name_it_already_knows_is_not_added_twice(self) -> None:
        await ui.save_name(self.memory, self.campaign, "Belleros", "npc", ["Bell"], [])
        form = ui.AddNameForm(self.campaign.id, secrets=True)
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
        self.assertEqual(hrothgar.kind_key, "npc")
        aliases = await self.memory.aliases(GUILD, self.campaign.id, entity_id=hrothgar.id)
        self.assertEqual({a.status for a in aliases}, {CONFIRMED})

    async def test_same_as_folds_it_into_a_known_name(self) -> None:
        belleros = await ui.save_name(self.memory, self.campaign, "Belleros", "npc", [], [])
        await self.suggest("Bellaros")
        view, _ = await self.review()
        await view._same(self.it())
        view.same = SimpleNamespace(values=[belleros.id])  # type: ignore[assignment]
        it = self.it()
        merged = self.slow("merge", it)
        await view._same_picked(it)
        self.assertEqual(merged, ["🔗 Joining **Bellaros** into **Belleros**…"])  # #351
        self.assertIn("**Bellaros** is now a name for **Belleros**", it.response.edited[-1][0])
        aliases = await self.memory.aliases(GUILD, self.campaign.id, entity_id=belleros.id)
        self.assertIn(("Bellaros", CONFIRMED), {(a.text, a.status) for a in aliases})

    async def spelled(self, name: str) -> str:
        written = await self.memory.add_entity(
            GUILD, self.campaign.id, type="concept", name=name, source="scan",
            description=ui.SPELLED_OUT,
        )  # fmt: skip
        return written.value.id

    async def test_a_spelled_out_name_says_so_and_offers_to_fix_the_known_spelling(self) -> None:
        await ui.save_name(self.memory, self.campaign, "Hrothgar", "npc", [], [])
        await self.spelled("Rothgar")
        it = self.it()
        await ui.start_review(it, self.campaign.id)
        text, kw = it.response.sent[0]
        self.assertIn("· spelled out at the table", text)
        self.assertIn("Same name, spelled **Rothgar**? Or a new name?", text)
        labels = [b.label for b in kw["view"].children]
        self.assertIn("✏️ Fix spelling", labels)
        self.assertTrue(all(len(label) <= ui.REVIEW_LABEL_MAX for label in labels))

    async def test_fixing_the_spelling_makes_the_spelled_name_the_main_name(self) -> None:
        hrothgar = await ui.save_name(self.memory, self.campaign, "Hrothgar", "npc", [], [])
        await self.spelled("Rothgar")
        it0 = self.it()
        await ui.start_review(it0, self.campaign.id)
        view = it0.response.sent[0][1]["view"]
        await view._fix_spelling(self.it())
        entity = await self.memory.entity(GUILD, self.campaign.id, hrothgar.id)
        assert entity is not None
        self.assertEqual(entity.name, "Rothgar")
        aliases = await self.memory.aliases(GUILD, self.campaign.id, entity_id=hrothgar.id)
        self.assertIn("hrothgar", {a.key for a in aliases})  # the old spelling is still a name

    async def test_a_pending_suggestion_someone_spelled_out_offers_to_fix_its_spelling(
        self,
    ) -> None:
        brenoch = await self.suggest("Brenoch")
        await self.memory.set_note(
            GUILD, self.campaign.id, brenoch, f"{ui.SPELLED_OUT}: Brannoch", source="scan"
        )
        it = self.it()
        await ui.start_review(it, self.campaign.id)
        text, kw = it.response.sent[0]
        self.assertIn("Someone spelled it out: **Brannoch**", text)
        labels = [b.label for b in kw["view"].children]
        self.assertEqual(labels[0], "✏️ Fix spelling")
        await kw["view"]._fix_pending(self.it())
        entity = await self.memory.entity(GUILD, self.campaign.id, brenoch)
        assert entity is not None
        self.assertEqual((entity.name, entity.status), ("Brannoch", PROPOSED))  # still the DM's

    async def test_a_spelling_said_at_the_table_reaches_the_pending_suggestion(self) -> None:
        from dmbot.transcript.spelled import SpellingWatcher

        brenoch = await self.suggest("Brenoch")
        speller = 7
        bot: Any = SimpleNamespace(
            memory=self.memory,
            consent=SimpleNamespace(consenting=AsyncMock(return_value={speller})),
        )
        for name in ("_note_spelling", "_record_spelled", "suggest_spelled"):
            setattr(bot, name, getattr(DMBot, name).__get__(bot))
        table = SimpleNamespace(
            spelling=SpellingWatcher(),
            spelled=[],
            spelled_hints=(),
            held_spelled={},
            hint_parts=None,
            guild_id=GUILD,
            campaign_id=self.campaign.id,
        )
        for who, line in (
            (1, "ask Brenoch"),
            (1, "how do you spell that"),
            (speller, "B R A N N O C H"),
        ):
            bot._note_spelling(table, who, line)
        await bot.suggest_spelled(table)
        waiting = await self.memory.entities(GUILD, self.campaign.id, statuses=[PROPOSED])
        names = [e.name for e in waiting]
        self.assertEqual(names, ["Brenoch"])  # no second entry
        it = self.it()
        await ui.start_review(it, self.campaign.id)
        text, kw = it.response.sent[0]
        self.assertIn("Someone spelled it out: **Brannoch**", text)
        self.assertEqual(text.count("Brannoch"), 1)  # shown once, not also as a "heard" note
        self.assertEqual(kw["view"].children[0].label, "✏️ Fix spelling")
        self.assertEqual(brenoch, waiting[0].id)

    async def test_a_note_cannot_be_set_on_a_confirmed_name(self) -> None:
        saved = await ui.save_name(self.memory, self.campaign, "Hrothgar", "npc", [], [])
        entity_id = saved.id
        with self.assertRaises(MemoryRuleError):
            await self.memory.set_note(
                GUILD, self.campaign.id, entity_id, "Spelled out at the table: X", source="scan"
            )

    async def test_a_spelled_out_name_that_sounds_like_nothing_has_no_fix_button(self) -> None:
        await self.spelled("Zorgul")
        it = self.it()
        await ui.start_review(it, self.campaign.id)
        labels = [b.label for b in it.response.sent[0][1]["view"].children]
        self.assertFalse(any("Fix spelling" in label for label in labels))

    async def test_a_near_match_comes_pre_filled_and_one_press_adds_it(self) -> None:
        # #394: "Rothgar" sounds like the known Hrothgar
        hrothgar = await ui.save_name(self.memory, self.campaign, "Hrothgar", "npc", [], [])
        await self.suggest("Rothgar")
        it = self.it()
        await ui.start_review(it, self.campaign.id)
        text, kw = it.response.sent[0]
        self.assertIn("Sounds like **Hrothgar**. The same, or new?", text)
        self.assertIn("· heard 3 times", text)
        view = kw["view"]
        labels = [b.label for b in view.children]
        self.assertEqual(
            labels,
            [
                "✅ It's Hrothgar",
                "➕ New name",
                "🔗 Another known name…",
                "🚫 Not a name",
                "⏳ Later",
            ],
        )
        self.assertTrue(all(len(label) <= ui.REVIEW_LABEL_MAX for label in labels))
        it = self.it()
        await view._another(it)
        self.assertIn("**Rothgar** is now a name for **Hrothgar**", it.response.edited[-1][0])
        aliases = await self.memory.aliases(GUILD, self.campaign.id, entity_id=hrothgar.id)
        self.assertIn(("Rothgar", CONFIRMED), {(a.text, a.status) for a in aliases})
        self.assertNotIn("Rothgar", await self.names((PROPOSED,)))

    async def test_new_name_is_still_offered_with_a_match(self) -> None:
        await ui.save_name(self.memory, self.campaign, "Hrothgar", "npc", [], [])
        await self.suggest("Rothgar")
        it = self.it()
        await ui.start_review(it, self.campaign.id)
        view = it.response.sent[0][1]["view"]
        await view._yes(self.it())  # "New name": then what it is, as before
        view.kind = SimpleNamespace(values=["npc"])
        it = self.it()
        await view._kind_picked(it)
        self.assertIn("Added **Rothgar**", it.response.edited[0][0])

    async def test_a_secret_name_is_never_the_match(self) -> None:
        await ui.save_name(self.memory, self.campaign, "Belleros", "npc", [], ["Silas Vane"])
        await self.suggest("Silas Vain")
        await self.review()  # "Is this a name in your game?": no match offered

    async def test_names_heard_with_it_are_shown_and_confirmed_with_it(self) -> None:
        written = await self.memory.add_entity(
            GUILD, self.campaign.id, type="concept", name="Oskar Vane", source="scan"
        )
        await self.memory.add_alias(
            GUILD, self.campaign.id, written.value.id, "Vane", kind="short", source="scan"
        )
        it = self.it()
        await ui.start_review(it, self.campaign.id)
        text, kw = it.response.sent[0]
        self.assertIn("Also heard as **Vane**: saved with it.", text)
        view = kw["view"]
        await view._yes(self.it())
        view.kind = SimpleNamespace(values=["npc"])
        it = self.it()
        await view._kind_picked(it)
        self.assertIn("✅ Added **Oskar Vane** (also **Vane**).", it.response.edited[-1][0])
        aliases = await self.memory.aliases(GUILD, self.campaign.id, entity_id=written.value.id)
        self.assertEqual({(a.text, a.status) for a in aliases},
                         {("Oskar Vane", CONFIRMED), ("Vane", CONFIRMED)})  # fmt: skip

    async def test_a_wrong_guess_can_point_to_someone_else(self) -> None:
        await ui.save_name(self.memory, self.campaign, "Hrothgar", "npc", [], [])
        other = await ui.save_name(self.memory, self.campaign, "Hrothmund", "npc", [], [])
        await self.suggest("Rothgar")
        it = self.it()
        await ui.start_review(it, self.campaign.id)
        view = it.response.sent[0][1]["view"]
        await view._same(self.it())  # "Another known name…"
        view.same = SimpleNamespace(values=[other.id])
        it = self.it()
        await view._same_picked(it)
        self.assertIn("**Rothgar** is now a name for **Hrothmund**", it.response.edited[-1][0])

    async def test_one_undo_takes_back_the_merge_and_its_names(self) -> None:
        hrothgar = await ui.save_name(self.memory, self.campaign, "Hrothgar", "npc", [], [])
        sid = await self.suggest("Rothgar")
        await self.memory.add_alias(
            GUILD, self.campaign.id, sid, "Rothgr", kind="misheard", source="scan"
        )
        written = await self.memory.merge(
            GUILD,
            self.campaign.id,
            hrothgar.id,
            sid,
            source="dm",
            dm_said_same=True,
            confirm_keys=["rothgar", "rothgr"],
        )
        aliases = await self.memory.aliases(GUILD, self.campaign.id, entity_id=hrothgar.id)
        self.assertTrue({"Rothgar", "Rothgr"} <= {a.text for a in aliases if a.status == CONFIRMED})
        assert written.batch is not None
        await self.memory.undo(GUILD, self.campaign.id, written.batch)
        self.assertIn("Rothgar", await self.names((PROPOSED,)))
        back = await self.memory.aliases(GUILD, self.campaign.id, entity_id=sid)
        self.assertEqual({(a.text, a.status) for a in back},
                         {("Rothgar", PROPOSED), ("Rothgr", PROPOSED)})  # fmt: skip

    async def test_a_refused_join_says_so_and_moves_on(self) -> None:
        belleros = await ui.save_name(self.memory, self.campaign, "Belleros", "npc", [], [])
        await self.suggest("Bellaros")
        view, _ = await self.review()
        await view._same(self.it())
        view.same = SimpleNamespace(values=[belleros.id])  # type: ignore[assignment]
        it = self.it()
        refused = AsyncMock(side_effect=MemoryRuleError("gone"))
        with patch.object(self.memory, "merge", refused):
            await view._same_picked(it)
        self.assertIn(ui.NOT_JOINED, it.response.edited[-1][0])  # still in the review

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
        self.assertEqual((cerric.kind_key, cerric.played_by), (ui.PC, PLAYER))

    async def test_later_ends_the_round_and_keeps_it_waiting(self) -> None:
        await self.suggest("Hrothgar")
        view, _ = await self.review()
        it = self.it()
        await view._later(it)
        self.assertIn("1 name still waiting", it.response.edited[0][0])
        self.assertIsNone(it.response.edited[0][1])  # the menu is finished
        self.assertTrue(view.is_finished())
        self.assertIn("Hrothgar", await self.names((PROPOSED,)))

    async def test_later_is_remembered_so_the_name_stays_listed_but_stops_holding_a_recap(
        self,
    ) -> None:
        await self.suggest("Hrothgar")
        entity = next(iter(await self.names((PROPOSED,))))
        found = (await self.names((PROPOSED,)))["Hrothgar"]
        self.assertEqual(await self.memory.deferred_names(GUILD, self.campaign.id), set())
        view, _ = await self.review()
        await view._later(self.it())
        self.assertEqual(entity, "Hrothgar")
        self.assertEqual(await self.memory.deferred_names(GUILD, self.campaign.id), {found.id})
        self.assertIn("Hrothgar", await self.names((PROPOSED,)))  # still waiting in the list
        again, _ = await self.review()  # and offered again at the next check
        self.assertEqual(again.queue, [found.id])

    async def test_a_name_left_for_later_no_longer_counts_as_a_question_a_recap_waits_on(
        self,
    ) -> None:
        await self.suggest("Hrothgar")
        await self.suggest("Belleros")
        self.bot._next_session_start = AsyncMock(return_value=None)  # type: ignore[method-assign]
        self.bot.import_job = None  # type: ignore[assignment]
        self.bot.heard_questions = None  # type: ignore[assignment]

        async def waiting() -> int:
            return await self.bot._open_name_questions(GUILD, self.campaign.id, "live", 0)

        self.assertEqual(await waiting(), 2)
        found = (await self.names((PROPOSED,)))["Hrothgar"]
        await self.memory.defer_name(GUILD, self.campaign.id, found.id)
        self.assertEqual(await waiting(), 1)  # only the other one holds a recap back
        self.assertEqual(len(await self.names((PROPOSED,))), 2)  # both still wait in the list

    async def test_a_name_that_goes_takes_its_later_with_it(self) -> None:
        await self.suggest("Hrothgar")
        found = (await self.names((PROPOSED,)))["Hrothgar"]
        await self.memory.defer_name(GUILD, self.campaign.id, found.id)
        await self.memory.defer_name(GUILD, self.campaign.id, found.id)  # a repeat does nothing
        self.assertEqual(await self.memory.deferred_names(GUILD, self.campaign.id), {found.id})
        async with self.db.guild(GUILD) as conn:  # the name goes (a forgotten suggestion, say)
            await conn.execute(
                "DELETE FROM memory_entities WHERE guild_id = %s AND campaign_id = %s AND id = %s",
                (GUILD, self.campaign.id, found.id),
            )
        self.assertEqual(await self.memory.deferred_names(GUILD, self.campaign.id), set())

    async def test_only_a_suggestion_can_be_left_for_later_and_only_in_its_own_campaign(
        self,
    ) -> None:
        known = await ui.save_name(self.memory, self.campaign, "Belleros", "npc", [], [])
        await self.memory.defer_name(GUILD, self.campaign.id, known.id)  # confirmed: not a wait
        await self.memory.defer_name(GUILD, "e" * 32, known.id)  # another campaign's id
        self.assertEqual(await self.memory.deferred_names(GUILD, self.campaign.id), set())
        self.assertEqual(await self.memory.deferred_names(GUILD, "e" * 32), set())

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


def script_file(name: str) -> str:
    """A file from docs/test-scripts."""
    path = Path(__file__).resolve().parents[2] / "docs" / "test-scripts" / name
    return path.read_text(encoding="utf-8")


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

    async def test_old_undo_history_goes_last_and_a_failure_is_only_logged(self) -> None:
        order = MagicMock()
        steps = (
            "finish_transcript", "_remove_stop_button", "post_summary", "post_session_summary",
            "keep_heard_names", "suggest_names", "close_stale_flags",
        )  # fmt: skip
        for name in steps:
            setattr(order, name, AsyncMock(return_value=0))
        order.prune_changes = AsyncMock(side_effect=RuntimeError("down"))
        with (
            patch.multiple(self.bot, **{name: getattr(order, name) for name in steps}),
            patch.object(self.memory, "prune_changes", order.prune_changes),
            self.assertLogs("dmbot.bot", "ERROR") as logs,
        ):
            await self.bot._after_session(self.table(), 0, True)
        called = [c[0] for c in order.mock_calls]
        self.assertEqual(called[-1], "prune_changes")  # after everything that writes
        self.assertIn("After the session: old undo history failed", logs.output[0])

    async def test_no_memory_or_no_campaign_prunes_nothing(self) -> None:
        with patch.object(self.memory, "prune_changes", AsyncMock()) as prune:
            await self.bot.prune_old_changes(make_table(None))  # type: ignore[arg-type]
            self.bot.memory = None
            await self.bot.prune_old_changes(self.table())
        prune.assert_not_awaited()

    async def test_flags_that_no_longer_apply_are_closed(self) -> None:
        cid = self.campaign.id

        async def add(name: str, kind: str) -> str:
            written = await self.memory.add_entity(GUILD, cid, type=kind, name=name, source="dm")
            return written.value.id

        cerric = await add("Cerric", "npc")
        p1, p2 = await add("Brynwater", "place"), await add("Thornewick", "place")
        first = await self.memory.add_relation(
            GUILD, cid, cerric, "located_in", p1, source="dm", confidence=1.0
        )
        await self.memory.add_relation(
            GUILD, cid, cerric, "located_in", p2, source="cleaner", confidence=0.5
        )
        self.assertEqual(len(await self.memory.flags(GUILD, cid)), 1)
        await self.memory.update_relation(
            GUILD, cid, first.value[0].id, status=REJECTED, source="dm"
        )
        await self.bot.close_stale_flags(self.table())  # one of the after-session steps
        self.assertEqual(await self.memory.flags(GUILD, cid), [])

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

    async def test_a_name_fixed_live_is_not_suggested_again(self) -> None:
        # #394: the scan reads the cleaned line, where "Rothgar" was already fixed
        from dmbot.audio.segmenter import Utterance
        from dmbot.memory.lookup import CampaignLookup

        await self.consent.grant(GUILD, PLAYER)
        await ui.save_name(self.memory, self.campaign, "Hrothgar", "npc", [], [])
        table = self.table()
        table.name_lookup = CampaignLookup.build(
            await self.memory.lookup_data(GUILD, self.campaign.id)
        )
        table.scene.note(
            [(await self.names())["Hrothgar"].id], PLAYER, __import__("time").monotonic()
        )
        self.bot.tables[GUILD] = table
        for words in ("We met Rothgar at dawn.", "Then Rothgar left."):
            utterance = Utterance(GUILD, PLAYER, 0, 0, bytes(32000), table.segmenter.session)
            self.bot._deliver_transcript(utterance, words)
        self.assertIn("Hrothgar", table.heard[0][1])
        posted = AsyncMock(return_value=True)
        self.bot.post = posted  # type: ignore[method-assign]
        await self.bot.suggest_names(table)
        self.assertEqual(await self.names((PROPOSED,)), {})
        posted.assert_not_awaited()

    async def test_one_question_per_thing_with_its_match(self) -> None:
        await self.consent.grant(GUILD, PLAYER)
        await ui.save_name(self.memory, self.campaign, "Ulfgar", "npc", [], [])
        table = self.table()
        table.heard = [
            (PLAYER, "We met Oskar Vane at dawn. I trust Oskar Vane."),
            (PLAYER, "Then Vane left. Ask Vane later."),
            (PLAYER, "We fear Ulfgarr now. We fear Ulfgarr again."),
        ]
        posted = AsyncMock(return_value=True)
        self.bot.post = posted  # type: ignore[method-assign]
        await self.bot.suggest_names(table)
        waiting = await self.names((PROPOSED,))
        self.assertEqual(set(waiting), {"Oskar Vane", "Ulfgarr"})  # "Vane" folded in
        aliases = await self.memory.aliases(
            GUILD, self.campaign.id, entity_id=waiting["Oskar Vane"].id
        )
        self.assertIn("Vane", {a.text for a in aliases})
        self.assertEqual(waiting["Ulfgarr"].description, "Heard 2 times")  # never a name

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

    async def test_a_clip_never_waits_long_for_names_or_gets_the_old_ones(self) -> None:
        # Every clip of a session asks for hints: while the names reload (just after a
        # change, maybe a name made secret) a clip goes with people only, and soon after
        # the new names are there (#581 review).
        from dmbot import bot as bot_module

        await ui.save_name(self.memory, self.campaign, "Belleros", "npc", [], [])
        table = make_table(self.campaign.id)
        self.bot.tables[GUILD] = table
        self.assertIn("Belleros", await self.bot._name_hints(clip(table)))
        gate = asyncio.Event()
        real = self.memory.lookup_data

        async def slow(guild_id: int, campaign_id: str) -> Any:
            await gate.wait()
            return await real(guild_id, campaign_id)

        cache = self.bot.lookup
        assert cache is not None
        cache.mark_stale(GUILD, self.campaign.id)
        with (
            patch.object(self.memory, "lookup_data", slow),
            patch.object(bot_module, "HINTS_WAIT_S", 0.05),
        ):
            hints = await self.bot._name_hints(clip(table))
            self.assertNotIn("Belleros", hints)  # not from the old copy
            self.assertIsNone(table.name_lookup)
            gate.set()
            await asyncio.gather(*cache._loading.values())
            self.assertIn("Belleros", await self.bot._name_hints(clip(table)))
        self.assertIsNotNone(table.name_lookup)

    async def test_people_in_the_voice_channel_come_first_and_a_revoke_counts_at_once(
        self,
    ) -> None:
        await ui.save_name(self.memory, self.campaign, "Belleros", "npc", [], [])
        table = make_table(self.campaign.id)
        self.bot.tables[GUILD] = table
        for user in (DM, PLAYER, STRANGER):
            await self.consent.grant(GUILD, user)
        voice = MagicMock(spec=discord.VoiceChannel)
        voice.members = [
            SimpleNamespace(id=PLAYER, bot=False, display_name="p")
        ]  # only the player is at the table
        self.bot.get_channel = lambda _id: voice  # type: ignore[method-assign]
        self.bot.name_of = lambda _g, user: f"user{user}"  # type: ignore[method-assign, assignment]
        hints = await self.bot._name_hints(clip(table))
        self.assertEqual(hints[0], f"user{PLAYER}")  # at the table: first
        self.assertLess(hints.index("Belleros"), hints.index(f"user{DM}"))  # not here: last
        self.assertLess(hints.index("Belleros"), hints.index(f"user{STRANGER}"))
        # Someone stops being recorded: gone from the very next hints, cache or not.
        await self.consent.revoke(GUILD, STRANGER)
        self.assertNotIn(f"user{STRANGER}", await self.bot._name_hints(clip(table)))
        # Who's in the voice channel is looked at again after a few seconds.
        voice.members = [
            SimpleNamespace(id=PLAYER, bot=False, display_name="p"),
            SimpleNamespace(id=DM, bot=False, display_name="d"),
        ]
        self.assertGreater(
            (await self.bot._name_hints(clip(table))).index(f"user{DM}"), 1
        )  # still the copy from a moment ago
        cached = self.bot._hint_people_cache[GUILD]
        self.bot._hint_people_cache[GUILD] = (cached[0] - 60, *cached[1:])  # time passes
        hints = await self.bot._name_hints(clip(table))
        self.assertEqual(sorted(hints[:2]), sorted([f"user{PLAYER}", f"user{DM}"]))
        # Someone in the voice channel who never agreed is never sent as a hint.
        voice.members.append(SimpleNamespace(id=9999, bot=False, display_name="x"))
        self.bot._hint_people_cache.clear()
        self.assertNotIn("user9999", await self.bot._name_hints(clip(table)))
        # Another server's names are kept apart, and nothing is kept past a session.
        await self.consent.grant(GUILD + 1, 4242)
        self.assertEqual(await self.bot._hint_people(GUILD + 1, None), ((), ("user4242",)))
        self.assertNotIn("user4242", await self.bot._name_hints(clip(table)))
        self.assertEqual(set(self.bot._hint_people_cache), {GUILD, GUILD + 1})
        # A quick "stop recording me" (before the database write) counts at once too.
        self.bot.stop_recording(GUILD, PLAYER)
        self.assertNotIn(f"user{PLAYER}", await self.bot._name_hints(clip(table)))
        # No voice channel DMbot can see: everyone who agreed goes last, none are lost.
        self.bot.get_channel = lambda _id: None  # type: ignore[method-assign]
        table.voice_channel_id = 3  # another channel: not the copy from before
        hints = await self.bot._name_hints(clip(table))
        self.assertEqual(hints[0], "Belleros")
        self.assertIn(f"user{DM}", hints)

    async def test_names_said_at_the_table_move_to_the_front(self) -> None:
        from dmbot.audio.segmenter import Utterance

        # One creation second for all three, so never-said names tie and sort by name
        # (a second boundary between saves would put Zephyr first as the newest).
        self.memory._clock = lambda: 1_700_000_000.0
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


class KeptAfterTheSession(NamesTest):
    async def test_names_said_are_kept_only_from_people_who_still_agree(self) -> None:
        from dmbot.audio.segmenter import Utterance

        zephyr = await ui.save_name(self.memory, self.campaign, "Zephyr", "npc", [], [])
        table = make_table(self.campaign.id)
        table.started_at = 1_700_000_000
        self.bot.tables[GUILD] = table
        await self.bot._name_hints(clip(table))  # loads the names for matching
        await self.consent.grant(GUILD, PLAYER)
        for who in (PLAYER, STRANGER):
            said = Utterance(GUILD, who, 0, 0, bytes(32000), table.segmenter.session)
            self.bot._deliver_transcript(said, "Ask Zephyr. Zephyr knows.")
        self.assertEqual(sum(table.heard_counts.values()), 2)  # once per line
        await self.bot.keep_heard_names(table)
        data = await self.memory.lookup_data(GUILD, self.campaign.id)
        self.assertEqual([(h.entity_id, h.times) for h in data.heard], [(zephyr.id, 1)])
        self.assertEqual(table.heard_counts, {})

    async def test_stopping_drops_their_counts_at_once(self) -> None:
        from dmbot.audio.segmenter import Utterance

        await ui.save_name(self.memory, self.campaign, "Zephyr", "npc", [], [])
        table = make_table(self.campaign.id)
        self.bot.tables[GUILD] = table
        await self.bot._name_hints(clip(table))
        said = Utterance(GUILD, PLAYER, 0, 0, bytes(32000), table.segmenter.session)
        self.bot._deliver_transcript(said, "Zephyr!")
        self.bot.stop_recording(GUILD, PLAYER)
        self.assertEqual(table.heard_counts, {})


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
        self.assertEqual(changed.value.kind_key, "npc")
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
        self.assertEqual(
            [(e.name, e.played_by) for e in copy if e.type != "person"], [("Cerric", PLAYER)]
        )
