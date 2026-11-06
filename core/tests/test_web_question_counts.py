"""The Questions page's read (#3438): the bot's own counts, shown only to a campaign's game master,
with no word of any question. Run as the website's own database role; invented numbers."""

from __future__ import annotations

from typing import Any

from dmbot.questions import kinds
from dmbot.web import question_counts
from tests.test_web_relationships import Web

NOW = 1_800_000_000


def row(kind: str, count: int, oldest: int | None = 100) -> dict[str, Any]:
    return {"kind": kind, "count": count, "oldest": oldest}


def test_the_shape_has_a_ref_and_a_total_over_the_known_kinds_only() -> None:
    got = question_counts.shape(
        5,
        [
            {
                "id": "c" * 32,
                "name": "Salt and Smoke",
                "countedAt": 1700,
                "kinds": [row(kinds.NAMES, 2), row("mystery", 9), row(kinds.WHICH, 0)],
            }
        ],
    )
    assert got == [
        {
            "ref": f"5.{'c' * 32}",
            "name": "Salt and Smoke",
            "total": 2,
            "kinds": [{"kind": "names", "count": 2, "oldest": 100}],
            "countedAt": 1700,
        }
    ]


class Counts(Web):
    async def count(self, campaign: str, kind: str, n: int, oldest: int | None = 100) -> None:
        async with self.db.guild(self.g) as conn:
            await conn.execute(
                "INSERT INTO question_counts"
                " (guild_id, campaign_id, kind, n, oldest, settled, counted_at)"
                " VALUES (%s, %s, %s, %s, %s, NULL, %s)",
                (self.g, campaign, kind, n, oldest, NOW),
            )

    async def test_a_game_master_reads_their_campaigns_counts(self) -> None:
        await self.count(self.one.id, kinds.NAMES, 3)
        await self.count(self.one.id, kinds.FAMILY, 1, None)
        got = await question_counts.read_questions(self.web, self.session, now=NOW)
        (mine,) = [c for c in got["campaigns"] if c["ref"] == self.cref]
        self.assertEqual(mine["total"], 4)
        self.assertEqual({k["kind"]: k["count"] for k in mine["kinds"]}, {"names": 3, "family": 1})
        self.assertEqual(mine["countedAt"], NOW)
        self.assertEqual(got["serverNow"], NOW)
        self.assertEqual(
            await question_counts.read_total(self.web, self.session, now=NOW),
            {"total": sum(c["total"] for c in got["campaigns"])},
        )

    async def test_a_campaign_not_yet_counted_is_listed_with_nothing_waiting(self) -> None:
        got = await question_counts.read_questions(self.web, self.session, now=NOW)
        (mine,) = [c for c in got["campaigns"] if c["ref"] == self.cref]
        self.assertEqual((mine["total"], mine["kinds"], mine["countedAt"]), (0, [], None))

    async def test_a_player_sees_nothing_of_a_campaign_they_do_not_run(self) -> None:
        """Bob only plays in campaign one (he runs campaign two), so its counts are not his."""
        await self.count(self.one.id, kinds.NAMES, 3)
        got = await question_counts.read_questions(self.web, self.player, now=NOW)
        self.assertNotIn(self.cref, {c["ref"] for c in got["campaigns"]})
        self.assertEqual(sum(c["total"] for c in got["campaigns"]), 0)
        self.assertEqual(
            await question_counts.read_total(self.web, self.player, now=NOW), {"total": 0}
        )

    async def test_one_campaigns_counts_never_show_in_another(self) -> None:
        await self.count(self.two.id, kinds.CLAIMS, 7)
        got = await question_counts.read_questions(self.web, self.session, now=NOW)
        by_ref = {c["ref"]: c for c in got["campaigns"]}
        self.assertEqual(by_ref[self.cref]["total"], 0)
