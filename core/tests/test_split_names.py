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
                " VALUES (%s, 's500', %s, %s, 'We ride for Grimsby’s gate at dawn.')",
                (GUILD, said_at_ms, speaker),
            )
        made = await self.memory.add_entity(
            GUILD, cid, name="Grimsby", type="place", status="proposed", source="extractor"
        )
        # (no "names heard" row: the after-session reader makes the name after that count is
        # kept, so the session is found by when the name was made, as in production)
        return made.value.id

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
        self.assertEqual(done.split, "group")  # (the caller tells the DM: never a silent hide)
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
        done = await self.memory.confirm_entity(
            GUILD, self.campaign.id, grimsby, "place", source="dm"
        )
        self.assertEqual(done.split, "unknown")
        self.assertTrue(await self.hidden(grimsby))
        self.assertEqual(await self.who_knows(grimsby), [])

    async def test_the_bulk_confirm_follows_the_same_rule(self) -> None:
        grimsby = await self.scene(split_at=600, speaker=8, said_at_ms=650_000)
        done = await self.memory.confirm_kinds(
            GUILD, self.campaign.id, [grimsby], "place", source="dm"
        )
        self.assertEqual(done.split_counts, (1, 0))
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

    async def test_said_in_two_groups_scenes_nobody_knows_it(self) -> None:
        """Supervisor's ruling on #4037: mixed groups can't be told, and nobody learns it."""
        grimsby = await self.scene(split_at=600, speaker=7, said_at_ms=650_000)
        async with self.db.guild(GUILD) as conn:
            await conn.execute(
                "INSERT INTO transcript_lines (guild_id, session_id, started_ms, user_id, heard)"
                " VALUES (%s, 's500', 700000, 8, 'Grimsby again.')",
                (GUILD,),
            )
        done = await self.memory.confirm_entity(
            GUILD, self.campaign.id, grimsby, "place", source="dm"
        )
        self.assertEqual(done.split, "unknown")
        self.assertTrue(await self.hidden(grimsby))
        self.assertEqual(await self.who_knows(grimsby), [])

    async def test_no_line_found_in_a_split_session_is_hidden_and_nobody_knows_it(self) -> None:
        grimsby = await self.scene(split_at=600, speaker=7, said_at_ms=650_000)
        async with self.db.guild(GUILD) as conn:
            await conn.execute(
                "UPDATE transcript_lines SET heard = 'Somewhere else.' WHERE guild_id = %s",
                (GUILD,),
            )
        done = await self.memory.confirm_entity(
            GUILD, self.campaign.id, grimsby, "place", source="dm"
        )
        self.assertEqual((done.split, await self.hidden(grimsby)), ("unknown", True))

    def test_the_dm_is_told_in_plain_words(self) -> None:
        from dmbot.ui import split_words

        self.assertIn("only the characters who were there", split_words.for_many(2, 0))
        self.assertIn("1 name is kept hidden", split_words.for_many(0, 1))
        self.assertEqual(split_words.for_one(""), "")
        self.assertIn("Who knows this?", split_words.for_one("unknown"))

    async def test_the_dms_sidebar_question_while_together_is_said_to_nobody(self) -> None:
        grimsby = await self.scene(split_at=600, speaker=7, said_at_ms=650_000)
        async with self.db.guild(GUILD) as conn:
            await conn.execute(
                "INSERT INTO transcript_lines (guild_id, session_id, started_ms, user_id, heard,"
                " sidebar) VALUES (%s, 's500', 550000, 99, 'Who is in Grimsby?', 'question')",
                (GUILD,),
            )
        done = await self.memory.confirm_entity(
            GUILD, self.campaign.id, grimsby, "place", source="dm"
        )
        self.assertEqual((done.split, await self.hidden(grimsby)), ("group", True))

    async def test_split_moments_are_found_and_a_together_campaign_costs_nothing(self) -> None:
        cid = self.campaign.id
        self.assertEqual(await self.memory.split_moments(GUILD, cid, [550, 650]), set())
        await self.scene(split_at=600, speaker=7, said_at_ms=650_000)
        self.assertEqual(await self.memory.split_moments(GUILD, cid, [550, 650]), {650})
        self.assertTrue(await self.memory.session_ever_split(GUILD, cid, 500))
