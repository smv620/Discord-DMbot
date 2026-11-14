"""A session's new name said while the game clock had the party split is known only to the group
that heard it (#4037, the names half of #2996 slice 2a). Real Postgres, invented names."""

from __future__ import annotations

from typing import Any

from dmbot.memory.models import CONFIRMED, MemoryRuleError
from dmbot.memory.store import AI_JOIN_REFUSED
from dmbot.ui import split_words
from tests.test_memory_names import GUILD, NamesTest


class SplitBase(NamesTest):
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


class SplitNames(SplitBase):
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


class SplitMerges(SplitBase):
    """#4043: a "Same as…" or DMbot's own join of a split-only name, and the session a name was
    made in (recorded, never guessed)."""

    async def known(self, name: str = "Grimsby Hollow") -> str:
        made = await self.memory.add_entity(
            GUILD, self.campaign.id, name=name, type="place", status=CONFIRMED, source="dm"
        )
        return made.value.id

    async def spelling(self, entity_id: str, text: str = "Grimsby") -> Any:
        aliases = await self.memory.aliases(
            GUILD, self.campaign.id, entity_id=entity_id, include_secret=True
        )
        return next(a for a in aliases if a.text == text)

    async def test_a_split_only_name_joins_as_a_secret_name_the_group_knows(self) -> None:
        grimsby = await self.scene(split_at=600, speaker=7, said_at_ms=650_000)
        hollow = await self.known()
        done = await self.memory.merge(
            GUILD, self.campaign.id, hollow, grimsby, source="dm", dm_said_same=True
        )
        self.assertEqual(done.split, "group")  # (the caller tells the DM)
        spelling = await self.spelling(hollow)
        self.assertTrue(spelling.secret)  # (the other group never learns the entry's spelling)
        known = await self.memory.statement_knowers(GUILD, self.campaign.id, "alias", [spelling.id])
        self.assertEqual(known.get(spelling.id, []), [self.wren])
        self.assertFalse(await self.hidden(hollow))  # (the entry itself stays as it was)

    async def test_when_the_group_cant_be_told_the_spelling_is_secret_and_known_to_nobody(
        self,
    ) -> None:
        grimsby = await self.scene(split_at=600, speaker=99, said_at_ms=650_000)  # (the DM)
        hollow = await self.known()
        done = await self.memory.merge(
            GUILD, self.campaign.id, hollow, grimsby, source="dm", dm_said_same=True
        )
        spelling = await self.spelling(hollow)
        known = await self.memory.statement_knowers(GUILD, self.campaign.id, "alias", [spelling.id])
        self.assertEqual((done.split, spelling.secret, known.get(spelling.id, [])),
                         ("unknown", True, []))  # fmt: skip

    async def test_a_name_said_while_together_joins_as_today(self) -> None:
        grimsby = await self.scene(split_at=None, speaker=7, said_at_ms=650_000)
        hollow = await self.known()
        done = await self.memory.merge(
            GUILD, self.campaign.id, hollow, grimsby, source="dm", dm_said_same=True
        )
        self.assertEqual(done.split, "")
        self.assertFalse((await self.spelling(hollow)).secret)

    async def test_dmbot_never_joins_a_split_only_name_by_itself(self) -> None:
        grimsby = await self.scene(split_at=600, speaker=7, said_at_ms=650_000)
        hollow = await self.known()
        with self.assertRaises(MemoryRuleError) as refused:
            await self.memory.join_by_ai(GUILD, self.campaign.id, hollow, grimsby)
        self.assertEqual(str(refused.exception), AI_JOIN_REFUSED)
        gone = await self.memory.entity(GUILD, self.campaign.id, grimsby)
        assert gone is not None
        self.assertEqual(gone.status, "proposed")  # (nothing moved)
        self.assertEqual((await self.spelling(grimsby)).entity_id, grimsby)

    async def test_keeping_the_waiting_name_keeps_the_known_one_and_its_spelling_secret(
        self,
    ) -> None:
        """The reviewer's case: the DM keeps the split-only waiting name and merges a known one
        into it, which confirms it."""
        grimsby = await self.scene(split_at=600, speaker=7, said_at_ms=650_000)
        hollow = await self.known()
        done = await self.memory.merge(
            GUILD, self.campaign.id, grimsby, hollow, source="dm", dm_said_same=True
        )
        self.assertEqual(done.value.id, hollow)  # (the confirmed entry is kept, still shown)
        self.assertEqual(done.split, "group")
        self.assertFalse(await self.hidden(hollow))
        self.assertTrue((await self.spelling(hollow)).secret)
        self.assertIn("were there", split_words.for_join(done.split))

    async def test_two_waiting_names_merged_then_confirmed_keep_the_split_spelling_secret(
        self,
    ) -> None:
        grimsby = await self.scene(split_at=600, speaker=7, said_at_ms=650_000)
        vale = await self.memory.add_entity(  # (a waiting name heard while together, earlier)
            GUILD, self.campaign.id, name="Grimsby Vale", type="place", status="proposed",
            source="dm",
        )  # fmt: skip
        await self.memory.merge(
            GUILD, self.campaign.id, vale.value.id, grimsby, source="dm", dm_said_same=True
        )
        await self.memory.confirm_entity(
            GUILD, self.campaign.id, vale.value.id, "place", source="dm"
        )
        self.assertTrue((await self.spelling(vale.value.id)).secret)

    async def test_a_turned_down_spelling_never_stops_the_merge(self) -> None:
        grimsby = await self.scene(split_at=600, speaker=7, said_at_ms=650_000)
        async with self.db.guild(GUILD) as conn:
            await conn.execute(
                "INSERT INTO memory_aliases (guild_id, campaign_id, id, entity_id, text, key,"
                " kind, status, secret, sound_codes, source, created_at)"
                " SELECT guild_id, campaign_id, %s, entity_id, 'Grimsbee', 'grimsbee', 'misheard',"
                " 'rejected', FALSE, '{}', 'dm', created_at"
                " FROM memory_aliases WHERE guild_id = %s AND entity_id = %s LIMIT 1",
                ("9" * 32, GUILD, grimsby),
            )
        hollow = await self.known()
        done = await self.memory.merge(
            GUILD, self.campaign.id, hollow, grimsby, source="dm", dm_said_same=True
        )
        self.assertEqual(done.split, "group")

    async def test_undo_takes_the_secret_and_the_knowers_back(self) -> None:
        grimsby = await self.scene(split_at=600, speaker=7, said_at_ms=650_000)
        hollow = await self.known()
        done = await self.memory.merge(
            GUILD, self.campaign.id, hollow, grimsby, source="dm", dm_said_same=True
        )
        spelling = await self.spelling(hollow)
        assert done.batch is not None
        await self.memory.undo(GUILD, self.campaign.id, done.batch)
        back = await self.spelling(grimsby)
        known = await self.memory.statement_knowers(GUILD, self.campaign.id, "alias", [back.id])
        self.assertEqual((back.id, back.secret, known.get(back.id, [])), (spelling.id, False, []))

    async def test_merge_many_counts_each_split_name(self) -> None:
        grimsby = await self.scene(split_at=600, speaker=7, said_at_ms=650_000)
        other = await self.memory.add_entity(
            GUILD, self.campaign.id, name="Old Rock", type="place", status="proposed",
            source="extractor", session_started_at=500,
        )  # fmt: skip
        async with self.db.guild(GUILD) as conn:
            await conn.execute(
                "INSERT INTO transcript_lines (guild_id, session_id, started_ms, user_id, heard)"
                " VALUES (%s, 's500', 660000, 99, 'Old Rock looms.')",
                (GUILD,),
            )
        hollow = await self.known()
        done = await self.memory.merge_many(
            GUILD, self.campaign.id, hollow, [grimsby, other.value.id], source="dm"
        )
        self.assertEqual(done.split_counts, (1, 1))  # (Wren's group; the DM's line: can't tell)
        self.assertEqual(done.split, "unknown")

    async def test_the_recorded_session_decides_not_the_newest_one(self) -> None:
        """Supervisor's case on #4044: an import dated before a live session, a name made after
        both. The import was together, so the name is shown, though the live session was split
        and names it too."""
        await self.scene(split_at=600, speaker=7, said_at_ms=650_000)  # (the live session, 500)
        cid = self.campaign.id
        async with self.db.guild(GUILD) as conn:
            await conn.execute(
                "INSERT INTO transcript_sessions (id, guild_id, campaign_id, started_at)"
                " VALUES ('s50', %s, %s, 50)",
                (GUILD, cid),
            )
            await conn.execute(
                "INSERT INTO transcript_lines (guild_id, session_id, started_ms, user_id, heard)"
                " VALUES (%s, 's50', 60000, 8, 'The road to Grimsby Vale.')",
                (GUILD,),
            )
        made = await self.memory.add_entity(
            GUILD, cid, name="Grimsby Vale", type="place", status="proposed", source="scan",
            session_started_at=50,
        )  # fmt: skip
        done = await self.memory.confirm_entity(GUILD, cid, made.value.id, "place", source="dm")
        self.assertEqual(done.split, "")
        self.assertFalse(await self.hidden(made.value.id))

    async def test_without_a_record_the_newest_session_is_still_the_guess(self) -> None:
        """A name made before the record existed: the older guess, which hides it here."""
        grimsby = await self.scene(split_at=600, speaker=7, said_at_ms=650_000)
        done = await self.memory.confirm_entity(
            GUILD, self.campaign.id, grimsby, "place", source="dm"
        )
        self.assertEqual(done.split, "group")
