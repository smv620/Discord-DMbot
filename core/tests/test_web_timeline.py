"""The Story timeline page's data (#2462), run as the website's own database role: sessions in order
with the transcripts page's own numbers, each with its recap (a player sees posted ones only, no
draft; only a game master gets the recap's address) and the names shown to the players at the table
(not an undone one, not a name hidden again, not another writer's, never under the wrong session,
and no secret-named entry for a player), and nothing from another campaign or server."""

from __future__ import annotations

import json
from typing import Any

from dmbot.memory.models import DM as DM_SOURCE
from dmbot.memory.store import MemoryStore
from dmbot.transcript.models import Line
from dmbot.transcript.store import TranscriptStore
from dmbot.web import sessions, timeline
from dmbot.web.recaps import RecapError
from tests.test_recap_web import BOB, NOW, Base, callers
from tests.test_web_api import ALICE, QUILLON, THURSDAY

START = 1_700_000_000
DAY = 86_400
CAROL = type(BOB)(id=3003, name="Carol", email="carol@example.com")  # in the server, no DM at all


class TimelineBase(Base):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.g = THURSDAY.id
        self.cref = f"{self.g}.{self.one.id}"
        self.memory = MemoryStore(self.db)
        store = TranscriptStore(self.db)
        # Session 1 ran an hour; session 2 a day later; another campaign has one of its own.
        first = await store.open_session(self.g, self.one.id, START)
        await store.add_lines(self.g, first, [Line(START * 1000 + 1000, ALICE.id, "a", "a")])
        await store.end_session(self.g, first, START + 3600)
        second = await store.open_session(self.g, self.one.id, START + DAY)
        await store.add_lines(
            self.g, second, [Line((START + DAY) * 1000 + 1000, ALICE.id, "b", "b")]
        )
        await store.end_session(self.g, second, START + DAY + 7200)
        other = await store.open_session(self.g, self.two.id, START)
        await store.add_lines(self.g, other, [Line(START * 1000 + 1000, BOB.id, "c", "c")])
        await store.end_session(self.g, other, START + 60)
        self.bob = await self.signed(BOB)
        self.carol = await self.signed(CAROL)

    async def signed(self, user: Any, guilds: Any = None) -> Any:
        token = await sessions.sign_in(
            self.web, user, guilds if guilds is not None else [THURSDAY], now=NOW, days=30
        )
        found = await sessions.find(self.web, token, now=NOW)
        assert found is not None
        return found

    async def sql(self, statement: str, *args: Any) -> None:
        async with self.db.guild(self.g) as conn:
            await conn.execute(statement, args)

    async def recap(
        self, session_at: int, title: str, *, status: str = "draft", approved: bool = False
    ) -> None:
        await self.sql(
            "INSERT INTO recap_jobs (guild_id, campaign_id, session_at, dm_user_id, state, title,"
            " status, approved_at, created_at) VALUES (%s, %s, %s, %s, 'ready', %s, %s, %s, 1)",
            self.g, self.one.id, session_at, ALICE.id, title, status, 5 if approved else 0,
        )  # fmt: skip

    async def entity(self, name: str, *, hidden: bool = False) -> str:
        made = await self.memory.add_entity(
            self.g, self.one.id, type="character", name=name, source=DM_SOURCE, status="confirmed"
        )
        if hidden:
            await self.sql(
                "UPDATE memory_entities SET hidden_from_players = TRUE WHERE id = %s", made.value.id
            )
        return str(made.value.id)

    version = 1000

    async def reveal(self, entity_id: str, at: int, *, batch: int, source: str = "scan") -> None:
        """A change that showed the entry to the players, as DMbot's scan writes it."""
        type(self).version += 1
        await self.sql(
            "INSERT INTO memory_changes (guild_id, campaign_id, version, batch, table_name, row_id,"
            " op, before, after, source, made_at) VALUES (%s, %s, %s, %s, 'memory_entities', %s,"
            " 'update', %s::jsonb, %s::jsonb, %s, %s)",
            self.g, self.one.id, type(self).version, batch, entity_id,
            json.dumps({"hidden_from_players": True}), json.dumps({"hidden_from_players": False}),
            source, at,
        )  # fmt: skip

    async def undo(self, batch: int, entity_id: str) -> None:
        """An Undo of a reveal: its own change row and the entry hidden again, as the store does."""
        await self.sql(
            "UPDATE memory_entities SET hidden_from_players = TRUE WHERE id = %s", entity_id
        )
        type(self).version += 1
        await self.sql(
            "INSERT INTO memory_changes (guild_id, campaign_id, version, batch, table_name, row_id,"
            " op, source, undoes, made_at) VALUES (%s, %s, %s, %s, 'memory_entities', 'x',"
            " 'update', 'undo', %s, %s)",
            self.g, self.one.id, type(self).version, batch + 1, batch, START,
        )  # fmt: skip

    async def read(self, who: Any, ref: str | None = None) -> Any:
        return await timeline.read_timeline(
            self.web, who, ref or self.cref, now=NOW, enforce_plans=False
        )


