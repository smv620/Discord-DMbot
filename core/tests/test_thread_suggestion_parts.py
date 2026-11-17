"""Quests slice 3 (#4500; design `docs/design/quests.md` §5, §5a, §5b, §6a, §8, §9.1): the lore
reader suggests a thread whole, with its steps, rewards, help, time limit and branches.

Code's side (no AI, the answers are written here): every part and branch needs its own quote in
the text; a giver's name that doesn't fit leaves the line without one; help is lent only when the
text asks for it back; a time limit is words only; at most two hints a thread. The keep's side
(Postgres): the lines wait on the suggestion, ✅ Keep writes those left on as the DM's keep (source
'lore') and each branch as a thread with its `branch_of` link, all in one change, and one Undo takes
it all back with the suggestion waiting again. Invented names."""

from __future__ import annotations

import json
import unittest

from dmbot.memory import thread_parts as tp
from dmbot.memory import thread_read as tr
from dmbot.memory.models import DM, HINTED, name_key
from dmbot.memory.thread_suggestions import (
    ThreadSuggestionStore,
    Waiting,
    from_extra,
    unkeep_in,
)
from dmbot.ui import logic
from dmbot.ui import thread_questions as ui
from tests.test_memory_store import GUILD_A, MemoryTest

TEXT = (
    "Old Pell offers thirty gold to whoever drives the troll Gnashwater off, paid when it's done. "
    "Ilsa took the job. Pell lends her his mule, Biscuit, to carry the traps; bring her back fed. "
    "Pell's map of the fens is hers to keep. It must be done before the spring thaw floods the "
    "ford. The bridge at Mossford is down, so the fens are the only way. Pell's son Ander went "
    "into the fens last winter and was never found."
)
IDS = {"Old Pell": "pell", "Pell": "pell", "Gnashwater": "troll", "Ilsa": "ilsa", "Ander": "ander"}
ENTRIES = {name_key(n): {i} for n, i in IDS.items()}
NAMES = {"pell": "Old Pell", "troll": "Gnashwater", "ilsa": "Ilsa", "ander": "Ander"}


def job(**kw: object) -> tr.Said:
    base: dict[str, object] = {
        "shape": tr.ASK, "aim": "drive the troll off", "giver": "Old Pell",
        "target": "Gnashwater", "taker": "", "missing": (), "in_danger": (),
        "quote": "Old Pell offers thirty gold",
    }  # fmt: skip
    base.update(kw)
    return tr.Said(**base)  # type: ignore[arg-type]


def part(kind: str, words: str, quote: str, **kw: object) -> tr.SaidPart:
    return tr.SaidPart(kind, words, quote=quote, **kw)  # type: ignore[arg-type]


def check(*said: tr.Said) -> tuple[list[tr.Suggestion], dict[str, int]]:
    return tr.check(said, TEXT, ENTRIES, NAMES)


