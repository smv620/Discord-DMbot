"""Where DMbot's knowledge of itself comes from (#1195): the plan and README the image was built
with, the website's Help page, and every slash command's name and
description and the DM screen's help card, read from the code itself so they cannot drift."""

from __future__ import annotations

import ast
import logging
import os
import re
from pathlib import Path

from dmbot.sidebar import knowledge
from dmbot.sidebar.knowledge import BUILT, Chunk

log = logging.getLogger(__name__)

PACKAGE = Path(__file__).resolve().parents[1]  # .../dmbot (in the image and in a checkout)
# The sentences of the Help page: each step's `text:` and the exported heading, intro and note.
_STRING = re.compile(
    r"(?:\btext:|export const \w+ =)\s*(?P<q>[\"'`])(?P<s>(?:\\.|(?!(?P=q)).)*)(?P=q)", re.S
)


def docs_root() -> Path | None:
    """The folder holding docs/PLAN.md: DMBOT_DOCS_DIR (the image sets it), else the checkout
    this code sits in. None when the documents were not shipped."""
    for candidate in (os.environ.get("DMBOT_DOCS_DIR"), str(PACKAGE.parents[2])):
        if candidate and (Path(candidate) / "docs" / "PLAN.md").is_file():
            return Path(candidate)
    return None


def collect() -> list[Chunk]:
    """Every chunk, or only the code's own (commands, help card) and the digest when the
    documents are missing."""
    chunks = command_chunks() + help_card_chunks()
    root = docs_root()
    if root is None:
        log.warning("DMbot's documents were not shipped: it answers about itself from the digest")
        return chunks
    for name in ("docs/PLAN.md", "README.md"):
        path = root / name
        if path.is_file():
            chunks += knowledge.chunk_markdown(name, path.read_text(encoding="utf-8"))
    chunks += help_page_chunks(root / "web" / "src" / "content" / "help.ts")
    return chunks


def help_page_chunks(path: Path) -> list[Chunk]:
    """The website's Help page: its sentences, as the page reads."""
    if not path.is_file():
        return []
    found = [m["s"] for m in _STRING.finditer(path.read_text("utf-8"))]
    text = " ".join(f.replace("\\'", "'").replace('\\"', '"') for f in found)
    text = knowledge.without_process_notes(re.sub(r"\$\{[^}]*\}", "", text))
    return [Chunk("help page", "The Help page", "", BUILT, " ".join(text.split()))] if text else []


def _text(node: ast.expr) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def command_chunks() -> list[Chunk]:
    """One chunk per slash command, from the decorators in the code."""
    groups: dict[str, str] = {}  # variable name -> the group's command name
    found: list[tuple[str, str, str]] = []  # (prefix variable or "", name, description)
    for path in sorted(PACKAGE.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
                kwargs = {k.arg: _text(k.value) for k in node.value.keywords if k.arg}
                func = ast.unparse(node.value.func)
                if func.endswith("app_commands.Group") and kwargs.get("name"):
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            groups[target.id] = kwargs["name"] or ""
            if isinstance(node, ast.AsyncFunctionDef):
                for deco in node.decorator_list:
                    if isinstance(deco, ast.Call) and isinstance(deco.func, ast.Attribute):
                        if deco.func.attr != "command":
                            continue
                        kwargs = {k.arg: _text(k.value) for k in deco.keywords if k.arg}
                        owner = ast.unparse(deco.func.value)
                        if kwargs.get("name") and kwargs.get("description"):
                            found.append((owner, kwargs["name"] or "", kwargs["description"] or ""))
    chunks = []
    for owner, name, description in sorted(set(found)):
        full = f"/{groups[owner]} {name}" if owner in groups else f"/{name}"
        chunks.append(Chunk("commands", full, "", BUILT, f"{full}: {description}."))
    return chunks


def _help_card_raw() -> str:
    """The help card's words as the code writes them (with its bold markers), or ""."""
    path = PACKAGE / "dm_screen" / "messages.py"
    if not path.is_file():
        return ""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        return ""
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "help_card":
            lines = [
                n.value
                for n in ast.walk(node)
                if isinstance(n, ast.Constant) and isinstance(n.value, str) and len(n.value) > 15
            ]
            return " ".join(lines)
    return ""


def help_card_chunks() -> list[Chunk]:
    """The DM screen's help card, from its function in the code."""
    text = " ".join(_help_card_raw().replace("**", "").split())
    return [Chunk("help card", "The DM screen help card", "", BUILT, text)] if text else []


def labels() -> list[str]:
    """Every button, menu and select label in the code (a literal, or a named constant), the
    help card's bold names: how people say what they press."""
    trees = []
    for path in sorted(PACKAGE.rglob("*.py")):
        try:
            trees.append(ast.parse(path.read_text(encoding="utf-8")))
        except (SyntaxError, UnicodeDecodeError):
            continue
    constants: dict[str, str] = {}  # NAME = "text" anywhere in the package
    for tree in trees:
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and (text := _text(node.value)):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        constants[target.id] = text
    found: set[str] = set()
    for tree in trees:
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                for keyword in node.keywords:
                    if keyword.arg != "label":
                        continue
                    value = keyword.value
                    if text := _text(value):
                        found.add(text)
                    elif isinstance(value, ast.Name) and value.id in constants:
                        found.add(constants[value.id])
                    elif isinstance(value, ast.Attribute) and value.attr in constants:
                        found.add(constants[value.attr])
    found.update(re.findall(r"\*\*([^*]{4,40})\*\*", _help_card_raw()))
    return sorted(found)
