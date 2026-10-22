"""The website's All names list (#2294), run as the website's own database role: a DM of the
campaign reads every confirmed name, a server manager who is not one reads the same without what
only a DM sees, anyone else gets "not found", and the page searches, filters, sorts and counts.
Invented names throughout."""

from __future__ import annotations

import time
import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import httpx

from dmbot.memory.models import DM as DM_SOURCE
from dmbot.memory.models import NewName
from dmbot.memory.store import MemoryStore
from dmbot.web import all_names, names, sessions
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
                {"key": "npc", "label": "People", "count": 1},
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


class TheSpeed(Listing):
    async def test_a_big_campaign_reads_fast_before_the_database_has_statistics(self) -> None:
        # Right after a restore or a big import nothing has been ANALYZEd: the secret-spelling check
        # used to run once per entry over all its names (21 s for 5,000 names and 15,000 others).
        names = [
            NewName(
                f"Name {i:05d} Thing", "npc", "confirmed",
                others=(f"Alias a{i}", f"Other b{i}", f"Third c{i}"),
                secrets=(f"Hidden s{i}",) if i % 50 == 0 else (),
            )
            for i in range(3000)
        ]  # fmt: skip
        async with self.db.guild(self.g) as conn:  # (a campaign nobody has analyzed yet)
            await conn.execute("SELECT 1")
        await self.memory._add_names(self.g, self.one.id, names, source=DM_SOURCE)
        started = time.monotonic()
        page = await self.page()
        took = time.monotonic() - started
        self.assertEqual(page["total"], 3003)
        self.assertLess(took, 5.0, f"the list took {took:.1f} s")

    async def test_a_big_list_refreshes_the_statistics_and_a_small_one_does_not(self) -> None:
        from unittest.mock import AsyncMock, patch

        big = [NewName(f"Bulk {i:04d}", "place", "confirmed") for i in range(503)]
        with patch.object(MemoryStore, "analyze", AsyncMock()) as analyzed:
            await self.memory.add_names(self.g, self.one.id, big[:3], source=DM_SOURCE)
            analyzed.assert_not_awaited()
            await self.memory.add_names(self.g, self.one.id, big[3:], source=DM_SOURCE)
            analyzed.assert_awaited_once_with(self.g)

    async def test_refreshing_never_fails_the_import_that_asked(self) -> None:
        await self.memory.analyze(self.g)  # runs
        from unittest.mock import patch

        with patch.object(self.db, "guild", side_effect=RuntimeError("no rights")):
            await self.memory.analyze(self.g)  # best effort: nothing raised

    async def test_the_secret_spelling_check_still_marks_the_same_entries(self) -> None:
        both = await self.add("Veil", "npc")
        await self.alias(both, "veil", secret=True)  # its own spelling, as a secret name
        got = {n["name"]: n for n in (await self.page())["names"]}
        self.assertTrue(got["Veil"]["secret"])
        self.assertFalse(got["Mira Voss"]["secret"])  # a secret *other* name is not the entry's own


