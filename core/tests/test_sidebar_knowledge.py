"""DMbot answering questions about itself (#1195): the index built from its own documents and
code, the ranking, what is marked built or planned, and the link at the running commit. No
Discord and no network."""

from __future__ import annotations

import re
import unittest
from pathlib import Path
from unittest.mock import patch

from dmbot.rules.index import srd
from dmbot.sidebar import context, knowledge, knowledge_sources

SRC = Path(knowledge_sources.__file__).resolve().parents[1]
COMMIT = "0123456789abcdef0123456789abcdef01234567"
DOC = """# Title

Intro words.

## Guiding principles

**Game clock (decided 2026-10-04, #965).** The clock keeps game time. *Built, #965.*

**Share codes.** A DM may hand out a code. Not built yet: the code itself.

| Command | What |
|---|---|
| `/dmbot start` | Start |
"""


def build(question: str) -> str:
    return context.build(
        question,
        target="2024",
        fallback="2014",
        index=srd(),
        house_rules=[],
        lookup=None,
        scene="",
    ).prompt


class Chunking(unittest.TestCase):
    def test_a_document_splits_at_headings_and_bold_lead_ins(self) -> None:
        chunks = knowledge.chunk_markdown("docs/PLAN.md", DOC)
        self.assertEqual(
            [c.heading for c in chunks],
            ["Title", "Guiding principles", "Game clock", "Share codes"],
        )

    def test_who_decided_what_and_issue_numbers_are_left_out(self) -> None:
        text = " ".join(c.text for c in knowledge.chunk_markdown("docs/PLAN.md", DOC))
        for process in ("2026-10-04", "#965", "decided"):
            self.assertNotIn(process, text)
        self.assertIn("The clock keeps game time.", text)

    def test_each_chunk_says_whether_it_is_built(self) -> None:
        status = {c.heading: c.status for c in knowledge.chunk_markdown("docs/PLAN.md", DOC)}
        self.assertEqual(status["Game clock"], knowledge.BUILT)
        self.assertEqual(status["Share codes"], knowledge.PLANNED)
        self.assertEqual(status["Title"], knowledge.DECIDED)

    def test_a_long_block_is_cut_to_a_size_a_prompt_can_take(self) -> None:
        text = "## Big\n\n" + "\n".join(f"- line {n} " + "word " * 30 for n in range(60))
        chunks = knowledge.chunk_markdown("docs/PLAN.md", text)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(c.text) <= knowledge.CHUNK_MAX + 400 for c in chunks))

    def test_anchors_are_githubs(self) -> None:
        self.assertEqual(knowledge.slug("Feature notes"), "feature-notes")
        self.assertEqual(knowledge.slug("Players & the DM (v2)"), "players--the-dm-v2")


class Ranking(unittest.TestCase):
    def test_the_best_chunk_for_a_question_comes_first(self) -> None:
        chunks = knowledge.chunk_markdown("docs/PLAN.md", DOC)
        got = knowledge.retrieve("what does the game clock do in dmbot", knowledge.Ranker(chunks))
        self.assertEqual(got[0].heading, "Game clock")

    def test_a_game_question_gets_nothing(self) -> None:
        for question in (
            "do you need line of sight for fireball",
            "how do I handle a grapple",
            "what is the plan for the heist",
            "how much damage does a falling creature take",
            "how long does Bless last",
        ):
            self.assertNotIn("ABOUT DMBOT", build(question), question)

    def test_what_is_added_is_kept_to_the_budget(self) -> None:
        got = knowledge.retrieve("how does DMbot record and keep the game clock", budget=2500)
        self.assertLessEqual(sum(len(c.text) for c in got), 2500)


