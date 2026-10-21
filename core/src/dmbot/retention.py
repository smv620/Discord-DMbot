"""How long a campaign is kept, the warnings before it is deleted, and the daily job that
does it (#964; docs/PLAN.md, "Plans and pricing", retention).

A campaign is deleted when its keep date passes: the last session plus its owner's plan's
`keepAfterLastSession`, or the plan's lapse plus 120 days, whichever comes first. The owner
is warned by private message 14 and 3 days before (the days are in plans.json), each warning
once. The rules are pure so they test with no Discord or database; the job takes everything
it touches as arguments.

A campaign with no owner is kept by Try It's rule: DMbot doesn't know a last owner's plan,
and the shortest rule is the safe one for storage; its DMs get the warnings instead.

Everything runs per campaign in that campaign's own server scope. The only reads across
servers are the owner's plan, through `usage.py`'s owner-scoped door, and the list of server
IDs that hold campaigns (a routing table), so a server DMbot has left is still reached.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from dmbot import plans
from dmbot.campaigns import Campaign, CampaignStore
from dmbot.plans import PlanId
from dmbot.retention_channels import ChannelCounts

log = logging.getLogger(__name__)

DAY = 24 * 3600
RUN_HOUR_UTC = 3  # after hours for the people playing
# One run may delete at most this share of all campaigns, so a mistake (a plan read wrongly,
# a bad clock) can't wipe everything: it stops and logs instead. Under MIN_FOR_GUARD
# deletions the guard doesn't apply, so a small deployment isn't stuck on one old campaign.
MAX_SHARE_PER_RUN = 0.10
MIN_FOR_GUARD = 3
LAST_RUN_FILE = "retention_last_run"
CATCH_UP_AFTER = DAY  # a run later than this since the last one is overdue (#989)
CATCH_UP_DELAY_S = 120  # after start-up, once the bot has its servers and is connected


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


def owner_left(campaign: Campaign) -> bool:
    """Its owner deleted their account and nobody has taken it on (#1059)."""
    return campaign.owner_user_id is None and campaign.owner_left_at is not None


def delete_date(campaign: Campaign, standing: Standing) -> int:
    """When the campaign is deleted. A campaign whose owner deleted their account goes
    120 days after that, whatever its plan was and however recently it was played (#1059;
    owner, 2026-10-11): the players may still read and download it until then, and any DM
    can take it on. Every other campaign follows `delete_at`."""
    if campaign.owner_left_at is not None and campaign.owner_user_id is None:
        # Tied on purpose to the 120 days a lapsed plan's campaigns are kept (the same
        # promise on the legal pages), not a separate setting.
        return campaign.owner_left_at + plans.load().keep_after_plan_stops_paying.days * DAY
    return delete_at(campaign.last_active_at, standing)


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


def catch_up_due(last_run: int | None, now: int) -> bool:
    """Was the last run more than a day ago? A bot that was down at 03:00 would otherwise
    skip that day. Never run before (a new install, or the first start after this was
    added) is not overdue: the next 03:00 run is the first."""
    return last_run is not None and now - last_run > CATCH_UP_AFTER


def read_last_run(state_dir: Path) -> int | None:
    """When the job last finished, or None if never or the file is unreadable."""
    try:
        return int((state_dir / LAST_RUN_FILE).read_text().strip())
    except (OSError, ValueError):
        return None


def write_last_run(state_dir: Path, now: int) -> None:
    """Keep the finish time of a run. Written aside and moved into place so a crash can't
    leave half a number; a failure is logged and only costs a catch-up next start."""
    tmp = state_dir / (LAST_RUN_FILE + ".tmp")
    try:
        state_dir.mkdir(parents=True, exist_ok=True)
        tmp.write_text(str(int(now)))
        os.replace(tmp, state_dir / LAST_RUN_FILE)
    except OSError:
        log.exception("Retention: couldn't record the time of this run")


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
    if owner_left(campaign):
        # A fixed date, not a clock a session restarts: only taking it on keeps it.
        why = "the DM who ran it deleted their account and no one has taken it on"
        keep = [
            f"any of its DMs can open ⚙️ Settings → 🗂️ Campaign management {where} and "
            f"press 🤝 **{TAKE_ON}**"
        ]
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


TAKE_ON = "Take it on"  # the button's label (dm_screen.handover.TAKE_ON_LABEL)
WARNINGS_PER_MESSAGE = 6  # keeps a message well under Discord's 2000 characters
NAME_SHOWN = 50


def grouped_warning_text(
    items: list[tuple[Campaign, Standing, int]],
    now: int,
    server_name: Callable[[int], str],
    site_url: str = "",
) -> str:
    """One private message for several campaigns of one person (#989): a line each with its
    delete date, then how to keep one. A single campaign gets `warning_text` as before."""
    if len(items) == 1:
        campaign, standing, at = items[0]
        return warning_text(campaign, at, now, standing, server_name(campaign.guild_id), site_url)
    lines = []
    for campaign, standing, at in sorted(items, key=lambda i: i[2]):  # soonest first
        days = max(1, round((at - now) / DAY))
        ended = campaign.owner_user_id is not None and standing.lapsed_at is not None
        lines.append(
            f"• **{_md(_short(campaign.name))}** ({_md(_short(server_name(campaign.guild_id)))}): "
            f"in {days} day"
            + ("" if days == 1 else "s")
            + f" ({_date(at)})"
            + (", plan ended" if ended else "")
            + (", its DM left" if owner_left(campaign) else "")
        )
    last = "Last warning: " if any(warning_stage(at, now) == 2 for _c, _s, at in items) else ""
    keep = []
    if any(not owner_left(c) for c, _s, _a in items):
        keep.append("play a session in that campaign's server (it is then safe for a while)")
    if any(owner_left(c) for c, _s, _a in items):
        keep.append(
            "for one whose DM left, any of its DMs can open ⚙️ Settings → 🗂️ Campaign "
            "management in its server and "
            f"press 🤝 **{TAKE_ON}** (a session doesn't keep it)"
        )
    if any(s.backups for _c, s, _a in items):
        keep.append("download a backup with `/dmbot backup` in that server")
    if any(c.owner_user_id is not None and s.lapsed_at is not None for c, s, _a in items):
        keep.append(f"where the plan ended, pick a plan again at {_plans_page(site_url)}")
    options = (
        keep[0]
        if len(keep) == 1
        else ", ".join(keep[:-1]) + (", or " if len(keep) > 2 else " or ") + keep[-1]
    )
    return (
        f"⏳ {last}These campaigns will be deleted for good: their story, notes and "
        f"transcripts can't be brought back.\n"
        + "\n".join(lines)
        + f"\nTo keep a campaign, {options}."
    )


def _short(text: str) -> str:
    """Names are cut so six lines always fit in one message."""
    return text if len(text) <= NAME_SHOWN else text[: NAME_SHOWN - 1] + "…"


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


GRACE_DAYS = 3  # a campaign already past its date when first seen gets this long
MAX_WARNINGS_PER_RUN = 300  # the rest wait for tomorrow: Discord limits private messages


def may_delete(campaign: Campaign, at: int, now: int) -> bool:
    """Has the campaign had its last warning for the date it is being deleted on? Either the
    3-day warning for this very date, or (stage 3) a warning sent when it was found already
    past its date, at least GRACE_DAYS ago and with no session since."""
    if at > now:
        return False
    stage, announced = campaign.retention_warned_stage, campaign.retention_warned_for
    if announced is None:
        return False
    if stage >= 2 and announced == at:
        return True
    return (
        stage == 3 and announced <= now and campaign.last_active_at <= announced - GRACE_DAYS * DAY
    )


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
    channels: ChannelCounts = field(default_factory=ChannelCounts)  # of the campaigns deleted


StandingOf = Callable[[int, int, int], Awaitable[Standing]]
Send = Callable[[int, str], Awaitable[bool]]
# Called with a campaign that was just deleted (its row read before the delete): deletes or
# closes the Discord channels it recorded (#1598) and says how many.
RetireChannels = Callable[[Campaign, int], Awaitable[ChannelCounts]]


class RetentionJob:
    def __init__(
        self,
        *,
        campaigns: CampaignStore,
        standing_of: StandingOf,
        guild_ids: Callable[[], Awaitable[list[int]]],
        forget_guild: Callable[[int], Awaitable[bool]] | None = None,
        running: Callable[[], set[str]],
        send: Send,
        retire_channels: RetireChannels | None = None,
        enforce: bool,
        server_name: Callable[[int], str] = lambda guild_id: "your server",
        site_url: str = "",
    ) -> None:
        """`standing_of(guild_id, owner_id, now)`: the owner's plan (raises if it can't be
        read: then that campaign is left alone). `running()`: campaigns in a session now.
        `send(user_id, text)`: a private message; False if it couldn't be sent.
        `guild_ids()`: every server that holds campaigns, from the database (#989).
        `forget_guild(guild_id)`: called for a server with no campaigns left."""
        self._campaigns = campaigns
        self._standing_of = standing_of
        self._guild_ids = guild_ids
        self._forget_guild = forget_guild
        self._running = running
        self._send = send
        self._retire_channels = retire_channels
        self._enforce = enforce
        self._server_name = server_name
        self._site_url = site_url

    async def run_once(self, now: int) -> Result:
        """Safe to run twice: warnings are remembered, and a deleted campaign is gone.

        A campaign is only ever deleted after its last warning went out for the date it is
        being deleted on (`may_delete`): one already past its date when first seen (a first
        run after deploy, a long outage) is warned first and deleted three days later."""
        result = Result(dry_run=not self._enforce)
        due: list[tuple[Campaign, int]] = []  # past their keep date
        warn: list[tuple[Campaign, Standing, int, int]] = []  # campaign, plan, date, stage
        owners: dict[int, Standing] = {}
        running = self._running()
        for guild_id in await self._guild_ids():
            campaigns = await self._campaigns.list_campaigns(guild_id)
            if not campaigns and self._enforce and self._forget_guild is not None:
                try:
                    await self._forget_guild(guild_id)
                except Exception:  # only a tidy-up: the next run tries again
                    log.exception("Retention: couldn't drop an empty server from the list")
            for campaign in campaigns:
                result.scanned += 1
                if campaign.created_at > now:  # the clock is wrong: touch nothing this run
                    log.error("Retention: a campaign is newer than the clock; doing nothing")
                    result.guard_stopped = True
                    return result
                if campaign.id in running:
                    result.skipped += 1
                    continue
                try:
                    standing = await self._standing(guild_id, campaign, now, owners)
                except Exception:  # never delete on a plan we couldn't read
                    log.exception("Retention: couldn't read a plan; leaving the campaign alone")
                    result.skipped += 1
                    continue
                at = delete_date(campaign, standing)
                if at <= now:
                    due.append((campaign, at))
                    waiting = (
                        campaign.retention_warned_stage == 3
                        and campaign.retention_warned_for is not None
                        and campaign.retention_warned_for > now
                    )  # the grace warning is out: just wait for its date
                    if not waiting and not may_delete(campaign, at, now):
                        # First seen past its date: warn now, delete in WARN_GRACE_DAYS.
                        warn.append((campaign, standing, now + GRACE_DAYS * DAY, 3))
                    continue
                stage = warning_stage(at, now)
                if campaign.retention_warned_stage and campaign.retention_warned_for != at:
                    # The date moved (it was played, or the plan changed): old warnings
                    # no longer count, so none can license a later deletion.
                    if self._enforce:
                        await self._remember(campaign, 0, None)
                    campaign = replace(
                        campaign, retention_warned_stage=0, retention_warned_for=None
                    )
                if stage and campaign.retention_warned_stage < stage:
                    warn.append((campaign, standing, at, stage))
        if result.scanned == 0:
            log.warning("Retention: no campaigns found (the bot may still be starting)")
        result.to_delete = len(due)
        # The guard comes first: if this run would delete too many, say nothing to anyone.
        evaluated = result.scanned - result.skipped
        if len(due) > MIN_FOR_GUARD and len(due) > MAX_SHARE_PER_RUN * evaluated:
            result.guard_stopped = True
            log.error(
                "Retention: %d of %d campaigns are past their keep date, more than %d%%; "
                "doing nothing. Check the plans and the clock.",
                len(due),
                evaluated,
                int(MAX_SHARE_PER_RUN * 100),
            )
            self._log(result)
            return result
        if not self._enforce:  # a dry run: counts only
            result.warnings = len(warn)
            self._log(result)
            return result
        for _campaign, stage in await self._warn_all(warn[:MAX_WARNINGS_PER_RUN], now):
            result.warnings += 1
            result.stages[stage] = result.stages.get(stage, 0) + 1
        for campaign, _at in due:
            if await self._delete(campaign, now, owners, result):
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

    async def _remember(self, campaign: Campaign, stage: int, announced: int | None) -> bool:
        try:
            await self._campaigns.set_retention_warned(
                campaign.guild_id, campaign.id, stage, announced
            )
        except Exception:  # one campaign's failure never stops the run
            log.exception("Retention: couldn't record a warning")
            return False
        return True

    async def _warn_all(
        self, warn: list[tuple[Campaign, Standing, int, int]], now: int
    ) -> list[tuple[Campaign, int]]:
        """Send the warnings, one private message per person however many campaigns they
        have (#989). A campaign counts as warned (and is remembered) when at least one of
        its people was reached; a campaign no one could be reached for is tried again tomorrow.
        Returns those remembered."""
        by_user: dict[int, list[tuple[Campaign, Standing, int, int]]] = {}
        for item in warn:
            for user_id in self._recipients(item[0]):
                by_user.setdefault(user_id, []).append(item)
        told: set[str] = set()
        for user_id, items in by_user.items():
            for start in range(0, len(items), WARNINGS_PER_MESSAGE):
                chunk = items[start : start + WARNINGS_PER_MESSAGE]
                text = grouped_warning_text(
                    [(c, st, at) for c, st, at, _stage in chunk],
                    now,
                    self._server_name,
                    self._site_url,
                )
                if await self._send(user_id, text):
                    told.update(c.id for c, _st, _at, _stage in chunk)
        done: list[tuple[Campaign, int]] = []
        for campaign, _standing, at, stage in warn:
            if campaign.id in told and await self._remember(campaign, stage, at):
                done.append((campaign, stage))
        return done

    async def _delete(
        self,
        stale: Campaign,
        now: int,
        owners: dict[int, Standing],
        result: Result | None = None,
    ) -> bool:
        """Delete one campaign, after looking again: a session may have started, it may
        have been played, or the owner's plan may have changed since the scan."""
        if stale.id in self._running():
            return False
        try:
            campaign = await self._campaigns.get(stale.guild_id, stale.id)
            if campaign is None:
                return False
            standing = await self._standing(campaign.guild_id, campaign, now, owners)
            if not may_delete(campaign, delete_date(campaign, standing), now):
                return False
            await self._campaigns.delete(campaign.guild_id, campaign.id)
        except Exception:
            log.exception("Retention: couldn't delete a campaign")
            return False
        # After the delete went through (a failed delete leaves everything as it was), and read
        # from the row as it was before it: the channel ids are gone with the row.
        if self._retire_channels is not None:
            try:
                counts = await self._retire_channels(campaign, now)
                if result is not None:
                    result.channels.add(counts)
            except Exception:  # the campaign is gone either way; this is only the tidy-up
                log.exception("Retention: couldn't tidy a deleted campaign's channels")
        text = deleted_text(campaign, self._server_name(campaign.guild_id))
        for user_id in self._recipients(campaign):
            await self._send(user_id, text)
        return True

    @staticmethod
    def _log(result: Result) -> None:
        log.info(
            "Retention %s: campaigns=%d past_keep_date=%d deleted=%d warned=%d skipped=%d%s"
            " channels: %d deleted, %d closed, %d skipped",
            "dry run" if result.dry_run else "run",
            result.scanned,
            result.to_delete,
            result.deleted,
            result.warnings,
            result.skipped,
            " guard_stopped=yes" if result.guard_stopped else "",
            result.channels.deleted,
            result.channels.closed,
            result.channels.skipped,
        )