class TheSessions(TimelineBase):
    async def test_sessions_come_oldest_first_with_the_transcripts_own_numbers(self) -> None:
        got = await self.read(self.session)
        self.assertEqual(got["name"], "One")
        self.assertEqual(
            [(s["number"], s["startedAt"], s["endedAt"]) for s in got["sessions"]],
            [(1, START, START + 3600), (2, START + DAY, START + DAY + 7200)],
        )

    async def test_it_is_this_campaigns_sessions_only(self) -> None:
        got = await self.read(self.bob, f"{self.g}.{self.two.id}")
        self.assertEqual([s["number"] for s in got["sessions"]], [1])

    async def test_anyone_in_the_server_reads_it_and_nobody_outside_does(self) -> None:
        for who in (self.session, self.bob, self.carol):
            self.assertEqual(len((await self.read(who))["sessions"]), 2)
        elsewhere = await self.signed(CAROL, [QUILLON])
        for who, ref in (
            (elsewhere, self.cref),
            (self.carol, f"{self.g}.nosuchcampaign"),
            (self.carol, "nonsense"),
            (self.carol, f"999.{self.one.id}"),
        ):
            with self.subTest(ref=ref), self.assertRaises(RecapError) as caught:
                await self.read(who, ref)
            self.assertEqual(caught.exception.code, "not_found")


class TheAttribution(TimelineBase):
    """Which session a reveal belongs to, when sessions are close together or have no end."""

    async def make_session(self, start: int, end: int | None) -> None:
        store = TranscriptStore(self.db)
        sid = await store.open_session(self.g, self.one.id, start)
        await store.add_lines(self.g, sid, [Line(start * 1000 + 1000, ALICE.id, "x", "x")])
        if end is not None:
            await store.end_session(self.g, sid, end)

    async def test_the_session_that_was_running_wins_over_an_earlier_ones_grace_window(
        self,
    ) -> None:
        # Session 3 starts 30 minutes after session 2 ended; a name is shown during session 3.
        await self.make_session(START + DAY + 7200 + 1800, START + DAY + 20_000)
        shown = await self.entity("During")
        await self.reveal(shown, START + DAY + 7200 + 2400, batch=1)
        got = (await self.read(self.session))["sessions"]
        self.assertEqual([s["revealed"] for s in got], [[], [], ["During"]])

    async def test_a_session_still_listening_owns_the_names_shown_during_it(self) -> None:
        await self.make_session(START + 2 * DAY, None)
        shown = await self.entity("Midgame")
        await self.reveal(shown, START + 2 * DAY + 3 * 3600, batch=1)  # three hours in
        got = (await self.read(self.session))["sessions"]
        self.assertEqual(got[2]["revealed"], ["Midgame"])


