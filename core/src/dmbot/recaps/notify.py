"""The email that tells a DM their recap is ready (or could not be written) (#1382).

It goes to the DM's account address through the mail DMbot already sends (dmbot.mail) and holds
the chapter title and a link, never the recap: sent mail is logged and forwarded, and the recap
is best read where it can be edited (the website). A failed job also emails, with the reason in
plain words and nothing technical. Both go to every DM of the campaign, since any of them can
answer the names or read the recap (#2237). A held recap that waits on name questions emails once
a day (#2174). Each kind goes once for each session (or each day, for the held one):
`Mailer.send` keeps the record, with a short kind built from a hash so no campaign id sits in
the mail log.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Iterable

from dmbot.mail import Mailer
from dmbot.recaps.allowance import is_refusal
from dmbot.recaps.pipeline import NO_RETRY
from dmbot.recaps.store import Job, RecapStore

log = logging.getLogger(__name__)


def job_ref(guild_id: int, campaign_id: str, session_at: int) -> str:
    """The id the website's page uses: `<server>.<campaign>.<session>`."""
    return f"{guild_id}.{campaign_id}.{session_at}"


def _ascii_number(text: str) -> bool:
    return text.isascii() and text.isdecimal()


def parse_ref(ref: str) -> tuple[int, str, int] | None:
    parts = ref.split(".")
    # ASCII digits only: `isdigit` is also true for characters like "²", which `int` refuses.
    if len(parts) != 3 or not _ascii_number(parts[0]) or not _ascii_number(parts[2]):
        return None
    if not parts[1].isalnum() or len(parts[1]) > 64:
        return None
    return int(parts[0]), parts[1], int(parts[2])


def page_link(site: str, guild_id: int, campaign_id: str, session_at: int) -> str:
    return f"{site.rstrip('/')}/account/recaps/{job_ref(guild_id, campaign_id, session_at)}"


def _kind(what: str, guild_id: int, campaign_id: str, session_at: int, run: int = 0) -> str:
    """Once for each session and each run (so a new recap gets its own email). `run` must never
    go down: it is the job's last-change time, not its tries, because a failure of ours gives the
    DM's Try again back and the same key would then drop the next failure's email (#2341)."""
    digest = hashlib.sha256(f"{guild_id}.{campaign_id}.{session_at}.{run}".encode()).hexdigest()[
        :16
    ]
    return f"recap-{what}:{digest}"


async def tell_the_dm(
    mailer: Mailer,
    store: RecapStore,
    site: str,
    guild_id: int,
    campaign_id: str,
    job: Job,
    dm_user_ids: Iterable[int] = (),
) -> str:
    """Email the DMs that the job is ready or has failed: the job's own DM and the campaign's
    other DMs (`dm_user_ids`), each once (#2237: the same people as the waiting email). Returns
    what the mailer said to the job's DM (`sent`, `no_address`, …); never raises."""
    if not site:
        return "not_ready"
    link = page_link(site, guild_id, campaign_id, job.session_at)
    if job.state == "ready":
        subject = f"Your recap is ready: {job.title}"
        body = (
            f"Your recap is ready: {job.title}\n\n"
            f"Read it and change it here: {link}\n\n"
            "When it's right, press Post to send it to your table. "
            "Nothing is posted until you do."
        )
        kind = _kind("ready", guild_id, campaign_id, job.session_at, job.updated_at)
    else:
        subject = f"No recap this time: {job.last_error or 'something went wrong'}"[:150]
        again = (
            ""
            if job.last_error in NO_RETRY or is_refusal(job.last_error)
            else f"\n\nTry again here: {link}"
        )
        body = f"{job.last_error or 'Something went wrong.'}{again}"
        kind = _kind("failed", guild_id, campaign_id, job.session_at, job.updated_at)
    result = "no_address"
    for user_id in dict.fromkeys((job.dm_user_id, *sorted(dm_user_ids))):  # the job's DM first
        try:
            address = await store.address(guild_id, user_id)
        except Exception:  # one DM's lookup failing must not stop the others
            log.exception("Recap: couldn't look up a DM's address")
            address = None
        sent = await mailer.send(user_id=user_id, kind=kind, to=address, subject=subject, body=body)
        log.info("Recap: the email was %s", sent)
        if user_id == job.dm_user_id:
            result = sent
    return result


async def remind_the_dm(
    mailer: Mailer,
    store: RecapStore,
    site: str,
    guild_id: int,
    campaign_id: str,
    dm_user_id: int,
    session_at: int,
    waiting: int,
    day: int,
    group_id: str = "",
) -> str:
    """Email a DM that a held recap still waits on name questions (#2174). The caller sends it to
    every DM of the campaign, like the ready email (#2237). At most one a day:
    the kind carries the day number, so a second round the same day finds it already sent.
    It holds a count and a link, never a name or any story. Never raises."""
    if not site or waiting <= 0:
        return "not_ready"
    address = await store.address(guild_id, dm_user_id)
    link = page_link(site, guild_id, campaign_id, session_at)
    noun = "name question" if waiting == 1 else "name questions"
    body = (
        f"{waiting} {noun} from your last session {'is' if waiting == 1 else 'are'} still open, "
        "so your recap is waiting.\n\n"
        "Answer in Discord (press Check new names), or press Write the recap now. "
        f"The recap page is here: {link}\n\n"
        "If nothing is done, DMbot writes the recap by itself, with the names as they are."
    )
    # An import's holds share one email a day: the group id is stable when the oldest session
    # of the group is answered and the next one speaks for it.
    # A lone hold is keyed by its own session, so two held sessions each get their email.
    kind = _kind(
        "waiting", guild_id, campaign_id, int(group_id, 16) if group_id else session_at, day
    )
    return await mailer.send(
        user_id=dm_user_id,
        kind=kind,
        to=address,
        subject="Your recap is waiting for you",
        body=body,
    )
