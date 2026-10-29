"""The name card's connections and ✏️ Edit a connection… (#2323): the card's one line each, the
menu in the same order and words, each change, Undo, and the website choice past 25."""

from __future__ import annotations

import dataclasses
import re
from typing import Any

import discord

from dmbot.memory.models import CONFIRMED
from dmbot.memory.models import DM as DM_SOURCE
from dmbot.memory.ontology import FALLBACK_PREDICATE
from dmbot.ui import connection_edit, name_card
from dmbot.ui import names as ui
from tests import test_memory_names as base

GUILD, DM, MANAGER, PLAYER = base.GUILD, base.DM, base.MANAGER, base.PLAYER
SITE = "https://dmbot.example.com"


class Cards(base.NamesTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        from dmbot.memory.lookup import LookupCache

        self.bot.lookup = LookupCache(self.memory)
        self.bell = await ui.save_name(self.memory, self.campaign, "Belleros", "npc", [], [])

    def fresh(self) -> None:
        self.bot.lookup.mark_all_stale()  # type: ignore[union-attr]

    async def know(self, name: str, *, secret: bool = False, predicate: str = "knows") -> Any:
        other = await ui.save_name(self.memory, self.campaign, name, "npc", [], [])
        await self.memory.add_relation(
            GUILD, self.campaign.id, self.bell.id, predicate, other.id,
            source="dm", confidence=1.0, status=CONFIRMED, secret=secret,
        )  # fmt: skip
        self.fresh()
        return other

    async def card(self, who: int = DM, *, site: str = "") -> tuple[str, Any]:
        self.bot.settings = dataclasses.replace(self.bot.settings, site_url=site)
        it = self.it(who)
        await name_card.show_card(it, self.campaign.id, self.bell.id)
        text, kw = it.response.sent[0]
        return text, kw["view"]

    async def connect_menu(self, who: int = DM) -> Any:
        it = self.it(who)
        await name_card.NameCard(self.campaign.id, self.bell.id)._connect(it)
        return it.response.edited[0][1]

    async def relations(self, *, secrets: bool = True) -> list[Any]:
        return await self.memory.relations(
            GUILD, self.campaign.id, entity_id=self.bell.id, include_secret=secrets
        )

    # ---- the card ----

    async def test_one_line_each_with_the_icon_only_on_a_secret_one(self) -> None:
        await self.know("Kesh")
        await self.know("Vessa", secret=True)
        text, _ = await self.card()
        self.assertIn("• knows **Kesh**\n", text + "\n")  # (no icon at the end of a known one)
        self.assertIn("• knows **Vessa** 🤫", text)
        self.assertEqual(text.count("🤫"), 1)  # a known connection has no icon

    async def test_a_player_card_view_never_names_a_secret_connection(self) -> None:
        await self.know("Kesh")
        await self.know("Vessa", secret=True)
        text, _ = await self.card(MANAGER)  # (someone who is not a DM)
        self.assertIn("• knows **Kesh**", text)
        self.assertNotIn("Vessa", text)
        self.assertNotIn("🤫", text)

    async def test_connections_that_read_alike_sit_together(self) -> None:
        await self.know("Kesh")
        await self.know("Hollis", predicate="serves")
        await self.know("Orla")
        text, _ = await self.card()
        lines = [ln for ln in text.split("\n") if ln.startswith("• ")]
        self.assertEqual(
            sorted(lines),
            sorted(["• knows **Kesh**", "• knows **Orla**", "• works for **Hollis**"]),
        )
        reading = [ln.split(" **")[0] for ln in lines]
        self.assertIn(
            reading, (["• knows", "• knows", "• works for"], ["• works for", "• knows", "• knows"])
        )

    async def fill(self, n: int) -> None:
        for k in range(n):
            await self.know(f"Friend number {k:03} " + "x" * 30)

    async def test_past_the_limit_it_says_how_many_more_and_where_to_see_them(self) -> None:
        await self.fill(70)
        text, _ = await self.card(site=SITE)
        self.assertLessEqual(len(text), name_card.CARD_MAX)
        shown = [ln for ln in text.split("\n") if ln.startswith("• knows")]
        last = text.split("\n")[-1]
        match = re.fullmatch(r"• …and (\d+) more\. All of them: <(https://[^>]+)>", last)
        assert match is not None
        self.assertEqual(len(shown) + int(match[1]), 70)
        self.assertEqual(match[2], f"{SITE}/account/names/{GUILD}.{self.campaign.id}")
        for line in shown:  # never cut inside a line
            self.assertTrue(line.endswith("x" * 30 + "**"), line)

    async def test_without_a_website_the_rest_is_still_counted(self) -> None:
        await self.fill(70)
        text, view = await self.card(site="")
        last = text.split("\n")[-1]
        self.assertRegex(last, r"^• …and \d+ more$")
        self.assertNotIn("http", text)
        # the whole message did not fit, so Show all is offered with the connections in it
        select = next(
            c for c in view.children if getattr(c, "placeholder", "") == name_card.SHOW_ALL
        )
        self.assertIn("Connections (70)", [o.label for o in select.options])

    async def test_a_note_and_the_rest_of_the_card_make_room_first(self) -> None:
        await self.fill(70)
        self.bot.settings = dataclasses.replace(self.bot.settings, site_url=SITE)
        it = self.it()
        await name_card.show_card(it, self.campaign.id, self.bell.id, note="✅ " + "y" * 600)
        self.assertLessEqual(len(it.response.sent[0][0]), 2000)

    # ---- the menu ----

    async def test_the_menu_lists_them_in_the_cards_order_and_words(self) -> None:
        await self.know("Kesh")
        await self.know("Hollis", predicate="serves")
        await self.know("Vessa", secret=True)
        text, _ = await self.card()
        view = await self.connect_menu()
        labels = [o.label for o in view.edit.options]
        on_card = [ln[2:].replace("**", "") for ln in text.split("\n") if ln.startswith("• ")]
        self.assertEqual(labels, on_card)
        self.assertTrue(any(label.endswith("🤫") for label in labels))
        self.assertEqual(view.edit.placeholder, "✏️ Edit a connection…")

    async def test_a_manager_is_not_offered_a_secret_one(self) -> None:
        await self.know("Kesh")
        await self.know("Vessa", secret=True)
        view = await self.connect_menu(MANAGER)
        self.assertEqual([o.label for o in view.edit.options], ["knows Kesh"])

    async def test_no_menu_when_there_is_nothing_to_edit(self) -> None:
        view = await self.connect_menu()
        self.assertFalse(hasattr(view, "edit"))

    async def test_twenty_five_fit_and_more_end_with_the_website(self) -> None:
        await self.fill(25)
        view = await self.connect_menu()
        options = view.edit.options
        self.assertEqual(len(options), 25)
        self.assertNotIn(connection_edit.ALL_LABEL, [o.label for o in options])
        await self.know("One more")
        view = await self.connect_menu()
        options = view.edit.options
        self.assertEqual(len(options), 25)
        self.assertEqual(options[-1].label, connection_edit.ALL_LABEL)
        self.assertEqual(options[-1].value, connection_edit.ALL_VALUE)
        self.assertEqual(sum(o.value == connection_edit.ALL_VALUE for o in options), 1)

    async def test_the_website_choice_answers_with_a_link_and_changes_nothing(self) -> None:
        self.bot.settings = dataclasses.replace(self.bot.settings, site_url=SITE)
        before = await self.relations()
        it = self.it()
        await connection_edit.website(it, self.campaign.id)
        text, kw = it.response.sent[0]
        self.assertEqual(text, connection_edit.WEBSITE_LINE)
        (button,) = kw["view"].children
        self.assertEqual(button.url, f"{SITE}/account/names/{GUILD}.{self.campaign.id}")
        self.assertEqual(await self.relations(), before)
        self.bot.settings = dataclasses.replace(self.bot.settings, site_url="")
        again = self.it()
        await connection_edit.website(again, self.campaign.id)
        self.assertIn("no website link", again.response.sent[0][0])

    async def test_choosing_a_connection_opens_its_page_and_the_last_choice_the_website(
        self,
    ) -> None:
        kesh = await self.know("Kesh")
        view = await self.connect_menu()
        (rel,) = await self.relations()
        view.edit._values = [rel.id]
        it = self.it()
        await view._edit_picked(it)
        text, page = it.response.edited[0]
        self.assertIn("Belleros knows Kesh", text)
        self.assertIsInstance(page, connection_edit.OneConnection)
        self.assertTrue(view.is_finished())
        self.assertTrue(kesh)
        again = await self.connect_menu()
        again.edit._values = [connection_edit.ALL_VALUE]
        link = self.it()
        await again._edit_picked(link)
        self.assertFalse(again.is_finished())  # the menu stays; the link is a private reply

    # ---- one connection ----

    async def page(self, who: int = DM) -> Any:
        (rel,) = await self.relations()
        return await self.page_of_relation(rel.id, who)

    async def page_of_relation(self, relation_id: str, who: int = DM) -> Any:
        found = await connection_edit._find(
            self.it(who), self.campaign.id, self.bell.id, relation_id
        )
        return (
            connection_edit.OneConnection(self.campaign.id, self.bell.id, found) if found else None
        )

    def labels(self, page: Any) -> list[str]:
        return [getattr(c, "label", "") for c in page.children]

    async def test_change_the_words_keeps_who_sees_it_and_says_what_it_reads_now(self) -> None:
        await self.know("Kesh", secret=True)
        (old,) = await self.relations()
        page = await self.page()
        words = {o.value: o.label for o in page.words.options}
        self.assertNotIn("knows", words)  # (it already says that)
        self.assertEqual(words["enemy_of"], "is an enemy of")
        page.words._values = ["enemy_of"]
        it = self.it()
        await page.words.callback(it)
        self.assertIn(
            "Changed to: Belleros is an enemy of Kesh. Wrong? Press **Undo**",
            it.response.edited[0][0],
        )
        (now,) = await self.relations()
        self.assertEqual((now.predicate, now.secret), ("enemy_of", True))
        self.assertNotEqual(now.id, old.id)

    async def test_the_note_reads_from_the_card_whichever_way_round_the_row_is_stored(self) -> None:
        """#2509: a connection with no direction ("is an enemy of") is said from the card the DM
        is on, for a row stored with the card's name first and for one stored with it second."""
        for kesh_first in (False, True):
            other = await ui.save_name(
                self.memory, self.campaign, "Kesh" if not kesh_first else "Mira", "npc", [], []
            )
            first, second = (other.id, self.bell.id) if kesh_first else (self.bell.id, other.id)
            await self.memory.add_relation(
                GUILD, self.campaign.id, first, "knows", second,
                source="dm", confidence=1.0, status=CONFIRMED,
            )  # fmt: skip
            self.fresh()
            (link,) = [r for r in await self.relations() if other.id in (r.subject_id, r.object_id)]
            page = await self.page_of_relation(link.id)
            page.words._values = ["enemy_of"]
            it = self.it()
            await page.words.callback(it)
            self.assertIn(
                f"Changed to: Belleros is an enemy of {other.name}. Wrong?",
                it.response.edited[0][0],
            )

    async def test_other_words_through_the_form(self) -> None:
        await self.know("Kesh")
        page = await self.page()
        form = connection_edit.OtherWords(self.campaign.id, self.bell.id, page.relation_id, page)
        form.words._value = "  sworn   on the ashes of "
        it = self.it()
        await form.on_submit(it)
        (now,) = await self.relations()
        self.assertEqual((now.predicate, now.detail), (FALLBACK_PREDICATE, "sworn on the ashes of"))
        self.assertIn("sworn on the ashes of", it.response.edited[0][0])
        again = connection_edit.OtherWords(self.campaign.id, self.bell.id, now.id, page)
        again.words._value = "   "
        empty = self.it()
        await again.on_submit(empty)
        self.assertIn("Type the words", empty.response.sent[0][0])
        (still,) = await self.relations()
        self.assertEqual(still.id, now.id)

    async def test_a_role_typed_from_the_second_names_card_reads_as_stored(self) -> None:
        """#2509: Belleros is the object of the stored row; "is the niece of" typed from his card
        stores him first, and the note must say that, not the reverse."""
        other = await ui.save_name(self.memory, self.campaign, "Kesh", "npc", [], [])
        await self.memory.add_relation(
            GUILD, self.campaign.id, other.id, "knows", self.bell.id,
            source="dm", confidence=1.0, status=CONFIRMED,
        )  # fmt: skip
        self.fresh()
        (rel,) = await self.relations()
        page = await self.page_of_relation(rel.id)
        it = self.it()
        await page._new_words(it, "kin_of", "niece")
        self.assertIn("Changed to: Belleros is the niece of Kesh.", it.response.edited[0][0])
        (now,) = await self.relations()
        self.assertEqual((now.subject_id, now.object_id), (self.bell.id, other.id))

    async def test_the_same_words_change_nothing(self) -> None:
        await self.know("Kesh")
        page = await self.page()
        before = await self.relations()
        it = self.it()
        await page._new_words(it, "knows", "")
        self.assertIn("already says that", str(it.response.sent or it.followup.send.await_args))
        self.assertEqual(await self.relations(), before)

    async def pc(self, name: str, user: int) -> Any:
        made = await ui.save_name(self.memory, self.campaign, name, "npc", [], [])
        await self.memory.confirm_entity(
            GUILD, self.campaign.id, made.id, "player_character", source="dm", played_by=user
        )
        self.fresh()
        return made

    async def who_page(self, who: int = DM) -> Any:
        """The "Who knows this?" page for the card's only connection."""
        page = await self.page(who)
        it = self.it(who)
        await page._who(it)
        return it.response.edited[0]

    async def test_who_knows_this_offers_three_answers_and_the_old_switch_meaning(self) -> None:
        await self.know("Kesh")
        page = await self.page()
        self.assertIn(connection_edit.WHO_BUTTON, self.labels(page))
        self.assertNotIn("🤫 Hide from players", self.labels(page))
        text, view = await self.who_page()
        self.assertIn("Players can see this connection.", text)
        # only me: the old "Hide from players"
        it = self.it()
        await view._only_me(it)
        self.assertIn("Only the DM can see it now", it.response.edited[0][0])
        (hidden,) = await self.relations()
        self.assertTrue(hidden.secret)
        self.assertEqual(
            await self.memory.connection_knowers(GUILD, self.campaign.id, [hidden.id]), {}
        )
        # everyone: the old "Show to players", with the usual Undo
        text, view = await self.who_page()
        self.assertIn("Only the DM can see it.", text)
        it = self.it()
        await view._everyone(it)
        self.assertIn("Players can see it now", it.response.edited[0][0])
        (shown,) = await self.relations()
        self.assertEqual((shown.secret, shown.unrevealed), (False, False))

    async def test_only_some_characters_are_picked_from_the_player_characters(self) -> None:
        await self.know("Kesh")
        mara = await self.pc("Mara", 1001)
        await self.pc("Tavin", 1002)
        await ui.save_name(self.memory, self.campaign, "Old Smith", "npc", [], [])  # (not offered)
        self.fresh()
        _, view = await self.who_page()
        self.assertEqual(sorted(o.label for o in view.pick.options), ["Mara", "Tavin"])
        view.pick._values = [mara.id]
        await view._picked(self.it())
        it = self.it()
        await view._some(it)
        self.assertIn("Only Mara knows it now (and the DM)", it.response.edited[0][0])
        (link,) = await self.relations()
        self.assertTrue(link.secret)  # hidden from the party, and:
        self.assertEqual(
            await self.memory.connection_knowers(GUILD, self.campaign.id, [link.id]),
            {link.id: [mara.id]},
        )
        text, again = await self.who_page()  # the page shows who knows it now
        self.assertIn("Only Mara knows it (and the DM)", text)
        self.assertEqual([o.default for o in again.pick.options if o.label == "Mara"], [True])

    async def test_some_with_nobody_picked_is_refused_and_changes_nothing(self) -> None:
        await self.know("Kesh")
        await self.pc("Mara", 1001)
        _, view = await self.who_page()
        self.assertTrue(view.some.disabled)  # nothing to save until someone is picked
        it = self.it()
        await view._some(it)
        self.assertIn(
            "Choose at least one character", str(it.response.sent or it.followup.send.await_args)
        )
        (link,) = await self.relations()
        self.assertFalse(link.secret)

    async def test_everyone_lets_go_of_who_knew_it(self) -> None:
        await self.know("Kesh", secret=True)
        mara = await self.pc("Mara", 1001)
        _, view = await self.who_page()
        view.pick._values = [mara.id]
        await view._picked(self.it())
        await view._some(self.it())
        _, view = await self.who_page()
        await view._everyone(self.it())
        (link,) = await self.relations()
        self.assertFalse(link.secret)
        self.assertEqual(
            await self.memory.connection_knowers(GUILD, self.campaign.id, [link.id]), {}
        )

    async def test_the_names_read_right_for_one_or_several_and_the_state_is_first(self) -> None:
        self.assertEqual(connection_edit.called(["Mara"]), "Mara")
        self.assertEqual(connection_edit.called(["Mara", "Tavin"]), "Mara and Tavin")
        self.assertEqual(connection_edit.called(["A", "B", "C"]), "A, B and C")
        names = {"1": "Mara", "2": "Tavin"}
        text = connection_edit.who_text("Kesh", "some", ["1", "2"], names, has_characters=True)
        self.assertTrue(text.startswith("**Hidden from the party. Only Mara and Tavin know it"))
        lost = connection_edit.who_text("Kesh", "some", ["9"], names, has_characters=True)
        self.assertIn("Only some characters know it", lost)  # a knower the menu cannot name
        bare = connection_edit.who_text("Kesh", "dm", [], {}, has_characters=False)
        self.assertIn("no player characters yet", bare)

    async def test_the_answer_it_is_already_at_changes_nothing_and_is_marked(self) -> None:
        await self.know("Kesh", secret=True)
        _, view = await self.who_page()
        self.assertEqual(
            [
                c.label
                for c in view.children
                if getattr(c, "style", None) == discord.ButtonStyle.success
            ],
            ["🤫 Only the DM"],
        )
        it = self.it()
        await view._only_me(it)
        self.assertIn("Already like that", str(it.response.sent or it.followup.send.await_args))
        (link,) = await self.relations()
        self.assertTrue(link.secret)

    async def test_a_bad_pick_changes_nothing_not_even_the_hiding(self) -> None:
        await self.know("Kesh")  # (public)
        npc = await ui.save_name(self.memory, self.campaign, "Old Smith", "npc", [], [])
        _, view = await self.who_page()
        view.picked = [npc.id]  # forged: not a player character
        it = self.it()
        await view._some(it)
        self.assertIn("Couldn't change that", str(it.response.sent or it.followup.send.await_args))
        (link,) = await self.relations()
        self.assertFalse(link.secret)  # still public: the check came before the hiding

    async def test_undo_is_offered_only_for_showing_it_and_works(self) -> None:
        await self.know("Kesh", secret=True)
        _, view = await self.who_page()
        it = self.it()
        await view._everyone(it)
        _, card = it.response.edited[0]
        (undo,) = [c for c in card.children if isinstance(c, name_card.UndoConnectionButton)]
        await undo.callback(self.it())
        (again,) = await self.relations()
        self.assertTrue(again.secret)  # showing it was undone
        _, view = await self.who_page()
        it = self.it()
        await view._only_me(it)  # already only the DM's: nothing to undo or write
        self.assertIn("Already like that", str(it.response.sent or it.followup.send.await_args))

    async def test_the_menu_holds_no_more_than_the_server_accepts(self) -> None:
        await self.know("Kesh")
        for k in range(14):
            await self.pc(f"Hero {k:02}", 2000 + k)
        _, view = await self.who_page()
        self.assertEqual(view.pick.max_values, 12)

    async def test_a_manager_cannot_press_the_who_knows_page(self) -> None:
        await self.know("Kesh")
        _, view = await self.who_page()
        await view._only_me(self.it(MANAGER))
        (link,) = await self.relations()
        self.assertFalse(link.secret)

    async def test_a_backstory_connection_is_held_back_until_everyone(self) -> None:
        """#2404: kept from a backstory it waits unrevealed; the DM's Everyone shows it."""
        other = await ui.save_name(self.memory, self.campaign, "Kesh", "npc", [], [])
        await self.memory.add_relation(
            GUILD, self.campaign.id, self.bell.id, "knows", other.id,
            source="dm", confidence=1.0, status=CONFIRMED, unrevealed=True,
        )  # fmt: skip
        self.fresh()
        text, view = await self.who_page()
        self.assertIn("Only the DM can see it.", text)
        it = self.it()
        await view._everyone(it)
        (shown,) = await self.relations()
        self.assertEqual((shown.secret, shown.unrevealed), (False, False))

    async def test_pin_and_unpin_use_what_the_page_showed(self) -> None:
        await self.know("Kesh")
        page = await self.page()
        self.assertIn("📌 Pin as 100% truth", self.labels(page))
        it = self.it()
        await page._pin(it)
        self.assertIn("Pinned:", it.response.edited[0][0])
        (relation,) = await self.relations()
        self.assertEqual(
            await self.memory.pinned_connections(GUILD, self.campaign.id), {relation.id}
        )
        again = self.it()
        await page._pin(again)  # the stale page still says "Pin": a double press changes nothing
        self.assertIn(
            "already like that", str(again.response.sent or again.followup.send.await_args)
        )
        shown = await self.page()
        self.assertIn("📌 Unpin", self.labels(shown))
        await shown._pin(self.it())
        self.assertEqual(await self.memory.pinned_connections(GUILD, self.campaign.id), set())

    async def test_a_manager_has_no_pin(self) -> None:
        await self.know("Kesh")
        dm_page = await self.page()
        await dm_page._pin(self.it(MANAGER))
        self.assertEqual(await self.memory.pinned_connections(GUILD, self.campaign.id), set())

    async def test_a_backstory_connection_is_shown_to_players_from_here_and_undo_hides_it(
        self,
    ) -> None:
        """#2404: kept from a backstory it waits unrevealed, not secret; the switch shows it."""
        other = await ui.save_name(self.memory, self.campaign, "Kesh", "npc", [], [])
        await self.memory.add_relation(
            GUILD, self.campaign.id, self.bell.id, "knows", other.id,
            source="dm", confidence=1.0, status=CONFIRMED, unrevealed=True,
        )  # fmt: skip
        self.fresh()
        _, view = await self.who_page()
        it = self.it()
        await view._everyone(it)
        note, card = it.response.edited[0]
        self.assertIn("Players can see it now", note)
        (shown,) = await self.relations()
        self.assertEqual((shown.secret, shown.unrevealed), (False, False))
        (undo,) = [c for c in card.children if isinstance(c, name_card.UndoConnectionButton)]
        await undo.callback(self.it())
        (held,) = await self.relations()
        self.assertEqual((held.secret, held.unrevealed), (False, True))

    async def test_a_manager_has_no_who_knows_and_cannot_press_a_dm_pages(self) -> None:
        await self.know("Kesh")
        mine = await self.page(MANAGER)
        self.assertNotIn(connection_edit.WHO_BUTTON, self.labels(mine))
        dm_page = await self.page()
        await dm_page._who(self.it(MANAGER))
        (still,) = await self.relations()
        self.assertFalse(still.secret)

    async def test_remove_asks_once_then_undo_puts_it_back_exactly(self) -> None:
        await self.know("Kesh", secret=True)
        (before,) = await self.relations()
        page = await self.page()
        it = self.it()
        await page._remove(it)
        text, ask = it.response.edited[0]
        self.assertIn("Remove **Belleros knows Kesh**?", text)
        self.assertEqual(await self.relations(), [before])  # asking changes nothing
        self.assertIsInstance(ask, connection_edit.ConfirmRemove)
        keep = self.it()
        await ask._keep(keep)
        self.assertEqual(await self.relations(), [before])
        yes = self.it()
        await ask._yes(yes)
        note, card = yes.response.edited[0]
        self.assertIn("Removed: Belleros knows Kesh. Wrong? Press **Undo**", note)
        self.assertEqual(await self.relations(), [])
        (undo,) = [c for c in card.children if isinstance(c, name_card.UndoConnectionButton)]
        back = self.it()
        await undo.callback(back)
        self.assertEqual(await self.relations(), [before])

    async def test_every_press_checks_again(self) -> None:
        await self.know("Kesh")
        page = await self.page()
        (rel,) = await self.relations()
        await self.memory.update_relation(
            GUILD, self.campaign.id, rel.id, status="rejected", source=DM_SOURCE
        )  # another DM took it back meanwhile
        self.fresh()
        for press in (page._who, page._remove):
            it = self.it()
            await press(it)
            self.assertIn(
                connection_edit.GONE_LINK, str(it.response.sent or it.followup.send.await_args)
            )
        form = connection_edit.OtherWords(self.campaign.id, self.bell.id, rel.id, page)
        form.words._value = "owes"
        it = self.it()
        await form.on_submit(it)
        self.assertIn(
            connection_edit.GONE_LINK, str(it.response.sent or it.followup.send.await_args)
        )
        self.assertEqual(await self.relations(), [])
        outsider = self.it(PLAYER)  # someone who may not manage names is turned away at the door
        await connection_edit.show_one(outsider, self.campaign.id, self.bell.id, rel.id)
        self.assertFalse(outsider.response.edited)

    async def test_the_dm_sees_how_a_pronoun_was_settled_in_plain_words_and_nobody_else_does(
        self,
    ) -> None:
        said = "who-is-it: accepted 95%"
        await self.know("Mara")
        (rel,) = [
            r
            for r in await self.memory.relations(GUILD, self.campaign.id)
            if r.predicate == "knows"
        ]
        async with self.db.guild(GUILD) as conn:
            await conn.execute(
                "UPDATE memory_relations SET decided = %s WHERE id = %s", (said, rel.id)
            )
        self.fresh()
        dm = self.it(DM)
        await connection_edit.show_one(dm, self.campaign.id, self.bell.id, rel.id)
        shown = str(dm.response.edited or dm.edit_original_response.await_args)
        self.assertIn("DMbot matched this name from your text.", shown)
        self.assertNotIn("%", shown)
        self.assertNotIn("who-is-it", shown)
        text, _ = await self.card()  # the name card itself never carries it
        self.assertNotIn("DMbot matched", text)
        outsider = self.it(PLAYER)  # and someone who may not manage names is turned away
        await connection_edit.show_one(outsider, self.campaign.id, self.bell.id, rel.id)
        self.assertFalse(outsider.response.edited)

    async def test_the_menu_labels_are_plain_and_within_discords_limit(self) -> None:
        await self.know("K" * 100)
        view = await self.connect_menu()
        (label,) = [o.label for o in view.edit.options]
        self.assertLessEqual(len(label), 100)
        self.assertIsInstance(view.edit, discord.ui.Select)

    async def test_an_oversized_card_still_ends_with_the_link(self) -> None:
        await self.know("Kesh")
        names = await self.bot.lookup.get(GUILD, self.campaign.id)  # type: ignore[union-attr]
        rel = await self.relations()
        text = name_card.card_text(names, self.bell.id, rel, secrets=True, limit=130, more_url=SITE)
        assert text is not None
        self.assertLessEqual(len(text), 130)
        self.assertIn("**Connections:**", text)
        self.assertTrue(text.endswith(f"All of them: <{SITE}>") or "Kesh" in text)

    async def test_new_words_that_already_exist_never_make_a_secret_public(self) -> None:
        kesh = await self.know("Kesh", secret=True)
        await self.memory.add_relation(
            GUILD, self.campaign.id, self.bell.id, "enemy_of", kesh.id,
            source="dm", confidence=1.0, status=CONFIRMED,
        )  # fmt: skip
        self.fresh()
        secret = next(r for r in await self.relations() if r.predicate == "knows")
        page = await self.page_of_relation(secret.id)
        await page._new_words(self.it(), "enemy_of", "")
        mine = await self.relations()
        self.assertEqual([(r.predicate, r.secret) for r in mine], [("enemy_of", True)])

    async def test_only_the_capitals_changed_keeps_the_connection(self) -> None:
        await self.know("Kesh")
        (rel,) = await self.relations()
        await self.memory.update_relation(
            GUILD, self.campaign.id, rel.id, status="rejected", source=DM_SOURCE
        )
        await self.memory.add_relation(
            GUILD, self.campaign.id, self.bell.id, FALLBACK_PREDICATE, rel.object_id
            if rel.subject_id == self.bell.id else rel.subject_id,
            source="dm", confidence=1.0, status=CONFIRMED, detail="sworn to",
        )  # fmt: skip
        self.fresh()
        (free,) = await self.relations()
        page = await self.page_of_relation(free.id)
        form = connection_edit.OtherWords(self.campaign.id, self.bell.id, free.id, page)
        form.words._value = "Sworn To"
        it = self.it()
        await form.on_submit(it)
        self.assertIn("already says that", str(it.response.sent or it.followup.send.await_args))
        (still,) = await self.relations()
        self.assertEqual(still.id, free.id)

    async def test_undo_after_new_words_puts_the_old_words_back_whole(self) -> None:
        await self.know("Kesh")
        (old,) = await self.relations()
        page = await self.page()
        page.words._values = ["enemy_of"]
        it = self.it()
        await page.words.callback(it)
        note, card = it.response.edited[0]
        self.assertIn("Wrong? Press **Undo**", note)
        (undo,) = [c for c in card.children if isinstance(c, name_card.UndoConnectionButton)]
        self.assertEqual([r.predicate for r in await self.relations()], ["enemy_of"])
        await undo.callback(self.it())
        (back,) = await self.relations()  # exactly one connection again
        self.assertEqual((back.id, back.predicate), (old.id, "knows"))