class TheRecaps(TimelineBase):
    async def test_a_game_master_sees_approved_and_posted_recaps_with_their_address(self) -> None:
        await self.recap(START, "The river", status="posted", approved=True)
        await self.recap(START + DAY, "The abbey", approved=True)  # approved, not posted
        got = (await self.read(self.session))["sessions"]
        self.assertEqual(got[0]["recap"], {"title": "The river", "posted": True,
                                           "ref": f"{self.cref}.{START}"})  # fmt: skip
        self.assertEqual(got[1]["recap"]["title"], "The abbey")
        self.assertFalse(got[1]["recap"]["posted"])

    async def test_a_player_sees_posted_titles_only_and_no_address(self) -> None:
        await self.recap(START, "The river", status="posted", approved=True)
        await self.recap(START + DAY, "The abbey", approved=True)
        for who in (self.bob, self.carol):
            got = (await self.read(who))["sessions"]
            self.assertEqual(got[0]["recap"], {"title": "The river", "posted": True, "ref": None})
            self.assertIsNone(got[1]["recap"])

    async def test_a_dropped_recap_is_in_nobodys_timeline(self) -> None:
        await self.recap(START, "Dropped", status="dropped", approved=True)
        for who in (self.session, self.bob):
            self.assertIsNone((await self.read(who))["sessions"][0]["recap"])

    async def test_a_draft_is_in_nobodys_timeline_and_an_untitled_recap_is_left_out(self) -> None:
        await self.recap(START, "A draft")  # not approved, not posted
        await self.recap(START + DAY, "", status="posted", approved=True)
        for who in (self.session, self.bob):
            got = (await self.read(who))["sessions"]
            self.assertEqual([s["recap"] for s in got], [None, None])

    async def test_a_recap_belongs_to_the_session_that_started_when_it_says(self) -> None:
        await self.recap(START + 5, "Nowhere", status="posted", approved=True)
        self.assertEqual(
            [s["recap"] for s in (await self.read(self.session))["sessions"]], [None, None]
        )


class TheReveals(TimelineBase):
    async def test_names_shown_after_a_session_are_under_that_session(self) -> None:
        mira = await self.entity("Mira")
        ash = await self.entity("Ash")
        await self.reveal(mira, START + 3700, batch=1)  # ten minutes after session 1 ended
        await self.reveal(ash, START + DAY + 7300, batch=2)
        for who in (self.session, self.bob):
            got = (await self.read(who))["sessions"]
            self.assertEqual([s["revealed"] for s in got], [["Mira"], ["Ash"]])

    async def test_one_change_that_showed_several_names_lists_them_all_by_name(self) -> None:
        names = [await self.entity(n) for n in ("Zed", "Alder")]
        for e in names:
            await self.reveal(e, START + 3700, batch=1)
        got = (await self.read(self.bob))["sessions"]
        self.assertEqual(got[0]["revealed"], ["Alder", "Zed"])

    async def test_an_undone_change_a_name_hidden_again_and_another_writer_are_left_out(
        self,
    ) -> None:
        undone = await self.entity("Undone")
        hidden_again = await self.entity("Again")
        by_dm = await self.entity("ByDm")
        kept = await self.entity("Kept")
        await self.reveal(undone, START + 3700, batch=10)
        await self.undo(10, undone)
        await self.reveal(hidden_again, START + 3700, batch=20)
        await self.sql(
            "UPDATE memory_entities SET hidden_from_players = TRUE WHERE id = %s", hidden_again
        )
        await self.reveal(by_dm, START + 3700, batch=30, source="dm")
        await self.reveal(kept, START + 3700, batch=40)
        got = (await self.read(self.carol))["sessions"]
        self.assertEqual(got[0]["revealed"], ["Kept"])

    async def test_a_name_shown_again_by_a_redo_is_back_in_the_timeline(self) -> None:
        redone = await self.entity("Redone")
        await self.reveal(redone, START + 3700, batch=50)
        await self.undo(50, redone)
        self.assertEqual((await self.read(self.carol))["sessions"][0]["revealed"], [])
        await self.sql(
            "UPDATE memory_entities SET hidden_from_players = FALSE WHERE id = %s", redone
        )  # the redo
        self.assertEqual((await self.read(self.carol))["sessions"][0]["revealed"], ["Redone"])

    async def test_a_name_shown_in_two_scans_is_listed_once(self) -> None:
        twice = await self.entity("Twice")
        await self.reveal(twice, START + 3700, batch=60)
        await self.reveal(twice, START + 3800, batch=61)
        self.assertEqual((await self.read(self.carol))["sessions"][0]["revealed"], ["Twice"])

    async def test_plot_notes_and_people_are_never_listed_for_a_player(self) -> None:
        names = {}
        for kind in ("quest", "mystery", "thread", "hook", "goal", "promise", "deed", "person"):
            # Made as a character, then given the kind directly: the store refuses to list a person.
            made = await self.entity(f"A {kind}")
            await self.sql("UPDATE memory_entities SET type = %s WHERE id = %s", kind, made)
            names[kind] = made
        shown = await self.entity("Ferryman")
        for n, entity_id in enumerate([*names.values(), shown]):
            await self.reveal(entity_id, START + 3700, batch=70 + n)
        for who in (self.bob, self.carol):
            self.assertEqual((await self.read(who))["sessions"][0]["revealed"], ["Ferryman"])
        # People are never names on this page, for a game master either.
        mine = (await self.read(self.session))["sessions"][0]["revealed"]
        self.assertIn("Ferryman", mine)
        self.assertNotIn("A person", mine)

    async def test_a_name_shown_at_some_other_time_is_not_put_under_a_session(self) -> None:
        late = await self.entity("Late")
        await self.reveal(late, START + 2 * DAY + 90_000, batch=1)  # such as an import, much later
        self.assertEqual(
            [s["revealed"] for s in (await self.read(self.session))["sessions"]], [[], []]
        )

    async def test_an_entry_whose_own_name_is_secret_is_shown_to_a_game_master_not_a_player(
        self,
    ) -> None:
        keeper = await self.entity("The Keeper")
        await self.memory.add_alias(
            self.g, self.one.id, keeper, "The Keeper", kind="short", source=DM_SOURCE,
            status="confirmed", secret=True,
        )  # fmt: skip
        await self.reveal(keeper, START + 3700, batch=1)
        self.assertEqual((await self.read(self.session))["sessions"][0]["revealed"], ["The Keeper"])
        self.assertEqual((await self.read(self.bob))["sessions"][0]["revealed"], [])

    async def test_another_campaigns_reveal_is_not_here(self) -> None:
        elsewhere = await self.memory.add_entity(
            self.g, self.two.id, type="character", name="Elsewhere", source=DM_SOURCE,
            status="confirmed",
        )  # fmt: skip
        async with self.db.guild(self.g) as conn:
            await conn.execute(
                "INSERT INTO memory_changes (guild_id, campaign_id, version, batch, table_name,"
                " row_id, op, before, after, source, made_at) VALUES (%s, %s, 5, 5,"
                " 'memory_entities', %s, 'update', %s::jsonb, %s::jsonb, 'scan', %s)",
                (self.g, self.two.id, elsewhere.value.id,
                 json.dumps({"hidden_from_players": True}),
                 json.dumps({"hidden_from_players": False}), START + 30),
            )  # fmt: skip
        self.assertEqual(
            [s["revealed"] for s in (await self.read(self.session))["sessions"]], [[], []]
        )
        self.assertEqual(
            (await self.read(self.bob, f"{self.g}.{self.two.id}"))["sessions"][0]["revealed"],
            ["Elsewhere"],
        )


