"""The website's rule lookup (#5156), run as the website's own database role: a DM gets the card
`/dmbot rule` builds (the same search, so the same words), the campaign's house rules and homebrew
come first, a player or a stranger gets "not found", and nothing is stored."""

from __future__ import annotations

from dmbot.rules import lookup
from dmbot.rules.homebrew import HomebrewStore
from dmbot.rules.house import HouseRuleStore
from dmbot.web import rule_lookup
from dmbot.web.recaps import RecapError
from tests.test_web_api import ALICE, THURSDAY
from tests.test_web_homebrew import HomebrewBase


class TheLookup(HomebrewBase):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.alice = self.session

    async def ask(self, who: object, typed: str, ref: str | None = None) -> dict[str, object]:
        return await rule_lookup.look_up(self.web, who, ref or self.cref, typed)  # type: ignore[arg-type]

    async def test_a_dm_gets_the_same_card_the_bot_builds(self) -> None:
        got = await self.ask(self.alice, "Grappled")
        campaign = await self.campaigns.get(THURSDAY.id, self.one.id)
        assert campaign is not None
        want = lookup.find(campaign, "Grappled", [], None, is_dm=True)
        self.assertEqual((got["kind"], got["parts"]), (want.kind, want.parts))
        self.assertEqual(got["kind"], lookup.CARD)
        self.assertIn("Source", "".join(got["parts"]))  # type: ignore[arg-type]

    async def test_a_house_rule_that_names_it_comes_first(self) -> None:
        await HouseRuleStore(self.db).add(
            THURSDAY.id, self.one.id, ALICE.id, "Grappled ends at the start of your turn"
        )
        got = await self.ask(self.alice, "Grappled")
        self.assertIn("Grappled ends at the start of your turn", "".join(got["parts"]))  # type: ignore[arg-type]

    async def test_the_campaigns_homebrew_shadows_the_book_and_is_cited(self) -> None:
        await HomebrewStore(self.db).add(
            THURSDAY.id, self.one.id, ALICE.id, kind="spell", name="Grappled",
            text="Our table's own words.",
        )  # fmt: skip
        got = await self.ask(self.alice, "grappled")
        self.assertEqual(got["kind"], lookup.HOMEBREW)
        self.assertIn("Our table's own words.", "".join(got["parts"]))  # type: ignore[arg-type]

    async def test_a_name_with_no_match_says_so_and_offers_close_names(self) -> None:
        got = await self.ask(self.alice, "Grapled")
        self.assertEqual(got["kind"], lookup.NONE)
        self.assertTrue(got["close"])

    async def test_a_player_a_stranger_and_a_missing_campaign_get_not_found(self) -> None:
        for who, ref in (
            (self.carol, self.cref),  # in the server, not a DM
            (self.bob, self.cref),  # DMs another campaign
            (self.alice, f"{THURSDAY.id}.nosuchcampaign"),
            (self.alice, "nonsense"),
        ):
            with self.subTest(ref=ref), self.assertRaises(RecapError) as caught:
                await self.ask(who, "Grappled", ref)
            self.assertEqual(caught.exception.code, "not_found")

    async def test_an_empty_or_too_long_name_is_refused(self) -> None:
        for typed in ("", "   ", "x" * (rule_lookup.TYPED_MAX + 1)):
            with self.subTest(len=len(typed)), self.assertRaises(RecapError) as caught:
                await self.ask(self.alice, typed)
            self.assertEqual(caught.exception.code, "bad_text")

    async def test_a_shared_books_edition_reaches_the_ranking(self) -> None:
        """The door carries `bookEdition` (0613): a 2014 book's entry named like a 2024 rule gives
        way to the newer rule, as in Discord, so the website's card is the bot's card."""
        await HomebrewStore(self.db).add(
            THURSDAY.id,
            self.one.id,
            ALICE.id,
            kind="spell",
            name="Grappled",
            text="Old book words.",
        )
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute(
                "UPDATE homebrew_entries SET from_book = TRUE, book_edition = '2014'"
                " WHERE guild_id = %s AND campaign_id = %s",
                (THURSDAY.id, self.one.id),
            )
            await conn.execute(
                "UPDATE campaigns SET target_ruleset = '2024' WHERE guild_id = %s AND id = %s",
                (THURSDAY.id, self.one.id),
            )
        campaign = await self.campaigns.get(THURSDAY.id, self.one.id)
        assert campaign is not None
        brew = await HomebrewStore(self.db).find(THURSDAY.id, self.one.id, "Grappled")
        want = lookup.find(campaign, "Grappled", [], brew, is_dm=True)
        got = await self.ask(self.alice, "Grappled")
        self.assertEqual((got["kind"], got["parts"]), (want.kind, want.parts))
