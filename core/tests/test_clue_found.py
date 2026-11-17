"""A session's "clue found" (#4624, quests slice 6c; the ruled plan and Supervisor's rulings 2 and
3; migration 0525): the session reader is shown each open thread's clues by label and may say the
table found one (a clue shown, or a new one's words) and who found it: the player characters it
names, else the quoted speakers' own characters, never an NPC. Code checks it all; the DM keeps it
on the 📜 card, where the clue is tied, each finder learns it, and a new clue only some found is
theirs alone, in one change that one Undo takes back. Invented names."""

from __future__ import annotations

import unittest
from typing import Any, ClassVar

from dmbot.memory import session_progress as sp
from dmbot.memory import session_threads as st
from dmbot.memory import thread_progress as tp
from dmbot.memory.gate import Viewer
from dmbot.memory.models import DM, OPEN
from dmbot.transcript.models import Line as Heard
from dmbot.transcript.store import TranscriptStore
from dmbot.ui import thread_progress_card as card
from tests.agreed import agree
from tests.test_memory_store import GUILD_A, MemoryTest
from tests.test_session_threads import BEL, DM_ID, KIA, lines

TALK = lines(
    (DM_ID, "Under the floorboard you find a torn letter, sealed with a raven."),
    (BEL, "I read it out loud: the raven meets at the mill."),
    (KIA, "Wren pockets the silver key from the desk."),
)
LETTER = sp.KnownClue("c1", "the torn letter")
MILL = sp.KnownThread("t1", "Who burned the mill?", clues=(LETTER,))
OTHER = sp.KnownThread("t2", "Guard the caravan", clues=(sp.KnownClue("c2", "the ledger"),))
KNOWN = [MILL, OTHER]
# (Bel plays Ash; Kia plays Wren; Harl is an NPC, so never a finder)
FINDERS = sp.Finders({"ash": "pc-ash", "wren": "pc-wren"}, {BEL: "pc-ash", KIA: "pc-wren"})


def said(**kw: Any) -> sp.SaidProgress:
    base: dict[str, Any] = {
        "thread": "T1", "change": sp.CLUE_FOUND, "part_kind": "", "part": "", "state": "",
        "words": "", "due": "", "quote": "I read it out loud", "clue": "T1.C1", "who": (),
    }  # fmt: skip
    base.update(kw)
    return sp.SaidProgress(**base)


def check(*s: sp.SaidProgress, finders: sp.Finders | None = FINDERS) -> tuple[list[Any], Any]:
    return sp.check(s, TALK, KNOWN, where=st.where, seen=set(), finders=finders)


class TheRead(unittest.TestCase):
    def test_the_known_block_lists_each_thread_s_clues_by_label(self) -> None:
        block = sp.known_block(KNOWN)
        self.assertIn("T1.C1 clue:", block)
        self.assertIn("T2.C1 clue:", block)

    def test_the_answer_s_who_is_read_as_names(self) -> None:
        raw = (
            '{"threads": [], "progress": [{"thread": "T1", "change": "clue_found", '
            '"part_kind": "", "part": "", "state": "", "words": "", "due": "", "clue": "T1.C1", '
            '"who": ["Ash ", "", 3], "quote": "x"}]}'
        )
        (got,) = sp.parse(raw)
        self.assertEqual((got.clue, got.who), ("T1.C1", ("Ash",)))

    def test_a_clue_shown_found_by_the_speaker_s_own_character(self) -> None:
        (line,), dropped = check(said())
        thread_id, change, _starts, speakers = line
        self.assertEqual((thread_id, change.kind, change.clue_id), ("t1", tp.CLUE, "c1"))
        self.assertEqual((change.finder_ids, speakers, dropped), (("pc-ash",), (BEL,), {}))

    def test_the_names_said_are_the_finders(self) -> None:
        (line,), _ = check(said(who=("Wren", "Ash")))
        self.assertEqual(set(line[1].finder_ids), {"pc-ash", "pc-wren"})

    def test_a_new_clue_by_its_words(self) -> None:
        (line,), _ = check(said(clue="", words="the silver key", quote="Wren pockets the silver"))
        change = line[1]
        self.assertEqual((change.clue_id, change.words, change.finder_ids),
                         (None, "the silver key", ("pc-wren",)))  # fmt: skip

    def test_what_code_refuses(self) -> None:
        cases = {
            "no_clue": said(clue="T1.C9"),
            "other_thread": said(clue="T2.C1"),
            "not_a_clue": said(clue="", words=""),
        }
        for why, s in cases.items():
            _, dropped = check(s)
            self.assertEqual(dropped, {why: 1}, why)

    def test_an_npc_or_the_dm_never_finds_a_clue(self) -> None:
        _, dropped = check(said(who=("Harl",)))  # (an NPC holding it hasn't found it, ruling 2)
        self.assertEqual(dropped, {"no_finder": 1})
        _, dropped = check(said(quote="you find a torn letter"))  # (the DM's line, no character)
        self.assertEqual(dropped, {"no_finder": 1})
        _, dropped = check(said(), finders=None)
        self.assertEqual(dropped, {"no_finder": 1})

    def test_one_line_per_clue_and_finders_however_many_say_it(self) -> None:
        got, dropped = check(said(), said(quote="the raven meets at the mill"))
        self.assertEqual((len(got), dropped), (1, {"double": 1}))

    def test_a_new_finder_of_the_same_clue_is_a_new_line(self) -> None:
        ash = tp.Change(tp.CLUE, "", "", tp.FOUND, None, "", "c1", ("pc-ash",))
        wren = tp.Change(tp.CLUE, "", "", tp.FOUND, None, "", "c1", ("pc-wren",))
        self.assertNotEqual(ash.key("t1"), wren.key("t1"))
        self.assertIsNone(tp.check(tp.Change(tp.CLUE, words="a key")))  # (nobody found it)