class TheParts(unittest.TestCase):
    def test_each_part_needs_its_own_quote_and_a_giver_that_doesnt_fit_is_left_off(self) -> None:
        (s,), counts = check(
            job(
                parts=(
                    part(tp.OBJECTIVE, "drive Gnashwater off", "drives the troll Gnashwater off"),
                    part(tp.REWARD, "thirty gold", "offers thirty gold", who="Old Pell",
                         due=tp.AFTERWARDS),
                    part(tp.COMPLICATION, "the bridge is down", "The bridge at Mossford is down"),
                    part(tp.REWARD, "a feast", "and a feast for all"),  # (never in the text)
                    part(tp.HELP, "a guide", "a guide", who="Somebody Else"),  # (no such name)
                )
            )
        )  # fmt: skip
        self.assertEqual(
            [(p.kind, p.words, p.who_id, p.due) for p in s.reads],
            [
                (tp.OBJECTIVE, "drive Gnashwater off", None, ""),
                (tp.REWARD, "thirty gold", "pell", tp.AFTERWARDS),
                (tp.COMPLICATION, "the bridge is down", None, ""),
            ],
        )
        self.assertEqual(counts.get("part_no_quote"), 2)

    def test_help_is_lent_only_when_the_text_asks_for_it_back(self) -> None:
        (s,), _ = check(
            job(
                parts=(
                    part(tp.HELP, "his mule, Biscuit", "lends her his mule, Biscuit", who="Pell",
                         back=True, terms="bring her back fed"),
                    part(tp.HELP, "Pell's map of the fens", "map of the fens is hers to keep",
                         terms="ignored", due=tp.UP_FRONT),
                )
            )
        )  # fmt: skip
        self.assertEqual(
            [(p.state, p.terms, p.due) for p in s.reads],
            [("lent", "bring her back fed", ""), ("to_keep", "", "")],
        )

    def test_a_time_limit_is_its_words_only(self) -> None:
        (s,), _ = check(
            job(
                time_limit="before the spring thaw floods the ford", limit=tp.HARD,
                limit_quote="before the spring thaw floods the ford",
            )
        )  # fmt: skip
        (limit,) = s.reads
        self.assertEqual(
            (limit.kind, limit.words, limit.state, limit.limit_kind),
            (tp.TIME_LIMIT, "before the spring thaw floods the ford", "running", tp.HARD),
        )
        (s2,), counts = check(job(time_limit="by the new moon", limit_quote="by the new moon"))
        self.assertEqual((s2.reads, counts.get("part_no_quote")), ((), 1))

    def test_one_or_stays_one_and_the_same_part_twice_is_one(self) -> None:
        (s,), _ = check(
            job(
                parts=(
                    part(tp.OBJECTIVE, "drive the troll off or kill it", "drives the troll"),
                    part(tp.OBJECTIVE, "Drive the troll off or kill it", "drives the troll"),
                )
            )
        )  # fmt: skip
        self.assertEqual([p.words for p in s.reads], ["drive the troll off or kill it"])

    def test_at_most_eight_parts(self) -> None:
        many = tuple(part(tp.OBJECTIVE, f"step {n}", "Ilsa took the job") for n in range(12))
        (s,), counts = check(job(parts=many))
        self.assertEqual((len(s.reads), counts.get("part_over")), (tr.PARTS_MAX, 12 - tr.PARTS_MAX))


class TheBranches(unittest.TestCase):
    def test_a_question_left_open_is_a_hint_and_at_most_two(self) -> None:
        hint = tr.SaidBranch(
            "What happened to Ander?", question=True, hint=True, missing=("Ander",),
            quote="was never found",
        )  # fmt: skip
        more = [
            tr.SaidBranch(f"Who saw Ander last {n}?", question=True, hint=True, quote="fens")
            for n in range(3)
        ]
        (s,), counts = check(job(branches=(hint, *more)))
        self.assertEqual(
            [(b.kind, b.name, b.state, b.parts) for b in s.branches[:1]],
            [("mystery", "What happened to Ander?", HINTED, ((tr.MISSING, "ander"),))],
        )
        self.assertEqual((len(s.branches), counts.get("hint_over")), (2, 2))

    def test_a_stated_branch_is_a_quest_and_one_without_its_quote_goes(self) -> None:
        (s,), counts = check(
            job(
                branches=(
                    tr.SaidBranch("find a way round the bridge", quote="the fens are the only way"),
                    tr.SaidBranch("rebuild the bridge", quote="the bridge will be rebuilt"),
                    tr.SaidBranch("Old Pell's job: Gnashwater", quote="Ilsa took the job"),
                )
            )
        )  # fmt: skip
        self.assertEqual([(b.kind, b.state) for b in s.branches], [("quest", tr.STATE["quest"])])
        self.assertEqual((counts.get("branch_no_quote"), counts.get("double")), (1, 1))

    def test_the_answer_is_read_with_its_parts_and_branches(self) -> None:
        answer = {
            "threads": [
                {"shape": "ask", "aim": "drive the troll off", "giver": "Old Pell",
                 "target": "Gnashwater", "taker": "", "missing": [], "in_danger": [],
                 "quote": "Old Pell offers thirty gold",
                 "parts": [{"kind": "help", "words": "his mule", "who": "Pell", "due": "",
                            "terms": "bring her back fed", "back": "yes",
                            "quote": "lends her his mule"},
                           {"kind": "loot", "words": "x", "who": "", "due": "", "terms": "",
                            "back": "no", "quote": "x"}],
                 "time_limit": "before the spring thaw", "limit": "soft",
                 "limit_quote": "before the spring thaw",
                 "branches": [{"aim": "What happened to Ander?", "question": True, "hint": True,
                               "target": "", "missing": ["Ander"], "in_danger": [],
                               "quote": "was never found"}]},
            ]
        }  # fmt: skip
        (got,) = tr.parse(json.dumps(answer))
        self.assertEqual(
            [(p.kind, p.back, p.terms) for p in got.parts], [(tp.HELP, True, "bring her back fed")]
        )
        self.assertEqual((got.time_limit, got.limit), ("before the spring thaw", tp.SOFT))
        self.assertEqual([(b.hint, b.missing) for b in got.branches], [(True, ("Ander",))])
        for words in ('"parts"', '"time_limit"', '"branches"', '"back"', "never a guess at who"):
            self.assertIn(words, tr.INSTRUCTIONS)


