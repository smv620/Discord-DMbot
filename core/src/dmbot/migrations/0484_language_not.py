"""0484_language_not

A language the DM says is wrong stays wrong (#4362; Supervisor's ruling, option A): the row stays,
with `given_by = 'not'`, so a later read of the same character and language finds the DM's no and
writes nothing. Every reader of `memory_story_languages` skips such a row
(`tests/test_story_language_readers.py` holds them to it). No row is changed.
"""

from __future__ import annotations

SQL = r"""
    ALTER TABLE memory_story_languages DROP CONSTRAINT memory_story_languages_given_by_check;
    ALTER TABLE memory_story_languages ADD CONSTRAINT memory_story_languages_given_by_check
        CHECK (given_by IN ('read', 'dm', 'not'));
    """

ISOLATED_TABLES: tuple[str, ...] = ()
