"""Partial reveals as non-specific markers for the recap (#2997; Supervisor's ruling): only partial
reveals, never the content or a name the table doesn't know, scenes always, told secrets always,
checks only when they matter. Invented names. The last class needs Postgres."""

from __future__ import annotations

import time
import unittest
import uuid

from dmbot.memory import gate
from dmbot.memory import reveal_markers as rm
from tests.test_map_pins import PinsBase
from tests.test_map_store import GUILD_A

OK, WULF, CERRIC = "o" * 32, "w" * 32, "c" * 32  # three player characters
VANE, ROOT = "v" * 32, "r" * 32  # an NPC the table knows; a secret the table doesn't
FRIEND = ("connection", "f" * 32)  # Okthandom's tie to Vane, learned by Okthandom alone
HERB = ("connection", "h" * 32)  # Wulfgar's herb find
DEBT = ("connection", "d" * 32)  # Vane owes Okthandom: a second secret about Vane, Okthandom's
TIE = ("connection", "t" * 32)  # Wulfgar's tie to Cerric, found by Okthandom's check
TOWER = "k" * 32  # a place the table knows
NAMES = {
    OK: "Okthandom", WULF: "Wulfgar", CERRIC: "Cerric", VANE: "Vane", ROOT: None,
    TOWER: "the Tower",
}  # fmt: skip
TABLE = (OK, WULF, CERRIC)


def data(**knowers: frozenset[str]) -> gate.Statements:
    every = frozenset(TABLE)
    return gate.Statements(
        knowers={
            ("entry", OK): every, ("entry", WULF): every, ("entry", CERRIC): every,
            ("entry", VANE): every, ("entry", TOWER): every,
            FRIEND: knowers.get("friend", frozenset({OK})),
            HERB: knowers.get("herb", frozenset({WULF})), DEBT: frozenset({OK}),
            TIE: frozenset({OK, WULF}), ("connection", "q" * 32): frozenset({OK}),
        },
        connections={
            FRIEND[1]: (OK, VANE), HERB[1]: (WULF, ROOT), DEBT[1]: (VANE, OK),
            TIE[1]: (WULF, CERRIC), "q" * 32: (OK, TOWER),
        },
        player_characters=every,
    )  # fmt: skip


class TheRule(unittest.TestCase):
    def test_a_told_secret_is_cited_without_what(self) -> None:
        got = rm.markers([rm.Reveal(OK, FRIEND, rm.TOLD)], [], data(), TABLE, NAMES, ())
        self.assertEqual(got, [rm.Marker(rm.TOLD, ("Okthandom",), ("Vane",))])
        self.assertEqual(rm.words(got[0]), "Okthandom learned something about Vane.")

    def test_a_private_scene_is_always_cited_once(self) -> None:
        got = rm.markers([], [(OK, WULF), (WULF, OK)], data(), TABLE, NAMES, ())
        self.assertEqual([rm.words(m) for m in got], ["Okthandom and Wulfgar had a private word."])

    def test_a_trivial_check_is_never_cited_and_one_that_matters_is(self) -> None:
        herb = [rm.Reveal(WULF, HERB, rm.CHECK)]
        self.assertEqual(rm.markers(herb, [], data(), TABLE, NAMES, ()), [])
        (got,) = rm.markers(herb, [], data(), TABLE, NAMES, (ROOT,))
        self.assertEqual(rm.words(got), "Wulfgar learned something.")  # (no name for the table)

    def test_what_the_whole_table_now_knows_is_no_partial_reveal(self) -> None:
        known = data(friend=frozenset(TABLE))
        told = [rm.Reveal(OK, FRIEND, rm.TOLD)]
        self.assertEqual(rm.markers(told, [], known, TABLE, NAMES, ()), [])

    def test_a_reveal_undone_since_is_never_cited(self) -> None:
        undone = data(friend=frozenset({WULF}))  # (the kept row stays; Okthandom no longer knows)
        told = [rm.Reveal(OK, FRIEND, rm.TOLD)]
        self.assertEqual(rm.markers(told, [], undone, TABLE, NAMES, ()), [])

    def test_a_check_about_another_player_character_never_names_them(self) -> None:
        # (a player hearing their character named would know their secret is in play)
        (got,) = rm.markers([rm.Reveal(OK, TIE, rm.CHECK)], [], data(), TABLE, NAMES, (CERRIC,))
        self.assertEqual(rm.words(got), "Okthandom learned something.")

    def test_one_sentence_per_person_never_twice(self) -> None:
        reveals = [
            rm.Reveal(OK, FRIEND, rm.TOLD), rm.Reveal(OK, DEBT, rm.CHECK),
            rm.Reveal(OK, ("connection", "q" * 32), rm.TOLD),
        ]  # fmt: skip
        got = rm.markers(reveals, [], data(), TABLE, NAMES, (VANE,))
        self.assertEqual(
            [rm.words(m) for m in got], ["Okthandom learned something about Vane and the Tower."]
        )

    def test_someone_not_at_the_table_or_unnamed_is_never_cited(self) -> None:
        reveals = [rm.Reveal(OK, FRIEND, rm.TOLD)]
        self.assertEqual(rm.markers(reveals, [], data(), (WULF, CERRIC), NAMES, ()), [])
        self.assertEqual(rm.markers(reveals, [], data(), TABLE, {**NAMES, OK: None}, ()), [])
        self.assertEqual(rm.markers([], [(OK, ROOT)], data(), TABLE, NAMES, ()), [])
        self.assertEqual(rm.markers([], [(OK, WULF)], data(), (OK, CERRIC), NAMES, ()), [])


