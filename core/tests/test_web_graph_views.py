"""Players View / Full View on the relationship web and the peek (#2404, slice 1): the door
decides, on every call, what the person may have. A DM may ask for either view; a player may peek
only when the campaign's DM screen allows it; a peek tells the DM once for each visit. Invented
names throughout."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from dmbot.campaigns import CampaignStore
from dmbot.dm_screen.peek_notices import PeekNotices, notice
from dmbot.web import relationships
from dmbot.web.recaps import RecapError
from tests.test_recap_web import BOB
from tests.test_web_all_names import MEMBER
from tests.test_web_api import QUILLON
from tests.test_web_relationships import Web


class Views(Web):
    async def setScreen(self, visibility: str) -> None:
        await CampaignStore(self.db).set_dm_screen_visibility(self.g, self.one.id, visibility)

    async def secret_link(self) -> str:
        return await self.link("owns", self.mira, self.keep, "hidden", secret=True)

    def ids(self, got: dict[str, Any]) -> set[str]:
        return {x["id"] for e in got["edges"] for x in e["links"]}

    async def refused_with(self, who: Any, **kw: Any) -> str:
        with self.assertRaises(RecapError) as caught:
            await self.graph(who, **kw)
        return caught.exception.code


class TheDm(Views):
    async def test_a_dm_gets_the_full_view_by_default_and_can_edit(self) -> None:
        secret = await self.secret_link()
        got = await self.graph(center=self.mira, hops=1)
        self.assertEqual((got["view"], got["canEdit"], got["canPeek"]), ("full", True, True))
        self.assertIn(secret, self.ids(got))
        self.assertEqual(got["visibility"], "peek")  # the default

    async def test_players_view_is_exactly_what_a_player_gets_and_read_only(self) -> None:
        secret = await self.secret_link()
        mine = await self.graph(center=self.mira, hops=1, view="players")
        theirs = await self.graph(self.player, center=self.mira, hops=1)
        self.assertEqual((mine["view"], mine["canEdit"]), ("players", False))
        self.assertNotIn(secret, self.ids(mine))
        self.assertEqual(mine["nodes"], theirs["nodes"])
        self.assertEqual(mine["edges"], theirs["edges"])

    async def test_full_marks_secret_links_and_players_view_marks_nothing(self) -> None:
        secret = await self.secret_link()
        full = await self.graph(center=self.mira, hops=1, view="full")
        flags = {x["id"]: x["secret"] for e in full["edges"] for x in e["links"]}
        self.assertTrue(flags[secret])
        players = await self.graph(center=self.mira, hops=1, view="players")
        self.assertFalse(any(x["secret"] for e in players["edges"] for x in e["links"]))

    async def test_a_name_with_a_secret_spelling_is_missing_from_players_view_only(self) -> None:
        hidden = await self.add("Lord Veil", "npc")
        await self.alias(hidden, "Lord Veil", secret=True)
        await self.link("knows", self.mira, hidden, "veil")
        full = await self.graph(center=self.mira, hops=1, view="full")
        players = await self.graph(center=self.mira, hops=1, view="players")
        self.assertIn(hidden, {n["id"] for n in full["nodes"]})
        self.assertNotIn(hidden, {n["id"] for n in players["nodes"]})

    async def test_the_size_follows_the_view(self) -> None:
        await self.secret_link()
        full = await relationships.read_size(
            self.web, self.session, self.cref, center=self.mira, hops=1, view="full"
        )
        players = await relationships.read_size(
            self.web, self.session, self.cref, center=self.mira, hops=1, view="players"
        )
        self.assertGreaterEqual(full["names"], players["names"])

    async def test_a_secret_name_is_left_out_of_the_players_counts_and_the_cap(self) -> None:
        hidden = await self.add("Lord Veil", "npc")
        await self.alias(hidden, "Lord Veil", secret=True)  # the real name is the secret
        await self.link("knows", self.mira, hidden, "veil")
        full = await relationships.read_size(
            self.web, self.session, self.cref, center=self.mira, hops=1, view="full"
        )
        players = await relationships.read_size(
            self.web, self.session, self.cref, center=self.mira, hops=1, view="players"
        )
        self.assertLess(players["names"], full["names"])  # strictly: the secret name is not counted
        with patch.object(relationships, "MAX_NODES", players["names"]):
            cut_full = await self.graph(center=self.mira, hops=1, view="full")
            whole_players = await self.graph(center=self.mira, hops=1, view="players")
        self.assertTrue(cut_full["truncated"])  # the DM's set is bigger than the cap
        self.assertFalse(whole_players["truncated"])  # the players' own set just fits

    async def test_a_pinned_connection_is_marked_for_the_dm_only(self) -> None:
        link = self.links["mira-keep"]
        await self.memory.pin(self.g, self.one.id, link, True, source="dm")

        def pins(got: dict[str, Any]) -> dict[str, bool]:
            return {x["id"]: x["pinned"] for e in got["edges"] for x in e["links"]}

        full = await self.graph(center=self.mira, hops=1, view="full")
        self.assertTrue(pins(full)[link])
        self.assertFalse(pins(full)[self.links["mira-rook"]])
        self.assertFalse(
            any(pins(await self.graph(center=self.mira, hops=1, view="players")).values())
        )
        await self.setScreen("peek")
        peek = await self.graph(self.player, center=self.mira, hops=1, view="full")
        self.assertFalse(any(pins(peek).values()))  # a peek never shows the DM's pin
        await self.memory.pin(self.g, self.one.id, link, False, source="dm")
        self.assertFalse(pins(await self.graph(center=self.mira, hops=1, view="full"))[link])

    async def test_a_view_that_is_neither_is_refused(self) -> None:
        self.assertEqual(await self.refused_with(self.session, view="everything"), "bad_view")


class ThePlayer(Views):
    async def test_a_player_gets_the_players_view_by_default_and_cannot_edit(self) -> None:
        got = await self.graph(self.player, center=self.mira, hops=1)
        self.assertEqual((got["view"], got["canEdit"], got["canPeek"]), ("players", False, True))

    async def test_a_peek_shows_everything_read_only_when_the_dm_screen_lets_players(self) -> None:
        secret = await self.secret_link()
        for visibility in ("peek", "open"):
            await self.setScreen(visibility)
            got = await self.graph(self.player, center=self.mira, hops=1, view="full")
            self.assertEqual((got["view"], got["canEdit"]), ("full", False), visibility)
            self.assertIn(secret, self.ids(got))
            self.assertEqual(got["visibility"], visibility)

    async def test_a_peek_under_a_private_screen_is_refused_on_every_call(self) -> None:
        secret = await self.secret_link()
        await self.setScreen("private")
        self.assertEqual(await self.refused_with(self.player, view="full"), "peek_off")
        got = await self.graph(self.player, center=self.mira, hops=1)  # the normal view still works
        self.assertEqual(
            (got["view"], got["canPeek"], got["visibility"]), ("players", False, "private")
        )
        self.assertNotIn(secret, self.ids(got))
        # opened, then shut again: nothing is remembered, the next call is refused
        await self.setScreen("peek")
        await self.graph(self.player, view="full")
        await self.setScreen("private")
        self.assertEqual(await self.refused_with(self.player, view="full"), "peek_off")

    async def test_a_full_view_asked_for_directly_still_tells_the_dm(self) -> None:
        """No peek call first: the graph door itself notes the visit, once."""
        await self.graph(self.player, view="full")
        await self.graph(self.player, view="full")
        notices = PeekNotices(self.db)
        taken = await notices.take(self.g)
        self.assertEqual([p.campaign_id for p in taken], [self.one.id])
        self.assertEqual(await notices.take(self.g), [])

    async def test_the_players_view_and_a_dms_view_leave_no_notice(self) -> None:
        await self.graph(self.player, view="players")
        await self.graph(self.player)
        await self.graph(view="full")
        await self.graph(view="players")
        self.assertEqual(await PeekNotices(self.db).take(self.g), [])

    async def test_the_size_of_a_peek_is_refused_under_a_private_screen_too(self) -> None:
        await self.setScreen("private")
        with self.assertRaises(RecapError) as caught:
            await relationships.read_size(
                self.web, self.player, self.cref, center=self.mira, hops=1, view="full"
            )
        self.assertEqual(caught.exception.code, "peek_off")

    async def test_asking_for_the_players_view_always_works(self) -> None:
        await self.setScreen("private")
        got = await self.graph(self.player, center=self.mira, hops=1, view="players")
        self.assertEqual(got["view"], "players")

    async def test_someone_outside_the_server_or_another_campaign_is_not_found(self) -> None:
        outsider = await self.signed(BOB, QUILLON)
        self.assertEqual(await self.refused_with(outsider, view="full"), "not_found")
        with self.assertRaises(RecapError) as caught:
            await relationships.read_web(self.web, self.player, f"{self.g}.{'f' * 32}", view="full")
        self.assertEqual(caught.exception.code, "not_found")


class ThePeekNotice(Views):
    async def peek(self, who: Any) -> dict[str, Any]:
        return await relationships.peek(self.web, who, self.cref)

    async def test_the_first_peek_of_a_visit_is_noted_once(self) -> None:
        self.assertEqual(await self.peek(self.player), {"noted": True})
        self.assertEqual(await self.peek(self.player), {"noted": False})  # same visit
        another_visit = await self.signed(BOB, MEMBER)
        self.assertEqual(await self.peek(another_visit), {"noted": True})  # a new sign-in

    async def test_the_bot_posts_it_once_with_the_name_and_a_day_later_it_is_swept(self) -> None:
        await self.peek(self.player)
        clock = [1_800_000_000.0]
        notices = PeekNotices(self.db, clock=lambda: clock[0])
        taken = await notices.take(self.g)
        self.assertEqual([p.campaign_id for p in taken], [self.one.id])
        self.assertEqual(await notices.take(self.g), [])  # never twice
        self.assertEqual(await notices.sweep(self.g), 0)
        clock[0] += 86_400  # a day on: kept, so the same visit is not told twice
        self.assertEqual(await notices.sweep(self.g), 0)
        clock[0] += 31 * 86_400
        self.assertEqual(await notices.sweep(self.g), 1)

    async def test_the_line_is_plain_text_with_no_mention_or_markdown(self) -> None:
        self.assertNotIn("\n", notice("Bo\nsecond line"))
        text = notice("**Bo** @everyone <@123> _x_")
        self.assertNotIn("@everyone", text.replace("@​everyone", ""))
        self.assertTrue(text.startswith("👀 "))
        self.assertTrue(text.endswith("peeked at the Story Graph."))

    async def test_private_screen_refuses_and_leaves_no_notice(self) -> None:
        await self.setScreen("private")
        with self.assertRaises(RecapError) as caught:
            await self.peek(self.player)
        self.assertEqual(caught.exception.code, "peek_off")
        self.assertEqual(await PeekNotices(self.db).take(self.g), [])

    async def test_a_dm_needs_no_notice_and_an_outsider_cannot_ask(self) -> None:
        self.assertEqual(await self.peek(self.session), {"noted": False})
        outsider = await self.signed(BOB, QUILLON)
        with self.assertRaises(RecapError):
            await self.peek(outsider)
        self.assertEqual(await PeekNotices(self.db).take(self.g), [])

    async def test_a_peek_in_one_campaign_is_no_notice_in_another_of_the_same_server(self) -> None:
        await self.peek(self.player)
        taken = await PeekNotices(self.db).take(self.g)
        self.assertEqual([p.campaign_id for p in taken], [self.one.id])
        self.assertNotIn(self.two.id, {p.campaign_id for p in taken})
        async with self.db.guild(self.g) as conn:
            cur = await conn.execute(
                "SELECT count(*) AS n FROM web_peeks WHERE campaign_id = %s", (self.two.id,)
            )
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual(row["n"], 0)

    async def test_a_notice_belongs_to_one_server_and_goes_with_its_campaign(self) -> None:
        await self.peek(self.player)
        self.assertEqual(await PeekNotices(self.db).take(self.g + 1), [])
        async with self.db.guild(self.g) as conn:
            await conn.execute("DELETE FROM campaigns WHERE id = %s", (self.one.id,))
            cur = await conn.execute("SELECT count(*) AS n FROM web_peeks")
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual(row["n"], 0)
