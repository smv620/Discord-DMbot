"""The one shape for how far a job has got, shared by every wait the website shows (#2564).

A job (an import, a recap, a hand-over move) reports `stage` (a code, never prose), `done` of
`total` where that is known, when it began (`startedAt`), the last time it moved forward
(`updatedAt`), and a `state`: running, stalled, failed or done. A failed or stalled job says why in
a short code (`reason`: the codes the jobs already store, plus "stalled" and "recap_failed"), which
the page puts into words. "Stalled" is worked out when the job is read, from the time since
`updatedAt` and that stage's limit below, so the limits live here on the server and the website
only shows `state`.
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
    "recap_not_written": None,  # the recap was held, declined or removed: a resting end
    "waiting": 2 * 60,  # a copy asked for on the website: the bot takes it within seconds
    "reading": 5 * 60,  # ... then reads the campaign
    "packing": 5 * 60,  # ... and packs it
    "sharing": 5 * 60,  # a sharing choice the bot is applying (#2783)
    # The steps of a recap being made on the website's page (#2636; `recaps.progress`). The name
    # pass wait is touched every half minute; a part of a long session can take a while.
    "recap_names": 10 * 60,
    "recap_reading": 20 * 60,
    "recap_beats": 10 * 60,
    "recap_writing": 30 * 60,
    "recap_checking": 30 * 60,
    "recap_check_waiting": None,  # written, and tried again later by design (hours, not minutes)
    "recap_finishing": 10 * 60,
    # The audio being made on the recap page (#2630): a part of the voice, the music call (up to
    # 7 minutes), the mix.
    "audio_voice": 10 * 60,
    "audio_music": 15 * 60,
    "audio_mix": 10 * 60,
    "recap_ready": None,
    "recap_failed": None,
    "done": None,
    "failed": None,
}
# The stages that end a job, with the state each one means.
ENDS: Final[Mapping[str, str]] = {
    "done": DONE,
    "recap_not_written": DONE,
    "recap_ready": DONE,
    "failed": FAILED,
    "recap_failed": FAILED,
}
# What a stalled job and a failed recap say (codes, like the reasons the jobs store).
STALLED_REASON: Final = "stalled"
RECAP_FAILED_REASON: Final = "recap_failed"
# What each count measures, for the stages that have one.
UNITS: Final[Mapping[str, str]] = {
    "cleaning": "lines",
    "audio_voice": "parts",
    "recap_reading": "parts",
}


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
    """One job's progress. `updated_at` never goes before `started_at` nor after `now` (the times
    come from the database's clock and `now` from the server's, which can differ a little).
    A job waiting its turn behind others passes the newest movement of the batch as `updated_at`,
    so it is not called stalled while the batch moves (see `web.imports`)."""
    updated_at = min(max(updated_at, started_at), max(now, started_at))
    state = ENDS.get(stage, RUNNING)
    limit = STALL_AFTER_S.get(stage)
    if state == RUNNING and limit is not None and now - updated_at > limit:
        state = STALLED
        reason = STALLED_REASON  # always: a reason stored for an earlier end is not this stall's
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
    earliest stage any unfinished one is in. While any job can still move the batch is `stalled`
    if one is, else `running`: a failed session stays on its own row and does not hide that the
    rest is stuck (the page would tell the DM to import again). Only when nothing can move any
    more is it `failed` (some failed) or `done`."""
    if not parts:
        return shape("done", started_at=now, updated_at=now, now=now)
    states = [p["state"] for p in parts]
    open_stages = [p["stage"] for p in parts if p["stage"] not in ENDS]
    if open_stages:
        stage = min(open_stages, key=lambda s: ORDER.index(s) if s in ORDER else len(ORDER))
        state = STALLED if STALLED in states else RUNNING
        reason = STALLED_REASON if state == STALLED else None
    else:
        stage = "failed" if FAILED in states else "done"
        state = FAILED if FAILED in states else DONE
        reason = next((p["reason"] for p in parts if p["state"] == FAILED and p["reason"]), None)
    return {
        "stage": stage,
        "startedAt": min(int(p["startedAt"]) for p in parts),
        "updatedAt": max(int(p["updatedAt"]) for p in parts),
        "serverNow": now,
        "state": state,
        "reason": reason,
        "done": sum(1 for s in states if s == DONE),
        "total": len(parts),
        "unit": "sessions",
    }
