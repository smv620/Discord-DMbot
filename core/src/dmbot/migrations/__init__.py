"""The database's migrations, one file each (#2127): `NNNN_name.py` holds the SQL of migration
`NNNN_name` as `SQL`, and, when the migration adds them, the website-role functions, isolated
tables, user-isolated tables and routing tables it registers. A new migration is one new file and
touches nothing else.

The lists `schema.py` used to hold are built here from the files, in name order, so the order of
`MIGRATIONS` is the order the migrations ran. Two files that start with the same number fail at
import, so two branches cannot silently take the same number (claim it on #2475 first).
`_shared.py` holds what more than one migration builds its SQL from.

Applying a migration is `Database.migrate`'s job; this package only holds them.
"""

from __future__ import annotations

import importlib
import pkgutil
import re
from types import ModuleType
from typing import Any

_FILE = re.compile(r"^(\d{4})_[a-z0-9_]+$")


def check_names(names: list[str]) -> None:
    """Every file is `NNNN_name`, and no two share a number: two branches that took the same
    number fail here instead of one silently winning."""
    seen: dict[str, str] = {}
    for name in names:
        match = _FILE.match(name)
        if match is None:
            raise ImportError(f"migration file {name!r} is not named NNNN_name")
        number = match.group(1)
        if number in seen:
            raise ImportError(f"migrations {seen[number]!r} and {name!r} share the number {number}")
        seen[number] = name


def _load() -> list[ModuleType]:
    names = sorted(m.name for m in pkgutil.iter_modules(__path__) if not m.name.startswith("_"))
    check_names(names)
    return [importlib.import_module(f"{__name__}.{name}") for name in names]


_MODULES = _load()
_BY_NAME: dict[str, ModuleType] = {m.__name__.rsplit(".", 1)[1]: m for m in _MODULES}

MIGRATIONS: tuple[tuple[str, str], ...] = tuple(
    (name, module.SQL) for name, module in _BY_NAME.items()
)


def _collect(attr: str) -> tuple[Any, ...]:
    out: list[Any] = []
    for module in _MODULES:
        out.extend(getattr(module, attr, ()))
    return tuple(out)


ISOLATED_TABLES: tuple[str, ...] = _collect("ISOLATED_TABLES")
USER_ISOLATED_TABLES: tuple[str, ...] = _collect("USER_ISOLATED_TABLES")
ROUTING_TABLES: tuple[str, ...] = _collect("ROUTING_TABLES")
WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = _collect("WEB_ROLE_FUNCTIONS")


def moved(name: str) -> Any:
    """A name the old `schema.py` defined and a migration file now holds (`schema.WEB_ACCOUNTS`
    and the like), looked up in the shared module first and then in each file. Raises
    AttributeError when nothing has it, as a missing module attribute does."""
    shared = importlib.import_module(f"{__name__}._shared")
    if hasattr(shared, name):
        return getattr(shared, name)
    for module in _MODULES:
        if name in vars(module):
            return vars(module)[name]
    raise AttributeError(f"module 'dmbot.schema' has no attribute {name!r}")