class Links(unittest.TestCase):
    def chunk(self, file: str) -> knowledge.Chunk:
        return knowledge.Chunk(file, "Game clock", "feature-notes", knowledge.BUILT, "text")

    def test_the_link_points_at_the_running_commit(self) -> None:
        self.assertEqual(
            knowledge.link(self.chunk("docs/PLAN.md"), COMMIT),
            f"https://github.com/smv620/Discord-DMbot/blob/{COMMIT}/docs/PLAN.md#feature-notes",
        )

    def test_the_commit_comes_from_the_build(self) -> None:
        with patch.dict("os.environ", {"GIT_COMMIT": COMMIT}):
            self.assertEqual(knowledge.build_commit(), COMMIT)
        with patch.dict("os.environ", {"GIT_COMMIT": "not a commit"}):
            self.assertIsNone(knowledge.build_commit())

    def test_no_link_without_a_commit_or_for_what_is_not_a_page_in_the_repo(self) -> None:
        with patch.dict("os.environ", {"GIT_COMMIT": ""}):
            self.assertIsNone(knowledge.link(self.chunk("docs/PLAN.md")))
        self.assertIsNone(knowledge.link(self.chunk("commands"), COMMIT))
        self.assertIsNone(knowledge.link(self.chunk("help card"), COMMIT))


class Shipped(unittest.TestCase):
    """The index of this checkout: new features cannot be left out of it."""

    def test_every_slash_command_in_the_code_is_in_the_index(self) -> None:
        in_code = 0
        for path in SRC.rglob("*.py"):
            in_code += len(re.findall(r"^@[\w.]*\bcommand\(", path.read_text("utf-8"), re.M))
        found = [c for c in knowledge_sources.command_chunks()]
        self.assertEqual(
            len(found), in_code, "a command's name or description isn't a plain string"
        )
        names = {c.heading for c in found}
        for name in ("/dmbot start", "/dmbot stop", "/dmbot help", "/consent give", "/transcript"):
            self.assertIn(name, names)

    def test_the_help_card_and_the_help_page_are_in_the_index(self) -> None:
        files = {c.file for c in knowledge_sources.collect()}
        self.assertIn("help card", files)
        if knowledge_sources.docs_root() is not None:
            self.assertTrue({"docs/PLAN.md", "README.md", "help page"} <= files)


class EveryChunk(unittest.TestCase):
    """Scans the whole index of this checkout, not a handful of questions."""

    def test_no_chunk_carries_a_session_name_a_date_or_an_issue_number(self) -> None:
        for chunk in knowledge_sources.collect():
            where = f"{chunk.file}: {chunk.heading}"
            for text in (chunk.heading, chunk.text):
                self.assertNotRegex(text, r"CloudDev|WebDev|Supervisor|RepoManager|dev1", where)
                self.assertNotRegex(text, r"\b\d{4}-\d{2}-\d{2}\b", where)
                self.assertNotRegex(text, r"#\d{3,}", where)

    def test_the_help_page_is_its_sentences_not_its_code(self) -> None:
        for chunk in knowledge_sources.collect():
            if chunk.file == "help page":
                for code in ("export ", "interface", "*/", "=>", "${"):
                    self.assertNotIn(code, chunk.text)
                self.assertIn("/dmbot start", chunk.text)


class GameWords(unittest.TestCase):
    """Words DMbot shares with the game must not add its documents to a rules question."""

    def test_game_questions_that_use_dmbots_words_get_no_block(self) -> None:
        for question in (
            "Can I restore hit points with a short rest?",
            "What does the Command spell do?",
            "who can see an invisible rogue?",
            "What do you do when a goblin grapples you?",
            "what is the weather in the forest between sessions",
            "does the bard record the song on the lute",
        ):
            self.assertNotIn("ABOUT DMBOT", build(question), question)

    def test_a_question_about_sharing_rules_between_campaigns_finds_its_section(self) -> None:
        self.assertIn("ABOUT DMBOT", build("how do I share my house rules with another campaign?"))