class TheCard(unittest.TestCase):
    NAMES: ClassVar[dict[str, str]] = {"c1": "the torn letter", "pc-ash": "Ash", "pc-wren": "Wren"}

    def test_a_new_clue_says_who_found_it(self) -> None:
        new = tp.Change(tp.CLUE, "", "the silver key", tp.FOUND, None, "", None, ("pc-wren",))
        self.assertEqual(card.line_text(new, self.NAMES), "Clue found: the silver key (by Wren)")

    def test_a_clue_the_campaign_has_says_who_knows_it_now(self) -> None:
        both = tp.Change(tp.CLUE, "", "", tp.FOUND, None, "", "c1", ("pc-ash", "pc-wren"))
        self.assertEqual(
            card.line_text(both, self.NAMES),
            "Clue found: the torn letter (now known by Ash and Wren)",
        )
        gone = tp.Change(tp.CLUE, "", "", tp.FOUND, None, "", "c1", ("pc-gone",))
        self.assertEqual(card.line_text(gone, self.NAMES), "Clue found again: the torn letter")
        new_gone = tp.Change(tp.CLUE, "", "a key", tp.FOUND, None, "", None, ("pc-gone",))
        self.assertEqual(card.line_text(new_gone, self.NAMES), "Clue found: a key")


LINE_MS = 1_700_000_005_000
NOW = 1_700_000_100


