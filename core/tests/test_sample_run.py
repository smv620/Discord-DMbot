"""The sample run (#3547): the one name it may touch, the refusals, every step on a real database
with no AI and no Discord (the bot's part is played by the test), and a report with counts only."""

from __future__ import annotations

import io
import unittest
from argparse import Namespace
from pathlib import Path
from typing import Any, ClassVar

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

    def test_the_players_not_on_discord_are_found_by_their_labels_and_the_old_ones(self) -> None:
        # (#3690) player_jac and friends, the older user_ and bare labels, and "DM"
        names = {label: label for label in ("player_ist", "user_jac", "eri", "player_owe", "DM")}
        roles, dm = plan.off_discord_roles(list(names), names)
        self.assertEqual(
            roles, {"player_ist": "ist", "user_jac": "jac", "eri": "eri", "player_owe": "owe"}
        )
        self.assertEqual(dm, "DM")
        # a label with a username part is read by its name, and case does not matter
        roles, _ = plan.off_discord_roles(
            ["Player_Jac (jac_v)"], {"Player_Jac (jac_v)": "Player_Jac"}
        )
        self.assertEqual(roles, {"Player_Jac (jac_v)": "jac"})
        with self.assertRaises(SampleError):
            plan.off_discord_roles(["stranger"], {"stranger": "stranger"})

    def test_players_are_no_longer_required(self) -> None:
        args = cli.parse_args(["--guild", "1", "--dm", "2", "--lore", "l", "--session", "s"])
        self.assertEqual(args.players, "")

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

    async def test_players_not_on_discord_are_imported_through_the_websites_own_import(
        self,
    ) -> None:
        from dmbot.consent import ConsentStore

        camp = await self.made()
        async with self.db.user(DM) as conn:
            await conn.execute(
                "INSERT INTO web_users (user_id, email, created_at, last_sign_in_at)"
                " VALUES (%s, NULL, 0, 0) ON CONFLICT DO NOTHING",
                (DM,),
            )
        await ConsentStore(self.db).grant(GUILD, DM)  # the DM agreed in Discord
        text = (
            "[0:00:05] player_ist: We open the door.\n[0:01:00] player_jac: I check the lock.\n"
            "[0:02:00] DM: It is stuck.\n"
        )
        async with steps.site_session(self.db, DM, GUILD, now=NOW) as site:
            made = await steps.make_speakers(
                self.db, site, GUILD, camp.id, ["player_ist", "player_jac"]
            )
            self.assertTrue(all(n < 0 for n in made.values()))
            sid = await steps.import_through_site(
                self.db, site, GUILD, camp.id, text,
                {
                    "player_ist": {"confirmed": True},
                    "player_jac": {"confirmed": True},
                    "DM": {"member": str(DM)},
                },
                started_at=NOW - 86400, now=NOW,
            )  # fmt: skip
        ids = await steps.speaker_ids(self.db, GUILD, camp.id)
        self.assertEqual(ids, {"player_ist": made["player_ist"], "player_jac": made["player_jac"]})
        lines = await self.transcripts.lines(GUILD, sid)
        self.assertEqual(sorted(line.user_id for line in lines), sorted([*made.values(), DM]))
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                "SELECT user_id, basis FROM import_consents WHERE campaign_id = %s", (camp.id,)
            )
            records = {int(r["user_id"]): r["basis"] for r in await cur.fetchall()}
            cur = await conn.execute(
                "SELECT count(*) AS n FROM web_sessions WHERE user_id = %s", (DM,)
            )
            left = (await cur.fetchone() or {})["n"]
        # the uploader's confirmation is recorded for each player not on Discord, as the import does
        self.assertEqual(set(records), set(made.values()))
        self.assertEqual(left, 0)  # the throwaway website session is gone

    async def test_a_speaker_nobody_confirmed_is_not_kept(self) -> None:
        from dmbot.consent import ConsentStore

        camp = await self.made()
        async with self.db.user(DM) as conn:
            await conn.execute(
                "INSERT INTO web_users (user_id, email, created_at, last_sign_in_at)"
                " VALUES (%s, NULL, 0, 0) ON CONFLICT DO NOTHING",
                (DM,),
            )
        await ConsentStore(self.db).grant(GUILD, DM)
        text = "[0:00:05] player_ist: Hello.\n[0:01:00] DM: Hi.\n"
        async with steps.site_session(self.db, DM, GUILD, now=NOW) as site:
            sid = await steps.import_through_site(
                self.db, site, GUILD, camp.id, text,
                {"player_ist": "drop", "DM": {"member": str(DM)}},
                started_at=NOW - 86400, now=NOW,
            )  # fmt: skip
        self.assertEqual([line.user_id for line in await self.transcripts.lines(GUILD, sid)], [DM])

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
                ["--guild", "1", "--dm", "2", "--players", "ist=3,jac=4,eri=5,owe=6",
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
            base = ["--guild", "1", "--dm", "2", "--players", "ist=3,jac=4,eri=5,owe=6",
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


class ThePlayerCharacters(DatabaseTest):
    """#3554: the four player characters are set by the run, after counting what the read found."""

    PLAYERS: ClassVar[dict[str, int]] = {"ist": 11, "jac": 12, "eri": 13, "owe": 14}

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        from dmbot.memory.store import MemoryStore

        self.campaigns = steps.campaign_store(self.db)
        self.memory = MemoryStore(self.db)
        self.sample = await self.campaigns.create(GUILD, NAME, DM)

    async def read(self, name: str, role: str | None = "player_character") -> str:
        """A name as the lore read leaves it: proposed, with the role it guessed."""
        from dmbot.memory.models import BACKGROUND_SOURCE

        made = await self.memory.add_entity(
            GUILD, self.sample.id, type="character", name=name, role=role,
            source=BACKGROUND_SOURCE,
        )  # fmt: skip
        return made.value.id

    async def people(self) -> dict[str, tuple[str, str | None, int | None]]:
        found = await self.memory.entities(
            GUILD, self.sample.id, statuses=("proposed", "confirmed")
        )
        # (the store itself adds a person for each account and the party: only characters count)
        return {e.name: (e.status, e.role, e.played_by) for e in found if e.type == "character"}

    async def run_it(self) -> Any:
        from dmbot.devtools.sample_run import characters

        return await characters.correct_player_characters(
            self.db, self.memory, GUILD, self.sample.id, self.PLAYERS
        )

    async def test_the_count_of_what_the_read_found_comes_before_the_fix(self) -> None:
        from dmbot.devtools.sample_run import characters

        await self.read("Cerric Vanya'elen")
        await self.read("Belleros Stormgrace")
        await self.read("Okthandom Korran", role="npc")  # found, but not as a player character
        n = await characters.found_by_the_read(self.db, self.memory, GUILD, self.sample.id)
        self.assertEqual(n, 2)  # (Claude Ambere was not found at all)
        got = await self.run_it()
        self.assertEqual((got.corrected, got.made), (4, 1))

    async def test_all_four_end_as_confirmed_player_characters_of_their_accounts(self) -> None:
        await self.read("Cerric Vanya'elen")
        await self.run_it()
        people = await self.people()
        for (name, label), who in zip(plan.PLAYER_CHARACTERS, (11, 12, 13, 14), strict=True):
            self.assertEqual(people[name], ("confirmed", "player_character", who), label)
        self.assertEqual(len(people), 4)  # nothing doubled

    async def test_a_first_name_the_read_made_is_the_entry_set_not_a_second_one(self) -> None:
        await self.read("Cerric")
        got = await self.run_it()
        self.assertEqual((got.corrected, got.made), (4, 3))
        people = await self.people()
        self.assertEqual(
            sorted(people), ["Belleros Stormgrace", "Cerric", "Claude Ambere", "Okthandom Korran"]
        )
        self.assertEqual(people["Cerric"], ("confirmed", "player_character", 11))

    async def test_running_it_again_changes_nothing_more(self) -> None:
        await self.run_it()
        again = await self.run_it()
        self.assertEqual((again.corrected, again.made), (0, 0))
        self.assertEqual(len(await self.people()), 4)

    async def test_another_campaign_is_refused_and_nothing_is_changed(self) -> None:
        from dmbot.devtools.sample_run import characters

        other = await self.campaigns.create(GUILD, "Frostmaiden", DM)
        with self.assertRaises(SampleError):
            await characters.correct_player_characters(
                self.db, self.memory, GUILD, other.id, self.PLAYERS
            )
        self.assertEqual(
            await self.memory.entities(GUILD, other.id, statuses=("proposed", "confirmed")), []
        )

    def test_the_run_needs_the_four_accounts(self) -> None:
        plan.require_character_players(self.PLAYERS)
        for missing in ("ist", "jac", "eri", "owe"):
            short = {k: v for k, v in self.PLAYERS.items() if k != missing}
            with self.assertRaises(SampleError):
                plan.require_character_players(short)

    async def test_a_secret_alias_or_an_npc_of_the_first_name_is_never_taken(self) -> None:
        """Only an entry's own name counts, and a first-word match must already be a player
        character: someone else's secret name, or an NPC called Claude, is left alone."""
        villain = await self.read("The Pale Hand", role="npc")
        await self.memory.add_alias(
            GUILD, self.sample.id, villain, "Cerric", kind="nickname", source="dm",
            status="confirmed", secret=True,
        )  # fmt: skip
        await self.read("Claude", role="npc")
        got = await self.run_it()
        self.assertEqual(got.made, 4)  # none of the four was found
        people = await self.people()
        self.assertEqual(people["The Pale Hand"][1:], ("npc", None))
        self.assertEqual(people["Claude"][1:], ("npc", None))
        self.assertEqual(people["Claude Ambere"], ("confirmed", "player_character", 14))

    async def test_two_characters_with_one_name_or_a_place_with_it_stop_the_step(self) -> None:
        from dmbot.memory.models import BACKGROUND_SOURCE

        await self.read("Cerric Vanya'elen")
        await self.read("Cerric Vanya'elen")
        with self.assertRaises(SampleError) as two:
            await self.run_it()
        self.assertIn("two characters", str(two.exception))
        await self.memory.add_entity(
            GUILD, self.sample.id, type="place", name="Claude Ambere", source=BACKGROUND_SOURCE
        )
        with self.assertRaises(SampleError):  # (the first error already stops it: use a fresh one)
            await self.run_it()

    async def test_a_place_with_a_characters_name_is_not_doubled(self) -> None:
        from dmbot.memory.models import BACKGROUND_SOURCE

        await self.memory.add_entity(
            GUILD, self.sample.id, type="place", name="Okthandom Korran", source=BACKGROUND_SOURCE
        )
        with self.assertRaises(SampleError) as err:
            await self.run_it()
        self.assertIn("not a character", str(err.exception))
        people = await self.people()
        self.assertEqual(people, {})  # nothing was made before it stopped

    async def test_an_entry_already_right_is_left_and_one_with_the_wrong_player_is_corrected(
        self,
    ) -> None:
        from dmbot.devtools.sample_run import characters

        first = await self.run_it()
        self.assertEqual((first.corrected, first.made), (4, 4))
        again = await self.run_it()
        self.assertEqual((again.corrected, again.made), (0, 0))
        wrong = dict(self.PLAYERS, jac=99)
        fixed = await characters.correct_player_characters(
            self.db, self.memory, GUILD, self.sample.id, wrong
        )
        self.assertEqual((fixed.corrected, fixed.made), (1, 0))
        people = await self.people()
        self.assertEqual(people["Belleros Stormgrace"][2], 99)

    async def test_a_rejected_name_is_not_found_so_the_character_is_made(self) -> None:
        entry = await self.read("Cerric Vanya'elen")
        await self.memory.set_entity_status(GUILD, self.sample.id, entry, "rejected", source="dm")
        got = await self.run_it()
        self.assertEqual(got.made, 4)

    async def test_the_party_is_linked_after_the_four_are_set(self) -> None:
        await self.run_it()
        from dmbot.memory.models import PARTY_SOURCE

        made = await self.memory.entities(GUILD, self.sample.id, statuses=("confirmed",))
        # (the group the sessions' step makes: found by its source, the way the store's rules do)
        self.assertTrue([e for e in made if e.type == "faction" and e.source == PARTY_SOURCE])

    async def test_the_count_is_read_only_and_refuses_another_campaign(self) -> None:
        from dmbot.devtools.sample_run import characters

        await self.read("Cerric Vanya'elen")
        await self.read("Belleros Stormgrace", role="npc")
        n = await characters.found_by_the_read(self.db, self.memory, GUILD, self.sample.id)
        self.assertEqual(n, 1)
        people = await self.people()
        self.assertEqual(people["Cerric Vanya'elen"][0], "proposed")  # nothing was changed
        other = await self.campaigns.create(GUILD, "Frostmaiden", DM)
        with self.assertRaises(SampleError):
            await characters.found_by_the_read(self.db, self.memory, GUILD, other.id)


class TheStepOrder(DatabaseTest):
    """#3554 (owner, 2026-11-06): the lore is scored as the reader left it, THEN the four are
    corrected, and before the session import."""

    async def test_the_four_are_corrected_after_the_lore_score_and_before_the_import(self) -> None:
        order = plan.STEPS
        lore_score = order.index("backup and score (lore)")
        correct = order.index("player characters corrected")
        found = order.index("player characters found by the read")
        accepted = order.index("accept all (lore)")
        imported = order.index("session import")
        self.assertLess(found, accepted)  # counted before anything is accepted
        self.assertLess(lore_score, correct)
        self.assertLess(correct, imported)

    async def test_the_run_follows_that_order_exactly_and_refuses_other_actions(self) -> None:
        seen: list[str] = []
        said: list[str] = []

        def act(name: str) -> Any:
            async def go() -> dict[str, int | str]:
                seen.append(name)
                return {"n": 1}

            return go

        done: list[Step] = []
        await steps.run_steps({name: act(name) for name in plan.STEPS}, done, said.append)
        self.assertEqual(seen, list(plan.STEPS))
        self.assertEqual([s.name for s in done], list(plan.STEPS))
        with self.assertRaises(SampleError):
            await steps.run_steps({"lore read": act("lore read")}, [], said.append)

    async def test_a_stop_part_way_still_reports_what_was_done(self) -> None:
        async def fine() -> dict[str, int | str]:
            return {}

        async def boom() -> dict[str, int | str]:
            raise SampleError("stopped")

        actions = {name: fine for name in plan.STEPS}
        actions["accept all (lore)"] = boom
        done: list[Step] = []
        with self.assertRaises(SampleError):
            await steps.run_steps(actions, done, lambda _l: None)
        self.assertEqual(
            [s.name for s in done], ["lore read", "player characters found by the read"]
        )
