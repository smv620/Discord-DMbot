"""The numbers meaning search compares (#2664): kept per campaign and model, compared only inside
one campaign, and a person's words leave no copy: a line's numbers go with the line by every route.
Invented names and made-up numbers."""

from __future__ import annotations

import asyncio

from dmbot.campaigns import CampaignStore
from dmbot.memory import vectors as vs
from dmbot.transcript.models import Line
from dmbot.transcript.store import TranscriptStore
from tests.pg import DatabaseTest

GUILD, OTHER_GUILD, DM, PLAYER = 1, 2, 7, 8
START = 1_700_000_000
MODEL = "voyage-3.5"


def line(seconds: int, user: int, text: str) -> Line:
    return Line(START * 1000 + seconds * 1000, user, text, text)


class Kept(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.campaigns = CampaignStore(self.db)
        self.transcripts = TranscriptStore(self.db)
        self.store = vs.VectorStore(self.db, clock=lambda: START)
        self.one = (await self.campaigns.create(GUILD, "Frostmaiden", DM)).id
        self.two = (await self.campaigns.create(GUILD, "Sunken City", DM)).id

    async def make_line(self, campaign: str, user: int = PLAYER, guild: int = GUILD) -> str:
        sid = await self.transcripts.open_session(guild, campaign, START)
        (line_id,) = await self.transcripts.add_lines(guild, sid, [line(1, user, "words")])
        return str(line_id)

    def item(self, owner: str, vector: list[float], kind: str = vs.ASSERTION) -> vs.Item:
        return vs.Item(kind, owner, vs.text_hash(owner), vector)

    async def test_vectors_are_kept_and_the_closest_come_first(self) -> None:
        kept = await self.store.save(
            GUILD,
            self.one,
            MODEL,
            [
                self.item("far", [0.0, 1.0]),
                self.item("near", [1.0, 0.1]),
                self.item("note", [1.0, 0.0], vs.DM_NOTE),
            ],
        )
        self.assertEqual(kept, 3)
        got = await self.store.nearest(GUILD, self.one, MODEL, [1.0, 0.0])
        self.assertEqual([(m.kind, m.owner_id) for m in got], [
            ("dm_note", "note"), ("assertion", "near"), ("assertion", "far")])  # fmt: skip
        self.assertAlmostEqual(got[0].score, 1.0)
        only = await self.store.nearest(
            GUILD, self.one, MODEL, [1.0, 0.0], kinds=[vs.ASSERTION], limit=1
        )
        self.assertEqual([m.owner_id for m in only], ["near"])

    async def test_a_vector_saved_again_replaces_the_first(self) -> None:
        await self.store.save(GUILD, self.one, MODEL, [self.item("a", [1.0, 0.0])])
        await self.store.save(
            GUILD, self.one, MODEL, [vs.Item(vs.ASSERTION, "a", "new", [0.0, 1.0])]
        )
        self.assertEqual(await self.store.kept(GUILD, self.one, MODEL), {("assertion", "a"): "new"})
        self.assertEqual(await self.store.count(GUILD, self.one), 1)

    async def test_nothing_is_compared_across_campaigns_or_servers(self) -> None:
        await self.store.save(GUILD, self.one, MODEL, [self.item("mine", [1.0, 0.0])])
        await self.store.save(GUILD, self.two, MODEL, [self.item("theirs", [1.0, 0.0])])
        self.assertEqual(
            [m.owner_id for m in await self.store.nearest(GUILD, self.one, MODEL, [1.0, 0.0])],
            ["mine"],
        )
        self.assertEqual(await self.store.nearest(OTHER_GUILD, self.one, MODEL, [1.0, 0.0]), [])
        self.assertEqual(
            await self.store.kept(GUILD, self.two, MODEL),
            {("assertion", "theirs"): vs.text_hash("theirs")},
        )

    async def test_a_campaign_that_is_not_there_keeps_nothing(self) -> None:
        self.assertEqual(await self.store.save(GUILD, "nope", MODEL, [self.item("a", [1.0])]), 0)
        self.assertEqual(
            await self.store.save(OTHER_GUILD, self.one, MODEL, [self.item("a", [1.0])]), 0
        )

    async def test_vectors_of_another_model_or_size_are_never_compared(self) -> None:
        await self.store.save(GUILD, self.one, "old-model", [self.item("old", [1.0, 0.0])])
        await self.store.save(GUILD, self.one, MODEL, [self.item("odd", [1.0, 0.0, 0.0])])
        await self.store.save(GUILD, self.one, MODEL, [self.item("ok", [1.0, 0.0])])
        got = await self.store.nearest(GUILD, self.one, MODEL, [1.0, 0.0])
        self.assertEqual([m.owner_id for m in got], ["ok"])

    async def test_a_change_of_model_drops_the_numbers_of_the_others(self) -> None:
        await self.store.save(GUILD, self.one, "old-model", [self.item("old", [1.0])])
        await self.store.save(GUILD, self.one, MODEL, [self.item("new", [1.0])])
        self.assertEqual(await self.store.drop_other_models(GUILD, self.one, MODEL), 1)
        self.assertEqual(
            list(await self.store.kept(GUILD, self.one, MODEL)), [("assertion", "new")]
        )
        self.assertEqual(await self.store.kept(GUILD, self.one, "old-model"), {})

    async def test_text_that_is_gone_loses_its_numbers(self) -> None:
        await self.store.save(
            GUILD, self.one, MODEL, [self.item("a", [1.0]), self.item("b", [1.0])]
        )
        self.assertEqual(await self.store.drop(GUILD, self.one, vs.ASSERTION, ["a", "zzz"]), 1)
        self.assertEqual(await self.store.drop(GUILD, self.one, vs.ASSERTION, []), 0)
        self.assertEqual(list(await self.store.kept(GUILD, self.one, MODEL)), [("assertion", "b")])

    async def test_a_lines_numbers_are_never_dropped_by_hand(self) -> None:
        self.assertEqual(await self.store.drop(GUILD, self.one, vs.LINE, ["1"]), 0)

    async def test_bad_vectors_and_kinds_are_skipped(self) -> None:
        kept = await self.store.save(
            GUILD,
            self.one,
            MODEL,
            [self.item("empty", []), self.item("huge", [0.0] * (vs.MAX_DIMS + 1)),
             vs.Item("secret_stuff", "x", "h", [1.0]), vs.Item(vs.LINE, "1", "h", [1.0])],
        )  # fmt: skip
        self.assertEqual(kept, 0)  # a line is saved only with `save_lines`


class Lines(Kept):
    async def kept_lines(self, campaign: str) -> set[str]:
        return {o for (k, o) in await self.store.kept(GUILD, campaign, MODEL) if k == vs.LINE}

    async def test_a_line_is_saved_only_for_a_line_of_that_campaign(self) -> None:
        mine, theirs = await self.make_line(self.one), await self.make_line(self.two)
        got = await self.store.save_lines(
            GUILD, self.one, MODEL,
            [vs.Item(vs.LINE, mine, "h", [1.0]), vs.Item(vs.LINE, theirs, "h", [1.0]),
             vs.Item(vs.LINE, "999999", "h", [1.0]), vs.Item(vs.LINE, "x", "h", [1.0])],
        )  # fmt: skip
        self.assertEqual(got, 1)
        self.assertEqual(await self.kept_lines(self.one), {mine})
        self.assertEqual(await self.kept_lines(self.two), set())

    async def test_a_line_deleted_before_its_numbers_arrive_leaves_nothing(self) -> None:
        gone = await self.make_line(self.one)
        await self.transcripts.delete_person(GUILD, PLAYER)  # while the text was being embedded
        self.assertEqual(
            await self.store.save_lines(
                GUILD, self.one, MODEL, [vs.Item(vs.LINE, gone, "h", [1.0])]
            ),
            0,
        )
        self.assertEqual(await self.store.count(GUILD, self.one), 0)

    async def test_a_line_deleted_while_the_save_runs_is_skipped_and_the_rest_are_kept(
        self,
    ) -> None:
        gone, stays = await self.make_line(self.one, PLAYER), await self.make_line(self.one, DM)
        items = [vs.Item(vs.LINE, gone, "h", [1.0]), vs.Item(vs.LINE, stays, "h", [1.0])]
        async with self.db.guild(GUILD) as deleting:
            await deleting.execute("DELETE FROM transcript_lines WHERE id = %s", (int(gone),))
            saving = asyncio.ensure_future(self.store.save_lines(GUILD, self.one, MODEL, items))
            await asyncio.sleep(0.5)  # it is waiting on the delete, which has not committed
            self.assertFalse(saving.done())
        self.assertEqual(await saving, 1)
        self.assertEqual(await self.kept_lines(self.one), {stays})

    async def test_stop_recording_me_takes_the_numbers_of_that_persons_lines(self) -> None:
        mine, other = await self.make_line(self.one, PLAYER), await self.make_line(self.one, DM)
        await self.store.save_lines(
            GUILD, self.one, MODEL,
            [vs.Item(vs.LINE, mine, "h", [1.0]), vs.Item(vs.LINE, other, "h", [1.0])],
        )  # fmt: skip
        await self.store.save(GUILD, self.one, MODEL, [self.item("note", [1.0], vs.DM_NOTE)])
        await self.transcripts.delete_person(GUILD, PLAYER)
        self.assertEqual(await self.kept_lines(self.one), {other})
        self.assertEqual(await self.store.count(GUILD, self.one), 2)  # the DM's line and the note

    async def test_a_deleted_session_transcript_takes_its_numbers(self) -> None:
        sid = await self.transcripts.open_session(GUILD, self.one, START)
        (line_id,) = await self.transcripts.add_lines(GUILD, sid, [line(1, PLAYER, "words")])
        await self.store.save_lines(
            GUILD, self.one, MODEL, [vs.Item(vs.LINE, str(line_id), "h", [1.0])]
        )
        async with self.db.guild(GUILD) as conn:
            await conn.execute("UPDATE transcript_sessions SET ended_at = %s", (START + 10,))
        gone = await self.transcripts.delete_session_transcript(
            GUILD, self.one, sid, START + 10_000, START + 10_000
        )
        self.assertEqual(gone, 1)
        self.assertEqual(await self.store.count(GUILD, self.one), 0)

    async def test_a_deleted_campaign_takes_all_its_numbers(self) -> None:
        mine = await self.make_line(self.one)
        await self.store.save_lines(GUILD, self.one, MODEL, [vs.Item(vs.LINE, mine, "h", [1.0])])
        await self.store.save(GUILD, self.one, MODEL, [self.item("note", [1.0])])
        await self.store.save(GUILD, self.two, MODEL, [self.item("keep", [1.0])])
        async with self.db.guild(GUILD) as conn:
            await conn.execute("DELETE FROM campaigns WHERE id = %s", (self.one,))
        self.assertEqual(await self.store.count(GUILD, self.one), 0)
        self.assertEqual(await self.store.count(GUILD, self.two), 1)