class TheDoor(Base):
    async def test_the_door_is_stable_because_it_only_reads(self) -> None:
        # provolatile 's' is STABLE (0206): the door reads and never writes.
        async with self.db.guild(THURSDAY.id) as conn:
            cur = await conn.execute(
                "SELECT provolatile FROM pg_proc WHERE proname = 'dmbot_web_story_timeline'"
            )
            rows = await cur.fetchall()
        self.assertGreaterEqual(len(rows), 1)
        for row in rows:
            self.assertEqual(row["provolatile"], "s")

    async def test_the_door_has_one_caller(self) -> None:
        self.assertEqual(callers("dmbot_web_story_timeline("), {"schema.py", "web/timeline.py"})

    async def test_the_door_keeps_its_pinned_search_path_and_no_public_execute(self) -> None:
        async with self.db.guild(THURSDAY.id) as conn:
            cur = await conn.execute(
                "SELECT proconfig::text AS cfg, prosecdef,"
                " has_function_privilege('public', oid, 'EXECUTE') AS public_may"
                " FROM pg_proc WHERE proname = 'dmbot_web_story_timeline'"
            )
            rows = await cur.fetchall()
        self.assertGreaterEqual(len(rows), 1)
        for row in rows:
            self.assertIn("pg_catalog", row["cfg"] or "")
            self.assertIn("pg_temp", row["cfg"] or "")
            self.assertTrue(row["prosecdef"])
            self.assertFalse(row["public_may"])
