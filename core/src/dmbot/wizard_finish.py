"""The start-up wizard's finish run, answered by the bot (#3955; docs/WIZARD_DESIGN.md, 4.4).

The website queues a row in `wizard_finishes` (ids, counts and words of progress only); this round
claims the oldest, checks again that the sender is still in the server and the plan has room, and
makes the campaign from the draft, then reads the DM's lore with the same queue the website's lore
upload uses. Every stage writes the row's `stage` and `moved_at` (so the page shows where it is and
when it last moved), and every write is conditional on this worker still holding the row
(`state = 'working'` and its claim stamp): a run the door took as lost, or retaken after a restart,
stops quietly. A campaign is made once however many times a run is retried (`made_campaign`, and a
look at an earlier run of the same person that already made one).

Not in this slice (the next PR): the party's members and sheet links, the supplements, and the
house-rule, homebrew and ruleset documents. A document in those slots is left in the draft.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from dmbot.campaigns.models import OWN_WRITING, CampaignError, fingerprint
from dmbot.db import Database
from dmbot.dm_screen import channel_sync
from dmbot.logs import log_context
from dmbot.memory.models import CONFIRMED, DM, PLAYER_CHARACTER, MemoryRuleError, name_key
from dmbot.memory.store import INT64_MAX
from dmbot.rules import optional

log = logging.getLogger(__name__)

GIVE_UP_S = 30 * 60  # as the door's: a run that has not moved this long ago was taken as lost
RETAKE_AFTER_S = 10 * 60  # a claim that stopped moving this long ago (the bot restarted)
MAX_TRIES = 3
LORE_POLL_S = 3.0
LORE_WAIT_S = 20 * 60  # the most one document's read is waited for

# The words a failed run ends with (the page turns them into plain words); the door also writes
# "lost" itself. "drive_gone" and "lore_failed" end a run that is otherwise done: the campaign and
# the other documents stand.
NOT_ALLOWED, PLAN, GONE, FAILED = "not-allowed", "plan", "gone", "failed"
DRIVE_GONE, LORE_FAILED, BAD_ANSWERS = "drive_gone", "lore_failed", "bad_answers"
PARTY_FAILED = "party_failed"  # the characters could not be made; the campaign stands
# Documents in a slot this run does not read yet (house rules, homebrew, an own ruleset): they are
# not read, and the draft is cleaned up, so the run says so and the page tells the DM to add them on
# the House rules and Homebrew pages. Nothing is silently dropped.
DOCS_LEFT = "docs_left"
NO_RIGHTS = "no_rights"  # documents with no right-to-use confirmation: nothing is read
SOME_DOCS = (DRIVE_GONE, LORE_FAILED, PARTY_FAILED, DOCS_LEFT)
NOT_READ_YET = ("house", "homebrew", "ruleset")

# The stage codes the page shows, in order.
STAGES = ("campaign", "data_use", "rules", "party", "lore", "secrets", "clean")
CHOICE_OF = {"lore": "world", "secrets": "spoilers"}


@dataclass(slots=True)
class Run:
    id: str
    user_id: int
    claimed_at: int  # milliseconds: the token that lets only this worker write
    made_campaign: str = ""
    tries: int = 1
    started_at: int = 0  # when the page queued it (seconds)


@dataclass(slots=True)
class Draft:
    answers: dict[str, Any]
    rights_shared_at: int
    rights_ruleset_at: int
    documents: list[dict[str, Any]] = field(default_factory=list)


class Lost(Exception):
    """This worker no longer holds the run."""


class Retry(Exception):
    """Not the run's fault (the bot's server list is not ready yet): leave it for a retake."""


class WizardFinishes:
    def __init__(self, db: Database, *, clock: Callable[[], float] = time.time) -> None:
        self._db = db
        self._now = clock

    async def claim_next(self, guild_id: int) -> Run | None:
        """The oldest waiting run (or one that stopped moving), marked as being worked on."""
        now_ms = int(self._now() * 1000)
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE wizard_finishes SET state = 'working', claimed_at = %s, tries = tries + 1,"
                " moved_at = %s WHERE (guild_id, id) = (SELECT guild_id, id FROM wizard_finishes"
                " WHERE guild_id = %s AND created_at >= %s AND (state = 'waiting'"
                " OR (state = 'working' AND claimed_at < %s))"
                " ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED)"
                " RETURNING id, user_id, made_campaign, tries, created_at",
                (now_ms, now_ms // 1000, guild_id, now_ms // 1000 - GIVE_UP_S,
                 now_ms - RETAKE_AFTER_S * 1000),
            )  # fmt: skip
            row = await cur.fetchone()
        if row is None:
            return None
        return Run(
            str(row["id"]),
            int(row["user_id"]),
            now_ms,
            str(row["made_campaign"]),
            int(row["tries"]),
            int(row["created_at"]),
        )

    async def move(
        self, guild_id: int, run: Run, *, stage: str, part: int = 0, of_parts: int = 0,
        made_campaign: str = "",
    ) -> None:  # fmt: skip
        """Record the stage and show the page the run is alive. Raises `Lost` when this worker
        no longer holds the run, so a stage never goes on writing for a run that was taken back."""
        stamp = int(self._now() * 1000)
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE wizard_finishes SET stage = %s, part = %s, of_parts = %s, claimed_at = %s,"
                " moved_at = %s, made_campaign = CASE WHEN %s <> '' THEN %s ELSE made_campaign END"
                " WHERE guild_id = %s AND id = %s AND state = 'working' AND claimed_at = %s",
                (stage, part, of_parts, stamp, stamp // 1000, made_campaign, made_campaign,
                 guild_id, run.id, run.claimed_at),
            )  # fmt: skip
            if not cur.rowcount:
                raise Lost
        run.claimed_at = stamp
        if made_campaign:
            run.made_campaign = made_campaign

    async def end(self, guild_id: int, run: Run, *, ok: bool, error: str = "") -> bool:
        """Close the run, only if this worker still holds it."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE wizard_finishes SET state = %s, error = %s, moved_at = %s"
                " WHERE guild_id = %s AND id = %s AND state = 'working' AND claimed_at = %s",
                ("done" if ok else "failed", error[:40], int(self._now()), guild_id, run.id,
                 run.claimed_at),
            )  # fmt: skip
            return bool(cur.rowcount)

    async def earlier_campaign(self, guild_id: int, user_id: int) -> str:
        """A campaign an earlier run made from THIS draft and did not finish (a run taken as lost
        and pressed again is a new row), or ''. Only a failed run counts, and only one queued
        after the draft began: a run that finished, or one for an older draft, never points a new
        wizard at an old campaign."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT f.made_campaign FROM wizard_finishes f JOIN wizard_drafts d"
                " ON d.guild_id = f.guild_id AND d.user_id = f.user_id"
                " WHERE f.guild_id = %s AND f.user_id = %s AND f.made_campaign <> ''"
                " AND f.state = 'failed' AND f.created_at >= d.created_at"
                " ORDER BY f.created_at DESC LIMIT 1",
                (guild_id, user_id),
            )
            row = await cur.fetchone()
        return str(row["made_campaign"]) if row else ""

    async def draft(self, guild_id: int, user_id: int) -> Draft | None:
        async with self._db.guild(guild_id, snapshot=True) as conn:
            cur = await conn.execute(
                "SELECT answers, rights_shared_at, rights_ruleset_at, created_at FROM wizard_drafts"
                " WHERE guild_id = %s AND user_id = %s",
                (guild_id, user_id),
            )
            row = await cur.fetchone()
            if row is None:
                return None
            docs = await conn.execute(
                "SELECT id, slot, name, source, text, status FROM wizard_documents"
                " WHERE guild_id = %s AND user_id = %s ORDER BY created_at, id",
                (guild_id, user_id),
            )
            documents = [dict(d) for d in await docs.fetchall()]
        return Draft(
            dict(row["answers"]), int(row["rights_shared_at"]), int(row["rights_ruleset_at"]),
            documents,
        )  # fmt: skip

    async def delete_draft(self, guild_id: int, run: Run) -> None:
        """Remove the draft, only while this worker still holds the run (a worker that lost it
        must not delete a draft a newer run is using)."""
        async with self._db.guild(guild_id) as conn:
            await conn.execute(
                "DELETE FROM wizard_drafts d WHERE d.guild_id = %s AND d.user_id = %s AND EXISTS"
                " (SELECT 1 FROM wizard_finishes f WHERE f.guild_id = d.guild_id AND f.id = %s"
                " AND f.state = 'working' AND f.claimed_at = %s)",
                (guild_id, run.user_id, run.id, run.claimed_at),
            )

    async def open_upload(self, guild_id: int, campaign_id: str) -> str:
        """The id of this campaign's open lore upload (the queue holds one at a time), or ''."""
        async with self._db.guild(guild_id, snapshot=True) as conn:
            cur = await conn.execute(
                "SELECT id FROM lore_uploads WHERE guild_id = %s AND campaign_id = %s"
                " AND state IN ('waiting', 'working')",
                (guild_id, campaign_id),
            )
            row = await cur.fetchone()
        return str(row["id"]) if row else ""

    async def cancel_upload(self, guild_id: int, upload: str) -> None:
        """Take an upload nobody read in time out of the queue, and blank its words."""
        async with self._db.guild(guild_id) as conn:
            await conn.execute(
                "UPDATE lore_uploads SET state = 'failed', error = 'not_taken', text = '',"
                " link = '' WHERE guild_id = %s AND id = %s AND state IN ('waiting', 'working')",
                (guild_id, upload),
            )

    async def sweep(self, guild_id: int) -> int:
        """Remove the drafts past their week, with their documents (by the cascade): the door
        does it for whoever presses one, this is for the drafts nobody comes back to."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "DELETE FROM wizard_drafts WHERE guild_id = %s AND expires_at <= %s",
                (guild_id, int(self._now())),
            )
            return cur.rowcount

    async def queue_lore(
        self,
        guild_id: int,
        campaign_id: str,
        user_id: int,
        text: str,
        *,
        choice: str,
        rights_at: int,
    ) -> str:
        """One document into the lore upload queue, as the website's upload does
        (`dmbot_web_lore_add`): the DM's own words, the right to use them confirmed on the draft
        (recorded as this account and that time), the choice stored."""
        now = int(self._now())
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "INSERT INTO lore_uploads (guild_id, campaign_id, id, user_id, source, text,"
                " choice, rights_by, rights_at, created_at, updated_at) VALUES (%s, %s,"
                " replace(gen_random_uuid()::text, '-', ''), %s, 'file', %s, %s, %s, %s, %s, %s)"
                " RETURNING id",
                (guild_id, campaign_id, user_id, text, choice, user_id, rights_at, now, now),
            )
            row = await cur.fetchone()
        assert row is not None
        return str(row["id"])

    async def lore_state(self, guild_id: int, upload: str) -> str:
        async with self._db.guild(guild_id, snapshot=True) as conn:
            cur = await conn.execute(
                "SELECT state FROM lore_uploads WHERE guild_id = %s AND id = %s", (guild_id, upload)
            )
            row = await cur.fetchone()
        return str(row["state"]) if row else "gone"


def check_answers(answers: dict[str, Any]) -> dict[str, Any] | None:
    """The draft's answers, checked again as the website's wrapper checks them (the door only
    keeps the key list and the size, so the bot does not trust the shapes). None: refuse."""
    from dmbot.web import wizard  # (the shape rules live with the website's wrapper)
    from dmbot.web.recaps import RecapError

    try:
        clean = wizard.check_patch(answers)
    except RecapError:
        return None
    return clean if str(clean.get("name", "")).strip() else None


async def run_round(bot: Any, guild_id: int) -> int:
    """Answer one waiting finish run on a server; returns how many were taken (0 or 1)."""
    runs = WizardFinishes(bot.campaigns.db)
    run = await runs.claim_next(guild_id)
    if run is None:
        return 0
    with log_context(guild_id=guild_id):
        word = FAILED if run.tries > MAX_TRIES else ""
        try:
            word = word or await _answer(bot, runs, guild_id, run)
        except Lost:
            log.info("A wizard finish run was taken back; stopping")
            return 1
        except Retry:
            log.info("A wizard finish run waits for the server list; it is retaken")
            return 1
        except Exception as exc:
            log.warning("A wizard finish run failed (%s)", type(exc).__name__)
            word = FAILED
        try:
            held = await runs.end(guild_id, run, ok=word in ("", *SOME_DOCS), error=word)
        except Exception as exc:  # the row stays working and is retaken, then failed at MAX_TRIES
            log.warning("A wizard finish run could not be closed (%s)", type(exc).__name__)
            return 1
        log.info("A wizard finish run ended: %s%s", word or "done", "" if held else " (lost)")
    return 1


async def _answer(bot: Any, runs: WizardFinishes, guild_id: int, run: Run) -> str:
    """The word the run ends with ("" for a campaign made and its lore read). Every check is made
    again here: minutes may have passed since the page queued it."""
    draft = await runs.draft(guild_id, run.user_id)
    if draft is None:
        # The campaign and its lore are done when a run that already made one finds no draft: the
        # worker died after deleting it. Anything else is a draft that went (discarded, expired).
        return "" if run.made_campaign else GONE
    answers = check_answers(draft.answers)
    if answers is None:
        return BAD_ANSWERS
    needs_rights = any(d["slot"] in CHOICE_OF for d in draft.documents)
    if needs_rights and not draft.rights_shared_at:
        return NO_RIGHTS  # nothing is read, and none is made up, without the DM's confirmation
    guild = bot.get_guild(guild_id)
    if guild is None:
        raise Retry  # the bot's server list is not ready (a start or reconnect)
    # Still in the server? The member cache is empty without the members intent, so a member the
    # cache lacks is asked for (as the channel sync does): only a clear "not a member" refuses; a
    # Discord hiccup keeps them, and every later stage is checked again anyway.
    if run.user_id not in await channel_sync.present_only(guild, [run.user_id]):
        return NOT_ALLOWED
    campaign_id = run.made_campaign or await runs.earlier_campaign(guild_id, run.user_id)
    campaign_id = campaign_id or await _made_before(bot, guild_id, run, answers["name"])
    if not campaign_id:
        await runs.move(guild_id, run, stage="campaign")
        refused = await bot.create_refusal(guild_id, run.user_id)
        if refused is not None:
            return PLAN
        try:
            made = await bot.campaigns.create(
                guild_id, answers["name"], run.user_id,
                target_ruleset=answers.get("target", "2024"),
                fallback_ruleset=answers.get("fallback", "2014"),
                dndbeyond_invite=answers.get("invite") or None,
            )  # fmt: skip
        except CampaignError as exc:
            log.info("A wizard campaign was refused: %s", type(exc).__name__)
            return FAILED
        campaign_id = made.id
    if campaign_id != run.made_campaign:
        await runs.move(guild_id, run, stage="campaign", made_campaign=campaign_id)
    await _record_rights(bot, guild_id, campaign_id, run.user_id, draft)  # (once, on any path)
    await runs.move(guild_id, run, stage="data_use")
    if "data_use" in answers:
        await bot.campaigns.set_share_improve(guild_id, campaign_id, bool(answers["data_use"]))
    await runs.move(guild_id, run, stage="rules")
    await _apply_supplements(bot, guild_id, campaign_id, answers)
    await runs.move(guild_id, run, stage="party")
    word = ""
    try:
        await _make_characters(bot, guild, guild_id, campaign_id, answers)
    except Lost:
        raise
    except Exception as exc:  # the campaign is usable without its characters: say so, go on
        log.warning("A wizard run could not make its characters (%s)", type(exc).__name__)
        word = PARTY_FAILED
    for slot in ("lore", "secrets"):
        docs = [d for d in draft.documents if d["slot"] == slot]
        word = await _read_slot(runs, guild_id, run, campaign_id, draft, slot, docs) or word
    if any(d["slot"] in NOT_READ_YET for d in draft.documents):
        word = word or DOCS_LEFT
    await runs.move(guild_id, run, stage="clean")
    await runs.delete_draft(guild_id, run)
    return word


# The wizard's supplement codes and the book each one is in the rules catalog
# (`rules.optional.CATALOG`). Only these two books have rules in it yet; a code with no rules there
# changes nothing until its rules are added.
BOOK_OF = {"xanathar": optional.XANATHAR, "tasha": optional.TASHA}


async def _apply_supplements(
    bot: Any, guild_id: int, campaign_id: str, answers: dict[str, Any]
) -> int:
    """The books the DM ticked in step 6 (supplements), as the campaign's optional rules: a rule of
    a ticked book is on, a rule of a book that is in the catalog but not ticked is off. Only the
    choice is stored, never a word of a book (the IP rule). A DM who skipped the step has no
    `supplements` answer, and the campaign keeps its default (every optional rule on where it does
    not clash). Returns how many rules were set (counts only)."""
    if "supplements" not in answers:
        return 0
    ticked = {BOOK_OF[code] for code in answers["supplements"] if code in BOOK_OF}
    for rule in optional.CATALOG:
        await bot.campaigns.set_optional_rule(guild_id, campaign_id, rule.id, rule.source in ticked)
    log.info("A wizard run set %d optional rule(s)", len(optional.CATALOG))  # counts only
    return len(optional.CATALOG)


async def _confirmed_players(guild: Any, wanted: set[int]) -> frozenset[int]:
    """Which of these ids are people in the server, confirmed: in the member cache, or fetched.
    Stricter than `channel_sync.present_only` on purpose: that one keeps a person when Discord
    cannot say (right for hiding a channel), but here an id is bound to a character, so only a
    confirmed member counts. A bot, an id out of range and any error all leave a player unset."""
    found: set[int] = set()
    for player in wanted:
        if not 0 < player <= INT64_MAX:
            continue
        member = guild.get_member(player)
        if member is None:
            try:
                member = await guild.fetch_member(player)
            except Exception:  # not a member, or Discord could not say: not confirmed
                continue
        if not getattr(member, "bot", False):
            found.add(player)
    return frozenset(found)


async def _make_characters(
    bot: Any, guild: Any, guild_id: int, campaign_id: str, answers: dict[str, Any]
) -> int:
    """The player characters the DM confirmed in the wizard (step 2) and who plays them (step 3),
    made as the DM's own confirmed entries; a character with no player yet is still a player
    character, its player "pending", answered later. A player's number counts only if that person
    is a confirmed member of the server (the page cannot be trusted with ids). A player character
    already in the campaign is not made again, but its short name is still added if it lacks it,
    so a run that stopped half way is finished by its retry. The party itself is made with every
    campaign (#3976); this only adds the characters as members, through the same step an
    assignment uses. Returns how many were made (the log gets counts only)."""
    memory = bot.memory
    characters = answers.get("characters") or []
    if memory is None or not characters:
        return 0
    wanted = {int(c["player"]) for c in characters if str(c.get("player", "")).isdecimal()}
    here = await _confirmed_players(guild, wanted) if wanted else frozenset()
    have = {
        name_key(e.name): e.id
        for e in await memory.entities(guild_id, campaign_id)
        if e.role == PLAYER_CHARACTER
    }
    made = 0
    for c in characters:
        name = str(c.get("full", "")).strip()
        if not name:
            continue
        entity_id = have.get(name_key(name))
        if entity_id is None:
            player = int(c["player"]) if str(c.get("player", "")).isdecimal() else None
            try:
                written = await memory.add_entity(
                    guild_id, campaign_id, type="character", name=name, source=DM,
                    status=CONFIRMED, role=PLAYER_CHARACTER,
                    played_by=player if player in here else None,
                )  # fmt: skip
            except (MemoryRuleError, ValueError) as exc:
                log.info("A wizard character was refused: %s", type(exc).__name__)
                continue
            entity_id = written.value.id
            have[name_key(name)] = entity_id
            made += 1
        short = str(c.get("party_name", "")).strip()
        if short and name_key(short) != name_key(name):
            try:  # (already there is fine: the call returns the existing alias)
                await memory.add_alias(
                    guild_id, campaign_id, entity_id, short, kind="short", source=DM,
                    status=CONFIRMED,
                )  # fmt: skip
            except (MemoryRuleError, ValueError) as exc:
                log.info("A wizard short name was refused: %s", type(exc).__name__)
    await memory.party_on_assignment(guild_id, campaign_id)
    log.info("A wizard run made %d player character(s)", made)  # counts only
    return made


async def _made_before(bot: Any, guild_id: int, run: Run, name: str) -> str:
    """A campaign this person's earlier try made but never recorded (the worker stopped between
    making it and writing its id): theirs, with this name, made since the run was queued."""
    for campaign in await bot.campaigns.list_campaigns(guild_id):
        if (
            campaign.is_owner(run.user_id)
            and campaign.created_at >= run.started_at
            and campaign.name.casefold() == name.casefold()
        ):
            return str(campaign.id)
    return ""


async def _record_rights(
    bot: Any, guild_id: int, campaign_id: str, user_id: int, draft: Draft
) -> None:
    """The right-to-use confirmation goes onto the campaign with who and when (CLAUDE.md, IP),
    once however many times the run is tried: only a fingerprint of the draft's documents' names
    is kept, never their text."""
    if not draft.rights_shared_at:
        return
    for done in await bot.campaigns.confirmations(guild_id, campaign_id):
        if done["user"] == str(user_id) and done["purpose"] == OWN_WRITING:
            return
    names = "|".join(sorted(str(d["name"]) for d in draft.documents if d["slot"] != "ruleset"))
    await bot.campaigns.record_confirmation(
        guild_id, campaign_id, user_id, OWN_WRITING, fingerprint(names or "wizard")
    )


async def _read_slot(
    runs: WizardFinishes, guild_id: int, run: Run, campaign_id: str, draft: Draft, slot: str,
    docs: list[dict[str, Any]],
) -> str:  # fmt: skip
    """Queue each readable document of a slot into the lore upload queue, one at a time, and wait
    for the existing reader to finish it. Returns "" or the word of the first one that did not
    read (the campaign and the other documents stand)."""
    word = ""
    for number, doc in enumerate(docs, 1):
        await runs.move(guild_id, run, stage=slot, part=number, of_parts=len(docs))
        if doc["source"] == "drive":
            word = word or DRIVE_GONE  # reading a picked Drive file waits for its own PR
            continue
        if doc["status"] != "ok" or not str(doc["text"]).strip():
            continue
        try:
            state = await _read_one(
                runs, guild_id, run, campaign_id, draft, slot, doc, number, len(docs)
            )
        except Lost:
            raise
        except Exception as exc:  # one document's trouble never stops the others
            log.warning("A wizard document could not be read (%s)", type(exc).__name__)
            state = "failed"
        if state != "done":
            word = word or LORE_FAILED
    return word


async def _read_one(
    runs: WizardFinishes, guild_id: int, run: Run, campaign_id: str, draft: Draft, slot: str,
    doc: dict[str, Any], number: int, of_parts: int,
) -> str:  # fmt: skip
    """Queue one document and wait for the reader. The queue holds one upload for a campaign at a
    time, so an earlier one still open (a retaken run's) is waited for first, and one that is not
    read in time is taken out, its words blanked."""
    waited = 0.0
    while (open_one := await runs.open_upload(guild_id, campaign_id)) != "":
        if waited >= LORE_WAIT_S:
            await runs.cancel_upload(guild_id, open_one)
            return "failed"
        await asyncio.sleep(LORE_POLL_S)
        waited += LORE_POLL_S
        await runs.move(guild_id, run, stage=slot, part=number, of_parts=of_parts)
    upload = await runs.queue_lore(
        guild_id, campaign_id, run.user_id, str(doc["text"]),
        choice=CHOICE_OF[slot], rights_at=draft.rights_shared_at,
    )  # fmt: skip
    while True:
        state = await runs.lore_state(guild_id, upload)
        if state in ("done", "failed", "gone"):
            return state
        if waited >= LORE_WAIT_S:
            await runs.cancel_upload(guild_id, upload)
            return "failed"
        await asyncio.sleep(LORE_POLL_S)
        waited += LORE_POLL_S
        await runs.move(guild_id, run, stage=slot, part=number, of_parts=of_parts)
