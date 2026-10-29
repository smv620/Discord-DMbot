"""Keeping a campaign's numbers up to date (#2664): what is sent, in what batches, under which
call limit, with consent checked before and after the call, long text split not cut, progress as
counts only, and what happens when the account runs out. A fake Voyage; invented words."""

from __future__ import annotations

from collections.abc import Sequence

from dmbot.ai import AIOutOfFunds
from dmbot.campaigns import CampaignStore
from dmbot.memory import embed_sources as es
from dmbot.memory import embedding as em
from dmbot.memory import vectors as vs
from dmbot.memory.voyage import BATCH, ProviderError
from dmbot.transcript.models import Line
from dmbot.transcript.store import TranscriptStore
from tests.pg import DatabaseTest

GUILD, DM, ANNA, BOB = 1, 7, 8, 9
START = 1_700_000_000
MODEL = "voyage-3.5"


class Fake:
    """Hands back [length, 1.0] for each text, and keeps what it was asked."""

    def __init__(self, *, fail: Exception | None = None, on_call: object = None) -> None:
        self.asked: list[list[str]] = []
        self.fail = fail
        self.on_call = on_call

    async def embed(self, texts: Sequence[str], *, query: bool = False) -> list[list[float]]:
        self.asked.append(list(texts))
        if self.on_call is not None:
            await self.on_call()  # type: ignore[operator]
        if self.fail is not None:
            raise self.fail
        return [[float(len(t)), 1.0] for t in texts]


def line(seconds: int, user: int, text: str, **kw: object) -> Line:
    return Line(START * 1000 + seconds * 1000, user, text, text, **kw)  # type: ignore[arg-type]


class Splitting(DatabaseTest):
    def test_short_text_is_one_piece_and_empty_text_none(self) -> None:
        self.assertEqual(em.split_text("hello"), ["hello"])
        self.assertEqual(em.split_text("   "), [])

    def test_long_text_is_split_at_breaks_and_nothing_is_lost(self) -> None:
        paragraph = "The ship sailed north. " * 10
        text = "\n\n".join([paragraph.strip()] * 6)
        parts = em.split_text(text, 300)
        self.assertGreater(len(parts), 1)
        self.assertTrue(all(len(p) <= 300 for p in parts))
        self.assertEqual(" ".join(" ".join(parts).split()), " ".join(text.split()))
        self.assertTrue(all(p.endswith(".") for p in parts))  # cut at sentence ends

    def test_a_word_longer_than_the_limit_is_the_only_cut_inside(self) -> None:
        parts = em.split_text("a" * 25 + " b", 10)
        self.assertEqual("".join(parts).replace(" ", ""), "a" * 25 + "b")
        self.assertTrue(all(len(p) <= 10 for p in parts))

    def test_pieces_carry_a_part_number_and_the_hash_of_the_whole_text(self) -> None:
        text = "word " * 2000
        pieces = em.pieces_of(vs.LINE, "5", text, speaker=ANNA)
        self.assertGreater(len(pieces), 1)
        self.assertEqual([p.owner_id for p in pieces][:2], ["5#1", "5#2"])
        self.assertEqual({p.hash for p in pieces}, {vs.text_hash(text)})
        (single,) = em.pieces_of(vs.ASSERTION, "e", "short")
        self.assertEqual(single.owner_id, "e")


