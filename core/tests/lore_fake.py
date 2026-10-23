"""A fake AI for the two-pass lore read (#2454): pass 1 (no schema) answers with a plain list, pass
2 (a schema) with JSON shaped only from the lines it was given, as the real shaping pass is told
to. Built from the JSON answer the older single-pass tests used, so they keep their fixtures."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

from dmbot.ai import Reply

# What the plain list says for each connection word the fixtures use.
WORDS = {
    "works for": "works for",
    "family": "is related to",
    "in": "is in",
    "owns": "owns",
    "enemy": "is an enemy of",
    "member": "is a member of",
    "friend": "is a friend of",
    "knows": "knows",
}


def plain_list(answer: dict[str, Any]) -> str:
    """The pass-1 list for a JSON answer in the old single-pass shape."""
    names = []
    for n in answer.get("names", []):
        parts = [f"- {n['name']}", n["kind"]]
        if n.get("also"):
            parts.append("other names: " + ", ".join(n["also"]))
        if n.get("secret"):
            parts.append("secret: " + ", ".join(n["secret"]))
        if n.get("quote"):
            parts.append(f'quote: "{n["quote"]}"')
        names.append(" | ".join(parts))
    links = [
        f'- {c["a"]} — {WORDS.get(c["how"], c["how"])} — {c["b"]} | quote: "{c["quote"]}"'
        for c in answer.get("connections", [])
    ]
    return "NAMES\n" + "\n".join(names) + "\n\nRELATIONSHIPS\n" + "\n".join(links)


def shaped(answer: dict[str, Any], listed: str) -> str:
    """The JSON for the entries of `answer` that the list text holds: names by their line, a
    connection by both ends. Whatever the list does not hold is not shaped."""
    return json.dumps(
        {
            "names": [n for n in answer.get("names", []) if f"- {n['name']} |" in listed],
            "connections": [
                {"words": "", **c}
                for c in answer.get("connections", [])
                if f"- {c['a']} —" in listed and f"— {c['b']} |" in listed
            ],
        }
    )


class LoreAI(SimpleNamespace):
    complete: AsyncMock
    answer: dict[str, Any]
    extra: dict[str, Any]
    cut_first: int
    reads: list[Any]


def lore_ai(answer: str | dict[str, Any], *, invented: dict[str, Any] | None = None) -> LoreAI:
    """`answer`: the JSON (text or dict) the older tests gave. `invented`: JSON entries the
    shaping pass adds that the list never said (they must be dropped by the checks)."""
    ai = LoreAI(complete=AsyncMock(), answer={}, extra=invented or {}, cut_first=0, reads=[])
    set_answer(ai, answer)

    async def complete(
        system: str, text: str, *, max_tokens: int = 8000, schema: Any = None
    ) -> Reply:
        if schema is None:  # pass 1
            if ai.reads:
                return ai.reads.pop(0)  # type: ignore[no-any-return]
            return Reply(plain_list(ai.answer), False)
        body = json.loads(shaped(ai.answer, text))
        for key in ("names", "connections"):
            body[key] += ai.extra.get(key, [])
        return Reply(json.dumps(body), False)

    ai.complete.side_effect = complete
    return ai


def set_answer(ai: LoreAI, answer: str | dict[str, Any]) -> None:
    ai.answer = json.loads(answer) if isinstance(answer, str) else answer
