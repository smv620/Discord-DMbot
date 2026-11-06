"""The import page's clock offer (#3199): the offers of an import read for a DM of the campaign
alone, the DM's Set it and Not now queued for the bot, and the offer's row (a minute and a state
code, never a word of a session). Invented names throughout."""

from __future__ import annotations

from typing import Any

from dmbot.memory import web_actions as wa
from dmbot.timebot import clock as game
from dmbot.transcript.import_clock import ImportClockOffers
from dmbot.web import import_clock
from dmbot.web.recaps import RecapError, _scoped
from tests.test_web_api import ALICE
from tests.test_web_relationships import Web

IMPORT = "a" * 32
S1, S2 = "1" * 32, "2" * 32  # (the queue takes 32-letter ids)
MINUTE = game.from_day_and_time(5, 19)


class Offered(Web):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.offers = ImportClockOffers(self.db)
        self.actions = wa.ActionStore(self.db)
        await self.imported(S1, "first.txt", 1000)
        await self.imported(S2, "second.txt", 2000)
        await self.offers.offer(self.g, self.one.id, S1, MINUTE)

    async def imported(self, sid: str, file: str, started: int) -> None:
        await self.sql(
            "INSERT INTO transcript_sessions (id, guild_id, campaign_id, started_at)"
            " VALUES (%s, %s, %s, %s)",
            sid, self.g, self.one.id, started,
        )  # fmt: skip
        await self.sql(
            "INSERT INTO session_imports (guild_id, campaign_id, session_id, import_id, file_name,"
            " imported_by, imported_at, state) VALUES (%s, %s, %s, %s, %s, %s, %s, 'done')",
            self.g, self.one.id, sid, IMPORT, file, ALICE.id, started,
        )  # fmt: skip

    async def rows(self, who: Any = None, campaign: str | None = None) -> list[dict[str, Any]]:
        who = who or self.session
        async with self.web.user(who.user_id, session=who.id_hash) as conn:
            await _scoped(conn, self.g)
            return await import_clock.read_rows(conn, self.g, campaign or self.one.id, IMPORT)

    async def choose(self, body: Any, who: Any = None) -> str:
        return await import_clock.choose(self.web, who or self.session, self.cref, IMPORT, body)

    async def turned_away(self, body: Any, who: Any = None) -> str:
        with self.assertRaises(RecapError) as caught:
            await self.choose(body, who)
        return caught.exception.code


class TheRead(Offered):
    async def test_a_dm_reads_the_offer_by_file_with_a_minute_and_a_state(self) -> None:
        [row] = await self.rows()
        self.assertEqual(
            (row["file"], row["sessionId"], row["state"]), ("first.txt", S1, "offered")
        )
        self.assertEqual(row["minute"], MINUTE)

    async def test_the_page_words_the_game_time_and_nothing_else(self) -> None:
        [row] = await self.rows()
        self.assertEqual(
            import_clock.shown(row),
            {"state": "offered", "label": game.short_label(MINUTE), "updatedAt": row["updatedAt"]},
        )
        await self.offers.settle(self.g, self.one.id, S1, "set")
        [done] = await self.rows()
        shown = import_clock.shown(done)
        assert shown is not None
        self.assertEqual(shown["setTo"], game.short_label(MINUTE))
        self.assertIsNone(import_clock.shown(None))

    async def test_a_player_and_another_campaign_read_nothing(self) -> None:
        self.assertEqual(await self.rows(self.player), [])
        other = await self.campaigns.create(self.g, "Other", self.session.user_id)
        self.assertEqual(await self.rows(campaign=other.id), [])

    async def test_an_offer_older_than_a_week_reads_as_expired(self) -> None:
        await self.sql("UPDATE import_clock_offers SET created_at = created_at - 700000")
        [row] = await self.rows()
        self.assertEqual(row["state"], "expired")

    async def test_a_press_held_for_a_moment_reads_as_offered_and_a_dead_one_as_expired(
        self,
    ) -> None:
        taken = await self.offers.take(self.g, self.one.id, S1)
        assert taken is not None
        self.assertEqual((await self.rows())[0]["state"], "offered")
        await self.sql("UPDATE import_clock_offers SET updated_at = updated_at - 700")
        self.assertEqual((await self.rows())[0]["state"], "expired")


