"""How long a campaign is kept, the warnings before it is deleted, and the daily job that
does it (#964; docs/PLAN.md, "Plans and pricing", retention).

A campaign is deleted when its keep date passes: the last session plus its owner's plan's
`keepAfterLastSession`, or the plan's lapse plus 120 days, whichever comes first. The owner
is warned by private message 14 and 3 days before (the days are in plans.json), each warning
once. The rules are pure so they test with no Discord or database; the job takes everything
it touches as arguments.

A campaign with no owner is kept by Try It's rule: DMbot doesn't know a last owner's plan,
and the shortest rule is the safe one for storage; its DMs get the warnings instead.

Everything runs per campaign in that campaign's own server scope. The only read across
servers is the owner's plan, through `usage.py`'s owner-scoped door.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from dmbot import plans
from dmbot.campaigns import Campaign, CampaignStore
from dmbot.plans import PlanId

log = logging.getLogger(__name__)

DAY = 24 * 3600
RUN_HOUR_UTC = 3  # after hours for the people playing
# One run may delete at most this share of all campaigns, so a mistake (a plan read wrongly,
# a bad clock) can't wipe everything: it stops and logs instead. Under MIN_FOR_GUARD
# deletions the guard doesn't apply, so a small deployment isn't stuck on one old campaign.
MAX_SHARE_PER_RUN = 0.10
MIN_FOR_GUARD = 3


@dataclass(frozen=True, slots=True)
class Standing:
    """What decides one owner's keep date: the plan whose keep time applies, when that plan
    stopped paying (None while it works), and whether it includes backups (for the words)."""

    plan: PlanId
    lapsed_at: int | None
    backups: bool


# Try It's rule: for a campaign with no owner, or an owner who never had a plan.
UNKNOWN = Standing("try-it", None, False)


def delete_at(last_active_at: int, standing: Standing) -> int:
    """When the campaign is deleted: last session + the plan's keep time, or the lapse +
    120 days, whichever is first."""
    table = plans.load()
    at = last_active_at + table.by_id[standing.plan].keep_after_last_session.days * DAY
    if standing.lapsed_at is not None:
        at = min(at, standing.lapsed_at + table.keep_after_plan_stops_paying.days * DAY)
    return at


def warning_stage(delete: int, now: int) -> int:
    """Which warning is due: 0 none yet, 1 the first (14 days), 2 the last (3 days)."""
    first, last = sorted(plans.load().deletion_warning_days_before, reverse=True)[:2]
    left = delete - now
    if left <= last * DAY:
        return 2
    if left <= first * DAY:
        return 1
    return 0


def next_run_after(now: int) -> int:
    """The next RUN_HOUR_UTC o'clock, strictly after `now`."""
    at = datetime.fromtimestamp(now, UTC).replace(hour=RUN_HOUR_UTC, minute=0, second=0)
    at = at.replace(microsecond=0)
    if at.timestamp() <= now:
        at += timedelta(days=1)
    return int(at.timestamp())


def _date(ts: int) -> str:
    return f"<t:{ts}:D>"


def warning_text(
    campaign: Campaign, delete: int, now: int, standing: Standing, server: str, site_url: str = ""
) -> str:
    """The private message before deletion. It names the server, since a private message has
    none, and says where each way of keeping the campaign is done. The reason mentions a
    plan only to the owner, whose message this is; the campaign's DMs (no owner) get the
    time-based reason."""
    days = max(1, round((delete - now) / DAY))
    left = f"{days} day" + ("" if days == 1 else "s")
    lapse_at = (
        None
        if standing.lapsed_at is None
        else standing.lapsed_at + plans.load().keep_after_plan_stops_paying.days * DAY
    )
    ended = campaign.owner_user_id is not None and lapse_at is not None and delete <= lapse_at
    why = "no one has played it for a long time" + (", and your plan ended" if ended else "")
    last = "Last warning: " if warning_stage(delete, now) == 2 else ""
    where = f"in **{_md(server)}**"
    keep = [f"play a session {where} (that starts the clock again)"]
    if standing.backups:
        keep.append(f"download a backup with `/dmbot backup` {where}")
    if campaign.owner_user_id is not None and standing.lapsed_at is not None:
        keep.append(f"pick a plan again at {_plans_page(site_url)}")
    options = (
        keep[0]
        if len(keep) == 1
        else ", ".join(keep[:-1]) + (", or " if len(keep) > 2 else " or ") + keep[-1]
    )
    return (
        f"⏳ {last}**{_md(campaign.name)}** (in **{_md(server)}**) will be deleted on "
        f"{_date(delete)}, in {left}: {why}. Its story, notes and transcripts go, and that "
        f"can't be undone. To keep it, {options}."
    )


def _plans_page(site_url: str) -> str:
    return f"{site_url}/account" if site_url else "DMbot's website"


def deleted_text(campaign: Campaign, server: str) -> str:
    return (
        f"🗑️ **{_md(campaign.name)}** (in **{_md(server)}**) was deleted. DMbot keeps a "
        f"campaign for a limited time. To play again, start a new one with `/dmbot start` "
        f"in {_md(server)}."
    )


def _md(text: str) -> str:
    return text.replace("\\", "\\\\").replace("*", "\\*").replace("_", "\\_").replace("`", "\\`")


