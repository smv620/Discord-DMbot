"""A session's new name said while the game clock had the party split is known only to the group
that heard it (#4037, the names half of #2996 slice 2a). Real Postgres, invented names."""

from __future__ import annotations

from dmbot.memory.models import CONFIRMED
from tests.test_memory_names import GUILD, NamesTest


class SplitNames(NamesTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.wren = await self.pc("Wren", 7)
        self.ash = await self.pc("Ash", 8)

    async def pc(self, name: str, player: int) -> str:
        made = await self.memory.add_entity(
            GUILD, self.campaign.id, name=name, type="character", role="player_character",
            played_by=player, status=CONFIRMED, source="dm",
        )  # fmt: skip
        return made.value.id

    async def scene(self, *, split_at: int | None, speaker: int, said_at_ms: int) -> str:
        """A session at 500 whose line names Grimsby (spoken by `speaker` at `said_at_ms`), the
        clock together from 100 and, with `split_at`, Wren in group 1 and Ash in group 2 from
        then. Returns the proposed name the story reader made."""
        cid = self.campaign.id
        async with self.db.guild(GUILD) as conn:
            await conn.execute(
                "INSERT INTO game_clock_log (guild_id, campaign_id, position, at, minute)"
                " VALUES (%s, %s, NULL, 100, 0)",
                (GUILD, cid),
            )
            if split_at is not None:
                await conn.execute(
                    "INSERT INTO game_clock_log (guild_id, campaign_id, position, at, minute)"
                    " VALUES (%s, %s, 1, %s, 0)",
                    (GUILD, cid, split_at),
                )
                for character, group in ((self.wren, 1), (self.ash, 2)):
                    await conn.execute(
                        "INSERT INTO game_clock_group_log (guild_id, campaign_id, character_id,"
                        " position, at) VALUES (%s, %s, %s, %s, %s)",
                        (GUILD, cid, character, group, split_at),
                    )
            await conn.execute(
                "INSERT INTO transcript_sessions (id, guild_id, campaign_id, started_at)"
                " VALUES ('s500', %s, %s, 500)",
                (GUILD, cid),
            )
            await conn.execute(
                "INSERT INTO transcript_lines (guild_id, session_id, started_ms, user_id, heard)"
                " VALUES (%s, 's500', %s, %s, 'We ride for Grimsby at dawn.')",
                (GUILD, said_at_ms, speaker),
            )
        made = await self.memory.add_entity(
            GUILD, cid, name="Grimsby", type="place", status="proposed", source="extractor"
        )
        grimsby = made.value.id
        async with self.db.guild(GUILD) as conn:
            await conn.execute(
                "INSERT INTO memory_heard (guild_id, campaign_id, entity_id, session_started_at,"
                " speaker_id, times) VALUES (%s, %s, %s, 500, %s, 1)",
                (GUILD, cid, grimsby, speaker),
            )
        return grimsby

    async def hidden(self, entity_id: str) -> bool:
        entity = await self.memory.entity(GUILD, self.campaign.id, entity_id)
        assert entity is not None
        return entity.hidden_from_players

    async def who_knows(self, entity_id: str) -> list[str]:
        known = await self.memory.statement_knowers(GUILD, self.campaign.id, "entry", [entity_id])
        return known.get(entity_id, [])

    async def test_said_while_together_is_shown_as_today(self) -> None:
        grimsby = await self.scene(split_at=None, speaker=7, said_at_ms=650_000)
        await self.memory.confirm_entity(GUILD, self.campaign.id, grimsby, "place", source="dm")
        self.assertFalse(await self.hidden(grimsby))

    async def test_said_in_one_groups_scene_only_that_group_knows_it(self) -> None:
        grimsby = await self.scene(split_at=600, speaker=7, said_at_ms=650_000)
        done = await self.memory.confirm_entity(
            GUILD, self.campaign.id, grimsby, "place", source="dm"
        )
        self.assertTrue(await self.hidden(grimsby))
        self.assertEqual(await self.who_knows(grimsby), [self.wren])
        assert done.batch is not None
        await self.memory.undo(GUILD, self.campaign.id, done.batch)  # (one Undo takes it all)
        self.assertFalse(await self.hidden(grimsby))
        self.assertEqual(await self.who_knows(grimsby), [])

    async def test_said_before_the_split_is_shown(self) -> None:
        grimsby = await self.scene(split_at=600, speaker=7, said_at_ms=550_000)
        await self.memory.confirm_entity(GUILD, self.campaign.id, grimsby, "place", source="dm")
        self.assertFalse(await self.hidden(grimsby))

    async def test_when_the_group_cant_be_told_nobody_knows_it_and_it_stays_hidden(self) -> None:
        grimsby = await self.scene(split_at=600, speaker=99, said_at_ms=650_000)  # (the DM)
        await self.memory.confirm_entity(GUILD, self.campaign.id, grimsby, "place", source="dm")
        self.assertTrue(await self.hidden(grimsby))
        self.assertEqual(await self.who_knows(grimsby), [])

    async def test_the_bulk_confirm_follows_the_same_rule(self) -> None:
        grimsby = await self.scene(split_at=600, speaker=8, said_at_ms=650_000)
        await self.memory.confirm_kinds(GUILD, self.campaign.id, [grimsby], "place", source="dm")
        self.assertTrue(await self.hidden(grimsby))
        self.assertEqual(await self.who_knows(grimsby), [self.ash])

    async def test_a_name_the_dm_typed_is_never_hidden_by_this(self) -> None:
        await self.scene(split_at=600, speaker=7, said_at_ms=650_000)
        made = await self.memory.add_entity(
            GUILD, self.campaign.id, name="Grimsby Hollow", type="place", status="proposed",
            source="dm",
        )  # fmt: skip
        await self.memory.confirm_entity(
            GUILD, self.campaign.id, made.value.id, "place", source="dm"
        )
        self.assertFalse(await self.hidden(made.value.id))
