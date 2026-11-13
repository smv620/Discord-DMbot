"""The website's "Met but not named" list (#4084): a DM-only door that lists the beings the party
met after the consent re-check and never sends a line's time or a speaker, and the three answers
(`met-name`, `met-join`, `met-remove`) queued on the name-action queue. Real Postgres; invented
names."""

from __future__ import annotations

from typing import Any

from dmbot.memory.met_store import MetStore
from dmbot.transcript.models import Line
from dmbot.transcript.store import TranscriptStore
from dmbot.web import met_list
from dmbot.web.recaps import RecapError
from tests.agreed import agree
from tests.test_web_api import ALICE
from tests.test_web_relationships import Web

LINE_MS = 1_700_000_001_000
STRANGER = 4242


class MetListDoor(Web):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        await agree(self.db, self.g, ALICE.id, STRANGER)
        self.met = MetStore(self.db)
        transcripts = TranscriptStore(self.db)
        self.sid = await transcripts.open_session(self.g, self.one.id, 1_700_000_000)
        await transcripts.add_lines(
            self.g,
            self.sid,
            [
                Line(LINE_MS, ALICE.id, "Two barbarians jump out.", "x"),
                Line(LINE_MS + 1_000, STRANGER, "I count the dogs.", "x"),
            ],
        )

    async def met_row(
        self, what: str, count: int = 2, speakers: tuple[int, ...] = (ALICE.id,)
    ) -> str:
        await self.met.add(
            self.g,
            self.one.id,
            self.sid,
            [(what, count, (LINE_MS,), speakers)],
            session_started_at=1_700_000_000,
        )
        rows = await self.met.of_session(self.g, self.one.id, self.sid)
        return next(r.id for r in rows if r.what == what)

    async def items(self, who: Any = None) -> list[dict[str, Any]]:
        got = await met_list.listed(self.web, who or self.session, self.cref)
        return list(got["items"])

    async def refusal(self, call: Any) -> str:
        with self.assertRaises(RecapError) as caught:
            await call
        return str(caught.exception.code)

    async def test_the_list_has_the_words_the_count_and_the_date_only(self) -> None:
        met = await self.met_row("barbarians")
        (item,) = await self.items()
        self.assertEqual(
            item, {"id": met, "what": "barbarians", "count": 2, "sessionStartedAt": 1_700_000_000}
        )

    async def test_it_is_empty_when_nobody_is_waiting(self) -> None:
        self.assertEqual(await self.items(), [])

    async def test_a_row_citing_someone_who_stopped_is_left_out(self) -> None:
        await self.met_row("barbarians")
        await self.met_row("sled dogs", 7, speakers=(ALICE.id, STRANGER))
        self.assertEqual(len(await self.items()), 2)
        async with self.db.guild(self.g) as conn:
            await conn.execute(
                "DELETE FROM consent WHERE guild_id = %s AND user_id = %s", (self.g, STRANGER)
            )
        self.assertEqual([i["what"] for i in await self.items()], ["barbarians"])

    async def test_a_player_gets_nothing(self) -> None:
        await self.met_row("barbarians")
        self.assertEqual(await self.refusal(self.items(self.player)), "not_allowed")

    async def test_naming_is_queued_with_the_row_and_the_name(self) -> None:
        met = await self.met_row("barbarians")
        answer = await met_list.queue(
            self.web, self.session, self.cref, met, {"action": "name", "name": "The Red Axes"}
        )
        self.assertRegex(answer, r"^[0-9a-f]{32}$")

    async def test_joining_needs_a_confirmed_entry(self) -> None:
        met = await self.met_row("barbarians")
        queued = await met_list.queue(
            self.web, self.session, self.cref, met, {"action": "join", "target": self.mira}
        )
        self.assertRegex(queued, r"^[0-9a-f]{32}$")
        nobody = "f" * 32
        self.assertEqual(
            await self.refusal(
                met_list.queue(
                    self.web, self.session, self.cref, met, {"action": "join", "target": nobody}
                )
            ),
            "bad_target",
        )

    async def test_removing_is_queued(self) -> None:
        met = await self.met_row("barbarians")
        answer = await met_list.queue(self.web, self.session, self.cref, met, {"action": "remove"})
        self.assertRegex(answer, r"^[0-9a-f]{32}$")

    async def test_a_row_that_is_not_there_is_not_found(self) -> None:
        self.assertEqual(
            await self.refusal(
                met_list.queue(self.web, self.session, self.cref, "a" * 32, {"action": "remove"})
            ),
            "not_found",
        )

    async def test_the_shapes_are_checked(self) -> None:
        met = await self.met_row("barbarians")

        async def refused(body: object) -> str:
            return await self.refusal(met_list.queue(self.web, self.session, self.cref, met, body))

        self.assertEqual(await refused(None), "bad_action")
        self.assertEqual(await refused({"action": "burn"}), "bad_action")
        self.assertEqual(await refused({"action": "name"}), "bad_name")
        self.assertEqual(await refused({"action": "name", "name": "  "}), "bad_name")
        self.assertEqual(await refused({"action": "remove", "name": "x"}), "bad_action")
        self.assertEqual(await refused({"action": "join"}), "bad_target")

    async def test_a_player_cannot_queue_an_answer(self) -> None:
        met = await self.met_row("barbarians")
        self.assertEqual(
            await self.refusal(
                met_list.queue(self.web, self.player, self.cref, met, {"action": "remove"})
            ),
            "not_allowed",
        )

    async def spoke(self, *speakers: int) -> None:
        """Lines for these speakers in the session (a row citing one with none is not kept)."""
        await TranscriptStore(self.db).add_lines(
            self.g,
            self.sid,
            [Line(LINE_MS + 5_000 + n, u, "Hm.", "x") for n, u in enumerate(speakers)],
        )

    async def test_a_speaker_without_discord_is_never_taken_as_stopped(self) -> None:
        await self.spoke(-5)
        await self.met_row("sled dogs", speakers=(-5,))
        self.assertEqual([i["what"] for i in await self.items()], ["sled dogs"])

    async def test_an_import_confirmation_counts_until_it_is_taken_back(self) -> None:
        await self.spoke(777)
        async with self.db.guild(self.g) as conn:
            await conn.execute(
                "INSERT INTO import_consents (guild_id, campaign_id, id, import_id, user_id,"
                " basis, confirmed_by, confirmed_at, terms_version) VALUES"
                " (%s, %s, %s, 'imp', 777, 'uploader_confirmed', %s, 1, 1)",
                (self.g, self.one.id, "c" * 32, ALICE.id),
            )
        await self.met_row("wolves", speakers=(777,))  # (the writer reads it too, #4153)
        self.assertEqual([i["what"] for i in await self.items()], ["wolves"])
        async with self.db.guild(self.g) as conn:
            await conn.execute(
                "UPDATE import_consents SET removed_at = 2 WHERE guild_id = %s AND user_id = 777",
                (self.g,),
            )
        self.assertEqual(await self.items(), [])

    async def test_another_campaigns_row_is_neither_listed_nor_answerable(self) -> None:
        other = await self.campaigns.create(self.g, "Other", ALICE.id)
        elsewhere = f"{self.g}.{other.id}"
        await self.met_row("barbarians")
        got = await met_list.listed(self.web, self.session, elsewhere)
        self.assertEqual(got["items"], [])
        met = (await self.items())[0]["id"]
        self.assertEqual(
            await self.refusal(
                met_list.queue(self.web, self.session, elsewhere, met, {"action": "remove"})
            ),
            "not_found",
        )

    async def test_the_queued_row_holds_the_met_row_the_name_and_the_target(self) -> None:
        met = await self.met_row("barbarians")
        queued = await met_list.queue(
            self.web, self.session, self.cref, met, {"action": "join", "target": self.mira}
        )
        named = await met_list.queue(
            self.web, self.session, self.cref, met, {"action": "name", "name": "The  Red Axes"}
        )
        async with self.db.guild(self.g) as conn:
            cur = await conn.execute(
                "SELECT id, entity_id, action, target_id, text, flag FROM name_actions"
                " WHERE guild_id = %s ORDER BY created_at, action",
                (self.g,),
            )
            rows = {r["id"]: r for r in await cur.fetchall()}
        self.assertEqual(
            (rows[queued]["entity_id"], rows[queued]["action"], rows[queued]["target_id"]),
            (met, "met-join", self.mira),
        )
        self.assertEqual(
            (rows[named]["action"], rows[named]["text"], rows[named]["flag"]),
            ("met-name", "The Red Axes", False),
        )

    async def test_the_door_itself_refuses_what_does_not_fit_an_action(self) -> None:
        met = await self.met_row("barbarians")

        async def door(action: str, target: str | None, text: str, flag: bool = False) -> str:
            async with self.db.user(self.session.user_id, session=self.session.id_hash) as conn:
                from dmbot.web.recaps import _scoped

                await _scoped(conn, self.g)
                cur = await conn.execute(
                    "SELECT dmbot_web_name_action_add(%s, %s, %s, %s, %s, %s, %s, %s) AS r",
                    (self.g, self.one.id, met, action, "", target, text, flag),
                )
                found = await cur.fetchone()
                assert found is not None
                return str(found["r"]["status"])

        self.assertEqual(await door("met-name", self.mira, "Axes"), "bad_text")
        self.assertEqual(await door("met-name", None, ""), "bad_text")
        self.assertEqual(await door("met-remove", None, "x"), "bad_text")
        self.assertEqual(await door("met-join", None, ""), "bad_text")
        self.assertEqual(await door("met-remove", None, "", True), "bad_text")
        self.assertEqual(await door("remove", None, ""), "needs_confirm")  # (the old rules stand)

    async def test_the_list_is_newest_session_first(self) -> None:
        transcripts = TranscriptStore(self.db)
        later = await transcripts.open_session(self.g, self.one.id, 1_700_100_000)
        await transcripts.add_lines(self.g, later, [Line(LINE_MS, ALICE.id, "A wolf.", "x")])
        await self.met_row("barbarians")
        await self.met.add(
            self.g,
            self.one.id,
            later,
            [("a wolf", 1, (LINE_MS,), (ALICE.id,))],
            session_started_at=1_700_100_000,
        )
        self.assertEqual([i["what"] for i in await self.items()], ["a wolf", "barbarians"])