class TheChoice(Offered):
    async def test_set_it_is_queued_against_the_session(self) -> None:
        answer = await self.choose({"file": "first.txt", "choice": "set"})
        self.assertRegex(answer, r"^[0-9a-f]{32}$")
        action = await self.actions.claim_next(self.g)
        assert action is not None
        self.assertEqual((action.action, action.target_id, action.text), ("clock-set", S1, ""))

    async def test_not_now_is_queued_as_a_skip(self) -> None:
        await self.choose({"file": "first.txt", "choice": "not-now"})
        action = await self.actions.claim_next(self.g)
        assert action is not None
        self.assertEqual(action.action, "clock-skip")

    async def test_the_bot_applies_it_through_the_clock_callback(self) -> None:
        calls: list[tuple[str, str, bool, int]] = []

        async def clock(campaign: str, session: str, want: bool, user: int) -> None:
            calls.append((campaign, session, want, user))

        await self.choose({"file": "first.txt", "choice": "set"})
        action = await self.actions.claim_next(self.g)
        assert action is not None
        done = await wa.apply(self.memory, self.g, {ALICE.id}, action, clock=clock)
        self.assertEqual((done.error, calls), ("", [(self.one.id, S1, True, ALICE.id)]))

    async def test_a_demoted_dm_is_not_allowed_and_nothing_is_called(self) -> None:
        async def clock(*_: Any) -> None:
            raise AssertionError("must not be called")

        await self.choose({"file": "first.txt", "choice": "set"})
        action = await self.actions.claim_next(self.g)
        assert action is not None
        done = await wa.apply(self.memory, self.g, set(), action, clock=clock)
        self.assertEqual(done.error, wa.NOT_ALLOWED)

    async def test_without_the_bots_clock_the_action_fails_and_can_be_sent_again(self) -> None:
        await self.choose({"file": "first.txt", "choice": "set"})
        action = await self.actions.claim_next(self.g)
        assert action is not None
        self.assertEqual((await wa.apply(self.memory, self.g, {ALICE.id}, action)).error, wa.FAILED)

    async def test_a_session_with_no_offer_or_a_wrong_file_is_not_found(self) -> None:
        self.assertEqual(
            await self.turned_away({"file": "second.txt", "choice": "set"}), "not_found"
        )
        self.assertEqual(await self.turned_away({"file": "nope.txt", "choice": "set"}), "not_found")

    async def test_an_offer_already_answered_is_not_found(self) -> None:
        await self.offers.settle(self.g, self.one.id, S1, "dismissed")
        self.assertEqual(
            await self.turned_away({"file": "first.txt", "choice": "set"}), "not_found"
        )

    async def test_the_door_checks_the_offer_again(self) -> None:
        async with self.web.user(self.session.user_id, session=self.session.id_hash) as conn:
            await _scoped(conn, self.g)
            cur = await conn.execute(
                "SELECT dmbot_web_name_action_add("
                "%s, %s, NULL, 'clock-set', '', %s, '', false) AS r",
                (self.g, self.one.id, S2),  # a session with no offer
            )
            found = await cur.fetchone()
        assert found is not None
        self.assertEqual(found["r"]["status"], "not_found")

    async def test_a_player_and_a_bad_body_are_refused(self) -> None:
        self.assertEqual(
            await self.turned_away({"file": "first.txt", "choice": "set"}, self.player), "not_a_dm"
        )
        bad: list[Any] = [None, {}, {"file": "first.txt"}, {"file": "", "choice": "set"}]
        bad += [{"file": "first.txt", "choice": "later"}, {"file": 3, "choice": "set"}]
        for body in bad:
            self.assertEqual(await self.turned_away(body), "bad_choice", body)

    async def test_the_choice_carries_no_words_of_a_session(self) -> None:
        await self.choose({"file": "first.txt", "choice": "set"})
        rows = await self.sql_rows("SELECT text, kind, entity_id FROM name_actions")
        self.assertEqual([(r["text"], r["kind"], r["entity_id"]) for r in rows], [("", "", "")])

    async def sql_rows(self, statement: str) -> list[dict[str, Any]]:
        async with self.db.guild(self.g) as conn:
            cur = await conn.execute(statement)
            return list(await cur.fetchall())


class TheStore(Offered):
    async def test_an_offer_is_held_for_one_press_only(self) -> None:
        first = await self.offers.take(self.g, self.one.id, S1)
        second = await self.offers.take(self.g, self.one.id, S1)
        self.assertEqual((first is not None, second), (True, None))
        await self.offers.finish(self.g, self.one.id, S1, "offered")  # refused: back it goes
        self.assertIsNotNone(await self.offers.take_minute(self.g, self.one.id, MINUTE))

    async def test_a_newer_offer_expires_an_older_one_still_waiting(self) -> None:
        await self.offers.offer(self.g, self.one.id, S2, MINUTE + 600)
        old = await self.offers.get(self.g, self.one.id, S1)
        assert old is not None
        self.assertEqual(old.state, "expired")

    async def test_not_now_from_discord_ends_what_is_waiting(self) -> None:
        self.assertEqual(await self.offers.dismiss_all(self.g, self.one.id), 1)
        self.assertEqual(await self.offers.dismiss_all(self.g, self.one.id), 0)

    async def test_a_known_minute_is_told_from_one_never_offered(self) -> None:
        self.assertTrue(await self.offers.known_minute(self.g, self.one.id, MINUTE))
        self.assertFalse(await self.offers.known_minute(self.g, self.one.id, MINUTE + 1))

    async def test_an_offer_goes_with_its_session(self) -> None:
        await self.sql("DELETE FROM transcript_sessions WHERE id = %s", S1)
        self.assertIsNone(await self.offers.get(self.g, self.one.id, S1))
