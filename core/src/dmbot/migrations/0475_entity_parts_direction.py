"""0475_entity_parts_direction

An entry's search text follows the DM's own direction for a connection (#4164, a follow-up to
#4154).
"""

from __future__ import annotations

# `dmbot_entity_parts` hands each connection's kind (`symmetric`) to `memory/entity_text.py`, which
# leaves a one-way connection off the other end's text. It never sent #4154's mark
# (`memory_relations.both_ways`, NULL for the kind's default), so a rivalry the DM made one way
# still read "is a rival of Cerric" on Wit's entry, and a `serves` the DM made two way never showed
# on the second entry: search and the AI disagreed with the card. The mark is now sent beside the
# kind, never folded into it, so the second entry is given the card's own words from its side ("is
# the boss of Mira", `link_words.back_words`), never the first entry's ("serves Mira", which would
# be the wrong way round). The function is read back as it is now (0426 changed it last), each
# anchor must be found exactly once, and it is made again with every setting it had (0469's way);
# a text that did not change stops the migration. No row changes. Text already made can lag until
# the next embedding run.
_SWAPS = (
    ("'other', k.other, 'outgoing', k.outgoing)",
     "'other', k.other, 'outgoing', k.outgoing, 'both_ways', k.both_ways)"),
    ("(r.subject_id = e.id) AS outgoing", "(r.subject_id = e.id) AS outgoing, r.both_ways"),
)  # fmt: skip


def _swaps() -> str:
    lines = []
    for old, new in _SWAPS:
        lines.append(
            f"""
        fresh := replace(body, $a${old}$a$, $b${new}$b$);
        IF (length(fresh) - length(body)) <> length($b${new}$b$) - length($a${old}$a$)
           OR position($a${old}$a$ IN body) = 0 THEN
            RAISE EXCEPTION 'dmbot_entity_parts: an anchor was not found exactly once';
        END IF;
        body := fresh;"""
        )
    return "".join(lines)


SQL = (
    r"""
    DO $$
    DECLARE
        def text;
        body text;
        fresh text;
    BEGIN
        def := pg_get_functiondef(
            'dmbot_entity_parts(bigint, text, text)'::regprocedure);
        body := def;"""
    + _swaps()
    + r"""
        EXECUTE body;
    END $$;
"""
)

ISOLATED_TABLES: tuple[str, ...] = ()

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = ()
