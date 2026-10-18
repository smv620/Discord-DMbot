"""Every function that runs SQL for a campaign or a server must scope it by that id (#1877).

CLAUDE.md, Hard rules, "Campaign and server isolation": every query is scoped to one campaign and
never crosses servers. #1870 and #1873 proved today's stores one by one; this is the guard for
tomorrow's. A new `async def` under `dmbot` that takes `guild_id`, `campaign_id` or `campaign` and
runs SQL (itself, or through a function of its own module that does, one step deep) must USE that
parameter in a call that is not logging: passed to `execute`, `select` or `sql.SQL`, to a helper,
or put in a list or tuple that is (`args = [guild_id, campaign_id]` then `*args` counts, since the
value flows into the call). A parameter that is only checked with `require_dm`, only logged, or
not used at all fails, with `file:line: <function> takes campaign_id but never scopes its query by
it`.

What it does not prove: that the SQL text names the column, or that the right column is compared.
That is what the two-campaign tests (`test_isolation_pass*.py`) are for; this guard is the net for
the commonest slip, a parameter that was taken and forgotten. No database and no network.
"""

from __future__ import annotations

import ast
import unittest
from dataclasses import dataclass
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src" / "dmbot"

SCOPE_PARAMS = ("guild_id", "campaign_id", "campaign")
# Calls that run SQL or build it.
SQL_CALLS = {"execute", "executemany", "select", "fetchone", "fetchall", "copy", "SQL"}
# Names whose arguments are not a scoping of the query: logging and plain conversions.
LOGGING = {"log", "logger", "logging", "print"}
# `require_dm` is the DM check, which asks the campaign a question but filters nothing in the
# caller's own query: a function that passes its campaign only there has not scoped it.
NOT_SCOPING = {
    "require_dm",
    "_require_dm",
    "len",
    "str",
    "int",
    "repr",
    "isinstance",
    "set",
    "list",
    "tuple",
    "sorted",
    "bool",
    "dict",
    "max",
    "min",
    "getattr",
    "hasattr",
}

# Real exceptions: (path under src/dmbot, function, parameter) -> why. Each must still be flagged
# by the raw scan, so a function that is fixed or removed leaves the list (the stale check below).
# There are none today; add one only for a function that must take the id and cannot use it, and
# say whose isolation covers it (row-level security inside a connection the caller scoped, the
# shared-ruleset reads, the one-copy-per-address homebrew pages).
ALLOWED: dict[tuple[str, str, str], str] = {}


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    function: str
    param: str

    def text(self) -> str:
        return (
            f"{self.path}:{self.line}: {self.function} takes {self.param} but never scopes its"
            " query by it (CLAUDE.md, Campaign and server isolation)"
        )


def callee(call: ast.Call) -> str | None:
    f = call.func
    return f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", None)


def root_name(node: ast.expr) -> str | None:
    while isinstance(node, ast.Attribute):
        node = node.value
    return node.id if isinstance(node, ast.Name) else None


def runs_sql(node: ast.AST) -> bool:
    return any(isinstance(c, ast.Call) and callee(c) in SQL_CALLS for c in ast.walk(node))


def names_in(node: ast.AST) -> set[str]:
    return {x.id for x in ast.walk(node) if isinstance(x, ast.Name)}


def carriers(fn: ast.AST, param: str) -> set[str]:
    """The parameter and every local variable its value flows into (`args = [guild_id, ...]`,
    `values.append(campaign_id)`), found by repeating until nothing new joins."""
    found = {param}
    changed = True
    while changed:
        changed = False
        for n in ast.walk(fn):
            targets: list[ast.expr] = []
            value: ast.expr | None = None
            if isinstance(n, ast.Assign):
                targets, value = list(n.targets), n.value
            elif isinstance(n, ast.AugAssign | ast.AnnAssign) and n.value is not None:
                targets, value = [n.target], n.value
            elif (
                isinstance(n, ast.Call)
                and isinstance(n.func, ast.Attribute)
                and n.func.attr in {"append", "extend", "add", "update"}
                and isinstance(n.func.value, ast.Name)
            ):
                if any(names_in(a) & found for a in n.args) and n.func.value.id not in found:
                    found.add(n.func.value.id)
                    changed = True
                continue
            if value is not None and names_in(value) & found:
                for target in targets:
                    for x in ast.walk(target):
                        if isinstance(x, ast.Name) and x.id not in found:
                            found.add(x.id)
                            changed = True
    return found