class TheCard(unittest.TestCase):
    """The 📜 card lists what's in it in the 📋 screen's words (the ux-critic's, #4471), each line
    one the DM can untick, and never shows a stored word."""

    lines = (
        tr.ReadPart(tp.OBJECTIVE, "drive Gnashwater off", "to_do", "q"),
        tr.ReadPart(tp.REWARD, "thirty gold", "owed", "q", "pell", due=tp.AFTERWARDS),
        tr.ReadPart(tp.HELP, "his mule", "lent", "q", "pell", terms="bring her back fed"),
        tr.ReadPart(tp.HELP, "a map", "to_keep", "q"),
        tr.ReadPart(tp.TIME_LIMIT, "before the thaw", "running", "q", limit_kind=tp.HARD),
        tr.ReadPart(tp.REWARD_GIVEN, "a warm meal", "given", "q", "pell"),
    )
    branch = tr.ReadBranch("mystery", "What happened to Ander?", "x", "q", HINTED)

    def waiting(self) -> Waiting:
        return Waiting(
            "s", "quest", "Old Pell's job: Gnashwater", "drive the troll off", "Old Pell offers",
            ((tr.GIVER, "pell"),), "Notes", 5, self.lines, (self.branch,),
        )  # fmt: skip

    def test_each_line_in_the_parts_screens_words(self) -> None:
        said = [ui.line_for(x, NAMES) for x in (*self.lines, self.branch)]
        self.assertEqual(
            said,
            [
                ("drive Gnashwater off", "Step"),
                ("thirty gold", "Reward from Old Pell, when the job's done"),
                ("his mule", "Lent by Old Pell, to return: bring her back fed"),
                ("a map", "Help, theirs to keep"),
                ("before the thaw", "Time limit, hard: miss it and something's lost"),
                ("a warm meal", "Given by Old Pell, not promised"),
                ("What happened to Ander?", f"{ui.BRANCH_WORD}, {ui.HINT_SAID}"),
            ],
        )
        for _words, what in said:
            self.assertLessEqual(len(what), logic.DESCRIPTION_MAX)
            for stored in ("to_keep", "_", "up_front", "afterwards"):
                self.assertNotIn(stored, what)

    def test_the_card_lists_them_and_strikes_one_left_out(self) -> None:
        text = ui.card_text(
            self.waiting(), NAMES, 1, 1, on=frozenset({0}), lines_on=frozenset({0, 1, 2, 3, 4, 5})
        )
        self.assertIn(ui.PARTS_HEADING, text)
        self.assertIn("• thirty gold (Reward from Old Pell, when the job's done)", text)
        self.assertIn("~~What happened to Ander?", text)
        self.assertIn(ui.PARTS_UNTICK, text)

    def test_a_branch_names_its_people_and_one_not_kept_yet(self) -> None:
        branch = tr.ReadBranch(
            "mystery", "Who killed Wit?", "x", "q", HINTED,
            ((tr.MISSING, "ander"), (tr.DANGER, "ilsa")),
        )  # fmt: skip
        _, what = ui.line_for(branch, NAMES, frozenset({"ilsa"}))
        self.assertEqual(
            what,
            f"{ui.BRANCH_WORD}, {ui.HINT_SAID} · Ander (the one missing), "
            f"Ilsa ({ui.NOT_KEPT_SHORT}) (in danger)",
        )

    def test_the_card_stays_inside_discords_limit(self) -> None:
        long = "a long winding phrase " * 20
        many = tuple(
            tr.ReadPart(tp.HELP, f"{n} {long}", "lent", "q", "pell", terms=long)
            for n in range(tr.PARTS_MAX)
        )
        branches = tuple(
            tr.ReadBranch("quest", f"{n} {long}"[:80], long, "q", "active",
                          tuple((tr.TARGET, w) for w in NAMES))
            for n in range(tr.BRANCHES_MAX)
        )  # fmt: skip
        big = Waiting(
            "s", "quest", long[:100], long, long, tuple((tr.TARGET, w) for w in NAMES), long, 5,
            many, branches,
        )  # fmt: skip
        text = ui.card_text(big, NAMES, 9, 9, on=frozenset(range(4)))
        self.assertLessEqual(len(text), 2000)
        self.assertIn("more: see the menu below", text)
        self.assertTrue(text.endswith(ui.AFTER))

    def test_stored_junk_reads_as_nothing(self) -> None:
        for raw in ("not json", '{"reads": null, "branches": 3}', "[]", None, {"reads": [7]}):
            self.assertEqual(from_extra(raw), ((), ()), raw)

    def test_a_card_from_before_shows_no_parts(self) -> None:
        old = Waiting("s", "quest", "x", "", "", (), "", 5)
        self.assertNotIn(ui.PARTS_HEADING, ui.card_text(old, {}, 1, 1, on=frozenset()))


