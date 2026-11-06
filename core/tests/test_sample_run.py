"""The sample run (#3547): the one name it may touch, the refusals, every step on a real database
with no AI and no Discord (the bot's part is played by the test), and a report with counts only."""

from __future__ import annotations

import io
import unittest
from argparse import Namespace
from pathlib import Path
from typing import Any

import psycopg

from dmbot.campaigns.models import CampaignError
from dmbot.devtools import graph_key_check
from dmbot.devtools.sample_run import __main__ as cli
from dmbot.devtools.sample_run import plan, steps
from dmbot.devtools.sample_run.plan import NAME, Existing, SampleError, Step
from dmbot.lore_upload import TEXT_MAX
from dmbot.transcript.store import TranscriptStore
from tests.pg import DatabaseTest

GUILD, DM, ELSE = 81, 5, 6
IST, JAC = 9001, 9002
SESSION = "[0:00:05] ist: We open the door.\n[0:01:00] jac: I check the lock.\n"
NOW = 1_800_000_000


class Pure(unittest.TestCase):
    def test_the_name_is_the_one_the_answer_key_scores(self) -> None:
        self.assertEqual(NAME, graph_key_check.SAMPLE_CAMPAIGN)

    def test_players_are_label_account_pairs(self) -> None:
        self.assertEqual(plan.parse_players("ist=1, jac=2"), {"ist": 1, "jac": 2})
        self.assertEqual(plan.parse_players("Mira (mira_v)=7"), {"Mira (mira_v)": 7})
        for bad in ("", "ist", "ist=", "ist=x", "ist=0", "ist=-3", "ist=1,ist=2", "a=1,b=1", "=5"):
            with self.assertRaises(SampleError, msg=bad):
                plan.parse_players(bad)
        with self.assertRaises(SampleError):
            plan.parse_players(",".join(f"p{i}={i + 1}" for i in range(13)))

    def test_only_the_dms_own_import_only_campaign_may_be_replaced(self) -> None:
        mine = Existing("c", DM, frozenset({DM}), sessions=2, imported=2)
        self.assertIsNone(plan.can_replace(mine, DM))
        self.assertIn("not this DM's", plan.can_replace(mine, ELSE) or "")
        other_owner = Existing("c", ELSE, frozenset({DM}), sessions=1, imported=1)
        self.assertIn("not this DM's", plan.can_replace(other_owner, DM) or "")
        played = Existing("c", DM, frozenset({DM}), sessions=3, imported=2)
        self.assertIn("not imported", plan.can_replace(played, DM) or "")

    def test_the_cost_line_adds_up_and_the_report_is_counts_only(self) -> None:
        cost = plan.Cost(8, 20, 3, 96)
        self.assertEqual(cost.total, 127)
        self.assertIn("Up to 127 paid AI calls", cost.line())
        text = "\n".join(
            plan.report(
                [Step("lore read", 61.4, {"names": 40, "calls": 7}), Step("recap", 90.0, {})],
                commit="abc1234",
                switches=["DMBOT_WHO_IS_IT"],
                today="2026-11-06",
            )
        )
        self.assertIn("commit abc1234", text)
        self.assertIn("- lore read: 61 s; names 40, calls 7", text)
        self.assertIn("Switches on: DMBOT_WHO_IS_IT", text)

    def test_switches_name_only_the_flags_that_are_on_and_never_a_secret(self) -> None:
        got = cli.switches(
            {
                "DMBOT_WHO_IS_IT": "1",
                "DMBOT_STORY": "off",
                "DMBOT_TOKEN": "1",
                "DMBOT_DATABASE_URL": "yes",
                "OTHER": "1",
            }
        )
        self.assertEqual(got, ["DMBOT_WHO_IS_IT"])

    def test_the_session_date_is_in_the_past(self) -> None:
        self.assertEqual(cli.session_start(None, NOW), NOW - 7 * 86400)
        self.assertLess(cli.session_start("2026-10-01", NOW), NOW)
        with self.assertRaises(SampleError):
            cli.session_start("2999-01-01", NOW)
        with self.assertRaises(SampleError):
            cli.session_start("not a date", NOW)

    def test_backups_may_not_go_inside_the_repository(self) -> None:
        from dmbot.devtools.common import REPO

        with self.assertRaises(SampleError):
            steps.keep_folder(REPO / "docs")

    def test_the_cost_estimate_grows_with_the_files(self) -> None:
        small = steps.estimate_cost(100, 10, 1_000)
        large = steps.estimate_cost(9_000, 3_000, 400_000)
        self.assertTrue(all(b >= a for a, b in zip(small, large, strict=True)))
        self.assertGreater(large[0], small[0])


