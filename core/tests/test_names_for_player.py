"""The player-facing reader of what a player's characters know names each end as that player's
characters know it (#3037 ruling): after a join, "the guard at the gate", never "Ken". Invented
names."""

from __future__ import annotations

from dmbot.memory.gate import ENTRY, Viewer
from dmbot.memory.models import CONFIRMED
from tests.test_memory_store import GUILD_A
from tests.test_same_person_join import JoinBase


class TheReaderNamesEachEndAsTheCharacterKnowsIt(JoinBase):
    """`what_my_characters_know` is a player-facing reader: after a join it names the guard's
    connection through the secret other name, never "Ken" (Supervisor, #3037)."""

    async def hidden_connection(self, a: str) -> str:
        captain = await self.add("Captain Vale", type="character", role="npc", status=CONFIRMED)
        made = await self.memory.add_relation(
            GUILD_A, self.c, a, "knows", captain, source="dm", confidence=1.0,
            status=CONFIRMED, secret=True,
        )  # fmt: skip
        return str(made.value[0].id)

    async def test_a_joined_connection_reads_as_the_guard_and_never_ken(self) -> None:
        await self.memory.set_hidden_from_players(GUILD_A, self.c, self.ken, True, source="dm")
        rel = await self.hidden_connection(self.guard)
        await self.memory.set_connection_knowers(
            GUILD_A, self.c, rel, [self.sees_guard], source="dm"
        )
        written = await self.join()
        after = await self.memory.what_my_characters_know(GUILD_A, self.c, 1)
        (shown,) = after.connections
        self.assertEqual((shown.subject, shown.object), (self.label, "Captain Vale"))
        self.assertNotIn("Ken", repr(after))
        batch = written.batch  # type: ignore[attr-defined]
        assert batch is not None
        await self.memory.undo(GUILD_A, self.c, batch, source="dm")
        undone = await self.memory.what_my_characters_know(GUILD_A, self.c, 1)
        self.assertNotIn("Ken", repr(undone))  # (the unnamed person is not a named entry again)

    async def test_a_player_whose_character_knew_ken_is_shown_ken(self) -> None:
        captain = await self.add("Captain Vale", type="character", role="npc", status=CONFIRMED)
        made = await self.memory.add_relation(
            GUILD_A, self.c, self.ken, "knows", captain, source="dm", confidence=1.0,
            status=CONFIRMED, secret=True,
        )  # fmt: skip
        rel = str(made.value[0].id)
        await self.memory.set_connection_knowers(GUILD_A, self.c, rel, [self.sees_ken], source="dm")
        await self.join()
        mine = await self.memory.what_my_characters_know(GUILD_A, self.c, 2)
        self.assertEqual([c.subject for c in mine.connections], ["Ken"])

    async def test_a_public_ken_is_still_the_guard_to_a_character_who_knew_only_the_guard(
        self,
    ) -> None:
        """A name belongs to the fact: Ken is public, but the joined connection is known through
        the guard's name, so it is listed under that name and never as Ken."""
        rel = await self.hidden_connection(self.guard)
        await self.memory.set_connection_knowers(
            GUILD_A, self.c, rel, [self.sees_guard], source="dm"
        )
        await self.join()
        mine = await self.memory.what_my_characters_know(GUILD_A, self.c, 1)
        (shown,) = mine.connections
        self.assertEqual(shown.subject, self.label)
        self.assertNotIn("Ken", repr(mine))

    async def test_a_player_whose_character_knows_ken_by_name_is_shown_ken(self) -> None:
        rel = await self.hidden_connection(self.ken)
        await self.memory.set_connection_knowers(GUILD_A, self.c, rel, [self.sees_ken], source="dm")
        await self.join()
        mine = await self.memory.what_my_characters_know(GUILD_A, self.c, 2)
        self.assertEqual([c.subject for c in mine.connections], ["Ken"])

    async def test_a_hidden_connection_between_two_public_entries_is_still_listed(self) -> None:
        mira = await self.add("Mira Voss", type="character", role="npc", status=CONFIRMED)
        keep = await self.add("Greywater Keep", type="place", status=CONFIRMED)
        made = await self.memory.add_relation(
            GUILD_A, self.c, mira, "located_in", keep, source="dm", confidence=1.0,
            status=CONFIRMED, secret=True,
        )  # fmt: skip
        await self.memory.set_connection_knowers(
            GUILD_A, self.c, str(made.value[0].id), [self.sees_guard], source="dm"
        )
        mine = await self.memory.what_my_characters_know(GUILD_A, self.c, 1)
        self.assertIn(
            ("Mira Voss", "Greywater Keep"), [(c.subject, c.object) for c in mine.connections]
        )

    async def test_an_unnamed_persons_label_never_carries_a_hidden_anchor(self) -> None:
        await self.memory.set_hidden_from_players(GUILD_A, self.c, self.ken, True, source="dm")
        sister = await self.memory.add_unnamed(
            GUILD_A, self.c, what="sister", role="sibling", anchor_id=self.ken
        )
        sister_id = sister.entity.id
        stored = await self.memory.entity(GUILD_A, self.c, sister_id)
        assert stored is not None
        self.assertIn("Ken", stored.name)  # (the stored label does embed him)
        shown = await self.memory.display_names(
            GUILD_A, self.c, Viewer.player([self.sees_guard]), [sister_id]
        )
        self.assertEqual(shown[sister_id], None)  # (no row: not shown at all)
        await self.know(ENTRY, sister_id, [self.sees_guard])
        shown = await self.memory.display_names(
            GUILD_A, self.c, Viewer.player([self.sees_guard]), [sister_id]
        )
        self.assertEqual(shown[sister_id], "someone's sister (name unknown)")
        # ... and the reader that lists what a player's characters know uses the same words
        mine = await self.memory.what_my_characters_know(GUILD_A, self.c, 1)
        self.assertNotIn("Ken", repr(mine))
        # the DM reads the stored label
        dm = await self.memory.display_names(GUILD_A, self.c, Viewer.the_dm(), [sister_id])
        self.assertIn("Ken", str(dm[sister_id]))

    async def test_the_reader_sends_only_display_names_and_withholds_a_note_that_could_name_ken(
        self,
    ) -> None:
        from dmbot.memory.ontology import FALLBACK_PREDICATE

        await self.memory.set_hidden_from_players(GUILD_A, self.c, self.ken, True, source="dm")
        sister = await self.memory.add_unnamed(
            GUILD_A, self.c, what="sister", role="sibling", anchor_id=self.ken
        )
        sister_id = str(sister.entity.id)
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "UPDATE memory_entities SET status = 'confirmed' WHERE id = %s", (sister_id,)
            )
        vale = await self.add("Captain Vale", type="character", role="npc", status=CONFIRMED)
        made = await self.memory.add_relation(
            GUILD_A, self.c, sister_id, FALLBACK_PREDICATE, vale, source="dm", confidence=1.0,
            status=CONFIRMED, secret=True, detail="owes a favour to Ken",
        )  # fmt: skip
        rel = str(made.value[0].id)
        await self.memory.set_connection_knowers(
            GUILD_A, self.c, rel, [self.sees_guard], source="dm"
        )
        await self.know(ENTRY, sister_id, [self.sees_guard])
        await self.memory.join_same_person(GUILD_A, self.c, sister_id, self.ken)
        found = await self.memory.aliases(GUILD_A, self.c, entity_id=self.ken, include_secret=True)
        label_alias = next(a.id for a in found if a.text.endswith("(name unknown)"))
        await self.memory.set_alias_knowers(
            GUILD_A, self.c, label_alias, [self.sees_guard], source="dm"
        )
        mine = await self.memory.what_my_characters_know(GUILD_A, self.c, 1)
        self.assertNotIn("Ken", repr(mine))
        # (the joined label is a person of its own for them: no "name of" line, which would say who)
        self.assertEqual(mine.names, ())
        self.assertEqual([c.subject for c in mine.connections], ["someone's sister (name unknown)"])
        self.assertTrue(all(c.detail == "" for c in mine.connections))