class TheKeep(MemoryTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.store = ThreadSuggestionStore(self.db)
        self.pell = await self.add("Old Pell", status="confirmed")
        self.troll = await self.add("Gnashwater", status="confirmed")
        self.ander = await self.add("Ander", status="confirmed")

    async def offer(self) -> None:
        s = tr.Suggestion(
            "quest", "Old Pell's job: Gnashwater", "drive the troll off",
            "Old Pell offers thirty gold", ((tr.GIVER, self.pell), (tr.TARGET, self.troll)),
            tr.STATE["quest"],
            reads=(
                tr.ReadPart(tp.OBJECTIVE, "drive Gnashwater off", "to_do", "drives the troll"),
                tr.ReadPart(tp.REWARD, "thirty gold", "owed", "offers thirty gold", self.pell,
                            due=tp.AFTERWARDS),
                tr.ReadPart(tp.HELP, "his mule", "lent", "lends her his mule", self.pell,
                            terms="bring her back fed"),
                tr.ReadPart(tp.TIME_LIMIT, "before the spring thaw", "running", "spring thaw",
                            limit_kind=tp.SOFT),
            ),
            branches=(
                tr.ReadBranch("mystery", "What happened to Ander?", "What happened to Ander?",
                              "was never found", HINTED, ((tr.MISSING, self.ander),)),
            ),
        )  # fmt: skip
        self.assertEqual(await self.store.offer(GUILD_A, self.c, [s], "Notes", 5), 1)

    async def keep(self, leave_off: list[int] | None = None) -> tuple[str, int]:
        (s,) = await self.store.waiting(GUILD_A, self.c)
        kept = await self.memory.keep_thread(
            GUILD_A, self.c, s.name, s.kind, s.state, aim=s.aim, parts=list(s.parts),
            source=DM, suggestion_id=s.id, leave_off=leave_off or [],
        )  # fmt: skip
        assert kept.batch is not None
        return kept.value.id, kept.batch

    async def test_the_lines_wait_with_it_and_are_kept_in_one_change(self) -> None:
        await self.offer()
        (s,) = await self.store.waiting(GUILD_A, self.c)
        self.assertEqual([x.words for x in s.reads][:2], ["drive Gnashwater off", "thirty gold"])
        self.assertEqual([b.name for b in s.branches], ["What happened to Ander?"])
        self.assertEqual(len(s.lines), 5)
        thread, batch = await self.keep()
        parts, _, _, branches = await self.memory.thread_card(GUILD_A, self.c, thread)
        self.assertEqual(
            sorted((p.kind, p.words, p.state, p.source, p.status) for p in parts),
            sorted([
                (tp.OBJECTIVE, "drive Gnashwater off", "to_do", tp.LORE, "confirmed"),
                (tp.REWARD, "thirty gold", "owed", tp.LORE, "confirmed"),
                (tp.HELP, "his mule", "lent", tp.LORE, "confirmed"),
                (tp.TIME_LIMIT, "before the spring thaw", "running", tp.LORE, "confirmed"),
            ]),
        )  # fmt: skip
        reward = next(p for p in parts if p.kind == tp.REWARD)
        self.assertEqual((reward.who_id, reward.amount, reward.unit), (self.pell, None, ""))
        (branch,) = branches
        hint = await self.memory.entity(GUILD_A, self.c, branch)
        assert hint is not None
        self.assertEqual((hint.name, hint.type), ("What happened to Ander?", "mystery"))
        (t,) = [x for x in await self.memory.threads(GUILD_A, self.c) if x.thread.id == branch]
        assert t.state is not None
        self.assertEqual(t.state.condition, HINTED)
        self.assertEqual([(i.who.id, i.role) for i in t.involved], [(self.ander, tr.MISSING)])
        # (one Undo: the thread, its parts and its branch go, and the suggestion waits again)
        await self.memory.undo(GUILD_A, self.c, batch, after=unkeep_in(batch))
        self.assertEqual(await self.memory.threads(GUILD_A, self.c), [])
        self.assertEqual(await self.store.count(GUILD_A, self.c), 1)

    async def test_lines_left_off_are_never_kept(self) -> None:
        await self.offer()
        thread, _ = await self.keep(leave_off=[1, 4])  # (the reward, and the branch)
        parts, _, _, branches = await self.memory.thread_card(GUILD_A, self.c, thread)
        self.assertEqual(
            sorted(p.kind for p in parts), sorted([tp.OBJECTIVE, tp.HELP, tp.TIME_LIMIT])
        )
        self.assertEqual(branches, [])
        self.assertEqual(len(await self.memory.threads(GUILD_A, self.c)), 1)

    async def test_a_suggestion_from_before_keeps_as_it_did(self) -> None:
        await self.offer()
        async with self.db.guild(GUILD_A) as conn:  # (a row as 0507 left it: the default)
            await conn.execute("UPDATE thread_suggestions SET extra = '{}'::jsonb")
        (s,) = await self.store.waiting(GUILD_A, self.c)
        self.assertEqual(s.lines, ())
        thread, _ = await self.keep()
        parts, _, _, branches = await self.memory.thread_card(GUILD_A, self.c, thread)
        self.assertEqual((parts, branches), ([], []))

    async def test_a_waiting_branch_counts_as_suggested(self) -> None:
        await self.offer()
        known = {key for key, _ in await self.store.known(GUILD_A, self.c)}
        self.assertIn(name_key("What happened to Ander?"), known)

    async def test_a_giver_that_no_longer_fits_leaves_the_part_without_one(self) -> None:
        place = await self.add("Mossford", type="place", status="confirmed")
        s = tr.Suggestion(
            "quest", "Pell's job", "x", "q", ((tr.GIVER, self.pell),), tr.STATE["quest"],
            reads=(tr.ReadPart(tp.REWARD, "a room", "owed", "q", place),
                   tr.ReadPart(tp.HELP, "a cart", "to_keep", "q", "f" * 32)),  # (gone)
        )  # fmt: skip
        await self.store.offer(GUILD_A, self.c, [s], "Notes", 5)
        thread, _ = await self.keep()
        parts, _, _, _ = await self.memory.thread_card(GUILD_A, self.c, thread)
        self.assertEqual(
            sorted((p.words, p.who_id) for p in parts), [("a cart", None), ("a room", None)]
        )


if __name__ == "__main__":
    unittest.main()
