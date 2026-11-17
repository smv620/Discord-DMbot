"""A sample custom id for any DynamicItem, made by reading its `template` pattern (#4533).

The registered buttons are many and their ids differ (`dm:peek:<campaign>`, numbers, 32-letter
campaign ids ...). To press each one in a test, an id is built from its own pattern: a literal
stays, a class takes its first character, a number is 1, a repeat is as short as it may be (or 1),
a choice takes its first branch. The result must match the pattern, which the caller checks.
"""

from __future__ import annotations

import importlib
import re
from typing import Any

_PARSER: Any = importlib.import_module("re._parser")
_CONSTANTS: Any = importlib.import_module("re._constants")


def sample(pattern: re.Pattern[str]) -> str:
    """One string the pattern matches (not every one: a handful of shapes are read)."""
    return _make(_PARSER.parse(pattern.pattern))


def _make(parsed: Any) -> str:
    c = _CONSTANTS
    out: list[str] = []
    for op, arg in parsed:
        if op == c.LITERAL:
            out.append(chr(arg))
        elif op == c.SUBPATTERN:
            out.append(_make(arg[3]))
        elif op == c.IN:
            out.append(_one_of(arg))
        elif op in (c.MAX_REPEAT, c.MIN_REPEAT):
            low, high, body = arg
            times = max(low, 1) if high else low
            out.append("".join(_make(body) for _ in range(times)))
        elif op == c.BRANCH:
            out.append(_make(arg[1][0]))
        elif op == c.CATEGORY:
            out.append("1" if arg == c.CATEGORY_DIGIT else "a")
        elif op == c.ANY:
            out.append("a")
        elif op != c.AT:  # (an anchor adds no character)
            raise ValueError(f"no sample for regex part {op}")
    return "".join(out)


def _one_of(members: Any) -> str:
    c = _CONSTANTS
    for op, value in members:
        if op == c.LITERAL:
            return chr(value)
        if op == c.RANGE:
            return chr(value[0])
        if op == c.CATEGORY:
            return "1" if value == c.CATEGORY_DIGIT else "a"
    raise ValueError("an empty character class")
