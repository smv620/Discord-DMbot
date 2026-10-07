"""Campaign memory step 3 (#126): names the DM adds, players' characters, checking the
names DMbot suggests after a session, and names as speech-to-text hints."""

import asyncio
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
from dmbot.memory.backup import MemorySection
from dmbot.memory.models import CONFIRMED, PROPOSED, REJECTED, MemoryRuleError
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

        return SimpleNamespace(
            client=self.bot,
            guild=SimpleNamespace(id=GUILD),
            guild_id=GUILD,
            user=user,
            response=response,
            followup=SimpleNamespace(send=AsyncMock()),
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
        self.assertEqual(view.open.placeholder, "✏️ Edit or remove a name…")

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
        self.assertIn("🪪 **Belleros** · NPC · Not heard in a session yet", text)
        self.assertIn("**Also called:** Bell", text)
        self.assertIn("**🤫 Secret:** the hooded stranger", text)
        manager = await self.card(MANAGER)
        self.assertNotIn("hooded", manager)  # a server manager may be at the table

    async def test_fix_spelling_changes_the_name_it_listens_for(self) -> None:
        from dmbot.ui import name_card

        form = name_card.FixSpellingForm(self.campaign.id, self.bell.id, "Belleros")
        form.name = SimpleNamespace(value="Bellaros")  # type: ignore[assignment]
        it = self.it()
        await form.on_submit(it)
        text = it.response.edited[0][0]  # the card, redrawn in place, read fresh
        self.assertIn("Now spelled **Bellaros**", text)
        self.assertIn("🪪 **Bellaros**", text)
        keys = {a.key for a in await self.memory.aliases(GUILD, self.campaign.id)}
        self.assertIn("bellaros", keys)
        self.assertNotIn("belleros", keys)

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
        self.assertIn("🪪 **Bell** · NPC", text)
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
        self.assertEqual(it.response.edited[-1], (name_card.TRY_AGAIN, None))

    async def test_same_as_never_offers_the_name_itself(self) -> None:
        from dmbot.ui import name_card

        self.fresh()
        it = self.it()
        await name_card.show_picks(it, self.campaign.id, self.bell.id, name_card.SAME, "Belleros")
        self.assertIn("No other name like **Belleros**", it.response.edited[0][0])

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
        await name_card.show_card(it, self.campaign.id, self.bell.id, full=True, note=note)
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

    async def test_show_all_lists_every_other_name(self) -> None:
        from dmbot.ui import name_card

        for n in range(5):
            await self.memory.add_alias(
                GUILD, self.campaign.id, self.bell.id, f"Bell {n}", kind="nickname",
                source="dm", status=CONFIRMED,
            )  # fmt: skip
        self.fresh()
        it = self.it()
        await name_card.show_card(it, self.campaign.id, self.bell.id)
        text, kw = it.response.sent[0]
        self.assertIn("… and 3 more", text)
        labels = [getattr(c, "label", None) for c in kw["view"].children]
        self.assertIn("Show all", labels)
        it = self.it()
        await name_card.show_card(it, self.campaign.id, self.bell.id, full=True)
        text, kw = it.response.sent[0]
        self.assertNotIn("… and", text)
        self.assertIn("Bell 4", text)
        self.assertNotIn("Show all", [getattr(c, "label", None) for c in kw["view"].children])

    async def test_the_type_ahead_suggests_nothing_to_strangers(self) -> None:
        from dmbot.ui import name_card

        self.assertEqual(await name_card.find_typeahead(self.it(STRANGER), "bell"), [])
        choices = await name_card.find_typeahead(self.it(), "bell")
        self.assertEqual([c.value for c in choices], [self.bell.id])
        hidden = await name_card.find_typeahead(self.it(MANAGER), "hooded")
        self.assertEqual(hidden, [])  # secret names only for the campaign's DMs
        plain = await name_card.find_typeahead(self.it(MANAGER), "bell")
        self.assertEqual([c.value for c in plain], [self.bell.id])  # the rest, yes


class Lists(NamesTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        from dmbot.memory.lookup import LookupCache

        self.bot.lookup = LookupCache(self.memory)
        await ui.save_name(self.memory, self.campaign, "Belleros", "npc", [], ["the stranger"])

    def fresh(self) -> None:
        self.bot.lookup.mark_all_stale()  # type: ignore[union-attr]

    async def test_add_many_saves_skips_and_asks_about_unclear_ones(self) -> None:
        from dmbot.ui import name_lists

        self.fresh()
        it = self.it()
        text = "# note\nBryn Shander | place | Bryn\nBelleros | npc\nUlfgar\nBell Eros | npc"
        await name_lists.import_list(it, self.campaign.id, text)
        summary = it.followup.send.call_args.args[0]
        self.assertIn("Added 3 names", summary)
        self.assertIn("Only one name wrong? Run `/dmbot names` and use 🔍 Find a name", summary)
        self.assertIn("2 names need you to check them", summary)  # no kind; sounds like Belleros
        self.assertIn("1 name DMbot already knows", summary)
        self.assertIn("Bryn Shander", await self.names())
        self.assertIn("Ulfgar", await self.names((PROPOSED,)))
        view = it.followup.send.call_args.kwargs["view"]
        (undo,) = [c for c in view.children if isinstance(c, name_lists.UndoListButton)]
        it = self.it()
        it.edit_original_response = AsyncMock()
        await undo.callback(it)
        self.assertIn(
            "Took the whole list back", it.edit_original_response.call_args.kwargs["content"]
        )
        self.assertNotIn("Bryn Shander", await self.names())

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
        self.assertIn("Every **wizard**: 4 names set to", it.response.edited[0][0])
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
        it = self.it(MANAGER)
        it.edit_original_response = AsyncMock()
        await offer._read(it)
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
        self.assertNotIn("need you to check", summary)
        self.assertIn("only the campaign's DM can add secret names", summary)
        self.fresh()
        it = self.it(MANAGER)
        await name_lists.send_download(it, self.campaign.id)
        text = it.followup.send.call_args.args[0]
        body = it.followup.send.call_args.kwargs["file"].fp.getvalue()
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
        body = it.followup.send.call_args.kwargs["file"].fp.getvalue().decode()
        (line,) = parse(body, secrets=True).lines
        self.assertEqual((line.name, line.secrets), ("Belleros", ("the stranger",)))

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
        text, card = it.response.edited[0]
        self.assertIn("DMbot will remember **Thornewick** (place)", text)
        self.assertIn(ui.MISTAKE_HINT, text)  # #353: its card, to fix or remove it
        self.assertIn("🪪 **Thornewick**", text)
        labels = {getattr(c, "label", None) for c in card.children}
        self.assertLessEqual({"✏️ Fix spelling", "🗑 Remove this name", "➕ Also called…"}, labels)
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
        merged = self.slow("merge", it)
        await view._same_picked(it)
        self.assertEqual(merged, ["🔗 Joining **Bellaros** into **Belleros**…"])  # #351
        self.assertIn("**Bellaros** is another name for **Belleros**", it.response.edited[-1][0])
        aliases = await self.memory.aliases(GUILD, self.campaign.id, entity_id=belleros.id)
        self.assertIn(("Bellaros", CONFIRMED), {(a.text, a.status) for a in aliases})

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
