"""No technical words in what a person reads (#1850; CLAUDE.md, Hard rules, "Simple enough for a
child").

A DM or a player at the table reads DMbot's buttons, menus, cards and replies. Words like "API",
"database" or "token" belong in the code, not in a message ("remembers your NPCs between
sessions", not "knowledge graph"). Only the ux-critic agent checked this, and only on a PR where
someone ran it. This reads every string literal in the files that build what people see and fails
on a banned word, with the file and line.

What is read: all string literals (and the fixed parts of f-strings) in `ui/`, `dm_screen/`,
`consent_dm.py` and `features.py` (when it exists), except docstrings, log calls, exception
messages that are never shown, dictionary keys, `template=` / `custom_id=` and the AI's own
instructions (a `*_PROMPT` or `SYSTEM` constant: those are for the model, not a person). Text built
elsewhere (the sidebar, recaps, the website) is out of scope for now.
"""

from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "dmbot"

# Whole words, any case, each with why a DM would be puzzled by it. Short and real: add a word only
# if it would actually confuse someone at the table.
BANNED: dict[str, str] = {
    "api": "a programmer's word for how programs talk to each other",
    "database": 'say where it is kept in plain words ("saved", "remembered")',
    "token": 'a programmer\'s word for a secret code (say "code" or "link")',
    "tokens": "a programmer's word for pieces of text",
    "error code": "say what went wrong and what to do",
    "exception": "a programmer's word for a failure",
    "json": "a file format name",
    "null": 'a programmer\'s word for "nothing"',
    "payload": "a programmer's word for what a message carries",
    "endpoint": "a programmer's word for an address a program calls",
    "cache": "a programmer's word for a quick copy",
    "regex": "a programmer's word for a pattern",
    "webhook": "a programmer's word for a message another program sends",
    "oauth": 'the name of a sign-in method; say "sign in with Discord" or "Google"',
    "schema": "a programmer's word for how data is laid out",
    "migration": "a programmer's word for a data update",
    "embedding": "an AI term",
    "embeddings": "an AI term",
    "llm": 'an AI term; say "the AI"',
    "prompt": 'an AI term (say "question" or "instructions")',
    "model": 'an AI term (say "the AI")',
    "knowledge graph": 'the example in CLAUDE.md: say "remembers your NPCs between sessions"',
}
WORDS = re.compile(
    r"\b(" + "|".join(re.escape(w) for w in sorted(BANNED, key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)

# Real exceptions: (file, the exact phrase as the test prints it, why). Each must still be hit, so
# a fixed or deleted phrase leaves the list (the stale-entry check below).
ALLOWED: list[tuple[str, str, str]] = [
    (
        "ui/logic.py",
        "json",
        "the file name of a backup the DM downloads and uploads again (.dmbot.json.gz): a name to "
        "match, not a sentence to understand",
    ),
]

LOG_METHODS = {"debug", "info", "warning", "error", "exception", "critical", "log"}
HIDDEN_KEYWORDS = {"template", "custom_id"}  # ids Discord keeps, never shown


def scoped_files() -> list[Path]:
    found = sorted((SRC / "ui").glob("*.py")) + sorted((SRC / "dm_screen").glob("*.py"))
    found += [SRC / name for name in ("consent_dm.py", "features.py") if (SRC / name).exists()]
    return found


def is_hidden_call(call: ast.Call) -> bool:
    """A log call, or an exception built to be raised (its words go to the log or the DM-side
    handler, not to a person; a user-facing refusal is a constant or a reply instead)."""
    func = call.func
    if isinstance(func, ast.Attribute) and func.attr in LOG_METHODS:
        return True
    name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
    return name.endswith(("Error", "Exception")) and name != "CampaignError"


def user_strings(source: str) -> list[tuple[int, str]]:
    """(line, text) of each string literal that could reach a person."""
    tree = ast.parse(source)
    skip: set[int] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
            and node.body
            and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
        ):
            skip.add(id(node.body[0].value))  # a docstring
        if isinstance(node, ast.Call):
            if is_hidden_call(node):
                skip.update(id(n) for n in ast.walk(node))
            for keyword in node.keywords:
                if keyword.arg in HIDDEN_KEYWORDS:
                    skip.update(id(n) for n in ast.walk(keyword.value))
        if isinstance(node, ast.ClassDef):  # class Button(DynamicItem, template=r"...")
            for keyword in node.keywords:
                if keyword.arg in HIDDEN_KEYWORDS:
                    skip.update(id(n) for n in ast.walk(keyword.value))
        if isinstance(node, ast.Subscript):
            skip.update(id(n) for n in ast.walk(node.slice))  # a dictionary key
        if isinstance(node, ast.Assign | ast.AnnAssign):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names = [t.id for t in targets if isinstance(t, ast.Name)]
            if any(n.endswith("PROMPT") or n == "SYSTEM" for n in names) and node.value:
                skip.update(id(n) for n in ast.walk(node.value))  # the AI's instructions
    return [
        (n.lineno, n.value)
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in skip
    ]


def findings(source: str) -> list[tuple[int, str]]:
    return [
        (line, match.group(0).lower())
        for line, text in user_strings(source)
        for match in WORDS.finditer(text)
    ]


def allowed(rel: str, word: str) -> bool:
    return any(f == rel and w == word for f, w, _ in ALLOWED)


class PlainWordsTest(unittest.TestCase):
    def test_no_technical_words_in_what_people_read(self) -> None:
        bad = []
        for path in scoped_files():
            rel = path.relative_to(SRC).as_posix()
            for line, word in findings(path.read_text()):
                if not allowed(rel, word):
                    bad.append(
                        f'{rel}:{line}: "{word}" in user text: say it in plain words '
                        f"(CLAUDE.md, Hard rules). Why: {BANNED[word]}"
                    )
        self.assertEqual(bad, [])

    def test_the_allow_list_has_no_stale_entries(self) -> None:
        for rel, word, why in ALLOWED:
            hits = [w for _, w in findings((SRC / rel).read_text())]
            self.assertIn(word, hits, f"{rel} no longer has {word!r}: remove the entry")
            self.assertTrue(why)

    def test_the_scan_really_reads_the_screens(self) -> None:
        texts = [t for path in scoped_files() for _, t in user_strings(path.read_text())]
        self.assertGreater(len(texts), 1000)
        self.assertTrue(any("Peek behind the DM screen" in t for t in texts))

    def test_every_banned_word_has_a_reason(self) -> None:
        self.assertTrue(all(BANNED.values()))

    def test_a_banned_word_in_a_button_or_reply_is_seen(self) -> None:
        source = (
            'LABEL = "Open the database"\n'
            "async def go(interaction):\n"
            '    await interaction.response.send_message(f"Your {count} tokens")\n'
        )
        self.assertEqual(findings(source), [(1, "database"), (3, "tokens")])

    def test_whole_words_only_and_any_case(self) -> None:
        self.assertEqual(findings('X = "Your API key"'), [(1, "api")])
        self.assertEqual(findings('X = "A rapid start, a modelling class"'), [])

    def test_docstrings_logs_ids_keys_and_ai_instructions_are_left_alone(self) -> None:
        source = (
            '"""The database for the cache."""\n'
            "def go(x):\n"
            '    """A token."""\n'
            '    log.warning("The cache is not ready")\n'
            '    raise ValueError("bad token")\n'
            '    y = x["token"]\n'
            '    return Button(custom_id="dmbot:token:1")\n'
            'SYSTEM = "Write a prompt for the model"\n'
            'class B(Item, template=r"dmbot:(?P<token>x)"):\n'
            "    pass\n"
        )
        self.assertEqual(findings(source), [])

    def test_a_campaign_error_is_shown_to_the_dm_so_it_is_read(self) -> None:
        self.assertEqual(findings('raise CampaignError("Bad schema")'), [(1, "schema")])
