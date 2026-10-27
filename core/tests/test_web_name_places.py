"""Where a name to check was said, on the recap page (#2403): the DM's read of short windows of the
transcript. Every line is read for the speaker's consent at that moment. Invented names."""

from __future__ import annotations

import json
from typing import Any

from dmbot.transcript.models import Line, Lineage, line_ref
from dmbot.transcript.store import TranscriptStore
from tests.test_recap_web import BOB
from tests.test_web_api import ALICE, THURSDAY
from tests.test_web_name_checks import AT, WithChecks

GUILD = THURSDAY.id
CARL = 777  # a player who speaks in the lines
DANA = 778


class Places(WithChecks):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.tx = TranscriptStore(self.db)

    async def open(self, campaign: str | None = None, at: int = AT) -> str:
        return await self.tx.open_session(GUILD, campaign or self.one.id, at)

    async def say(self, sid: str, lines: list[Line]) -> None:
        await self.tx.add_lines(GUILD, sid, lines)

    async def point(self, entity: str, sid: str, line: Line, *, at: int = AT) -> None:
        """A mention pointing at the line, as the scan leaves it."""
        start = line.text.lower().index("harrow")
        await self.memory.add_mention(
            GUILD, self.one.id, entity, line_ref=line_ref(sid, line.started_ms, line.user_id),
            span=(start, start + 6), confidence=1.0, method="exact", source="scan",
            session_started_at=at,
        )  # fmt: skip

    async def sql(self, text: str, *args: Any) -> None:
        async with self.db.guild(GUILD) as conn:
            await conn.execute(text, args)

    async def agree(self, *users: int, leave: tuple[int, ...] = ()) -> None:
        for user in users:
            await self.sql(
                "INSERT INTO consent (guild_id, user_id, granted_at) VALUES (%s, %s, 0)"
                " ON CONFLICT DO NOTHING",
                GUILD, user,
            )  # fmt: skip
        for user in leave:
            await self.sql("DELETE FROM consent WHERE guild_id = %s AND user_id = %s", GUILD, user)

    async def card(self) -> dict[str, Any]:
        (card,) = (await self.listed())["names"]
        return dict(card)

    async def table(self) -> tuple[str, str, list[Line]]:
        """A name suggested, three lines of three speakers around it, all agreeing."""
        harrow = await self.suggest("Harrow")
        sid = await self.open()
        lines = [
            Line(61_000, CARL, "The road was long.", "The road was long."),
            Line(65_000, ALICE.id, "We rode to Harrow's keep.", "We rode to Harrow's keep."),
            Line(70_000, DANA, "It was cold there.", "It was cold there."),
        ]
        await self.say(sid, lines)
        await self.point(harrow, sid, lines[1])
        await self.agree(ALICE.id, CARL, DANA)
        return harrow, sid, lines

    async def test_the_dm_reads_the_lines_around_the_name_with_who_and_when(self) -> None:
        await self.table()
        await self.sql(
            "UPDATE transcript_sessions SET started_at = 5, speaker_names = %s::jsonb",
            json.dumps({str(CARL): "Carl", str(ALICE.id): "Alice"}),
        )
        card = await self.card()
        self.assertEqual(card["morePlaces"], 0)
        (place,) = card["places"]
        self.assertEqual((place["session"], place["available"]), (1, True))
        self.assertEqual(
            place["lines"],
            [
                {"speaker": "Carl", "at": 56, "text": "The road was long."},
                {"speaker": "Alice", "at": 60, "text": "We rode to Harrow's keep."},
                {"speaker": "Someone", "at": 65, "text": "It was cold there."},
            ],
        )

    async def test_a_speaker_who_stops_is_gone_from_the_very_next_read(self) -> None:
        await self.table()
        before = (await self.card())["places"][0]["lines"]
        self.assertTrue(all(not x.get("removed") for x in before))
        await self.agree(leave=(ALICE.id,))  # Stop recording me
        after = (await self.card())["places"][0]
        self.assertEqual(
            [x.get("removed") for x in after["lines"]], [None, True, None]
        )  # the line stays as a placeholder: no speaker, time or words
        self.assertEqual(after["lines"][1], {"removed": True})
        self.assertNotIn("Harrow's keep", json.dumps(await self.card()))

    async def test_a_window_with_everyone_stopped_has_nothing_to_show(self) -> None:
        await self.table()
        await self.agree(leave=(ALICE.id, CARL, DANA))
        (place,) = (await self.card())["places"]
        self.assertEqual((place["available"], place["lines"]), (False, []))
        self.assertNotIn("keep", json.dumps(await self.card()))

    async def test_a_speaker_who_agrees_again_is_shown_again(self) -> None:
        await self.table()
        await self.agree(leave=(ALICE.id,))
        await self.agree(ALICE.id)
        self.assertEqual((await self.card())["places"][0]["lines"][1]["speaker"], "Someone")

    async def test_the_dm_sidebar_is_never_a_place_or_a_neighbour(self) -> None:
        harrow = await self.suggest("Harrow")
        sid = await self.open()
        said = Line(65_000, ALICE.id, "We rode to Harrow's keep.", "We rode to Harrow's keep.")
        side = Line(64_000, ALICE.id, "Who is Harrow?", "Who is Harrow?", sidebar="question",
                    lineage=Lineage(ref="q1"))  # fmt: skip
        reply = Line(66_000, ALICE.id, "A lord, per memory.", "A lord, per memory.",
                     sidebar="answer", lineage=Lineage(reply_to="q1"))  # fmt: skip
        await self.say(sid, [side, said, reply])
        await self.point(harrow, sid, said)
        await self.agree(ALICE.id)
        (place,) = (await self.card())["places"]
        self.assertEqual([x["text"] for x in place["lines"]], ["We rode to Harrow's keep."])

    async def test_a_pointer_at_a_sidebar_line_shows_nothing(self) -> None:
        harrow = await self.suggest("Harrow")
        sid = await self.open()
        side = Line(64_000, ALICE.id, "Who is Harrow?", "Who is Harrow?", sidebar="question")
        await self.say(sid, [side])
        await self.point(harrow, sid, side)
        await self.agree(ALICE.id)
        (place,) = (await self.card())["places"]
        self.assertEqual((place["available"], place["lines"]), (False, []))

    async def test_a_deleted_transcript_says_it_is_gone_and_keeps_its_number(self) -> None:
        await self.table()
        await self.sql("DELETE FROM transcript_lines WHERE guild_id = %s", GUILD)
        await self.sql("UPDATE transcript_sessions SET line_count = 0, transcripts_deleted_at = 9")
        (place,) = (await self.card())["places"]
        self.assertEqual((place["session"], place["available"], place["lines"]), (1, False, []))

    async def test_two_windows_at_first_and_the_rest_counted(self) -> None:
        harrow = await self.suggest("Harrow")
        await self.agree(ALICE.id)
        sids = []
        for n in range(5):
            sid = await self.open(at=AT + n * 1000)
            line = Line(10_000 + n, ALICE.id, f"Harrow, number {n}.", f"Harrow, number {n}.")
            await self.say(sid, [line])
            await self.point(harrow, sid, line, at=AT + n * 1000)
            sids.append(sid)
        card = await self.card()
        # the earliest, and the middle one of five (as Discord's card picks them)
        self.assertEqual(
            [p["lines"][0]["text"] for p in card["places"]],
            ["Harrow, number 0.", "Harrow, number 2."],
        )
        self.assertEqual([p["session"] for p in card["places"]], [1, 3])
        self.assertEqual(card["morePlaces"], 3)

    async def test_a_name_never_said_anywhere_has_no_places(self) -> None:
        await self.suggest("Harrow")
        self.assertNotIn("places", await self.card())

    async def test_an_imported_session_reads_only_game_talk(self) -> None:
        harrow = await self.suggest("Harrow")
        sid = await self.open()
        chat = Line(64_000, CARL, "Pass the crisps.", "Pass the crisps.", topic="off_topic")
        said = Line(65_000, ALICE.id, "We rode to Harrow's keep.", "We rode to Harrow's keep.")
        await self.say(sid, [chat, said])
        await self.sql(
            "INSERT INTO session_imports (guild_id, campaign_id, session_id, import_id, file_name,"
            " imported_by, imported_at) VALUES (%s, %s, %s, %s, 'notes.txt', 1, 1)",
            GUILD, self.one.id, sid, "a" * 32,
        )  # fmt: skip
        await self.point(harrow, sid, said)
        await self.agree(ALICE.id, CARL)
        (place,) = (await self.card())["places"]
        self.assertEqual([x["text"] for x in place["lines"]], ["We rode to Harrow's keep."])

    async def test_a_pointer_into_another_campaigns_session_shows_nothing_of_it(self) -> None:
        harrow = await self.suggest("Harrow")
        other = await self.open(self.two.id, at=AT + 5)
        line = Line(65_000, ALICE.id, "Secret Harrow plans.", "Secret Harrow plans.")
        await self.say(other, [line])
        await self.point(harrow, other, line)
        await self.agree(ALICE.id)
        card = await self.card()
        self.assertNotIn("places", card)  # nothing of it is shown, not even a count

    async def test_only_a_dm_of_the_campaign_reads_it(self) -> None:
        await self.table()
        bob = await self.bob()
        self.assertEqual(await self.code(self.listed_as(bob)), "not_found")

    async def listed_as(self, who: Any) -> dict[str, Any]:
        from dmbot.web import name_checks as web

        return await web.read_checks(self.web, who, self.ref)

    async def test_the_door_refuses_a_caller_who_is_not_a_dm_even_by_sql(self) -> None:
        await self.table()
        bob = await self.bob()
        async with self.db.user(bob.user_id, session=bob.id_hash) as conn:
            cur = await conn.execute(
                "SELECT dmbot_recap_name_places(%s, %s, %s) AS r", (GUILD, self.one.id, AT)
            )
            self.assertIsNone((await cur.fetchone() or {}).get("r"))

    async def bob(self) -> Any:
        from typing import cast

        from dmbot.web import sessions
        from tests.test_recap_web import NOW

        token = await sessions.sign_in(self.web, cast(Any, BOB), [THURSDAY], now=NOW, days=30)
        got = await sessions.find(self.web, token, now=NOW)
        assert got is not None
        return got