class Running(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.campaigns = CampaignStore(self.db)
        self.transcripts = TranscriptStore(self.db)
        self.store = vs.VectorStore(self.db, clock=lambda: START)
        self.c = (await self.campaigns.create(GUILD, "Frostmaiden", DM)).id
        self.agree: frozenset[int] = frozenset({DM, ANNA, BOB})
        self.progress: list[tuple[str, int, int]] = []
        self.counted = 0
        self.on = True

    async def consenting(self, guild_id: int) -> frozenset[int]:
        return self.agree

    async def allowed(self) -> bool:
        return self.on

    async def on_progress(self, stage: str, done: int, total: int) -> None:
        self.progress.append((stage, done, total))

    async def counter(self) -> None:
        self.counted += 1

    async def session(self, *lines: Line) -> list[int]:
        sid = await self.transcripts.open_session(GUILD, self.c, START)
        return list(await self.transcripts.add_lines(GUILD, sid, list(lines)))

    async def go(self, fake: Fake, *, max_calls: int = 10, model: str = MODEL) -> em.Embedded:
        return await em.run(
            self.store, fake, [es.LineSource(self.db)], self.consenting, GUILD, self.c, model,
            allowed=self.allowed, max_calls=max_calls, count_call=self.counter,
            on_progress=self.on_progress,
        )  # fmt: skip

    async def kept(self) -> set[str]:
        return {o for (_, o) in await self.store.kept(GUILD, self.c, MODEL)}

    async def test_lines_get_numbers_and_the_run_reports_counts(self) -> None:
        a, b = await self.session(line(1, ANNA, "We sail at dawn"), line(2, BOB, "Aye"))
        fake = Fake()
        out = await self.go(fake)
        self.assertEqual((out.made, out.calls, out.failed, out.left), (2, 1, "", 0))
        self.assertEqual(await self.kept(), {str(a), str(b)})
        self.assertEqual(fake.asked, [["We sail at dawn", "Aye"]])
        self.assertEqual(self.counted, 1)
        self.assertNotIn("dawn", out.line())

    async def test_what_is_made_is_not_asked_again_and_a_changed_text_is(self) -> None:
        (a,) = await self.session(line(1, ANNA, "We sail at dawn"))
        await self.go(Fake())
        again = Fake()
        out = await self.go(again)
        self.assertEqual((out.made, out.current, again.asked), (0, 1, []))
        async with self.db.guild(GUILD) as conn:
            await conn.execute("UPDATE transcript_lines SET text = 'We sail at dusk'")
        changed = Fake()
        out = await self.go(changed)
        self.assertEqual((out.made, changed.asked), (1, [["We sail at dusk"]]))
        self.assertEqual(await self.kept(), {str(a)})

    async def test_only_game_talk_by_people_who_agree_is_sent(self) -> None:
        await self.session(
            line(1, ANNA, "We sail at dawn"),
            line(2, 999, "A bot speaking"),
            line(3, BOB, "pass the chips please"),
        )
        async with self.db.guild(GUILD) as conn:
            await conn.execute(
                "UPDATE transcript_lines SET topic = 'table_talk' WHERE user_id = %s", (BOB,)
            )
        fake = Fake()
        out = await self.go(fake)
        self.assertEqual(fake.asked, [["We sail at dawn"]])
        self.assertEqual(out.skipped, 1)  # the one who does not agree; table talk is never read

    async def test_someone_who_stops_while_the_text_is_away_has_nothing_kept(self) -> None:
        _, b = await self.session(line(1, ANNA, "We sail at dawn"), line(2, BOB, "Aye"))

        async def anna_stops() -> None:
            self.agree = frozenset({DM, BOB})

        out = await self.go(Fake(on_call=anna_stops))
        self.assertEqual(await self.kept(), {str(b)})
        self.assertEqual((out.made, out.skipped), (1, 1))

    async def test_a_person_who_stops_during_one_batch_is_not_sent_in_the_next(self) -> None:
        await self.session(
            *[line(n, DM, f"dm {n}") for n in range(BATCH)], line(BATCH + 1, ANNA, "late words")
        )
        calls = 0

        async def anna_stops_after_the_first() -> None:
            nonlocal calls
            calls += 1
            if calls == 1:
                self.agree = frozenset({DM, BOB})

        fake = Fake(on_call=anna_stops_after_the_first)
        out = await self.go(fake)
        self.assertEqual(len(fake.asked), 1)  # the second batch held only Anna: no call at all
        self.assertNotIn("late words", [t for batch in fake.asked for t in batch])
        self.assertEqual((out.made, out.skipped), (BATCH, 1))

    async def test_nothing_is_sent_when_the_switch_is_off_or_turned_off_mid_run(self) -> None:
        await self.session(*[line(n, ANNA, f"line {n}") for n in range(BATCH + 3)])
        self.on = False
        fake = Fake()
        out = await self.go(fake)
        self.assertEqual((fake.asked, out.made, out.left), ([], 0, BATCH + 3))
        self.on = True

        async def switched_off() -> None:
            self.on = False

        out = await self.go(Fake(on_call=switched_off))
        self.assertEqual((out.made, out.left), (BATCH, 3))

    def test_a_stored_part_id_names_its_owner(self) -> None:
        self.assertEqual((em.owner_of("12#2"), em.owner_of("12")), ("12", "12"))

    async def test_a_line_piece_with_no_speaker_is_never_sent(self) -> None:
        self.assertFalse(em._may_send(em.Piece(vs.LINE, "1", "x", "h"), frozenset({ANNA})))
        self.assertTrue(em._may_send(em.Piece(vs.ASSERTION, "e", "x", "h"), frozenset()))

    async def test_a_speaker_who_no_longer_agrees_loses_numbers_already_kept(self) -> None:
        _, b = await self.session(line(1, ANNA, "We sail at dawn"), line(2, BOB, "Aye"))
        await self.go(Fake())
        self.agree = frozenset({DM, BOB})
        out = await self.go(Fake())
        self.assertEqual(await self.kept(), {str(b)})
        self.assertEqual(out.dropped, 1)

    async def test_a_line_deleted_while_the_text_is_away_is_not_kept(self) -> None:
        _, b = await self.session(line(1, ANNA, "We sail at dawn"), line(2, BOB, "Aye"))

        async def anna_deleted() -> None:
            await self.transcripts.delete_person(GUILD, ANNA)

        out = await self.go(Fake(on_call=anna_deleted))
        self.assertEqual(await self.kept(), {str(b)})
        self.assertEqual(out.made, 1)

    async def test_batches_share_one_call_limit_and_the_rest_is_left_not_forgotten(self) -> None:
        await self.session(*[line(n, ANNA, f"line {n}") for n in range(BATCH + 5)])
        first = await self.go(Fake(), max_calls=1)
        self.assertEqual((first.made, first.left, first.calls), (BATCH, 5, 1))
        self.assertEqual(self.progress[-1], ("making", BATCH, BATCH + 5))
        rest = await self.go(Fake(), max_calls=1)
        self.assertEqual((rest.made, rest.left, rest.current), (5, 0, BATCH))

    async def test_a_limit_of_nothing_makes_nothing_and_says_what_is_left(self) -> None:
        await self.session(line(1, ANNA, "We sail at dawn"))
        fake = Fake()
        out = await self.go(fake, max_calls=0)
        self.assertEqual((fake.asked, out.made, out.left, self.counted), ([], 0, 1, 0))

    async def test_no_credit_stops_the_run_with_a_code_and_keeps_what_was_made(self) -> None:
        await self.session(*[line(n, ANNA, f"line {n}") for n in range(BATCH * 2)])
        calls = 0

        async def second_call_fails() -> None:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise AIOutOfFunds("none")

        out = await self.go(Fake(on_call=second_call_fails))
        self.assertEqual((out.stopped, out.made, out.left), (em.OUT_OF_FUNDS, BATCH, BATCH))

    async def test_a_failed_call_stops_the_run_with_a_code_and_never_its_words(self) -> None:
        await self.session(line(1, ANNA, "We sail at dawn"))
        out = await self.go(Fake(fail=ProviderError("busy", 503)))
        self.assertEqual((out.failed, out.made, out.left), (em.FAILED, 0, 1))
        self.assertNotIn("dawn", out.line())

    async def test_a_failing_progress_write_never_loses_the_work(self) -> None:
        await self.session(line(1, ANNA, "We sail at dawn"))

        async def broken(stage: str, done: int, total: int) -> None:
            raise RuntimeError("db")

        out = await em.run(
            self.store, Fake(), [es.LineSource(self.db)], self.consenting, GUILD, self.c, MODEL,
            allowed=self.allowed, max_calls=3, on_progress=broken,
        )  # fmt: skip
        self.assertEqual(out.made, 1)

    async def test_a_change_of_model_drops_the_old_numbers_and_makes_all_again(self) -> None:
        await self.session(line(1, ANNA, "We sail at dawn"))
        await self.go(Fake(), model="voyage-3")
        out = await self.go(Fake())
        self.assertEqual((out.dropped, out.made), (1, 1))
        self.assertEqual(await self.store.kept(GUILD, self.c, "voyage-3"), {})

    async def test_a_long_line_goes_in_parts_and_is_all_there(self) -> None:
        text = "The ship sailed north. " * 400
        (a,) = await self.session(line(1, ANNA, text))
        fake = Fake()
        out = await self.go(fake)
        sent = [t for batch in fake.asked for t in batch]
        self.assertGreater(len(sent), 1)
        self.assertEqual(" ".join(" ".join(sent).split()), " ".join(text.split()))
        self.assertTrue(all(len(t) <= em.PART_CHARS for t in sent))
        self.assertEqual(await self.kept(), {f"{a}#{n}" for n in range(1, len(sent) + 1)})
        self.assertEqual(out.made, len(sent))
        # a text that shrinks to one part drops the others
        async with self.db.guild(GUILD) as conn:
            await conn.execute("UPDATE transcript_lines SET text = 'short now'")
        again = await self.go(Fake())
        self.assertEqual((await self.kept(), again.dropped), ({str(a)}, len(sent)))

    async def test_entries_are_embedded_by_name_and_description_only_when_confirmed(self) -> None:
        async with self.db.guild(GUILD) as conn:
            for n, (name, desc, status) in enumerate(
                [
                    ("Bram", "A loyal cook", "confirmed"),
                    ("Mira", "A spy", "proposed"),
                    ("Zed", "", "confirmed"),
                ]
            ):
                await conn.execute(
                    "INSERT INTO memory_entities (guild_id, campaign_id, id, type, name,"
                    " description, status, source, created_at) VALUES (%s, %s, %s, 'character',"
                    " %s, %s, %s, 'dm', 1)",
                    (GUILD, self.c, f"{n:032x}", name, desc, status),
                )
        fake = Fake()
        out = await em.run(
            self.store, fake, [es.EntitySource(self.db)], self.consenting, GUILD, self.c, MODEL,
            allowed=self.allowed, max_calls=3,
        )  # fmt: skip
        self.assertEqual((fake.asked, out.made), ([["Bram: A loyal cook"]], 1))

    async def test_nothing_is_read_from_another_campaign(self) -> None:
        other = (await self.campaigns.create(GUILD, "Sunken City", DM)).id
        sid = await self.transcripts.open_session(GUILD, other, START)
        await self.transcripts.add_lines(GUILD, sid, [line(1, ANNA, "A secret of theirs")])
        fake = Fake()
        out = await self.go(fake)
        self.assertEqual((fake.asked, out.made), ([], 0))
