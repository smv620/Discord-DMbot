"""The website's All names list (#2294), run as the website's own database role: a DM of the
campaign reads every confirmed name, a server manager who is not one reads the same without what
only a DM sees, anyone else gets "not found", and the page searches, filters, sorts and counts.
Invented names throughout."""

from __future__ import annotations

from typing import Any

import httpx

from dmbot.memory.models import DM as DM_SOURCE
from dmbot.memory.store import MemoryStore
from dmbot.web import all_names, sessions
from dmbot.web.app import create_app
from dmbot.web.discord import DiscordGuild
from dmbot.web.payments import FakeProvider
from dmbot.web.recaps import RecapError
from tests.test_recap_web import BOB, NOW, Base, callers
from tests.test_web_api import ALICE, API, THURSDAY, FakeDiscord, settings

MEMBER = DiscordGuild(id=THURSDAY.id, name=THURSDAY.name, manage=False)


class Listing(Base):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.memory = MemoryStore(self.db)
        self.g = THURSDAY.id
        self.cref = f"{self.g}.{self.one.id}"
        self.mira = await self.add("Mira Voss", "npc")
        self.add_alias_ids: dict[str, str] = {}
        await self.alias(self.mira, "the Ferrywoman")
        await self.alias(self.mira, "Lady Grey", secret=True)
        self.keep = await self.add("Greywater Keep", "place")
        self.sword = await self.add("Dawnbreaker", "item")

    async def add(self, name: str, kind: str, **kw: Any) -> str:
        made = await self.memory.add_entity(
            self.g, self.one.id, type=kind, name=name, source=DM_SOURCE, status="confirmed", **kw
        )
        return made.value.id

    async def alias(self, entity: str, text: str, *, secret: bool = False) -> None:
        await self.memory.add_alias(
            self.g, self.one.id, entity, text, kind="short", source=DM_SOURCE,
            status="confirmed", secret=secret,
        )  # fmt: skip

    async def sql(self, statement: str, *args: Any) -> None:
        async with self.db.guild(self.g) as conn:
            await conn.execute(statement, args)

    async def signed(self, user: Any, *guilds: Any) -> Any:
        token = await sessions.sign_in(self.web, user, list(guilds), now=NOW, days=30)
        found = await sessions.find(self.web, token, now=NOW)
        assert found is not None
        return found

    async def page(self, who: Any = None, ref: str | None = None, **kw: Any) -> dict[str, Any]:
        return await all_names.read_page(self.web, who or self.session, ref or self.cref, **kw)

    async def refused(self, who: Any, ref: str, **kw: Any) -> str:
        with self.assertRaises(RecapError) as caught:
            await all_names.read_page(self.web, who, ref, **kw)
        return caught.exception.code


