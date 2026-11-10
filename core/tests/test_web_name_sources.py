"""The Names page's "found in this upload" filter (#3675 Part B, #3879): the uploads a campaign's
names were read from (a DM of the campaign only: a label can name a private backstory), and the
names one or more uploads gave, a merge followed forward. Run as the website's own role. Invented
names."""

from __future__ import annotations

import hashlib
from typing import Any

import httpx

from dmbot.memory.models import DM as DM_SOURCE
from dmbot.web import all_names, sessions
from dmbot.web.app import create_app
from dmbot.web.payments import FakeProvider
from dmbot.web.recaps import RecapError
from tests.test_recap_web import BOB, NOW
from tests.test_web_all_names import MEMBER, Listing
from tests.test_web_api import ALICE, THURSDAY, FakeDiscord, settings
from tests.test_web_relationships import BOB_USER


def choice(upload_key: str) -> str:
    """The id the page is given for an upload: `l` and 16 hex of md5 (0420), never the key."""
    return "l" + hashlib.md5(upload_key.encode()).hexdigest()[:16]


class NameSources(Listing):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        # Mira and the Keep were read from the Brynwater handout; the Keep and the sword from a
        # backstory that names a character (a label a player must never be offered).
        await self.memory.record_name_sources(
            self.g, self.one.id, [self.mira, self.keep], upload_key="up1", label="The handout"
        )
        await self.memory.record_name_sources(
            self.g, self.one.id, [self.keep, self.sword], upload_key="up2",
            label="Tavin's backstory",
        )  # fmt: skip

    async def names_from(self, *keys: str, who: object = None) -> list[str]:
        got = await self.page(who, sources=[choice(k) for k in keys])
        return sorted(n["name"] for n in got["names"])

    async def test_the_dm_is_offered_each_upload_once_with_its_label_newest_first(self) -> None:
        async with self.db.guild(self.g) as conn:
            await conn.execute(
                "UPDATE memory_name_sources SET created_at = 1000 WHERE upload_key = 'up1'"
            )
        got = await all_names.list_sources(self.web, self.session, self.cref)
        self.assertEqual(  # (up1 was read long ago: the backstory is the newer one)
            got["sources"],
            [
                {"id": choice("up2"), "kind": "lore", "label": "Tavin's backstory"},
                {"id": choice("up1"), "kind": "lore", "label": "The handout"},
            ],
        )

    async def test_the_choice_never_carries_the_upload_key_or_its_words(self) -> None:
        text = str(await all_names.list_sources(self.web, self.session, self.cref))
        self.assertNotIn("up1", text)
        self.assertNotIn("up2", text)

    async def test_the_names_from_one_upload_are_only_those(self) -> None:
        self.assertEqual(await self.names_from("up1"), ["Greywater Keep", "Mira Voss"])
        self.assertEqual(await self.names_from("up2"), ["Dawnbreaker", "Greywater Keep"])

    async def test_two_uploads_give_the_names_of_either_once_each(self) -> None:
        self.assertEqual(
            await self.names_from("up1", "up2"), ["Dawnbreaker", "Greywater Keep", "Mira Voss"]
        )

    async def test_an_upload_nobody_read_gives_no_names_not_all_of_them(self) -> None:
        self.assertEqual(await self.names_from("nobody"), [])

    async def test_a_merged_away_name_reads_as_the_kept_one(self) -> None:
        other = await self.add("Mira the Elder", "npc")
        await self.memory.record_name_sources(
            self.g, self.one.id, [other], upload_key="up3", label="The map"
        )
        await self.memory.merge(
            self.g, self.one.id, self.mira, other, source=DM_SOURCE, dm_said_same=True
        )
        self.assertEqual(await self.names_from("up3"), ["Mira Voss"])

    async def test_no_filter_still_lists_every_name(self) -> None:
        self.assertEqual((await self.page())["total"], 3)

    async def test_a_player_and_a_stranger_are_given_nothing(self) -> None:
        bob = await self.signed(BOB, MEMBER)  # in the server, not a DM of the campaign
        with self.assertRaises(RecapError) as listing:
            await all_names.list_sources(self.web, bob, self.cref)
        self.assertEqual(listing.exception.code, "not_found")
        self.assertEqual(await self.refused(bob, self.cref, sources=[choice("up1")]), "not_found")

    async def test_another_campaigns_dm_is_given_nothing(self) -> None:
        other = f"{self.g}.{self.two.id}"
        with self.assertRaises(RecapError):
            await all_names.list_sources(self.web, self.session, other)

    async def door(self, name: str, *args: object) -> Any:
        """A source door called directly, as the website's role, for the DM."""
        async with self.web.user(self.session.user_id, session=self.session.id_hash) as conn:
            await conn.execute("SELECT set_config('dmbot.guild_id', %s, true)", (str(self.g),))
            cur = await conn.execute(
                f"SELECT {name}(%s, %s, %s) AS r", (self.g, self.one.id, *args)
            )
            found = await cur.fetchone()
            assert found is not None
            return found["r"]

    async def test_the_doors_never_reach_another_campaign_s_names_or_labels(self) -> None:
        theirs = await self.memory.add_entity(
            self.g, self.two.id, type="npc", name="Other One", source=DM_SOURCE,
            status="confirmed",
        )  # fmt: skip
        await self.memory.record_name_sources(
            self.g, self.two.id, [theirs.value.id], upload_key="up1", label="Theirs"
        )
        ids = await self.door("dmbot_web_name_source_ids", [choice("up1")])
        self.assertNotIn(theirs.value.id, ids)
        self.assertEqual(len(ids), 2)
        listed = str(await all_names.list_sources(self.web, self.session, self.cref))
        self.assertNotIn("Theirs", listed)

    async def test_a_name_from_another_campaign_s_upload_is_not_found_here(self) -> None:
        mine = await self.memory.add_entity(
            self.g, self.two.id, type="npc", name="Other One", source=DM_SOURCE,
            status="confirmed",
        )  # fmt: skip
        await self.memory.record_name_sources(
            self.g, self.two.id, [mine.value.id], upload_key="up1", label="Theirs"
        )
        self.assertEqual(await self.names_from("up1"), ["Greywater Keep", "Mira Voss"])