class NeverTheContent(PinsBase):  # (Postgres: the real read, names from the gate)
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.ok, self.wulf = [
            (await self.memory.add_entity(
                GUILD_A, self.one.id, name=n, type="character", role="player_character",
                played_by=p, status="confirmed", source="dm",
            )).value.id
            for n, p in (("Okthandom", 4001), ("Wulfgar", 4002))
        ]  # fmt: skip
        self.traitor = (await self.memory.add_entity(
            GUILD_A, self.one.id, name="Ser Vane the Traitor", type="character",
            status="confirmed", source="dm",
        )).value.id  # fmt: skip
        await self.memory.set_hidden_from_players(
            GUILD_A, self.one.id, self.traitor, True, source="dm", dm_chose_secret=True
        )
        self.bruenor = (await self.memory.add_entity(
            GUILD_A, self.one.id, name="Bruenor", type="character", status="confirmed",
            source="dm",
        )).value.id  # fmt: skip
        self.at = 1_700_000_000

    async def learn(
        self,
        kind: str,
        sid: str,
        *,
        speakers: list[int],
        at: int | None = None,
        status: str = "kept",
        campaign: str | None = None,
    ) -> None:
        campaign = campaign or self.one.id
        if status == "kept" and campaign == self.one.id:
            await self.memory.learn_statement(GUILD_A, campaign, kind, sid, self.ok, source="dm")
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "INSERT INTO memory_reveal_proposals (guild_id, campaign_id, id, character_id,"
                " statement_kind, statement_id, how, check_name, teller_id, lines, speaker_ids,"
                " session_started_at, prompt_version, model, status, created_at) VALUES (%s, %s,"
                " %s, %s, %s, %s, 'tell', '', %s, %s, %s, %s, 'v1', 'test', %s, %s)",
                (
                    GUILD_A, campaign, uuid.uuid4().hex, self.ok, kind, sid, self.wulf, [],
                    speakers, at or self.at, status, int(time.time()),
                ),
            )  # fmt: skip

    async def said(self, opted_in: list[int] | None = None) -> list[str]:
        async with self.memory._read(GUILD_A, self.one.id) as scope:
            got = await rm.session_markers(
                scope, self.at, [self.ok, self.wulf], (), opted_in or [4001, 4002]
            )
        return [rm.words(m) for m in got]

    async def secret_tie(self) -> str:
        made = await self.memory.add_relation(
            GUILD_A, self.one.id, self.ok, "knows", self.bruenor, source="dm", confidence=1.0,
            status="confirmed", secret=True, dm_chose_secret=True,
        )  # fmt: skip
        return str(made.value[0].id)

    async def test_a_name_the_table_knows_is_said_and_a_secret_one_never(self) -> None:
        await self.learn("entry", self.traitor, speakers=[4001])
        await self.learn("connection", await self.secret_tie(), speakers=[4001])
        said = await self.said()
        self.assertEqual(said, ["Okthandom learned something about Bruenor."])  # (a real name)
        for word in ("Vane", "Traitor", "Ser"):
            self.assertNotIn(word, " ".join(said))

    async def test_who_someone_really_is_is_never_a_subject(self) -> None:
        alias = await self.memory.add_alias(
            GUILD_A, self.one.id, self.bruenor, "the Hidden King", kind="title", source="dm",
            status="confirmed", secret=True, dm_chose_secret=True,
        )  # fmt: skip
        await self.learn("alias", alias.value.id, speakers=[4001])
        said = await self.said()
        self.assertEqual(said, ["Okthandom learned something."])
        self.assertNotIn("Hidden", " ".join(said))

    async def test_only_this_sessions_kept_rows_of_this_campaign_with_consent(self) -> None:
        tie = await self.secret_tie()
        await self.learn("entry", self.traitor, speakers=[4001], status="proposed")
        await self.learn("connection", tie, speakers=[4001], at=self.at + 1)
        await self.learn("connection", tie, speakers=[4001], campaign=self.two.id)
        self.assertEqual(await self.said(), [])
        await self.learn("connection", tie, speakers=[4001])
        self.assertEqual(await self.said([4002]), [])  # (its speaker isn't recorded now)
        self.assertEqual(len(await self.said()), 1)

    async def test_a_scene_needs_both_at_the_table_and_its_speakers_recorded(self) -> None:
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "INSERT INTO memory_private_scenes (guild_id, campaign_id, id, first_id,"
                " second_id, session_started_at, from_ms, to_ms, line_count, speaker_ids, status,"
                " created_at) VALUES (%s, %s, %s, %s, %s, %s, 0, 1000, 4, %s, 'kept', %s)",
                (
                    GUILD_A, self.one.id, uuid.uuid4().hex, *sorted((self.ok, self.wulf)),
                    self.at, [4001, 4002], int(time.time()),
                ),
            )  # fmt: skip
        named = {self.ok: "Okthandom", self.wulf: "Wulfgar"}
        first, second = (named[i] for i in sorted(named))
        self.assertEqual(await self.said(), [f"{first} and {second} had a private word."])
        self.assertEqual(await self.said([4001]), [])