class TheSaying(Listing):
    """How to say a name (#2317): a DM's rows carry the id of the entry's main name and how it is
    said; nobody else's do."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.bob = await self.signed(BOB, THURSDAY)
        self.zeth = await self.add("Ka'zeth", "npc")
        await self.alias(self.zeth, "Ka'zeth")  # the entry's own name, as the narrator reads it
        await self.alias(self.zeth, "the Pale King")

    async def rows(self, who: Any = None) -> dict[str, dict[str, Any]]:
        got = await self.page(who)
        return {n["name"]: n for n in got["names"]}

    async def test_a_dm_gets_the_main_names_id_and_no_spelling_yet(self) -> None:
        row = (await self.rows())["Ka'zeth"]
        self.assertRegex(row["aliasId"], r"^[0-9a-f]{32}$")
        self.assertIsNone(row["saidAs"])

    async def test_the_id_is_the_one_the_said_as_door_takes_and_the_spelling_comes_back(
        self,
    ) -> None:
        row = (await self.rows())["Ka'zeth"]
        await names.set_said_as(self.web, self.session, self.cref, row["aliasId"], "Kah-zeth")
        self.assertEqual((await self.rows())["Ka'zeth"]["saidAs"], "Kah-zeth")

    async def test_the_id_is_the_main_name_not_another_name_of_the_entry(self) -> None:
        row = (await self.rows())["Ka'zeth"]
        async with self.db.guild(self.g) as conn:
            cur = await conn.execute(
                "SELECT text FROM memory_aliases WHERE guild_id = %s AND id = %s",
                (self.g, row["aliasId"]),
            )
            found = await cur.fetchone()
        assert found is not None
        self.assertEqual(found["text"], "Ka'zeth")

    async def test_an_entry_with_no_main_name_has_neither_key(self) -> None:
        row = (await self.rows())["Dawnbreaker"]  # added without an alias of its own name
        self.assertNotIn("aliasId", row)
        self.assertNotIn("saidAs", row)

    async def test_a_secret_main_name_has_neither_key_and_its_spelling_is_never_sent(self) -> None:
        vecnor = await self.add("Vecnor", "npc")
        await self.alias(vecnor, "Vecnor", secret=True)
        await self.sql(
            "UPDATE memory_aliases SET said_as = NULL WHERE entity_id = %s", vecnor
        )  # (a secret name may not hold one: the table's own check)
        row = (await self.rows())["Vecnor"]
        self.assertNotIn("aliasId", row)
        self.assertNotIn("saidAs", row)

    async def test_another_campaigns_entry_gets_no_keys_even_when_asked_for_by_id(self) -> None:
        other = await self.memory.add_entity(
            self.g, self.two.id, type="npc", name="Ka'zeth", source=DM_SOURCE, status="confirmed"
        )
        await self.memory.add_alias(
            self.g, self.two.id, other.value.id, "Ka'zeth", kind="short", source=DM_SOURCE,
            status="confirmed",
        )  # fmt: skip
        async with self.web.user(ALICE.id, session=self.session.id_hash) as conn:
            await conn.execute("SELECT set_config('dmbot.guild_id', %s, true)", (str(self.g),))
            cur = await conn.execute(
                "SELECT dmbot_web_all_names_said(%s, %s, %s) AS r",
                (self.g, self.one.id, [other.value.id]),
            )
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual(row["r"], [])

    async def test_a_manager_who_is_not_a_dm_learns_no_spelling_or_id(self) -> None:
        row = (await self.rows())["Ka'zeth"]
        await names.set_said_as(self.web, self.session, self.cref, row["aliasId"], "Kah-zeth")
        theirs = (await self.rows(self.bob))["Ka'zeth"]
        self.assertNotIn("aliasId", theirs)
        self.assertNotIn("saidAs", theirs)

    async def test_the_door_answers_nothing_without_the_server_set_for_the_transaction(
        self,
    ) -> None:
        async with self.web.user(ALICE.id, session=self.session.id_hash) as conn:
            cur = await conn.execute(
                "SELECT dmbot_web_all_names_said(%s, %s, %s) AS r",
                (self.g, self.one.id, [self.zeth]),
            )
            row = await cur.fetchone()
        assert row is not None
        self.assertIsNone(row["r"])

    async def test_the_door_keeps_its_pinned_search_path_and_no_public_execute(self) -> None:
        async with self.db.guild(self.g) as conn:
            cur = await conn.execute(
                "SELECT proconfig::text AS cfg, prosecdef,"
                " has_function_privilege('public', oid, 'EXECUTE') AS public_may"
                " FROM pg_proc WHERE proname = 'dmbot_web_all_names_said'"
            )
            rows = await cur.fetchall()
        self.assertGreaterEqual(len(rows), 1)
        for row in rows:
            self.assertIn("search_path", row["cfg"] or "")
            self.assertTrue(row["prosecdef"])
            self.assertFalse(row["public_may"])


class TheDoorSettings(Listing):
    async def test_the_door_keeps_its_pinned_search_path_and_no_public_execute(self) -> None:
        async with self.db.guild(self.g) as conn:
            cur = await conn.execute(
                "SELECT proname, proconfig::text AS cfg, prosecdef,"
                " has_function_privilege('public', oid, 'EXECUTE') AS public_may"
                " FROM pg_proc WHERE proname = 'dmbot_web_all_names'"
            )
            rows = await cur.fetchall()
        self.assertGreaterEqual(len(rows), 1)  # (one for each test schema)
        for row in rows:
            self.assertIn("search_path", row["cfg"] or "", row["proname"])
            self.assertTrue(row["prosecdef"], row["proname"])
            self.assertFalse(row["public_may"], row["proname"])


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
        for bad in ("abc", "1.5", "", "0", "-2", "99999999999999999999", "²", "--1", "9" * 5000):
            got = await self.client.get(url, params={"page": bad})
            self.assertEqual((got.status_code, got.json()), (400, {"error": "bad_page"}), bad)
        other = await self.client.get(f"/me/campaigns/{self.g}.{'0' * 32}/all-names")
        self.assertEqual((other.status_code, other.json()), (404, {"error": "not_found"}))
        anonymous = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url=API, follow_redirects=False
        )
        self.addAsyncCleanup(anonymous.aclose)
        self.assertEqual((await anonymous.get(url)).status_code, 401)


class TheRestore(unittest.IsolatedAsyncioTestCase):
    """A big restore refreshes the planner's statistics, a failed one does not (#2376)."""

    def bot(self, *, fails: bool = False) -> Any:
        from contextlib import asynccontextmanager
        from unittest.mock import MagicMock

        @asynccontextmanager
        async def lock() -> Any:
            yield

        campaign = SimpleNamespace(id="c1", name="Frostmaiden")
        store = SimpleNamespace(
            get=AsyncMock(return_value=None),
            import_backup=AsyncMock(
                side_effect=RuntimeError("bad copy") if fails else None, return_value=campaign
            ),
        )
        return SimpleNamespace(
            campaigns=store,
            memory=SimpleNamespace(analyze=AsyncMock()),
            restore_gate=AsyncMock(return_value=None),
            session_lock=lambda guild_id: lock(),
            is_campaign_playing=AsyncMock(return_value=False),
            _rules_archive_changed=MagicMock(),
        )

    def interaction(self, bot: Any) -> Any:
        from unittest.mock import MagicMock

        it = MagicMock()
        it.client = bot
        it.guild = SimpleNamespace(id=7)
        it.user = SimpleNamespace(id=9)
        it.response.is_done = MagicMock(return_value=False)
        it.response.edit_message = AsyncMock()
        it.edit_original_response = AsyncMock()
        return it

    async def test_a_successful_restore_refreshes_the_statistics_once(self) -> None:
        from dmbot.ui.dmbot_commands import RestoreChoice

        bot = self.bot()
        await RestoreChoice({}, "Frostmaiden", []).restore(self.interaction(bot), None)
        bot.memory.analyze.assert_awaited_once_with(7)

    async def test_a_failed_restore_does_not(self) -> None:
        from dmbot.ui.dmbot_commands import RestoreChoice

        bot = self.bot(fails=True)
        await RestoreChoice({}, "Frostmaiden", []).restore(self.interaction(bot), None)
        bot.memory.analyze.assert_not_awaited()
