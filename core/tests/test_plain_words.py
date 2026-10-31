"""No technical words in what a person reads (#1850; CLAUDE.md, Hard rules, "Simple enough for a
child").

A DM or a player at the table reads DMbot's buttons, menus, cards and replies. Words like "API",
"database" or "token" belong in the code, not in a message ("remembers your NPCs between
sessions", not "knowledge graph"). Only the ux-critic agent checked this, and only on a PR where
someone ran it. This reads every string literal in the files that build what people see and fails
on a banned word, with the file and line.

What is read: all string literals (and the fixed parts of f-strings) in `ui/`, `dm_screen/`,
`consent_dm.py` and `features.py` (when it exists) (#1850), and in the other modules that write to
people: `sidebar/`, `recaps/`, `transcript/`, `rules/`, `timebot/`, `fightbot.py`, `bot.py`,
`retention_channels.py` and the consent and table-chat helpers (#1854). Skipped: docstrings, log
calls, exception messages that are never shown, dictionary keys, `template=` / `custom_id=`, SQL
(a string that starts with `SELECT`, `INSERT`, ... or is passed to `execute`, `fetch*` or
`sql.SQL`), patterns given to `re`, and the AI's own instructions (a `*_PROMPT` or `*_SYSTEM`
constant: those are for the model, not a person). The website has the same check in
`web/test/plain-words.test.ts`, from the same word list.
"""

from __future__ import annotations

import ast
import json
import re
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "dmbot"