class TheDmsList(Listing):
    async def test_a_dm_gets_the_whole_list_with_the_shape_the_page_expects(self) -> None:
        got = await self.page()
        self.assertEqual(
            {k: got[k] for k in ("campaign", "canEdit", "total", "page", "pages", "pageSize")},
            {"campaign": "One", "canEdit": True, "total": 3, "page": 1, "pages": 1, "pageSize": 20},
        )
        by_name = {n["name"]: n for n in got["names"]}
        self.assertEqual(list(by_name), ["Dawnbreaker", "Greywater Keep", "Mira Voss"])  # A to Z
        mira = by_name["Mira Voss"]
        self.assertEqual(mira["kind"], "npc")
        self.assertEqual(mira["others"], ["the Ferrywoman"])
        self.assertEqual(mira["secretOthers"], ["Lady Grey"])  # a DM may see and edit these
        self.assertFalse(mira["secret"])
        self.assertIsNone(mira["npcId"])  # no page was listed for it
        self.assertEqual(
            got["kinds"],
            [
                {"key": "item", "label": "Items", "count": 1},
                {"key": "npc", "label": "NPCs", "count": 1},
                {"key": "place", "label": "Places", "count": 1},
            ],
        )

    async def test_an_npc_with_a_listed_page_carries_its_id(self) -> None:
        await self.sql(
            "INSERT INTO npc_roster (guild_id, campaign_id, entry_id, view, updated_at)"
            " VALUES (%s, %s, %s, '{}', 1)", self.g, self.one.id, self.mira,
        )  # fmt: skip
        by_name = {n["name"]: n for n in (await self.page())["names"]}
        self.assertEqual(by_name["Mira Voss"]["npcId"], self.mira)
        self.assertIsNone(by_name["Greywater Keep"]["npcId"])

    async def test_a_player_character_a_god_and_a_plain_character_have_their_own_kinds(
        self,
    ) -> None:
        await self.add("Rook", "character", role="player_character")
        await self.add("Ilmara", "character", role="god")
        await self.add("Wanderer", "character")
        keys = {k["key"] for k in (await self.page())["kinds"]}
        self.assertTrue({"player_character", "deity", "character"} <= keys)

    async def test_search_finds_a_name_or_any_other_name_and_ignores_case(self) -> None:
        for typed, expected in (
            ("GREY", ["Greywater Keep", "Mira Voss"]),  # the keep's name and Mira's secret name
            ("ferry", ["Mira Voss"]),
            ("  dawn ", ["Dawnbreaker"]),
            ("zzz", []),
        ):
            got = await self.page(q=typed)
            self.assertEqual([n["name"] for n in got["names"]], expected, typed)
            self.assertEqual(got["total"], len(expected))
            self.assertEqual(len(got["kinds"]), 3)  # (the kinds' counts ignore the search)

    async def test_one_row_by_id_for_the_row_of_a_changed_name(self) -> None:
        got = await self.page(only=self.keep)
        self.assertEqual([n["name"] for n in got["names"]], ["Greywater Keep"])
        self.assertEqual(got["total"], 1)

    async def test_a_kind_filters_and_the_total_follows(self) -> None:
        got = await self.page(kind="place")
        self.assertEqual([n["name"] for n in got["names"]], ["Greywater Keep"])
        self.assertEqual(got["total"], 1)

    async def test_pages_of_20_and_a_page_past_the_end_is_refused(self) -> None:
        for n in range(24):
            await self.add(f"Person {n:02d}", "npc")
        first, second = await self.page(kind="npc"), await self.page(kind="npc", page=2)
        self.assertEqual((first["total"], first["pages"], len(first["names"])), (25, 2, 20))
        self.assertEqual(len(second["names"]), 5)
        seen = [n["id"] for n in first["names"] + second["names"]]
        self.assertEqual(len(set(seen)), 25)
        self.assertEqual(await self.refused(self.session, self.cref, page=3), "bad_page")
        self.assertEqual(await self.refused(self.session, self.cref, page=0), "bad_page")
        self.assertEqual(len((await self.page(q="zzz"))["names"]), 0)  # an empty list is page 1

    async def test_a_huge_page_number_is_refused_not_a_server_error(self) -> None:
        for page in (2_000_000_000, 100_001, -1):
            self.assertEqual(await self.refused(self.session, self.cref, page=page), "bad_page")

    async def test_search_characters_are_plain_letters(self) -> None:
        await self.add("100% Sure", "item")
        await self.add("Under_score", "item")
        for typed, expected in (("%", ["100% Sure"]), ("_", ["Under_score"]), ("\\", [])):
            got = await self.page(q=typed)
            self.assertEqual([n["name"] for n in got["names"]], expected, typed)
        long = await self.page(q="x" * 500)
        self.assertEqual(long["total"], 0)  # (cut to 100 letters, no error)

    async def test_a_kind_and_a_search_together_and_a_kind_nobody_has(self) -> None:
        both = await self.page(kind="npc", q="grey")
        self.assertEqual([n["name"] for n in both["names"]], ["Mira Voss"])
        none = await self.page(kind="bogus")
        self.assertEqual((none["names"], none["total"], none["pages"]), ([], 0, 1))

    async def test_only_confirmed_aliases_are_listed_or_searched(self) -> None:
        await self.memory.add_alias(
            self.g, self.one.id, self.mira, "Rejected Name", kind="short", source=DM_SOURCE,
            status="proposed",
        )  # fmt: skip
        await self.alias(self.mira, "Mira Voss")  # its own name again: not an "other" name
        got = await self.page(q="rejected")
        self.assertEqual(got["total"], 0)
        mira = next(n for n in (await self.page())["names"] if n["name"] == "Mira Voss")
        self.assertEqual(mira["others"], ["the Ferrywoman"])

    async def test_a_secret_other_name_does_not_make_the_entry_secret(self) -> None:
        mira = next(n for n in (await self.page())["names"] if n["name"] == "Mira Voss")
        self.assertFalse(mira["secret"])  # (only its own spelling being secret does)

    async def test_a_rejected_or_merged_away_entry_is_not_listed(self) -> None:
        await self.sql("UPDATE memory_entities SET status = 'rejected' WHERE id = %s", self.sword)
        self.assertNotIn("Dawnbreaker", str(await self.page()))

    async def test_newest_first_orders_by_when_it_was_added(self) -> None:
        await self.sql("UPDATE memory_entities SET created_at = 5 WHERE id = %s", self.keep)
        await self.sql("UPDATE memory_entities SET created_at = 9 WHERE id = %s", self.sword)
        await self.sql("UPDATE memory_entities SET created_at = 7 WHERE id = %s", self.mira)
        got = await self.page(sort="newest")
        self.assertEqual(
            [n["name"] for n in got["names"]], ["Dawnbreaker", "Mira Voss", "Greywater Keep"]
        )
        self.assertEqual(await self.refused(self.session, self.cref, sort="oldest"), "bad_sort")

    async def test_only_confirmed_names_and_never_a_person_or_a_guess(self) -> None:
        await self.memory.add_entity(
            self.g, self.one.id, type="npc", name="Maybe Manor", source=DM_SOURCE
        )  # a guess
        await self.sql(
            "INSERT INTO memory_entities (guild_id, campaign_id, id, type, name, description,"
            " status, source, created_at) VALUES (%s, %s, %s, 'person', 'Alice Real', '',"
            " 'confirmed', 'dm', 0)", self.g, self.one.id, "e" * 32,
        )  # fmt: skip
        text = str(await self.page())
        self.assertNotIn("Maybe Manor", text)
        self.assertNotIn("Alice Real", text)

    async def test_another_campaigns_names_never_appear(self) -> None:
        await self.memory.add_entity(
            self.g, self.two.id, type="npc", name="Brynwater", source=DM_SOURCE, status="confirmed"
        )
        self.assertNotIn("Brynwater", str(await self.page()))