class OverHttp(Listing):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        await self.memory.record_name_sources(
            self.g, self.one.id, [self.mira, self.keep], upload_key="up1", label="The handout"
        )
        await self.memory.record_name_sources(
            self.g, self.one.id, [self.keep, self.sword], upload_key="up2", label="A backstory"
        )
        app = create_app(
            settings(), self.web, FakeDiscord(), payments=FakeProvider(b"h" * 32, "https://s.example"),
            clock=lambda: NOW,
        )  # fmt: skip
        token = await sessions.sign_in(self.web, ALICE, [THURSDAY], now=NOW, days=30)
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://web-api.internal.test"
        )
        self.client.cookies.set("__Host-dmbot_session", token, domain="web-api.internal.test")
        self.addAsyncCleanup(self.client.aclose)

    async def test_the_choices_and_the_filter_over_http(self) -> None:
        listed = await self.client.get(f"/me/campaigns/{self.cref}/name-sources")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(len(listed.json()["sources"]), 2)
        got = await self.client.get(
            f"/me/campaigns/{self.cref}/all-names", params={"sources": choice("up1")}
        )
        self.assertEqual([n["name"] for n in got.json()["names"]], ["Greywater Keep", "Mira Voss"])

    async def test_a_player_is_refused_the_choices_over_http(self) -> None:
        token = await sessions.sign_in(self.web, BOB_USER, [MEMBER], now=NOW, days=30)
        self.client.cookies.set("__Host-dmbot_session", token, domain="web-api.internal.test")
        got = await self.client.get(f"/me/campaigns/{self.cref}/name-sources")
        self.assertEqual(got.status_code, 404)

    async def test_a_choice_the_page_never_offers_is_refused(self) -> None:
        url = f"/me/campaigns/{self.cref}/all-names"
        for bad in ("up1", "s3", "l123", choice("up1") + ",x", ",".join([choice("up1")] * 41)):
            got = await self.client.get(url, params={"sources": bad})
            self.assertEqual((got.status_code, got.json()), (400, {"error": "bad_page"}), bad)