class TheOpenGate(unittest.TestCase):
    """How people ask DMbot about itself in its private chat: nobody says "DMbot" there (#1195)."""

    def about(self, question: str) -> str:
        prompt = build(question)
        return prompt.split("ABOUT DMBOT", 1)[1] if "ABOUT DMBOT" in prompt else ""

    def test_questions_put_to_dmbot_get_its_documents(self) -> None:
        for question in (
            "what can you do?",
            "can you track initiative?",
            "do you remember NPCs between sessions?",
            "how do I add my sheet?",
            "how do I peek behind the DM screen?",
            "how do I turn on table chat?",
        ):
            self.assertTrue(self.about(question), question)

    def test_the_right_section_comes_first(self) -> None:
        first = {
            "how do I add my sheet?": r"sheet",
            "how do I turn on table chat?": r"chat",
        }
        for question, pattern in first.items():
            shown = self.about(question).split("FROM DMBOT'S DOCUMENTS", 1)[1]
            lead = shown.splitlines()[1]
            self.assertRegex(lead.lower(), pattern, question)

    def test_a_rules_question_that_says_you_stays_a_rules_question(self) -> None:
        for question in (
            "can you see an invisible creature with truesight?",
            "does fireball need line of sight?",
        ):
            self.assertEqual(self.about(question), "", question)

    def test_every_button_label_and_command_name_in_the_index_opens_the_gate(self) -> None:
        ranker = knowledge.load()
        self.assertGreater(len(ranker.terms), 30)
        for term in sorted(ranker.terms):
            self.assertTrue(knowledge.opens_gate(f"about {term}", ranker), term)
            # (the label as a person types it, with the game's punctuation)
        self.assertTrue(knowledge.opens_gate("where is the D&D Beyond invite", ranker))

    def test_the_labels_people_press_are_in_the_index(self) -> None:
        terms = knowledge.load().terms
        for label in ("stop recording me", "peek behind the dm screen", "my character sheet"):
            self.assertIn(label, terms)


class LabelNames(unittest.TestCase):
    def test_two_modules_with_the_same_constant_name_both_keep_their_words(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "one.py").write_text(
                'STOP_LABEL = "Stop recording me"\nButton(label=STOP_LABEL)\n', encoding="utf-8"
            )
            (root / "two.py").write_text(
                'STOP_LABEL = "Stop sharing"\nButton(label=STOP_LABEL)\n', encoding="utf-8"
            )
            with patch.object(knowledge_sources, "PACKAGE", root):
                found = knowledge_sources.labels()
        self.assertIn("Stop recording me", found)
        self.assertIn("Stop sharing", found)


class GoldenQuestions(unittest.TestCase):
    """What the AI is given for the owner's kind of question: the section that answers it, how far
    it is built, and nothing about DMbot for a game question."""

    def test_stopping_the_recording(self) -> None:
        prompt = build("how do I stop being recorded")
        self.assertIn("ABOUT DMBOT", prompt)
        self.assertIn("Stop recording me", prompt)

    def test_the_game_clock(self) -> None:
        prompt = build("what does the game clock do")
        self.assertIn("ABOUT DMBOT", prompt)
        self.assertRegex(prompt, r"\[(BUILT|MIXED)\] (TimeBot|The Help page)")

    def test_character_sheets(self) -> None:
        prompt = build("how do I add my character sheet")
        self.assertIn("My character sheet", prompt)

    def test_fightbot(self) -> None:
        prompt = build("what's FightBot")
        self.assertIn("FightBot", prompt.split("ABOUT DMBOT", 1)[1])

    def test_remembering_npcs(self) -> None:
        prompt = build("do you remember NPCs between sessions")
        self.assertRegex(prompt.lower(), r"campaign memory|remember")

    def test_a_planned_feature_is_marked_so(self) -> None:
        chunks = knowledge.chunk_markdown("docs/PLAN.md", DOC)
        with patch.object(knowledge, "load", return_value=knowledge.Ranker(chunks)):
            shown = knowledge.render(knowledge.retrieve("what are dmbot share codes"))
        self.assertIn("[PLANNED] Share codes", shown)

    def test_a_rules_question_has_no_dmbot_block(self) -> None:
        self.assertNotIn("ABOUT DMBOT", build("does fireball need line of sight"))

    def test_the_prompt_carries_no_session_names_or_issue_numbers(self) -> None:
        prompt = build("what does the game clock do")
        self.assertNotRegex(prompt, r"CloudDev|Supervisor|WebDev|dev1|RepoManager")
        self.assertNotRegex(prompt.split("ABOUT DMBOT", 1)[1], r"#\d{3,}")

    def test_the_link_is_at_the_build_commit(self) -> None:
        with patch.dict("os.environ", {"GIT_COMMIT": COMMIT}):
            built = context.build(
                "what does the game clock do",
                target="2024",
                fallback="2014",
                index=srd(),
                house_rules=[],
                lookup=None,
                scene="",
            )
        if knowledge_sources.docs_root() is not None:
            assert built.about_link is not None
            self.assertIn(f"/blob/{COMMIT}/", built.about_link)
