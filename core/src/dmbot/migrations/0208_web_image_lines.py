"""0208_web_image_lines

The website tags a picture line (#2507).
"""

from __future__ import annotations

from dmbot.migrations._shared import (
    WEB_TRANSCRIPT_PAGES,
)

WEB_IMAGE_LINES = """
    -- The website's transcript lines say which line describes a picture (#2507, after #2450): the
    -- reading page and the downloads tag it "(image)", as the bot's own file does, never "(typed)".
    -- The door is the one WEB_TRANSCRIPT_PAGES made, with one more key on each line. No rows
    -- change.
"""

_lines_start = WEB_TRANSCRIPT_PAGES.index("    CREATE FUNCTION dmbot_web_transcript_lines(")

_lines_end = WEB_TRANSCRIPT_PAGES.index("    $fn$;", _lines_start) + len("    $fn$;")

_lines_door = WEB_TRANSCRIPT_PAGES[_lines_start:_lines_end].replace(
    "CREATE FUNCTION dmbot_web_transcript_lines(",
    "CREATE OR REPLACE FUNCTION dmbot_web_transcript_lines(",
    1,
)

assert _lines_door.count("'typed', p.typed)") == 1

WEB_IMAGE_LINES += (
    _lines_door.replace("'typed', p.typed)", "'typed', p.typed, 'image', p.image)")
    + """
    -- (a redefined function loses its pinned search path)
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_transcript_lines(BIGINT, TEXT, TEXT, BIGINT, BIGINT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_transcript_lines(BIGINT, TEXT, TEXT, BIGINT, BIGINT)
        FROM PUBLIC;
"""
)

SQL = WEB_IMAGE_LINES
