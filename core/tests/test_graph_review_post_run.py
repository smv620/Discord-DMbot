"""Posting the stored graph improvement reports (#3742, slice B): the bot's side, on a real database
with GitHub faked. Who may be quoted is read at posting time, a campaign that stopped sharing posts
nothing, a report that was posted but never marked is found and not posted twice, a failure waits an
hour and stops after five. Invented names."""

from __future__ import annotations

import json
from typing import Any

from dmbot.campaigns import CampaignStore
from dmbot.config import ConfigError, load_settings
from dmbot.memory import graph_review_post as gp
from dmbot.memory import graph_review_post_run as run
from tests.pg import DatabaseTest
from tests.test_graph_review_post import ALICE, BOB, FakeGitHub, verdict

GUILD = 111
NOW = 1_800_000_000


class Posting(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.clock = [float(NOW)]
        self.campaign = (await CampaignStore(self.db).create(GUILD, "Frost", ALICE)).id
        await self.sql("UPDATE campaigns SET share_improve = true WHERE id = %s", self.campaign)
        self.store = run.GraphReportStore(self.db, clock=lambda: self.clock[0])
        self.people = {ALICE, BOB}
        self.fake = FakeGitHub()

    async def sql(self, statement: str, *args: Any) -> None:
        async with self.db.guild(GUILD) as conn:
            await conn.execute(statement, args)

    async def report(
        self, rid: str, *, at: int = 1000, speakers: tuple[int, ...] = (ALICE,),
        verdicts: dict[str, Any] | bool | None = True,
    ) -> None:  # fmt: skip
        body = (
            {"items": [verdict(speaker_ids=list(speakers))], "more_not_shown": 0}
            if verdicts is True
            else verdicts
        )
        await self.sql(
            "INSERT INTO graph_reviews (guild_id, campaign_id, id, created_at, checked, missed,"
            " report, speaker_ids, prompt_version, model, verdicts)"
            " VALUES (%s, %s, %s, %s, 2, 1, 'Markdown', %s, 'graph-review-1', 'm', %s::jsonb)",
            GUILD, self.campaign, rid * 32, at, list(speakers),
            None if body is None else json.dumps(body),
        )  # fmt: skip

    def poster(self) -> run.ReportPoster:
        async def consenting(guild_id: int) -> set[int]:
            return set(self.people)

        async def allowed(people: Any) -> set[int]:
            return set(people)

        return run.ReportPoster(
            self.store,
            gp.GitHub("smv620/dmbot-improvements", self.fake),
            consenting=consenting,
            improvement_allowed=allowed,
        )

    async def row(self, rid: str) -> dict[str, Any]:
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                "SELECT posted_at, discussion, held, post_failures, posting_at FROM graph_reviews"
                " WHERE id = %s",
                (rid * 32,),
            )
            found = await cur.fetchone()
        assert found is not None
        return dict(found)

    def created(self) -> int:
        return len([c for c in self.fake.calls if c[0].startswith("mutation")])

    async def test_a_report_is_posted_once_and_marked_with_its_discussion(self) -> None:
        await self.report("a")
        self.assertEqual(await self.poster().round(GUILD), 1)
        row = await self.row("a")
        self.assertEqual((row["posted_at"], row["held"], row["post_failures"]), (NOW, "", 0))
        self.assertGreater(row["discussion"], 0)
        self.assertEqual(await self.poster().round(GUILD), 0)  # nothing more to post
        self.assertEqual(self.created(), 1)
        body = self.fake.discussions[0]["body"]
        self.assertIn(f"<!-- dmbot-report:{'a' * 32} -->", body)

    async def test_one_report_a_round_oldest_first(self) -> None:
        await self.report("b", at=2000)
        await self.report("a", at=1000)
        await self.poster().round(GUILD)
        self.assertIsNotNone((await self.row("a"))["posted_at"])
        self.assertIsNone((await self.row("b"))["posted_at"])
        await self.poster().round(GUILD)
        self.assertIsNotNone((await self.row("b"))["posted_at"])

    async def test_a_campaign_that_stopped_sharing_posts_nothing_until_it_shares_again(
        self,
    ) -> None:
        await self.report("a")
        await self.sql("UPDATE campaigns SET share_improve = false WHERE id = %s", self.campaign)
        self.assertEqual(await self.poster().round(GUILD), 0)
        self.assertEqual((await self.row("a"))["held"], "not_sharing")
        self.assertEqual(self.created(), 0)
        await self.sql("UPDATE campaigns SET share_improve = true WHERE id = %s", self.campaign)
        self.clock[0] += 3601  # looked at again an hour later
        self.assertEqual(await self.poster().round(GUILD), 1)
        self.assertEqual((await self.row("a"))["held"], "")

    async def test_a_speaker_who_stopped_holds_the_whole_report(self) -> None:
        await self.report("a", speakers=(ALICE, BOB))
        self.people = {ALICE}  # BOB stopped being recorded since the report was made
        self.assertEqual(await self.poster().round(GUILD), 0)
        self.assertEqual((await self.row("a"))["held"], "speaker_stopped")
        self.assertEqual(self.created(), 0)

    async def test_a_report_with_no_verdicts_is_never_taken(self) -> None:
        await self.report("a", verdicts=None)
        self.assertEqual(await self.poster().round(GUILD), 0)
        self.assertEqual((await self.row("a"))["posting_at"], None)  # not even leased
        self.assertEqual(self.fake.calls, [])  # and GitHub was never asked anything

    async def test_a_report_that_went_out_but_was_never_marked_is_found_not_posted_again(
        self,
    ) -> None:
        await self.report("a")
        post = gp.render(
            gp.Stored("a" * 32, self.campaign, 1000, None, 2, 1, (ALICE,),
                      {"items": [verdict()], "more_not_shown": 0}),
            allowed_speakers={ALICE}, sharing=True,
        )  # fmt: skip
        assert isinstance(post, gp.Post)
        self.fake.discussions.append({"number": 41, "body": post.body})
        await self.poster().round(GUILD)
        self.assertEqual((await self.row("a"))["discussion"], 41)
        self.assertEqual(self.created(), 0)

    async def test_a_failure_waits_an_hour_and_five_failures_stop_the_report(self) -> None:
        await self.report("a")

        async def down(query: str, variables: Any) -> dict[str, Any]:
            raise gp.PostError("http_502")

        broken = run.ReportPoster(
            self.store, gp.GitHub("smv620/dmbot-improvements", down),
            consenting=lambda g: _people(self.people), improvement_allowed=_same,
        )  # fmt: skip
        for tries in range(1, 6):
            self.assertEqual(await broken.round(GUILD), 0)
            self.assertEqual((await self.row("a"))["post_failures"], tries)
            self.assertEqual(await broken.round(GUILD), 0)  # not looked at again within the hour
            self.assertEqual((await self.row("a"))["post_failures"], tries)
            self.clock[0] += 3601
        self.assertEqual(await self.poster().round(GUILD), 0)  # five failures: left alone
        self.assertEqual(self.created(), 0)

    async def test_the_lease_keeps_two_workers_off_one_report(self) -> None:
        await self.report("a")
        first = await self.store.claim_next(GUILD)
        self.assertIsNotNone(first)
        self.assertIsNone(await self.store.claim_next(GUILD))  # leased for ten minutes
        self.clock[0] += run.LEASE_S + 1
        self.assertIsNotNone(await self.store.claim_next(GUILD))


async def _people(people: set[int]) -> set[int]:
    return set(people)


async def _same(people: Any) -> set[int]:
    return set(people)


class TheConfig(DatabaseTest):
    async def test_the_repository_is_checked_and_the_token_is_never_printed(self) -> None:
        base = {"DISCORD_TOKEN": "t", "EARS_SHARED_SECRET": "s", "DATABASE_URL": "postgresql://x"}
        got = load_settings(
            {**base, "DMBOT_IMPROVEMENTS_REPO": "smv620/dmbot-improvements",
             "GITHUB_IMPROVEMENTS_TOKEN": "github_pat_secret_token_value"}
        )  # fmt: skip
        self.assertEqual(got.improvements_repo, "smv620/dmbot-improvements")
        self.assertNotIn("secret_token_value", repr(got))
        self.assertEqual(load_settings(base).improvements_repo, "")
        with self.assertRaises(ConfigError):
            load_settings({**base, "DMBOT_IMPROVEMENTS_REPO": "not a repo"})
