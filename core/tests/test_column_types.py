"""A column that holds a Discord id, a millisecond time or a time of any kind is 64-bit (#4060).

Discord ids (snowflakes) are about 19 digits and a time in milliseconds passes 2^31 within weeks of
1970: a 32-bit INTEGER column overflows on either. The rule is by name: a column that ends `_id`,
`_ids`, `_ms`, `_at` or `_by`, or is called `guild`, `user` or `speaker_ids`, is BIGINT or BIGINT[].
Only an id or a who (`_id`, `_ids`, `_by`) may be TEXT or TEXT[]: a campaign's or an entry's id is a
32-character hex string. Anything else must be on the written list below, with its reason, so a new
table with an INTEGER id, or a time kept as text, fails CI.
"""

from __future__ import annotations

import re

from tests.pg import DatabaseTest

GUILD = 1  # (any server: the test only reads the catalog)

NAMED = re.compile(r"(_id|_ids|_ms|_at|_by)$|^(guild|user|speaker_ids)$")

# (table, column): why a 32-bit value is right. Each reason says what the number is.
EXCEPTIONS: dict[tuple[str, str], str] = {
    (
        "transcript_lines",
        "duration_ms",
    ): "how long one spoken line lasted: seconds, never past 2^31 ms",
    ("schema_migrations", "applied_at"): "a timestamptz, not a number: it cannot overflow",
}


class ColumnTypes(DatabaseTest):
    async def columns(self) -> list[tuple[str, str, str, str]]:
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                "SELECT c.table_name::text AS t, c.column_name::text AS c,"
                " c.data_type::text AS d, c.udt_name::text AS u"
                " FROM information_schema.columns c"
                " JOIN information_schema.tables t ON t.table_schema = c.table_schema"
                " AND t.table_name = c.table_name AND t.table_type = 'BASE TABLE'"
                " WHERE c.table_schema = current_schema() ORDER BY 1, 2"
            )
            return [(r["t"], r["c"], r["d"], r["u"]) for r in await cur.fetchall()]

    async def test_every_id_and_time_column_is_64_bit_or_text(self) -> None:
        wrong = []
        for table, column, data_type, udt in await self.columns():
            if not NAMED.search(column) or (table, column) in EXCEPTIONS:
                continue
            is_id = column.endswith(("_id", "_ids", "_by"))  # (who: an entry id kept as text)
            kinds = ("_int8", "_text") if is_id else ("_int8",)
            ok = (
                data_type == "bigint"
                or (is_id and data_type == "text")
                or (data_type == "ARRAY" and udt in kinds)
            )
            if not ok:
                wrong.append(f"{table}.{column} is {data_type if data_type != 'ARRAY' else udt}")
        self.assertEqual(wrong, [], "widen these to BIGINT, or list them with a reason")

    async def test_the_exception_list_has_no_stale_entry(self) -> None:
        present = {(t, c) for t, c, _, _ in await self.columns()}
        self.assertEqual(sorted(set(EXCEPTIONS) - present), [])

    async def test_a_backups_size_is_in_bytes_and_64_bit(self) -> None:
        types = {(t, c): d for t, c, d, _ in await self.columns()}
        self.assertEqual(types[("web_backups", "size")], "bigint")
        self.assertEqual(types[("web_backup_parts", "size")], "bigint")
