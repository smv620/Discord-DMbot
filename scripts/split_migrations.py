#!/usr/bin/env python3
"""Split `core/src/dmbot/schema.py` into one file per migration (#2127).

Reads the monolithic schema.py and writes `core/src/dmbot/migrations/NNNN_name.py` (one per entry
of `MIGRATIONS`), a `_shared.py` for what more than one migration needs, and a slim `schema.py`.
Run it from the repository root on the latest `development` right before the merge, so that
migrations that landed meanwhile are carried along without hand edits:

    python scripts/split_migrations.py            # capture the fixture, then split
    python scripts/split_migrations.py --check    # only report what it would do

The split is a pure move of source text. Every statement that builds a migration's SQL goes to the
migration that needs it (or to `_shared.py` when two or more do); each module's `SQL` is the same
expression the old tuple held. Before it writes anything it captures what the old module evaluates
to (names, a SHA-256 of each migration's SQL, the isolated, user-isolated and routing tables, the
website role's functions) in `core/tests/data/migrations_before.json`; `tests/test_migration_files.py`
holds the built package to that file. The script is a one-time tool: once the split has merged,
new migrations are written as files by hand.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.util
import json
import re
import sys
import textwrap
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = ROOT / "core" / "src" / "dmbot" / "schema.py"
PACKAGE = ROOT / "core" / "src" / "dmbot" / "migrations"
FIXTURE = ROOT / "core" / "tests" / "data" / "migrations_before.json"

# The registrations that move into each migration's own file: the tuples in schema.py, and the
# attribute each module declares.
REGISTRATIONS = ("ISOLATED_TABLES", "USER_ISOLATED_TABLES", "ROUTING_TABLES")
FUNCTIONS = "WEB_ROLE_FUNCTIONS"
SPECIAL = {"MIGRATIONS", FUNCTIONS, *REGISTRATIONS}

# A table or function whose creating migration the SQL search cannot find (name -> migration).
MANUAL_OWNER: dict[str, str] = {}


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def evaluate(path: Path) -> Any:
    """The old schema.py as a module (it is plain data and string building)."""
    spec = importlib.util.spec_from_file_location("old_schema", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["old_schema"] = module
    spec.loader.exec_module(module)
    return module


def capture(old: Any) -> dict[str, Any]:
    return {
        "names": [n for n, _ in old.MIGRATIONS],
        "sha256": {n: sha(s) for n, s in old.MIGRATIONS},
        "isolated": sorted(old.ISOLATED_TABLES),
        "user_isolated": sorted(old.USER_ISOLATED_TABLES),
        "routing": sorted(old.ROUTING_TABLES),
        "web_role_functions": sorted([list(f) for f in old.WEB_ROLE_FUNCTIONS]),
    }


class Node:
    """One top-level statement of the old schema.py."""

    def __init__(self, index: int, stmt: ast.stmt, prefix: str) -> None:
        self.index = index
        self.stmt = stmt
        self.prefix = prefix  # the comments and blank lines above it, which travel with it
        self.res: dict[str, int] = {}  # a name it reads -> the statement that bound it last
        self.loads: set[str] = set()
        self.stores: set[str] = set()  # names this statement (re)binds for later statements
        self.deps: set[int] = set()
        self.owners: set[str] = set()


def names_in(node: ast.AST) -> set[str]:
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}


def assigned(stmt: ast.stmt) -> set[str]:
    if isinstance(stmt, ast.Assign):
        out: set[str] = set()
        for t in stmt.targets:
            out |= names_in(t)
        return out
    if isinstance(stmt, (ast.AnnAssign, ast.AugAssign)):
        return names_in(stmt.target)
    return set()


def analyse(stmt: ast.stmt) -> tuple[set[str], set[str]]:
    loads: set[str] = set()
    stores: set[str] = set()
    if isinstance(stmt, ast.Assign):
        stores |= assigned(stmt)
        loads |= names_in(stmt.value)
    elif isinstance(stmt, ast.AnnAssign):
        stores |= assigned(stmt)
        if stmt.value is not None:
            loads |= names_in(stmt.value)
    elif isinstance(stmt, ast.AugAssign):
        stores |= assigned(stmt)
        loads |= names_in(stmt.target) | names_in(stmt.value)
    elif isinstance(stmt, ast.FunctionDef):
        stores.add(stmt.name)
        loads |= names_in(stmt)
    elif isinstance(stmt, ast.For):
        # `for _old, _new in (...): _door = _door.replace(...)`: rebinds what its body assigns.
        # The loop's own variables are temporaries nobody else reads.
        loop_vars = names_in(stmt.target)
        for n in ast.walk(stmt):
            if isinstance(n, ast.Name) and n.id not in loop_vars:
                if isinstance(n.ctx, ast.Store):
                    stores.add(n.id)
                loads.add(n.id)
    else:  # Assert, If (a guard), a bare expression: reads only
        loads |= names_in(stmt)
    return loads, stores


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="report only; write nothing")
    args = parser.parse_args()

    source = SCHEMA.read_text()
    lines = source.split("\n")
    tree = ast.parse(source)
    old = evaluate(SCHEMA)
    before = capture(old)

    names = before["names"]
    if names != sorted(names):
        print("MIGRATIONS is not in name order", file=sys.stderr)
        return 1
    numbers = [n[:4] for n in names]
    if len(set(numbers)) != len(numbers):
        dup = sorted({n for n in numbers if numbers.count(n) > 1})
        print(f"two migrations share a number: {dup}", file=sys.stderr)
        return 1

    # ---- the statements -------------------------------------------------------------------
    nodes: list[Node] = []
    migrations_stmt: ast.stmt | None = None
    prev_end = 0
    header_end = 0
    for stmt in tree.body:
        end = stmt.end_lineno or stmt.lineno
        if (
            isinstance(stmt, ast.Expr)
            and isinstance(stmt.value, ast.Constant)
            and isinstance(stmt.value.value, str)
            and stmt.lineno == 1
        ):
            prev_end = header_end = end
            continue
        if isinstance(stmt, ast.ImportFrom):
            prev_end = end
            continue
        # comments and blank lines between the previous statement and this one travel with it
        prefix = "\n".join(lines[prev_end : stmt.lineno - 1]).strip("\n")
        prev_end = end
        node = Node(len(nodes), stmt, prefix)
        node.loads, node.stores = analyse(stmt)
        nodes.append(node)
        if "MIGRATIONS" in assigned(stmt):
            migrations_stmt = stmt
    assert isinstance(migrations_stmt, (ast.Assign, ast.AnnAssign))
    assert isinstance(migrations_stmt.value, ast.Tuple)
    mig_node = next(n for n in nodes if n.stmt is migrations_stmt)

    # the entries: (name, SQL expression, comment)
    entries: list[tuple[str, ast.expr, str]] = []
    for elt in migrations_stmt.value.elts:
        assert isinstance(elt, ast.Tuple) and len(elt.elts) == 2
        name_node, expr = elt.elts
        assert isinstance(name_node, ast.Constant)
        # comment: on the entry's own lines, or the standalone comment lines just above it
        span = lines[elt.lineno - 1 : (elt.end_lineno or elt.lineno)]
        comment = ""
        for ln in reversed(span):
            m = re.search(r"#\s*(.*)$", ln)
            if m and not ln.strip().startswith('"'):
                comment = m.group(1).strip()
                break
        # (the comment at the end of the entry's last line: `),  # why`)
        tail = lines[(elt.end_lineno or elt.lineno) - 1]
        m = re.search(r"\)\s*,?\s*#\s*(.*)$", tail)
        if m:
            comment = m.group(1).strip()
        above: list[str] = []
        i = elt.lineno - 2
        while i >= 0 and lines[i].strip().startswith("#"):
            above.insert(0, lines[i].strip().lstrip("#").strip())
            i -= 1
        if above:
            comment = " ".join(above + ([comment] if comment else []))
        entries.append((str(name_node.value), expr, comment))

    # ---- dependencies ---------------------------------------------------------------------
    last_store: dict[str, int] = {}
    for node in nodes:
        if node is mig_node:
            break
        node.res = {n: last_store[n] for n in node.loads if n in last_store}
        node.deps = set(node.res.values())
        for n in node.stores:
            last_store[n] = node.index
    final_store = dict(last_store)

    special_idx = {n.index for n in nodes if assigned(n.stmt) & SPECIAL}

    def closure(roots: set[int]) -> set[int]:
        seen: set[int] = set()
        stack = list(roots)
        while stack:
            i = stack.pop()
            if i in seen:
                continue
            seen.add(i)
            stack.extend(nodes[i].deps)
        return seen

    guards = [
        n
        for n in nodes
        if not n.stores and n.deps and isinstance(n.stmt, (ast.Assert, ast.If, ast.Expr))
    ]
    closures: dict[str, set[int]] = {}
    for name, expr, _ in entries:
        roots = {final_store[n] for n in names_in(expr) if n in final_store}
        c = closure(roots)
        for g in guards:  # a check on what this migration builds runs with it
            if g.deps <= c:
                c.add(g.index)
        closures[name] = c

    for name, c in closures.items():
        for i in c:
            nodes[i].owners.add(name)

    # Anything left in schema.py that reads a name a migration builds makes that name shared.
    residual = [
        n for n in nodes if not n.owners and n is not mig_node and n.index not in special_idx
    ]
    for n in residual:
        for d in closure(n.deps):
            nodes[d].owners.add("<schema>")

    placement: dict[int, str] = {}
    for n in nodes:
        if n is mig_node or n.index in special_idx:
            continue
        if len(n.owners) == 1 and "<schema>" not in n.owners:
            placement[n.index] = next(iter(n.owners))
        elif n.owners:
            placement[n.index] = "<shared>"
        else:
            placement[n.index] = "<schema>"

    # A name the shared module binds more than once (a door built up in steps, each step needed
    # by a different migration) keeps its first name and numbers the later bindings, so a consumer
    # imports exactly the version it read in the old file.
    store_name: dict[tuple[int, str], str] = {}
    seen_count: dict[str, int] = defaultdict(int)
    for i, where in sorted(placement.items()):
        if where == "<shared>":
            for nm in sorted(nodes[i].stores):
                seen_count[nm] += 1
                store_name[(i, nm)] = nm if seen_count[nm] == 1 else f"{nm}__{seen_count[nm]}"
    clashes: dict[str, list[int]] = {}
    shared_idx = {i for i, w in placement.items() if w == "<shared>"}

    def visible_name(nm: str, res: dict[str, int]) -> str:
        """What a reader of `nm` (bound by statement res[nm]) calls it now."""
        idx = res.get(nm)
        if idx is not None and (idx, nm) in store_name:
            return store_name[(idx, nm)]
        return nm

    def render(node: Node, res_override: dict[str, int] | None = None) -> str:
        """The statement's source with shared names renamed to the version it read or writes."""
        stmt = node.stmt
        first = stmt.lineno - 1
        last = stmt.end_lineno or stmt.lineno
        res = res_override if res_override is not None else node.res
        edits: dict[int, list[tuple[int, int, str]]] = defaultdict(list)
        # A loop that builds a name up (`_door = _door.replace(...)`) reads its own last value, so
        # inside it the name is the new version throughout; it starts from the old one.
        carried: dict[str, str] = {}
        prologue: list[str] = []
        if isinstance(stmt, ast.For):
            for nm in sorted(node.stores & node.loads):
                new = store_name.get((node.index, nm), nm)
                if new != nm:
                    carried[nm] = new
                    prologue.append(f"{new} = {visible_name(nm, res)}")
        for n in ast.walk(stmt):
            if not isinstance(n, ast.Name):
                continue
            if n.id in carried:
                new = carried[n.id]
            elif isinstance(n.ctx, ast.Store):
                new = store_name.get((node.index, n.id), n.id)
            else:
                new = visible_name(n.id, res)
            if new != n.id:
                edits[n.lineno].append((n.col_offset, n.end_col_offset or 0, new))
        out_lines: list[str] = []
        for ln_no in range(first + 1, last + 1):
            raw = lines[ln_no - 1].encode()
            for c0, c1, new in sorted(edits.get(ln_no, []), reverse=True):
                raw = raw[:c0] + new.encode() + raw[c1:]
            out_lines.append(raw.decode())
        text = "\n".join([*prologue, *out_lines])
        if isinstance(stmt, ast.AugAssign) and isinstance(stmt.target, ast.Name):
            new = store_name.get((node.index, stmt.target.id), stmt.target.id)
            if new != stmt.target.id:  # `X += v` -> `X__2 = X + (v)`: the old version is read
                old_name = visible_name(stmt.target.id, res)
                text = (
                    re.sub(rf"^{re.escape(new)}\s*\+=\s*", f"{new} = {old_name} + (", text, count=1)
                    + ")"
                )
        return (node.prefix + "\n" + text).strip("\n") if node.prefix else text

    def reads(node_res: dict[str, int], loads: set[str], local: set[str]) -> set[str]:
        """The shared names (as exported) a statement or expression needs to import."""
        need: set[str] = set()
        for nm in loads:
            idx = node_res.get(nm)
            if idx is not None and (idx, nm) in store_name:
                need.add(store_name[(idx, nm)])
        return need - local

    # ---- who owns each registration ------------------------------------------------------
    sql_of = dict(old.MIGRATIONS)

    def creator(table: str) -> str | None:
        if table in MANUAL_OWNER:
            return MANUAL_OWNER[table]
        pat = re.compile(
            rf"CREATE\s+TABLE\s+(IF\s+NOT\s+EXISTS\s+)?(public\.)?{re.escape(table)}\b", re.I
        )
        for n in names:
            if pat.search(sql_of[n]):
                return n
        return None

    def fn_creator(fn: str) -> str | None:
        if fn in MANUAL_OWNER:
            return MANUAL_OWNER[fn]
        pat = re.compile(rf"CREATE\s+(OR\s+REPLACE\s+)?FUNCTION\s+{re.escape(fn)}\s*\(", re.I)
        for n in names:
            if pat.search(sql_of[n]):
                return n
        return None

    registrations: dict[str, dict[str, list[str]]] = {r: defaultdict(list) for r in REGISTRATIONS}
    function_regs: dict[str, list[str]] = defaultdict(list)
    unowned: list[str] = []

    def elements(attr: str) -> list[tuple[ast.expr, str]]:
        stmt = next(n.stmt for n in nodes if attr in assigned(n.stmt))
        assert isinstance(stmt, (ast.Assign, ast.AnnAssign))
        value = stmt.value
        assert isinstance(value, ast.Tuple), attr
        out: list[tuple[ast.expr, str]] = []
        prev = value.lineno  # the line of the opening bracket
        for elt in value.elts:
            start = elt.lineno - 1
            j = start - 1
            comments: list[str] = []
            while j >= prev and lines[j].strip().startswith("#"):
                comments.insert(0, lines[j].strip())
                j -= 1
            body = ast.get_source_segment(source, elt) or ""
            # a comment after the element on its last line (`"x",  # why`)
            rest = lines[(elt.end_lineno or elt.lineno) - 1].encode()[(elt.end_col_offset or 0) :]
            m = re.match(rb"\s*,?\s*(#.*)$", rest)
            if m:
                body += "  " + m.group(1).decode()
            out.append((elt, "\n".join([*comments, body])))
            prev = elt.end_lineno or prev
        return out

    def as_item(text: str) -> str:
        """An element's source (with the comments above it) as one line-group of a tuple."""
        text = text.rstrip()
        if not text.rstrip().endswith(","):
            # a trailing comment may follow the element: put the comma before it
            last = text.split("\n")[-1]
            m = re.match(r"^(.*?)(\s+#.*)$", last)
            if m and not last.strip().startswith("#"):
                text = "\n".join([*text.split("\n")[:-1], m.group(1) + "," + m.group(2)])
            else:
                text += ","
        return "\n".join(
            ("    " + ln.strip()) if ln.strip().startswith("#") else ln for ln in text.split("\n")
        )

    for attr in REGISTRATIONS:
        flat: list[tuple[str, str]] = []  # (table, source text)
        for elt, text in elements(attr):
            if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                flat.append((elt.value, text))
            elif isinstance(elt, ast.Starred) and isinstance(elt.value, ast.Name):
                # `*_MEMORY_TABLES`: spread a tuple of plain strings defined earlier
                for table in getattr(old, elt.value.id):
                    flat.append((table, f'"{table}"'))
            else:
                print(f"{attr}: an element that is not a plain string: {text}", file=sys.stderr)
                return 1
        for table, text in flat:
            owner = creator(table)
            if owner is None:
                unowned.append(f"{attr}: {table}")
                continue
            registrations[attr][owner].append(text)
    for elt, text in elements(FUNCTIONS):
        assert isinstance(elt, ast.Tuple) and isinstance(elt.elts[0], ast.Constant)
        fn = str(elt.elts[0].value)
        owner = fn_creator(fn)
        if owner is None:
            unowned.append(f"{FUNCTIONS}: {fn}")
            continue
        function_regs[owner].append(text)

    shared_count = sum(1 for w in placement.values() if w == "<shared>")
    stay = sum(1 for w in placement.values() if w == "<schema>")
    print(f"{len(names)} migrations; {shared_count} shared statements; {stay} stay in schema.py")
    if unowned:
        print(
            "registrations with no creating migration found (add to MANUAL_OWNER):", file=sys.stderr
        )
        for u in unowned:
            print("  " + u, file=sys.stderr)
    if clashes or unowned:
        return 1
    if args.check:
        return 0

    # ---- write ---------------------------------------------------------------------------
    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE.write_text(json.dumps(before, indent=1, sort_keys=True) + "\n")
    PACKAGE.mkdir(parents=True, exist_ok=True)
    for old_file in PACKAGE.glob("[0-9][0-9][0-9][0-9]_*.py"):
        old_file.unlink()

    def import_block(wanted: set[str], reexport: bool = False) -> str:
        """The imports from `_shared`; schema.py re-exports its (`X as X`) for the type checker."""
        if not wanted:
            return ""
        item = (lambda n: f"{n} as {n}") if reexport else (lambda n: n)
        return (
            "from dmbot.migrations._shared import (\n"
            + "".join(f"    {item(n)},\n" for n in sorted(wanted))
            + ")\n"
        )

    shared_nodes = [nodes[i] for i, w in sorted(placement.items()) if w == "<shared>"]
    (PACKAGE / "_shared.py").write_text(
        '"""What more than one migration builds its SQL from (#2127): the helper functions, the pieces\n'
        "that several migrations share, and the website role's name. Moved unchanged from\n"
        'schema.py by scripts/split_migrations.py."""\n\nfrom __future__ import annotations\n\n'
        + "\n\n".join(render(n) for n in shared_nodes)
        + "\n"
    )

    for name, expr, comment in entries:
        local = [nodes[i] for i, w in sorted(placement.items()) if w == name]
        local_names: set[str] = set()
        for n in local:
            local_names |= n.stores
        final_res = {nm: final_store[nm] for nm in names_in(expr) if nm in final_store}
        wanted: set[str] = reads(final_res, names_in(expr), local_names)
        for n in local:
            wanted |= reads(n.res, n.loads, local_names)
        title = comment.replace('"""', "'''").replace("\\", "\\\\") if comment else ""
        if title:
            wrapped = textwrap.fill(title[0].upper() + title[1:] + ".", width=96)
            doc = f'"""{name}\n\n{wrapped}\n"""'
        else:
            doc = f'"""{name}."""'
        parts = [doc, "", "from __future__ import annotations", ""]
        imp = import_block(wanted)
        if imp:
            parts.append(imp)
        for n in local:
            parts.append(render(n) + "\n")
        sql_src = ast.get_source_segment(source, expr) or ""
        # the SQL expression reads the final versions; rename any shared one it uses
        for nm in sorted(names_in(expr), key=len, reverse=True):
            vis = visible_name(nm, final_res)
            if vis != nm:
                sql_src = re.sub(rf"\b{re.escape(nm)}\b", vis, sql_src)
        parts.append(f"SQL = {sql_src}")
        for attr in REGISTRATIONS:
            if registrations[attr].get(name):
                items = "\n".join(as_item(t) for t in registrations[attr][name])
                parts.append(f"\n{attr}: tuple[str, ...] = (\n{items}\n)")
        if function_regs.get(name):
            items = "\n".join(as_item(t) for t in function_regs[name])
            parts.append(f"\n{FUNCTIONS}: tuple[tuple[str, str], ...] = (\n{items}\n)")
        (PACKAGE / f"{name}.py").write_text("\n".join(parts) + "\n")

    # the slim schema.py: the header, the residual statements in order, and the built lists
    out = ["\n".join(lines[:header_end]), "", "from __future__ import annotations", ""]
    resid = [n for n in nodes if placement.get(n.index) == "<schema>"]
    resid_stores: set[str] = set()
    for n in resid:
        resid_stores |= n.stores
    need: set[str] = set()
    for n in resid:
        need |= reads(n.res, n.loads, resid_stores)
    imp = import_block(need, reexport=True)
    out.append("from dmbot import migrations\n" + imp)
    built = {
        "MIGRATIONS": "MIGRATIONS: tuple[Migration, ...] = migrations.MIGRATIONS",
        FUNCTIONS: f"{FUNCTIONS}: tuple[tuple[str, str], ...] = migrations.{FUNCTIONS}",
        **{r: f"{r}: tuple[str, ...] = migrations.{r}" for r in REGISTRATIONS},
    }
    for n in nodes:
        if n is mig_node or n.index in special_idx:
            key = next(k for k in built if k in assigned(n.stmt))
            lead: list[str] = []  # the comments that led into the statement, not its data
            for ln in n.prefix.split("\n"):
                if ln.strip().startswith("#") or not ln.strip():
                    lead.append(ln)
            out.append("\n".join(lead).strip("\n") + ("\n" if lead else "") + built[key] + "\n")
        elif placement.get(n.index) == "<schema>":
            out.append(render(n) + "\n")
    out.append(
        "\n\ndef __getattr__(name: str) -> Any:\n"
        '    """The names the old schema.py defined, now in the migration files: tests and code that\n'
        '    import them from here keep working."""\n'
        "    return migrations.moved(name)\n"
    )
    text = "\n".join(out)
    text = text.replace(
        "from dmbot import migrations\n",
        "from typing import Any\n\nfrom dmbot import migrations\n",
        1,
    )
    SCHEMA.write_text(text)
    print(f"wrote {len(entries)} migration files, _shared.py and a slim schema.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