# Whole words, any case, each with why a DM would be puzzled by it: one list for the bot and the
# website (docs/plain-words.json), so the two tests cannot drift apart.
BANNED: dict[str, str] = json.loads(
    (Path(__file__).resolve().parents[2] / "docs" / "plain-words.json").read_text()
)["banned"]
WORDS = re.compile(
    r"\b(" + "|".join(re.escape(w) for w in sorted(BANNED, key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)

# Real exceptions: (file, the exact phrase as the test prints it, why). Each must still be hit, so
# a fixed or deleted phrase leaves the list (the stale-entry check below).
ALLOWED: list[tuple[str, str, str]] = [
    (
        "recaps/pipeline.py",
        "model",
        "the name of an attribute read off the AI client to size the transcript (#2590): a "
        "lookup key, never shown to a person",
    ),
    (
        "rules/house_file_link.py",
        "api",
        "OneDrive's host name (api.onedrive.com), compared to name the site a link is on: an "
        "address to match, never shown to a person",
    ),
    (
        "recaps/draft.py",
        "json",
        "the instructions the recap's AI is given (what form to answer in, #2074): sent to the AI, "
        "never shown to a person",
    ),
    ("recaps/draft.py", "schema", "the same AI instructions: the shape of the answer asked for"),
    ("recaps/draft.py", "model", "the same AI instructions: 'a small model' cuts the text up"),
    (
        "ui/logic.py",
        "json",
        "the file name of a backup the DM downloads and uploads again (.dmbot.json.gz): a name to "
        "match, not a sentence to understand",
    ),
    (
        "recaps/audio/eleven.py",
        "api",
        "the speech company's web address, sent by the bot, never shown",
    ),
    ("recaps/audio/eleven.py", "json", "an HTTP header value, sent by the bot, never shown"),
    (
        "features.py",
        "model",
        'NOT_A_SWITCH\'s note on why a setting is not listed ("which voice model, a choice"): a '
        "reason for the developer who reads that table, never put in a message to a person",
    ),
    (
        "recaps/draft.py",
        "prompt",
        "the problems listed back to the AI so it can redo a draft ('give one music prompt for "
        "each section'): words for the model, not for a person",
    ),
    (
        "recaps/calls.py",
        "prompt",
        "a phrase to find in a problem the checks wrote ('exactly one music prompt'), to sort it "
        "under a fixed word in a test report: matched, never shown",
    ),
    (
        "recaps/pipeline.py",
        "json",
        "a problem listed back to the AI so it can answer again: for the model, not a person",
    ),
    (
        "transcript/export.py",
        "model",
        "the data lineage tag on the DM Sidebar's lines in the raw transcript (model=, prompt=): "
        "the owner's decision #933, for the people improving DMbot from it",
    ),
    (
        "transcript/export.py",
        "prompt",
        "the data lineage tag on the DM Sidebar lines in the raw transcript (owner, #933)",
    ),
    (
        "rules/homebrew.py",
        "null",
        "a piece of a database question (SQL) that does not begin with a SQL word, never shown",
    ),
    (
        "transcript/store.py",
        "null",
        "a piece of a database question (SQL) shared by two queries, never shown",
    ),
    (
        "recaps/notes.py",
        "null",
        "a piece of a database question (SQL) that does not begin with a SQL word, never shown",
    ),
    (
        "recaps/plot_talk_run.py",
        "null",
        "a piece of a database question (SQL) that does not begin with a SQL word, never shown",
    ),
    ("rules/index.py", "json", "a file pattern the bot searches for, never shown"),
    (
        "timebot/store.py",
        "token",
        "the name of a field kept in the clock's undo record, never shown",
    ),
]

LOG_METHODS = {"debug", "info", "warning", "error", "exception", "critical", "log"}
HIDDEN_KEYWORDS = {"template", "custom_id"}  # ids Discord keeps, never shown


SCOPE_DIRS = ("ui", "dm_screen", "sidebar", "recaps", "transcript", "rules", "timebot")
SCOPE_FILES = (
    "consent_dm.py",
    "features.py",  # (only once it exists)
    "fightbot.py",
    "bot.py",
    "retention_channels.py",
    "retention.py",
    "consent_notify.py",
    "consent_site.py",
    "consent_words.py",
    "table_chat.py",
    "hours.py",
    "fight_style.py",
    "owner_presence.py",
    "sheet_reread.py",
    "campaign_cap.py",
    "mail.py",
)
SQL_START = re.compile(r"^\s*(SELECT|INSERT|UPDATE|DELETE|WITH|CREATE|ALTER|DROP|BEGIN)\b", re.I)
SQL_CALLS = {"execute", "executemany", "fetch", "fetchone", "fetchall", "fetchval", "SQL"}
REGEX_CALLS = {"compile", "search", "match", "fullmatch", "sub", "subn", "split", "findall"}


def scoped_files() -> list[Path]:
    found: list[Path] = []
    for name in SCOPE_DIRS:
        found += sorted((SRC / name).rglob("*.py"))
    found += [SRC / name for name in SCOPE_FILES if (SRC / name).exists()]
    return found


def is_hidden_call(call: ast.Call) -> bool:
    """A log call, or an exception built to be raised (its words go to the log or the DM-side
    handler, not to a person; a user-facing refusal is a constant or a reply instead)."""
    func = call.func
    if isinstance(func, ast.Attribute) and func.attr in LOG_METHODS:
        return True
    called = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
    if called in SQL_CALLS:
        return True  # SQL
    if called in REGEX_CALLS and isinstance(func, ast.Attribute):
        return True  # a pattern given to `re` (a method `.sub` on a string is not caught here)
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
        if isinstance(node, ast.Dict):
            for key in node.keys:
                if key is not None:
                    skip.update(id(n) for n in ast.walk(key))  # a dictionary key
        if isinstance(node, ast.Subscript):
            skip.update(id(n) for n in ast.walk(node.slice))  # a dictionary key
        if isinstance(node, ast.Assign | ast.AnnAssign):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names = [t.id for t in targets if isinstance(t, ast.Name)]
            if any(n.endswith(("PROMPT", "SYSTEM")) for n in names) and node.value:
                skip.update(id(n) for n in ast.walk(node.value))  # the AI's instructions
    return [
        (n.lineno, n.value)
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant)
        and isinstance(n.value, str)
        and id(n) not in skip
        and not SQL_START.match(n.value)
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