@dataclass(slots=True)
class Result:
    """Counts only: nothing about campaigns or people goes in the log."""

    scanned: int = 0
    to_delete: int = 0
    deleted: int = 0
    warnings: int = 0
    skipped: int = 0  # plan couldn't be read, or a session is running
    guard_stopped: bool = False
    dry_run: bool = False
    stages: dict[int, int] = field(default_factory=dict)


StandingOf = Callable[[int, int, int], Awaitable[Standing]]
Send = Callable[[int, str], Awaitable[bool]]


class RetentionJob:
    def __init__(
        self,
        *,
        campaigns: CampaignStore,
        standing_of: StandingOf,
        guild_ids: Callable[[], list[int]],
        running: Callable[[], set[str]],
        send: Send,
        enforce: bool,
        server_name: Callable[[int], str] = lambda guild_id: "your server",
        site_url: str = "",
    ) -> None:
        """`standing_of(guild_id, owner_id, now)`: the owner's plan (raises if it can't be
        read: then that campaign is left alone). `running()`: campaigns in a session now.
        `send(user_id, text)`: a private message; False if it couldn't be sent."""
        self._campaigns = campaigns
        self._standing_of = standing_of
        self._guild_ids = guild_ids
        self._running = running
        self._send = send
        self._enforce = enforce
        self._server_name = server_name
        self._site_url = site_url

    async def run_once(self, now: int) -> Result:
        """Safe to run twice: warnings are remembered, and a deleted campaign is gone."""
        result = Result(dry_run=not self._enforce)
        due: list[tuple[Campaign, Standing, int]] = []  # past their keep date
        warn: list[tuple[Campaign, Standing, int, int]] = []
        owners: dict[int, Standing] = {}
        running = self._running()
        for guild_id in self._guild_ids():
            for campaign in await self._campaigns.list_campaigns(guild_id):
                result.scanned += 1
                if campaign.id in running:
                    result.skipped += 1
                    continue
                try:
                    standing = await self._standing(guild_id, campaign, now, owners)
                except Exception:  # never delete on a plan we couldn't read
                    log.exception("Retention: couldn't read a plan; leaving the campaign alone")
                    result.skipped += 1
                    continue
                at = delete_at(campaign.last_active_at, standing)
                if at <= now:
                    due.append((campaign, standing, at))
                    continue
                stage = warning_stage(at, now)
                sent = (
                    campaign.retention_warned_for == at and campaign.retention_warned_stage >= stage
                )
                if stage and not sent:
                    warn.append((campaign, standing, at, stage))
        result.to_delete = len(due)
        if not self._enforce:  # a dry run: counts only
            result.warnings = len(warn)
            self._log(result)
            return result
        for campaign, standing, at, stage in warn:
            if await self._warn(campaign, standing, at, stage, now):
                result.warnings += 1
                result.stages[stage] = result.stages.get(stage, 0) + 1
        too_many = len(due) > MIN_FOR_GUARD and len(due) > MAX_SHARE_PER_RUN * result.scanned
        if too_many:
            result.guard_stopped = True
            log.error(
                "Retention: %d of %d campaigns are due for deletion, more than %d%%; "
                "deleting none. Check the plans and the clock.",
                len(due),
                result.scanned,
                int(MAX_SHARE_PER_RUN * 100),
            )
        else:
            for campaign, _standing, _at in due:
                if await self._delete(campaign):
                    result.deleted += 1
        self._log(result)
        return result

    async def _standing(
        self, guild_id: int, campaign: Campaign, now: int, cache: dict[int, Standing]
    ) -> Standing:
        owner = campaign.owner_user_id
        if owner is None:
            return UNKNOWN
        if owner not in cache:
            cache[owner] = await self._standing_of(guild_id, owner, now)
        return cache[owner]

    def _recipients(self, campaign: Campaign) -> list[int]:
        if campaign.owner_user_id is not None:
            return [campaign.owner_user_id]
        return sorted(campaign.dm_user_ids)

    async def _warn(
        self, campaign: Campaign, standing: Standing, at: int, stage: int, now: int
    ) -> bool:
        text = warning_text(
            campaign, at, now, standing, self._server_name(campaign.guild_id), self._site_url
        )
        told = False
        for user_id in self._recipients(campaign):
            told = await self._send(user_id, text) or told
        if not told:
            return False  # nobody could be reached: try again tomorrow
        await self._campaigns.set_retention_warned(campaign.guild_id, campaign.id, stage, at)
        return True

    async def _delete(self, campaign: Campaign) -> bool:
        try:
            await self._campaigns.delete(campaign.guild_id, campaign.id)
        except Exception:
            log.exception("Retention: couldn't delete a campaign")
            return False
        text = deleted_text(campaign, self._server_name(campaign.guild_id))
        for user_id in self._recipients(campaign):
            await self._send(user_id, text)
        return True

    @staticmethod
    def _log(result: Result) -> None:
        log.info(
            "Retention %s: campaigns=%d past_keep_date=%d deleted=%d warned=%d skipped=%d%s",
            "dry run" if result.dry_run else "run",
            result.scanned,
            result.to_delete,
            result.deleted,
            result.warnings,
            result.skipped,
            " guard_stopped=yes" if result.guard_stopped else "",
        )
