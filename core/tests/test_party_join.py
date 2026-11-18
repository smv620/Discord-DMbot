"""A character joining the party (#4537, party knowledge slice 2b; `docs/design/party_knowledge.md`
§5 and rule 13; Supervisor's ruling on #4537): the join question, "From the first session", and the
end of the automatic copy behind `DMBOT_PARTY_JOIN`. Invented names and ids only."""

from __future__ import annotations

import unittest
from typing import Any

from dmbot.campaigns import CampaignStore
from dmbot.memory import join_questions
from dmbot.memory.gate import ALIAS, CONNECTION, ENTRY, PROPERTY, Viewer
from dmbot.memory.join_questions import AlreadyAnswered
from dmbot.memory.models import CONFIRMED, DM, PLAYER_CHARACTER, MemoryRuleError
from dmbot.memory.store import MemoryStore
from dmbot.transcript.store import TranscriptStore
from dmbot.ui import party_join
from tests.pg import DatabaseTest

GUILD = 700_000_000_000_000_011
DM_ID, ANNA, BEN, CARA, DAN = (
    700_000_000_000_000_101,
    700_000_000_000_000_102,
    700_000_000_000_000_103,
    700_000_000_000_000_104,
    700_000_000_000_000_105,
)


class TheSwitch(unittest.TestCase):
    def test_off_by_default_and_checked(self) -> None:
        from dmbot.config import ConfigError, load_settings

        base = {"DISCORD_TOKEN": "t", "EARS_SHARED_SECRET": "s", "DATABASE_URL": "postgresql://x"}
        self.assertFalse(load_settings(base).party_join_on)
        self.assertTrue(load_settings({**base, "DMBOT_PARTY_JOIN": "1"}).party_join_on)
        with self.assertRaises(ConfigError):
            load_settings({**base, "DMBOT_PARTY_JOIN": "maybe"})


class TheWords(unittest.TestCase):
    def test_names_join_with_and(self) -> None:
        self.assertEqual(party_join.names_text(["Mira"]), "Mira")
        self.assertEqual(party_join.names_text(["Mira", "Tavin"]), "Mira and Tavin")
        self.assertEqual(party_join.names_text(["A", "B", "C"]), "A, B and C")

    def test_the_question_names_the_character_and_drops_the_count_at_zero(self) -> None:
        text = party_join.question_text(["Mira"], 212)
        self.assertTrue(text.startswith("**Mira has joined the party.** What does Mira know"))
        self.assertIn("leave out the 212 things Mira doesn't know yet.", text)
        self.assertIn("Mira's own backstory", text)
        self.assertNotIn("leave out", party_join.question_text(["Mira"], 0))
        self.assertIn("the 1 thing", party_join.question_text(["Mira"], 1))
        two = party_join.question_text(["Mira", "Tavin"], 3)
        self.assertIn("Mira and Tavin have joined", two)
        self.assertIn("Mira and Tavin don't know yet", two)
        for word in ("she", "he", "her", "him", "his", "they"):  # (ruling C: named, no pronoun)
            self.assertNotIn(f" {word} ", f" {text} ")


