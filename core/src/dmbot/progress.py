"""The one shape for how far a job has got, shared by every wait the website shows (#2564).

A job (an import, a recap, a hand-over move) reports `stage` (a code, never prose), `done` of
`total` where that is known, when it began (`startedAt`), the last time it moved forward
(`updatedAt`), and a `state`: running, stalled, failed or done. A failed job says why in plain
words. "Stalled" is worked out when the job is read, from the time since `updatedAt` and that
stage's limit below, so the limits live here on the server and the website only shows `state`.
A job that has stalled can still move: its next step turns it back to running.

Counts, codes and fixed words only: nothing from a document or a transcript is ever put in.
Times are Unix seconds from the server's clock, and `serverNow` goes with them so the page never
trusts the visitor's clock.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Final

RUNNING, STALLED, FAILED, DONE = "running", "stalled", "failed", "done"

# Seconds without a step before a stage counts as stalled. None: the stage waits for a person (or
# is the end), so it can never stall. Each is a little over what one step of that stage may take.
STALL_AFTER_S: Final[Mapping[str, int | None]] = {
    "cleaning": 10 * 60,  # a chunk of lines, then the next (waiting to be taken counts too)
    "checking_names": 15 * 60,  # the name scan's own retake window
    "finding_names": 15 * 60,
    "waiting_for_names": None,  # waiting for the DM's answers
    "starting_recap": 10 * 60,
    "writing_recap": 30 * 60,
    "recap_not_written": 10 * 60,
    "recap_ready": None,
    "recap_failed": None,
    "done": None,
    "failed": None,
}
# The stages that end a job, with the state each one means.
ENDS: Final[Mapping[str, str]] = {
    "done": DONE,
    "recap_ready": DONE,
    "failed": FAILED,
    "recap_failed": FAILED,
}
# What a stalled job and a failed recap say, in plain words.
STALLED_REASON: Final = "Nothing has moved for a while."
RECAP_FAILED_REASON: Final = "The recap could not be written."
# What each count measures, for the stages that have one.
UNITS: Final[Mapping[str, str]] = {"cleaning": "lines"}


def shape(
    stage: str,
    *,
    started_at: int,
    updated_at: int,
    now: int,
    done: int | None = None,
    total: int | None = None,
    unit: str | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    """One job's progress. `updated_at` never goes before `started_at`."""
    updated_at = max(updated_at, started_at)
    state = ENDS.get(stage, RUNNING)
    limit = STALL_AFTER_S.get(stage)
    if state == RUNNING and limit is not None and now - updated_at > limit:
        state = STALLED
        reason = reason or STALLED_REASON
    if state == FAILED and not reason:
        reason = RECAP_FAILED_REASON if stage == "recap_failed" else None
    if state in (RUNNING, DONE):
        reason = None
    out: dict[str, Any] = {
        "stage": stage,
        "startedAt": started_at,
        "updatedAt": updated_at,
        "serverNow": now,
        "state": state,
        "reason": reason,
    }
    if done is not None and total is not None and total > 0:
        out.update(done=min(done, total), total=total, unit=unit)
    return out


# The order a job passes through the stages: the whole import is at the earliest one that some
# session has not finished.
ORDER: Final[Sequence[str]] = (
    "cleaning",
    "checking_names",
    "finding_names",
    "waiting_for_names",
    "starting_recap",
    "writing_recap",
    "recap_not_written",
)


def whole(parts: Sequence[Mapping[str, Any]], *, now: int) -> dict[str, Any]:
    """The shape for a batch of jobs: `done` of `total` jobs finished (unit 'sessions'), at the
    earliest stage any unfinished one is in, failed if one failed, else stalled if one stalled."""
    if not parts:
        return shape("done", started_at=now, updated_at=now, now=now)
    states = [p["state"] for p in parts]
    open_stages = [p["stage"] for p in parts if p["stage"] not in ENDS]
    if open_stages:
        stage = min(open_stages, key=lambda s: ORDER.index(s) if s in ORDER else len(ORDER))
    else:
        stage = "failed" if FAILED in states else "done"
    state = (
        FAILED if FAILED in states else STALLED if STALLED in states
        else DONE if all(s == DONE for s in states) else RUNNING
    )  # fmt: skip
    # (a failure's reason is the one shown when a stalled session is also in the batch)
    wanted = FAILED if state == FAILED else STALLED
    reason = next((p["reason"] for p in parts if p["state"] == wanted and p["reason"]), None)
    out = {
        "stage": stage,
        "startedAt": min(int(p["startedAt"]) for p in parts),
        "updatedAt": max(int(p["updatedAt"]) for p in parts),
        "serverNow": now,
        "state": state,
        "reason": reason if state in (FAILED, STALLED) else None,
        "done": sum(1 for s in states if s == DONE),
        "total": len(parts),
        "unit": "sessions",
    }
    return out
