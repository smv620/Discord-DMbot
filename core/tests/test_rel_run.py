"""Reading one session's relationships (#3244 slice 6) with a stand-in reader and embedder, against
a real Postgres: ties are checked and kept with their lineage, the type is chosen by meaning when
the match is clear, consent is asked again, a cut answer is retried in halves, and a half result
never replaces a whole one. Invented names."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from dmbot.memory.rel_type_texts import TYPES
from dmbot.transcript import rel_run
from dmbot.transcript.models import Line
from dmbot.transcript.rel_store import RelStore
from dmbot.transcript.store import TranscriptStore
from dmbot.transcript.theatre_store import Row, TheatreStore
from tests.test_memory_names import GUILD, NamesTest

TAVIN, MIRA = 8, 10
START = 1_700_000_000
MS = START * 1000
KNOWN = ["Tavin", "Quill", "Mira"]
NAMES = {TAVIN: "Tavin", MIRA: "Mira"}
KEYS = [t.key for t in TYPES]


def vec(key: str) -> list[float]:
    return [1.0 if k == key else 0.0 for k in KEYS]


async def embed_by_sentence(texts: Any, as_query: bool) -> list[list[float]]:
    """A stand-in: a type's text is its own one-hot; a sentence is the one-hot of the first type
    key it mentions ('kin' words -> kin_of, 'teach' -> mentors), else a vector near nothing."""
    if not as_query:
        return [vec(k) for k in KEYS]
    out = []
    for text in texts:
        low = text.lower()
        key = "kin_of" if "uncle" in low else "mentors" if "taught" in low else None
        out.append(vec(key) if key else [0.01] * len(KEYS))
    return out


class RunCase(NamesTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.transcripts = TranscriptStore(self.db)
        self.session = await self.transcripts.open_session(GUILD, self.campaign.id, START)
        await self.transcripts.end_session(GUILD, self.session, START + 100)
        await self.transcripts.add_lines(
            GUILD,
            self.session,
            [
                Line(MS + 1000, TAVIN, "a", "Quill is my uncle."),
                Line(MS + 2000, MIRA, "b", "He taught me swords."),
                Line(MS + 3000, TAVIN, "c", "We ride at dawn."),
                Line(MS + 4000, MIRA, "d", "Fine."),
            ],
        )
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                "SELECT id FROM transcript_lines WHERE session_id = %s ORDER BY started_ms",
                (self.session,),
            )
            self.ids = [int(r["id"]) for r in await cur.fetchall()]
        a, b, c, d = self.ids
        await TheatreStore(self.db).replace(
            GUILD,
            self.session,
            [
                Row(TAVIN, "[Quill] is my uncle.", (a,)),
                Row(MIRA, "[Quill] taught me swords.", (b,)),
                Row(TAVIN, "We ride at dawn.", (c,)),
                Row(MIRA, "Fine.", (d,)),
            ],
        )
        self.calls: list[str] = []
        self.agreed = {TAVIN, MIRA}

    async def still(self) -> set[int]:
        return set(self.agreed)

    async def go(self, answer: Any, **kw: Any) -> rel_run.Report:
        async def ask(instructions: str, window: str) -> Any:
            self.calls.append(window)
            return answer(window) if callable(answer) else SimpleNamespace(text=answer, cut=False)

        return await rel_run.run(
            self.db, ask, kw.pop("embed", embed_by_sentence), GUILD, self.session,
            agreed=self.agreed, names=NAMES, known=KNOWN, still_agreed=self.still, **kw,
        )  # fmt: skip

    async def kept(self) -> list[Any]:
        return await RelStore(self.db).read(GUILD, self.session)


ROWS = (
    "1 | Quill | Tavin | other | - | Quill is Tavin's uncle.\n"
    "2 | Quill | Mira | other | - | Quill taught Mira swordplay.\n"
)


class TheRun(RunCase):
    async def test_ties_are_checked_typed_by_meaning_and_kept_with_their_lines(self) -> None:
        report = await self.go(ROWS)
        self.assertTrue(report.saved)
        self.assertEqual((report.ties, report.kept), (2, 2))
        got = await self.kept()
        self.assertEqual(
            [(t.kind, t.ai_kind, t.chosen_by) for t in got],
            [("kin_of", "other", "embedding"), ("mentors", "other", "embedding")],
        )
        self.assertEqual(got[0].source_ids, (self.ids[0],))
        self.assertEqual(got[1].source_ids, (self.ids[1],))

    async def test_a_weak_match_leaves_the_readers_own_type(self) -> None:
        await self.go("3 | Quill | Mira | knows | friendly | Quill nods at Mira.")
        (tie,) = await self.kept()
        self.assertEqual((tie.kind, tie.chosen_by, tie.word), ("knows", "ai", "friendly"))

    async def test_a_row_naming_someone_off_the_list_is_dropped_and_counted(self) -> None:
        report = await self.go("1 | Quill | Gorm | kin_of | - | Quill is Gorm's uncle.")
        self.assertFalse(report.saved)
        self.assertEqual(report.counts["unknown_name"], 1)

    async def test_a_form_of_address_is_counted_and_never_stored(self) -> None:
        report = await self.go("1 | Quill | Tavin | term_of_address | - | He calls him child.")
        self.assertEqual(report.counts["form_of_address_not_stored"], 1)
        self.assertEqual(await self.kept(), [])

    async def test_a_failed_embedding_leaves_the_readers_own_types(self) -> None:
        async def broken(texts: Any, as_query: bool) -> list[list[float]]:
            raise RuntimeError("down")

        report = await self.go(ROWS.replace("other", "kin_of"), embed=broken)
        self.assertEqual(report.counts["embed_failed"], 1)
        self.assertEqual({t.chosen_by for t in await self.kept()}, {"ai"})

    async def test_a_cut_answer_is_retried_in_halves_never_taken_as_nothing(self) -> None:
        def answer(window: str) -> Any:
            if window.count("\n") >= 3:  # the whole script is too long to answer
                return SimpleNamespace(text="1 | Quill | Tavin |", cut=True)
            return SimpleNamespace(
                text=ROWS.splitlines()[0] if window.startswith("1 |") else "", cut=False
            )

        report = await self.go(answer)
        self.assertEqual(report.counts["split"], 1)
        self.assertEqual(len(await self.kept()), 1)

    async def test_chatter_with_no_rows_is_nothing_found_not_a_failed_window(self) -> None:
        report = await self.go("There are no relationships in this scene.")
        self.assertEqual((report.failed_windows, report.counts["unreadable_answer"]), (0, 1))
        self.assertEqual(len(self.calls), 1)  # (not asked again in halves)

    async def test_a_co_author_who_stopped_keeps_their_whole_line_out_of_the_reader(self) -> None:
        a, b, _, d = self.ids
        await TheatreStore(self.db).replace(
            GUILD,
            self.session,
            [Row(TAVIN, "[Quill] is my uncle.", (a, b)), Row(MIRA, "Fine.", (d,))],
        )
        self.agreed = {TAVIN}  # (Mira stopped; her words are in the first line too)
        await self.go(ROWS)
        self.assertEqual(self.calls, [])

    async def test_the_save_uses_who_agrees_at_that_moment(self) -> None:
        def answer(window: str) -> Any:
            return SimpleNamespace(text=ROWS, cut=False)

        report = await self.go(answer)
        self.assertEqual(report.kept, 2)

    async def test_a_window_that_cannot_be_read_keeps_the_old_ties(self) -> None:
        await self.go(ROWS)

        def broken(window: str) -> Any:
            raise RuntimeError("out of funds")

        report = await self.go(broken)
        self.assertEqual((report.failed_windows, report.saved), (1, False))
        self.assertEqual(len(await self.kept()), 2)  # (the good ones stand)

    async def test_a_person_who_stops_during_the_call_has_the_window_left_out_whole(self) -> None:
        def answer(window: str) -> Any:
            self.agreed = {TAVIN}  # (Mira pressed Stop while the AI was away)
            return SimpleNamespace(text=ROWS, cut=False)

        report = await self.go(answer)
        self.assertEqual(report.counts["window_left_out_no_longer_agreed"], 1)
        self.assertEqual(await self.kept(), [])

    async def test_a_person_who_has_stopped_is_never_sent(self) -> None:
        self.agreed = {TAVIN}
        await self.go(ROWS)
        self.assertTrue(all("Mira" not in c for c in self.calls))

    async def test_no_id_goes_to_the_reader_and_the_wait_is_shown(self) -> None:
        seen: list[rel_run.Progress] = []

        async def progress(p: rel_run.Progress) -> None:
            seen.append(p)

        await self.go(ROWS, progress=progress, window=2)
        self.assertEqual([p.stage for p in seen][-1], rel_run.STAGE_DONE)
        self.assertEqual((seen[1].part, seen[1].of), (1, 2))
        # (speakers are display names, and the script lines carry no ids)
        self.assertTrue(
            all(
                line.split(" | ")[1].split(":")[0] in NAMES.values()
                for call in self.calls
                for line in call.splitlines()
            )
        )


class TheSwitchedWiring(RunCase):
    async def test_a_failing_reader_never_shows_and_never_raises(self) -> None:
        from dmbot.ui import theatre_button

        class Boom:
            async def complete(self, instructions: str, window: str) -> Any:
                raise RuntimeError("down")

        async def agreed_now() -> set[int]:
            return set(self.agreed)

        async def table_names(guild_id: int, campaign_id: str) -> list[str]:
            return KNOWN

        bot = SimpleNamespace(
            campaigns=SimpleNamespace(db=self.db), voyage=SimpleNamespace(embed=embed_by_sentence)
        )
        memory = SimpleNamespace(table_names=table_names)
        await theatre_button.read_relationships(
            bot, GUILD, self.session, Boom(), agreed_now, NAMES, memory, self.campaign
        )
        self.assertEqual(await self.kept(), [])
