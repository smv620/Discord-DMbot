"""Which build of DMbot made a row (#3739): the commit stamped on what DMbot generates.

The owner's rule: a fault found later must be pinned to a version, so an old row from a build that
is already fixed is not mistaken for a new bug. The value is the commit the running image was built
from (`GIT_COMMIT`, the same one a backup writes as `dmbot_commit`), read once at start-up.

**What the stamp means, and what it does not.** It says which *build wrote the row*, never "DMbot
invented this": a row a DM adds by hand in Discord is still written by some build of the code, and
the row's own `source` says who asked for it. Rows from before versions were recorded have NULL,
and readers say "made before versions were recorded".

**How it is attached** (so no writer and no call site has to remember): `Database` opens every
transaction with `dmbot.commit`, and the trigger `dmbot_stamp_commit()` (migration 0378) copies it
into `made_by_commit`. On INSERT the row gets the running commit. On UPDATE the stored stamp is
kept, so a DM's own edit never changes it, unless the writer is DMbot itself rewriting its own work
(`dmbot.rewrites`, opened by `Database.guild(..., by_dmbot=True)`; story memory opens it for every
write whose source is not the DM). A restore or an Undo opens `dmbot.restoring`, and the trigger
then keeps exactly the stamp the row was written with, NULL included: an old row is never stamped
with the build that restored it. A writer that forgets to opt in keeps the older stamp, which never
claims a version made something it did not.
"""

from __future__ import annotations

import os
import re

_COMMIT = re.compile(r"[0-9a-f]{40}")
# Schema-side pattern, shared by every migration's CHECK so the column means one thing everywhere.
COMMIT_CHECK = "~ '^[0-9a-f]{40}$'"
NOT_STAMPED = "made before versions were recorded"


def running_commit() -> str | None:
    """The commit this build was made from (the full 40 characters), or None when the image has
    none (`GIT_COMMIT` unset or malformed): the stamp is then NULL, never the word "unknown"."""
    value = (os.environ.get("GIT_COMMIT") or "").strip().casefold()
    return value if _COMMIT.fullmatch(value) else None


def valid(value: object) -> bool:
    """A stamp as it may arrive in an untrusted backup: None or 40 lower-case hex characters."""
    return value is None or (isinstance(value, str) and _COMMIT.fullmatch(value) is not None)


def short(value: str | None) -> str:
    """What the website's details show: "made by DMbot version abc1234", or the old-row words."""
    return f"made by DMbot version {value[:7]}" if value else NOT_STAMPED
