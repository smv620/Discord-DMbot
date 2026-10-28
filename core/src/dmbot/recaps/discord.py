"""The recap features of the Discord bot, as the first piece moved out of `bot.py` (#2129).

Everything the bot does about recaps lives here: queueing one when a session ends, holding it for
the DM's answers about names, the plan and allowance checks at every stage, the DM-screen lines,
the posting, the audio files, the recaps of imported sessions, the website's recap requests and
spoken notes, and the loops that run them. The methods were moved from `DMBot` unchanged.

**A mixin, not a Cog, for now (Supervisor's ruling, 2026-10-23).** `DMBot` inherits
`RecapFeatures`, so `DMBot.<name>` still resolves and the tests, which build a fake bot and bind
these methods onto it, keep working untouched. A real discord.py Cog holds its bot as `self.bot`,
which would mean editing the fakes in about fifteen test files at the same time as moving the
code; that is a later pass, once the features are out of `bot.py`, and the next features follow the
same shape. `setup(bot)` is the extension entry: `DMBot.setup_hook` awaits it to register the recap
buttons. The loops are started by `recap_tasks`.

State is reached only through the bot's public methods and the stores it holds; nothing here sets
another feature's fields.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import logging
import time
from collections.abc import (
    Awaitable,
    Callable,
    Collection,
    Sequence,
)
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any

import discord

from dmbot.audio.segmenter import Utterance
from dmbot.campaigns import Campaign
from dmbot.dm_screen.recap_panel import PlanFacts, audio_how
from dmbot.entitlements import RECAP_AUDIO_PLANS
from dmbot.logs import log_context
from dmbot.memory.exposures import secret_connections
from dmbot.memory.lookup import CampaignLookup
from dmbot.memory.models import (
    GOING,
    HINTED,
    OPEN,
    PROPOSED,
    MemoryRuleError,
    name_key,
)
from dmbot.memory.recall import DM_SCREEN, PLAYERS, RecallService
from dmbot.memory.scene import SceneTracker, scene_hints
from dmbot.memory.scene import prepare as prepare_hints
from dmbot.recaps import allowance as recap_allowance
from dmbot.recaps import critics, cues, name_answers, short, uploaded, voice
from dmbot.recaps import feedback as recap_feedback
from dmbot.recaps import hold as recap_hold
from dmbot.recaps import post as recap_post
from dmbot.recaps import requests as recap_requests
from dmbot.recaps.approval import text_hash
from dmbot.recaps.audio import names as audio_names
from dmbot.recaps.audio.files import FILE_NAMES, MUSIC, VOICE, AudioFiles
from dmbot.recaps.audio.worker import AudioInputs
from dmbot.recaps.inputs import (
    EVERY_NAME,
    load_inputs,
    recap_name_sets,
    recap_recanted,
    recap_said,
)
from dmbot.recaps.notify import page_link, remind_the_dm, tell_the_dm
from dmbot.recaps.pipeline import Inputs
from dmbot.recaps.requests import Request as RecapRequest
from dmbot.recaps.significance import Memory
from dmbot.recaps.store import Job, SavedLists
from dmbot.recaps.text import names_a_secret, strip_cues
from dmbot.transcript.cleaner import (
    clean,
)
from dmbot.transcript.import_job import ImportStore, Ready
from dmbot.transcription.base import (
    TranscriptionProblem,
)
from dmbot.ui import recap_audio
from dmbot.ui import recap_feedback as recap_feedback_screen
from dmbot.ui.names import (
    RecapNamesButton,
    recap_names_dm_view,
    recap_names_reminder,
    recap_names_text,
    review_view,
)
from dmbot.ui.recap_again import (
    AgainButton,
    KeepButton,
    again_view,
    dmbot_recap,  # noqa: F401 (registers it)
)
from dmbot.ui.recap_dm import AUDIO_WHY, RecapButton, ready_text, ready_view
from dmbot.ui.recap_hold import WriteNowButton, hold_line_key, hold_view
from dmbot.ui.recap_share import ShareButton
from dmbot.ui.recap_short import EXPIRED, ShortRecapButton, ask_key, ask_view

if TYPE_CHECKING:  # (only for annotations: `bot.py` imports this module)
    from collections.abc import Coroutine, Hashable, Sequence

    from dmbot import plan_rules
    from dmbot.ai import TierClient
    from dmbot.bot import DMBot
    from dmbot.campaigns import web_backup, web_restore
    from dmbot.campaigns.store import CampaignStore
    from dmbot.campaigns.web_delete import WebDeleteNotices
    from dmbot.config import Settings
    from dmbot.consent import ConsentStore
    from dmbot.dm_screen.peek_notices import PeekNotices
    from dmbot.dm_screen.web_settings import SettingStore
    from dmbot.mail import Mailer
    from dmbot.memory.exposures import ExposureStore
    from dmbot.memory.importance import ImportanceCache
    from dmbot.memory.lookup import LookupCache
    from dmbot.memory.store import MemoryStore
    from dmbot.memory.web_actions import ActionStore
    from dmbot.memory.web_sheets import ActionStore as SheetActionStore
    from dmbot.recaps.audio.worker import AudioWorker
    from dmbot.recaps.pipeline import RecapWorker
    from dmbot.recaps.plot import PlotRunner
    from dmbot.recaps.plot_talk_run import TalkRunner
    from dmbot.recaps.store import RecapStore
    from dmbot.recaps.style import NoteStore, StyleStore
    from dmbot.rules.web_sharing import ActionStore as RuleActionStore
    from dmbot.table import HintPeople, Table
    from dmbot.timebot.store import ClockStore
    from dmbot.transcript.import_job import ImportJob
    from dmbot.transcript.store import TranscriptStore
    from dmbot.transcript.waiting_questions import WaitingQuestionStore
    from dmbot.transcript.web_line_fixes import ActionStore as LineFixStore
    from dmbot.transcription.pipeline import TranscriptionPipeline
    from dmbot.ui.decision_lines import DecisionLines
    from dmbot.usage import Meter

# Logged under the bot's own name, so the lines and any filter on them stay as they were.
log = logging.getLogger("dmbot.bot")

RECAP_ATTACHMENT_MAX = 24 * 1024 * 1024  # Discord's own most for a message
RECAP_DEFAULT_FILE_LIMIT = 10 * 1024 * 1024 - 65536  # a server's smallest limit, with some room
RECAPS_PER_ROUND = 2  # recap jobs worked in one minute, across all servers (a small global limit)
WEB_RESTORE_ALIVE_EVERY_S = 30  # seconds between touches of the queued website restores
WEB_RESTORE_TIDY_EVERY = 30  # rounds (about a minute) between the restore rows' upkeep
RECAP_REQUEST_ROUND_S = 2  # how often the bot looks for what a DM typed on the recap page
RECAP_REQUESTS_PER_ROUND = 3  # for one server, so one busy server can't hold the others
RECAP_VOICE_AT_ONCE = 3  # spoken notes written down at the same time, in all servers (#2161)
NAME_ANSWERS_PER_ROUND = 10  # a page of names answered at once, for one server
CLIP_SWEEP_EVERY = (
    30  # rounds (about a minute) between sweeps of voice clips older than ten minutes
)
RECAP_ROUND_S = 60
IMPORT_RECAP_TRIES = 3  # so is the start of an imported session's recap
RECAP_RECALL_BUDGET = 3500  # characters of story memory PlotBot reads for one recap (#2254)
PLOT_TALK_SWEEP_EVERY = 720  # rounds: about an hour at five seconds
PLOT_TALK_ROUND_S = 5  # how often the DM's messages to PlotBot are looked for (#2123)
HINTS_WAIT_S = 1.5  # the longest a clip waits for the campaign's names to load
VOICE_TRANSCRIBE_S = 65.0  # a spoken note is up to two minutes: far longer than a live line


class PlanUnchecked(Exception):
    """The plan could not be checked right now (a timeout, the database): not a "no"."""


def _attachable(
    files: AudioFiles, gid: int, cid: str, session_at: int
) -> list[tuple[str, Path, int]]:
    """The narration and music files that exist, with their sizes. A file that vanishes between
    the look and the size (a sweep, a remake) is just left out."""
    out: list[tuple[str, Path, int]] = []
    for kind, path in files.present(gid, cid, session_at).items():
        if kind not in (VOICE, MUSIC):
            continue
        try:
            out.append((kind, path, path.stat().st_size))
        except OSError:
            continue
    return out


async def setup(bot: DMBot) -> None:
    """The extension entry: the recap buttons keep working after a restart (a button is dropped
    by Discord's "This interaction failed" unless it is registered at start-up)."""
    bot.add_dynamic_items(RecapNamesButton)  # the private message's check (#2027)
    bot.add_dynamic_items(RecapButton)  # the recap on the DM screen (#1455)
    bot.add_dynamic_items(
        recap_audio.ListenButton, recap_audio.KeepAudioButton, recap_audio.MakeAudioAgainButton
    )  # the audio's end, said once (#2621)
    bot.add_dynamic_items(ShareButton)  # Share a recap (#2118)
    bot.add_dynamic_items(AgainButton, KeepButton)  # Make a recap again (#2608)
    bot.add_dynamic_items(ShortRecapButton)  # "write a recap anyway?" for a short session (#2104)
    bot.add_dynamic_items(WriteNowButton)  # a recap waiting for name answers (#2169)


class RecapFeatures:
    """The recap methods of `DMBot` (see the module note)."""

    if TYPE_CHECKING:
        # What these methods read of the bot, declared the way `DMBot.__init__` sets it, so the
        # checker reads the moved code as it read it in `bot.py`. Each is `DMBot`'s own: a change
        # there is a change here until the Cog pass gives this class a real bot to hold.
        _audio_seen: dict[tuple[int, str, int], tuple[int, int]]
        _clip_sweep_rounds: int
        _group_locks: dict[str, asyncio.Lock]
        _group_made: dict[str, list[int]]
        _group_started: dict[tuple[int, str, str], float]
        _import_queued: dict[str, list[int]]
        _import_unhonoured: dict[str, list[str]]
        _short_asks: dict[str, int]
        _voice_next: int
        _voice_tasks: dict[int, asyncio.Task[None]]
        audio_waits: recap_audio.AudioWaits
        audio_worker: AudioWorker | None
        campaigns: CampaignStore
        clocks: ClockStore | None
        consent: ConsentStore
        decision_lines: DecisionLines
        exposures: ExposureStore | None
        extractor_ai: TierClient | None
        heard_questions: WaitingQuestionStore | None
        import_job: ImportJob | None
        importance: ImportanceCache | None
        lookup: LookupCache | None
        mailer: Mailer | None
        memory: MemoryStore | None
        meter: Meter | None
        name_actions: ActionStore | None
        name_answers: name_answers.AnswerStore | None
        peek_notices: PeekNotices | None
        pipeline: TranscriptionPipeline
        plot_runner: PlotRunner | None
        plot_talk: TalkRunner | None
        recap_audio_files: AudioFiles
        recap_feedback_ai: TierClient | None
        recap_feedback_limit: recap_feedback.FeedbackLimit
        recap_holds: recap_hold.HoldStore | None
        recap_notes: NoteStore | None
        recap_requests: recap_requests.RequestStore | None
        recap_styles: StyleStore | None
        recap_worker: RecapWorker | None
        recaps: RecapStore | None
        settings: Settings
        tables: dict[int, Table]
        transcripts: TranscriptStore | None
        web_backups: web_backup.WebBackups | None
        web_restores: web_restore.WebRestores | None
        web_delete_notices: WebDeleteNotices | None
        web_settings: SettingStore | None
        web_rule_actions: RuleActionStore | None
        web_sheets: SheetActionStore | None
        web_line_fixes: LineFixStore | None

        @property
        def guilds(self) -> Sequence[discord.Guild]: ...
        def get_channel(
            self, id: int, /
        ) -> (
            discord.VoiceChannel
            | discord.StageChannel
            | discord.ForumChannel
            | discord.TextChannel
            | discord.CategoryChannel
            | discord.Thread
            | discord.abc.PrivateChannel
            | None
        ): ...
        async def fetch_user(self, user_id: int, /) -> discord.User: ...
        async def run_web_backups(self) -> None: ...
        async def run_web_delete_notices(self) -> None: ...
        @staticmethod
        def _watched(work: Coroutine[Any, Any, None], name: str) -> asyncio.Task[None]: ...
        async def _dm_message(
            self, user_id: int, text: str, view: discord.ui.View | None
        ) -> discord.Message | None: ...
        async def _hint_people(self, guild_id: int, table: Table | None) -> HintPeople: ...
        async def plan_allows(
            self,
            action: plan_rules.Action,
            guild_id: int,
            campaign: Campaign,
            *,
            fail_open: bool = True,
        ) -> bool: ...
        async def post(
            self,
            channel_id: int,
            text: str,
            view: discord.ui.View | None = None,
            *,
            remember: Hashable | None = None,
            secret_of: tuple[int, str] | None = None,
        ) -> bool: ...
        async def post_message(
            self,
            channel_id: int,
            text: str,
            view: discord.ui.View | None = None,
            *,
            remember: Hashable | None = None,
        ) -> discord.Message | None: ...
        async def run_name_actions(self) -> None: ...
        async def run_peek_notices(self) -> None: ...
        async def run_web_settings(self) -> None: ...
        async def run_web_rule_sharing(self) -> None: ...
        async def read_put_back_lines(self, guild_id: int) -> None: ...
        async def run_web_report_requests(self) -> None: ...
        async def run_web_sheets(self) -> None: ...
        async def run_web_line_fixes(self) -> None: ...
        async def run_web_restores(self) -> None: ...
        async def tidy_web_restores(self) -> None: ...
        async def keep_web_restores_alive(self) -> None: ...
        async def wait_until_ready(self) -> None: ...
        async def sidebar_send_dm(
            self, user_id: int, text: str, view: discord.ui.View | None = None
        ) -> str: ...

    def recap_tasks(self) -> list[asyncio.Task[None]]:
        """The recap loops that run beside the bot, each only where its worker or store exists.
        Started by `DMBot.setup_hook` with the others."""
        return [
            *(
                [self._watched(self._recap_loop(), "recaps")]
                if self.recap_worker is not None
                else []
            ),
            *(
                [self._watched(self._recap_audio_loop(), "recap-audio")]
                if self.audio_worker is not None
                else []
            ),
            *(
                [
                    self._watched(self._web_restore_loop(), "web-restores"),
                    self._watched(self._web_restore_alive_loop(), "web-restore-queue"),
                ]
                if self.web_restores is not None
                else []
            ),
            *(
                [
                    self._watched(self._recap_request_loop(), "recap-requests"),
                    self._watched(self._recap_voice_loop(), "recap-voice"),
                ]
                if self.recap_requests is not None
                else []
            ),
        ]

    async def _web_restore_loop(self) -> None:
        """Apply the copies DMs uploaded on the website (#2746). Its own loop: a big restore takes
        minutes, and the recap requests, settings and backups must not wait for it. One restore at
        a time in this process, so a row that stopped moving belongs to a bot that stopped."""
        # No restore is judged before Discord has said where the bot is.
        await self.wait_until_ready()
        rounds = 0
        while True:
            if rounds % WEB_RESTORE_TIDY_EVERY == 0:  # about a minute; the first round too
                try:
                    await self.tidy_web_restores()
                except Exception as exc:
                    log.warning("The website restore upkeep failed: %s", type(exc).__name__)
            rounds += 1
            try:
                await self.run_web_restores()
            except Exception as exc:  # the type only: a database error can quote a row
                log.warning("The website restores failed: %s", type(exc).__name__)
            await asyncio.sleep(RECAP_REQUEST_ROUND_S)

    async def _web_restore_alive_loop(self) -> None:
        """Keep the queued website restores showing as alive (#2746) while a long restore runs in
        the other loop: the page calls a queued restore stalled after two minutes."""
        await self.wait_until_ready()
        while True:
            try:
                await self.keep_web_restores_alive()
            except Exception as exc:  # the type only
                log.warning("The website restore queue touch failed: %s", type(exc).__name__)
            await asyncio.sleep(WEB_RESTORE_ALIVE_EVERY_S)

    async def _waiting_recaps(self, table: Table) -> None:
        """A session starts and a finished recap from before is still waiting for the DM (#1474):
        the DM screen says so once, with the same buttons as when it was ready, for at most
        the two newest. Not after a restart in the middle of a session, and nothing for a posted,
        skipped or unfinished recap, or when the campaign's recaps are off. No email."""
        if (
            self.recaps is None
            or not self.settings.recaps_on
            or table.campaign_id is None
            or table.resumed
        ):
            return
        gid, cid = table.guild_id, table.campaign_id
        with log_context(guild_id=gid, campaign_id=cid):
            try:
                campaign = await self.campaigns.get(gid, cid)
                if campaign is None or not campaign.recaps_text:
                    return
                why = await self.recap_audio_why(gid, cid)
                for session_at, title in await self.recaps.waiting_drafts(gid, cid):
                    if self.tables.get(gid) is not table:
                        return  # the session ended meanwhile
                    shown = discord.utils.escape_markdown(title)
                    site = self.settings.site_url
                    await self.post(
                        table.screen_channel_id,
                        f"📜 Last session's recap is still waiting: **{shown}**. "
                        "Nothing has gone to the players." + (f"\n{AUDIO_WHY[why]}" if why else ""),
                        view=ready_view(
                            cid,
                            session_at,
                            site,
                            page_link(site, gid, cid, session_at) if site else "",
                            audio_off=bool(why),
                        ),
                        remember=("recap", cid, session_at),
                    )
            except Exception:
                log.exception("The waiting-recap line failed")

    async def recap_feedback_gate(self, campaign: Campaign, job: Job | None = None) -> str | None:
        """Why a DM's feedback on this recap is not sorted now, in plain words, or None: the
        recap's switch and plan (as at every stage) and a limit per campaign, both before the
        AI call that sorts it costs anything (#2076)."""
        if not await self.recap_allowed(campaign.guild_id, campaign.id, job):
            return recap_feedback_screen.NO_RECAPS
        if not self.recap_feedback_limit.allow(campaign.id, time.monotonic()):
            return recap_feedback.TOO_OFTEN
        return None

    async def sidebar_recap_feedback(self, table: Table, user_id: int, text: str) -> bool:
        """The DM said what they thought of the recap that is waiting for them (#2076): sort it
        and answer in their private messages. False when no recap is waiting or the feedback
        can't be handled (the question then goes the usual way)."""
        if (
            self.recaps is None
            or self.recap_feedback_ai is None
            or table.campaign_id is None
            or not self.settings.recaps_on
        ):
            return False
        campaign = await self.campaigns.get(table.guild_id, table.campaign_id)
        if campaign is None or user_id not in campaign.dm_user_ids:
            return False
        job = await self.recaps.latest_draft(table.guild_id, campaign.id)
        if job is None:
            return False
        stopped = await self.recap_feedback_gate(campaign, job)
        if stopped is not None:
            await self.sidebar_send_dm(user_id, stopped)
            return True
        sorted_ = await recap_feedback.sort_feedback(self.recap_feedback_ai, text)
        if sorted_ is None or not (sorted_.style or sorted_.corrections or sorted_.replies):
            return False  # nothing to act on (or the sort failed): it is an ordinary question

        async def send(message: str, view: discord.ui.View | None) -> Any:
            return await self._dm_message(user_id, message, view)

        await recap_feedback_screen.present(send, sorted_, self.recaps, campaign, job.session_at)
        return True

    async def recap_session(self, table: Table) -> None:
        """After a session, only with DMBOT_RECAPS on and a plan that includes it (#1382): queue
        the recap job and tell the DM, on the DM screen, that an email is coming. The writing is
        done away from the table by `run_recaps`, and the DM reads, edits and posts it on the
        website. Off means off: no client, no stored line read. A plan that can't be checked is
        not run. The log gets counts only."""
        if (
            not self.settings.recaps_on
            or self.recap_worker is None
            or self.recaps is None
            or self.transcripts is None
            or table.campaign_id is None
            or table.transcript_session_id is None
        ):
            return
        gid, cid = table.guild_id, table.campaign_id
        with log_context(guild_id=gid, campaign_id=cid):
            campaign = await self.campaigns.get(gid, cid)
            if campaign is not None and not campaign.recaps_text:
                return  # the DM chose no recaps for this campaign (#1421): nothing is said
            if campaign is None or not await self.plan_allows(
                "recap", gid, campaign, fail_open=False
            ):
                log.info("Recap: not run (the plan lacks it, or can't be checked)")
                return
            session = await self.transcripts.session(gid, table.transcript_session_id)
            if session is None or session.lines == 0:
                return
            dm = campaign.owner_user_id or min(campaign.dm_user_ids, default=0)
            if dm <= 0:
                return
            # A short session asks first (#2104): what the recap would read is not changed.
            lines = await self.transcripts.lines(gid, session.id)
            span = short.span_minutes(lines)
            talk = short.talk_minutes(lines, await self.consent.consenting(gid))
            if short.is_short(span, talk):
                if await self.recaps.get(gid, cid, session.started_at) is not None:
                    return  # a repeat of the end of session, or already decided: ask once only
                sent = await self.post_message(
                    table.screen_channel_id,
                    short.question(span, talk),
                    ask_view(cid, session.started_at),
                    remember=ask_key(cid, session.started_at),
                )
                if sent is None:
                    log.warning("Recap: couldn't ask about a short session")
                    return
                self._short_asks[cid] = session.started_at
                log.info("Recap: asked first (a short session)")  # no numbers of the table's
                return
            await self._hold_or_queue(
                campaign, session.id, session.started_at, table.screen_channel_id, dm
            )

    def _extractor_pending(self) -> bool:
        """Whether the story extractor will run after this session and so may still propose names
        (#2169). The same gate as `extract_claims`."""
        return bool(
            self.settings.story_extractor_on
            and self.extractor_ai is not None
            and self.memory is not None
        )

    async def _next_session_start(self, gid: int, cid: str, began: int) -> int | None:
        """When the campaign's next session began after `began`, or None: names proposed from
        then on belong to that session, not this one (#2169: counted by session)."""
        if self.transcripts is None:
            return None
        return await self.transcripts.next_session_start(gid, cid, began)

    async def _open_name_questions(self, gid: int, cid: str, session_id: str, began: int) -> int:
        """How many name questions from this session still wait for the DM (#2169): names the name
        scan or the extractor proposed from `began` (the session's start) to the start of the
        campaign's next session, that nobody has checked, and its unanswered "Did they mean…?"
        questions. Counts only; nothing is read aloud or logged."""
        total = 0
        if self.memory is not None:
            # A live session owns the names proposed from its start to the next session's start;
            # an imported one (old words, scanned now) owns those proposed while its own scan ran
            # (#2106). Another proposal made in the same seconds (a live scan) is counted too:
            # an over-count only makes the DM answer a little more, and the 3-day cap bounds it.
            scan = await self.import_job.store.window(gid, session_id) if self.import_job else None
            until: int | None
            if scan is not None:
                since, until = scan[0], scan[1] + 1  # (the scan's own seconds, both ends)
            else:
                since, until = began, await self._next_session_start(gid, cid, began)
            proposed = await self.memory.entities(gid, cid, statuses=[PROPOSED])
            later = await self.memory.deferred_names(gid, cid)  # "Later": asked, but not waited on
            total += sum(
                1
                for e in proposed
                if e.created_at >= since
                and (until is None or e.created_at < until)
                and e.id not in later
            )
        if self.heard_questions is not None:
            total += await self.heard_questions.count_for_session(gid, session_id)
        return total

    async def _hold_or_queue(
        self,
        campaign: Campaign,
        session_id: str,
        session_at: int,
        channel_id: int,
        dm: int,
        *,
        extractor_to_come: bool = True,
        quiet: bool = False,
        group_id: str = "",
        say: bool = True,
    ) -> str:
        """Queue the recap, unless the session left name questions open or the story extractor is
        still to run: then hold it (#2169). With questions open the DM is asked at once; with only
        the extractor to come the hold is silent until `recheck_recap_hold` has counted again. The
        recap is never lost: if counting, holding or asking fails, the hold is released into a
        queued recap (or the recap is queued as before when nothing was held). Returns "held"
        (waiting for answers), "queued" or "already" (a repeat: held or queued before).
        `extractor_to_come` is False when the story extractor has already run or will never run
        for the session (the DM pressing "Write a recap" after the end-of-session steps, or an
        imported session): only open questions hold it, never a silent wait. `quiet`: the DM was
        asked about another recap of the same import already, so this hold is made asked but its
        question is not posted again. `group_id`: the import's id; its holds go together.
        `say` False: a recap queued here is not announced (an import round says it once)."""
        gid, cid = campaign.guild_id, campaign.id
        store = self.recap_holds
        if store is not None:
            held = False
            try:
                waiting = await self._open_name_questions(gid, cid, session_id, session_at)
                pending = extractor_to_come and self._extractor_pending()
                if waiting > 0 or pending:
                    if not await store.hold(
                        gid, cid, session_at, session_id=session_id, dm_user_id=dm,
                        channel_id=channel_id, asked=waiting > 0, names_open=waiting,
                        group_id=group_id,
                    ):  # fmt: skip
                        if await store.get(gid, cid, session_at) is not None:
                            return "held"  # it is waiting already: a repeat of the end or press
                        return "already"  # a recap job exists for it
                    held = True
                    if waiting == 0:
                        log.info("Recap: held until the story extractor has run")
                        return "held"
                    sent = quiet or await self.post(
                        channel_id,
                        recap_hold.question_text(waiting),
                        view=hold_view(cid, session_at, group_id),
                        remember=hold_line_key(gid, cid, session_at, group_id),
                    )
                    if sent:
                        log.info("Recap: held for %d name question(s)", waiting)  # a count
                        return "held"
                    log.warning("Recap: couldn't ask about the name questions; writing it now")
            except Exception:
                log.exception("Recap: couldn't hold it for name questions; writing it now")
            if held:  # the DM was not told: a wait nobody knows about is no wait
                try:
                    if await store.release(gid, cid, session_at) == recap_hold.QUEUED:
                        if say:
                            await self._say_writing(campaign, channel_id, session_at)
                        return "queued"
                except Exception:
                    log.exception("Recap: couldn't release a hold nobody was told about")
        return (
            "queued"
            if await self._queue_recap(campaign, session_at, channel_id, dm, say=say)
            else "already"
        )

    async def recheck_recap_hold(self, table: Table) -> None:
        """After the story extractor (#2169): count the name questions again for a recap held
        while it ran, under the same rules as every round. A recap that was not held is left
        alone."""
        store = self.recap_holds
        if (
            store is None
            or self.transcripts is None
            or table.campaign_id is None
            or table.transcript_session_id is None
        ):
            return
        gid, cid = table.guild_id, table.campaign_id
        session = await self.transcripts.session(gid, table.transcript_session_id)
        if session is None:
            return
        held = await store.get(gid, cid, session.started_at)
        if held is None or held.asked:
            return
        await self._release_one(store, gid, held, extractor_done=True)

    async def release_recap_holds(self, gid: int) -> None:
        """A round's check: a held recap whose questions are all answered is written (#2169). The
        hold and the job are made in one transaction, so answering never writes a recap twice.
        A campaign that is gone, or whose DM switched recaps off, drops the hold and writes
        nothing; a plan that cannot be checked right now only waits for the next round."""
        store = self.recap_holds
        if store is None:
            return
        holds = await store.held(gid)
        live = {(gid, h.campaign_id, h.group_id) for h in holds}
        for key in [k for k in self._group_started if k[0] == gid and k not in live]:
            del self._group_started[key]  # an import with nothing held here needs no start time
        for held in holds:
            # The holds of one import go together (#2106): the oldest speaks for the group (the
            # question, the reminder, the expiry line); the others only follow their own answers.
            group = recap_hold.group_of(holds, held)
            try:
                await self._release_one(store, gid, held, group=group or [held])
            except Exception:  # one campaign's trouble does not hold up the others
                log.exception("Recap: couldn't check a held recap")

    async def _release_one(
        self,
        store: recap_hold.HoldStore,
        gid: int,
        held: recap_hold.Hold,
        *,
        extractor_done: bool = False,
        group: list[recap_hold.Hold] | None = None,
    ) -> None:
        """One held recap, the same rules whether the round or the end of the extractor asks: a
        campaign that is gone or has recaps off drops it; a silent hold waits for the extractor
        (`extractor_done`, or `QUIET_WAIT_S` having passed); questions open ask the DM once; none
        open writes the recap, if the plan allows it. A question that cannot be posted is not
        waited on: the recap is written instead; if the plan cannot be checked at that moment the
        hold stays, silent, and the next round tries again."""
        campaign = await self.campaigns.get(gid, held.campaign_id)
        if campaign is None or not campaign.recaps_text:  # the DM's own choice: no recap
            await store.release(gid, held.campaign_id, held.session_at, queue=False)
            await self._close_hold_lines(gid, [held], recap_hold.LINE_NO_RECAP)
            return
        with log_context(guild_id=gid, campaign_id=held.campaign_id):
            members = group or [held]
            lead = held.session_at == min(m.session_at for m in members)  # speaks for its group
            age = store.now() - held.created_at  # the store's clock wrote created_at
            if not held.asked and not extractor_done and age < recap_hold.QUIET_WAIT_S:
                return  # the story extractor is still running; it counts again when done
            waiting = await self._open_name_questions(
                gid, held.campaign_id, held.session_id, held.session_at
            )
            # The website's count follows every count, 0 included: when the last answer is in but
            # the plan blocks the recap, "answer N" must not stay on the page (#2174).
            await store.set_names_open(gid, held.campaign_id, held.session_at, waiting)
            if age >= recap_hold.HOLD_MAX_S:
                # A wait nobody answers cannot go on for ever (#2174): the recap is written with
                # the names as they are. The note is only for questions the DM was asked and that
                # are still open, counted just now (an answer a moment before the deadline is not
                # "no answer", and a hold never asked, such as one the bot was down for, says
                # nothing of the sort). The plan is still checked; when it cannot be, the hold is
                # kept and tried again each round.
                if not await self.recap_allowed(gid, held.campaign_id, None):
                    log.info("Recap: a hold past its limit is kept (plan not allowed or unchecked)")
                    return
                note = recap_hold.EXPIRED_NOTE if waiting and held.asked else ""
                if held.group_id:
                    if not lead:
                        return  # the oldest of the import ends them all, with one line
                    ended: list[recap_hold.Hold] = []
                    made = []
                    async with self._group_lock(held.group_id):
                        for m in members:
                            result = await store.release(gid, m.campaign_id, m.session_at)
                            if result != recap_hold.GONE:  # (over all the same: a job existed)
                                ended.append(m)
                            if result == recap_hold.QUEUED:
                                made.append(m.session_at)
                        await self._close_hold_lines(gid, ended, recap_hold.LINE_EXPIRED)
                        # Recaps answers released earlier were not told: they are told here too.
                        made += self._group_made.pop(held.group_id, [])
                    if made:
                        await self._say_group_writing(
                            campaign, held.channel_id, len(made), note=note, session_at=made[-1]
                        )
                    return
                result = await store.release(gid, held.campaign_id, held.session_at)
                if result != recap_hold.GONE:
                    await self._close_hold_lines(gid, [held], recap_hold.LINE_EXPIRED)
                if result == recap_hold.QUEUED:
                    await self._say_writing(campaign, held.channel_id, held.session_at, note=note)
                return
            if waiting:
                if held.asked:
                    # One reminder after a day, claimed first so the round and a restart never
                    # send it twice; any failure to post gives the claim back for the next round.
                    # A group is reminded once, whoever of it is oldest by then: the claim is made
                    # for every member, so one released by answers leaves the rest marked.
                    if (
                        lead
                        and age >= recap_hold.HOLD_REMIND_S
                        and not any(m.reminded for m in members)
                    ):
                        for m in members:
                            if m is not held:  # the whole group's questions, as Discord shows them
                                waiting += await self._open_name_questions(
                                    gid, m.campaign_id, m.session_id, m.session_at
                                )
                        await self._remind(store, gid, held, waiting, age, members)
                    if lead:
                        await self._email_hold(campaign, gid, held, members)
                    return
                # Claim first, so the round and the end of the extractor never both ask.
                if not await store.mark_asked(gid, held.campaign_id, held.session_at):
                    return  # the other one is asking
                if await self.post(
                    held.channel_id,
                    recap_hold.question_text(waiting),
                    view=hold_view(held.campaign_id, held.session_at, held.group_id),
                    remember=hold_line_key(gid, held.campaign_id, held.session_at, held.group_id),
                ):
                    log.info("Recap: held for %d name question(s)", waiting)  # a count
                    return
                # Not told: a wait nobody knows about is no wait. Give the claim back and fall
                # through to write it (if the plan cannot be checked now, the hold stays silent
                # and the next round asks again).
                await store.unmark_asked(gid, held.campaign_id, held.session_at)
            if not await self.recap_allowed(gid, held.campaign_id, None):
                return  # the plan could not be checked, or lacks it: kept, asked again next round
            told: list[int] = []
            async with self._group_lock(held.group_id):
                result = await store.release(gid, held.campaign_id, held.session_at)
                if result != recap_hold.GONE:
                    await self._close_hold_lines(gid, [held], recap_hold.LINE_ANSWERED)
                if result == recap_hold.QUEUED and held.group_id:
                    # An import's recaps start one by one: the last says so, once. Recording and
                    # claiming (the pop) are under the import's lock with the release, so no press
                    # or expiry can pop the list between them.
                    self._group_made.setdefault(held.group_id, []).append(held.session_at)
                    if not recap_hold.group_of(await store.held(gid), held):
                        told = self._group_made.pop(held.group_id, [])
            if told:
                await self._say_group_writing(
                    campaign, held.channel_id, len(told), session_at=told[-1]
                )
            elif result == recap_hold.QUEUED and not held.group_id:
                await self._say_writing(campaign, held.channel_id, held.session_at)

    def _group_lock(self, group_id: str) -> contextlib.AbstractAsyncContextManager[object]:
        """The import's lock; a hold with no import needs none."""
        if not group_id:
            return contextlib.nullcontext()
        return self._group_locks.setdefault(group_id, asyncio.Lock())

    async def _close_hold_lines(
        self,
        gid: int,
        holds: Sequence[recap_hold.Hold],
        outcome: str,
        *,
        pressed_id: int | None = None,
    ) -> None:
        """Take the buttons off the question and the reminder of holds that are over, and say how
        they ended (#2174), so no old line offers buttons that only answer "already". Called after
        the release. An import posts one line for all its sessions (under the group's key), so it
        comes off only when none of its sessions is still held. A failed edit is logged inside and
        goes no further."""
        remaining: list[recap_hold.Hold] | None = []
        if self.recap_holds is not None and any(h.group_id for h in holds):
            try:
                remaining = await self.recap_holds.held(gid)
            except Exception:
                log.exception("Recap: couldn't tell whether an import still waits")
                remaining = None  # its line stays (it comes off at a restart); a lone hold's goes
        for h in holds:
            if h.group_id and (remaining is None or recap_hold.group_of(remaining, h)):
                continue  # the import still waits on other sessions: its line stays true
            await self.decision_lines.close(
                hold_line_key(gid, h.campaign_id, h.session_at, h.group_id),
                outcome,
                pressed_id=pressed_id,
            )

    async def _remind(
        self,
        store: recap_hold.HoldStore,
        gid: int,
        held: recap_hold.Hold,
        waiting: int,
        age: float,
        members: list[recap_hold.Hold] | None = None,
    ) -> None:
        """The one reminder of a hold (#2174). The claim comes first so two rounds never both send
        it; a post that fails or raises gives the claim back, so the reminder is not lost. An
        import's holds are claimed together (`members`), so it is one reminder for the group."""
        group = members or [held]
        if not await store.mark_reminded(gid, held.campaign_id, held.session_at):
            return  # another round has it
        for m in group:
            if m is not held:
                await store.mark_reminded(gid, m.campaign_id, m.session_at)
        sent = False
        try:
            sent = bool(
                await self.post(
                    held.channel_id,
                    recap_hold.reminder_text(waiting, recap_hold.HOLD_MAX_S - age),
                    view=hold_view(held.campaign_id, held.session_at, held.group_id),
                    remember=hold_line_key(gid, held.campaign_id, held.session_at, held.group_id),
                )
            )
        finally:
            if not sent:
                for m in group:
                    await store.unmark_reminded(gid, m.campaign_id, m.session_at)

    async def _email_hold(
        self,
        campaign: Campaign,
        gid: int,
        held: recap_hold.Hold,
        members: list[recap_hold.Hold],
    ) -> None:
        """A held recap also emails the DMs of the campaign, the same people as the ready email
        (#2237), at most once a calendar day from the first day on, when the campaign's recap
        emails are on (#2174). The mailer's once-per-kind record is the daily limit, so rounds
        and restarts never double it (the kind is the calendar day, so a restart changes
        nothing); a failure is logged and the next round tries again. The count is the whole
        import's open questions. The first day is counted from the earliest hold the import has
        had this run, so answering its oldest session does not start the wait over; the lead only
        sends (the oldest remaining speaks for the import)."""
        if not campaign.recaps_email or self.mailer is None or self.recaps is None:
            return
        if self.recap_holds is None:
            return
        started: float = min(m.created_at for m in members)
        if held.group_id:
            key = (gid, held.campaign_id, held.group_id)
            started = min(started, self._group_started.get(key, started))
            self._group_started[key] = started
        now = self.recap_holds.now()  # the store's clock wrote created_at
        if now - started < recap_hold.HOLD_REMIND_S:
            return
        try:
            waiting = 0
            for m in members:
                waiting += await self._open_name_questions(
                    gid, m.campaign_id, m.session_id, m.session_at
                )
        except Exception:
            log.exception("Recap: couldn't count the questions for the email")
            return
        for user_id in sorted(campaign.dm_user_ids):
            try:
                await remind_the_dm(
                    self.mailer,
                    self.recaps,
                    self.settings.site_url,
                    gid,
                    held.campaign_id,
                    user_id,
                    held.session_at,
                    waiting,
                    int(now // 86400),  # the calendar day: a restart the same day sends no second
                    held.group_id,
                )
            except Exception:
                log.exception("Recap: couldn't email the reminder")

    async def recap_hold_write_now(
        self,
        gid: int,
        cid: str,
        session_at: int,
        *,
        group_id: str = "",
        pressed_id: int | None = None,
    ) -> str:
        """The DM said to write the held recap with what DMbot has (#2169): the same gates as after
        any session. "queued", "already" (no hold: written or decided before) or "refused". The
        "writing it now" line goes to the DM screen the hold stored, wherever the button was
        pressed."""
        if self.recap_holds is None or self.recaps is None or self.recap_worker is None:
            return "refused"
        if not self.settings.recaps_on:
            return "off"  # switched off for every server: its own words
        campaign = await self.campaigns.get(gid, cid)
        if campaign is None or not await self.recap_allowed(gid, cid, None):
            return "refused"
        held = await self.recap_holds.get(gid, cid, session_at)
        if held is None:
            if not group_id:
                return "already"
            # The session this button was posted for is answered, but the import still waits:
            # the press reaches the whole import all the same (its button names the group).
            group = [
                h
                for h in await self.recap_holds.held(gid)
                if h.campaign_id == cid and h.group_id == group_id
            ]
            if not group:
                return "already"
            held = group[0]
        else:
            group = [held]
            if held.group_id:  # one press writes all of an import's held recaps (#2106)
                group = recap_hold.group_of(await self.recap_holds.held(gid), held)
        ended: list[recap_hold.Hold] = []
        made = []
        async with self._group_lock(held.group_id):
            for h in group:
                result = await self.recap_holds.release(gid, cid, h.session_at)
                if result != recap_hold.GONE:
                    ended.append(h)
                if result == recap_hold.QUEUED:
                    made.append(h.session_at)
            await self._close_hold_lines(gid, ended, recap_hold.LINE_WRITING, pressed_id=pressed_id)
            if held.group_id:  # the ones answers released earlier were not told: tell them too
                made += self._group_made.pop(held.group_id, [])
        if not made:
            return "already"
        await self._say_group_writing(campaign, held.channel_id, len(made), session_at=made[-1])
        return "queued"

    async def _queue_recap(
        self, campaign: Campaign, session_at: int, channel_id: int, dm: int, *, say: bool = True
    ) -> bool:
        """Queue the recap of a session and say so on the DM screen. False when there already is
        one (a restart, or the session ended twice)."""
        if self.recaps is None:
            return False
        gid, cid = campaign.guild_id, campaign.id
        if not await self.recaps.enqueue(gid, cid, session_at, dm_user_id=dm):
            return False
        if say:
            await self._say_writing(campaign, channel_id, session_at)
        return True

    async def recap_unchecked(self, gid: int, cid: str, job: Any) -> None:
        """A recap that DMbot could not finish checking for three days (#2255): said once on the
        DM screen, with the link to the recap page. Nothing goes to the table. Fixed words only."""
        campaign = await self.campaigns.get(gid, cid)
        if campaign is None or campaign.dm_screen_channel_id is None:
            return
        link = page_link(self.settings.site_url, gid, cid, job.session_at)
        where = f"\nYour recap page: {link}" if self.settings.site_url else ""
        await self.post(
            campaign.dm_screen_channel_id,
            f"{critics.UNCHECKED_TOLD}{where}",
            view=again_view(cid, job.session_at, keep=job.keeps_previous),
        )

    async def _say_writing(
        self, campaign: Campaign, channel_id: int, session_at: int, note: str = ""
    ) -> None:
        """Tell the DM, on the DM screen, that the recap is being written (`note`: why, when it was
        not asked for, such as a hold that ran out)."""
        link = page_link(self.settings.site_url, campaign.guild_id, campaign.id, session_at)
        where = f"\nYour recap page: {link}" if self.settings.site_url else ""
        ready = (
            "You'll get an email when it's ready."
            if campaign.recaps_email
            else "Find it on your recap page when it's ready."
        )
        lead = f"{note} " if note else ""
        await self.post(
            channel_id,
            f"📜 {lead}Writing a recap of this session. {ready} "
            f"Nothing goes to the table until you say so.{where}",
        )

    async def _say_group_writing(
        self,
        campaign: Campaign,
        channel_id: int,
        count: int,
        note: str = "",
        *,
        session_at: int = 0,
        also: str = "",
    ) -> None:
        """One line for the recaps of an import that start together (#2106), not one for each.
        A single one (`session_at`) is told like any recap, never as "1 recaps". `also` is the
        import's other news, the recaps that were not written, so the DM gets one line, not two."""
        if count == 1 and session_at and not also:
            await self._say_writing(campaign, channel_id, session_at, note=note)
            return
        ready = (
            "You'll get an email as each is ready."
            if campaign.recaps_email
            else "Find them on your recap page when they're ready."
        )
        lead = f"{note} " if note else ""
        await self.post(
            channel_id,
            f"📜 {lead}Writing {count} {'recap' if count == 1 else 'recaps'} from your imported "
            f"sessions. {ready} Nothing goes to the table until you say so."
            + (f"\n{also}" if also else ""),
        )

    async def recap_short_expired(self, gid: int, cid: str, session_at: int) -> bool:
        """True once a newer session of the campaign has started or been stored (#2104): the
        question about a short session is then out of date. Checked on every press, so it holds
        after a restart too."""
        if self.transcripts is None:
            return False
        newest = await self.transcripts.sessions(gid, cid, limit=1)
        return bool(newest) and newest[0].started_at > session_at

    async def recap_write_anyway(self, gid: int, cid: str, session_at: int, channel_id: int) -> str:
        """The DM said to write the recap of a short session. The same gates as after any session:
        the switch and the plan (closed when they can't be checked), and the hold for name answers.
        "queued", "held" (waiting for the DM's name answers), "already" (there is a recap job for
        it) or "refused"."""
        if self.recap_worker is None or self.recaps is None or self.transcripts is None:
            return "refused"
        campaign = await self.campaigns.get(gid, cid)
        if campaign is None or not await self.recap_allowed(gid, cid, None):
            return "refused"
        dm = campaign.owner_user_id or min(campaign.dm_user_ids, default=0)
        if dm <= 0:
            return "refused"
        # Like any session, it waits for the DM's name answers first (#2174).
        session_id = next(
            (x.id for x in await self.transcripts.sessions(gid, cid, limit=1)
             if x.started_at == session_at), None,
        )  # fmt: skip
        if session_id is None:
            log.warning("Recap: the session to hold was not found; writing it without a hold")
            return (
                "queued"
                if await self._queue_recap(campaign, session_at, channel_id, dm)
                else "already"
            )
        return await self._hold_or_queue(
            campaign, session_id, session_at, channel_id, dm, extractor_to_come=False
        )

    async def _expire_short_asks(self, table: Table) -> None:
        """A new session of the campaign has started: the open question about its last short
        session says it expired and loses its buttons (#2104). Memory only; after a restart the
        press itself finds it out of date."""
        if table.campaign_id is None:
            return
        asked = self._short_asks.pop(table.campaign_id, None)
        if asked is not None:
            await self.decision_lines.close(ask_key(table.campaign_id, asked), EXPIRED)

    async def recap_secret_names(
        self, campaign: Campaign, session_at: int | None = None
    ) -> list[str] | None:
        """Every secret name of the campaign, read fresh (a name may have been made secret since
        the recap was written), or None when they can't be read: then nothing is posted or
        saved, since a secret name couldn't be kept out. With `session_at`, a name the players
        haven't been shown yet counts too unless the table said it in that session."""
        if self.lookup is None:
            return None
        try:
            lookup = await self.lookup.get(campaign.guild_id, campaign.id)
        except Exception:
            log.exception("Recap: couldn't read the campaign's secret names")
            return None
        return recap_name_sets(
            lookup,
            await recap_said(self.transcripts, campaign.guild_id, campaign.id, session_at),
            await self._recanted(campaign.guild_id, campaign.id, session_at),
        )[1]

    async def recap_allowed_names(
        self, campaign: Campaign, session_at: int | None = None
    ) -> list[str]:
        """The campaign's confirmed names that are not secret and not someone at the table, read
        fresh, for the secret check at Post (#2087): "Arcane Brotherhood" inside the confirmed
        "Arcane Brotherhood Hall" is the hall, as the draft check already reads it. Empty when they
        can't be read: the check is then stricter, never looser."""
        if self.lookup is None:
            return []
        try:
            lookup = await self.lookup.get(campaign.guild_id, campaign.id)
        except Exception:
            log.exception("Recap: couldn't read the campaign's confirmed names")
            return []
        return recap_name_sets(
            lookup,
            await recap_said(self.transcripts, campaign.guild_id, campaign.id, session_at),
            await self._recanted(campaign.guild_id, campaign.id, session_at),
        )[0]

    async def recap_inputs(self, gid: int, cid: str, job: Job) -> Inputs | None:
        """What the recap stages read for one job (see `dmbot.recaps.inputs`)."""
        if self.transcripts is None or self.lookup is None or self.recaps is None:
            return None
        found = await load_inputs(
            campaigns=self.campaigns,
            lookup=self.lookup,
            transcripts=self.transcripts,
            recaps=self.recaps,
            clocks=self.clocks,
            guild_id=gid,
            campaign_id=cid,
            session_at=job.session_at,
            styles=self.recap_styles,
            notes=self.recap_notes,
            importance=self.importance,
            recanted=await self._recanted(gid, cid, job.session_at),
            relation_ties=await self._relation_ties(gid, cid),
            # The earlier session is read for the split stage only, at the start of a try.
            with_earlier=job.split is None and job.window_done == 0,
        )
        # What the DM corrected in this one recap, for this try only (#2076).
        if found is None:
            return None
        threads = (
            await self._open_threads(gid, cid)
            if self.settings.recap_writer_reads == "transcript"
            else ()
        )
        return dataclasses.replace(found, corrections=job.corrections, threads=threads)

    async def recap_story(self, gid: int, cid: str, job: Job, seeds: Sequence[str]) -> list[Memory]:
        """The confirmed story memory around the beats' names for PlotBot's notes (#2254). Two
        reads of one campaign: everything the DM confirmed (the DM screen's view), and what the
        players' view of it holds; an item is `known` only if it is in both. That is the gate
        between "keep this" and "this may be explained"; it is made here, in code."""
        if self.memory is None or not seeds:
            return []
        if self.importance is not None:
            # The names that matter most to the story come first (#2411), so the recall's budget
            # is spent on them; this only orders names the beats already had.
            scores = await self.importance.get(gid, cid)
            seeds = sorted(dict.fromkeys(seeds), key=lambda n: -scores.get(name_key(n), 0.0))
        service = RecallService(self.memory)
        everything = await service.recall(
            gid, cid, seeds=seeds, audience=DM_SCREEN, budget=RECAP_RECALL_BUDGET,
            confirmed_only=True,
        )  # fmt: skip
        players = await service.recall(
            gid, cid, seeds=seeds, audience=PLAYERS, budget=RECAP_RECALL_BUDGET
        )
        # What the writer's side may be told about an item the players know is the players' own
        # text for it (no DM note, no condition); the DM-screen text is only for items they do
        # not know, which are never explained.
        theirs = {i.id: i.text for i in players.items}
        return [
            Memory(i.id, theirs[i.id], True) if i.id in theirs else Memory(i.id, i.text, False)
            for i in everything.items
        ]

    async def recap_allowed(self, gid: int, cid: str, job: Job | None) -> bool:
        """The switch and the plan, checked at every stage and closed when they can't be."""
        if not self.settings.recaps_on:
            return False
        campaign = await self.campaigns.get(gid, cid)
        return (
            campaign is not None
            and campaign.recaps_text  # the DM's choice can only turn it off (#1421)
            and await self.plan_allows("recap", gid, campaign, fail_open=False)
        )

    async def recap_approval_problems(self, campaign: Campaign, job: Job) -> list[str]:
        """What stops the DM approving this text (#2083), in plain words: a cue that is wrong,
        a secret name in it, or a player no longer recorded. The checks run again at every
        press, as at post time. Empty: the text may be approved. Counts only in the log."""
        problems = [p.words() for p in cues.cue_problems(job.summary)]
        secrets = await self.recap_secret_names(campaign, job.session_at)
        if secrets is None:
            problems.append("DMbot couldn't check your secret names just now. Try again.")
        elif names_a_secret(
            recap_post.to_the_table(job),
            secrets,
            await self.recap_allowed_names(campaign, job.session_at),
        ):
            problems.append("It names something you keep secret. Change the text first.")
        if not set(job.speakers) <= set(await self.consent.agreed(campaign.guild_id)):
            problems.append(
                "A player stopped being recorded since this was written, so it can't go ahead."
            )
        log.info("Recap approval: %d problem(s)", len(problems))
        return problems

    async def recap_share_link(
        self, campaign: Campaign, session_at: int, *, new: bool = False
    ) -> tuple[str | None, str]:
        """The public link of a recap for "Share a recap" (#2118): (token, ""), or (None, why in
        plain words). A recap not posted yet is posted first, the same move and checks as Post
        (its approval, the secret names, people still recorded, all read again); a posted one has
        the secret and people checks read again before its link is handed out. The token is a
        secret link: it is never logged."""
        store = self.recaps
        if store is None:
            return None, "Recaps aren't available right now. Try again in a minute."
        gid, cid = campaign.guild_id, campaign.id
        job = await store.get(gid, cid, session_at)
        where = (
            f": {self.settings.site_url.rstrip('/')}/account/recaps"
            if self.settings.site_url
            else ""
        )
        if job is None or job.state != "ready":
            return None, "This recap isn't ready yet. Try again in a minute."
        was_draft = job.status == "draft"
        if was_draft:
            if not job.approved:  # a draft that was never approved is never shared
                return (
                    None,
                    f"Read and approve this recap on your recap page first{where}. "
                    "Nothing was shared.",
                )
            if job.audio_choice != "text":
                # Approved for narration or music: it is posted after the audio is made and
                # reviewed, never by Share, so say what to do instead of a failure that can't mend.
                return (
                    None,
                    "This recap is waiting for its narration. Review it on your recap page, "
                    f"then share{where}. Nothing was shared.",
                )
            # The approval is checked again in SQL, in the same statement that moves it on, so a
            # text edited since the check above cannot slip through; of two presses, one wins.
            if await store.decide(gid, cid, session_at, "post", require_approved=True):
                await self.post_recaps(gid)  # its own checks; a failed one puts it back as a draft
            job = await store.get(gid, cid, session_at)
            if job is None or job.status == "posting":
                return None, "It's being posted right now. Try again in a moment."
            if job.status == "draft":
                why = job.last_error or "It couldn't be posted just now."
                return (
                    None,
                    f"Not posted: {why} Nothing was shared. Try again, or check your recap page.",
                )
        if job.status == "posting":
            return None, "It's being posted right now. Try again in a moment."
        if job.status != "posted":
            return None, "You skipped this recap, so it can't be shared."
        if was_draft:  # it is posted now: the old line's Post and Skip buttons would mislead
            await self.decision_lines.close(("recap", cid, session_at), "Posted.")
        secrets = await self.recap_secret_names(campaign, session_at)
        if secrets is None:
            return None, "DMbot couldn't check your secret names just now. Try again."
        text = "\n".join([job.title, strip_cues(job.summary), *job.housekeeping])
        if names_a_secret(text, secrets, await self.recap_allowed_names(campaign, session_at)):
            return (
                None,
                "This recap names a secret of yours, so it wasn't shared. "
                "Edit it on your recap page, then try again.",
            )
        if not set(job.speakers) <= set(await self.consent.consenting(gid)):
            return (
                None,
                "A player has stopped being recorded, so this recap can't be shared as "
                "written. Nothing was shared. Write it again from your recap page.",
            )
        token = await store.share_token(gid, cid, session_at, new=new)
        if token is None:
            return None, "This recap isn't posted, so it can't be shared. Try again."
        return token, ""

    async def recap_audio_wanted(self, gid: int, cid: str, job: Job) -> bool:
        """Whether the audio is to be made for a recap that has just become ready: the switch,
        the key and voice, and a plan that includes spoken recaps (closed when it can't be
        checked). Without them the recap is text-only and nothing fails."""
        if not self.settings.recap_audio_on:
            return False
        if not await self.recap_audio_switched(gid, cid, job):
            return False
        return await self.recap_audio_allowed(gid, cid, job)

    async def recap_text_allowance(self, gid: int, cid: str, job: Job) -> str | None:
        """Why this recap can't be written under the campaign's monthly allowance, or None
        (#1428). Raises if it can't be told: then nothing is spent."""
        return await self._recap_allowance(gid, cid, audio=False)

    async def recap_audio_allowance(self, gid: int, cid: str, job: Job) -> str | None:
        return await self._recap_allowance(gid, cid, audio=True)

    async def _recap_allowance(self, gid: int, cid: str, *, audio: bool) -> str | None:
        """The month's count against the allowance. Free access and grants are not limited. On
        refusal the DM screen says so, with the day the next one is."""
        message = await self._recap_refusal(gid, cid, audio=audio)
        if message is None:
            return None
        campaign = await self.campaigns.get(gid, cid)
        if campaign is not None and campaign.dm_screen_channel_id is not None:
            await self.post(campaign.dm_screen_channel_id, f"📜 Recap: {message}")
        return message

    async def _recap_refusal(self, gid: int, cid: str, *, audio: bool) -> str | None:
        """The same check with no side effects: the refusal's words, or None when one more is
        allowed. The greyed audio button asks this, so asking never posts to the DM screen."""
        if self.recaps is None:
            raise RuntimeError("no recap store")
        campaign = await self.campaigns.get(gid, cid)
        if campaign is None:
            return None  # the other gates deal with a campaign that is gone
        now = time.time()
        owner = campaign.owner_user_id
        if owner is not None and self.meter is not None:
            access = await self.meter.access(gid, owner, int(now))
            if access.kind in ("free", "grant"):
                return None
        used = await self.recaps.used_in_month(gid, cid, now)
        count, limit = (
            (used.narrated, self.settings.recap_audio_per_month)
            if audio
            else (used.recaps, self.settings.recaps_per_month)
        )
        if count < limit:
            return None
        return recap_allowance.audio_refusal(now) if audio else recap_allowance.text_refusal(now)

    async def recap_audio_switched(self, gid: int, cid: str, job: Job) -> bool:
        """The DM's own choice to have the narration and music (#1421); it can only turn the
        audio off, never on past the plan."""
        campaign = await self.campaigns.get(gid, cid)
        return campaign is not None and campaign.recaps_audio

    async def recap_plan_facts(self, campaign: Campaign) -> PlanFacts:
        """What the plan allows for recaps, for the DM's Recaps panel (#1421): recaps at all, the
        narration and music (only when they are switched on in DMbot too), and whether those
        come with the plan or as an add-on."""
        gid = campaign.guild_id
        text = await self.plan_allows("recap", gid, campaign, fail_open=False)
        audio = (
            text
            and self.settings.recap_audio_on
            and await self.plan_allows("recap_audio", gid, campaign, fail_open=False)
        )
        how = ""
        owner = campaign.owner_user_id
        if audio and self.settings.enforce_plans and self.meter is not None and owner is not None:
            try:
                access = await self.meter.access(gid, owner, int(time.time()))
                how = audio_how(access.plan_name, access.kind, RECAP_AUDIO_PLANS)
            except Exception:
                log.exception("Couldn't tell whether spoken recaps are part of the plan")
        return PlanFacts(text, audio, how)

    async def recap_audio_why(self, gid: int, cid: str) -> str:
        """Why audio can't be made for this campaign's recaps now (#2420), for the greyed button:
        "not_set_up" (this server has no narration, or the DM switched it off), "unavailable" (the
        plan has none, or the month's is used up: a co-DM is never told the owner's plan, #1470),
        or "" when it can be made. The audio step checks all of it again before any paid call."""
        if not self.settings.recap_audio_on:
            return "not_set_up"
        campaign = await self.campaigns.get(gid, cid)
        if campaign is None or not campaign.recaps_audio:
            return "not_set_up"
        if not await self.plan_allows("recap_audio", gid, campaign, fail_open=False):
            return "unavailable"
        try:
            if await self._recap_refusal(gid, cid, audio=True) is not None:
                return "unavailable"
        except Exception:  # not told: the button stays off rather than promise what may not come
            log.exception("Recap: couldn't read the audio allowance")
            return "unavailable"
        return ""

    async def recap_audio_allowed(self, gid: int, cid: str, job: Job) -> bool:
        campaign = await self.campaigns.get(gid, cid)
        return campaign is not None and await self.plan_allows(
            "recap_audio", gid, campaign, fail_open=False
        )

    async def recap_audio_inputs(self, gid: int, cid: str, job: Job) -> AudioInputs | None:
        """What the audio step reads: the campaign's names, secret ones fresh. None when they
        can't be read (then nothing is sent, since a secret name couldn't be kept out)."""
        if self.lookup is None:
            return None
        try:
            lookup = await self.lookup.get(gid, cid)
        except Exception:
            log.exception("Recap audio: couldn't load the campaign's names")
            return None
        # The same lists as the written recap's check (#2050), so the two locks agree: a name the
        # players haven't been shown yet is secret unless the table said it in this session.
        _, secret = recap_name_sets(
            lookup,
            await recap_said(
                getattr(self, "transcripts", None), gid, cid, getattr(job, "session_at", None)
            ),
            await self._recanted(gid, cid, getattr(job, "session_at", None)),
        )
        hidden = set(secret)
        return AudioInputs(
            secret_names=secret,
            # The DM's "said as" spelling (#1419); a secret name never has one, and the names are
            # read fresh all the same, so a secret one is always known to the filter.
            names=[
                audio_names.SpokenName(
                    n.text,
                    n.said_as,
                    secret=n.secret or n.text in hidden,
                    confirmed=n.confirmed,
                    person=n.person,
                )
                for n in lookup.names
            ],
        )

    async def recap_told(self, gid: int, cid: str, job: Job) -> None:
        """Tell the DM that their recap is ready, or could not be written. A ready recap goes on
        the DM screen with buttons (read it, post it, skip it, tell DMbot what you thought; #1455),
        and is emailed unless the DM chose not to be (#1421). A failed one is emailed, or said on
        the DM screen."""
        campaign = await self.campaigns.get(gid, cid)
        link = (
            page_link(self.settings.site_url, gid, cid, job.session_at)
            if self.settings.site_url
            else ""
        )
        if campaign is not None and campaign.dm_screen_channel_id is not None:
            if job.state == "ready":
                try:
                    why = await self.recap_audio_why(gid, cid)  # the audio button's greyed reason
                except Exception:  # the recap still goes up, with the button off
                    log.exception("Recap: couldn't tell why audio is off")
                    why = "unavailable"
                await self.post(
                    campaign.dm_screen_channel_id,
                    ready_text(job.title, link, AUDIO_WHY[why] if why else ""),
                    view=ready_view(
                        cid, job.session_at, self.settings.site_url, link, audio_off=bool(why)
                    ),
                    remember=("recap", cid, job.session_at),
                )
            elif not campaign.recaps_email:
                where = f": {link}" if link else " on the website"
                await self.post(
                    campaign.dm_screen_channel_id,
                    "📜 I couldn't write this session's recap. "
                    f"Try again from your recap page{where}"
                    + (
                        "\nThe recap you had before is kept: press ↩️ to bring it back."
                        if job.keeps_previous
                        else ""
                    ),
                    view=again_view(cid, job.session_at, keep=job.keeps_previous),
                )
        if campaign is not None and job.state == "ready" and job.to_confirm:
            await self._queue_recap_names(gid, cid, job, campaign)
        if campaign is not None and not campaign.recaps_email:
            return  # no email by the DM's choice
        if self.mailer is not None and self.recaps is not None:
            await tell_the_dm(
                self.mailer,
                self.recaps,
                self.settings.site_url,
                gid,
                cid,
                job,
                campaign.dm_user_ids if campaign is not None else (),
            )

    async def _queue_recap_names(self, gid: int, cid: str, job: Job, campaign: Campaign) -> None:
        """Names the recap kept that DMbot didn't know (#2027) go into the after-session name
        check as suggestions, and the DM screen says so at once, with the check's button. They are
        only proposed: nothing is written to story memory until the DM says yes. The names
        themselves stay on the DM's recap page, so nothing reaches players who peek. Once only
        (`names_state`); a failure costs the notice, never the recap."""
        if self.memory is None or self.recaps is None:
            return
        try:
            if not await self.recaps.set_names_state(gid, cid, job.session_at, 1, only_from=0):
                return
            known = set(await self.memory.known_keys(gid, cid))
            # Read fresh: a name may have been made secret since the recap was written.
            secret = await self.recap_secret_names(campaign)
            if secret is None:
                return  # a secret name couldn't be kept out: queue nothing
            added = 0
            for name in job.to_confirm:
                if name_key(name) in known or names_a_secret(name, secret, []):
                    continue  # the list has it (or the DM said it is not a name) since
                try:
                    await self.memory.add_entity(
                        gid,
                        cid,
                        type="concept",
                        name=name,
                        source="scan",
                        description="Used in a recap",
                    )
                except MemoryRuleError as exc:
                    log.info("Skipped a name from the recap: %s", exc)
                    continue
                added += 1
            await self._link_recap_names(gid, cid, job)
            if added and campaign.dm_screen_channel_id is not None:
                await self.post(
                    campaign.dm_screen_channel_id, recap_names_text(added), view=review_view(cid)
                )
            if added:
                await self._private_names_note(gid, cid, job.dm_user_id, added)
            if self.lookup is not None and not any(
                t.campaign_id == cid for t in self.tables.values()
            ):
                self.lookup.drop([cid])
        except Exception:
            log.exception("Recap: couldn't queue the names to confirm")

    async def _link_recap_names(self, gid: int, cid: str, job: Job) -> None:
        """Remember which waiting suggestions are this recap's names to check (ids only), so its
        page can list them and answer them (#2113). A name the list already had as a suggestion
        counts too. A failure costs the page its list, never the recap."""
        if self.memory is None or self.name_answers is None:
            return
        try:
            # (A waiting suggestion an earlier recap also kept is listed under both recaps: it is
            # one name, answered once.)
            wanted = {name_key(n) for n in job.to_confirm}
            waiting = await self.memory.entities(gid, cid, statuses=(PROPOSED,))
            ids = [e.id for e in waiting if name_key(e.name) in wanted]
            await self.name_answers.link(gid, cid, job.session_at, ids)
        except Exception as exc:
            log.warning("Couldn't link a recap's names to check: %s", type(exc).__name__)

    async def _private_names_note(self, gid: int, cid: str, dm_user_id: int, added: int) -> None:
        """The same note as a private message to the DM, with the same check (#2027): it waits
        until the DM next opens Discord. Closed private messages cost nothing but a line in the
        log: the DM screen has it too. Never raises."""
        try:
            user = await self.fetch_user(dm_user_id)
            await user.send(
                recap_names_text(added),
                view=recap_names_dm_view(gid, cid),
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except discord.Forbidden:
            log.info("Recap: the DM's private messages are closed (names note)")
        except Exception:
            log.info("Recap: the private names note could not be sent")

    async def _waiting_recap_names(self, table: Table) -> None:
        """A session starts and names from a recap are still unanswered (#2027): the DM screen
        reminds once, with the check's button. Not after a restart in the middle of a session.
        Names the DM answered since are not counted, and nothing is said when none are left."""
        if self.recaps is None or self.memory is None or table.campaign_id is None or table.resumed:
            return
        gid, cid = table.guild_id, table.campaign_id
        with log_context(guild_id=gid, campaign_id=cid):
            try:
                waiting = await self.recaps.names_waiting(gid, cid)
                if not waiting:
                    return
                proposed = {
                    name_key(e.name)
                    for e in await self.memory.entities(gid, cid, statuses=[PROPOSED])
                }
                left = 0
                for session_at, names in waiting:
                    if await self.recaps.set_names_state(gid, cid, session_at, 2, only_from=1):
                        left += sum(1 for n in names if name_key(n) in proposed)
                if left and self.tables.get(gid) is table and table.screen_channel_id is not None:
                    await self.post(
                        table.screen_channel_id, recap_names_reminder(left), view=review_view(cid)
                    )
            except Exception:
                log.exception("The waiting-names reminder failed")

    async def run_recaps(self) -> None:
        """One round of the recap worker: the due jobs of each server this bot serves, a few at a
        time, and the recaps the DM has pressed Post on. Each campaign has one job at a time."""
        if not self.settings.recaps_on or self.recaps is None or self.recap_worker is None:
            return
        for guild in list(self.guilds):  # recaps whose name questions are answered (#2169)
            try:
                await self.release_recap_holds(guild.id)
            except Exception:
                log.exception("Recap: releasing the held recaps failed for a server")
        for guild in list(self.guilds):  # first: a DM's Post never waits on a slow job
            try:
                await self.post_recaps(guild.id)
            except Exception:
                log.exception("Recap: posting failed for a server")
        done = 0
        for guild in list(self.guilds):
            try:
                due = await self.recaps.due(guild.id)
            except Exception:
                log.exception("Recap: couldn't list the due jobs for a server")
                continue
            for cid, at in due:
                if done >= RECAPS_PER_ROUND:
                    return
                done += 1  # counted before it runs, so a job that raises can't stall the round
                with log_context(guild_id=guild.id, campaign_id=cid):
                    try:
                        await self.recap_worker.run(guild.id, cid, at)
                    except Exception:
                        log.exception("Recap: a job failed unexpectedly")

    async def run_recap_audio(self) -> None:
        """One round of the audio step: recaps whose text is ready and whose narration and music
        wait to be made, one at a time (each is minutes of provider work)."""
        if self.audio_worker is None or self.recaps is None:
            return
        for guild in list(self.guilds):
            try:
                if stuck := await self.recaps.fail_stuck_audio(guild.id):
                    log.warning("Recap audio: %d recap(s) never finished and were given up", stuck)
                due = await self.recaps.audio_due(guild.id)
            except Exception:
                log.exception("Recap audio: couldn't list the waiting recaps for a server")
                continue
            for cid, at in due:
                with log_context(guild_id=guild.id, campaign_id=cid):
                    try:
                        ran = await self.audio_worker.run(guild.id, cid, at)
                    except Exception:
                        log.exception("Recap audio: a job failed unexpectedly")
                        return
                if ran is not None:
                    return  # one recap's audio a round: the next round takes the next

    async def post_recaps(self, guild_id: int) -> None:
        """Send the recaps the DM pressed Post on, after checking again what may have changed
        since: the plan, who is still recorded, secret names read fresh. A check that fails
        puts the recap back as a draft with a plain note on the page. Posted once."""
        if self.recaps is None:
            return
        for cid, at in await self.recaps.to_post(guild_id):
            with log_context(guild_id=guild_id, campaign_id=cid):
                try:
                    await self._post_one_recap(guild_id, cid, at)
                except Exception:
                    log.exception("Recap: posting failed")
                    await self.recaps.back_to_draft(
                        guild_id, cid, at, note="Couldn't post it. Try Post again."
                    )

    async def _post_one_recap(self, gid: int, cid: str, at: int) -> None:
        assert self.recaps is not None
        job = await self.recaps.get(gid, cid, at)
        campaign = await self.campaigns.get(gid, cid)

        async def back(note: str) -> None:
            assert self.recaps is not None
            await self.recaps.back_to_draft(gid, cid, at, note=note)

        if job is None or campaign is None or job.status != "posting":
            return
        if not await self.plan_allows("recap", gid, campaign, fail_open=False):
            return await back(
                "Your plan doesn't include recaps right now, so nothing was posted. "
                "Your draft is saved."
            )
        channel = (
            self.get_channel(campaign.dmbchat_channel_id) if campaign.dmbchat_channel_id else None
        )
        if (
            channel is None
            or not hasattr(channel, "send")
            or (getattr(getattr(channel, "guild", None), "id", gid) != gid)
        ):
            return await back(
                "There's no chat channel for this campaign yet. It's made when a session starts. "
                "Start one, then press Post."
            )
        secrets = await self.recap_secret_names(campaign, job.session_at)
        if secrets is None:
            return await back("DMbot couldn't check your secret names just now. Try Post again.")
        text = recap_post.to_the_table(job)
        if names_a_secret(text, secrets, await self.recap_allowed_names(campaign, job.session_at)):
            return await back("It names something you keep secret. Change the text, then post.")
        try:  # (the progress clock's step: cosmetic, so it never stops a post, and never sits
            # between the consent read and the send, which must stay back to back)
            await self.recaps.posting_step(gid, cid, at, "post")
        except Exception:
            log.warning("Recap: couldn't move the progress step to post", exc_info=True)
        # The audio is looked up first, so the consent read below is the last await before the send.
        audio, audio_note = await self._recap_audio_files(gid, campaign, job, channel)
        agreed = await self.consent.agreed(gid)  # (a pause is not a stop)
        if not set(job.speakers) <= set(agreed):
            for f in audio:
                f.close()
            return await back(
                "A player stopped being recorded since this was written, so it can't be posted. "
                "Write a new recap, or leave this one out."
            )
        # Sent first and marked posted after, so a failed send is never recorded as a post.
        link = ""
        try:
            parts = recap_post.parts_of(text)
            for i, part in enumerate(parts):
                none = discord.AllowedMentions.none()
                if audio and i == len(parts) - 1:
                    sent = await channel.send(part, allowed_mentions=none, files=audio)
                else:
                    sent = await channel.send(part, allowed_mentions=none)
                if i == 0:
                    link = str(getattr(sent, "jump_url", "") or "")
        except Exception:
            for f in audio:
                f.close()
            log.warning("Recap: the send failed")
            return await back(
                "DMbot couldn't write in the chat channel. Check it can post there, "
                "then press Post again."
            )
        await self.recaps.posted(gid, cid, at, url=link)
        if campaign.dm_screen_channel_id:
            await self.post(
                campaign.dm_screen_channel_id,
                "📜 Recap posted to the table." + (f"\n{audio_note}" if audio_note else ""),
            )
        changed = job.summary != job.original
        log.info("Recap: posted%s", " after an edit" if changed else "")  # counts only

    async def _recap_audio_files(
        self, gid: int, campaign: Campaign, job: Job, channel: Any = None
    ) -> tuple[list[discord.File], str]:
        """The narration and the music as attachments, and a note for the DM when they were left
        out. Attached only if the recap has them, they were made from this very text (an edit
        since makes them stale), the plan still includes them (fails closed) and they fit the
        server's file limit. The text is posted either way."""
        if job.audio != "ready" or not campaign.recaps_audio:
            return [], ""
        if job.audio_text_hash != text_hash(job.title, job.summary):
            return [], "The audio was left out: the text changed after it was made."
        if not await self.plan_allows("recap_audio", gid, campaign, fail_open=False):
            return [], ""
        limit = min(
            RECAP_ATTACHMENT_MAX,
            int(
                getattr(getattr(channel, "guild", None), "filesize_limit", RECAP_DEFAULT_FILE_LIMIT)
            ),
        )
        found = await asyncio.to_thread(
            _attachable, self.recap_audio_files, gid, campaign.id, job.session_at
        )
        if not found:
            return [], ""
        if sum(size for _, _, size in found) > limit:  # the limit is for the whole message
            return [], "The audio was left out: it is too big for this server's file limit."
        return [discord.File(path, filename=FILE_NAMES[kind]) for kind, path, _ in found], ""

    async def _start_import_recap(self, gid: int, store: ImportStore, ready: Ready) -> None:
        """Start the recap of one scanned import. One that cannot be started is tried again next
        round, up to `IMPORT_RECAP_TRIES` times, so it never blocks the others for ever."""
        try:
            campaign = await self.campaigns.get(gid, ready.campaign_id)
            if campaign is not None:
                why = await self._import_recap(campaign, ready)
                if why:
                    self._import_unhonoured.setdefault(ready.campaign_id, []).append(why)
            await store.mark_recap_started(gid, ready.session_id)
        except PlanUnchecked:
            log.warning("Import: the plan could not be checked; the recap is tried again")
        except Exception:
            log.exception("Import: a session's recap could not be started")
            if await store.note_recap_failure(gid, ready.session_id) >= IMPORT_RECAP_TRIES:
                await store.mark_recap_started(gid, ready.session_id)  # let go
                self._import_unhonoured.setdefault(ready.campaign_id, []).append(
                    "it could not be started"
                )

    async def _import_recap(self, campaign: Campaign, ready: Ready) -> str:
        """The recap of one scanned import the DM asked for: through the same gates as after a live
        session (the switch, the DM's own choice, the plan) and the same hold for the DM's name
        answers (#2174). Without a DM-screen channel to ask in, it is queued without a hold.
        Returns why it was not written ("" when it was started or held); raises when it could not
        be tried (a plan that cannot be checked), so the round tries again."""
        gid = campaign.guild_id
        if self.recaps is None:
            return ""
        if not self.settings.recaps_on:
            log.info("Import: no recap written (recaps are off)")
            return "recaps are switched off"
        if not campaign.recaps_text:
            log.info("Import: no recap written (the DM chose none)")
            return "recaps are off for this campaign"
        if not await self.plan_allows("recap", gid, campaign, fail_open=False):
            if await self.plan_allows("recap", gid, campaign, fail_open=True):
                raise PlanUnchecked  # not a no: try again, and don't count it
            log.info("Import: no recap written (not in the plan)")
            return "your plan doesn't include recaps"
        dm = campaign.owner_user_id or min(campaign.dm_user_ids, default=0)
        if dm <= 0:
            log.warning("Import: no recap written (the campaign has no DM)")
            return "the campaign has no DM"
        channel = campaign.dm_screen_channel_id
        if channel is None:
            await self.recaps.enqueue(gid, campaign.id, ready.started_at, dm_user_id=dm)
            return ""
        # One question for the whole import, not one per session: when an earlier recap of this
        # campaign is already waiting and was asked about, this one waits quietly beside it.
        asked_before = self.recap_holds is not None and any(
            h.group_id == ready.import_id and h.asked for h in await self.recap_holds.held(gid)
        )
        # `say` False: the round tells the DM once for all of the import's recaps.
        result = await self._hold_or_queue(
            campaign,
            ready.session_id,
            ready.started_at,
            channel,
            dm,
            extractor_to_come=False,
            quiet=asked_before,
            group_id=ready.import_id,
            say=False,
        )
        if result == "queued":
            self._import_queued.setdefault(campaign.id, []).append(ready.started_at)
        return ""

    async def _recap_audio_loop(self) -> None:
        """Every minute: the narration and music of recaps whose text is ready (#1383). Its own
        loop, because one recap's audio is minutes of waiting on the provider and must never
        hold up the text jobs or a DM's Post."""
        await asyncio.sleep(120)
        while True:
            try:
                await self.run_recap_audio()
                await self.refresh_audio_waits()
                await self.run_audio_notices()
            except Exception:
                log.exception("The recap audio failed")
            await asyncio.sleep(RECAP_ROUND_S)

    async def _recap_loop(self) -> None:
        """Every minute: the recap jobs that are due and the recaps the DM has pressed Post on
        (#1382). A job resumes where it stopped, so a restart loses nothing."""
        await asyncio.sleep(90)
        rounds = 0
        while True:
            try:
                await self.run_recaps()
            except Exception:
                log.exception("The recap jobs failed")
            rounds += 1
            if rounds % 60 == 0:  # about hourly: audio older than 180 days goes
                try:
                    gone = await asyncio.to_thread(self.recap_audio_files.sweep)
                    if gone:
                        log.info("Removed the audio of %d old recaps", gone)
                except Exception:
                    log.exception("Removing old recap audio failed")
                try:
                    await self._sweep_deleted_campaign_audio()
                except Exception:
                    log.exception("Removing the audio of deleted campaigns failed")
            await asyncio.sleep(RECAP_ROUND_S)

    async def _recap_request_loop(self) -> None:
        """Every few seconds: what DMs typed on the recap page (#2113), sorted and written back
        for the page to poll. A short poll instead of a wake-up for now; the worker (#2128) can
        take it over unchanged."""
        sweeps = 0
        while True:
            try:
                await self.run_recap_requests()
            except Exception as exc:  # the type only: a database error can quote a row
                log.warning("The recap requests failed: %s", type(exc).__name__)
            try:
                await self.run_uploaded_checks()
            except Exception as exc:
                log.warning("The uploaded recap checks failed: %s", type(exc).__name__)
            try:
                await self.run_name_answers()
            except Exception as exc:
                log.warning("The name answers failed: %s", type(exc).__name__)
            try:
                await self.run_name_actions()
            except Exception as exc:
                log.warning("The name actions failed: %s", type(exc).__name__)
            try:
                await self.run_web_settings()
            except Exception as exc:
                log.warning("The website settings failed: %s", type(exc).__name__)
            try:
                await self.run_web_rule_sharing()
            except Exception as exc:
                log.warning("The website rule choices failed: %s", type(exc).__name__)
            try:
                await self.run_web_sheets()
            except Exception as exc:
                log.warning("The website sheet choices failed: %s", type(exc).__name__)
            try:
                await self.run_web_line_fixes()
            except Exception as exc:
                log.warning("The website line fixes failed: %s", type(exc).__name__)
            try:
                await self.run_peek_notices()
            except Exception as exc:
                log.warning("The peek notices failed: %s", type(exc).__name__)
            try:
                await self.run_web_delete_notices()
            except Exception as exc:
                log.warning("The website delete notices failed: %s", type(exc).__name__)
            try:
                await self.run_web_backups()
            except Exception as exc:
                log.warning("The website backups failed: %s", type(exc).__name__)
            try:  # last: a report is many AI calls, and the quick ones above must not wait for it
                await self.run_web_report_requests()
            except Exception as exc:
                log.warning("The website report requests failed: %s", type(exc).__name__)
            sweeps += 1  # counted whatever happened, so the sweep below can't be starved
            if sweeps % 300 == 0 and self.recap_requests is not None:  # about every 10 minutes
                for guild in list(self.guilds):
                    if self.web_line_fixes is not None:
                        try:  # its own try: it carries the promise that words go within a day
                            await self.web_line_fixes.sweep(guild.id)
                        except Exception as exc:
                            log.warning("The line fix sweep failed: %s", type(exc).__name__)
                    try:
                        await self.recap_requests.sweep(guild.id)
                        if self.name_answers is not None:
                            await self.name_answers.sweep(guild.id)
                        if self.name_actions is not None:
                            await self.name_actions.sweep(guild.id)
                        if self.web_settings is not None:
                            await self.web_settings.sweep(guild.id)
                        if self.web_rule_actions is not None:
                            await self.web_rule_actions.sweep(guild.id)
                        if self.web_sheets is not None:
                            await self.web_sheets.sweep(guild.id)
                        # Lines a DM put back after their session (#2587): read once.
                        await self.read_put_back_lines(guild.id)
                        if self.peek_notices is not None:
                            await self.peek_notices.sweep(guild.id)
                    except Exception as exc:
                        log.warning("The recap request sweep failed: %s", type(exc).__name__)
                if self.web_delete_notices is not None or self.web_backups is not None:
                    try:
                        for guild_id in await self.campaigns.guilds_with_campaigns():
                            if self.web_delete_notices is not None:
                                await self.web_delete_notices.sweep(guild_id)
                            if self.web_backups is not None:
                                await self.web_backups.sweep(guild_id)
                    except Exception as exc:
                        log.warning("The delete notice sweep failed: %s", type(exc).__name__)
            await asyncio.sleep(RECAP_REQUEST_ROUND_S)

    async def run_name_answers(self) -> None:
        """What DMs answered on the recap page about its names to check (#2113), applied through
        the same memory paths as Discord's name check, so an answer on either answers both. The
        sender must still be a DM of the campaign. Only the outcome word is logged, never the
        spelling or the note."""
        store = self.name_answers
        if store is None or self.memory is None:
            return
        for guild in list(self.guilds):
            for _ in range(NAME_ANSWERS_PER_ROUND):
                answer = await store.claim_next(guild.id)
                if answer is None:
                    break
                with log_context(guild_id=guild.id, campaign_id=answer.campaign_id):
                    try:
                        campaign = await self.campaigns.get(guild.id, answer.campaign_id)
                        why = (
                            name_answers.NOT_ALLOWED
                            if campaign is None
                            else await name_answers.apply(
                                self.memory, guild.id, campaign.dm_user_ids, answer
                            )
                        )
                    except Exception as exc:  # the row must never stay "working" for a bug
                        log.warning("A name answer failed: %s", type(exc).__name__)
                        why = name_answers.FAILED
                    await store.finish(guild.id, answer, error=why)
                    log.info("Name answer: %s", why or "done")  # the word only
                    if not why and self.lookup is not None:
                        self.lookup.mark_stale(guild.id, answer.campaign_id)  # lists read it fresh

    async def run_recap_requests(self) -> None:
        store = self.recap_requests
        ai = self.recap_feedback_ai
        if store is not None:
            # No clip outlives about ten minutes, whatever happened to its request and whether or
            # not recaps are on: this runs before that is looked at, about once a minute.
            self._clip_sweep_rounds += 1
            if self._clip_sweep_rounds % CLIP_SWEEP_EVERY == 1:
                try:  # every server, the ones the bot has left included
                    await store.sweep_clips()
                except Exception as exc:
                    log.warning("The clip sweep failed: %s", type(exc).__name__)
        if store is None or self.recaps is None or not self.settings.recaps_on:
            return
        for guild in list(self.guilds):
            for _ in range(RECAP_REQUESTS_PER_ROUND):
                request = await store.claim_next(guild.id, voice=False)
                if request is None:
                    break

                async def gate(req: RecapRequest, gid: int = guild.id) -> str | None:
                    campaign = await self.campaigns.get(gid, req.campaign_id)
                    job = await self.recaps.get(gid, req.campaign_id, req.session_at)  # type: ignore[union-attr]

                    async def feedback_gate() -> str | None:
                        why = await self.recap_feedback_gate(campaign, job)  # type: ignore[arg-type]
                        if why is None:
                            return None
                        return (
                            recap_requests.TOO_SOON
                            if why == recap_feedback.TOO_OFTEN
                            else recap_requests.NOT_ALLOWED
                        )

                    return await recap_requests.stop_reason(
                        req, campaign, job, has_ai=ai is not None, feedback_gate=feedback_gate
                    )

                async def sort(text: str) -> Any:
                    assert ai is not None
                    return await recap_feedback.sort_feedback(ai, text)

                with log_context(guild_id=guild.id, campaign_id=request.campaign_id):
                    outcome = await recap_requests.run_one(
                        store, guild.id, request, gate=gate, sort=sort
                    )
                    log.info("Recap request: %s", outcome)  # the word only, never the text

    async def _recap_voice_loop(self) -> None:
        """Spoken notes (#2113) are worked apart from typed ones (#2161): a clip near two minutes
        takes a minute of decoding and speech-to-text, and must not hold up a typed note behind it
        in any server. At most `RECAP_VOICE_AT_ONCE` at a time overall and one for each server;
        each runs as a task of its own, which keeps the claim, deletes the clip with the outcome
        and adds the meter once the claim has finished the row."""
        while True:
            try:
                await self.run_recap_voice()
            except Exception as exc:  # the type only: a database error can quote a row
                log.warning("The recap voice requests failed: %s", type(exc).__name__)
            await asyncio.sleep(RECAP_REQUEST_ROUND_S)

    async def run_recap_voice(self) -> None:
        """One round of `_recap_voice_loop`: start a voice task for each server that has a waiting
        clip and none running, while there is room."""
        store = self.recap_requests
        if store is None or self.recaps is None or not self.settings.recaps_on:
            return
        guilds = list(self.guilds)
        # Start after the server served last, so a busy server cannot keep the later ones waiting.
        start = self._voice_next % len(guilds) if guilds else 0
        for guild in guilds[start:] + guilds[:start]:
            if len(self._voice_tasks) >= RECAP_VOICE_AT_ONCE:
                return
            if guild.id in self._voice_tasks:
                continue
            request = await store.claim_next(guild.id, voice=True)
            if request is None:
                continue
            task = asyncio.create_task(
                self._run_voice_request(guild.id, request), name=f"recap-voice-{guild.id}"
            )
            self._voice_tasks[guild.id] = task
            self._voice_next = guilds.index(guild) + 1
            task.add_done_callback(partial(self._voice_done, guild.id))

    def _voice_done(self, guild_id: int, _task: asyncio.Task[None]) -> None:
        self._voice_tasks.pop(guild_id, None)  # the slot is free again, however it ended

    async def _run_voice_request(self, guild_id: int, request: RecapRequest) -> None:
        store = self.recap_requests
        assert store is not None
        try:
            with log_context(guild_id=guild_id, campaign_id=request.campaign_id):
                outcome = await recap_requests.run_voice(
                    store,
                    guild_id,
                    request,
                    gate=lambda req: self._voice_gate(guild_id, req),
                    write=lambda req: self.recap_voice_text(guild_id, req),
                    meter=lambda req, secs: self._meter_voice(guild_id, req, secs),
                )
                log.info("Recap voice request: %s", outcome)  # the word only
        except Exception as exc:  # the type only; the claim is taken back after a while
            log.warning("A recap voice request failed: %s", type(exc).__name__)

    async def _voice_gate(self, guild_id: int, request: RecapRequest) -> str | None:
        """Why a spoken note is not written down, or None: the sender is still a DM of the
        campaign, the recap is still a draft, and its switch and plan allow it. No AI is used."""
        campaign = await self.campaigns.get(guild_id, request.campaign_id)
        job = await self.recaps.get(guild_id, request.campaign_id, request.session_at)  # type: ignore[union-attr]

        async def allowed() -> str | None:
            ok = await self.recap_allowed(guild_id, request.campaign_id, job)
            return None if ok else recap_requests.NOT_ALLOWED

        return await recap_requests.stop_reason(
            request, campaign, job, has_ai=True, feedback_gate=allowed
        )

    async def recap_voice_text(self, guild_id: int, request: RecapRequest) -> tuple[str, float]:
        """A DM's spoken note on the recap page, written down (#2113): decoded in memory, sent
        to the table's speech-to-text with the campaign's name hints (never a secret name), names
        the Cleaner is sure of fixed, and its length added to the hours meter. Only a DM who has
        agreed to be recorded; consent is asked again after every wait. The audio is never
        written anywhere here; the caller deletes the clip with the outcome and adds the minutes
        to the meter once its own claim has finished the row. Returns the words and the clip's
        length. Raises `voice.ClipError` for a clip it can't use."""
        gid, cid, user = guild_id, request.campaign_id, request.user_id
        # `consenting` loads the server's consent when it is not loaded yet (after a restart);
        # the cache-only `has_consent` would refuse a DM who did agree.
        if user not in await self.consent.consenting(gid):
            raise voice.ClipError(voice.NO_CONSENT)
        pcm = await asyncio.to_thread(voice.decode, request.clip, request.clip_type)
        seconds = voice.seconds(pcm)
        if user not in await self.consent.consenting(gid):
            raise voice.ClipError(voice.NO_CONSENT)
        hints, lookup, people = await self._recap_voice_hints(gid, cid)
        if user not in await self.consent.consenting(
            gid
        ):  # asked again right before the audio leaves
            raise voice.ClipError(voice.NO_CONSENT)
        now_ms = int(time.time() * 1000)
        utterance = Utterance(gid, user, now_ms - int(seconds * 1000), now_ms, pcm, 0)
        try:
            heard = await asyncio.wait_for(
                self.pipeline.transcriber.transcribe(utterance, hints), VOICE_TRANSCRIBE_S
            )
        except (TranscriptionProblem, TimeoutError) as exc:
            raise voice.ClipError(voice.TRANSCRIBE_FAILED) from exc
        finally:
            del pcm, utterance  # the audio goes as soon as it is written down
        if user not in await self.consent.consenting(gid):
            raise voice.ClipError(voice.NO_CONSENT)  # they stopped meanwhile: nothing is kept
        text = str(heard).strip() if heard else ""
        if not text:
            return "", seconds
        if lookup is not None:
            try:
                text = clean(lookup, text, people=people, unsure=False).text
            except Exception:
                log.warning("Couldn't fix names in a spoken note; kept it as heard")
        return text, seconds

    async def _recap_voice_hints(
        self, guild_id: int, campaign_id: str
    ) -> tuple[list[str], CampaignLookup | None, list[str]]:
        """The name hints a live session would send for this campaign, without a running table:
        characters, then people who agreed. The lookup is None when the names could not be
        loaded (the note then goes with people only and no name is fixed)."""
        people, absent = await self._hint_people(guild_id, None)
        everyone = [*people, *absent]
        if self.lookup is None:
            return everyone, None, everyone
        try:
            found = await self.lookup.get_within(guild_id, campaign_id, HINTS_WAIT_S)
            if found is None:
                return everyone, None, everyone
            parts = await asyncio.to_thread(prepare_hints, found, time.time())
        except Exception:
            log.warning("Couldn't load the campaign's names for a spoken note")
            return everyone, None, everyone
        hints = scene_hints(
            found, parts, SceneTracker(), time.monotonic(), people=people, absent=absent
        )
        return hints, found, everyone

    async def _meter_voice(self, guild_id: int, request: RecapRequest, seconds: float) -> None:
        """Add a spoken note's length to the hours meter, rounded up to a whole minute, against
        the recap's session. Never raises: the words are worth more than a missed minute."""
        if self.meter is None:
            return
        try:
            campaign = await self.campaigns.get(guild_id, request.campaign_id)
            if campaign is None:
                return
            minutes, now = voice.meter_minutes(seconds), int(time.time())
            if campaign.owner_user_id is None:
                await self.meter.add_unowned(
                    guild_id=guild_id,
                    campaign_id=request.campaign_id,
                    session_started_at=request.session_at,
                    minutes=minutes,
                    now=now,
                )
            else:
                await self.meter.add(
                    guild_id=guild_id,
                    campaign_id=request.campaign_id,
                    owner_user_id=campaign.owner_user_id,
                    session_started_at=request.session_at,
                    minutes=minutes,
                    now=now,
                )
        except Exception as exc:
            log.warning("A spoken note's minutes could not be recorded: %s", type(exc).__name__)

    async def run_uploaded_checks(self) -> None:
        """Recaps a DM added from the website, written by someone at the table (#2609), have their
        names checked here, where the memory is: the website stores the words and nothing else.
        A few each round, in every server."""
        if self.recaps is None or not self.settings.recaps_on:
            return
        for guild in list(self.guilds):
            for cid, at in await self.recaps.unchecked_uploads(guild.id):
                with log_context(guild_id=guild.id, campaign_id=cid):
                    try:
                        await self.check_uploaded_recap(guild.id, cid, at)
                    except Exception as exc:  # the type only: a database error can quote a row
                        log.warning("Checking an uploaded recap failed: %s", type(exc).__name__)

    async def check_uploaded_recap(self, gid: int, cid: str, session_at: int) -> bool:
        """The check of one uploaded recap (#2609): names no list knows go to the DM's name check
        like any recap's; a secret name or link gives a note on the recap page and on the DM
        screen, never a block. Nothing goes into story memory by itself. False: nothing was done
        (not an uploaded recap, checked already, or the secret names could not be read, in which
        case it is tried again next round)."""
        if self.recaps is None:
            return False
        job = await self.recaps.get(gid, cid, session_at)
        campaign = await self.campaigns.get(gid, cid)
        if job is None or campaign is None or not job.written_by or job.checked_at:
            return False
        secret = await self.recap_secret_names(campaign)
        if secret is None:
            return False  # advice needs them: it waits, and is not skipped
        allowed = await self.recap_allowed_names(campaign)
        names = uploaded.unknown_names(job.summary, allowed, secret)
        note = uploaded.secret_note(job.summary, allowed, secret)
        if not await self.recaps.set_upload_check(
            gid, cid, session_at, to_confirm=names, note=note
        ):
            return False
        fresh = await self.recaps.get(gid, cid, session_at)
        if fresh is not None and names:
            await self._queue_recap_names(gid, cid, fresh, campaign)
        if campaign.dm_screen_channel_id is not None:
            link = (
                page_link(self.settings.site_url, gid, cid, session_at)
                if self.settings.site_url
                else ""
            )
            who = discord.utils.escape_markdown(job.written_by)
            await self.post(
                campaign.dm_screen_channel_id,
                f"📜 A recap written by **{who}** was added for this session. "
                "Nothing goes to the table until you say so."
                + (f"\n🤫 {note}" if note else "")
                + (f"\nYour recap page: {link}" if link else ""),
                view=ready_view(cid, session_at, self.settings.site_url, link),
                remember=("recap", cid, session_at),
            )
        return True

    async def recap_saved_lists(
        self, campaign: Campaign, session_at: int, user_id: int
    ) -> SavedLists | None:
        """What making this recap again would throw away, for the question first (#2727): the
        count of scenes whose "who was there" list the DM saved, and whether a finished recap
        stands. None: nothing to warn about (no recap yet, or not a DM). A count of None when it
        cannot be told, so the question warns anyway, as the website's does. Only a DM of the
        campaign is told anything."""
        if self.recaps is None or user_id not in campaign.dm_user_ids:
            return None
        try:
            return await self.recaps.saved_lists(campaign.guild_id, campaign.id, session_at)
        except Exception:
            log.exception("Recap again: couldn't read whether lists were saved")
            return SavedLists(
                None, None, None
            )  # (neither is known: warn, and do not claim an old one)

    async def recap_again(
        self, campaign: Campaign, session_at: int, user_id: int, *, replace_ok: bool = False
    ) -> str:
        """A DM makes the recap of a session, or makes it again (#2608). The outcome word: the
        store's (`queued`, `needs_confirm`, `busy`, `no_lines`, `not_allowed`) or `off` (recaps are
        switched off for DMbot), `no_plan` or `unchecked` (the plan could not be checked: nothing
        is spent). Asked for, so the campaign's own recap switch does not stop it; the plan and the
        month's allowance do, as for every recap (the worker checks the allowance and fails
        closed). Says on the DM screen that it is being written."""
        if self.recaps is None or not self.settings.recaps_on:
            return "off"
        if user_id not in campaign.dm_user_ids:
            return "not_allowed"
        gid = campaign.guild_id
        if not await self.plan_allows("recap", gid, campaign, fail_open=False):
            if await self.plan_allows("recap", gid, campaign, fail_open=True):
                return "unchecked"  # not a no: nothing was spent, so try again
            return "no_plan"
        waits = await self.recaps.others_working(gid, campaign.id, session_at)
        outcome = await self.recaps.start_over(
            gid, campaign.id, session_at, user_id, replace_ok=replace_ok
        )
        if outcome == "queued" and campaign.dm_screen_channel_id is not None:
            try:
                await self._say_writing(
                    campaign,
                    campaign.dm_screen_channel_id,
                    session_at,
                    note="Waiting its turn: another recap of this campaign is first."
                    if waits
                    else "",
                )
            except Exception:
                log.exception("Recap again: couldn't say on the DM screen that it is queued")
        return outcome

    async def _recanted(self, gid: int, cid: str, session_at: int | None) -> Collection[str]:
        """The secret names the DM took back for a session (#2591). If they can't be read, every
        secret name said aloud counts as recanted: stricter, never looser."""
        try:
            return await recap_recanted(self.exposures, gid, cid, session_at)
        except Exception:
            log.exception("Recap: couldn't read the recanted secret names")
            return EVERY_NAME

    async def _relation_ties(
        self, gid: int, cid: str
    ) -> tuple[tuple[tuple[str, ...], tuple[str, ...]], ...]:
        """The campaign's 🤫 relations as pairs of name sets (#2591). The writer is told the pairs
        (never the relation's words) and the DM gets a note on the sentence that names both. They
        advise: if they can't be read the recap goes on without them, the DM decides."""
        if self.memory is None:
            return ()
        try:
            found = await secret_connections(self.memory, gid, cid)
        except Exception:
            log.exception("Recap: couldn't read the secret connections")
            return ()
        return tuple((c.subject, c.object) for c in found)

    async def _open_threads(self, gid: int, cid: str) -> tuple[str, ...]:
        """The names of the story threads still open (not resolved or abandoned), for PlotBot's
        guide to a writer that reads the transcript (#2590). Names only; none if they can't be
        read (the guide is then shorter, nothing else changes)."""
        if self.memory is None:
            return ()
        try:
            found = await self.memory.threads(gid, cid)
        except Exception:
            log.exception("Recap: couldn't read the open story threads")
            return ()
        live = (OPEN, GOING, HINTED)
        return tuple(
            t.thread.name for t in found if t.state is not None and t.state.condition in live
        )

    async def run_audio_notices(self) -> None:
        """Say once, on the DM screen, that a recap's audio is ready or has failed (#2621). The
        marker is written first and only if the audio is still what is being told, so a restart
        never says it twice and a new making that has begun meanwhile is told when it ends."""
        if self.recaps is None:
            return
        for guild in list(self.guilds):
            try:
                due = await self.recaps.audio_to_tell(guild.id)
            except Exception:
                log.exception("Recap audio: couldn't list the notices for a server")
                continue
            for cid, at, what in due:
                with log_context(guild_id=guild.id, campaign_id=cid):
                    try:
                        if await self.recaps.mark_audio_told(guild.id, cid, at, what):
                            await self.recap_audio_told(guild.id, cid, at, what)
                    except Exception:
                        log.exception("Recap audio: the notice failed")

    async def recap_audio_told(self, gid: int, cid: str, at: int, what: str) -> None:
        """The notice itself, to the DM screen only. Fixed words and the recap's title."""
        assert self.recaps is not None
        campaign = await self.campaigns.get(gid, cid)
        job = await self.recaps.get(gid, cid, at)
        if campaign is None or job is None or campaign.dm_screen_channel_id is None:
            return
        failed = what == "failed"
        await self.audio_waits.finish((gid, cid, at), recap_audio.DONE_LINE)
        self._audio_seen.pop((gid, cid, at), None)
        link = page_link(self.settings.site_url, gid, cid, at) if self.settings.site_url else ""
        text = (recap_audio.failed_text if failed else recap_audio.ready_text)(job.title)
        await self.post(
            campaign.dm_screen_channel_id,
            text + (f"\nYour recap page: {link}" if link else ""),
            recap_audio.audio_view(cid, at, failed=failed, review_url=link),
        )

    async def recap_audio_started(self, campaign: Campaign, session_at: int) -> None:
        """The progress line when audio is chosen: it is being made, and it will be said here."""
        if campaign.dm_screen_channel_id is None:
            return
        key = (campaign.guild_id, campaign.id, session_at)
        if key in self.audio_waits:
            return
        sent = await self.post_message(campaign.dm_screen_channel_id, recap_audio.MAKING)
        if sent is not None:
            await self.audio_waits.start(key, sent)

    async def refresh_audio_waits(self) -> None:
        """Each round: a live "Making the audio" line counts a try or spent credits as a change,
        and is closed when its audio is no longer being made (the notice follows by itself for a
        finished or failed one)."""
        if self.recaps is None:
            return
        for key in self.audio_waits.open_keys():
            gid, cid, at = key
            try:
                job = await self.recaps.get(gid, cid, at)
            except Exception:
                log.exception("Recap audio: couldn't read a waiting line's recap")
                continue
            if job is not None and job.audio == "queued":
                seen = (job.audio_tries, job.audio_credits)
                if seen != self._audio_seen.get(key):
                    self.audio_waits.moved(key)
                self._audio_seen[key] = seen
            elif job is None or job.audio not in ("ready", "failed"):
                await self.audio_waits.finish(key, recap_audio.STOPPED_LINE)
                self._audio_seen.pop(key, None)

    async def run_plot_checks(self) -> None:
        """One round of PlotBot's checks of ready recaps (#2122), a few at a time."""
        if self.plot_runner is None:
            return
        for guild in list(self.guilds):
            try:
                with log_context(guild_id=guild.id):
                    await self.plot_runner.round(guild.id)
            except Exception as exc:  # the type only: a database error can quote a row
                log.warning("Plot checks failed for a server: %s", type(exc).__name__)

    async def run_plot_talk(self) -> None:
        """One round of answers to the DM's messages about plot issues, and of the changes the DM
        saved from them (#2123)."""
        if self.plot_talk is None:
            return
        for guild in list(self.guilds):
            try:
                with log_context(guild_id=guild.id):
                    await self._plot_talk_step(self.plot_talk.apply_round, guild.id)  # first: a
                    await self._plot_talk_step(self.plot_talk.round, guild.id)  # Save never waits
            except Exception as exc:  # the type only: a database error can quote a row
                log.warning("Plot talk failed for a server: %s", type(exc).__name__)

    async def _plot_talk_step(self, step: Callable[[int], Awaitable[int]], guild_id: int) -> None:
        """One part of a talk round, on its own: if it fails (say the AI is down), the rest run."""
        try:
            await step(guild_id)
        except Exception as exc:  # the type only: a database error can quote a row
            log.warning("A plot talk step failed: %s", type(exc).__name__)

    async def _plot_talk_loop(self) -> None:
        """Every few seconds (a DM is waiting for the answer), in its own task so a slow check
        never holds an answer up and a slow answer never holds a recap job up."""
        await asyncio.sleep(30)
        rounds = 0
        while True:
            await self.run_plot_talk()
            rounds += 1
            if (
                rounds % PLOT_TALK_SWEEP_EVERY == 1 and self.plot_talk is not None
            ):  # (first round too)
                for guild in list(self.guilds):  # about hourly: talks older than thirty days go
                    try:
                        await self.plot_talk.sweep(guild.id)
                    except Exception as exc:
                        log.warning("Plot talk sweep failed: %s", type(exc).__name__)
            await asyncio.sleep(PLOT_TALK_ROUND_S)

    async def _plot_loop(self) -> None:
        """Every minute: PlotBot's checks of ready recaps (#2122). Its own task, because a check
        waits on the AI for up to two minutes and must never hold up a recap job or a DM's Post."""
        await asyncio.sleep(120)
        while True:
            await self.run_plot_checks()
            await asyncio.sleep(RECAP_ROUND_S)

    async def _sweep_deleted_campaign_audio(self) -> None:
        """A campaign deleted on the website leaves its audio on the disk: that container mounts
        the volume read-only (#2554), so the bot removes it here. A folder is removed only when
        two passes in a row (an hour apart) both list it as a stray and a fresh look in every
        server still finds no such campaign: the passes read the servers one after another, so a
        campaign that moves servers while one runs can look gone for that pass (#2532), and its
        new folder is as old as the files copied into it. If no server lists a campaign the sweep
        does nothing, so a failed read can never wipe the audio."""
        keep: dict[int, set[str]] = {}
        for guild_id in await self.campaigns.guilds_with_campaigns():
            keep[guild_id] = await self.campaigns.campaign_ids(guild_id)
        if not any(keep.values()):
            self._audio_strays: set[tuple[int, str]] = set()
            return
        files = self.recap_audio_files
        strays = set(await asyncio.to_thread(files.strays, keep))
        seen_before: set[tuple[int, str]] = getattr(self, "_audio_strays", set())
        self._audio_strays = strays  # (this pass's list replaces the last one)
        gone = 0
        for guild_id, campaign_id in sorted(strays & seen_before):
            if await self.campaigns.exists_anywhere(campaign_id):
                continue
            if await asyncio.to_thread(files.remove_stray, guild_id, campaign_id):
                gone += 1
        if gone:
            log.info("Removed the audio of %d deleted campaigns", gone)

    async def recap_names_ready(self, gid: int, cid: str, job: Job) -> bool:
        """Whether PlotBot's name pass on the recap's transcript is done (#2628)."""
        if self.transcripts is None:
            return True
        return await self.transcripts.names_repaired(gid, cid, job.session_at)
