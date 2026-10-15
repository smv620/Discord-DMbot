"""What DMbot learns about who is who (#1188 part 2): waiting questions from the names
people answered to, the DM's answers, and Stop recording me taking the learned ones away.
Names are made up."""

from __future__ import annotations

from dmbot.memory.models import MemoryRuleError
from dmbot.ui.people_review import question_text, waiting_text
from tests.test_memory_store import DM as SRC_DM
from tests.test_memory_store import GUILD_A, MemoryTest
from tests.test_people import OTHER, PLAYER


class Learning(MemoryTest):
    async def waiting(self, campaign: str | None = None) -> list[tuple[str, str, int]]:
        found = await self.memory.people_suggestions(GUILD_A, campaign or self.c)
        return [(s.kind, s.name, s.user_id) for s in found]

    async def test_a_name_nothing_knows_waits_on_the_person_who_answered(self) -> None:
        made = await self.memory.learn_people(GUILD_A, self.c, [("Mike", PLAYER, 3)], limit=10)
        self.assertEqual([(s.kind, s.name, s.times) for s in made.value], [("name", "Mike", 3)])
        self.assertEqual(await self.waiting(), [("name", "Mike", PLAYER)])
        # Nothing is true yet: the person's own names don't include it.
        got = await self.memory.aliases(GUILD_A, self.c)
        self.assertNotIn("Mike", {a.text for a in got if a.status == "confirmed"})

    async def test_a_character_nobody_plays_waits_as_a_link(self) -> None:
        await self.add("Cerric")
        made = await self.memory.learn_people(GUILD_A, self.c, [("Cerric", PLAYER, 2)], limit=10)
        self.assertEqual([s.kind for s in made.value], ["plays"])
        rels = await self.memory.relations(GUILD_A, self.c)
        self.assertEqual([r.status for r in rels if r.predicate == "plays"], ["proposed"])

    async def test_a_character_someone_else_plays_changes_nothing(self) -> None:
        await self.add("Cerric", type="player_character", played_by=OTHER)
        made = await self.memory.learn_people(GUILD_A, self.c, [("Cerric", PLAYER, 5)], limit=10)
        self.assertEqual(made.value, [])
        self.assertEqual(await self.waiting(), [])

    async def test_a_name_already_known_for_a_person_changes_nothing(self) -> None:
        await self.memory.learn_people(GUILD_A, self.c, [("Mike", PLAYER, 2)], limit=10)
        again = await self.memory.learn_people(GUILD_A, self.c, [("Mike", OTHER, 2)], limit=10)
        self.assertEqual(again.value, [])

    async def test_at_most_the_limit(self) -> None:
        rows = [(f"Name{chr(97 + i)}", PLAYER, 2) for i in range(6)]
        made = await self.memory.learn_people(GUILD_A, self.c, rows, limit=3)
        self.assertEqual(len(made.value), 3)

    async def test_the_dm_words_are_never_learned(self) -> None:
        made = await self.memory.learn_people(GUILD_A, self.c, [("GM", PLAYER, 4)], limit=10)
        self.assertEqual(made.value, [])

    async def test_yes_makes_it_the_dms_own(self) -> None:
        made = await self.memory.learn_people(GUILD_A, self.c, [("Mike", PLAYER, 2)], limit=10)
        await self.memory.accept_person_name(GUILD_A, self.c, made.value[0].row_id)
        self.assertEqual(await self.waiting(), [])
        # Stop recording me no longer removes it.
        self.assertEqual(await self.memory.forget_learned(GUILD_A, PLAYER), 0)
        got = await self.memory.aliases(GUILD_A, self.c)
        self.assertIn("mike", {a.key for a in got if a.status == "confirmed"})

    async def test_someone_else_moves_the_name(self) -> None:
        made = await self.memory.learn_people(GUILD_A, self.c, [("Mike", PLAYER, 2)], limit=10)
        await self.memory.accept_person_name(GUILD_A, self.c, made.value[0].row_id, user_id=OTHER)
        people = {
            e.played_by: e.id
            for e in await self.memory.entities(GUILD_A, self.c)
            if e.type == "person"
        }
        owners = {
            a.entity_id for a in await self.memory.aliases(GUILD_A, self.c) if a.key == "mike"
        }
        self.assertEqual(owners, {people[OTHER]})

    async def test_not_a_player_is_never_asked_again(self) -> None:
        made = await self.memory.learn_people(GUILD_A, self.c, [("Mike", PLAYER, 2)], limit=10)
        await self.memory.reject_person_name(GUILD_A, self.c, made.value[0].row_id)
        again = await self.memory.learn_people(GUILD_A, self.c, [("Mike", PLAYER, 9)], limit=10)
        self.assertEqual(again.value, [])
        with self.assertRaises(MemoryRuleError):
            await self.memory.reject_person_name(GUILD_A, self.c, made.value[0].row_id)

    async def test_yes_to_a_link_sets_the_player_and_no_is_not_asked_again(self) -> None:
        cerric = await self.add("Cerric")
        made = await self.memory.learn_people(GUILD_A, self.c, [("Cerric", PLAYER, 2)], limit=10)
        await self.memory.decide_plays(GUILD_A, self.c, made.value[0].row_id, yes=True)
        found = {e.id: e for e in await self.memory.entities(GUILD_A, self.c)}
        self.assertEqual(found[cerric].played_by, PLAYER)
        self.assertTrue(found[cerric].is_player_character)
        # A second character, answered "no": never asked again.
        belleros = await self.add("Belleros")
        two = await self.memory.learn_people(GUILD_A, self.c, [("Belleros", OTHER, 2)], limit=10)
        await self.memory.decide_plays(GUILD_A, self.c, two.value[0].row_id, yes=False)
        again = await self.memory.learn_people(GUILD_A, self.c, [("Belleros", OTHER, 2)], limit=10)
        self.assertEqual(again.value, [])
        self.assertTrue(belleros)

    async def test_stop_recording_me_removes_what_was_learned_for_them_everywhere(self) -> None:
        other = (await self.campaigns.create(GUILD_A, "Another", SRC_DM)).id
        await self.add("Cerric")
        await self.memory.learn_people(
            GUILD_A, self.c, [("Mike", PLAYER, 2), ("Cerric", PLAYER, 2)], limit=10
        )
        await self.memory.learn_people(GUILD_A, other, [("Mikey", PLAYER, 2)], limit=10)
        await self.memory.learn_people(GUILD_A, self.c, [("Vera", OTHER, 2)], limit=10)
        # Three learned names and links, and the two placeholder entries (one per campaign)
        # nothing else hangs on.
        self.assertEqual(await self.memory.forget_learned(GUILD_A, PLAYER), 5)
        self.assertEqual(await self.waiting(), [("name", "Vera", OTHER)])
        for campaign in (self.c, other):
            gone = [
                e for e in await self.memory.entities(GUILD_A, campaign) if e.played_by == PLAYER
            ]
            self.assertEqual(gone, [])
        self.assertEqual(await self.waiting(other), [])
        # Not even the change log keeps it.
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute(
                "SELECT count(*) AS n FROM memory_changes WHERE after::text LIKE '%Mike%'"
            )
            self.assertEqual((await cur.fetchone() or {})["n"], 0)

    async def test_a_person_with_something_hanging_on_it_stays(self) -> None:
        await self.add("Testa", type="player_character", played_by=PLAYER)  # a confirmed link
        await self.memory.learn_people(GUILD_A, self.c, [("Mike", PLAYER, 2)], limit=10)
        await self.memory.forget_learned(GUILD_A, PLAYER)
        mine = [e for e in await self.memory.entities(GUILD_A, self.c) if e.played_by == PLAYER]
        self.assertEqual([e.type for e in mine if e.type == "person"], ["person"])

    async def test_a_placeholder_with_a_correction_on_it_stays_and_nothing_fails(self) -> None:
        await self.memory.learn_people(GUILD_A, self.c, [("Mike", PLAYER, 2)], limit=10)
        person = next(
            e for e in await self.memory.entities(GUILD_A, self.c) if e.played_by == PLAYER
        )
        async with self.db.guild(GUILD_A) as conn:  # something unlogged that hangs on it
            await conn.execute(
                "INSERT INTO memory_corrections (guild_id, campaign_id, id, heard, heard_key,"
                " entity_id, action, source, created_at)"
                " VALUES (%s, %s, repeat('c', 32), 'Mikel', 'mikel', %s, 'fix', 'dm', 0)",
                (GUILD_A, self.c, person.id),
            )
        other = (await self.campaigns.create(GUILD_A, "Another", SRC_DM)).id
        await self.memory.learn_people(GUILD_A, other, [("Mikey", PLAYER, 2)], limit=10)
        await self.memory.forget_learned(GUILD_A, PLAYER)  # does not raise
        here = [e for e in await self.memory.entities(GUILD_A, self.c) if e.played_by == PLAYER]
        there = [e for e in await self.memory.entities(GUILD_A, other) if e.played_by == PLAYER]
        self.assertEqual((len(here), len(there)), (1, 0))  # the other campaign was still cleaned
        self.assertEqual(await self.waiting(), [])  # and the learned name went from this one

    async def test_the_lookup_has_the_names_the_dm_confirmed_for_people_and_nothing_else(
        self,
    ) -> None:
        made = await self.memory.learn_people(GUILD_A, self.c, [("Mike", PLAYER, 2)], limit=10)
        waiting = await self.memory.lookup_data(GUILD_A, self.c)
        self.assertEqual(waiting.people, ())  # a waiting question is not true yet
        await self.memory.accept_person_name(GUILD_A, self.c, made.value[0].row_id)
        data = await self.memory.lookup_data(GUILD_A, self.c)
        self.assertEqual([a.text for a in data.people], ["Mike"])  # not "Player 1234"
        self.assertEqual([e for e in data.entities if e.type == "person"], [])
        # Not the ones the DM said no to, nor secret ones.
        await self.memory.learn_people(GUILD_A, self.c, [("Vera", PLAYER, 2)], limit=10)
        wrong = await self.memory.people_suggestions(GUILD_A, self.c)
        await self.memory.reject_person_name(GUILD_A, self.c, wrong[0].row_id)
        again = await self.memory.lookup_data(GUILD_A, self.c)
        self.assertEqual([a.text for a in again.people], ["Mike"])

    async def person_id(self) -> str:
        return next(
            e.id for e in await self.memory.entities(GUILD_A, self.c) if e.played_by == PLAYER
        )

    async def stays_after_forgetting(self) -> bool:
        await self.memory.forget_learned(GUILD_A, PLAYER)
        return any(e.played_by == PLAYER for e in await self.memory.entities(GUILD_A, self.c))

    async def test_a_placeholder_the_dm_wrote_on_stays(self) -> None:
        await self.memory.learn_people(GUILD_A, self.c, [("Mike", PLAYER, 2)], limit=10)
        person = await self.person_id()
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "UPDATE memory_entities SET description = 'Runs the shop' WHERE id = %s", (person,)
            )
        self.assertTrue(await self.stays_after_forgetting())

    async def test_the_after_session_check_does_not_make_a_placeholder_the_dms(self) -> None:
        await self.memory.learn_people(GUILD_A, self.c, [("Mike", PLAYER, 2)], limit=10)
        marked = await self.memory.check_people(GUILD_A, self.c, [])
        self.assertEqual(marked.value, 1)  # it plays nothing: "needs a look"
        self.assertFalse(await self.stays_after_forgetting())  # but Stop recording me removes it

    async def test_a_placeholder_with_a_change_by_the_dm_logged_stays(self) -> None:
        await self.memory.learn_people(GUILD_A, self.c, [("Mike", PLAYER, 2)], limit=10)
        person = await self.person_id()
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "INSERT INTO memory_changes (guild_id, campaign_id, version, batch, table_name,"
                " row_id, op, source, made_at) VALUES (%s, %s, 9999, 9999, 'memory_entities',"
                " %s, 'update', 'dm', 0)",
                (GUILD_A, self.c, person),
            )
        self.assertTrue(await self.stays_after_forgetting())

    async def test_a_placeholder_with_heard_counts_or_a_sheet_stays(self) -> None:
        for table_name in ("memory_heard", "character_sheets"):
            await self.memory.learn_people(GUILD_A, self.c, [("Mike", PLAYER, 2)], limit=10)
            person = await self.person_id()
            async with self.db.guild(GUILD_A) as conn:
                if table_name == "memory_heard":
                    await conn.execute(
                        "INSERT INTO memory_heard (guild_id, campaign_id, entity_id,"
                        " session_started_at, speaker_id, times) VALUES (%s, %s, %s, 1, %s, 2)",
                        (GUILD_A, self.c, person, PLAYER),
                    )
                else:
                    await conn.execute(
                        "INSERT INTO character_sheets (guild_id, campaign_id, entity_id, player_id,"
                        " url) VALUES (%s, %s, %s, %s,"
                        " 'https://www.dndbeyond.com/characters/12345')",
                        (GUILD_A, self.c, person, PLAYER),
                    )
            self.assertTrue(await self.stays_after_forgetting(), table_name)
            async with self.db.guild(GUILD_A) as conn:
                await conn.execute(f"DELETE FROM {table_name}")
                await conn.execute("DELETE FROM memory_entities WHERE id = %s", (person,))

    async def test_a_removed_placeholder_cannot_come_back_by_undo(self) -> None:
        await self.memory.learn_people(GUILD_A, self.c, [("Mike", PLAYER, 2)], limit=10)
        await self.memory.forget_learned(GUILD_A, PLAYER)
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute(
                "SELECT count(*) AS n FROM memory_changes WHERE after::text LIKE '%Player 1234%'"
                " OR before::text LIKE '%Player 1234%'"
            )
            self.assertEqual((await cur.fetchone() or {})["n"], 0)

    async def test_who_is_who_is_kept_until_the_memory_changes(self) -> None:
        pc = await self.add("Testa", type="player_character", played_by=PLAYER)
        first = await self.memory.table_people(GUILD_A, self.c)
        first[0][1].append("tampered")  # a caller changing its copy can't change the cache
        self.assertEqual((await self.memory.table_people(GUILD_A, self.c))[0][1], ["Testa"])
        # A change to the campaign's memory is seen at once.
        await self.memory.rename_entity(GUILD_A, self.c, pc, "Testa the Bold", source="dm")
        self.assertEqual(
            (await self.memory.table_people(GUILD_A, self.c))[0][1], ["Testa the Bold"]
        )

    async def test_the_cache_is_bounded_keeps_empty_tables_and_stays_in_its_campaign(self) -> None:
        from dmbot.memory import store as store_module

        other = (await self.campaigns.create(GUILD_A, "Another", SRC_DM)).id
        await self.add("Testa", type="player_character", played_by=PLAYER)
        self.assertEqual(await self.memory.table_people(GUILD_A, other), [])  # empty: cached
        self.assertIn((GUILD_A, other), self.memory._people)
        self.assertEqual(len(await self.memory.table_people(GUILD_A, self.c)), 1)  # not other's
        third = (await self.campaigns.create(GUILD_A, "Third", SRC_DM)).id
        self.memory._people.clear()
        old = store_module.PEOPLE_CACHE_MAX
        store_module.PEOPLE_CACHE_MAX = 2
        try:
            for campaign in (self.c, other, third):  # three misses, room for two
                await self.memory.table_people(GUILD_A, campaign)
        finally:
            store_module.PEOPLE_CACHE_MAX = old
        # Only the oldest went, not the whole cache.
        self.assertEqual(list(self.memory._people), [(GUILD_A, other), (GUILD_A, third)])

    async def test_a_person_and_their_names_stay_in_their_campaign(self) -> None:
        other = (await self.campaigns.create(GUILD_A, "Another", SRC_DM)).id
        await self.memory.learn_people(GUILD_A, self.c, [("Mike", PLAYER, 2)], limit=10)
        self.assertEqual(await self.waiting(other), [])
        found = await self.memory.entities(GUILD_A, other)
        self.assertEqual([e for e in found if e.type == "person"], [])


class Words(MemoryTest):
    async def test_the_questions_are_in_plain_words(self) -> None:
        made = await self.memory.learn_people(GUILD_A, self.c, [("Mike", PLAYER, 2)], limit=10)
        text = question_text(made.value[0], "Mia", 2)
        self.assertIn("Someone said **Mike**, and **Mia** answered", text)
        self.assertIn("1 more after this", text)
        self.assertIn("2 quick questions", waiting_text(2))
        self.assertNotIn("Mike", waiting_text(1))  # the screen says how many, never who
