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
    ) -> str:
        sid = f"{n:032x}"
        async with self.db.guild(self.g) as conn:
            await conn.execute(
                "INSERT INTO memory_private_scenes (guild_id, campaign_id, id, first_id, second_id,"
                " session_started_at, from_ms, to_ms, line_count, speaker_ids, status, created_at)"
                " VALUES (%s, %s, %s, %s, %s, %s, 1000, %s, 5, %s, 'kept', 1)",
                (
                    self.g, self.one.id, sid, self.first, self.second, started,
                    1000 + minutes * 60_000, list(speakers),
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
                "minutes": 12,
                "lines": 5,
            },
        )

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