class TheManagersList(Listing):
    """Bob manages the server and DMs "Two", not "One"."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.bob = await self.signed(BOB, THURSDAY)
        self.secret = await self.add("Vecnor", "npc")
        await self.alias(self.secret, "Vecnor", secret=True)  # its own spelling is secret
        self.unknown = await self.add("The Hidden Vault", "place")
        await self.sql(
            "UPDATE memory_entities SET hidden_from_players = true WHERE id = %s", self.unknown
        )
        self.quest = await self.add("Find the Lens", "quest")

    async def test_the_dm_sees_every_one_of_them_marked(self) -> None:
        got = await self.page()
        by_name = {n["name"]: n for n in got["names"]}
        self.assertTrue(by_name["Vecnor"]["secret"])
        self.assertTrue(by_name["The Hidden Vault"]["hidden"])
        self.assertIn("Find the Lens", by_name)

    async def test_a_manager_who_is_not_a_dm_gets_no_secret_or_dm_only_names(self) -> None:
        got = await self.page(self.bob)
        self.assertFalse(got["canEdit"])
        names = [n["name"] for n in got["names"]]
        self.assertEqual(names, ["Dawnbreaker", "Greywater Keep", "Mira Voss"])
        mira = next(n for n in got["names"] if n["name"] == "Mira Voss")
        self.assertEqual((mira["others"], mira["secretOthers"]), (["the Ferrywoman"], []))
        self.assertEqual(got["total"], 3)
        self.assertEqual(sum(k["count"] for k in got["kinds"]), 3)  # the counts leave them out
        self.assertEqual(len(got["kinds"]), 3)

    async def test_a_manager_pages_like_a_dm_and_threads_and_deeds_stay_hidden(self) -> None:
        for kind in ("thread", "mystery", "hook", "goal", "promise", "deed"):
            await self.add(f"A {kind}", kind)
        await self.sql(
            "UPDATE memory_entities SET audience_limited = true WHERE id = %s", self.keep
        )
        for n in range(22):
            await self.add(f"Person {n:02d}", "npc")
        got = await self.page(self.bob, kind="npc")
        self.assertEqual((got["total"], got["pages"]), (23, 2))  # Mira and 22 more; not Vecnor
        self.assertEqual(len((await self.page(self.bob, kind="npc", page=2))["names"]), 3)
        self.assertEqual(await self.refused(self.bob, self.cref, kind="npc", page=3), "bad_page")
        rest = str(await self.page(self.bob))
        for hidden in ("A thread", "A mystery", "A hook", "A goal", "A promise", "A deed"):
            self.assertNotIn(hidden, rest)
        self.assertNotIn("Greywater Keep", rest)  # a clue only some know

    async def test_a_secret_spelling_counts_with_apostrophes_hyphens_case_and_a_proposal(
        self,
    ) -> None:
        keep = await self.add("Kazeth", "npc")
        await self.sql(
            "INSERT INTO memory_aliases (guild_id, campaign_id, id, entity_id, text, key, kind,"
            " secret, status, sound_codes, source, created_at) VALUES"
            " (%s, %s, %s, %s, %s, %s, 'full', true, 'proposed', '{}', 'dm', 0)",
            self.g,
            self.one.id,
            "c" * 32,
            keep,
            "Ka'zeth",
            "kazeth-x",
        )  # (the same name but for the apostrophe, only proposed as a secret)
        self.assertNotIn("Kazeth", str(await self.page(self.bob)))
        self.assertIn("Kazeth", str(await self.page()))

    async def test_a_secret_name_cannot_be_found_by_searching_for_it(self) -> None:
        for typed in ("Vecnor", "Lady Grey", "Vault", "Lens"):
            got = await self.page(self.bob, q=typed)
            self.assertEqual((got["names"], got["total"]), ([], 0), typed)
        self.assertNotIn("Lady Grey", str(await self.page(self.bob)))

    async def test_a_member_who_does_not_manage_the_server_gets_not_found(self) -> None:
        member = await self.signed(BOB, MEMBER)
        self.assertEqual(await self.refused(member, self.cref), "not_found")

    async def test_a_stranger_a_server_outside_the_list_and_a_bad_address_get_not_found(
        self,
    ) -> None:
        for ref in (f"999.{self.one.id}", "nonsense", f"{self.g}.{self.one.id}²"):
            self.assertEqual(await self.refused(self.bob, ref), "not_found", ref)
        self.assertEqual(await self.refused(self.bob, f"{self.g}.{'0' * 32}"), "not_found")

    async def test_the_door_answers_nothing_without_the_server_set_for_the_transaction(
        self,
    ) -> None:
        async with self.web.user(ALICE.id, session=self.session.id_hash) as conn:
            cur = await conn.execute(
                "SELECT dmbot_web_all_names(%s, %s, '', '', false, 1, 20, NULL) AS r",
                (self.g, self.one.id),
            )
            row = await cur.fetchone()
        assert row is not None
        self.assertIsNone(row["r"])


class TheDoor(Listing):
    async def test_the_list_door_has_one_caller(self) -> None:
        self.assertEqual(callers("dmbot_web_all_names("), {"schema.py", "web/all_names.py"})


class TheRoute(Listing):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.app = create_app(
            settings(), self.web, FakeDiscord(), payments=FakeProvider(b"h" * 32, "https://s.example"),
            clock=lambda: NOW,
        )  # fmt: skip
        token = await sessions.sign_in(self.web, ALICE, [THURSDAY], now=NOW, days=30)
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url=API, follow_redirects=False
        )
        self.client.cookies.set("__Host-dmbot_session", token, domain="api.dmbot.example")
        self.addAsyncCleanup(self.client.aclose)

    async def test_the_list_over_http(self) -> None:
        got = await self.client.get(
            f"/me/campaigns/{self.cref}/all-names", params={"q": "grey", "sort": "name"}
        )
        self.assertEqual(got.status_code, 200)
        self.assertEqual([n["name"] for n in got.json()["names"]], ["Greywater Keep", "Mira Voss"])

    async def test_refusals_over_http(self) -> None:
        url = f"/me/campaigns/{self.cref}/all-names"
        past = await self.client.get(url, params={"page": 4})
        self.assertEqual((past.status_code, past.json()), (400, {"error": "bad_page"}))
        self.assertEqual((await self.client.get(url, params={"sort": "x"})).status_code, 400)
        other = await self.client.get(f"/me/campaigns/{self.g}.{'0' * 32}/all-names")
        self.assertEqual((other.status_code, other.json()), (404, {"error": "not_found"}))
        anonymous = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url=API, follow_redirects=False
        )
        self.addAsyncCleanup(anonymous.aclose)
        self.assertEqual((await anonymous.get(url)).status_code, 401)