class Party(DatabaseTest):
    """A campaign with two player characters, Vell and Tuck, and the switch on."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.now = 10_000
        self.memory = MemoryStore(self.db, clock=lambda: float(self.now))
        self.memory.party_join = True
        self.campaign = await CampaignStore(self.db).create(GUILD, "Saltmarsh", DM_ID)
        self.c = self.campaign.id
        self.sessions = TranscriptStore(self.db)
        self.vell = await self.pc("Vell", ANNA)
        self.tuck = await self.pc("Tuck", BEN)
        made = await self.memory.add_entity(
            GUILD, self.c, name="Harbourmaster", type="npc", status=CONFIRMED, source=DM
        )
        self.npc = str(made.value.id)  # (shown: the whole party knows it)
        await self.played(5_000, 6_000)  # (a session has been played: joining after it is asked)

    async def pc(self, name: str, player: int) -> str:
        made = await self.memory.add_entity(
            GUILD, self.c, name=name, type="character", role=PLAYER_CHARACTER, played_by=player,
            status=CONFIRMED, source=DM,
        )  # fmt: skip
        return str(made.value.id)

    async def played(self, start: int, end: int | None) -> None:
        sid = await self.sessions.open_session(GUILD, self.c, start)
        async with self.db.guild(GUILD) as conn:  # (a session with lines: an empty one isn't one)
            await conn.execute(
                "UPDATE transcript_sessions SET line_count = 3 WHERE id = %s", (sid,)
            )
        if end is not None:
            await self.sessions.end_session(GUILD, sid, end)

    async def sees(self, character: str, kind: str, statement: str) -> bool:
        seen = await self.memory.visible_to(GUILD, self.c, Viewer.character(character))
        return seen.knows(kind, statement)

    async def waiting(self) -> list[join_questions.Question]:
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                "SELECT id, entity_id, status, group_id, sent_at, created_at"
                " FROM memory_join_questions WHERE guild_id = %s AND campaign_id = %s",
                (GUILD, self.c),
            )
            return [join_questions._question(r) for r in await cur.fetchall()]


class TheQuestion(Party):
    async def test_switch_off_copies_at_once_as_before(self) -> None:
        self.memory.party_join = False
        cara = await self.pc("Cara", CARA)
        self.assertTrue(await self.sees(cara, ENTRY, self.npc))
        self.assertEqual(await self.waiting(), [])

    async def test_the_first_characters_are_asked_nothing(self) -> None:
        self.assertEqual(await self.waiting(), [])  # (Vell and Tuck began, before any session)
        self.assertTrue(await self.sees(self.tuck, ENTRY, self.vell))  # (given what is shown)

    async def test_a_newcomer_knows_nothing_until_the_dm_answers_and_it_waits_for_play(
        self,
    ) -> None:
        sid = await self.sessions.open_session(GUILD, self.c, 9_000)  # (a session is running)
        async with self.db.guild(GUILD) as conn:
            await conn.execute(
                "UPDATE transcript_sessions SET line_count = 3 WHERE id = %s", (sid,)
            )
        cara = await self.pc("Cara", CARA)
        self.assertFalse(await self.sees(cara, ENTRY, self.npc))
        self.assertTrue(await self.sees(cara, ENTRY, cara))  # (its own entry)
        [q] = await self.waiting()
        self.assertEqual((q.entity_id, q.status, q.sent_at), (cara, "waiting", None))
        self.assertEqual(await self.memory.join_questions_waiting(GUILD, self.c), (1, 10_000))
        self.assertEqual(await self.memory.join_questions_to_send(GUILD, self.c), [])  # (play)
        await self.sessions.end_session(GUILD, sid, 9_500)
        sent = await self.memory.join_questions_to_send(GUILD, self.c)
        self.assertEqual([s.entity_id for s in sent], [cara])
        self.assertEqual(await self.memory.join_questions_to_send(GUILD, self.c), [])  # (once)
        group = sent[0].group_id
        assert group is not None
        self.assertEqual(await self.memory.join_question_groups_waiting(GUILD, self.c), [group])

    async def test_two_joining_before_it_goes_share_one_message(self) -> None:
        cara = await self.pc("Cara", CARA)
        dan = await self.pc("Dan", DAN)
        sent = await self.memory.join_questions_to_send(GUILD, self.c)
        self.assertEqual({s.entity_id for s in sent}, {cara, dan})
        self.assertEqual(len({s.group_id for s in sent}), 1)

    async def test_what_the_party_knows_copies_the_counted_things_once_with_undo(self) -> None:
        cara = await self.pc("Cara", CARA)
        [q] = await self.memory.join_questions_to_send(GUILD, self.c)
        assert q.group_id is not None
        count = await self.memory.party_known_count(GUILD, self.c, [cara])
        self.assertGreater(count, 0)
        done = await self.memory.join_answer_party(GUILD, self.c, q.group_id)
        self.assertEqual(done.value, count)  # (the number shown is the number copied)
        self.assertTrue(await self.sees(cara, ENTRY, self.npc))
        with self.assertRaises(AlreadyAnswered):
            await self.memory.join_answer_party(GUILD, self.c, q.group_id)
        with self.assertRaises(AlreadyAnswered):
            await self.memory.join_answer_everyone(GUILD, self.c, q.group_id)
        assert done.batch is not None
        await self.memory.undo(GUILD, self.c, done.batch)
        self.assertFalse(await self.sees(cara, ENTRY, self.npc))
        [after] = await self.waiting()
        self.assertEqual(after.status, "everyone")  # (Undo: "only what everyone knows again")

    async def test_only_what_everyone_knows_writes_nothing_and_leaves_the_other_answer(
        self,
    ) -> None:
        cara = await self.pc("Cara", CARA)
        [q] = await self.memory.join_questions_to_send(GUILD, self.c)
        assert q.group_id is not None
        await self.memory.join_answer_everyone(GUILD, self.c, q.group_id)
        self.assertFalse(await self.sees(cara, ENTRY, self.npc))
        self.assertEqual(await self.memory.join_questions_waiting(GUILD, self.c), (0, None))
        await self.memory.join_answer_party(GUILD, self.c, q.group_id)  # (still offered)
        self.assertTrue(await self.sees(cara, ENTRY, self.npc))

    async def test_nothing_to_copy_leaves_the_question_open(self) -> None:
        async with self.db.guild(GUILD) as conn:  # (the party knows nothing at all)
            await conn.execute(
                "DELETE FROM memory_knowers WHERE guild_id = %s AND campaign_id = %s",
                (GUILD, self.c),
            )
        await self.pc("Cara", CARA)
        [q] = await self.memory.join_questions_to_send(GUILD, self.c)
        assert q.group_id is not None
        done = await self.memory.join_answer_party(GUILD, self.c, q.group_id)
        self.assertEqual((done.value, done.batch), (0, None))
        [after] = await self.waiting()
        self.assertEqual(after.status, "waiting")  # (answered again once the party knows more)

    async def test_a_character_who_left_is_not_asked_about(self) -> None:
        cara = await self.pc("Cara", CARA)
        await self.memory.leave_party(GUILD, self.c, cara, "left")
        self.assertEqual(await self.memory.join_questions_waiting(GUILD, self.c), (0, None))
        self.assertEqual(await self.memory.join_questions_to_send(GUILD, self.c), [])

    async def test_a_newcomer_answered_everyone_leaves_little_for_the_next(self) -> None:
        # The rule as written (§5, rule 13.4): "what the party knows" is what EVERY other
        # current member knows, so a member who was told only what everyone knows holds back
        # a later newcomer's copy. Put to Supervisor on #4537; asserted so a change is seen.
        cara = await self.pc("Cara", CARA)
        [q] = await self.memory.join_questions_to_send(GUILD, self.c)
        assert q.group_id is not None
        await self.memory.join_answer_everyone(GUILD, self.c, q.group_id)
        dan = await self.pc("Dan", DAN)
        [q] = await self.memory.join_questions_to_send(GUILD, self.c)
        assert q.group_id is not None
        count = await self.memory.party_known_count(GUILD, self.c, [dan])
        done = await self.memory.join_answer_party(GUILD, self.c, q.group_id)
        self.assertEqual(done.value, count)
        self.assertFalse(await self.sees(dan, ENTRY, self.npc))  # (Cara doesn't know it)
        self.assertFalse(await self.sees(cara, ENTRY, self.npc))

    async def test_undoing_the_join_takes_the_question(self) -> None:
        made = await self.memory.add_entity(
            GUILD, self.c, name="Cara", type="character", status="proposed", source="extractor"
        )
        cara = str(made.value.id)
        joined = await self.memory.confirm_entity(
            GUILD, self.c, cara, "character", source=DM, played_by=CARA, role=PLAYER_CHARACTER
        )
        self.assertEqual(len(await self.waiting()), 1)
        assert joined.batch is not None
        await self.memory.undo(GUILD, self.c, joined.batch)
        self.assertEqual(await self.waiting(), [])


class FromTheFirstSession(Party):
    async def test_it_dates_the_stretch_copies_and_undoes_to_a_waiting_question(self) -> None:
        await self.played(2_000, 3_000)
        cara = await self.pc("Cara", CARA)
        count = await self.memory.party_known_count(GUILD, self.c, [cara])
        done = await self.memory.join_from_first_session(GUILD, self.c, cara)
        self.assertEqual(done.value, count)
        [span] = await self.memory.party_spans(GUILD, self.c, cara)
        self.assertEqual(span.from_at, 2_000)
        self.assertTrue(await self.sees(cara, ENTRY, self.npc))
        [q] = await self.waiting()
        self.assertEqual(q.status, "first")  # (no join question: ruling B)
        self.assertEqual(await self.memory.join_questions_to_send(GUILD, self.c), [])
        assert done.batch is not None
        await self.memory.undo(GUILD, self.c, done.batch)
        [span] = await self.memory.party_spans(GUILD, self.c, cara)
        self.assertEqual(span.from_at, 10_000)
        self.assertFalse(await self.sees(cara, ENTRY, self.npc))
        [q] = await self.waiting()
        self.assertEqual(q.status, "waiting")
        resent = await self.memory.join_questions_to_send(GUILD, self.c)  # (as the DM is told)
        self.assertEqual([r.entity_id for r in resent], [cara])

    async def test_a_stretch_never_moves_over_an_earlier_one(self) -> None:
        await self.played(2_000, 3_000)
        cara = await self.pc("Cara", CARA)
        self.now = 20_000
        await self.memory.leave_party(GUILD, self.c, self.vell, "away")
        async with self.memory._write(GUILD, self.c, DM) as w:  # (Vell back, slice 4's way)
            from dmbot.memory import party_record

            await party_record.open_span(w, self.vell)
        with self.assertRaises(MemoryRuleError):
            await self.memory.join_from_session(GUILD, self.c, self.vell, 2_000)
        with self.assertRaises(MemoryRuleError):  # (not in the party: refused, nothing moved)
            await self.memory.leave_party(GUILD, self.c, cara, "left")
            await self.memory.join_from_session(GUILD, self.c, cara, 2_000)

    async def test_a_later_session_dates_the_stretch_and_the_question_stands(self) -> None:
        await self.played(2_000, 3_000)
        cara = await self.pc("Cara", CARA)
        await self.memory.join_from_session(GUILD, self.c, cara, 5_000)
        [span] = await self.memory.party_spans(GUILD, self.c, cara)
        self.assertEqual(span.from_at, 5_000)
        [q] = await self.waiting()
        self.assertEqual(q.status, "waiting")


class JoinsAndLeaves(Party):
    """Rule 13.13: one campaign walked through joins and leaves, comparing the gate's answer for
    every kind of statement at each step (replacing #2995's flag-versus-party test)."""

    async def statements(self) -> dict[str, tuple[str, str]]:
        g, c = GUILD, self.c
        place = str(
            (await self.memory.add_entity(
                g, c, name="Gull Rock", type="place", status=CONFIRMED, source=DM
            )).value.id
        )  # fmt: skip
        alias = await self.memory.add_alias(
            g, c, self.npc, "Old Hask", kind="nickname", source=DM, status=CONFIRMED
        )
        relation = await self.memory.add_relation(
            g, c, self.npc, "located_in", place, source=DM, confidence=1.0, status=CONFIRMED
        )
        state = await self.memory.set_condition(g, c, self.npc, "alive", source=DM)
        assert state.value[0] is not None
        world = str(
            (await self.memory.add_entity(
                g, c, name="Ten-Towns", type="place", status=CONFIRMED, source=DM
            )).value.id
        )  # fmt: skip
        await self.memory.set_statement_knowers(
            g, c, "entry", world, [], source=DM, dm_chose_secret=True
        )
        await self.memory.add_common_knowledge(g, c, "entry", [world], source=DM)
        sister = str(
            (await self.memory.add_entity(
                g, c, name="Vell's sister", type="character", status=CONFIRMED, source=DM
            )).value.id
        )  # fmt: skip
        tie = await self.memory.add_relation(
            g, c, sister, "located_in", place, source=DM, confidence=1.0, status=CONFIRMED
        )
        backstory = str(tie.value[0].id)  # (Vell's own backstory: known to Vell alone)
        await self.memory.set_statement_knowers(
            g, c, "connection", backstory, [self.vell], source=DM, dm_chose_secret=True
        )
        hidden = str(
            (await self.memory.add_entity(
                g, c, name="The smuggler", type="npc", status=CONFIRMED, source=DM
            )).value.id
        )  # fmt: skip
        await self.memory.set_hidden_from_players(
            g, c, hidden, True, source=DM, dm_chose_secret=True
        )
        monk = str(
            (await self.memory.add_entity(
                g, c, name="Brother Ansel", type="character", status=CONFIRMED, source=DM
            )).value.id
        )  # fmt: skip
        secret_tie = await self.memory.add_relation(
            g, c, monk, "located_in", place, source=DM, confidence=1.0, status=CONFIRMED
        )
        npc_only = str(secret_tie.value[0].id)  # (an entry still follows its flags: slice 5)
        await self.memory.set_statement_knowers(
            g, c, "connection", npc_only, {monk: 1.0}, source=DM, dm_chose_secret=True
        )
        return {
            "entry": (ENTRY, self.npc),
            "name": (ALIAS, str(alias.value.id)),
            "connection": (CONNECTION, str(relation.value[0].id)),
            "state": (PROPERTY, str(state.value[0].id)),
            "common": (ENTRY, world),
            "backstory": (CONNECTION, backstory),
            "hidden": (ENTRY, hidden),
            "npc only": (CONNECTION, npc_only),
        }

    async def known(self, character: str, statements: dict[str, tuple[str, str]]) -> set[str]:
        seen = await self.memory.visible_to(GUILD, self.c, Viewer.character(character))
        return {label for label, (kind, sid) in statements.items() if seen.knows(kind, sid)}

    async def test_the_walk(self) -> None:
        st = await self.statements()
        table = {"entry", "name", "connection", "state", "common"}
        # 1. two characters: the table's statements, Vell's backstory for Vell only
        self.assertEqual(await self.known(self.vell, st), table | {"backstory"})
        self.assertEqual(await self.known(self.tuck, st), table)
        before: dict[str, set[str]] = {
            self.vell: await self.known(self.vell, st),
            self.tuck: await self.known(self.tuck, st),
        }
        # 2. Cara joins; "Only what everyone knows": common knowledge and nothing else
        cara = await self.pc("Cara", CARA)
        self.assertEqual(await self.known(cara, st), {"common"})
        [q] = await self.memory.join_questions_to_send(GUILD, self.c)
        assert q.group_id is not None
        await self.memory.join_answer_everyone(GUILD, self.c, q.group_id)
        self.assertEqual(await self.known(cara, st), {"common"})
        # (while Cara knows only that, Cara's gap would keep anything from being "what the party
        # knows" for a later newcomer: the copy is only what EVERY other member holds)
        await self.memory.join_answer_party(GUILD, self.c, q.group_id)  # (still offered)
        self.assertEqual(await self.known(cara, st), table)  # (never the backstory or hidden)
        # 3. Dan joins; "What the party knows" copies the table's statements, then Undo
        dan = await self.pc("Dan", DAN)
        self.assertEqual(await self.known(dan, st), {"common"})
        [q] = await self.memory.join_questions_to_send(GUILD, self.c)
        assert q.group_id is not None
        done = await self.memory.join_answer_party(GUILD, self.c, q.group_id)
        self.assertEqual(await self.known(dan, st), table)
        assert done.batch is not None
        await self.memory.undo(GUILD, self.c, done.batch)
        self.assertEqual(await self.known(dan, st), {"common"})
        self.assertEqual(await self.known(cara, st), table)  # (nothing taken from Cara)
        # 4. Vell leaves (retired), the leave undone, and taken again: nothing taken from anyone
        left = await self.memory.leave_party(GUILD, self.c, self.vell, "retired")
        self.assertEqual(await self.known(self.vell, st), before[self.vell])
        assert left.batch is not None
        await self.memory.undo(GUILD, self.c, left.batch)
        await self.memory.leave_party(GUILD, self.c, self.vell, "retired")
        self.assertEqual(await self.known(self.vell, st), before[self.vell])
        self.assertEqual(await self.known(self.tuck, st), before[self.tuck])
        self.assertEqual(
            set(await self.memory.current_party(GUILD, self.c)), {self.tuck, cara, dan}
        )
        # (a statement shown after Vell left reaches the current party only, rule 13.6)
        made = await self.memory.add_entity(
            GUILD, self.c, name="Lighthouse", type="place", status=CONFIRMED, source=DM
        )
        later = str(made.value.id)
        self.assertTrue(await self.sees(self.tuck, ENTRY, later))
        self.assertFalse(await self.sees(self.vell, ENTRY, later))


__all__: list[Any] = []
