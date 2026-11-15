"""0507_thread_suggestion_parts

A story-thread suggestion holds the parts and branches the lore reader found with it (#4500,
quests slice 3; design `docs/design/quests.md` §9.1, §6a and §14 row 3).
"""

from __future__ import annotations

# One new column on `thread_suggestions` (0502), `extra`: a JSON object with two lists.
# - `reads`: the thread's parts as the reader checked them, each {kind, words, state, quote, who,
#   due, terms, limit_kind}. `kind` is one of `memory_thread_parts`' kinds (0504) and `who` an
#   entry id or null, never a free name (Supervisor's rule for `parts`, 0502).
# - `branches`: its branches, each {kind, name, aim, quote, state, parts}, with `parts` as the
#   suggestion's own [part word, entry id] pairs.
# While the suggestion waits they are DMbot's reads and nothing else: no story memory, no list and
# no count reads them (§9.6). ✅ Keep writes the lines the DM left on through the change log, in
# the keep's one change (`MemoryStore.keep_thread`), so one Undo takes them back with the thread.
# The row stays, marked kept, so each part's quote is still there as its evidence.
# An object, so a later slice can add a list without another migration; '{}' for a row from
# before, which keeps as it did. Nothing else changes and no existing row is written, so no
# row-level security opens.
SQL = r"""
    ALTER TABLE thread_suggestions
        ADD COLUMN extra JSONB NOT NULL DEFAULT '{}'::jsonb
            CHECK (jsonb_typeof(extra) = 'object');
    """
