"""0157_name_answers

Answering a recap's names to check on its page (#2113).
"""

from __future__ import annotations

from dmbot.migrations._shared import (
    NAME_ANSWERS,
)

SQL = NAME_ANSWERS

ISOLATED_TABLES: tuple[str, ...] = (
    "recap_name_checks",
    "name_answers",
)

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    # The recap's names to check, answered on its page (#2113): the read door and the queue door.
    ("dmbot_recap_names_read", "dmbot_recap_names_read(BIGINT, TEXT, BIGINT)"),
    (
        "dmbot_name_answer_add",
        "dmbot_name_answer_add(BIGINT, TEXT, BIGINT, TEXT, TEXT, TEXT, TEXT, TEXT)",
    ),
)
