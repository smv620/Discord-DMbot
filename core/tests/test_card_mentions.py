"""The mentions step of a character's card job (#5050): where each character was first met and last
mentioned, and DMbot's words for what was happening. A stand-in AI and recap store. Invented names.
Needs Postgres."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

from dmbot.ai import Reply
from dmbot.memory import card_mentions
from dmbot.memory.card_summaries import CardSummaries
from dmbot.memory.models import CONFIRMED
from dmbot.memory.models import DM as DM_SOURCE
from dmbot.transcript.models import Line
from dmbot.transcript.store import TranscriptStore
from tests.test_memory_names import DM, GUILD, PLAYER, NamesTest

START = 1_700_000_000
DAY = 86_400


def line(second: int, user: int, text: str, **kw: Any) -> Line:
    return Line((START + second) * 1000, user, text, text, **kw)


def ai(text: str = "when the party argued over the ship's price") -> Any:
    out: Any = type("FakeAI", (), {})()
    out.complete = AsyncMock(return_value=Reply(text=text, cut=False))
    return out


class TheMentions(NamesTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.rows = CardSummaries(self.db, clock=lambda: 1_700_000_000)
        self.store = TranscriptStore(self.db)
        await self.consent.grant(GUILD, DM)
        await self.consent.grant(GUILD, PLAYER)
        ids: dict[str, str] = {}
        for name in ("Shafina", "Rook"):
            made = await self.memory.add_entity(
                GUILD, self.campaign.id, type="character", name=name, source=DM_SOURCE,
                status=CONFIRMED, description=f"{name} lives here.",
            )  # fmt: skip
            ids[name] = made.value.id
        self.shafina, self.rook = ids["Shafina"], ids["Rook"]
        self.s1 = await self.store.open_session(GUILD, self.campaign.id, START)
        self.s2 = await self.store.open_session(GUILD, self.campaign.id, START + DAY)
        self.s3 = await self.store.open_session(GUILD, self.campaign.id, START + 2 * DAY)
        await self.store.add_lines(
            GUILD,
            self.s1,
            [
                line(1, DM, "Welcome back."),
                line(2, PLAYER, "We reach the inn and Shafina greets us."),
            ],
        )
        await self.store.add_lines(
            GUILD,
            self.s2,
            [
                line(1, DM, "Last time Shafina greeted us at the inn."),  # (the recap read aloud)
                line(2, PLAYER, "Rook sneaks off with the map."),
            ],
        )
        await self.store.add_lines(
            GUILD, self.s3, [line(1, DM, "Shafina argues about the ship's price.")]
        )

    async def lookup_get(self, guild_id: int, campaign_id: str) -> Any:
        assert self.bot.lookup is not None
        return await self.bot.lookup.get(guild_id, campaign_id)

    async def go(
        self, client: Any, ids: list[str] | None = None, recaps: Any = None
    ) -> dict[str, str]:
        return await card_mentions.refresh_mentions(
            self.rows,
            self.store,
            recaps,
            self.lookup_get,
            lambda: self.consent.recorded_in(GUILD, self.campaign.id),
            client,
            GUILD,
            self.campaign.id,
            ids or [self.shafina, self.rook],
        )

    async def test_first_and_last_come_from_the_sessions_with_the_scene_the_ai_wrote(self) -> None:
        # (session 2 opens with the recap: marked, so it never makes Shafina's "first")
        await self.store.save_session_split(
            GUILD, self.s2, {"parts": [["recap", 1, 1]], "unsure": False}
        )
        client = ai()
        got = await self.go(client)
        self.assertEqual(got, {self.shafina: "kept", self.rook: "kept"})
        shafina = await self.rows.get(GUILD, self.campaign.id, self.shafina)
        assert shafina is not None and shafina.first and shafina.last
        self.assertEqual((shafina.first.session_at, shafina.first.line), (START, 2))
        self.assertEqual((shafina.last.session_at, shafina.last.line), (START + 2 * DAY, 1))
        self.assertEqual(shafina.first.scene, "when the party argued over the ship's price")
        rook = await self.rows.get(GUILD, self.campaign.id, self.rook)
        assert rook is not None and rook.first and rook.last
        self.assertEqual((rook.first.session_at, rook.first.line), (START + DAY, 2))
        self.assertEqual(rook.first, rook.last)

    async def test_a_recap_beat_near_the_line_is_the_scene_and_no_ai_is_asked(self) -> None:
        beats = [{"what": "The party reached the Northlook inn", "who": [], "lines": [2],
                  "kind": "event", "where": "Bryn Shander"}]  # fmt: skip

        async def get(gid: int, cid: str, session_at: int) -> Any:
            return SimpleNamespace(beats=beats if session_at == START else None)

        client = ai()
        await self.go(client, [self.shafina], SimpleNamespace(get=get))
        got = await self.rows.get(GUILD, self.campaign.id, self.shafina)
        assert got is not None and got.first
        self.assertEqual(got.first.scene, "The party reached the Northlook inn in Bryn Shander")
        self.assertEqual(client.complete.await_count, 1)  # (only the last mention needed the AI)

    async def test_a_character_never_mentioned_keeps_a_row_that_says_so(self) -> None:
        made = await self.memory.add_entity(
            GUILD, self.campaign.id, type="character", name="Nobody", source=DM_SOURCE,
            status=CONFIRMED, description="Never said.",
        )  # fmt: skip
        client = ai()
        self.assertEqual(await self.go(client, [made.value.id]), {made.value.id: "never"})
        got = await self.rows.get(GUILD, self.campaign.id, made.value.id)
        assert got is not None
        self.assertEqual((got.first, got.last), (None, None))
        client.complete.assert_not_awaited()

    async def test_a_scene_already_written_is_kept_and_not_asked_again(self) -> None:
        client = ai()
        await self.go(client, [self.shafina])
        calls = client.complete.await_count
        await self.go(client, [self.shafina])
        self.assertEqual(client.complete.await_count, calls)  # (the mentions did not move)

    async def test_a_scene_moves_with_a_new_mention(self) -> None:
        await self.go(ai(), [self.shafina])
        await self.store.add_lines(
            GUILD, self.s3, [line(5, DM, "Shafina waves goodbye at the dock.")]
        )
        await self.go(ai("when she waved goodbye"), [self.shafina])
        got = await self.rows.get(GUILD, self.campaign.id, self.shafina)
        assert got is not None and got.last
        self.assertEqual((got.last.line, got.last.scene), (2, "when she waved goodbye"))

    async def test_the_ai_saying_unknown_gives_an_honest_clause_not_still_writing(self) -> None:
        await self.go(ai("UNKNOWN"), [self.shafina])
        got = await self.rows.get(GUILD, self.campaign.id, self.shafina)
        assert got is not None and got.first and got.last
        self.assertEqual(got.first.scene, "when they were first named")
        self.assertEqual(got.last.scene, "when they were last named")

    async def test_a_failing_ai_keeps_the_mention_and_tries_the_scene_again_next_round(
        self,
    ) -> None:
        broken: Any = type("Broken", (), {})()
        broken.complete = AsyncMock(side_effect=RuntimeError("the lines"))
        with self.assertLogs("dmbot.memory.card_mentions", level="WARNING") as logs:
            got = await self.go(broken, [self.shafina])
        self.assertEqual(got, {self.shafina: "scene-later"})
        self.assertNotIn("the lines", " ".join(logs.output))
        row = await self.rows.get(GUILD, self.campaign.id, self.shafina)
        assert row is not None and row.first
        self.assertEqual(row.first.scene, "")  # (the line says "still writing" until it is made)
        await self.go(ai(), [self.shafina])
        again = await self.rows.get(GUILD, self.campaign.id, self.shafina)
        assert again is not None and again.first
        self.assertTrue(again.first.scene)

    async def test_only_lines_the_count_would_read_reach_the_prompt(self) -> None:
        await self.consent.revoke(GUILD, PLAYER)
        await self.store.add_lines(GUILD, self.s3, [line(9, DM, "Shafina?", sidebar="question")])
        client = ai()
        await self.go(client, [self.shafina])
        sent = " ".join(c.args[1] for c in client.complete.await_args_list)
        self.assertNotIn("greets us", sent)  # (the player stopped: their lines are not sent)
        self.assertNotIn("Shafina?", sent)  # (a sidebar line is never sent)

    async def test_a_window_is_cut_to_twenty_lines_either_side(self) -> None:
        many = [line(i, DM, f"filler {i}") for i in range(1, 60)]
        many[29] = line(30, DM, "Shafina appears.")
        s4 = await self.store.open_session(GUILD, self.campaign.id, START + 3 * DAY)
        await self.store.add_lines(GUILD, s4, many)
        near = card_mentions.around(await self.store.lines(GUILD, s4), {DM}, 30)
        self.assertEqual(len(near), 41)
        self.assertIn("filler 10", near[0])
        self.assertIn("filler 50", near[-1])

    async def test_the_scenes_stop_when_the_rounds_calls_are_spent(self) -> None:
        client = ai()
        budget = card_mentions.Budget(1)
        got = await card_mentions.refresh_mentions(
            self.rows, self.store, None, self.lookup_get,
            lambda: self.consent.recorded_in(GUILD, self.campaign.id),
            client, GUILD, self.campaign.id, [self.shafina], budget,
        )  # fmt: skip
        self.assertEqual(client.complete.await_count, 1)  # (first and last: only one was allowed)
        self.assertEqual(got, {self.shafina: "scene-later"})
        self.assertEqual(budget.used, 1)

    async def test_a_player_who_stops_during_the_call_keeps_their_lines_off_the_card(self) -> None:
        async def revoke(*args: Any, **kw: Any) -> Any:
            await self.consent.revoke(GUILD, PLAYER)
            return Reply(text="when the party reached the inn", cut=False)

        client = ai()
        client.complete = AsyncMock(side_effect=revoke)
        got = await self.go(client, [self.shafina])
        self.assertEqual(got, {self.shafina: "scene-later"})
        row = await self.rows.get(GUILD, self.campaign.id, self.shafina)
        assert row is not None and row.first
        self.assertEqual(row.first.scene, "")  # (nothing made from their line is kept)
