"""The email that tells a DM their recap is ready (or could not be written) (#1382).

It goes to the DM's account address through the mail DMbot already sends (dmbot.mail) and holds
the chapter title and a link, never the recap: sent mail is logged and forwarded, and the recap
is best read where it can be edited (the website). A failed job also emails, with the reason in
plain words and nothing technical. Each kind goes once for each session: `Mailer.send` keeps the
record, with a short kind built from a hash so no campaign id sits in the mail log.
"""

from __future__ import annotations

import hashlib
import logging

from dmbot.mail import Mailer
from dmbot.recaps.store import Job, RecapStore

log = logging.getLogger(__name__)


def job_ref(guild_id: int, campaign_id: str, session_at: int) -> str:
    """The id the website's page uses: `<server>.<campaign>.<session>`."""
    return f"{guild_id}.{campaign_id}.{session_at}"


def parse_ref(ref: str) -> tuple[int, str, int] | None:
    parts = ref.split(".")
    if len(parts) != 3 or not parts[0].isdigit() or not parts[2].isdigit():
        return None
    if not parts[1].isalnum() or len(parts[1]) > 64:
        return None
    return int(parts[0]), parts[1], int(parts[2])


def page_link(site: str, guild_id: int, campaign_id: str, session_at: int) -> str:
    return f"{site.rstrip('/')}/account/recaps/{job_ref(guild_id, campaign_id, session_at)}"


def _kind(what: str, guild_id: int, campaign_id: str, session_at: int) -> str:
    digest = hashlib.sha256(f"{guild_id}.{campaign_id}.{session_at}".encode()).hexdigest()[:16]
    return f"recap-{what}:{digest}"


async def tell_the_dm(
    mailer: Mailer, store: RecapStore, site: str, guild_id: int, campaign_id: str, job: Job
) -> str:
    """Email the DM that the job is ready or has failed. Returns what the mailer said
    (`sent`, `no_address`, …); never raises."""
    if not site:
        return "not_ready"
    address = await store.address(guild_id, job.dm_user_id)
    link = page_link(site, guild_id, campaign_id, job.session_at)
    if job.state == "ready":
        subject = "Your session recap is ready"
        body = (
            f"Your recap is ready: {job.title}\n\n"
            f"Read it, change it and decide what to do with it here:\n{link}\n\n"
            "Nothing is posted to your table until you say so."
        )
        kind = _kind("ready", guild_id, campaign_id, job.session_at)
    else:
        subject = "DMbot couldn't write a recap for your session"
        body = (
            f"{job.last_error or 'Something went wrong.'}\n\n"
            f"You can look at it, and try again, here:\n{link}"
        )
        kind = _kind("failed", guild_id, campaign_id, job.session_at)
    result = await mailer.send(
        user_id=job.dm_user_id, kind=kind, to=address, subject=subject, body=body
    )
    log.info("Recap: the email was %s", result)
    return result