class Steps(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.campaigns = steps.campaign_store(self.db)
        self.transcripts = TranscriptStore(self.db)
        self.said: list[str] = []

    def say(self, line: str) -> None:
        self.said.append(line)

    async def made(self, owner: int = DM, name: str = NAME) -> Any:
        return await self.campaigns.create(GUILD, name, owner)

    async def test_a_paid_dm_passes_and_one_without_a_plan_stops_the_run(self) -> None:
        async def paid(*_a: Any) -> Any:
            return None, Namespace(kind="paid")

        async def none(*_a: Any) -> Any:
            return None, Namespace(kind="none")

        await steps.require_paid(self.db, DM, NOW, paid)
        with self.assertRaises(SampleError):
            await steps.require_paid(self.db, DM, NOW, none)

    async def test_the_last_runs_campaign_is_deleted_and_a_new_one_made(self) -> None:
        old = await self.made()
        new = await steps.fresh_campaign(
            self.db, self.campaigns, GUILD, DM, keep_old=False, today="2026-11-06", say=self.say
        )
        self.assertNotEqual(old.id, new.id)
        self.assertEqual(new.name, NAME)
        self.assertIsNone(await self.campaigns.get(GUILD, old.id))
        self.assertEqual(new.owner_user_id, DM)

    async def test_keep_old_renames_it_out_of_the_samples_name_and_never_deletes_it(self) -> None:
        old = await self.made()
        await steps.fresh_campaign(
            self.db, self.campaigns, GUILD, DM, keep_old=True, today="2026-11-06", say=self.say
        )
        kept = await self.campaigns.get(GUILD, old.id)
        assert kept is not None
        self.assertTrue(kept.name.startswith(NAME + " (kept 2026-11-06"))
        # and the next run, which deletes only the exact name, leaves it alone
        await steps.fresh_campaign(
            self.db, self.campaigns, GUILD, DM, keep_old=False, today="2026-11-07", say=self.say
        )
        self.assertIsNotNone(await self.campaigns.get(GUILD, old.id))

    async def test_a_campaign_of_that_name_that_is_not_the_tools_is_refused_untouched(self) -> None:
        theirs = await self.made(owner=ELSE)
        with self.assertRaises(SampleError):
            await steps.fresh_campaign(
                self.db, self.campaigns, GUILD, DM, keep_old=False, today="d", say=self.say
            )
        self.assertIsNotNone(await self.campaigns.get(GUILD, theirs.id))
        # one somebody played in is refused too
        await self.campaigns.delete(GUILD, theirs.id)
        mine = await self.made()
        await self.transcripts.open_session(GUILD, mine.id, NOW - 1000)  # not an import
        with self.assertRaises(SampleError):
            await steps.fresh_campaign(
                self.db, self.campaigns, GUILD, DM, keep_old=False, today="d", say=self.say
            )
        self.assertIsNotNone(await self.campaigns.get(GUILD, mine.id))

    async def test_a_second_campaign_with_another_name_is_never_touched(self) -> None:
        real = await self.made(name="Frostmaiden")
        await steps.fresh_campaign(
            self.db, self.campaigns, GUILD, DM, keep_old=False, today="d", say=self.say
        )
        self.assertIsNotNone(await self.campaigns.get(GUILD, real.id))
        with self.assertRaises(CampaignError):  # (the name is free of clashes only once)
            await self.campaigns.create(GUILD, NAME, DM)

    async def sleep_then(self, change: str, *args: Any) -> Any:
        async def sleep(_s: float) -> None:
            async with self.db.guild(GUILD) as conn:
                await conn.execute(change, args)

        return sleep

    async def test_the_lore_is_queued_like_the_websites_and_the_wait_prints_counts(self) -> None:
        camp = await self.made()
        up = await steps.queue_lore(
            self.db, GUILD, camp.id, DM, "Mira was raised by the Ashen Crown.",
            choice="smart", now=NOW,
        )  # fmt: skip
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                "SELECT state, choice, rights_by, source FROM lore_uploads WHERE id = %s", (up,)
            )
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual(
            (row["state"], row["choice"], row["rights_by"], row["source"]),
            ("waiting", "smart", DM, "file"),
        )
        sleep = await self.sleep_then(
            "UPDATE lore_uploads SET state = 'done', stage = 'done', names_found = 40,"
            " links_found = 55, words_done = 900, calls_made = 7"
        )
        got = await steps.wait_lore(self.db, GUILD, up, say=self.say, sleep=sleep, poll_s=0)
        self.assertEqual(got, {"names": 40, "connections": 55, "words": 900, "calls": 7})
        self.assertTrue(any("lore waiting" in line for line in self.said))
        self.assertNotIn("Ashen", "".join(self.said))  # counts only

    async def test_a_failed_lore_read_and_a_bot_that_never_takes_it_stop_the_run(self) -> None:
        camp = await self.made()
        up = await steps.queue_lore(self.db, GUILD, camp.id, DM, "words", choice="world", now=NOW)
        sleep = await self.sleep_then("UPDATE lore_uploads SET state = 'failed', error = 'no_ai'")
        with self.assertRaises(SampleError) as err:
            await steps.wait_lore(self.db, GUILD, up, say=self.say, sleep=sleep, poll_s=0)
        self.assertIn("no_ai", str(err.exception))
        ticks = iter(range(0, 10**6, 1000))

        async def idle(_s: float) -> None:
            return None

        up2_camp = await self.made(name="x another")
        up2 = await steps.queue_lore(
            self.db, GUILD, up2_camp.id, DM, "words", choice="world", now=NOW
        )
        with self.assertRaises(SampleError) as late:
            await steps.wait_lore(
                self.db, GUILD, up2, say=self.say, sleep=idle,
                clock=lambda: float(next(ticks)), limit_s=2500,
            )  # fmt: skip
        self.assertIn("not read in time", str(late.exception))

    async def test_bad_lore_is_refused_before_anything_is_queued(self) -> None:
        camp = await self.made()
        for text, choice in (("  ", "smart"), ("words", "everything")):
            with self.assertRaises(SampleError):
                await steps.queue_lore(self.db, GUILD, camp.id, DM, text, choice=choice, now=NOW)
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute("SELECT count(*) AS n FROM lore_uploads")
            self.assertEqual((await cur.fetchone() or {})["n"], 0)

    async def planned(self, agreed: Any = (IST, JAC), refused: Any = ()) -> Any:
        return await steps.preflight_session(
            GUILD, {"ist": IST, "jac": JAC}, SESSION, agreed, refused, started_at=NOW - 86400
        )

    async def stored(self) -> tuple[Any, str]:
        camp = await self.made()
        sid = await steps.store_import(
            self.db, GUILD, camp.id, DM, await self.planned(), started_at=NOW - 86400, now=NOW
        )
        return camp, sid

    async def test_the_session_is_stored_as_an_import_the_bot_cleans(self) -> None:
        camp, sid = await self.stored()
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                "SELECT state, imported_by FROM session_imports WHERE session_id = %s", (sid,)
            )
            imp = await cur.fetchone()
            cur = await conn.execute("SELECT count(*) AS n FROM import_consents")
            ticked = (await cur.fetchone() or {})["n"]
            cur = await conn.execute(
                "SELECT line_count, speakers, campaign_id FROM transcript_sessions WHERE id = %s",
                (sid,),
            )
            session = await cur.fetchone()
        assert imp is not None and session is not None
        self.assertEqual((imp["state"], imp["imported_by"]), ("waiting", DM))
        self.assertEqual(ticked, 0)  # the test accounts agreed themselves: nobody was ticked in
        self.assertEqual((session["line_count"], session["speakers"]), (2, [IST, JAC]))
        self.assertEqual(session["campaign_id"], camp.id)
        lines = await self.transcripts.lines(GUILD, sid)
        self.assertEqual([line.user_id for line in lines], [IST, JAC])
        # and the guard sees it as the tool's own, so the next run may replace it
        [seen] = await steps.existing_samples(self.db, self.campaigns, GUILD)
        self.assertIsNone(plan.can_replace(seen, DM))

    async def test_a_stop_half_way_leaves_no_session_to_jam_the_guard(self) -> None:
        camp = await self.made()
        good = await self.planned()
        # (a file name past the column's limit fails the LAST insert; the database says so)
        with self.assertRaises(psycopg.errors.CheckViolation):
            await steps.store_import(
                self.db,
                GUILD,
                camp.id,
                DM,
                good,
                started_at=NOW - 86400,
                now=NOW,
                file_name="x" * 500,
            )
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute("SELECT count(*) AS n FROM transcript_sessions")
            self.assertEqual((await cur.fetchone() or {})["n"], 0)
            cur = await conn.execute("SELECT count(*) AS n FROM transcript_lines")
            self.assertEqual((await cur.fetchone() or {})["n"], 0)

    async def test_everything_that_can_refuse_the_session_is_asked_before_anything_is_spent(
        self,
    ) -> None:
        with self.assertRaises(SampleError) as err:
            await steps.preflight_session(
                GUILD, {"ist": IST}, SESSION, {IST}, (), started_at=NOW - 86400
            )
        self.assertIn("no --players entry", str(err.exception))
        with self.assertRaises(SampleError) as no:
            await self.planned(refused={JAC})
        self.assertIn("said no", str(no.exception))
        with self.assertRaises(SampleError) as quiet:
            await self.planned(agreed={IST})
        self.assertIn("not agreed", str(quiet.exception))
        with self.assertRaises(SampleError):
            await steps.preflight_session(
                GUILD, {"ist": IST, "jac": JAC}, "not a transcript", {IST, JAC}, (),
                started_at=NOW - 86400,
            )  # fmt: skip
        plan_ok = await self.planned()
        self.assertEqual(plan_ok.kept, 2)

    async def test_refusals_are_read_from_the_database(self) -> None:
        self.assertEqual(await steps.refused_accounts(self.db, GUILD, {IST, JAC}), set())
        async with self.db.guild(GUILD) as conn:
            await conn.execute(
                "INSERT INTO consent_refusals (guild_id, user_id, kind, refused_at)"
                " VALUES (%s, %s, 'declined', 1)",
                (GUILD, JAC),
            )
        self.assertEqual(await steps.refused_accounts(self.db, GUILD, {IST, JAC}), {JAC})

    async def test_the_wait_for_the_import_ends_on_the_story_read(self) -> None:
        _camp, sid = await self.stored()
        sleep = await self.sleep_then(
            "UPDATE session_imports SET state = 'done', scan_finished = TRUE,"
            " story_state = 'read', step_total = 2, step_done = 2, words_done = 11, calls_made = 1"
        )
        got = await steps.wait_import(self.db, GUILD, sid, say=self.say, sleep=sleep, poll_s=0)
        self.assertEqual(got, {"lines": 2, "words": 11, "calls": 1, "story": "read"})

    async def done_scan(self, story: str) -> None:
        async with self.db.guild(GUILD) as conn:
            await conn.execute(
                "UPDATE session_imports SET state = 'done', scan_finished = TRUE, story_state = %s",
                (story,),
            )

    async def test_a_story_that_has_not_started_is_given_longer_than_the_bots_rounds(self) -> None:
        _camp, sid = await self.stored()
        await self.done_scan("")
        now = [0.0]

        async def sleep(seconds: float) -> None:
            now[0] += 40  # a round of the bot is 30 s: the story read starts a round later
            if now[0] >= 100:
                async with self.db.guild(GUILD) as conn:
                    await conn.execute("UPDATE session_imports SET story_state = 'read'")

        got = await steps.wait_import(
            self.db, GUILD, sid, say=self.say, sleep=sleep, clock=lambda: now[0], poll_s=0
        )
        self.assertEqual(got["story"], "read")  # not taken for "switched off" after 30 s
        # while one that never starts ends as not wanted, after the grace
        await self.done_scan("")
        now2 = [0.0]

        async def tick(_s: float) -> None:
            now2[0] += 40

        got2 = await steps.wait_import(
            self.db, GUILD, sid, say=self.say, sleep=tick, clock=lambda: now2[0], poll_s=0
        )
        self.assertEqual(got2["story"], "not read")
        self.assertGreaterEqual(now2[0], steps.STORY_GRACE_S)

    async def test_a_story_read_stopped_by_the_limit_is_said_and_the_import_stopped(self) -> None:
        _camp, sid = await self.stored()
        await self.done_scan("waiting")
        now = [0.0]

        async def tick(_s: float) -> None:
            now[0] += 100

        with self.assertRaises(SampleError) as err:
            await steps.wait_import(
                self.db, GUILD, sid, say=self.say, sleep=tick, clock=lambda: now[0], poll_s=0
            )
        self.assertIn("story read is waiting", str(err.exception))

    async def test_a_timeout_takes_the_work_out_of_the_queue(self) -> None:
        camp, sid = await self.stored()
        now = [0.0]

        async def tick(_s: float) -> None:
            now[0] += 10_000

        with self.assertRaises(SampleError):
            await steps.wait_import(
                self.db, GUILD, sid, say=self.say, sleep=tick, clock=lambda: now[0], poll_s=0
            )
        row = await steps._import_row(self.db, GUILD, sid)
        self.assertEqual((row["state"], row["reason"]), ("failed", "stopped"))
        up = await steps.queue_lore(self.db, GUILD, camp.id, DM, "words", choice="world", now=NOW)
        now[0] = 0.0
        with self.assertRaises(SampleError):
            await steps.wait_lore(
                self.db, GUILD, up, say=self.say, sleep=tick, clock=lambda: now[0], poll_s=0
            )
        lore_row = await steps._lore_row(self.db, GUILD, up)
        self.assertEqual(lore_row["state"], "failed")
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute("SELECT text FROM lore_uploads WHERE id = %s", (up,))
            self.assertEqual((await cur.fetchone() or {})["text"], "")  # the words are blanked

    async def test_a_failed_cleaning_stops_the_run(self) -> None:
        _camp, sid = await self.stored()
        sleep = await self.sleep_then(
            "UPDATE session_imports SET state = 'failed', reason = 'names'"
        )
        with self.assertRaises(SampleError):
            await steps.wait_import(self.db, GUILD, sid, say=self.say, sleep=sleep, poll_s=0)

    async def test_lore_that_the_door_would_refuse_is_refused_up_front(self) -> None:
        steps.check_lore("some words", "smart")
        for text, choice in (
            ("x" * (TEXT_MAX + 1), "smart"),
            ("a\x00b", "smart"),
            ("a\ud800b", "smart"),
            ("  ", "smart"),
            ("words", "everything"),
        ):
            with self.assertRaises(SampleError):
                steps.check_lore(text, choice)

    async def test_the_deleted_campaign_is_checked_under_the_lock_for_its_owner(self) -> None:
        old = await self.made()
        # a hand-over between the check and the delete: the delete refuses and nothing is lost
        found = await steps.check_existing(self.db, self.campaigns, GUILD, DM)
        async with self.db.guild(GUILD) as conn:
            await conn.execute(
                "UPDATE campaigns SET owner_user_id = %s WHERE id = %s", (ELSE, old.id)
            )
        with self.assertRaises(CampaignError):  # the campaign store refuses under its lock
            await steps.fresh_campaign(
                self.db, self.campaigns, GUILD, DM, keep_old=False, today="d",
                say=self.say, found=found,
            )  # fmt: skip
        self.assertIsNotNone(await self.campaigns.get(GUILD, old.id))

    async def test_a_look_alike_name_is_a_plain_refusal_and_nothing_was_deleted(self) -> None:
        like = await self.made(name=NAME.upper())
        with self.assertRaises(SampleError) as err:
            await steps.fresh_campaign(
                self.db, self.campaigns, GUILD, DM, keep_old=False, today="d", say=self.say
            )
        self.assertIn("look-alike", str(err.exception))
        self.assertIsNotNone(await self.campaigns.get(GUILD, like.id))

    async def test_the_accept_all_step_gives_counts_only_and_keeps_to_the_sample(self) -> None:
        camp = await self.made()
        accept = cli._accept_all()
        got = await accept(self.db, GUILD, camp.id)
        self.assertEqual(sorted(got), ["accepted", "changes", "left waiting", "refused by a rule"])
        other = await self.made(name="Frostmaiden")
        with self.assertRaises(SampleError):  # (accept_all's WrongCampaign, said plainly)
            await accept(self.db, GUILD, other.id)

    async def test_the_backup_goes_to_a_private_file_and_scores_with_counts_only(self) -> None:
        camp = await self.made()
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            folder = steps.keep_folder(Path(tmp) / "backups")
            path = await steps.backup(self.campaigns, GUILD, camp.id, folder, "lore")
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(folder.stat().st_mode & 0o777, 0o700)
            lines = steps.score(path, "lore")
        self.assertTrue(lines)

    async def test_the_recap_report_is_swallowed_and_only_the_code_comes_back(self) -> None:
        seen: list[list[str]] = []

        async def fake(argv: list[str]) -> int:
            seen.append(argv)
            print("THE RECAP'S OWN WORDS")
            return 0

        out = io.StringIO()
        import contextlib

        with contextlib.redirect_stdout(out):
            code = await steps.write_recap(GUILD, "c1", 123, run=fake)
        self.assertEqual(code, 0)
        self.assertEqual(out.getvalue(), "")
        self.assertEqual(seen, [["--session", str(GUILD), "c1", "123", "--yes"]])
        self.assertNotIn("--audio", seen[0])


