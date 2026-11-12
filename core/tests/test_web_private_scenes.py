"""The website's "Private scenes" list (#4102): a DM-only door that lists the times two player
characters were alone together, after the consent re-check, with names, a date, about how long
and how many lines and nothing else, and a "Not private" door. Real Postgres; invented names."""

from __future__ import annotations

from typing import Any

from dmbot.web import private_scenes
from dmbot.web.recaps import RecapError
from tests.agreed import agree
from tests.test_web_api import ALICE
from tests.test_web_relationships import Web

STRANGER = 4242


class PrivateScenesDoor(Web):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        await agree(self.db, self.g, ALICE.id, STRANGER)
        self.first, self.second = sorted((self.rook, self.mira))

    async def scene(
        self,
        n: int = 1,
        speakers: tuple[int, ...] = (ALICE.id, STRANGER),
        started: int = 1_700_000_000,
        minutes: int = 12,
        ms: tuple[int, int] | None = None,
    ) -> str:
        sid = f"{n:032x}"
        begin, end = (
            ms if ms is not None else (0, minutes * 60_000)
        )  # (ms from the session's start)
        async with self.db.guild(self.g) as conn:
            await conn.execute(
                "INSERT INTO memory_private_scenes (guild_id, campaign_id, id, first_id, second_id,"
                " session_started_at, from_ms, to_ms, line_count, speaker_ids, status, created_at)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 5, %s, 'kept', 1)",
                (
                    self.g, self.one.id, sid, self.first, self.second, started,
                    started * 1000 + begin, started * 1000 + end, list(speakers),
                ),
            )  # fmt: skip
        return sid

    async def items(self, who: Any = None) -> list[dict[str, Any]]:
        got = await private_scenes.listed(self.web, who or self.session, self.cref)
        return list(got["items"])

    async def refusal(self, call: Any) -> str:
        with self.assertRaises(RecapError) as caught:
            await call
        return str(caught.exception.code)

    async def test_the_list_has_names_a_date_the_time_and_the_lines_only(self) -> None:
        sid = await self.scene()
        (item,) = await self.items()
        self.assertEqual(
            item,
            {
                "id": sid,
                "first": "Rook" if self.first == self.rook else "Mira Voss",
                "second": "Mira Voss" if self.first == self.rook else "Rook",
                "sessionStartedAt": 1_700_000_000,
                "into": 0,
                "minutes": 12,
                "lines": 5,
            },
        )

    async def test_the_minutes_round_as_discord_does_and_never_read_zero(self) -> None:
        await self.scene(1, ms=(0, 90_000))  # 1.5 minutes: Discord's round() says 2
        await self.scene(2, started=1_600_000_000, ms=(0, 10_000))  # a few seconds: at least 1
        mins = {i["id"]: i["minutes"] for i in await self.items()}
        self.assertEqual(sorted(mins.values()), [1, 2])

    async def test_it_says_how_far_into_the_session_it_began(self) -> None:
        await self.scene(1, ms=(125 * 60_000, 130 * 60_000))
        (item,) = await self.items()
        self.assertEqual(item["into"], 125)

    async def test_it_is_empty_before_any_scene(self) -> None:
        self.assertEqual(await self.items(), [])

    async def test_newest_session_first(self) -> None:
        await self.scene(1, started=1_700_000_000)
        later = await self.scene(2, started=1_700_100_000)
        self.assertEqual((await self.items())[0]["id"], later)

    async def test_a_scene_citing_someone_who_stopped_is_left_out(self) -> None:
        await self.scene(1)
        async with self.db.guild(self.g) as conn:
            await conn.execute(
                "DELETE FROM consent WHERE guild_id = %s AND user_id = %s", (self.g, STRANGER)
            )
        self.assertEqual(await self.items(), [])

    async def test_a_player_gets_nothing(self) -> None:
        await self.scene()
        self.assertEqual(await self.refusal(self.items(self.player)), "not_allowed")

    async def test_another_campaigns_scene_is_not_listed(self) -> None:
        await self.scene()
        other = await self.campaigns.create(self.g, "Other", ALICE.id)
        got = await private_scenes.listed(self.web, self.session, f"{self.g}.{other.id}")
        self.assertEqual(got["items"], [])

    async def test_not_private_marks_it_and_names_the_two(self) -> None:
        sid = await self.scene()
        said = await private_scenes.not_private(self.web, self.session, self.cref, sid)
        self.assertEqual({said["first"], said["second"]}, {"Rook", "Mira Voss"})
        self.assertEqual(await self.items(), [])
        async with self.db.guild(self.g) as conn:
            cur = await conn.execute(
                "SELECT status, decided_at FROM memory_private_scenes WHERE id = %s", (sid,)
            )
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual(row["status"], "not_private")
        self.assertIsNotNone(row["decided_at"])

    async def test_a_second_press_or_a_missing_scene_is_gone(self) -> None:
        sid = await self.scene()
        await private_scenes.not_private(self.web, self.session, self.cref, sid)
        for scene in (sid, "f" * 32):
            self.assertEqual(
                await self.refusal(
                    private_scenes.not_private(self.web, self.session, self.cref, scene)
                ),
                "gone",
            )

    async def test_a_malformed_id_is_not_found(self) -> None:
        self.assertEqual(
            await self.refusal(
                private_scenes.not_private(self.web, self.session, self.cref, "nope")
            ),
            "not_found",
        )

    async def test_a_player_cannot_mark_one(self) -> None:
        sid = await self.scene()
        self.assertEqual(
            await self.refusal(private_scenes.not_private(self.web, self.player, self.cref, sid)),
            "not_allowed",
        )
        self.assertEqual(len(await self.items()), 1)

    async def test_a_merged_character_shows_under_the_name_it_became(self) -> None:
        await self.scene()
        survivor = await self.add("Rook the Bold", "character", role="player_character")
        await self.memory.merge(
            self.g, self.one.id, survivor, self.rook, source="dm", dm_said_same=True
        )
        (item,) = await self.items()
        self.assertIn("Rook the Bold", (item["first"], item["second"]))

    async def test_a_speaker_without_discord_is_never_taken_as_stopped(self) -> None:
        await self.scene(1, speakers=(ALICE.id, -5))
        self.assertEqual(len(await self.items()), 1)

    async def test_an_import_confirmation_counts_until_it_is_taken_back(self) -> None:
        async with self.db.guild(self.g) as conn:
            await conn.execute(
                "INSERT INTO import_consents (guild_id, campaign_id, id, import_id, user_id,"
                " basis, confirmed_by, confirmed_at, terms_version) VALUES"
                " (%s, %s, %s, 'imp', 777, 'uploader_confirmed', %s, 1, 1)",
                (self.g, self.one.id, "c" * 32, ALICE.id),
            )
        await self.scene(1, speakers=(ALICE.id, 777))
        self.assertEqual(len(await self.items()), 1)
        async with self.db.guild(self.g) as conn:
            await conn.execute(
                "UPDATE import_consents SET removed_at = 2 WHERE guild_id = %s AND user_id = 777",
                (self.g,),
            )
        self.assertEqual(await self.items(), [])

    async def test_a_merge_chain_of_two_steps_shows_the_name_it_ended_as(self) -> None:
        await self.scene(1)
        middle = await self.add("Rook the Bold", "character", role="player_character")
        last = await self.add("Rook of the Reach", "character", role="player_character")
        await self.memory.merge(
            self.g, self.one.id, middle, self.rook, source="dm", dm_said_same=True
        )
        await self.memory.merge(self.g, self.one.id, last, middle, source="dm", dm_said_same=True)
        (item,) = await self.items()
        self.assertIn("Rook of the Reach", (item["first"], item["second"]))

    async def test_a_dm_of_another_campaign_cannot_list_or_mark_these(self) -> None:
        sid = await self.scene()
        other = await self.campaigns.create(self.g, "Other", ALICE.id)
        async with self.db.guild(self.g) as conn:
            await conn.execute(
                "DELETE FROM campaign_dms WHERE guild_id = %s AND campaign_id = %s",
                (self.g, self.one.id),
            )
            await conn.execute(
                "INSERT INTO campaign_dms (guild_id, campaign_id, user_id) VALUES (%s, %s, %s)"
                " ON CONFLICT DO NOTHING",
                (self.g, other.id, ALICE.id),
            )
        self.assertEqual(await self.refusal(self.items()), "not_allowed")
        self.assertEqual(
            await self.refusal(private_scenes.not_private(self.web, self.session, self.cref, sid)),
            "not_allowed",
        )
        elsewhere = f"{self.g}.{other.id}"
        self.assertEqual(
            await self.refusal(private_scenes.not_private(self.web, self.session, elsewhere, sid)),
            "gone",  # (a DM of that campaign, but the scene is not in it)
        )

    async def test_a_scene_marked_here_is_not_found_again_by_the_finder_flag(self) -> None:
        sid = await self.scene()
        await private_scenes.not_private(self.web, self.session, self.cref, sid)
        async with self.db.guild(self.g) as conn:
            cur = await conn.execute(
                "SELECT count(*) AS n FROM memory_private_scenes WHERE guild_id = %s"
                " AND status = 'not_private'",
                (self.g,),
            )
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual(row["n"], 1)  # (the store's own mark: the finder skips it, 0419)