class TheKeep(MemoryTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.store = tp.ThreadProgressStore(self.db)
        made = await self.memory.new_thread(
            GUILD_A, self.c, "Who burned the mill?", "mystery", OPEN, source=DM
        )
        self.thread = made.value.id
        self.ash = await self.pc("Ash", BEL)
        self.wren = await self.pc("Wren", KIA)
        await agree(self.db, GUILD_A, DM_ID, BEL, KIA)
        self.transcripts = TranscriptStore(self.db)
        self.sid = await self.transcripts.open_session(GUILD_A, self.c, 1_700_000_000)
        await self.transcripts.add_lines(
            GUILD_A, self.sid, [Heard(LINE_MS, BEL, "x", "I read it out loud.")]
        )

    async def pc(self, name: str, player: int) -> str:
        made = await self.memory.add_entity(
            GUILD_A, self.c, name=name, type="character", role="player_character",
            played_by=player, status="confirmed", source=DM,
        )  # fmt: skip
        return made.value.id

    async def offer(self, change: tp.Change) -> None:
        await self.store.offer(
            GUILD_A, self.c, self.sid, 1_700_000_000,
            [(self.thread, change, (LINE_MS,), (BEL,))], now=NOW,
        )  # fmt: skip

    async def keep(self) -> int:
        (line,) = await self.store.waiting(GUILD_A, self.c)
        done = await self.memory.keep_thread_progress(GUILD_A, self.c, [line.id], source=DM)
        assert done.batch is not None
        return done.batch

    async def clues(self) -> list[Any]:
        return await self.memory.thread_clues(GUILD_A, self.c, self.thread, Viewer.the_dm())

    async def knowers(self, clue_id: str) -> set[str]:
        """The characters with a kept "knows" on the clue."""
        return {
            r.subject_id
            for r in await self.memory.relations(
                GUILD_A, self.c, entity_id=clue_id, include_secret=True
            )
            if r.predicate == "knows" and r.status == "confirmed" and r.object_id == clue_id
        }

    def found(self, *finders: str, clue_id: str | None = None, words: str = "") -> tp.Change:
        return tp.Change(tp.CLUE, "", words, tp.FOUND, None, "", clue_id, finders)

    async def test_a_new_clue_found_by_some_is_tied_and_theirs_alone(self) -> None:
        await self.offer(tp.Change(tp.CLUE, "", "the silver key", tp.FOUND, None, "", None,
                                   (self.wren,)))  # fmt: skip
        batch = await self.keep()
        (clue,) = await self.clues()
        self.assertEqual((clue.name, clue.found, clue.false_lead), ("the silver key", True, False))
        entry = await self.memory.entity(GUILD_A, self.c, clue.clue_id)
        assert entry is not None
        self.assertTrue(entry.audience_limited)  # (Ash didn't find it: Wren's alone)
        self.assertEqual(await self.knowers(clue.clue_id), {self.wren})
        await self.memory.undo(GUILD_A, self.c, batch, source=DM, after=tp.unkeep_in(batch))
        self.assertEqual(await self.clues(), [])
        gone = await self.memory.entity(GUILD_A, self.c, clue.clue_id)
        self.assertTrue(gone is None or gone.status != "confirmed")  # (the new clue went too)
        self.assertEqual(len(await self.store.waiting(GUILD_A, self.c)), 1)  # (waits again)

    async def test_the_campaign_s_clue_keeps_its_audience_and_gains_its_finder(self) -> None:
        tied = await self.memory.tie_clue(
            GUILD_A, self.c, self.thread, new_name="the torn letter", source=DM
        )
        clue_id = tied.value[0].subject_id
        await self.offer(tp.Change(tp.CLUE, "", "", tp.FOUND, None, "", clue_id, (self.ash,)))
        await self.keep()
        (clue,) = await self.clues()
        self.assertTrue(clue.found)
        self.assertEqual(await self.knowers(clue_id), {self.ash})
        entry = await self.memory.entity(GUILD_A, self.c, clue_id)
        assert entry is not None
        self.assertFalse(entry.audience_limited)  # (the DM's own clue: never narrowed here)

    async def test_a_clue_the_dm_took_off_since_is_never_tied_back(self) -> None:
        tied = await self.memory.tie_clue(
            GUILD_A, self.c, self.thread, new_name="the torn letter", source=DM
        )
        clue_id = tied.value[0].subject_id
        await self.offer(self.found(self.ash, clue_id=clue_id))
        await self.memory.untie_clue(GUILD_A, self.c, self.thread, clue_id, source=DM)
        (line,) = await self.store.waiting(GUILD_A, self.c)
        done = await self.memory.keep_thread_progress(GUILD_A, self.c, [line.id], source=DM)
        self.assertEqual(done.value, 0)  # ("Not a clue to this" stands)
        self.assertEqual(await self.clues(), [])

    async def test_a_new_clue_every_player_character_found_is_not_narrowed(self) -> None:
        await self.offer(self.found(self.ash, self.wren, words="the silver key"))
        await self.keep()
        (clue,) = await self.clues()
        entry = await self.memory.entity(GUILD_A, self.c, clue.clue_id)
        assert entry is not None
        self.assertFalse(entry.audience_limited)
        self.assertEqual(await self.knowers(clue.clue_id), {self.ash, self.wren})

    async def test_new_words_naming_a_tied_clue_find_that_clue(self) -> None:
        tied = await self.memory.tie_clue(
            GUILD_A, self.c, self.thread, new_name="the torn letter", source=DM
        )
        clue_id = tied.value[0].subject_id
        await self.offer(self.found(self.wren, words="The Torn Letter"))
        await self.keep()
        (clue,) = await self.clues()  # (one clue, never a twin)
        self.assertEqual(clue.clue_id, clue_id)
        self.assertEqual(await self.knowers(clue_id), {self.wren})
        entry = await self.memory.entity(GUILD_A, self.c, clue_id)
        assert entry is not None
        self.assertFalse(entry.audience_limited)

    async def test_a_finder_gone_since_leaves_the_line_unkept(self) -> None:
        await self.offer(tp.Change(tp.CLUE, "", "a key", tp.FOUND, None, "", None, (self.wren,)))
        await self.memory.set_entity_type(GUILD_A, self.c, self.wren, "npc", source=DM)
        (line,) = await self.store.waiting(GUILD_A, self.c)
        done = await self.memory.keep_thread_progress(GUILD_A, self.c, [line.id], source=DM)
        self.assertEqual(done.value, 0)
        self.assertEqual(await self.clues(), [])
