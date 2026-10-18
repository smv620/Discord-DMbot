"""One message limit (#1835): the builders stay inside it, and no module carries its own 2000.

Discord refuses a message over 2,000 characters and the whole action fails, so every builder that
formats campaign names into one message is fed the worst case here, and a source check keeps new
copies of the number out.
"""

import ast
import unittest
from pathlib import Path

from dmbot.discord_limits import MESSAGE_MAX
from dmbot.memory import story_watch as memory_story_watch
from dmbot.ui import guide, story_watch

SRC = Path(__file__).resolve().parents[1] / "src" / "dmbot"

# Files that write 2000 for something that is not a message's length, and why.
NOT_A_MESSAGE = {
    "ai.py": "the length of an error body kept for the log",
    "audio/segmenter.py": "milliseconds",
    "devtools/stt_bakeoff/report.py": "bootstrap rounds",
    "dm_screen/adventure_card.py": "characters per page of a book, for 'about N pages'",
    "memory/adventure.py": "characters per chunk of a book",
    "memory/name_documents.py": "characters per page, for a message about pages",
    "memory/name_list.py": "lines in a name list",
    "rules/homebrew.py": "the longest homebrew text stored",
    "rules/house_file.py": "the longest line of a house-rules file",
    "rules/house_file_link.py": "the longest link",
    "rules/rule_sets.py": "the longest link",
    "rules/site_urls.py": "the longest address",
    "ui/house_file.py": "a form field's max_length (4,000 is the form limit)",
    "ui/name_lists.py": "the longest link in a form field",
    "ui/rule_lookup.py": "entries kept in a map",
    "web/feedback.py": "the longest feedback text",
    "web/transcripts.py": "lines per page of a transcript read",
}


def literals_of_2000(source: str) -> list[int]:
    return [
        n.lineno
        for n in ast.walk(ast.parse(source))
        if isinstance(n, ast.Constant) and type(n.value) is int and n.value == 2000
    ]


def names_of_25() -> list[str]:
    return ["N" * 100 for _ in range(25)]


class MessageLimitTest(unittest.TestCase):
    def test_no_module_writes_its_own_message_limit(self) -> None:
        bad = []
        for path in SRC.rglob("*.py"):
            rel = path.relative_to(SRC).as_posix()
            if rel == "discord_limits.py" or rel in NOT_A_MESSAGE:
                continue
            bad += [f"{rel}:{line}" for line in literals_of_2000(path.read_text())]
        self.assertEqual(bad, [], "use dmbot.discord_limits.MESSAGE_MAX / fit_message")

    def test_the_allow_list_has_no_stale_entries(self) -> None:
        for rel in NOT_A_MESSAGE:
            self.assertTrue(literals_of_2000((SRC / rel).read_text()), f"{rel} no longer needs it")

    def test_the_check_sees_a_copy(self) -> None:
        self.assertEqual(literals_of_2000("LIMIT = 2_000\nx = 1999"), [1])

    def test_guide_text_stays_inside_one_message(self) -> None:
        text = guide._fit([f"{n}. " + "**" + "x" * 100 + "**" for n in range(25)])
        self.assertLessEqual(len(text), MESSAGE_MAX)
        blocks = guide._fit_blocks(["**" + "y" * 300 + "**" for _ in range(25)])
        self.assertLessEqual(len(blocks), MESSAGE_MAX)

    def test_story_watch_message_stays_inside_one_message(self) -> None:
        line_pieces = names_of_25()
        clash = memory_story_watch.Clash(
            id="d" * 32,
            book_name=line_pieces[0],
            book_needs=line_pieces[1],
            need=line_pieces[2],
            chapter=line_pieces[3],
            table_name=line_pieces[4],
            condition="x" * 100,
            session_started_at=None,
        )
        found = memory_story_watch.Found(shown=(clash,) * 25, more=40)
        self.assertLessEqual(len(story_watch.message_text(found, {})), MESSAGE_MAX)