class TheCommand(unittest.IsolatedAsyncioTestCase):
    async def test_without_yes_it_says_the_cost_and_runs_nothing(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            lore, session = Path(tmp) / "lore.txt", Path(tmp) / "session.txt"
            lore.write_text("Mira was raised by the Ashen Crown.")
            session.write_text(SESSION)
            args = cli.parse_args(
                ["--guild", "1", "--dm", "2", "--players", "ist=3,jac=4",
                 "--lore", str(lore), "--session", str(session)]
            )  # fmt: skip
            said: list[str] = []
            self.assertEqual(await cli.run(args, say=said.append), 0)
        self.assertTrue(said[0].startswith("Up to "))
        self.assertIn("Add --yes", said[-1])

    async def test_a_bad_lore_choice_or_date_stops_before_the_cost_is_even_printed(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            lore, session = Path(tmp) / "lore.txt", Path(tmp) / "session.txt"
            lore.write_text("Mira was raised by the Ashen Crown.")
            session.write_text(SESSION)
            base = ["--guild", "1", "--dm", "2", "--players", "ist=3,jac=4",
                    "--lore", str(lore), "--session", str(session)]  # fmt: skip
            said: list[str] = []
            for extra in (["--lore-choice", "nope"], ["--date", "2999-01-01"]):
                with self.assertRaises(SampleError):
                    await cli.run(cli.parse_args([*base, *extra]), say=said.append)
            self.assertEqual(said, [])

    async def test_a_bad_player_list_or_file_is_a_plain_refusal(self) -> None:
        args = cli.parse_args(
            ["--guild", "1", "--dm", "2", "--players", "oops", "--lore", "/nonexistent",
             "--session", "/nonexistent"]
        )  # fmt: skip
        with self.assertRaises(SampleError):
            await cli.run(args, say=lambda _l: None)