def is_scoped(fn: ast.AST, param: str) -> bool:
    """Is the parameter (or a variable it flowed into) an argument of a call that is neither
    logging nor a plain conversion nor the DM check?"""
    found = carriers(fn, param)
    for n in ast.walk(fn):
        if not isinstance(n, ast.Call) or root_name(n.func) in LOGGING:
            continue
        if callee(n) in NOT_SCOPING:
            continue
        if any(names_in(a) & found for a in [*n.args, *(k.value for k in n.keywords)]):
            return True
    return False


def scan(source: str, path: str) -> tuple[int, list[Finding]]:
    """(how many functions were checked, the ones that take an id and never scope by it)."""
    tree = ast.parse(source)
    functions = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef)}
    sync = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    direct = {name for name, fn in {**sync, **functions}.items() if runs_sql(fn)}
    checked = 0
    findings: list[Finding] = []
    for n in ast.walk(tree):
        if not isinstance(n, ast.AsyncFunctionDef):
            continue
        params = {a.arg for a in n.args.args + n.args.kwonlyargs} & set(SCOPE_PARAMS)
        if not params:
            continue
        through_helper = any(
            isinstance(c, ast.Call) and callee(c) in direct and callee(c) != n.name
            for c in ast.walk(n)
        )
        if not (runs_sql(n) or through_helper):
            continue
        checked += 1
        findings += [
            Finding(path, n.lineno, n.name, p) for p in sorted(params) if not is_scoped(n, p)
        ]
    return checked, findings


def scan_tree() -> tuple[int, list[Finding]]:
    total = 0
    found: list[Finding] = []
    for file in sorted(SRC.rglob("*.py")):
        checked, findings = scan(file.read_text(), str(file.relative_to(SRC)))
        total += checked
        found += findings
    return total, found


def stale(allowed: dict[tuple[str, str, str], str], raw: list[Finding]) -> list[str]:
    """Allow-list entries the raw scan no longer flags (the function was fixed or removed)."""
    hit = {(f.path, f.function, f.param) for f in raw}
    return [
        f"{key} is allowed but no longer flagged: remove it" for key in allowed if key not in hit
    ]


class TheRealCode(unittest.TestCase):
    def test_every_function_that_takes_an_id_and_runs_sql_scopes_by_it(self) -> None:
        _, raw = scan_tree()
        loose = [f.text() for f in raw if (f.path, f.function, f.param) not in ALLOWED]
        self.assertEqual(loose, [])

    def test_the_scan_checks_a_real_number_of_functions(self) -> None:
        # A scan that quietly found nothing would pass the test above. Today it checks about 500.
        checked, _ = scan_tree()
        self.assertGreater(checked, 400)

    def test_the_allow_list_has_no_stale_entries(self) -> None:
        _, raw = scan_tree()
        self.assertEqual(stale(ALLOWED, raw), [])

    def test_taking_the_filter_out_of_a_real_store_method_is_caught(self) -> None:
        """The same slip, made in the real adventure store: `chunks` stops passing its campaign."""
        path = SRC / "memory" / "adventure.py"
        source = path.read_text()
        old = "(guild_id, campaign_id, document_id),\n            )\n            return [Chunk("
        self.assertEqual(source.count(old), 1)
        broken = source.replace(
            old, old.replace("guild_id, campaign_id, document_id", "guild_id, document_id")
        )
        _, before = scan(source, "memory/adventure.py")
        _, after = scan(broken, "memory/adventure.py")
        self.assertEqual([f for f in before if f.function == "chunks"], [])
        self.assertEqual(
            [(f.function, f.param) for f in after if f.function == "chunks"],
            [("chunks", "campaign_id")],
        )


