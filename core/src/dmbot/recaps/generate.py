"""Writing one recap (#1382): the transcript in, a checked draft out, or the plain reason there
is none. One call on the fast tier; a draft that fails the checks gets one more try on the careful
tier told what to fix; a second failure is "no recap this time", never a rough draft.

Consent is read before the lines are sent and again after each answer (CLAUDE.md, after every
async step): if anyone whose words were sent has stopped being recorded, the draft is dropped,
since their words shaped it. The log gets counts only, never the recap.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Collection, Sequence
from dataclasses import dataclass, field

from dmbot.ai import AIOutOfFunds
from dmbot.memory.extractor import Client, readable
from dmbot.recaps import draft as rd
from dmbot.transcript.models import Line

log = logging.getLogger(__name__)

NOTHING = "There was nothing to recap."
STOPPED = "Someone in the recap stopped being recorded, so it was thrown away."
FAILED = "Couldn't write a recap this time."


@dataclass(frozen=True, slots=True)
class Result:
    draft: rd.Draft | None
    calls: int = 0
    why: str = ""  # plain words for the DM when there is no draft
    problems: tuple[str, ...] = field(default=())  # what the last rejected draft got wrong


async def generate(
    fast: Client,
    careful: Client,
    lines: Sequence[Line],
    *,
    dm_ids: Collection[int],
    consenting: Callable[[], Awaitable[Collection[int]]],
    allowed_names: Collection[str],
    secret_names: Collection[str],
    previous: str,
    setting: str | None,
    day: int | None,
) -> Result:
    """The recap for one session's `lines`, or why not. `previous` is the earlier recap's text
    without its cues (the bridge); `allowed_names` the confirmed names that are not secret;
    `secret_names` every secret name (which may appear nowhere)."""
    agreed = await consenting()
    said = [s for s in readable(lines) if s.user_id in agreed]
    if not said:
        return Result(None, 0, NOTHING)
    rows = [(str(s.number), "DM" if s.user_id in dm_ids else "Player", s.text) for s in said]
    sent_from = {s.user_id for s in said}
    transcript = "\n".join(text for _, _, text in rows)
    prompt = rd.message(rows, previous, allowed_names, day)
    instructions = rd.system(setting)
    calls = 0
    problems: list[str] = []
    for client in (fast, careful):
        message = prompt
        if problems:
            message += (
                "\nYour last draft was rejected: "
                + "; ".join(problems)
                + ". Write the whole recap again, fixing exactly that."
            )
        calls += 1
        try:
            reply = await client.complete(instructions, message, max_tokens=rd.MAX_TOKENS)
        except AIOutOfFunds:
            return Result(None, calls, FAILED)
        except Exception as exc:  # the AI service, the network: never fatal to the session
            log.warning("Recap: a call failed (%s)", type(exc).__name__)
            problems = ["the last attempt failed"]
            continue
        if not sent_from <= set(await consenting()):  # again, after the answer
            log.info("Recap: thrown away, a speaker stopped being recorded")
            return Result(None, calls, STOPPED)
        parsed = rd.parse(reply.text)
        if parsed is None:
            problems = ["it must follow the form: TITLE, SUMMARY, HOUSEKEEPING, MUSIC"]
            continue
        problems = rd.check(
            parsed,
            transcript=transcript,
            allowed_names=allowed_names,
            secret_names=secret_names,
            previous=previous,
            setting=setting,
        )
        if not problems:
            return Result(parsed, calls)
        log.info("Recap: a draft failed %d check(s)", len(problems))  # counts only
    return Result(None, calls, FAILED, tuple(problems))
