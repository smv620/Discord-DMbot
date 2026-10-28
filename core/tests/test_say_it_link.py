"""The name card's 🎤 Say it link (#2580): it opens that name on the website, the name after `#`
(a fragment never reaches a server log, and a name may be secret), only on the DM's private card,
only with a site address, and not for a name the page would ignore. Invented names throughout."""

from __future__ import annotations

import dataclasses
from typing import Any
from urllib.parse import quote

import discord

from dmbot.ui import all_names, name_card
from dmbot.ui import names as ui
from tests import test_memory_names as base

GUILD, DM, PLAYER = base.GUILD, base.DM, base.PLAYER
SITE = "https://dmbot.example.com"
NAMES = ("Kelvin's Caer", "Brother Aldric", "Belléros")


class TheLink(base.NamesTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        from dmbot.memory.lookup import LookupCache

        self.bot.lookup = LookupCache(self.memory)

    async def card(self, name: str, who: int = DM, *, site: str = SITE) -> Any:
        self.bot.settings = dataclasses.replace(self.bot.settings, site_url=site)
        made = await ui.save_name(self.memory, self.campaign, name, "npc", [], [])
        self.bot.lookup.mark_all_stale()  # type: ignore[union-attr]
        it = self.it(who)
        await name_card.show_card(it, self.campaign.id, made.id)
        return it.response.sent[0][1].get("view")  # (None: a card with no buttons at all)

    @staticmethod
    def links(view: Any) -> list[Any]:
        children = view.children if view is not None else []
        return [c for c in children if getattr(c, "label", "") == all_names.SAY_IT]

    async def test_the_link_has_the_expected_form_for_each_name(self) -> None:
        for name in NAMES:
            with self.subTest(name):
                (button,) = self.links(await self.card(name))
                self.assertEqual(
                    button.url,
                    f"{SITE}/account/names/{GUILD}.{self.campaign.id}#said={quote(name, safe='')}",
                )
                self.assertEqual(button.style, discord.ButtonStyle.link)

    async def test_the_name_is_never_in_a_query(self) -> None:
        for name in NAMES:
            url = self.links(await self.card(name))[0].url
            self.assertNotIn("?", url)
            self.assertNotIn(name, url)  # (only ever encoded)

    async def test_a_player_sees_no_link(self) -> None:
        self.assertEqual(self.links(await self.card("Brother Aldric", PLAYER)), [])

    async def test_without_a_site_address_there_is_no_link(self) -> None:
        self.assertEqual(self.links(await self.card("Brother Aldric", site="")), [])
        self.assertEqual(
            self.links(await self.card("Sir Olwen", site="http://dmbot.example.com")), []
        )

    async def test_a_name_longer_than_the_page_reads_has_no_link(self) -> None:
        longest = "a" * all_names.SAID_MAX
        self.assertEqual(len(self.links(await self.card(longest))), 1)  # exactly the limit
        self.assertEqual(self.links(await self.card("a" * (all_names.SAID_MAX + 1))), [])
        accents = "é" * 20  # each letter is six characters once encoded: 120 > 80
        self.assertEqual(self.links(await self.card(accents)), [])

    async def test_nothing_else_on_the_card_changes(self) -> None:
        with_link = await self.card("Brother Aldric")
        without = await self.card("Sir Olwen", site="")

        def labels(view: Any) -> list[Any]:
            return [getattr(c, "label", None) for c in view.children]

        self.assertEqual(
            [x for x in labels(with_link) if x != all_names.SAY_IT],
            [x for x in labels(without) if x != all_names.SAY_IT],
        )

    async def test_a_full_card_still_builds_and_keeps_every_other_button(self) -> None:
        # the busiest card there is: a promise (its thread buttons) with every undo, Show all, the
        # sheet and the narrator's spelling
        flags: dict[str, Any] = dict(
            others=True, cut=[("connections", "Connections (9)")], sheet=True, thread=True,
            promise=True, led=True, say=True, hide=True, undo_connection=1, undo_spelling=2,
            finish_past=3,
        )  # fmt: skip
        plain = name_card.NameCard("c" * 32, "e" * 32, **flags)
        linked = name_card.NameCard(
            "c" * 32, "e" * 32, say_it_url=f"{SITE}/account/names/1.c#said=x", **flags
        )
        others = [
            getattr(c, "label", None) for c in linked.children if getattr(c, "url", None) is None
        ]
        self.assertEqual(
            others,
            [getattr(c, "label", None) for c in plain.children if getattr(c, "url", None) is None],
        )

    async def test_the_address_helper_edge_cases(self) -> None:
        page = f"{SITE}/account/names/{GUILD}.{'c' * 32}"
        self.assertEqual(all_names.say_it_url(SITE, GUILD, "c" * 32, "  "), "")  # a blank name
        self.assertEqual(
            all_names.say_it_url(SITE + "/", GUILD, "c" * 32, " Orla "), f"{page}#said=Orla"
        )
        self.assertEqual(  # a port stays, and anything after the host is dropped
            all_names.say_it_url("https://dmbot.example.com:8443/x?y=1", GUILD, "c" * 32, "Orla"),
            f"https://dmbot.example.com:8443/account/names/{GUILD}.{'c' * 32}#said=Orla",
        )