STAND_INS = """
class Store:
    async def forgets_the_campaign(self, guild_id, campaign_id):
        async with self._db.guild(guild_id) as conn:
            await conn.execute("SELECT * FROM t WHERE guild_id = %s", (guild_id,))

    async def uses_both(self, guild_id, campaign_id):
        async with self._db.guild(guild_id) as conn:
            await conn.execute("SELECT * FROM t WHERE a = %s AND b = %s", (guild_id, campaign_id))

    async def uses_them_through_a_list(self, guild_id, campaign_id):
        args = [guild_id, campaign_id]
        async with self._db.guild(guild_id) as conn:
            await conn.execute("UPDATE t SET x = 1 WHERE a = %s AND b = %s", (*args,))

    async def only_checks_the_dm(self, guild_id, campaign_id, user_id):
        async with self._db.guild(guild_id) as conn:
            await require_dm(conn, guild_id, campaign_id, user_id)
            await conn.execute("SELECT * FROM t WHERE guild_id = %s", (guild_id,))

    async def only_logs_it(self, guild_id, campaign_id):
        log.info("reading %s", campaign_id)
        async with self._db.guild(guild_id) as conn:
            await conn.execute("SELECT * FROM t WHERE guild_id = %s", (guild_id,))

    async def hands_both_to_a_helper(self, guild_id, campaign_id):
        return await self._read(guild_id, campaign_id)

    async def _read(self, guild_id, campaign_id):
        async with self._db.guild(guild_id) as conn:
            await conn.execute("SELECT 1 WHERE %s AND %s", (guild_id, campaign_id))

    async def forgets_through_a_helper(self, guild_id, campaign_id):
        return await self._read_guild(guild_id)

    async def _read_guild(self, guild_id):
        async with self._db.guild(guild_id) as conn:
            await conn.execute("SELECT 1 WHERE %s", (guild_id,))

    async def runs_no_sql(self, guild_id, campaign_id):
        return guild_id

    def not_async(self, guild_id, campaign_id):
        return self.conn.execute("SELECT 1")
"""


class TheGuardCanFail(unittest.TestCase):
    """Planted stand-ins: a method that ignores its id is flagged, one that uses it is not."""

    def flagged(self) -> set[tuple[str, str]]:
        _, findings = scan(STAND_INS, "stand_in.py")
        return {(f.function, f.param) for f in findings}

    def test_a_method_that_ignores_its_campaign_is_flagged_and_one_that_uses_it_is_not(
        self,
    ) -> None:
        flagged = self.flagged()
        self.assertIn(("forgets_the_campaign", "campaign_id"), flagged)
        self.assertNotIn(("uses_both", "campaign_id"), flagged)
        self.assertNotIn(("uses_both", "guild_id"), flagged)

    def test_a_value_that_flows_into_the_query_through_a_list_counts(self) -> None:
        self.assertFalse({x for x in self.flagged() if x[0] == "uses_them_through_a_list"})

    def test_only_checking_the_dm_or_only_logging_is_not_scoping(self) -> None:
        flagged = self.flagged()
        self.assertIn(("only_checks_the_dm", "campaign_id"), flagged)
        self.assertIn(("only_logs_it", "campaign_id"), flagged)

    def test_a_helper_one_step_deep_is_followed(self) -> None:
        flagged = self.flagged()
        self.assertNotIn(("hands_both_to_a_helper", "campaign_id"), flagged)
        self.assertIn(("forgets_through_a_helper", "campaign_id"), flagged)

    def test_a_function_that_runs_no_sql_or_is_not_async_is_not_checked(self) -> None:
        names = {name for name, _ in self.flagged()}
        self.assertNotIn("runs_no_sql", names)
        self.assertNotIn("not_async", names)

    def test_the_message_names_the_place_the_function_and_the_rule(self) -> None:
        _, findings = scan(STAND_INS, "stand_in.py")
        (one,) = [f for f in findings if f.function == "forgets_the_campaign"]
        self.assertRegex(
            one.text(),
            r"^stand_in\.py:\d+: forgets_the_campaign takes campaign_id but never scopes its query"
            r" by it \(CLAUDE\.md, Campaign and server isolation\)$",
        )

    def test_a_stale_allow_list_entry_is_reported(self) -> None:
        _, raw = scan(STAND_INS, "stand_in.py")
        allowed = {
            ("stand_in.py", "forgets_the_campaign", "campaign_id"): "still flagged: fine",
            ("stand_in.py", "uses_both", "campaign_id"): "fixed since: stale",
        }
        report = stale(allowed, raw)
        self.assertEqual(len(report), 1)
        self.assertIn("uses_both", report[0])
