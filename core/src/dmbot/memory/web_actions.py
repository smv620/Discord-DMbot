"""The bot's side of the website's All names page (#2294): what a DM did there, applied here.

The website has no right on the memory tables, so a DM's action on the page (fix a spelling,
change the kind, add or remove another name, switch a secret, merge, remove) is queued as a row
(`name_actions`). This module claims the rows one at a time in the order they were made and applies
each through the same `MemoryStore` calls as Discord's name card, so the change log, Undo and every
list agree: a change on the page can be undone from the card, and the other way round.

Checked again at the moment of use, because the queue-time check can be stale: the sender must still
be a DM of that campaign, and the name must still be a confirmed one of it. The DM's words (a
spelling, another name) are never logged, only the outcome word; a row is deleted after an hour
whatever happened, and a claimed row that stalled is picked up again.
"""

from __future__ import annotations

import contextlib
import logging
import re
import time
from collections.abc import Awaitable, Callable, Collection, Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from dmbot.db import Database
from dmbot.memory import backstory_keep as keep
from dmbot.memory import (
    beliefs,
    evidence,
    link_edits,
    map_facts,
    map_uploads,
    name_edits,
    placing,
    told,
)
from dmbot.memory import claims as claims_mod
from dmbot.memory import lore_pdfs as lore_pdfs_mod
from dmbot.memory import thread_clues as clues_mod
from dmbot.memory import threads as thread_model
from dmbot.memory import time_limits as tl
from dmbot.memory import unnamed as unnamed_mod
from dmbot.memory._changes import ALREADY_UNDONE, CLAIMS, MAP_PICTURE_GONE, Changes
from dmbot.memory._changes import NOT_FOUND as STORE_NOT_FOUND
from dmbot.memory.backstory_links import DONE, FULL, BackstoryLinks
from dmbot.memory.claims import WAITING_NAME_REASON, ClaimStore
from dmbot.memory.deed_proposals import GONE as DEED_PROPOSALS_GONE
from dmbot.memory.link_words import predicate_for_either
from dmbot.memory.lookup import CampaignLookup
from dmbot.memory.map_fact_store import GONE as MAP_GONE
from dmbot.memory.map_fact_store import ONLY_KNOWN_ROAD, ONLY_ROUTES
from dmbot.memory.models import (
    CLAIM_CONFIRMED,
    CLAIM_PROPOSED,
    CLAIM_REJECTED,
    CONFIRMED,
    DEED,
    DM,
    KNOWS_HOWS,
    LIVE,
    MERGE_MANY_MAX,
    MERGED,
    NEEDS_A_LOOK,
    PLAYER_CHARACTER,
    PRONOUNCED,
    PROPOSED,
    REJECTED,
    THREAD_TYPES,
    Entity,
    MemoryRuleError,
    Relation,
    TooLateToUndo,
    lookup_key,
    name_key,
)
from dmbot.memory.party_record import ENDED
from dmbot.memory.reveal_proposals import RevealProposals
from dmbot.memory.store import INT64_MAX, LENGTH_CHANGED, PERSON, ROUTE_GONE, MemoryStore
from dmbot.memory.store import NAMES_NOT_SHOWN as NAMES_NOT_SHOWN_WORDS
from dmbot.rules import homebrew, rule_sets, set_sync
from dmbot.rules.house import HouseRuleError
from dmbot.rules.web_updates import RuleTools

if TYPE_CHECKING:
    from dmbot.maps.store import MapStore
    from dmbot.memory.map_uploads import MapUploads
from dmbot.memory.which_else import COULD_NOT_KEEP, none_of_these

log = logging.getLogger(__name__)

RETAKE_AFTER_S = 120  # a claim this old belongs to a bot that stopped
KEEP_FOR_S = 3600  # nothing waits on a row longer than this

# What a failed action says, in words the page can turn into plain ones.
NOT_ALLOWED = "not_allowed"  # the sender is not a DM of the campaign any more
NOT_FOUND = "not_found"  # the name (or the other name) is gone, or no longer confirmed
BAD_NAME = "bad_name"  # a spelling or another name the rules refuse
BAD_KIND = "bad_kind"  # a kind the rules do not take
UNKNOWN_ID = "unknown_id"  # Discord does not know that ID
LOOKUP_FAILED = "lookup_failed"  # Discord could not be asked: nothing is linked, try again
BAD_PLAYER = "bad_player"  # not an ID the rules take
BOT_PLAYER = "bot_player"  # the ID is a bot's: a bot cannot play a character
EXISTS = "exists"  # the spelling is already another name's
# A feeling picked on the website rides the `link-words` action (the door's text rules are
# unchanged) as `feeling:<word>`; applying it is the same change as typing "knows (<word>)" but only
# on a `knows` connection (#3606), so the picker can never turn another kind of connection into one.
FEELING_TEXT = "feeling:"
# The website's "have they met?" on a knows line (#3882): its own queued action, applied by `_link`
# through `MemoryStore.set_knows_how`. The door's allowed-actions list holds it (WebDev's
# migration).
KNOWS_HOW_ACTION = "link-knows-how"
# "Who knows this?" on a connection and on a secret other name (#2999): text `dm`, `everyone` or
# `common` (🌍, #4559) or `some <ids>` (web/who_knows.py); the door checks its shape and that the
# ids are this campaign's.
LINK_WHO, ALIAS_WHO = "link-who", "alias-who"
# Which way a connection reads, the DM's choice (#4154): `text` "a-b" (from the pair's first name
# by id, the edge's `a` on the page), "b-a", or "both"
DIRECTION_ACTION = "link-direction"
RIGHT_ACTION = "link-right"  # ✅ That's right on a place's assumed parent (#4304)
DIRECTIONS = ("a-b", "b-a", "both")
BAD_TARGET = "bad_target"  # a merge, a secret or a removal the rules refuse
NAMES_NOT_SHOWN = "names_not_shown"  # a connection to a name still held back (#3426)
CANNOT_SHOW = "cannot_show"  # 🌍 Common knowledge asked for what the players are not shown (#4559)
NAME_UNNAMED = "name_unnamed"  # ... or to a person the story never named (#3426)
FAILED = "failed"  # anything unexpected; the action can be sent again
GONE = "gone"  # a waiting question that is not waiting any more
# a heads-up whose answer the rules refused: nothing changed and it is still waiting, as on the
# card, which shows the rule's message and keeps the heads-up (#3695)
REFUSED = "refused"
NO_CLOCK = "no_clock"  # a time limit's date asked for with no game clock to count on (#4503)
CHANGED = "changed"  # an Undo refused: something done since is in the way (#4156)
TOO_LATE = "too_late"  # an Undo past the store's limit (#4156)
LINK_UNDO = "link-undo"  # takes back the change batch of one of the DM's own connection edits
NAME_FIRST = "name_first"  # a new name in it has to be answered first (in Check new names)
STOPPED = "stopped_recording"  # someone whose words it holds is no longer recorded
CANNOT_WRITE = "cannot_write"  # the rules refuse to write that fact now
QUEUE_FULL = "queue_full"  # too many connections wait already: answer some of them first
KNOWN_NAME = "known_name"  # naming a person the story never named: DMbot knows that name (#2808)
AMBIGUOUS = "ambiguous"  # ... and more than one entry has it: nothing is written

WAITING = (
    "claim-confirm",
    "claim-some",  # 👥 Some know (#4680): kept held back, known by exactly the ticked characters
    "claim-reject",
    "claim-pick",  # "Which one?" for a claim kept because a name could be two entries (#3463)
    "claim-unpick",
    "claim-undo",  # takes back one fact of an "All true" / "All secret" press (#3645)
    "backstory-keep",
    # "Let players see it" (#4432): a keep that also lets the players see a connection it replaces
    # that was hidden (`keep_all(players="show")`, #4438). The plain `backstory-keep` never does.
    "backstory-keep-show",
    "backstory-keep-some",  # 👥 Some know (#4680 part 2): held back, exactly the ticked know it
    "backstory-skip",
    "backstory-keep-all",
    "backstory-which-pick",
    "backstory-which-neither",
)
READING_ACTIONS = ("reading-place", "reading-no", "reading-undo")
# People the story described and never named (#2790, #2808): give them a name, say they are a
# name DMbot knows, take either back. The entry is a proposed unnamed one, not a confirmed name.
UNNAMED_ACTIONS = ("name", "name-join", "name-undo")
# "Is this the same place?" (#3533): the DM confirms or turns down a proposed other name of a place.
SPELLING_ACTIONS = ("spelling-yes", "spelling-no")
# "Add all" for the names an upload found (#3207): say yes to them by kind, or take that press back.
CONFIRM_ACTIONS = ("confirm-kinds", "confirm-undo")
# The import clock offer on the website (#3199): set the clock where an imported session ended, or
# not now. The target is the imported session; the result is written to its offer row.
CLOCK_ACTIONS = ("clock-set", "clock-skip")
# "Tell a character" (#3542, #3089): the DM tells one player character that a secret other name is
# this entry, or takes the telling back.
TOLD_ACTIONS = ("told-add", "told-remove")
# DMbot's own matches (#3602, slice 4b): the DM says a name DMbot matched on its own is right, or
# takes it back. Filed under no name; the target is the match's batch, in digits.
MATCH_ACTIONS = ("match-confirm", "match-undo")
# A place's land, climate and ruler, and the switch for showing them to players (#4287): the text is
# "<value> <shade>", or "<group id> <how>" for the ruler, or blank for "Not sure".
MAP_ACTIONS = (
    "map-terrain", "map-climate", "map-control", "map-culture", "map-population", "map-show",
)  # fmt: skip
# World notes (#4285, slice 1c): ✅ on the lines the page showed (their ids, space-separated), 🚫 on
# one line, or ↩ Undo of one of those (the text is its change number). Filed under no name.
WORLD_ACTIONS = ("world-keep", "world-not", "world-undo", "world-bias")
ROUTE_LENGTH_ACTIONS = ("route-length", "route-length-keep", "route-undo")  # (#4392)
ROAD_CONDITION_ACTIONS = ("road-condition", "road-condition-keep", "road-condition-show")  # (#4451)
THREAD_STATE_ACTIONS = ("thread-state", "thread-check")  # a thread's state (#3696 slice 2a)
THREAD_INVOLVE_ACTION = "thread-involve"  # who is involved in a thread (#3696 slice 2b)
THREAD_NEW_ACTIONS = ("thread-new", "thread-due", "thread-party")  # (#3696 slice 2c)
THREAD_LINK_ACTIONS = ("thread-link", "thread-led-to")  # an event, what led to what (slice 2d)
REVEALS_ACTIONS = ("reveals-ask", "reveals-keep", "reveals-not")  # what they learned (#3863)
CONTINUITY_ACTIONS = ("continuity-ask", "continuity-answer")  # the heads-ups (#3695 half 1)
# "Names that look alike" on the website (#5259): merge a group (filed under the kept name), say a
# group is not the same, or take a merge back; the same calls as Discord's group screen.
LOOKALIKE_APART = "lookalike-apart"
LOOKALIKE_UNDO = "lookalike-undo"
MORE_NAMES_ACTION = "names-more"  # 📝 More names: the scan's waiting names (#5081, #5055 B)
LEAVE_ACTIONS = ("leave-yes", "leave-no")  # "Still in the party?" (#4895, party knowledge slice 6a)
DEED_ACTIONS = ("deed-record", "deed-not", "deed-undo")  # deeds the party did (#3591)
JOIN_ACTIONS = (  # the DM's join question (#4778) and "Let me choose" (#4799)
    "join-everyone", "join-party", "join-undo", "join-choices", "join-choose",
)  # fmt: skip
PARTY_ACTIONS = (
    "party-leave",
    "party-back",
    "party-undo",
    "party-catch-up-choices",
    "party-catch-up",
)  # who is in the party (#4778)
HEARD_ACTIONS = (
    "heard-pick",
    "heard-keep",
    "heard-type",
)  # "Did they mean…?" from the page (#4841 B)
RULE_UPDATE_ACTIONS = (
    "rules-update-pick",
    "rules-update-undo",
    "rules-update-undo-last",
    "pack-update-undo-last",  # the Undo of a followed pack's taken update (#4841 packs)
    "pack-update-pick",  # Pick changes on a followed pack, and its Undo (#4841 packs P3)
    "pack-update-undo",
)  # a followed set's pick (#4841)
# The story threads DMbot found in an upload (#4457, quests slice 1): the same store calls as
# Discord's 📜 card, so each keep is one change and one Undo.
THREAD_ACTIONS = ("thread-keep", "thread-not", "thread-undo")
LINES_WORD = re.compile(r"\+(-|[0-9]{1,2}(,[0-9]{1,2}){0,49})")  # (a keep's lines left on, #4501)
# The changes to a kept thread's parts and branches (#4476, quests slice 2): the same store calls as
# Discord's 🧵 "📋 Steps, rewards and more", each one change and one Undo.
PART_ACTIONS = (
    "part-add", "part-fix", "part-state", "part-show", "part-remove", "branch-move", "branch-not",
    "part-undo", "part-date",
)  # fmt: skip
# A character's languages from the website (#4271, #4229 slice 1b): the same store calls as
# Discord's 🗣️ Languages screen, each one change and one Undo.
LANG_ACTIONS = ("lang-add", "lang-remove", "lang-check", "lang-undo")
# The 🤫 Known or secret? answers from the website (#4598): the same store call as Discord's card
# (`answer_secret_question`), a make is one change and one Undo.
SECRET_ACTIONS = ("secret-keep", "secret-make", "secret-undo")
# The "Progress to check" answers from the website (#4616): the same store calls as Discord's 🧵
# card (`keep_thread_progress`, `turn_down`, `unturn`), a keep is one change and one Undo.
PROGRESS_ACTIONS = ("progress-keep", "progress-none", "progress-undo")
# A story thread's clues from the website (#4625): the same store calls as Discord's 🔎 Clues
# screen (`tie_clue`, `set_clue_word`, `untie_clue`), each one change and one Undo.
CLUE_ACTIONS = ("clue-add", "clue-word", "clue-off", "clue-undo")
# The Maps page's presses (#4347): a DM's yes or no to using a map (`<map id> yes|no`), Show to
# the table or Hide (`<map id> shown|hidden`), Remove (`<map id>`), a poster's Take it back
# (`<map id>`), and ↩ Undo of one of those (the text is its change number). Filed under no name.
# Only a DM may use, show or remove; the poster alone may take their own map back, so these run
# before the "sender is a DM" check, which a player is never meant to pass. (`MAP_ACTIONS` above
# is the place's land, climate and ruler.)
MAPS_PAGE_ACTIONS = (
    "maps-use", "maps-show", "maps-remove", "maps-takeback", "maps-undo", "maps-add",
)  # fmt: skip
# What a campaign's player (not a DM) may do on the Maps page: send a map, take back their own, and
# Undo one of those two; the map store checks it is their own map (#4347)
PLAYER_MAPS_ACTIONS = ("maps-add", "maps-takeback", "maps-undo")


async def _map_fact(
    memory: MemoryStore, guild_id: int, cid: str, entity_id: str, a: Action
) -> Outcome:
    """Run one of `MAP_ACTIONS` through the store's own rules (which place, which layer, which
    group), so the website can never write what Discord's menus refuse."""
    try:
        if a.action == "map-show":
            await memory.show_map_facts(guild_id, cid, entity_id, a.flag, source=DM)
        elif a.action in ("map-culture", "map-population"):
            # (#4412) `<lineage | culture id> <how>` or `other <how> <words>`; `-` is no how, and
            # no text clears the layer (every lineage), as Discord's pickers do
            first, _, rest = a.text.partition(" ")
            how, words = rest, ""
            if first == "other":
                how, _, words = rest.partition(" ")
            await memory.set_map_fact(
                guild_id, cid, entity_id, a.action[4:], first or None,
                how="" if how == "-" else how, words=words, source=DM,
            )  # fmt: skip
        else:
            first, _, how = a.text.partition(" ")
            await memory.set_map_fact(
                guild_id, cid, entity_id, a.action[4:], first or None, how=how, source=DM
            )
    except MemoryRuleError as exc:  # gone since it was queued, not a place, or not allowed there
        return _failed(NOT_FOUND if str(exc) == STORE_NOT_FOUND else BAD_TARGET)
    return Outcome(result_id=entity_id)


# The "Met but not named" list (#4045): `entity_id` names the list's row (memory_met), not an entry.
MET_ACTIONS = ("met-name", "met-join", "met-remove")
MET_WAITING = "met_waiting"  # the name is still waiting in the name check: check it there first
NAMES_FIXED_ACTIONS = ("names-fixed-undo",)  # the recap page puts a name-pass fix back (#2684)
# What the page is told when a reading cannot be kept (the words are `placing`'s and `beliefs`').
READING_REFUSALS = {
    placing.WAITING_ALREADY: "waiting_already",
    placing.PLOT_IS_KNOWN: "plot_is_known",
    placing.UNKNOWN_CHARACTER: "unknown_character",
    placing.UNKNOWN_LIAR: "unknown_liar",
    placing.A_STATE: "bad_reading",
    placing.NOT_A_READING: "gone",
    beliefs.BAD_READING: "bad_reading",
    beliefs.ALREADY_KNOWN: "already_known",
}
CLAIM_REFUSALS = {
    claims_mod.GONE: GONE,
    claims_mod.NAME_FIRST: NAME_FIRST,
    claims_mod.STOPPED_NOW: STOPPED,
    # A claim that reads like an instruction is dropped for good as it is looked at, so the answer
    # is final: the question is gone, not "try again" (Supervisor, #2638).
    claims_mod.ODD_NOW: GONE,
    # (the page does not list a reading, so this is a stale press: the question is not there)
    claims_mod.A_READING: GONE,
}
ALIAS_KIND = "nickname"  # what the name card calls an other name the DM adds


@dataclass(frozen=True, slots=True)
class Action:
    id: str
    campaign_id: str
    entity_id: str
    user_id: int
    action: str
    kind: str = ""
    target_id: str = ""
    text: str = ""
    flag: bool = False
    claimed_at: int = 0  # this worker's claim: only it may finish the row


class ActionStore:
    def __init__(self, db: Database, clock: Callable[[], float] = time.time) -> None:
        self._db = db
        self._now = clock

    async def claim_next(self, guild_id: int) -> Action | None:
        """The oldest waiting action (or a stalled one) of this server, marked as being worked
        on. Oldest by `seq`, so one DM's quick "spell" then "merge" run in that order; one worker
        takes them one at a time."""
        now = int(self._now() * 1000)  # milliseconds, so two claims of one row never match
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                # (a retaken row starts its count again: a dead worker's "12 of 50" is not the new
                # worker's)
                "UPDATE name_actions SET state = 'working', claimed_at = %s, progress_done = 0,"
                " progress_total = 0, progress_at = 0 WHERE (guild_id, id)"
                " = (SELECT guild_id, id FROM name_actions WHERE guild_id = %s AND"
                " (state = 'waiting' OR (state = 'working' AND claimed_at < %s))"
                # Never past an earlier open action of the campaign: waiting (it may be locked by
                # another worker whose claim has not committed yet) or being applied. The order
                # made is the order done, even with two bots at once.
                " AND NOT EXISTS (SELECT 1 FROM name_actions p WHERE p.guild_id = %s"
                " AND p.campaign_id = name_actions.campaign_id AND p.seq < name_actions.seq"
                " AND (p.state = 'waiting' OR (p.state = 'working' AND p.claimed_at >= %s)))"
                " ORDER BY seq LIMIT 1 FOR UPDATE SKIP LOCKED)"
                " RETURNING id, campaign_id, entity_id, user_id, action, kind, target_id, text,"
                " flag",
                (now, guild_id, now - RETAKE_AFTER_S * 1000, guild_id, now - RETAKE_AFTER_S * 1000),
            )
            row = await cur.fetchone()
        if row is None:
            return None
        return Action(
            row["id"],
            row["campaign_id"],
            row["entity_id"],
            int(row["user_id"]),
            row["action"],
            row["kind"],
            row["target_id"],
            row["text"],
            bool(row["flag"]),
            now,
        )

    async def finish(
        self,
        guild_id: int,
        action: Action,
        *,
        error: str = "",
        result_id: str = "",
        result_other: str = "",
        result_counts: str = "",
        result_batches: Sequence[int] = (),
    ) -> bool:
        """Done, or failed with why, for the page to read. Only the worker that holds the current
        claim can write. False: not its row any more. `result_counts` and `result_batches` are an
        Add all's (#3207); an empty one leaves what `keep_result` already wrote for the row."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE name_actions SET state = %s, error = %s, result_id = %s,"
                " result_other = %s, result_counts = COALESCE(NULLIF(%s, ''), result_counts),"
                " result_batches = CASE WHEN cardinality(%s::bigint[]) > 0 THEN %s::bigint[]"
                " ELSE result_batches END"
                " WHERE guild_id = %s AND id = %s AND state = 'working' AND claimed_at = %s",
                (
                    "failed" if error else "done",
                    error,
                    # (the one failed answer that names an entry: "DMbot knows that name", #2808)
                    result_id if (not error or error == KNOWN_NAME) else "",
                    "" if error else result_other,
                    result_counts[:200],
                    list(result_batches)[:50],
                    list(result_batches)[:50],
                    guild_id,
                    action.id,
                    action.claimed_at,
                ),
            )
            return bool(cur.rowcount)

    async def found_of(
        self, guild_id: int, campaign_id: str, upload_id: str
    ) -> dict[str, list[str]] | None:
        """The names a finished lore upload proposed, kind by kind (ids only), or None when there is
        no such finished upload in this campaign (#3207); or, given an import's id, the names its
        finished sessions' scans proposed, together (#5276)."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT found FROM lore_uploads WHERE guild_id = %s AND campaign_id = %s"
                " AND id = %s AND state = 'done'",
                (guild_id, campaign_id, upload_id),
            )
            row = await cur.fetchone()
            if row is None:  # not an upload: an import's id (#5276), its sessions' names together
                cur = await conn.execute(
                    "SELECT found FROM session_imports WHERE guild_id = %s AND campaign_id = %s"
                    " AND import_id = %s AND state = 'done' ORDER BY imported_at, session_id",
                    (guild_id, campaign_id, upload_id),
                )
                rows = await cur.fetchall()
                if not rows:
                    return None
                merged: dict[str, list[str]] = {}
                for each in rows:
                    for k, v in (each["found"] or {}).items():
                        if isinstance(v, list):
                            known = merged.setdefault(str(k), [])
                            known += [str(i) for i in v if str(i) not in known]
                return merged
        if not isinstance(row["found"], dict):
            return None
        return {str(k): [str(i) for i in v] for k, v in row["found"].items() if isinstance(v, list)}

    async def keep_result(
        self, guild_id: int, action: Action, counts: str, batches: Sequence[int]
    ) -> bool:
        """What an Add all has done so far, written as it goes: a bot that stops before it can
        mark the row finished (and takes it again) must not add the same names twice nor lose the
        changes an Undo needs. Only from the worker that holds the claim."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE name_actions SET result_counts = %s, result_batches = %s"
                " WHERE guild_id = %s AND id = %s AND state = 'working' AND claimed_at = %s",
                (counts[:200], list(batches)[:50], guild_id, action.id, action.claimed_at),
            )
            return bool(cur.rowcount)

    async def kept_result(self, guild_id: int, action: Action) -> tuple[str, list[int]]:
        """What `keep_result` wrote for this row before a restart: (counts, change batches)."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT result_counts, result_batches FROM name_actions"
                " WHERE guild_id = %s AND id = %s",
                (guild_id, action.id),
            )
            row = await cur.fetchone()
        if row is None:
            return "", []
        return str(row["result_counts"]), [int(b) for b in row["result_batches"]]

    async def pressed(
        self, guild_id: int, campaign_id: str, action_id: str, user_id: int
    ) -> tuple[str, list[int]] | None:
        """(counts, change batches) of this person's finished Add all press, for its Undo; None
        when there is no such press, or it made nothing (#3207)."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT result_counts, result_batches FROM name_actions WHERE guild_id = %s"
                " AND campaign_id = %s AND id = %s AND user_id = %s AND action = 'confirm-kinds'"
                " AND state = 'done'",
                (guild_id, campaign_id, action_id, user_id),
            )
            row = await cur.fetchone()
        if row is None or not row["result_batches"]:
            return None
        return str(row["result_counts"]), [int(b) for b in row["result_batches"]]

    async def progress(self, guild_id: int, action: Action, done: int, total: int) -> bool:
        """How far a Keep all has got, for the page's "12 of 50" (#2924): both counts and the time
        in ONE update (the table refuses a done above its total), and only from the worker that
        holds the claim. Counts and times only. False: not its row any more."""
        total = max(total, 0)
        done = min(max(done, 0), total)
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE name_actions SET progress_done = %s, progress_total = %s,"
                " progress_at = %s"  # (Unix seconds; claimed_at is milliseconds)
                " WHERE guild_id = %s AND id = %s AND state = 'working' AND claimed_at = %s",
                (done, total, int(self._now()), guild_id, action.id, action.claimed_at),
            )
            return bool(cur.rowcount)

    async def named_batch(
        self, guild_id: int, campaign_id: str, user_id: int, action_id: str
    ) -> int | None:
        """The change batch of this DM's own done `name` or `name-join` (the action `action_id`),
        for its Undo; None when it is not theirs, not done, or has no batch (#2808)."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT result_other FROM name_actions WHERE guild_id = %s AND campaign_id = %s"
                " AND id = %s AND user_id = %s AND action IN ('name', 'name-join')"
                " AND state = 'done' AND result_other ~ '^[0-9]+$'",
                (guild_id, campaign_id, action_id, user_id),
            )
            row = await cur.fetchone()
        return None if row is None else int(row["result_other"])

    async def link_batches_with(
        self, guild_id: int, campaign_id: str, user_id: int, batch: int
    ) -> list[int]:
        """Every change of the one connection edit, by this DM, done, that made `batch` (#4156;
        one edit can make two, #4159), so an Undo can only take back what they did themselves from
        the website, and all of it. Empty: no such edit."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT result_batches FROM name_actions WHERE guild_id = %s AND campaign_id = %s"
                " AND user_id = %s AND action LIKE 'link-%%' AND action <> 'link-undo'"
                " AND state = 'done' AND %s = ANY (result_batches) LIMIT 1",
                (guild_id, campaign_id, user_id, batch),
            )
            row = await cur.fetchone()
        return [int(b) for b in row["result_batches"]] if row is not None else []

    async def world_batch_made(
        self, guild_id: int, campaign_id: str, user_id: int, batch: int
    ) -> bool:
        """Whether `batch` is the change this DM's own done World notes ✅, 🚫 or ✏️ made from the
        website (#4285), so the page's Undo can only take back what they did themselves."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT 1 FROM name_actions WHERE guild_id = %s AND campaign_id = %s"
                " AND user_id = %s AND action IN ('world-keep', 'world-not', 'world-bias')"
                " AND state = 'done' AND %s = ANY (result_batches) LIMIT 1",
                (guild_id, campaign_id, user_id, batch),
            )
            return await cur.fetchone() is not None

    async def lookalike_batch_made(
        self, guild_id: int, campaign_id: str, user_id: int, batch: int
    ) -> bool:
        """Whether `batch` is the change this DM's own done look-alike merge made from the website
        (#5259), so the page's Undo can only take back what they did themselves."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT 1 FROM name_actions WHERE guild_id = %s AND campaign_id = %s"
                " AND user_id = %s AND action = 'lookalike-merge'"
                " AND state = 'done' AND %s = ANY (result_batches) LIMIT 1",
                (guild_id, campaign_id, user_id, batch),
            )
            return await cur.fetchone() is not None

    async def route_batch_made(
        self, guild_id: int, campaign_id: str, user_id: int, batch: int
    ) -> bool:
        """Whether `batch` is the change this DM's own done route length action made from the
        website (#4392), so the page's Undo can only take back what they did themselves."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT 1 FROM name_actions WHERE guild_id = %s AND campaign_id = %s"
                " AND user_id = %s AND action IN ('route-length', 'route-length-keep',"
                " 'road-condition', 'road-condition-keep', 'road-condition-show')"
                " AND state = 'done' AND %s = ANY (result_batches) LIMIT 1",
                (guild_id, campaign_id, user_id, batch),
            )
            return await cur.fetchone() is not None

    async def party_batch_made(
        self, guild_id: int, campaign_id: str, user_id: int, batch: int
    ) -> tuple[str, str] | None:
        """The action and text of this DM's own done `party-leave`, `party-back` or `party-catch-up`
        that made `batch` (#4778, #4793), so the page's Undo can only take back what they did
        themselves."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT action, text FROM name_actions WHERE guild_id = %s AND campaign_id = %s"
                " AND user_id = %s AND action IN ('party-leave', 'party-back', 'party-catch-up')"
                " AND state = 'done' AND %s = ANY (result_batches) LIMIT 1",
                (guild_id, campaign_id, user_id, batch),
            )
            row = await cur.fetchone()
        return None if row is None else (str(row["action"]), str(row["text"]))

    async def deed_batch_made(
        self, guild_id: int, campaign_id: str, user_id: int, batch: int
    ) -> bool:
        """Whether `batch` is the change this DM's own done `deed-record` made from the website
        (#3591), so the page's Undo can only take back what they recorded themselves."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT 1 FROM name_actions WHERE guild_id = %s AND campaign_id = %s"
                " AND user_id = %s AND action = 'deed-record'"
                " AND state = 'done' AND %s = ANY (result_batches) LIMIT 1",
                (guild_id, campaign_id, user_id, batch),
            )
            return await cur.fetchone() is not None

    async def join_batch_made(
        self, guild_id: int, campaign_id: str, user_id: int, batch: int
    ) -> bool:
        """Whether `batch` is the change this DM's own done "What the party knows" made from the
        website (#4778), so the page's Undo can only take back what they did themselves."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT 1 FROM name_actions WHERE guild_id = %s AND campaign_id = %s"
                " AND user_id = %s AND action IN ('join-party', 'join-choose', 'leave-yes',"
                " 'reveals-keep')"
                " AND state = 'done' AND %s = ANY (result_batches) LIMIT 1",
                (guild_id, campaign_id, user_id, batch),
            )
            return await cur.fetchone() is not None

    async def thread_batch_made(
        self, guild_id: int, campaign_id: str, user_id: int, batch: int
    ) -> bool:
        """Whether `batch` is the change this DM's own done story-thread ✅ made from the website
        (#4457), so the page's Undo can only take back what they did themselves."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT 1 FROM name_actions WHERE guild_id = %s AND campaign_id = %s"
                " AND user_id = %s AND action = 'thread-keep'"
                " AND state = 'done' AND %s = ANY (result_batches) LIMIT 1",
                (guild_id, campaign_id, user_id, batch),
            )
            return await cur.fetchone() is not None

    async def part_batch_made(
        self, guild_id: int, campaign_id: str, user_id: int, batch: int
    ) -> bool:
        """Whether `batch` is the change this DM's own done part or branch action made from the
        website (#4476), so the page's Undo can only take back what they did themselves."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT 1 FROM name_actions WHERE guild_id = %s AND campaign_id = %s"
                " AND user_id = %s AND action = ANY(%s) AND state = 'done'"
                " AND %s = ANY (result_batches) LIMIT 1",
                (
                    guild_id,
                    campaign_id,
                    user_id,
                    [a for a in PART_ACTIONS if a != "part-undo"],
                    batch,
                ),
            )
            return await cur.fetchone() is not None

    async def lang_batch_made(
        self, guild_id: int, campaign_id: str, user_id: int, batch: int
    ) -> bool:
        """Whether `batch` is the change this DM's own done language action made from the website
        (#4271), so the page's Undo can only take back what they did themselves."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT 1 FROM name_actions WHERE guild_id = %s AND campaign_id = %s"
                " AND user_id = %s AND action = ANY(%s) AND state = 'done'"
                " AND %s = ANY (result_batches) LIMIT 1",
                (
                    guild_id,
                    campaign_id,
                    user_id,
                    [a for a in LANG_ACTIONS if a != "lang-undo"],
                    batch,
                ),
            )
            return await cur.fetchone() is not None

    async def secret_batch_made(
        self, guild_id: int, campaign_id: str, user_id: int, batch: int
    ) -> bool:
        """Whether `batch` is the change this DM's own done 🤫 answer made from the website
        (#4598), so the page's Undo can only take back what they did themselves."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT 1 FROM name_actions WHERE guild_id = %s AND campaign_id = %s"
                " AND user_id = %s AND action = ANY(%s) AND state = 'done'"
                " AND %s = ANY (result_batches) LIMIT 1",
                (
                    guild_id,
                    campaign_id,
                    user_id,
                    [a for a in SECRET_ACTIONS if a != "secret-undo"],
                    batch,
                ),
            )
            return await cur.fetchone() is not None

    async def progress_batch_made(
        self, guild_id: int, campaign_id: str, user_id: int, batch: int
    ) -> bool:
        """Whether `batch` is the change this DM's own done "Progress to check" keep made from the
        website (#4616), so the page's Undo can only take back what they did themselves."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT 1 FROM name_actions WHERE guild_id = %s AND campaign_id = %s"
                " AND user_id = %s AND action = 'progress-keep' AND state = 'done'"
                " AND %s = ANY (result_batches) LIMIT 1",
                (guild_id, campaign_id, user_id, batch),
            )
            return await cur.fetchone() is not None

    async def clue_batch_made(
        self, guild_id: int, campaign_id: str, user_id: int, batch: int
    ) -> bool:
        """Whether `batch` is the change this DM's own done clue action made from the website
        (#4625), so the page's Undo can only take back what they did themselves."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT 1 FROM name_actions WHERE guild_id = %s AND campaign_id = %s"
                " AND user_id = %s AND action = ANY(%s) AND state = 'done'"
                " AND %s = ANY (result_batches) LIMIT 1",
                (
                    guild_id,
                    campaign_id,
                    user_id,
                    [a for a in CLUE_ACTIONS if a != "clue-undo"],
                    batch,
                ),
            )
            return await cur.fetchone() is not None

    async def map_batch_made(
        self, guild_id: int, campaign_id: str, user_id: int, batch: int, *, own_only: bool = False
    ) -> bool:
        """Whether `batch` is the change this person's own done Maps press made from the website
        (#4347: a DM's use, show or remove, or a poster's take-back), so the page's Undo can only
        take back what they did themselves."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT 1 FROM name_actions WHERE guild_id = %s AND campaign_id = %s"
                " AND user_id = %s AND action = ANY(%s) AND state = 'done'"
                " AND %s = ANY (result_batches) LIMIT 1",
                (
                    guild_id,
                    campaign_id,
                    user_id,
                    # `own_only`: a person who is not a DM may Undo only what a player may do
                    ["maps-add", "maps-takeback"]
                    if own_only
                    else ["maps-use", "maps-show", "maps-remove", "maps-takeback", "maps-add"],
                    batch,
                ),
            )
            return await cur.fetchone() is not None

    async def claim_batch_made(
        self, guild_id: int, campaign_id: str, user_id: int, claim_id: str, batch: int
    ) -> bool:
        """Whether `batch` is the change this DM's own done `claim-confirm` (or `claim-some`) of
        this claim made, so an Undo can only take back what they answered themselves from the
        website (#3645)."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT 1 FROM name_actions WHERE guild_id = %s AND campaign_id = %s"
                " AND user_id = %s AND action IN ('claim-confirm', 'claim-some')"
                " AND target_id = %s AND state = 'done' AND result_other = %s",
                (guild_id, campaign_id, user_id, claim_id, str(batch)),
            )
            return await cur.fetchone() is not None

    async def kept_reading(
        self, guild_id: int, campaign_id: str, user_id: int, answer_id: str
    ) -> tuple[str, int] | None:
        """The keep of a reading that this DM made (the answer `answer_id`), as (the claim, the
        change batch) for its Undo; None when it is not theirs, not done, or already undone."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT target_id, result_other FROM name_actions WHERE guild_id = %s"
                " AND campaign_id = %s AND id = %s AND user_id = %s AND action = 'reading-place'"
                " AND state = 'done' AND result_other ~ '^[0-9]+$'",
                (guild_id, campaign_id, answer_id, user_id),
            )
            row = await cur.fetchone()
        return None if row is None else (str(row["target_id"]), int(row["result_other"]))

    async def spent(self, guild_id: int, campaign_id: str, answer_id: str) -> None:
        """The keep was undone: the same answer is not offered an Undo again."""
        async with self._db.guild(guild_id) as conn:
            # every answer that holds the same keep (a retried Place hands back the same batch)
            await conn.execute(
                "UPDATE name_actions SET result_other = '' WHERE guild_id = %s AND campaign_id = %s"
                " AND action = 'reading-place' AND result_other ~ '^[0-9]+$' AND result_other ="
                " (SELECT result_other FROM name_actions WHERE guild_id = %s AND campaign_id = %s"
                " AND id = %s)",
                (guild_id, campaign_id, guild_id, campaign_id, answer_id),
            )

    async def sweep(self, guild_id: int) -> int:
        """Delete actions older than `KEEP_FOR_S`, whatever their state."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "DELETE FROM name_actions WHERE guild_id = %s AND created_at < %s",
                (guild_id, int(self._now()) - KEEP_FOR_S),
            )
            return int(cur.rowcount)


@dataclass(frozen=True, slots=True)
class Outcome:
    """`error` is "" when it worked. `result_id` is the name whose row the page shows next: the
    changed name, or the one a merge kept; empty for a removal."""

    error: str = ""
    result_id: str = ""
    result_other: str = ""  # a connection's other name: the pair the page draws next (#2323)
    counts: str = ""  # what an Add all did, counts only: "place:5 npc:7" (#3207)
    batches: tuple[int, ...] = ()  # the changes it made, so one Undo takes them all back


def _failed(why: str) -> Outcome:
    return Outcome(error=why)


@dataclass(frozen=True, slots=True)
class Waiting:
    """What the answers to waiting connections need besides the memory store (#2635): the claims
    PlotBot found in a session, the connections a backstory found, and who may be recorded now."""

    claims: ClaimStore
    backstory: BackstoryLinks
    opted_in: Callable[[], Awaitable[Collection[int]]]
    # An earlier keep of a reading, for its Undo: given the campaign, the DM and the answer, the
    # (claim id, change batch), or None when it is not theirs, not done, or already undone (#2412).
    # `spent` marks the Undo as used.
    kept_reading: Callable[[str, int, str], Awaitable[tuple[str, int] | None]] | None = None
    spent: Callable[[str, str], Awaitable[None]] | None = None
    # Put a name-pass fix back (campaign, Undo number): how many lines went back, 0 when nothing
    # could (#2684). The Discord Undo makes the same calls.
    name_pass_undo: Callable[[str, int], Awaitable[int | None]] | None = None
    # Tell the page how far a Keep all has got: (the action, done, total). Never needed for the
    # work itself, so a failure to write it must not fail the keep (#2924).
    progress: Callable[[Action, int, int], Awaitable[object]] | None = None


async def apply(
    memory: MemoryStore,
    guild_id: int,
    dm_user_ids: Collection[int],
    action: Action,
    waiting: Waiting | None = None,
    named_batch: Callable[[str, int, str], Awaitable[int | None]] | None = None,
    look_up: PlayerLookupFn | None = None,
    actions: ActionStore | None = None,
    clock: ClockChoiceFn | None = None,
    maps: MapStore | None = None,
    uploads: MapUploads | None = None,
    reads: Callable[[int], Awaitable[bool]] | None = None,
    game_now: GameNowFn | None = None,
    lore_pdfs: lore_pdfs_mod.LorePdfs | None = None,
    rules: RuleTools | None = None,
    heard: HeardFn | None = None,
    join_choices: JoinChoicesFn | None = None,
    reveals_ask: RevealsAskFn | None = None,
    reveals_drop: RevealsDropFn | None = None,
    deeds: DeedAnswerFn | None = None,
    continuity_ask: ContinuityAskFn | None = None,
    continuity_answer: ContinuityAnswerFn | None = None,
    more_names: MoreNamesFn | None = None,
) -> Outcome:
    """Do one action the way Discord's name card does. Nothing here logs the DM's words."""
    if action.action in MAPS_PAGE_ACTIONS:
        return await _maps(memory, maps, actions, guild_id, dm_user_ids, action, uploads, reads)
    if action.user_id not in dm_user_ids:
        return _failed(NOT_ALLOWED)
    cid = action.campaign_id
    if action.action == "lore-pdf-estimate":
        # (#4762 step 2: count a staged lore PDF; the sender is a DM, checked above)
        return await lore_pdfs_mod.estimate(
            lore_pdfs, guild_id, cid, a=action, now=int(time.time())
        )
    if action.action in CLOCK_ACTIONS:
        if clock is None:
            return _failed(FAILED)
        # The offer's row says how it ended (set, dismissed, moved on, split); the action is done
        # whichever it was. The sender is a DM (checked above) and is checked again by `clock`.
        await clock(cid, action.target_id, action.action == "clock-set", action.user_id)
        return Outcome()
    if (
        action.action == "offer-clear"
    ):  # lift a Never or Not now on words (#3577); filed under no name
        await memory.clear_kind_offer(guild_id, cid, action.text)
        return Outcome()  # (lifting words that are already lifted is done, not a failure)
    if action.action in CONFIRM_ACTIONS:
        if actions is None:
            return _failed(FAILED)
        return await _confirm(memory, actions, guild_id, cid, action)
    if action.action in MATCH_ACTIONS:
        return await _match(memory, guild_id, cid, action)
    if action.action in ROUTE_LENGTH_ACTIONS:
        if actions is None and action.action == "route-undo":
            return _failed(FAILED)
        return await _route_length(memory, actions, guild_id, cid, action)
    if action.action in ROAD_CONDITION_ACTIONS:
        return await _road_condition(memory, guild_id, cid, action)
    if action.action in WORLD_ACTIONS:
        if actions is None and action.action == "world-undo":
            return _failed(FAILED)
        return await _world(memory, actions, guild_id, cid, action)
    if action.action in PARTY_ACTIONS:
        if actions is None:
            return _failed(FAILED)
        return await _party(memory, actions, guild_id, cid, action)
    if action.action in HEARD_ACTIONS:
        if heard is None:
            return _failed(FAILED)
        return await _heard(heard, guild_id, action)
    if action.action in RULE_UPDATE_ACTIONS:
        if rules is None:
            return _failed(FAILED)
        return await _rule_update(rules, guild_id, cid, action)
    if action.action in JOIN_ACTIONS:
        if actions is None:
            return _failed(FAILED)
        return await _join(memory, actions, guild_id, cid, action, join_choices)
    if action.action in REVEALS_ACTIONS:
        return await _reveals(memory, waiting, guild_id, cid, action, reveals_ask, reveals_drop)
    if action.action in CONTINUITY_ACTIONS:
        return await _continuity(guild_id, cid, action, continuity_ask, continuity_answer)
    if action.action in (LOOKALIKE_APART, LOOKALIKE_UNDO):
        if actions is None and action.action == LOOKALIKE_UNDO:
            return _failed(FAILED)
        return await _look_alikes(memory, actions, guild_id, cid, action)
    if action.action == MORE_NAMES_ACTION:
        return await _more_names(more_names, actions, guild_id, cid, action)
    if action.action in LEAVE_ACTIONS:
        return await _leave(memory, guild_id, cid, action)
    if action.action in DEED_ACTIONS:
        if actions is None and action.action == "deed-undo":
            return _failed(FAILED)
        return await _deed(memory, actions, deeds, guild_id, cid, action)
    if action.action in THREAD_STATE_ACTIONS:
        return await _thread_state(memory, guild_id, cid, action)
    if action.action == THREAD_INVOLVE_ACTION:
        return await _thread_involve(memory, guild_id, cid, action)
    if action.action in THREAD_NEW_ACTIONS:
        return await _thread_new(memory, guild_id, cid, action)
    if action.action in THREAD_LINK_ACTIONS:
        return await _thread_link(memory, guild_id, cid, action)
    if action.action in PART_ACTIONS:
        if actions is None and action.action == "part-undo":
            return _failed(FAILED)
        return await _parts(memory, actions, guild_id, cid, action, game_now)
    if action.action in LANG_ACTIONS:
        if actions is None and action.action == "lang-undo":
            return _failed(FAILED)
        return await _languages(memory, actions, guild_id, cid, action)
    if action.action in SECRET_ACTIONS:
        if actions is None and action.action == "secret-undo":
            return _failed(FAILED)
        return await _secrets(memory, actions, guild_id, cid, action)
    if action.action in CLUE_ACTIONS:
        if actions is None and action.action == "clue-undo":
            return _failed(FAILED)
        return await _clues(memory, actions, guild_id, cid, action)
    if action.action in PROGRESS_ACTIONS:
        if actions is None and action.action == "progress-undo":
            return _failed(FAILED)
        return await _progress(memory, actions, guild_id, cid, action)
    if action.action in THREAD_ACTIONS:
        if actions is None and action.action == "thread-undo":
            return _failed(FAILED)
        return await _thread(memory, actions, guild_id, cid, action)
    if action.action == LINK_UNDO:
        if actions is None:
            return _failed(FAILED)
        return await _link_undo(memory, actions, guild_id, cid, action)
    if action.action in MET_ACTIONS:
        if waiting is None and action.action != "met-remove":
            return _failed(FAILED)  # (naming or joining needs who is recorded now)
        return await _met(memory, guild_id, cid, action, waiting)
    if action.action in UNNAMED_ACTIONS:
        try:
            return await _unnamed(memory, guild_id, cid, action, named_batch)
        except MemoryRuleError:
            return _failed(GONE)
    if action.action == "claim-undo":
        if actions is None or waiting is None:
            return _failed(FAILED)
        try:
            return await _claim_undo(memory, actions, guild_id, cid, action, waiting)
        except MemoryRuleError:
            return _failed(GONE)
    if (
        action.action in WAITING
        or action.action in READING_ACTIONS
        or action.action in NAMES_FIXED_ACTIONS
    ):
        if waiting is None:
            return _failed(FAILED)
        try:
            return await _answer_waiting(memory, guild_id, cid, action, waiting)
        except MemoryRuleError:
            return _failed(NOT_FOUND)
    entity = await memory.entity(guild_id, cid, action.entity_id)
    if entity is not None and entity.type != PERSON and entity.status != CONFIRMED:
        # A claim taken again after a first run that did its work but did not get to say so:
        # the wanted end state is already there, so it is done, not failed.
        if action.action == "remove" and entity.status == REJECTED:
            return Outcome()
        if action.action == "merge" and entity.merged_into == action.target_id:
            return Outcome(result_id=action.target_id)
    if (
        action.action == "remove"
        and entity is not None
        and entity.unnamed
        and entity.status == REJECTED
    ):
        return Outcome()  # taken again after a first run that did its work but did not say so
    if (
        action.action == "remove"
        and action.flag
        and entity is not None
        and entity.unnamed
        and entity.status in (PROPOSED, CONFIRMED)
    ):
        # A person the story never named (#2803), waiting or kept: removed like any name, in one
        # change (one batch, so it can be taken back); the door asked for the confirmation.
        try:
            await memory.set_entity_status(guild_id, cid, entity.id, REJECTED, source=DM)
        except MemoryRuleError:
            return _failed(NOT_FOUND)
        return Outcome()
    if entity is None or entity.status != CONFIRMED or entity.type == PERSON:
        return _failed(NOT_FOUND)
    try:
        return await _do(memory, guild_id, cid, entity.id, entity.name, action, look_up)
    except MemoryRuleError:
        return _failed(NOT_FOUND)  # the name changed under us (another DM, or Discord)


async def _met(
    memory: MemoryStore, guild_id: int, cid: str, a: Action, waiting: Waiting | None
) -> Outcome:
    """The website's "Met but not named" list (#4045): `entity_id` is the list's row. Name it (a
    new entry; a name the campaign has is EXISTS, so the page offers Same as…), join it to the
    entry `target_id`, or remove it. The same store calls as Discord's list, so each is one write
    (the entry, its rank 0 and the row's removal together)."""
    from dmbot.memory.met_store import MetStore
    from dmbot.memory.store import MET_GONE, MET_NAME_TAKEN, MET_NAME_WAITING

    try:
        if a.action == "met-remove":
            # (gone already is done too: a row taken again after a first run that removed it)
            await MetStore(memory._db).remove(guild_id, cid, a.entity_id)
            return Outcome()
        assert waiting is not None  # (checked by the caller for these two)
        recorded = await waiting.opted_in()  # (read now: the consent re-check at use, #4045)
        if a.action == "met-join":
            written = await memory.join_met(
                guild_id, cid, a.entity_id, a.target_id, opted_in=recorded
            )
            return Outcome(result_id=written.value.id)
        made = await memory.name_met(
            guild_id, cid, a.entity_id, " ".join(a.text.split()), opted_in=recorded
        )
        # met while the party was split (#4086): counted as the names' splits are, for the page
        split = {"group": "names_split_group:1", "unknown": "names_split_unknown:1"}
        return Outcome(result_id=made.value.id, counts=split.get(made.split, ""))
    except MemoryRuleError as exc:
        if str(exc) == MET_NAME_TAKEN:
            return _failed(EXISTS)
        if str(exc) == MET_NAME_WAITING:
            return _failed(MET_WAITING)
        if str(exc) == MET_GONE:
            return _failed(GONE)
        return _failed(NOT_FOUND if a.action == "met-join" else BAD_NAME)


async def _unnamed(
    memory: MemoryStore,
    guild_id: int,
    cid: str,
    a: Action,
    named_batch: Callable[[str, int, str], Awaitable[int | None]] | None,
) -> Outcome:
    """Give a person the story never named a name, say they are a name DMbot already knows, or
    take either back (#2808): the calls of Discord's "Give them a name" (`ui/name_unnamed.py`), so
    a change on either shows on both and the Undo works from either. Checked again here, in this
    campaign only. Nothing here logs the typed name."""
    if a.action == "name-undo":
        batch = None if named_batch is None else await named_batch(cid, a.user_id, a.target_id)
        if batch is None:
            return _failed(GONE)
        try:
            await memory.undo(guild_id, cid, batch)  # as Discord's Undo does
        except MemoryRuleError as exc:
            if str(exc) != ALREADY_UNDONE:  # a later change touched it: the page says it is gone
                raise
            # (undone already: a retried job whose first run did not get to say so)
        return Outcome(result_id=a.entity_id)
    entity = await memory.entity(guild_id, cid, a.entity_id)
    if (
        a.action == "name-join"
        and entity is not None
        and entity.merged_into == a.target_id
        and entity.status == MERGED
    ):
        # A retried job: it went in the first time. Its batch is not known now, so the page offers
        # no Undo for the retry (the first answer, if it got through, carries it).
        return Outcome(result_id=a.target_id)
    if entity is None or entity.status not in LIVE:
        return _failed(GONE)
    if a.action == "name-join":
        keep = await memory.entity(guild_id, cid, a.target_id)
        if not entity.unnamed or keep is None or keep.id == entity.id:
            return _failed(GONE)
        if keep.unnamed or keep.type == PERSON or keep.status != CONFIRMED:
            return _failed(GONE)  # (only a name the DM has confirmed can take someone in)
        if keep.type != entity.type:
            return _failed(GONE)  # (a person joins a person, a group a group, #3736)
        written = await memory.merge(
            guild_id, cid, keep.id, entity.id, source=DM, dm_said_same=True
        )
        return Outcome(
            result_id=keep.id, result_other="" if written.batch is None else str(written.batch)
        )
    new = " ".join(a.text.split())  # "name"
    if not entity.unnamed:
        if name_key(entity.name) == name_key(new):
            return Outcome(result_id=entity.id)  # a retried job: it went in the first time
        return _failed(GONE)
    if not new or PRONOUNCED.search(new):  # how to say it is not how to spell it (#2332)
        return _failed(BAD_NAME)
    found = CampaignLookup.build(await memory.lookup_data(guild_id, cid))
    others = {
        e.entity_id
        for e in found.exact(new)
        if e.entity_id != entity.id
        and not e.person
        and (other := found.entities.get(e.entity_id)) is not None
        and not other.unnamed
        and other.type == entity.type  # (only one of the same kind can take them in, #3736)
    }
    if len(others) > 1:
        return _failed(AMBIGUOUS)
    if others:
        return Outcome(error=KNOWN_NAME, result_id=next(iter(others)))
    try:
        written = await memory.rename_entity(guild_id, cid, entity.id, new, source=DM)
    except MemoryRuleError:
        return _failed(BAD_NAME)
    return Outcome(
        result_id=entity.id, result_other="" if written.batch is None else str(written.batch)
    )


async def _other(
    memory: MemoryStore, guild_id: int, campaign_id: str, entity_id: str, name: str, text: str
) -> str | None:
    """The id of the entry's other name spelled `text` (not the main name), if it has one."""
    try:
        own, wanted = lookup_key(name), lookup_key(text)
    except MemoryRuleError:
        return None
    if wanted == own:
        return None
    for a in await memory.aliases(guild_id, campaign_id, entity_id=entity_id, include_secret=True):
        if a.key == wanted and a.status != REJECTED:
            return a.id
    return None


async def _spelling(
    memory: MemoryStore, guild_id: int, campaign_id: str, entity_id: str, a: Action
) -> Outcome:
    """Yes confirms the proposed other name, No turns it down (a rejected name is what keeps the
    same pair from being offered again). The name must still be a proposed one of this place; one
    that is already in the wanted state is done, so a retaken action is not a failure."""
    wanted = CONFIRMED if a.action == "spelling-yes" else REJECTED
    for alias in await memory.aliases(
        guild_id, campaign_id, entity_id=entity_id, include_secret=True
    ):
        if alias.id != a.target_id:
            continue
        if alias.status == wanted:
            return Outcome(result_id=entity_id)
        if alias.status != PROPOSED:
            return _failed(GONE)  # answered the other way (in Discord, say) in the meantime
        await memory.update_alias(guild_id, campaign_id, alias.id, source=DM, status=wanted)
        return Outcome(result_id=entity_id)
    # `aliases` leaves out a turned-down name, so a No that is applied a second time (pressed twice
    # before the bot got to it; the door let both in, as the name was still proposed then) finds
    # nothing and is done, not failed. A Yes that finds nothing is gone: the name was turned down.
    return Outcome(result_id=entity_id) if a.action == "spelling-no" else _failed(GONE)


def _is_cannot_show(exc: MemoryRuleError) -> bool:
    """The refusal `link_edits.with_common` makes when 🌍 Common knowledge is asked for an item the
    players are not shown (and the item can't be shown by this answer): "Show X to the players
    first: common knowledge is something they see." The page tells the DM to show it first."""
    reason = str(exc)
    return reason.startswith("Show ") and reason.endswith("common knowledge is something they see.")


SHOWN_WORD = "shown"  # (`<answer> shown <ids>`: the characters the page's list showed, #4968)


def _who_of(text: str) -> tuple[str, list[str]]:
    """The website's answer as the door let it through: `dm`, `everyone`, `common` or
    `some <ids>`. An empty one is "" (the store refuses it as no answer)."""
    words = text.split()
    if SHOWN_WORD in words:  # (what the list showed is `_shown_of`'s, not part of the answer)
        words = words[: words.index(SHOWN_WORD)]
    return (words[0], words[1:]) if words else ("", [])


def _shown_of(text: str) -> list[str] | None:
    """The characters the website's list showed, when the answer says so (`... shown <ids>`,
    #4968), else None: an older page, which saves as before."""
    words = text.split()
    if SHOWN_WORD not in words:
        return None
    return words[words.index(SHOWN_WORD) + 1 :]


async def _alias_who(
    memory: MemoryStore, guild_id: int, campaign_id: str, entity_id: str, a: Action
) -> Outcome:
    """ "Who knows this?" for a secret other name of this entry (#2999, #4159): the name card's
    call, `name_edits.set_who_knows`, with the website's whole list of who knows it. A refusal (no
    character picked, one who isn't a confirmed character of this campaign, a name that can't be
    shown yet) is the page's "bad target", never a half change."""
    alias = next(
        (
            x
            for x in await memory.aliases(
                guild_id, campaign_id, entity_id=entity_id, include_secret=True
            )
            if x.id == a.target_id
        ),
        None,
    )
    if alias is None:
        return _failed(NOT_FOUND)
    who, characters = _who_of(a.text)
    made: list[int] = []
    try:  # (the same answer again writes nothing: the store only changes what differs)
        await name_edits.set_who_knows(
            memory, guild_id, campaign_id, alias, who, characters, made=made
        )
    except MemoryRuleError as exc:
        return _failed(CANNOT_SHOW if _is_cannot_show(exc) else BAD_TARGET)
    return Outcome(result_id=entity_id, batches=tuple(made))


async def _told(
    memory: MemoryStore, guild_id: int, campaign_id: str, entity_id: str, a: Action
) -> Outcome:
    """Tell one character that a secret other name of this entry is the entry, or take that back
    (#3542). The store holds the rules (only the DM, a secret name, a character who knows the
    name); a refusal is the page's "bad target", never a half change. Telling again is done, and
    so is taking back what is already taken back, so a retaken action is not a failure."""
    own = {
        x.id
        for x in await memory.aliases(
            guild_id, campaign_id, entity_id=entity_id, include_secret=True
        )
    }
    if a.target_id not in own:
        return _failed(NOT_FOUND)
    try:
        if a.action == "told-add":
            await memory.tell_character(guild_id, campaign_id, a.target_id, a.text, source=DM)
        else:
            await memory.take_back_told(guild_id, campaign_id, a.target_id, a.text, source=DM)
    except MemoryRuleError as exc:
        if a.action == "told-remove" and str(exc) == told.NOT_TOLD:
            return Outcome(result_id=entity_id)  # already taken back (pressed twice, or in Discord)
        return _failed(BAD_TARGET)
    return Outcome(result_id=entity_id)


async def _confirm(
    memory: MemoryStore, actions: ActionStore, guild_id: int, cid: str, a: Action
) -> Outcome:
    """The website's "Add all" (#3207), the way Discord's buttons do it: say yes to the names a
    finished upload proposed, by kind or all at once, one change (one Undo) for each kind; or take
    one press back. Only names still waiting are touched (one answered since is left alone), and
    the result is counts and change numbers, never a name. A kind the rules refuse is left out and
    the rest still go in, so what was added can always be taken back with one Undo."""
    if a.action == "confirm-undo":
        return await _confirm_undo(memory, actions, guild_id, cid, a)
    found = await actions.found_of(guild_id, cid, a.target_id)
    if found is None:
        return _failed(GONE)
    asked = a.text.split()
    kinds = [k for k in found if k not in NEEDS_A_LOOK] if asked == ["all"] else asked
    if not kinds or any(k in NEEDS_A_LOOK for k in kinds):
        return _failed(BAD_KIND)
    # A row taken again after a stop part way carries what it already did: not done twice.
    kept, batches = await actions.kept_result(guild_id, a)
    parts = kept.split()
    done_kinds = {p.partition(":")[0] for p in parts}
    refused = False
    for number, kind in enumerate(dict.fromkeys(kinds)):
        if kind in done_kinds:
            continue
        if not await actions.progress(guild_id, a, number, len(kinds)):
            return _failed(FAILED)  # not this worker's row any more
        try:
            written = await memory.confirm_kinds(
                guild_id, cid, found.get(kind, []), kind, source=DM
            )
        except MemoryRuleError:
            refused = True
            continue
        if written.value:
            parts.append(f"{kind}:{written.value}")
            if written.batch is not None:
                batches.append(written.batch)
            await actions.keep_result(guild_id, a, " ".join(parts), batches)
    await actions.progress(guild_id, a, len(kinds), len(kinds))
    if refused and not parts:
        return _failed(BAD_TARGET)
    return Outcome(counts=" ".join(parts), batches=tuple(batches))


async def _match(memory: MemoryStore, guild_id: int, cid: str, a: Action) -> Outcome:
    """The DM's word on a name DMbot matched on its own, from the page: ✅ confirms it, ↩️ takes it
    back, the same store calls as Discord's check (`confirm_join`, `undo`). Only a match that is
    still waiting can be touched: the batch is looked up in the campaign's waiting matches first
    (read again now, as the door only checked that the batch was one of DMbot's), so a made-up
    number never reaches `undo`, which would undo any change. `not_found` covers a match already
    answered or undone; `gone` is the store refusing (a name was changed since)."""
    if not (a.target_id.isascii() and a.target_id.isdigit()) or len(a.target_id) > 15:
        return _failed(NOT_FOUND)  # ("²" is a digit to Python and int() refuses it)
    batch = int(a.target_id)
    if not any(j.batch == batch for j in await memory.waiting_joins(guild_id, cid)):
        return _failed(NOT_FOUND)
    try:
        if a.action == "match-confirm":
            await memory.confirm_join(guild_id, cid, batch)
        else:
            await memory.undo(guild_id, cid, batch)
    except MemoryRuleError as exc:  # (TooLateToUndo is one: a name was changed since)
        return _failed(NOT_FOUND if str(exc) in (STORE_NOT_FOUND, ALREADY_UNDONE) else GONE)
    return Outcome()


async def _world(
    memory: MemoryStore, actions: ActionStore | None, guild_id: int, cid: str, a: Action
) -> Outcome:
    """World notes from the page (#4285), the same store calls as Discord's screen: ✅ keeps the
    lines the page showed (never one added since: the ids are the page's own), 🚫 takes one out,
    each one change, so one Undo. The store checks every line again, so one checked meanwhile (in
    Discord, or a second press) is `gone`. ↩ Undo takes back only a change this DM's own World
    notes action made, read again here (a made-up number must never reach `undo`)."""
    if a.action == "world-undo":
        assert actions is not None  # (checked by the caller)
        return await _world_undo(memory, actions, guild_id, cid, a)
    try:
        if a.action == "world-keep":
            written = await memory.keep_world_notes(guild_id, cid, a.text.split(), source=DM)
            return Outcome(
                counts=f"kept:{written.value}",
                batches=(written.batch,) if written.batch is not None else (),
            )
        if a.action == "world-bias":
            # ✏️ Change on a feeling: `bias:<row id> <step>`, kept at that step as the DM's own
            line, _, step = a.text.partition(" ")
            row_id = line.removeprefix("bias:")
            changed = await memory.change_read_bias(guild_id, cid, row_id, step, source=DM)
            return Outcome(batches=(changed.batch,) if changed.batch is not None else ())
        done = await memory.turn_down_world_note(guild_id, cid, a.text, source=DM)
    except MemoryRuleError:
        return _failed(GONE)
    return Outcome(batches=(done.batch,) if done.batch is not None else ())


NOT_IN_PARTY = "not_in_party"  # a "Has left the party" for a character not in the party now
CATCH_CHOICES_MAX = 4  # members one "Catch up" answer lists (`result_counts` is 200 characters)
NOTHING_LEFT = "nothing_left"  # a "Catch up" for a member who already knows all the rest know here
ALREADY_BACK = "already_back"  # a "Back in the party" (or an Undo of a leave) for one who is in it
NOT_A_PC = "not_a_pc"  # a "Back in the party" for a character who is no player character now


async def _party(
    memory: MemoryStore, actions: ActionStore, guild_id: int, cid: str, a: Action
) -> Outcome:
    """The DM's changes to who is in the party, from the character's page (#4778): the store's own
    `leave_party` and `back_in_party`, as Discord's card does, each one change with one Undo; the
    character keeps everything they knew (§6). ↩ Undo takes back only a change this DM's own done
    `party-leave` or `party-back` made, read again here (a made-up number must never reach `undo`);
    an Undo of a leave over a character who is back in the party since is refused as Discord's is.
    Coming back also leaves the question about the time away to the bot's own rounds (never during
    play), which send it as they send a join's."""
    if a.action == "party-undo":
        if not (a.text.isascii() and a.text.isdigit()) or len(a.text) > 15:
            return _failed(GONE)
        batch = int(a.text)
        made = await actions.party_batch_made(guild_id, cid, a.user_id, batch)
        if made is None:
            return _failed(GONE)
        kind, text = made
        if kind == "party-leave":
            # (a retried Undo finds them already back: told so, and nothing changes)
            entity = text.split(" ", 1)[0]
            if any(s.open for s in await memory.party_spans(guild_id, cid, entity)):
                return _failed(ALREADY_BACK)
        try:
            await memory.undo(guild_id, cid, batch)
        except TooLateToUndo:
            return _failed(TOO_LATE)
        except MemoryRuleError as exc:
            if str(exc) == ALREADY_UNDONE:
                return Outcome()  # (taken back already: a retried Undo)
            return _failed(GONE if str(exc) == STORE_NOT_FOUND else CHANGED)
        return Outcome()
    if a.action == "party-catch-up-choices":
        # Who is missing what on this card, from the store's own rule (`knowers.tell_counts`): ids
        # and counts only, `<member>=<how many>` in the party list's order; the page names them.
        try:
            choices = await memory.tell_choices(guild_id, cid, a.text)
        except MemoryRuleError:
            return _failed(GONE)
        # (the answer's column holds 200 characters: four pairs of a 32-character id and a count
        # always fit, with `;more=<k>` saying how many were left out, so the page can say so;
        # whoever is left over shows once these are caught up and it is read again)
        shown = choices[:CATCH_CHOICES_MAX]
        pairs = ",".join(f"{m}={n}" for m, _name, n in shown)
        return Outcome(counts=f"catch:{pairs};more={len(choices) - len(shown)}")
    if a.action == "party-catch-up":
        member, _, entry = a.text.partition(" ")
        try:
            told = await memory.tell_member(guild_id, cid, member, entry)
        except MemoryRuleError:
            return _failed(NOT_IN_PARTY)
        if not told.value:
            return _failed(NOTHING_LEFT)  # (another DM may have done it)
        return Outcome(counts=f"told:{told.value}", batches=(told.batch,) if told.batch else ())
    if a.action == "party-leave":
        entity, _, why = a.text.partition(" ")
        if why not in ENDED:
            return _failed(GONE)
        try:
            done = await memory.leave_party(guild_id, cid, entity, why)
        except MemoryRuleError:
            return _failed(NOT_IN_PARTY)
        return Outcome(batches=(done.batch,) if done.batch is not None else ())
    try:
        back = await memory.back_in_party(guild_id, cid, a.text)
    except MemoryRuleError:
        if any(s.open for s in await memory.party_spans(guild_id, cid, a.text)):
            return _failed(ALREADY_BACK)
        return _failed(NOT_A_PC)
    _span, waiting = back.value  # (which question waits for the character, if any)
    return Outcome(
        counts=f"asked:{waiting or 'none'}", batches=(back.batch,) if back.batch is not None else ()
    )


# "Did they mean…?" answered from the page: the bot's own function (`DMBot.answer_waiting_coded`),
# given the guild, the question's id, the pick ("keep" or an index) and the DM; what comes back has
# `code`, `done` and `reason` (never a word of the DM's).
HeardFn = Callable[[int, int, str, int, str | None], Awaitable[Any]]
HEARD_OK = frozenset({"kept", "fixed", "fixed-new", "line-kept"})  # the answer was saved
KEEP_PICK = "keep"  # `transcript.questions.KEEP`: the pick that keeps the words as heard
TYPE_PICK = "type"  # `transcript.questions.TYPE`: the pick that comes with a typed name


async def _heard(heard: HeardFn, guild_id: int, a: Action) -> Outcome:
    """The DM's answer to one waiting "Did they mean…?" from the page (#4841 B1): the same
    function as Discord's buttons, which checks the DM, the speaker's consent and the names again.
    A saved answer is its code in the counts ("heard:fixed"); anything else is the code as the
    error word (`only_dm`, `name_gone`, `expired`…), with a typed problem's reason in the counts.
    Nothing here logs a word."""
    question, _, index = a.text.partition(" ")
    if not (question.isdigit() and len(question) <= 15):
        return _failed(GONE)
    typed: str | None = None
    if a.action == "heard-keep":
        pick = KEEP_PICK
    elif a.action == "heard-type":
        pick, typed = TYPE_PICK, index  # the typed name is checked by the bot, never here
    elif index.isdigit() and len(index) == 1:
        pick = index
    else:
        return _failed(GONE)
    try:
        got = await heard(guild_id, int(question), pick, a.user_id, typed)
    except Exception:
        # (`log.exception` writes the traceback, never the typed words: they are not in a message)
        log.exception("A website answer to a waiting question failed")
        return _failed(FAILED)
    code = str(got.code)
    if code in HEARD_OK:
        return Outcome(counts=f"heard:{code}")
    reason = str(got.reason) if code == "typed-problem" and got.reason else ""
    return Outcome(error=code.replace("-", "_"), counts=f"reason:{reason}" if reason else "")


NOTHING_LEFT_OUT = "nothing_left_out"  # a pick that keeps every change: the page never takes it
TOO_MANY = "too_many"  # an update with more changes than the page can list to pick from
NO_ROOM = "no_room"  # the pick would put the campaign over its house-rule limit (Discord says so)
PICK_MAX = 50  # changes a pick can name (13 hex digits of the action's text)


async def _rule_update(tools: RuleTools, guild_id: int, cid: str, a: Action) -> Outcome:
    """The DM's Pick changes on a followed house-rules set, and its Undo, from the website (#4841
    C2): the same store calls as Discord's `dm_screen/set_update.take` and `UndoView._undo`, so no
    rule is copied. The pick is checked against the version the DM was shown (`changed` if a newer
    one came). Leaving anything out ends the follow and the campaign keeps its own copy; keeping
    everything is refused (`nothing_left_out`), since the page never takes an update itself:
    DMbot takes it at a session's start. The Undo handle is a row of `web_rule_takes`, for this DM
    only and only the newest, for a day."""
    if a.action == "rules-update-undo":
        return await _rule_undo(tools, guild_id, cid, a)
    if a.action == "rules-update-undo-last":
        return await _rule_undo_last(tools, guild_id, cid, a)
    if a.action == "pack-update-undo-last":
        return await _pack_undo_last(tools, guild_id, cid, a)
    if a.action == "pack-update-undo":
        return await _pack_undo(tools, guild_id, cid, a)
    if a.action == "pack-update-pick":
        return await _pack_pick(tools, guild_id, cid, a)
    version_text, _, picks_text = a.text.partition(" ")
    if not (version_text.isdigit() and re.fullmatch(r"[0-9a-f]{1,13}", picks_text)):
        return _failed(GONE)
    version, picks = int(version_text), int(picks_text, 16)
    update = await tools.sets.pending_update(guild_id, cid)
    if update is None:
        return _failed(GONE)  # nothing newer waits, or the set is no longer followed
    if update.offer.version != version:
        return _failed(CHANGED)  # a newer version came after the one the DM looked at
    items = len(update.changes) + len(update.def_changes)
    if items > PICK_MAX:
        return _failed(TOO_MANY)  # the page lists 50 and says so: pick the rest in Discord
    if picks >> items:
        return _failed(GONE)
    rules_in = len(update.changes)
    numbers = {c.number for i, c in enumerate(update.changes) if picks >> i & 1}
    keys = {d.key for i, d in enumerate(update.def_changes) if picks >> (rules_in + i) & 1}
    left = items - bin(picks).count("1")
    if left == 0:
        return _failed(NOTHING_LEFT_OUT)
    try:
        taken = await tools.sets.take_update(
            guild_id, cid, a.user_id, numbers, expected_version=version, accepted_defs=keys
        )
    except rule_sets.RuleSetError as exc:
        text = str(exc)
        if text == rule_sets.CHANGED_AGAIN:
            return _failed(CHANGED)
        if text in (rule_sets.NOT_FOLLOWING, rule_sets.NOTHING_NEW, rule_sets.NO_SUCH_SET):
            return _failed(GONE)
        if text == rule_sets.NO_ROOM:
            return _failed(NO_ROOM)
        return _failed(FAILED)
    except HouseRuleError:
        return _failed(FAILED)
    # (something is always left out, so the follow always ended: the kept relationships become
    # the campaign's own, as in Discord's `take`)
    def_batch: int | None = None
    if taken.merged:
        done = await set_sync.safely(set_sync.keep_as_own(tools.memory, guild_id, cid, taken))
        def_batch = done.batch if done is not None else None
    counts = f"picked:{items - left} left:{left}"
    try:
        saved = await tools.takes.save(guild_id, cid, a.user_id, taken, def_batch)
    except Exception:  # the pick is done: say so, without an Undo, rather than an error
        log.exception("A website pick's undo handle could not be saved")
        return Outcome(counts=counts)
    return Outcome(result_id=str(saved), counts=counts)


async def _rule_undo_last(tools: RuleTools, guild_id: int, cid: str, a: Action) -> Outcome:
    """↩️ Undo of the update DMbot already took (#4841 C3): Discord's note Undo, the same store call
    (`undo_last_take`), which keeps the copy before in the follow's own row and works only while
    the campaign is still on the version the page showed. The version sent is checked there, the
    DM too. The answer says which version the campaign is back on."""
    if not (a.text.isascii() and a.text.isdigit()) or len(a.text) > 9:
        return _failed(GONE)
    try:
        follow = await tools.sets.undo_last_take(guild_id, cid, a.user_id, int(a.text))
    except HouseRuleError:
        return _failed(TOO_LATE)  # already undone, or a newer update was taken
    await tools.settle(guild_id, cid)
    return Outcome(counts=f"back:{follow.previous_version}")


_PACK_UNDO = re.compile(r"([0-9a-f]{32}) ([0-9]{1,9})")


async def _pack_undo_last(tools: RuleTools, guild_id: int, cid: str, a: Action) -> Outcome:
    """↩️ Undo of the update DMbot already took on a followed pack (#4841 packs P2): Discord's note
    Undo, the same store call (`undo_last_pack_take`), only while the campaign is still on the
    version the page showed. A running session then reads the homebrew names again, as Discord's
    does. The answer says which version it went back to."""
    found = _PACK_UNDO.fullmatch(a.text)
    if found is None or tools.packs is None:
        return _failed(GONE if found is None else FAILED)
    try:
        follow = await tools.packs.undo_last_pack_take(
            guild_id, cid, a.user_id, found[1], int(found[2])
        )
    except HouseRuleError:
        return _failed(TOO_LATE)  # already undone, or a newer update was taken
    if tools.packs_changed is not None:
        tools.packs_changed(guild_id, cid)
    return Outcome(counts=f"back:{follow.previous_version}")


_PACK_PICK = re.compile(r"([0-9a-f]{32}) ([0-9]{1,9}) ([0-9a-f]{1,13})")


async def _pack_pick(tools: RuleTools, guild_id: int, cid: str, a: Action) -> Outcome:
    """The DM's Pick changes on a followed pack, from the website (#4841 packs P3): the same store
    call as Discord's `dm_screen/pack_update.take`. The pick is checked against the version the DM
    was shown (`changed` if a newer one came). Leaving anything out ends the follow and copies the
    pack into the campaign's own homebrew (entries the campaign already has stay its own, counted
    as `skipped`); keeping everything is refused, since the page never takes an update itself. The
    undo handle is a row of `web_rule_takes` of kind 'pack', for this DM only, for a day."""
    found = _PACK_PICK.fullmatch(a.text)
    if found is None:
        return _failed(GONE)
    if tools.packs is None:
        return _failed(FAILED)
    set_id, version, picks = found[1], int(found[2]), int(found[3], 16)
    update = await tools.packs.pending_pack_update(guild_id, cid, set_id)
    if update is None:
        return _failed(GONE)  # nothing newer waits, or the pack is no longer followed
    if update.offer.version != version:
        return _failed(CHANGED)  # a newer version came after the one the DM looked at
    items = len(update.changes)
    if items > PICK_MAX:
        return _failed(TOO_MANY)  # the page lists 50 and says so: pick the rest in Discord
    if picks >> items:
        return _failed(GONE)
    keys = {c.key for i, c in enumerate(update.changes) if picks >> i & 1}
    left = items - len(keys)
    if left == 0:
        return _failed(NOTHING_LEFT_OUT)
    try:
        taken = await tools.packs.take_pack_update(
            guild_id, cid, a.user_id, set_id, keys, expected_version=version
        )
    except rule_sets.RuleSetError as exc:
        text = str(exc)
        if text == rule_sets.CHANGED_AGAIN:
            return _failed(CHANGED)
        if text in (rule_sets.NOT_FOLLOWING, rule_sets.NOTHING_NEW, rule_sets.NO_SUCH_SET):
            return _failed(GONE)
        if text == homebrew.FULL:  # the copy would pass the campaign's homebrew limit
            return _failed(NO_ROOM)
        return _failed(FAILED)
    except HouseRuleError:
        return _failed(FAILED)
    if tools.packs_changed is not None:
        tools.packs_changed(guild_id, cid)
    counts = f"picked:{items - left} left:{left} skipped:{taken.skipped}"
    try:
        saved = await tools.takes.save_pack(guild_id, cid, a.user_id, taken)
    except Exception:  # the pick is done: say so, without an Undo, rather than an error
        log.exception("A website pack pick's undo handle could not be saved")
        return Outcome(counts=counts)
    return Outcome(result_id=str(saved), counts=counts)


async def _pack_undo(tools: RuleTools, guild_id: int, cid: str, a: Action) -> Outcome:
    """↩️ Undo of a pack pick: this DM's own, the newest handle for that pack, within the day; else
    Discord's too-late words on the page."""
    if not (a.text.isascii() and a.text.isdigit()) or len(a.text) > 15:
        return _failed(GONE)
    if tools.packs is None:
        return _failed(FAILED)
    take_id = int(a.text)
    saved = await tools.takes.get_pack(guild_id, cid, take_id)
    if saved is None or saved.user_id != a.user_id:
        return _failed(GONE)  # (none, or another DM's pick)
    if (
        not tools.takes.fresh_pack(saved)
        or await tools.takes.newest_pack_id(guild_id, cid, saved.taken.before.set_id) != take_id
    ):
        return _failed(TOO_LATE)
    try:
        kept = await tools.packs.undo_pack_take(guild_id, cid, a.user_id, saved.taken)
    except HouseRuleError:
        # (deliberately one word for the page: the campaign follows the pack again, is at its pack
        # limit, or the pack is gone; the card then reads again and shows what is true)
        return _failed(TOO_LATE)
    if tools.packs_changed is not None:
        tools.packs_changed(guild_id, cid)
    await tools.takes.delete(guild_id, cid, take_id)
    return Outcome(counts=f"undone:{kept}")


async def _rule_undo(tools: RuleTools, guild_id: int, cid: str, a: Action) -> Outcome:
    """↩️ Undo of a pick: this DM's own, the newest, within the day; else Discord's too-late
    words on the page."""
    if not (a.text.isascii() and a.text.isdigit()) or len(a.text) > 15:
        return _failed(GONE)
    take_id = int(a.text)
    saved = await tools.takes.get(guild_id, cid, take_id)
    if saved is None or saved.user_id != a.user_id:
        return _failed(GONE)  # (none, or another DM's pick)
    if not tools.takes.fresh(saved) or await tools.takes.newest_id(guild_id, cid) != take_id:
        return _failed(TOO_LATE)
    try:
        kept = await tools.sets.undo_take(guild_id, cid, a.user_id, saved.taken)
    except HouseRuleError:
        return _failed(TOO_LATE)  # the campaign follows something else now, or the set is gone
    if saved.taken.merged and saved.def_batch is not None:
        # the relationships go back as the set's before the follow is reconciled (as Discord's)
        await set_sync.safely(tools.memory.undo(guild_id, cid, saved.def_batch))
    await tools.settle(guild_id, cid)
    await tools.takes.delete(guild_id, cid, take_id)
    return Outcome(counts=f"undone:{kept}")


# "Let me choose" (#4799): the bot's own function, which works the choices out and keeps them for
# the DM's page (given the guild, the campaign, the question's group and the DM).
# It answers whether there was anything to choose (False: the question is gone or has no one left).
JoinChoicesFn = Callable[[int, str, str, int], Awaitable[bool]]
# (#3863) work out the waiting reveals in words for one DM and keep them for the page: (server,
# campaign, DM) -> how many; and take an answered proposal out of every DM's kept set.
RevealsAskFn = Callable[[int, str, int], Awaitable[int]]
RevealsDropFn = Callable[[int, str, str], Awaitable[None]]
# (#3695 half 1) work out the campaign's continuity heads-ups and keep their pointers for the page:
# (server, campaign, DM) -> how many; and answer one as the card's button does: (server, campaign,
# claim id, "c" or "n") -> APPLIED, GONE (not waiting any more, or someone who said it stopped) or
# REFUSED (a rule refused it; still waiting).
ContinuityAskFn = Callable[[int, str, int], Awaitable[int]]
# 📝 More names (#5081): (server, campaign, progress(done, of)) -> (names made, names still
# waiting): the bot's `add_more_names`, which Discord's button calls too.
MoreNamesFn = Callable[
    [int, str, Callable[[int, int], Awaitable[None]] | None], Awaitable[tuple[int, int]]
]
ContinuityAnswerFn = Callable[[int, str, str, str], Awaitable[str]]
APPLIED = "applied"
CHOSE_MAX = 10  # characters named in one pick's answer (the answer is 200 characters at most)


async def _leave(memory: MemoryStore, guild_id: int, cid: str, a: Action) -> Outcome:
    """The DM's answer to "Still in the party?" for one character, from the page (#4895): the same
    store calls as Discord's buttons. `leave-yes` ("Left the party") is the card's own call, one
    change whose number goes back for the page's Undo (`join-undo`, which takes back only a change
    this DM's own done `leave-yes` made; the Undo asks the question again); `leave-no` ("Still in
    the party") writes nothing but the answer. A question another DM already answered is `gone`."""
    if not re.fullmatch(r"[0-9a-f]{32}", a.text):
        return _failed(GONE)
    try:
        if a.action == "leave-no":
            await memory.staying_answer_stays(guild_id, cid, a.text)
            return Outcome()
        done = await memory.staying_answer_left(guild_id, cid, a.text)
    except (
        MemoryRuleError
    ):  # (AlreadyAnswered is one, and so is a character no longer in the party:
        return _failed(GONE)  # `gone` for both, as Discord answers both with "already answered")
    return _changed(done.batch)


# A deed answered from the page (#3591): the bot's own function (`deed_card.apply_answer`, the one
# place Discord's Record and Not this buttons go through), given the guild, the campaign, the
# deed's id and whether to record it. It returns (words, change batch), which the page never gets:
# only a short code and the batch. It raises `MemoryRuleError` (`deed_proposals.GONE` when the deed
# is no longer waiting).
DeedAnswerFn = Callable[[int, str, str, bool], Awaitable[tuple[str, int | None]]]
# The store said no to a record (a name in the deed DMbot does not know yet, or another of its
# rules): one neutral code, since the store's own words quote the deed and the page never gets them.
DEED_REFUSED = "refused"


async def _deed(
    memory: MemoryStore,
    actions: ActionStore | None,
    answer: DeedAnswerFn | None,
    guild_id: int,
    cid: str,
    a: Action,
) -> Outcome:
    """The DM's answer to one deed from the page (#3591), the same function as Discord's card
    (consent is asked again inside it). A recorded deed is `deed:recorded` with its change for the
    page's Undo, a turned-down one `deed:not`; one already answered, or whose speaker stopped being
    recorded, is `gone`; a name DMbot does not know (or any other rule of the store) is
    `refused`, a code only, never the store's words (they quote the deed). `deed-undo` takes
    back only a change this DM's own done `deed-record` made, read again here (a made-up number
    must never reach `undo`), as the card's own Undo does (`source` DM); past the store's limit it
    is `too_late`. Nothing here logs a word of the deed."""
    if a.action == "deed-undo":
        if actions is None or not (a.text.isascii() and a.text.isdigit()) or len(a.text) > 15:
            return _failed(GONE)
        batch = int(a.text)
        if not await actions.deed_batch_made(guild_id, cid, a.user_id, batch):
            return _failed(GONE)
        try:
            await memory.undo(guild_id, cid, batch, source=DM)
        except TooLateToUndo:
            return _failed(TOO_LATE)
        except MemoryRuleError as exc:
            if str(exc) == ALREADY_UNDONE:
                return Outcome()  # (taken back already: a retried Undo)
            return _failed(GONE if str(exc) == STORE_NOT_FOUND else CHANGED)
        return Outcome()
    if answer is None:
        return _failed(FAILED)
    if not re.fullmatch(r"[0-9a-f]{32}", a.text):
        return _failed(GONE)
    record = a.action == "deed-record"
    try:
        _words, batch_made = await answer(guild_id, cid, a.text, record)
    except MemoryRuleError as exc:
        return _failed(GONE if str(exc) == DEED_PROPOSALS_GONE else DEED_REFUSED)
    except Exception:
        log.exception("A website answer to a deed failed")  # (the traceback, never the deed)
        return _failed(FAILED)
    if not record:
        return Outcome(counts="deed:not")
    return Outcome(counts="deed:recorded", batches=(batch_made,) if batch_made is not None else ())


async def _thread_state(memory: MemoryStore, guild_id: int, cid: str, a: Action) -> Outcome:
    """A plot thread's state from the page (#3696 slice 2a), the same store calls as the 🧵 card's
    buttons: `thread-state` (`<thread id> <state>`) is `set_condition`, and the state it is already
    in counts as checked, as on the card; `thread-check` is "Checked, still going" (`check_thread`).
    Each is one change and, as on the card, has no Undo. Only a confirmed thread, quest, mystery,
    hook, goal or promise of this campaign: `set_condition` also moves a character's or an item's
    state, so what the id names is read again here (a made-up id must never reach it). One that is
    gone, merged away or not a thread is `gone`; `check_thread` refuses a thread that is not live
    (`gone` too: the page's list was out of date)."""
    thread_id, _, state = a.text.partition(" ")
    # (the queue door's shape check already limits `state` to the five words; a word that is odd
    # anyway is refused by `set_condition` and answered `gone` below)
    entity = await memory.entity(guild_id, cid, thread_id)
    if (
        entity is None
        or entity.type not in THREAD_TYPES
        or entity.status != CONFIRMED
        or entity.merged_into is not None
    ):
        return _failed(GONE)
    try:
        if a.action == "thread-check":
            await memory.check_thread(guild_id, cid, thread_id, source=DM)
            return Outcome()
        moved = await memory.set_condition(guild_id, cid, thread_id, state, source=DM)
        if moved.value[0] is None and state in thread_model.LIVE_STATES:
            # The state it is already in: the DM means "still", so it counts as checked.
            await memory.check_thread(guild_id, cid, thread_id, source=DM)
    except MemoryRuleError:
        return _failed(GONE)
    return Outcome()


async def _thread_involve(memory: MemoryStore, guild_id: int, cid: str, a: Action) -> Outcome:
    """The card's "👥 Add who's involved" from the page (#3696 slice 2b): its own store call,
    `involve`. The text is `<thread id> <who's id>[ <their part>]` (the door limits the shape
    and the part to 60 characters; an empty part is kept, as the card's "optional" box says,
    #4937, #4940). As the card's name search does, only a
    confirmed character, group, place, item or other entry that is not a person card, a deed or a
    thread and is not the thread itself can be added, and the thread must be a confirmed, un-merged
    thread of this campaign: anything else is `gone`. One change, no Undo, as on the card."""
    thread_id, _, rest = a.text.partition(" ")
    who_id, _, role = rest.partition(" ")
    thread = await memory.entity(guild_id, cid, thread_id)
    who = await memory.entity(guild_id, cid, who_id)
    if (
        thread is None
        or thread.type not in THREAD_TYPES
        or thread.status != CONFIRMED
        or thread.merged_into is not None
        or who is None
        or who.id == thread.id
        or who.status != CONFIRMED
        or who.merged_into is not None
        or who.type in (*THREAD_TYPES, "person", DEED)
    ):
        return _failed(GONE)
    try:
        await memory.involve(guild_id, cid, thread.id, who.id, role, source=DM)
    except MemoryRuleError:
        return _failed(GONE)
    return Outcome()


async def _thread_new(memory: MemoryStore, guild_id: int, cid: str, a: Action) -> Outcome:
    """A new thread or promise, a promise's due note, and who made a promise or who it was made to,
    from the page (#3696 slice 2c): the 🧵 card's own store calls (`new_thread`, `set_due`,
    `promise_party`), one change each and, as on the card, no Undo. `thread-new` is
    `<kind> <state> <name>` and answers with the new thread's id (so the page can follow with a due
    note for a promise, as the card's form does in two calls); `thread-due` is
    `<promise id>[ <note>]` (empty clears it); `thread-party` is `<promise id> by|to <who's id>`.
    The door limits the shapes. The thread or promise must be a confirmed, un-merged entry of this
    campaign (`gone` otherwise); the store itself refuses what is not a promise, a kind that is not
    a thread kind and a `who` of the wrong kind, answered `gone` for an entry that went and
    `failed` for a rule the DM broke (a name too long or already taken)."""
    if a.action == "thread-new":
        kind, state, name = a.text.split(" ", 2)
        try:
            made = await memory.new_thread(guild_id, cid, name, kind, state, source=DM)
        except MemoryRuleError:
            return _failed(FAILED)
        return Outcome(result_id=made.value.id)
    thread_id, _, rest = a.text.partition(" ")
    thread = await memory.entity(guild_id, cid, thread_id)
    if (
        thread is None
        or thread.type != thread_model.PROMISE
        or thread.status != CONFIRMED
        or thread.merged_into is not None
    ):
        return _failed(GONE)
    try:
        if a.action == "thread-due":
            await memory.set_due(guild_id, cid, thread.id, rest, source=DM)
        else:
            side, _, who_id = rest.partition(" ")
            who = await memory.entity(guild_id, cid, who_id)
            if who is None or who.status != CONFIRMED or who.merged_into is not None:
                return _failed(GONE)
            await memory.promise_party(guild_id, cid, thread.id, who.id, to=side == "to", source=DM)
    except MemoryRuleError:
        return _failed(FAILED)
    return Outcome()


async def _thread_link(memory: MemoryStore, guild_id: int, cid: str, a: Action) -> Outcome:
    """The card's "📌 Link an event" and "Led to…" from the page (#3696 slice 2d), through the same
    store calls (`link_event`, `led_to`), so the thread rules and the DM check stay the card's
    (GraphEngineer: the link is the Story Graph's own connection, but the website does not use its
    generic edit). `thread-link` is `<thread id> moved|ended <event id>` or `<thread id>
    moved|ended new <event name>`: an event typed by name is made with its link in one write, hidden
    from the players like any name the DM adds by hand (#2404), as the card does (`link_new_event`,
    #4950, so a failed link leaves no event behind); `thread-led-to` is
    `<from id> <to id>` and the store refuses a link to itself, a loop and the wrong kinds
    (`failed`, nothing written). One change each, no Undo, as on the card. An entry that is
    gone, merged away or not confirmed is `gone`."""

    async def usable(entry_id: str, kinds: tuple[str, ...]) -> Entity | None:
        entry = await memory.entity(guild_id, cid, entry_id)
        ok = entry is not None and entry.status == CONFIRMED and entry.merged_into is None
        return entry if ok and entry is not None and entry.type in kinds else None

    first, _, rest = a.text.partition(" ")
    try:
        if a.action == "thread-led-to":
            # (the store checks the kinds; a thread, event or clue leads to a thread or an event)
            source_entry = await memory.entity(guild_id, cid, first)
            target_entry = await memory.entity(guild_id, cid, rest)
            for entry in (source_entry, target_entry):
                if entry is None or entry.status != CONFIRMED or entry.merged_into is not None:
                    return _failed(GONE)
            await memory.led_to(guild_id, cid, first, rest, source=DM)
            return Outcome()
        thread = await usable(first, THREAD_TYPES)
        how, _, which = rest.partition(" ")
        if thread is None:
            return _failed(GONE)
        if which.startswith("new "):
            # (the page searches the names first and offers "new" only when nothing matches, as the
            # card's `link_by_name` does) The event and its link are ONE write, one change, so a
            # link that fails leaves no event behind (#4950, `link_new_event`).
            done = await memory.link_new_event(
                guild_id, cid, thread.id, which[4:], ends_it=how == "ended", source=DM
            )
            event_id = done.value.subject_id
        else:
            found = await usable(which, ("event",))
            if found is None:
                return _failed(GONE)
            event_id = found.id
            done = await memory.link_event(
                guild_id, cid, thread.id, event_id, ends_it=how == "ended", source=DM
            )
    except MemoryRuleError:
        return _failed(FAILED)
    return Outcome(result_id=event_id, batches=(done.batch,) if done.batch else ())


async def _reveals(
    memory: MemoryStore,
    waiting: Waiting | None,
    guild_id: int,
    cid: str,
    a: Action,
    ask: RevealsAskFn | None,
    drop: RevealsDropFn | None,
) -> Outcome:
    """What the characters learned, from the page (#3863 step 2), the same store calls as the
    🧠 card (`RevealProposals.keep` / `dismiss`, with the consent re-check inside). `reveals-ask`
    works the words out (no AI call: the gate and the names, read now) and keeps them for this DM's
    page, replacing the earlier set, so asking again adds nothing twice; the answer is `n:<how
    many>`. Keep gives the change for the page's Undo (`join-undo`); a keep that changed nothing
    (they already knew it) is `nothing_left`, not a failure; a proposal gone, or whose statement or
    speaker is gone, is `gone`. Either answer takes it out of the kept set at once. The queue is
    worked one action at a time, so an ask never lands between a press and its `drop`. A press may
    name any proposal of this campaign (the DM may answer one the page has not shown): the store
    scopes it to the campaign and the DM."""
    if a.action == "reveals-ask":
        if ask is None:
            return _failed(FAILED)
        try:
            return Outcome(counts=f"n:{await ask(guild_id, cid, a.user_id)}")
        except Exception as exc:  # (the type only: a database error can quote the row's words)
            log.warning("The waiting reveals could not be worked out (%s)", type(exc).__name__)
            return _failed(FAILED)
    if waiting is None:
        return _failed(FAILED)
    proposals = RevealProposals(memory)
    batch: int | None = None
    try:
        if a.action == "reveals-keep":
            batch = await proposals.keep(
                guild_id, cid, a.text, opted_in=await waiting.opted_in(), source=DM
            )
        else:
            await proposals.dismiss(guild_id, cid, a.text, source=DM)
    except MemoryRuleError:
        if drop is not None:
            await drop(guild_id, cid, a.text)
        return _failed(GONE)
    if drop is not None:
        await drop(guild_id, cid, a.text)
    if a.action == "reveals-keep" and batch is None:
        return _failed(NOTHING_LEFT)
    return Outcome(batches=(batch,) if batch is not None else ())


async def _more_names(
    more: MoreNamesFn | None, actions: ActionStore | None, guild_id: int, cid: str, a: Action
) -> Outcome:
    """📝 More names from the page (#5081): the bot's own `add_more_names`, the call Discord's
    button makes, so both make the same names to check. The sender is a DM of the campaign (checked
    above). The answer is `made:<N> left:<M>`, counts only; the page's wait shows how far it has got
    (`progress`, never needed for the work, so a failure to write it is let go). A failure leaves
    every name waiting (`add_more_names` puts back what it had not made)."""
    if more is None:
        return _failed(FAILED)

    async def moved(done: int, total: int) -> None:
        if actions is not None:
            with contextlib.suppress(Exception):
                await actions.progress(guild_id, a, done, total)

    try:
        made, left = await more(guild_id, cid, moved)
    except Exception as exc:  # (the type only: the names are the campaign's words)
        log.warning("More names from the page failed (%s)", type(exc).__name__)
        return _failed(FAILED)
    return Outcome(counts=f"made:{made} left:{left}")


async def _continuity(
    guild_id: int,
    cid: str,
    a: Action,
    ask: ContinuityAskFn | None,
    answer: ContinuityAnswerFn | None,
) -> Outcome:
    """The continuity heads-ups from the page (#3695 half 1), through the bot's own check and the
    card's own answer (`ContinuityStore.check` / `answer`, consent read at that moment). The sender
    is a DM of the campaign (checked above; the ask checks again). `continuity-ask` works the list
    out (no AI call) and keeps its pointers for the page, replacing the earlier set; the answer is
    `n:<how many>`. `continuity-answer` is "<claim id> c" (the story changed) or "<claim id> n" (it
    wasn't real), and only for a claim the last ask listed (the card answers only its own lines); a
    claim no longer listed or waiting (answered in Discord, say) is `gone`, and one whose answer a
    rule refused stays waiting and is `refused`, as on the card. There is no Undo,
    as on the card: "the story changed" is one change in the change log, which the fact's own Undo
    can take back. "Ignore" never reaches the queue: it writes nothing, on Discord too."""
    if a.action == "continuity-ask":
        if ask is None:
            return _failed(FAILED)
        try:
            return Outcome(counts=f"n:{await ask(guild_id, cid, a.user_id)}")
        except Exception as exc:  # (the type only: a database error can quote a row's words)
            log.warning("The continuity heads-ups could not be worked out (%s)", type(exc).__name__)
            return _failed(FAILED)
    claim_id, _, choice = a.text.partition(" ")
    if answer is None or not re.fullmatch(r"[0-9a-f]{32}", claim_id) or choice not in ("c", "n"):
        return _failed(FAILED)
    try:
        said = await answer(guild_id, cid, claim_id, choice)
    except Exception as exc:  # (the type only)
        log.warning("A continuity heads-up could not be answered (%s)", type(exc).__name__)
        return _failed(FAILED)
    if said == APPLIED:
        return Outcome(result_id=claim_id)
    return _failed(REFUSED if said == REFUSED else GONE)


async def _route_length(
    memory: MemoryStore, actions: ActionStore | None, guild_id: int, cid: str, a: Action
) -> Outcome:
    """A route's stated length from the page (#4392), the same store calls as Discord's 🛣️ Length
    and ✅ Length is right: `route-length` is `<c|p> <id>[ <words>]` (`set_route_length`, empty
    words take it off), `route-length-keep` is `<c|p> <id>` (`keep_route_length`: DMbot's read
    becomes the DM's). Each is one change with one Undo; nothing changed (the same words again)
    writes nothing and carries no change number. A route that is gone is `gone`; a keep of a length
    that is the DM's already or has changed is `changed` (the page's list was old). `route-undo`
    takes back only a change this DM's own done route action made, read again here (a made-up number
    must never reach `undo`)."""
    if a.action == "route-undo":
        assert actions is not None  # (checked by the caller)
        return await _route_undo(memory, actions, guild_id, cid, a)
    kind, _, rest = a.text.partition(" ")
    target, _, words = rest.partition(" ")
    if kind not in ("c", "p"):  # (the door's shape check says so too)
        return _failed(GONE)
    on = "connection" if kind == "c" else "place"
    try:
        batch: int | None
        if a.action == "route-length-keep":
            batch = (await memory.keep_route_length(guild_id, cid, target, on=on, source=DM)).batch
        else:
            batch = (
                await memory.set_route_length(guild_id, cid, target, words, on=on, source=DM)
            ).batch
    except MemoryRuleError as exc:
        text = str(exc)
        if text == ROUTE_GONE:
            return _failed(GONE)
        return _failed(CHANGED if text == LENGTH_CHANGED else FAILED)
    return Outcome(batches=(batch,) if batch is not None else ())


async def _road_condition(memory: MemoryStore, guild_id: int, cid: str, a: Action) -> Outcome:
    """A road's or route place's condition from the page (#4451), the same store calls as
    Discord's 🚧 Condition, ✅ Condition is right and 🙈 / 👁️. `road-condition` is
    `<c|p> <id> <value>` (`none` takes it off) or `<c|p> <id> o <words>`; `road-condition-keep` is
    `c <id>`; `road-condition-show` is `c <id> <1|0>`. Each is one change with one Undo
    (`route-undo`, which reads the number again); the same value again writes nothing and carries
    no change number. A road or place that is gone, or no longer a known road or a route, is
    `gone`; words the store refuses are `failed`."""
    kind, _, rest = a.text.partition(" ")
    target, _, what = rest.partition(" ")
    if kind not in ("c", "p") or (a.action != "road-condition" and kind != "c"):
        return _failed(GONE)
    try:
        batch: int | None
        if a.action == "road-condition-keep":
            batch = (await memory.keep_road_condition(guild_id, cid, target, source=DM)).batch
        elif a.action == "road-condition-show":
            shown = what == "1"
            batch = (
                await memory.show_road_condition(guild_id, cid, target, shown, source=DM)
            ).batch
        else:
            word, _, own = what.partition(" ")
            own_words = word == "o"
            value = None if word == "none" else (map_facts.OTHER if own_words else word)
            batch = (
                await memory.set_route_condition(
                    guild_id, cid, value, words=own if own_words else "",
                    place_id=target if kind == "p" else "",
                    relation_id=target if kind == "c" else "",
                    source=DM,
                )
            ).batch  # fmt: skip
    except MemoryRuleError as exc:
        text = str(exc)
        if text in (MAP_GONE, ONLY_KNOWN_ROAD, ONLY_ROUTES):
            return _failed(GONE)
        return _failed(FAILED)
    return Outcome(batches=(batch,) if batch is not None else ())


async def _look_alike_merge(
    memory: MemoryStore, guild_id: int, cid: str, keep_id: str, a: Action
) -> Outcome:
    """🔗 Merge on a group of look-alike names, as Discord's confirmation does it: the ticked names
    go into the kept one in one change (one Undo takes all of it back), then each name the DM left
    unticked is remembered as not the same as the kept one. The text is the merged ids, then
    optionally ` | ` and the unticked ids. A retried job whose merge went in already makes no second
    change and so offers no Undo."""
    merged_part, _, left_part = a.text.partition(" | ")
    ids = list(dict.fromkeys(merged_part.split()))
    left = [i for i in dict.fromkeys(left_part.split()) if i != keep_id and i not in ids]
    if not ids or keep_id in ids or len(ids) > MERGE_MANY_MAX:
        return _failed(BAD_TARGET)
    waiting: list[str] = []
    for other_id in ids:
        other = await memory.entity(guild_id, cid, other_id)
        if other is not None and other.merged_into == keep_id:
            continue  # a retried job: this one went in the first time
        if other is None or other.status != CONFIRMED or other.type == PERSON:
            return _failed(NOT_FOUND)
        waiting.append(other_id)
    batches: tuple[int, ...] = ()
    if waiting:
        try:
            written = await memory.merge_many(guild_id, cid, keep_id, waiting, source=DM)
        except MemoryRuleError:
            return _failed(BAD_TARGET)
        batches = (written.batch,) if written.batch is not None else ()
    for other_id in left:
        try:
            await memory.not_the_same(guild_id, cid, [keep_id, other_id], user_id=a.user_id)
        except MemoryRuleError:
            continue  # (a name that has gone since: nothing to remember)
    return Outcome(result_id=keep_id, batches=batches)


async def _look_alikes(
    memory: MemoryStore, actions: ActionStore | None, guild_id: int, cid: str, a: Action
) -> Outcome:
    """Not the same (`lookalike-apart`: every pair of the ids is remembered, so the group is not
    offered again) and ↩ Undo of a merge (`lookalike-undo`: the text is a change number, read
    again in this DM's own done merges before `undo` sees it, so a made-up number never undoes
    anything)."""
    if a.action == LOOKALIKE_APART:
        ids = list(dict.fromkeys(a.text.split()))
        if len(ids) < 2:
            return _failed(BAD_TARGET)
        try:
            await memory.not_the_same(guild_id, cid, ids, user_id=a.user_id)
        except MemoryRuleError:
            return _failed(NOT_FOUND)
        return Outcome()
    if actions is None or not (a.text.isascii() and a.text.isdigit()) or len(a.text) > 15:
        return _failed(GONE)
    batch = int(a.text)
    if not await actions.lookalike_batch_made(guild_id, cid, a.user_id, batch):
        return _failed(GONE)
    try:
        await memory.undo(guild_id, cid, batch)
    except TooLateToUndo:
        return _failed(TOO_LATE)
    except MemoryRuleError as exc:
        if str(exc) == ALREADY_UNDONE:
            return Outcome()  # (taken back already: a retried Undo)
        return _failed(GONE if str(exc) == STORE_NOT_FOUND else CHANGED)
    return Outcome()


async def _route_undo(
    memory: MemoryStore, actions: ActionStore, guild_id: int, cid: str, a: Action
) -> Outcome:
    """↩ Undo of one length change from the page, as `_world_undo` does it."""
    if not (a.text.isascii() and a.text.isdigit()) or len(a.text) > 15:
        return _failed(GONE)
    batch = int(a.text)
    if not await actions.route_batch_made(guild_id, cid, a.user_id, batch):
        return _failed(GONE)
    try:
        await memory.undo(guild_id, cid, batch)
    except TooLateToUndo:
        return _failed(TOO_LATE)
    except MemoryRuleError as exc:
        if str(exc) == ALREADY_UNDONE:
            return Outcome()  # (taken back already: a retried Undo)
        return _failed(GONE if str(exc) == STORE_NOT_FOUND else CHANGED)
    return Outcome()


async def _join_choose(
    memory: MemoryStore, join_choices: JoinChoicesFn | None, guild_id: int, cid: str, a: Action
) -> Outcome:
    """ "Let me choose" from the page: `join-choices` works the choices out and keeps them for this
    DM (the page reads them from its own door); `join-choose` gives one pick with
    `MemoryStore.join_choose`, which checks the question and what is left to give again, so a stale
    page can never give more than the bot would now. The answer is who was given how many things
    (`first:<0|1> chose:<id first 8>=<n>,…`, ids and counts only, whether this pick answered the
    question first), and the change for the page's Undo (`join-undo`, which takes back only a change
    this DM's own done pick made)."""
    group, _, pick = a.text.partition(" ")
    if a.action == "join-choices":
        if join_choices is None:
            return _failed(FAILED)
        try:
            anything = await join_choices(guild_id, cid, group, a.user_id)
        except Exception:
            log.exception("The choices for a join question could not be worked out")
            return _failed(FAILED)
        return Outcome() if anything else _failed(GONE)
    try:
        if pick[:1] == "s" and pick[1:].isdigit():
            done = await memory.join_choose(guild_id, cid, group, session_at=int(pick[1:]))
        elif pick[:1] == "t":
            done = await memory.join_choose(guild_id, cid, group, thread_id=pick[1:])
        else:
            return _failed(GONE)
    except MemoryRuleError:  # (AlreadyAnswered is one)
        return _failed(GONE)
    told, first = done.value
    if done.batch is None or not told:
        return _failed(NOTHING_LEFT)  # nothing was left to give from this pick
    # (`first` leads, so a long answer never cuts it off; at most `CHOSE_MAX` are named)
    shown = list(told.items())[:CHOSE_MAX]
    given = ",".join(f"{c[:8]}={n}" for c, n in shown)
    more = f";more={len(told) - len(shown)}" if len(told) > len(shown) else ""
    return Outcome(counts=f"first:{int(first)} chose:{given}{more}", batches=(done.batch,))


async def _join(
    memory: MemoryStore,
    actions: ActionStore,
    guild_id: int,
    cid: str,
    a: Action,
    join_choices: JoinChoicesFn | None = None,
) -> Outcome:
    """The DM's answer to the join question from the page (#4778 part b), the same store calls as
    Discord's buttons: "Only what everyone knows" writes nothing, "What the party knows" is one
    change with one Undo. The store checks the question is still waiting (another DM may have
    answered: `gone`). ↩ Undo takes back only a change this DM's own done `join-party` made, read
    again here (a made-up number must never reach `undo`)."""
    if a.action in ("join-choices", "join-choose"):
        return await _join_choose(memory, join_choices, guild_id, cid, a)
    if a.action == "join-undo":
        if not (a.text.isascii() and a.text.isdigit()) or len(a.text) > 15:
            return _failed(GONE)
        batch = int(a.text)
        if not await actions.join_batch_made(guild_id, cid, a.user_id, batch):
            return _failed(GONE)
        try:
            await memory.undo(guild_id, cid, batch)
        except TooLateToUndo:
            return _failed(TOO_LATE)
        except MemoryRuleError as exc:
            if str(exc) == ALREADY_UNDONE:
                return Outcome()  # (taken back already: a retried Undo)
            return _failed(GONE if str(exc) == STORE_NOT_FOUND else CHANGED)
        return Outcome()
    try:
        if a.action == "join-everyone":
            await memory.join_answer_everyone(guild_id, cid, a.text)
            return Outcome()
        done = await memory.join_answer_party(guild_id, cid, a.text)
    except MemoryRuleError:  # (AlreadyAnswered is one)
        return _failed(GONE)
    return Outcome(
        counts=f"given:{done.value}", batches=(done.batch,) if done.batch is not None else ()
    )


def _changed(batch: int | None) -> Outcome:
    """The change a website action made, for the page's Undo (none when nothing changed)."""
    return Outcome(batches=(batch,) if batch is not None else ())


def _none(word: str) -> str | None:
    """A `-` in a part action's text is "none" or "clear"."""
    return None if word == "-" else word


async def _parts(
    memory: MemoryStore,
    actions: ActionStore | None,
    guild_id: int,
    cid: str,
    a: Action,
    game_now: GameNowFn | None = None,
) -> Outcome:
    """A change to a kept thread's parts or branches from the page (#4476), through the same store
    calls as Discord's 🧵 card (`add_thread_part`, `correct_thread_part`, `set_thread_part_state`,
    `show_thread_part`, `remove_thread_part`, `branch_of`, `not_a_branch`), so each is one change
    and one Undo. The door checked the text's shape; the store checks the rest again, so a part or
    thread gone meanwhile is `gone`, and a refusal by the rules (a loop of branches, a thread no
    longer kept, a name that is not allowed) is `bad_target`. ↩ Undo takes back only a change this
    DM's own done part action made, read again here."""
    from dmbot.memory import thread_parts as tp

    try:
        if a.action == "part-undo":
            assert actions is not None  # (checked by the caller)
            batch = int(a.text)
            if not await actions.part_batch_made(guild_id, cid, a.user_id, batch):
                return _failed(GONE)
            await memory.undo(guild_id, cid, batch)
            return Outcome()
        words = a.text.split(" ")
        if a.action == "part-date":
            # N game days from the clock's now, counted here and never sent by the page, so the
            # website cannot write a date the clock would not; `-` clears (#4503).
            now = await game_now(cid) if game_now is not None else None
            if words[1] != "-" and now is None:
                return _failed(NO_CLOCK)
            minute = None if words[1] == "-" else tl.days_from(now or 0, int(words[1]))
            dated = await memory.set_time_limit_date(
                guild_id, cid, words[0], minute, now=now, source=DM
            )
            return _changed(dated.batch)
        if a.action == "part-add":
            thread, kind, state, due, limit, who, about, under = words[:8]
            made = await memory.add_thread_part(
                guild_id, cid, thread, kind, " ".join(words[8:]), source=DM, state=_none(state),
                due=_none(due) or "", limit_kind=_none(limit) or "", who_id=_none(who),
                about_id=_none(about), under_id=_none(under),
            )  # fmt: skip
            return Outcome(
                result_id=made.value.id, batches=(made.batch,) if made.batch is not None else ()
            )
        if a.action == "part-fix":
            part, field, value = a.text.split(" ", 2)
            changes: dict[str, Any] = {}
            if field in ("words", "words_story"):
                changes["words"] = value
            elif field == "terms":
                changes["terms"] = "" if value == "-" else value
            elif field == "due":
                changes["due"] = _none(value) or ""
            elif field == "limit":
                changes["limit_kind"] = _none(value) or ""
            else:  # who, about, under
                changes[f"{field}_id"] = _none(value)
            done = await memory.correct_thread_part(
                guild_id, cid, part, source=DM, in_story=field == "words_story", **changes
            )
            return Outcome(batches=(done.batch,) if done.batch is not None else ())
        if a.action == "part-state":
            set_to = await memory.set_thread_part_state(
                guild_id, cid, words[0], words[1], source=DM
            )
            return _changed(set_to.batch)
        if a.action == "part-show":
            shown = await memory.show_thread_part(
                guild_id, cid, words[0], words[1] == "1", source=DM
            )
            return _changed(shown.batch)
        if a.action == "part-remove":
            removed = await memory.remove_thread_part(guild_id, cid, words[0], source=DM)
            return _changed(removed.batch)
        if a.action == "branch-move":
            moved = await memory.branch_of(
                guild_id, cid, words[0], words[1], source=DM, move=words[-1] == "move"
            )
            return _changed(moved.batch)
        off = await memory.not_a_branch(guild_id, cid, words[0], source=DM)  # branch-not
        return _changed(off.batch)
    except MemoryRuleError as exc:  # (TooLateToUndo is one: something done since is in the way)
        if str(exc) == ALREADY_UNDONE:
            return Outcome()  # (a second Undo of the same change: done, as a retry is)
        if a.action == "part-undo":
            return _failed(CHANGED)
        return _failed(GONE if str(exc) == tp.GONE else BAD_TARGET)
    except ValueError:  # (a kind or state the store does not know)
        return _failed(BAD_TARGET)


async def _languages(
    memory: MemoryStore, actions: ActionStore | None, guild_id: int, cid: str, a: Action
) -> Outcome:
    """A change to a character's languages from the page (#4271), through the store calls Discord's
    🗣️ Languages screen makes (`add_story_language`, `remove_story_language`,
    `check_story_languages`), so each is one change and one Undo. The door checked the text's shape;
    the store checks the rest again: a character or language gone meanwhile is `gone`, a rule (not
    a character, or one with a sheet: its languages are the sheet's) is `bad_target`. ↩ Undo takes
    back only a change this DM's own done language action made, read again here. Words and ids
    only; the language's name is the DM's own and is never logged."""
    from dmbot.memory import language_rows

    try:
        if a.action == "lang-undo":
            assert actions is not None  # (checked by the caller)
            batch = int(a.text)
            if not await actions.lang_batch_made(guild_id, cid, a.user_id, batch):
                return _failed(GONE)
            await memory.undo(guild_id, cid, batch)
            return Outcome()
        if a.action == "lang-add":
            entity, _, name = a.text.partition(" ")
            added = await memory.add_story_language(guild_id, cid, entity, name, source=DM)
            return _changed(added.batch)
        if a.action == "lang-remove":
            removed = await memory.remove_story_language(guild_id, cid, a.text, source=DM)
            return _changed(removed.batch)
        checked = await memory.check_story_languages(guild_id, cid, a.text, source=DM)
        return _changed(checked.batch)
    except MemoryRuleError as exc:  # (TooLateToUndo is one: something done since is in the way)
        if str(exc) == ALREADY_UNDONE:
            return Outcome()  # (a second Undo of the same change: done, as a retry is)
        if a.action == "lang-undo":
            return _failed(CHANGED)
        return _failed(GONE if str(exc) in (STORE_NOT_FOUND, language_rows.GONE) else BAD_TARGET)


async def _secrets(
    memory: MemoryStore, actions: ActionStore | None, guild_id: int, cid: str, a: Action
) -> Outcome:
    """A 🤫 Known or secret? answer from the page (#4598), through the store call Discord's card
    makes (`answer_secret_question`): `secret-keep` is the answer that changes nothing (Keep it
    known, Keep both, Keep it secret) and `secret-make` the one that changes something (Make it
    secret, End the old one, Let them know), one change with one Undo. The door checked the id's
    shape; the store checks the rest again: a question answered meanwhile (in Discord, or a second
    press) is `gone`. A 🌍 World read's question is answered one upload at a time in Discord, so
    it is `bad_target` here. ↩ Undo takes back only a change this DM's own done answer made, read
    again here. Ids only; nothing is logged but counts."""
    from dmbot.memory import secret_questions as sq

    try:
        if a.action == "secret-undo":
            assert actions is not None  # (checked by the caller)
            batch = int(a.text)
            if not await actions.secret_batch_made(guild_id, cid, a.user_id, batch):
                return _failed(GONE)
            await memory.undo(guild_id, cid, batch)
            return Outcome()
        waiting = await memory.secret_questions_waiting(guild_id, cid)
        asked = next((q for q in waiting if q.id == a.text), None)
        if asked is None:
            return _failed(GONE)
        if asked.kind == sq.REVEAL and asked.change.get("common"):
            return _failed(BAD_TARGET)
        # (a reveal's default is the secret: Keep it secret is `make_secret`, Let them know is
        # the change; for the other two kinds Make it secret / End the old one is the change)
        make_secret = (
            (a.action == "secret-keep") if asked.kind == sq.REVEAL else (a.action == "secret-make")
        )
        done = await memory.answer_secret_question(guild_id, cid, a.text, make_secret, source=DM)
        return _changed(done.batch)
    except MemoryRuleError as exc:  # (TooLateToUndo is one: something done since is in the way)
        if str(exc) == ALREADY_UNDONE:
            return Outcome()  # (a second Undo of the same change: done, as a retry is)
        if a.action == "secret-undo":
            return _failed(CHANGED)
        return _failed(GONE if str(exc) == sq.GONE else BAD_TARGET)


async def _clues(
    memory: MemoryStore, actions: ActionStore | None, guild_id: int, cid: str, a: Action
) -> Outcome:
    """A change to a story thread's clues from the page (#4625), through the store calls Discord's
    🔎 Clues screen makes: `clue-add` ties the campaign's clue of that name or makes a new one
    hidden from the players (`tie_clue`), `clue-word` marks it a false lead or not
    (`set_clue_word`), `clue-off` takes it off the thread (`untie_clue`), each one change with one
    Undo. The door checked the text's shape; the store checks the rest again: a thread or clue gone
    meanwhile, or no longer tied, is `gone`; a rule (not a thread, not a clue, already on the
    thread) is `bad_target`. ↩ Undo takes back only a change this DM's own done clue action made,
    read again here. Words and ids only; a clue's name is the DM's own and is never logged."""
    try:
        if a.action == "clue-undo":
            assert actions is not None  # (checked by the caller)
            batch = int(a.text)
            if not await actions.clue_batch_made(guild_id, cid, a.user_id, batch):
                return _failed(GONE)
            await memory.undo(guild_id, cid, batch)
            return Outcome()
        thread, _, rest = a.text.partition(" ")
        if a.action == "clue-add":
            made = await memory.tie_clue(guild_id, cid, thread, new_name=rest, source=DM)
            return _changed(made.batch)
        clue, _, word = rest.partition(" ")
        if a.action == "clue-word":
            set_ = await memory.set_clue_word(
                guild_id, cid, thread, clue, false_lead=word == "false", source=DM
            )
            return _changed(set_.batch)
        off = await memory.untie_clue(guild_id, cid, thread, clue, source=DM)
        return _changed(off.batch)
    except MemoryRuleError as exc:  # (TooLateToUndo is one: something done since is in the way)
        if str(exc) == ALREADY_UNDONE:
            return Outcome()  # (a second Undo of the same change: done, as a retry is)
        if a.action == "clue-undo":
            return _failed(CHANGED)
        gone = (STORE_NOT_FOUND, clues_mod.NOT_TIED)
        return _failed(GONE if str(exc) in gone else BAD_TARGET)


async def _progress(
    memory: MemoryStore, actions: ActionStore | None, guild_id: int, cid: str, a: Action
) -> Outcome:
    """A "Progress to check" answer from the page (#4616), through the store calls Discord's 🧵
    card makes. `progress-keep` keeps the ticked lines and turns the unticked down in one change
    (one Undo); `progress-none` turns the lines down; `progress-undo` takes back only a keep this
    DM's own done action made (`keep <change>`), or puts turned-down lines back to waiting
    (`none <ids>`, open to any DM of the campaign, as Discord's ↩ is). The lines are read again
    here: every one must still wait and belong to one thread, or the answer is `gone` (answered
    meanwhile) or `bad_target` (lines of two threads, which no card ever sends). A speaker who
    stopped being recorded is checked inside the keep, which then keeps nothing (`changed`).
    Ids only; nothing is logged but counts."""
    from dmbot.memory import thread_progress as tprog

    store = tprog.ThreadProgressStore(memory._db)
    try:
        if a.action == "progress-undo":
            assert actions is not None  # (checked by the caller)
            how, _, ref = a.text.partition(" ")
            if how == "keep":
                if not (ref.isascii() and ref.isdigit()) or len(ref) > 15:
                    return _failed(GONE)
                batch = int(ref)
                if not await actions.progress_batch_made(guild_id, cid, a.user_id, batch):
                    return _failed(GONE)
                await memory.undo(guild_id, cid, batch, after=tprog.unkeep_in(batch))
                return Outcome()
            await store.unturn(guild_id, cid, ref.split(","))
            return Outcome()
        on_text, _, off_text = (
            a.text.partition(" ") if a.action == "progress-keep" else ("", "", "")
        )
        on = [] if on_text in ("", "-") else on_text.split(",")
        off = [] if off_text in ("", "-") else off_text.split(",")
        ids = on + off if a.action == "progress-keep" else a.text.split(",")
        waiting = {x.id: x for x in await store.waiting(guild_id, cid)}
        if len(set(ids)) != len(ids) or any(i not in waiting for i in ids):
            return _failed(GONE)
        if len({waiting[i].thread_id for i in ids}) != 1:
            return _failed(BAD_TARGET)
        if a.action == "progress-none":
            await store.turn_down(guild_id, cid, ids)
            return Outcome()
        moved_batch: int | None = None
        if on:  # (the unticked ones are turned down inside the keep: one change)
            moved = await memory.keep_thread_progress(guild_id, cid, on, source=DM, turn_down=off)
            moved_batch = moved.batch
            if moved_batch is None:
                # (they no longer fit the thread, or a speaker stopped: nothing was kept; the
                # unticked still mean it didn't happen, as on Discord's card)
                await store.turn_down(guild_id, cid, off)
                return _failed(CHANGED)
            return _changed(moved_batch)
        # (nothing ticked: unticked means it didn't happen)
        await store.turn_down(guild_id, cid, off)
        return Outcome()
    except MemoryRuleError as exc:  # (TooLateToUndo is one: something done since is in the way)
        if str(exc) == ALREADY_UNDONE:
            return Outcome()  # (a second Undo of the same keep: done, as a retry is)
        if a.action == "progress-undo":
            return _failed(CHANGED)
        return _failed(GONE if str(exc) == tprog.GONE else BAD_TARGET)


async def _thread(
    memory: MemoryStore, actions: ActionStore | None, guild_id: int, cid: str, a: Action
) -> Outcome:
    """A story thread DMbot found, from the page (#4457), the same store calls as Discord's 📜
    card (`ui/thread_questions`): ✅ keeps it with the people the DM left on (by their place in the
    suggestion's own list, so no name or id is ever typed) and, if the DM renamed it, that name;
    🚫 turns it down for good. The suggestion is read again here, so one answered meanwhile (in
    Discord, or a second press) is `gone`. ↩ Undo takes back only a keep this DM's own done action
    made; putting a turned-down suggestion back to waiting is open to any DM of the campaign, as
    Discord's ↩ is (it only makes the suggestion wait again). Words and counts only."""
    from dmbot.memory.thread_suggestions import GONE as SUGGESTION_GONE
    from dmbot.memory.thread_suggestions import ThreadSuggestionStore, unkeep_in

    store = ThreadSuggestionStore(memory._db)
    try:
        if a.action == "thread-undo":
            assert actions is not None  # (checked by the caller)
            how, _, ref = a.text.partition(" ")
            if how == "keep":
                if not (ref.isascii() and ref.isdigit()) or len(ref) > 15:
                    return _failed(GONE)
                batch = int(ref)
                if not await actions.thread_batch_made(guild_id, cid, a.user_id, batch):
                    return _failed(GONE)
                await memory.undo(guild_id, cid, batch, after=unkeep_in(batch))
                return Outcome()
            return Outcome() if await store.unturn(guild_id, cid, ref) else _failed(GONE)
        suggestion_id, _, rest = a.text.partition(" ")
        if a.action == "thread-not":
            return (
                Outcome() if await store.turn_down(guild_id, cid, suggestion_id) else _failed(GONE)
            )
        shown = next((s for s in await store.waiting(guild_id, cid) if s.id == suggestion_id), None)
        if shown is None:
            return _failed(GONE)
        on_text, _, renamed = rest.partition(" ")
        on = set() if on_text == "-" else {int(i) for i in on_text.split(",")}
        # The lines left on come as `+<n,n,...>` (or `+-`) between the people and the name; a keep
        # without it (an older page) keeps none of the lines (#4501)
        lines_on: set[int] = set()
        first, _, after = renamed.partition(" ")
        if LINES_WORD.fullmatch(first):
            lines_on = set() if first == "+-" else {int(i) for i in first[1:].split(",")}
            renamed = after
        done = await memory.keep_thread(
            guild_id, cid, " ".join(renamed.split()) or shown.name, shown.kind, shown.state,
            aim=shown.aim, parts=[p for i, p in enumerate(shown.parts) if i in on],
            source=DM, suggestion_id=shown.id,
            # (only the lines the page showed and the DM left on are kept; nothing the DM didn't
            # see is kept, §9.6, so a keep that names no lines keeps none)
            leave_off=[i for i in range(len(shown.lines)) if i not in lines_on],
        )  # fmt: skip
    except MemoryRuleError as exc:  # (TooLateToUndo is one: a name was changed since)
        if str(exc) == ALREADY_UNDONE:
            return Outcome()  # (a second Undo of the same keep: done, as a retry is)
        if a.action == "thread-undo":
            return _failed(CHANGED)  # (something done since is in the way)
        # A keep the store refuses for the DM's own name is not "answered meanwhile".
        return _failed(GONE if str(exc) == SUGGESTION_GONE else BAD_TARGET)
    return Outcome(
        result_id=str(done.value.id),
        batches=(done.batch,) if done.batch is not None else (),
    )


async def _maps(
    memory: MemoryStore,
    maps: MapStore | None,
    actions: ActionStore | None,
    guild_id: int,
    dm_user_ids: Collection[int],
    a: Action,
    uploads: MapUploads | None = None,
    reads: Callable[[int], Awaitable[bool]] | None = None,
) -> Outcome:
    """One press on the Maps page (#4347), through the map store's own calls (as Discord's buttons,
    #4373 and #4390), so each is one change and one Undo. A DM may answer a map's use, show or hide
    it and remove it; only a map's poster takes it back; ↩ Undo takes back only a change this
    person's own done Maps action made, read again here. The store rechecks the map, so one gone
    meanwhile answers `gone`. Words and ids only."""
    from dmbot.maps.store import (  # (a late import: no cycle)
        DMS_FILE,
        NOT_YOURS,
        AlreadyAnswered,
        MapError,
        Viewer,
    )

    if maps is None or actions is None:
        return _failed(FAILED)
    cid = a.campaign_id
    is_dm = a.user_id in dm_user_ids
    # The door lets anyone who passes the campaign's people rule queue the player actions; the bot
    # asks the very same rule (`reads`: `channel_sync.reads_campaign`, with "still in the server")
    # and the store checks the map is theirs. Without the rule at hand, nobody but a DM is let in.
    if (
        a.action in PLAYER_MAPS_ACTIONS
        and not is_dm
        and (reads is None or not await reads(a.user_id))
    ):
        return _failed(NOT_ALLOWED)
    if a.action == "maps-add":
        return await map_uploads.add(uploads, guild_id, cid, is_dm=is_dm, a=a)
    if a.action == "maps-undo":
        if not (a.text.isascii() and a.text.isdigit()) or len(a.text) > 15:
            return _failed(GONE)  # ("²" is a digit to Python and int() refuses it)
        batch = int(a.text)
        # A DM may Undo their own presses; anyone else only their own add or take-back
        if not await actions.map_batch_made(guild_id, cid, a.user_id, batch, own_only=not is_dm):
            return _failed(GONE)
        try:
            await memory.undo(guild_id, cid, batch)
        except TooLateToUndo:
            return _failed(TOO_LATE)
        except MemoryRuleError as exc:
            if str(exc) == ALREADY_UNDONE:
                return Outcome()  # (taken back already: a retried Undo)
            # (the map went with its picture since: "gone", never "changed", #4697)
            return _failed(GONE if str(exc) in (STORE_NOT_FOUND, MAP_PICTURE_GONE) else CHANGED)
        return Outcome()
    if a.action not in PLAYER_MAPS_ACTIONS and not is_dm:
        return _failed(NOT_ALLOWED)
    map_id, _, word = a.text.partition(" ")
    made: int | None  # the change the press made, for the page's Undo (none when nothing changed)
    try:
        if a.action == "maps-use":
            made = (
                await maps.answer_use(
                    guild_id, cid, map_id, dm_id=a.user_id, yes=word == "yes", first_only=True
                )
            ).batch
        elif a.action == "maps-show":
            made = (
                await maps.set_audience(guild_id, cid, map_id, "table" if word == "shown" else "dm")
            ).batch
        elif a.action == "maps-remove":
            made = (await maps.remove_map(guild_id, cid, map_id)).batch
        else:
            found = await maps.get(guild_id, cid, map_id, Viewer(dm=True))
            if found is None:
                return _failed(GONE)
            made = (await maps.take_back(guild_id, cid, found.picture_id, a.user_id)).batch
    except AlreadyAnswered:
        return Outcome()  # (the first answer stands: done, with no change to undo)
    except MapError as exc:
        # a map gone meanwhile, or not the sender's to take back
        # (a DM's file's map is never shown to the table: not allowed, not gone, #4259)
        return _failed(NOT_ALLOWED if str(exc) in (NOT_YOURS, DMS_FILE) else GONE)
    return Outcome(batches=(made,) if made is not None else ())


async def _world_undo(
    memory: MemoryStore, actions: ActionStore, guild_id: int, cid: str, a: Action
) -> Outcome:
    """↩ Undo of one World notes ✅ or 🚫 from the page, as `_link_undo` does it: the number is read
    again in this DM's own done rows (the queue keeps them about an hour, so the page's Undo lasts
    that long, then says `gone`). A retried Undo is done; `changed`/`too_late` as there."""
    if not (a.text.isascii() and a.text.isdigit()) or len(a.text) > 15:
        return _failed(GONE)  # ("²" is a digit to Python and int() refuses it)
    batch = int(a.text)
    if not await actions.world_batch_made(guild_id, cid, a.user_id, batch):
        return _failed(GONE)
    try:
        await memory.undo(guild_id, cid, batch)
    except TooLateToUndo:
        return _failed(TOO_LATE)
    except MemoryRuleError as exc:
        if str(exc) == ALREADY_UNDONE:
            return Outcome()  # (taken back already: a retried Undo)
        return _failed(GONE if str(exc) == STORE_NOT_FOUND else CHANGED)
    return Outcome()


async def _link_undo(
    memory: MemoryStore, actions: ActionStore, guild_id: int, cid: str, a: Action
) -> Outcome:
    """Take back one connection edit from the page's ↩ Undo (#4156): the store's own `undo`, as
    Discord's Undo button does. Only a batch this DM's own done edit of this campaign made (read
    again here, as the door checked it before; a made-up number must never reach `undo`, which would
    undo any change). `gone`: already undone or no longer remembered; `changed`: something done
    since is in the way; `too_late`: past the store's limit. The queue keeps a row about an hour
    (the door prunes older ones), so in practice the page's Undo lasts about that long and then says
    `gone`; a retried edit that finds its work already done carries no batch, so offers no Undo."""
    if not (a.target_id.isascii() and a.target_id.isdigit()) or len(a.target_id) > 15:
        return _failed(GONE)  # ("²" is a digit to Python and int() refuses it)
    batch = int(a.target_id)
    together = await actions.link_batches_with(guild_id, cid, a.user_id, batch)
    if not together:
        return _failed(GONE)
    # Every change the one edit made, newest first (#4159: "Who knows this?" can be two), so the
    # connection is as it was before the edit, never half of it. One already taken back (a retried
    # Undo) is skipped; a later refusal stops there and says why.
    undone = 0
    for each in sorted(together, reverse=True):
        try:
            await memory.undo(guild_id, cid, each)
        except TooLateToUndo:
            return _failed(TOO_LATE)
        except MemoryRuleError as exc:
            if str(exc) == ALREADY_UNDONE:
                continue
            if undone == 0 and str(exc) == STORE_NOT_FOUND:
                return _failed(GONE)
            return _failed(CHANGED)
        undone += 1
    return Outcome() if undone else _failed(GONE)


async def _confirm_undo(
    memory: MemoryStore, actions: ActionStore, guild_id: int, cid: str, a: Action
) -> Outcome:
    """Take back one Add all press: each of its changes, like Discord's Undo. A change already
    taken back (this row was taken again after a stop part way) is skipped; refused once any of
    those names changed since (nothing is deleted silently)."""
    press = await actions.pressed(guild_id, cid, a.target_id, a.user_id)
    if press is None:
        return _failed(GONE)
    counts, batches = press
    for batch in reversed(batches):
        try:
            await memory.undo(guild_id, cid, batch)
        except MemoryRuleError as exc:  # (too late, or changed since)
            if str(exc) == ALREADY_UNDONE:
                continue
            return _failed(GONE)  # (the page offers nothing more to take back)
    return Outcome(counts=counts)


@dataclass(frozen=True)
class PlayerLookup:
    """Who a Discord ID belongs to, for the DM to confirm before DMbot links it (#3004). The name is
    only shown, never kept: what is linked is the ID."""

    name: str
    bot: bool
    in_server: bool


# Answers an import clock offer: (campaign, session, set it?, who) -> nothing. The bot's own
# function (it needs the Discord client to move and redraw the clock); the offer row carries the
# outcome.
ClockChoiceFn = Callable[[str, str, bool, int], Awaitable[None]]

# The campaign's game clock now, in minutes (the group furthest ahead while the party is split),
# or None with no clock: what a time limit's date is counted from (#4503). The bot's own function.
GameNowFn = Callable[[str], Awaitable[int | None]]

# Asks Discord about one ID: None when Discord does not know it. A failed lookup raises, so it is
# never read as "unknown" (and nothing is linked without the name having been shown).
PlayerLookupFn = Callable[[int], Awaitable[PlayerLookup | None]]
LOOKUP_KIND = "player_lookup"  # a `kind` action that only looks an ID up and writes nothing


def _player_id(text: str) -> int | None:
    if not (text.isascii() and text.isdigit()) or not 0 < int(text) <= INT64_MAX:
        return None
    return int(text)


async def _ask_discord(look_up: PlayerLookupFn | None, user_id: int) -> PlayerLookup | Outcome:
    """The lookup, or the outcome to give when it cannot be used (unknown, a bot, a failure)."""
    if look_up is None:
        return _failed(LOOKUP_FAILED)  # no way to ask: nothing is guessed
    try:
        found = await look_up(user_id)
    except Exception as exc:  # the type only: never the ID, never a name
        log.warning("A player lookup failed: %s", type(exc).__name__)
        return _failed(LOOKUP_FAILED)
    if found is None:
        return _failed(UNKNOWN_ID)
    if found.bot:
        return _failed(BOT_PLAYER)
    return found


async def _player_lookup(a: Action, look_up: PlayerLookupFn | None) -> Outcome:
    """Say who an ID is, for the page's "Please confirm: <name>". The answer rides in `result_other`
    (a letter for in the server or not, then the name, cut to fit); the row is gone within the hour,
    and nothing about the character changes."""
    user_id = _player_id(a.text)
    if user_id is None:
        return _failed(BAD_PLAYER)
    found = await _ask_discord(look_up, user_id)
    if isinstance(found, Outcome):
        return found
    return Outcome(result_other=("i" if found.in_server else "o") + found.name[:31])


def _answer_counts(confirmed: claims_mod.Confirmed) -> str:
    """What the bot did beside the yes, for the page to say: "shown:1" (the players can see it
    now, #3963 c), "held:1" (kept hidden beside another hidden one, #3979), "scene:1" (said while
    the party was split; the group there knows it) or "split:1" (which group can't be told), #2996.
    Never two at once."""
    if confirmed.shown:
        return "shown:1"
    if confirmed.held:
        return "held:1"
    if confirmed.split:
        return "scene:1" if confirmed.scene else "split:1"
    return ""


async def _player_character(
    memory: MemoryStore,
    guild_id: int,
    cid: str,
    entity_id: str,
    a: Action,
    look_up: PlayerLookupFn | None,
) -> Outcome:
    """Make a name a player character, as Discord's "What it is" does. With a Discord ID the
    player is attached too (Discord's "Who plays it?"). The DM has confirmed the name Discord shows
    for the ID (`flag`: the page sends it only after "That's them"), and the person need not be in
    the server yet. The ID is asked again here, so a bot or an unknown ID is never linked."""
    if not a.text:
        await memory.set_entity_type(guild_id, cid, entity_id, PLAYER_CHARACTER, source=DM)
        await memory.party_on_assignment(guild_id, cid)  # its player "pending": a member (#3995)
        return Outcome(result_id=entity_id)
    user_id = _player_id(a.text)
    if user_id is None or not a.flag:  # nothing links without the confirmation
        return _failed(BAD_PLAYER)
    found = await _ask_discord(look_up, user_id)
    if isinstance(found, Outcome):
        return found
    await memory.confirm_entity(
        guild_id, cid, entity_id, PLAYER_CHARACTER, source=DM, played_by=user_id
    )
    await memory.party_on_assignment(guild_id, cid)  # a lore-only start (#3036)
    return Outcome(result_id=entity_id)


async def _do(
    memory: MemoryStore,
    guild_id: int,
    cid: str,
    entity_id: str,
    name: str,
    a: Action,
    look_up: PlayerLookupFn | None = None,
) -> Outcome:
    if a.action == "spell":
        new = " ".join(a.text.split())
        if PRONOUNCED.search(new):  # how to say it is not how to spell it (#2332)
            return _failed(BAD_NAME)
        if name_key(new) != name_key(name):
            clash = await memory.confirmed_name_for(guild_id, cid, name_key(new))
            if clash is not None and name_key(clash) != name_key(name):
                return _failed(EXISTS)
        try:
            await memory.rename_entity(guild_id, cid, entity_id, new, source=DM)
        except MemoryRuleError:
            return _failed(BAD_NAME)
        return Outcome(result_id=entity_id)
    if a.action == "kind":
        if a.kind == LOOKUP_KIND:
            return await _player_lookup(a, look_up)
        if a.kind == PLAYER_CHARACTER:
            return await _player_character(memory, guild_id, cid, entity_id, a, look_up)
        try:
            await memory.set_entity_type(guild_id, cid, entity_id, a.kind, source=DM)
        except MemoryRuleError:
            return _failed(BAD_KIND)
        return Outcome(result_id=entity_id)
    if a.action == "add-other":
        text = " ".join(a.text.split())
        clash = await memory.confirmed_name_for(guild_id, cid, name_key(text))
        if clash is not None and name_key(clash) != name_key(name):
            return _failed(EXISTS)
        try:
            if await _other(memory, guild_id, cid, entity_id, name, text) is not None:
                return _failed(EXISTS)  # it already has that name (a secret one, say)
            await memory.add_alias(
                guild_id, cid, entity_id, text, kind=ALIAS_KIND, source=DM, status=CONFIRMED
            )
        except MemoryRuleError:
            return _failed(BAD_NAME)
        return Outcome(result_id=entity_id)
    if a.action == "remove-other":
        alias_id = await _other(memory, guild_id, cid, entity_id, name, a.text)
        if alias_id is None:
            return _failed(NOT_FOUND)
        await memory.update_alias(guild_id, cid, alias_id, source=DM, status=REJECTED)
        return Outcome(result_id=entity_id)
    if a.action in SPELLING_ACTIONS:
        return await _spelling(memory, guild_id, cid, entity_id, a)
    if a.action == "players-line":  # the line the players are told (#3568); a blank clears it
        await memory.set_players_text(guild_id, cid, entity_id, a.text, source=DM)
        return Outcome(result_id=entity_id)
    if a.action == "place-setting":  # the campaign's setting (#4173 slice 2): the flag says which
        try:  # ("remove" clears only this place's mark, never another place's: `only_if`)
            await memory.set_setting(
                guild_id, cid, entity_id if a.flag else None, source=DM, only_if=entity_id
            )
        except MemoryRuleError as exc:  # gone since it was queued, or not a place big enough
            return _failed(NOT_FOUND if str(exc) == STORE_NOT_FOUND else BAD_TARGET)
        return Outcome(result_id=entity_id)
    if a.action == "place-size":  # how big a place is (#4173); a blank is "Not sure"
        try:
            await memory.set_place_level(guild_id, cid, entity_id, a.text or None, source=DM)
        except MemoryRuleError as exc:  # gone since it was queued, or not a place
            return _failed(NOT_FOUND if str(exc) == STORE_NOT_FOUND else BAD_TARGET)
        return Outcome(result_id=entity_id)
    if a.action == "place-form":  # land, water or a road (#4173 slice 6a); a blank is land
        try:
            await memory.set_place_form(guild_id, cid, entity_id, a.text or None, source=DM)
        except MemoryRuleError as exc:  # gone since it was queued, or not a place
            return _failed(NOT_FOUND if str(exc) == STORE_NOT_FOUND else BAD_TARGET)
        return Outcome(result_id=entity_id)
    if a.action in MAP_ACTIONS:  # a place's land, climate and ruler (#4287); blank is "Not sure"
        return await _map_fact(memory, guild_id, cid, entity_id, a)
    if a.action in TOLD_ACTIONS:
        return await _told(memory, guild_id, cid, entity_id, a)
    if a.action == ALIAS_WHO:
        return await _alias_who(memory, guild_id, cid, entity_id, a)
    if a.action == "secret":  # one of its other names
        alias_id = await _other(memory, guild_id, cid, entity_id, name, a.text)
        if alias_id is None:
            return _failed(NOT_FOUND)
        try:
            await memory.update_alias(
                guild_id, cid, alias_id, source=DM, secret=a.flag, dm_chose_secret=True
            )
        except MemoryRuleError:
            return _failed(BAD_TARGET)
        return Outcome(result_id=entity_id)
    if a.action == "hide":  # "Players don't know this one yet", the whole entry
        try:
            await memory.set_hidden_from_players(
                guild_id, cid, entity_id, a.flag, source=DM, dm_chose_secret=True
            )
        except MemoryRuleError:
            return _failed(BAD_TARGET)
        return Outcome(result_id=entity_id)
    if a.action == "merge":
        keep = await memory.entity(guild_id, cid, a.target_id)
        if keep is None or keep.status != CONFIRMED or keep.type == PERSON or keep.id == entity_id:
            return _failed(BAD_TARGET)
        try:
            await memory.merge(guild_id, cid, keep.id, entity_id, source=DM, dm_said_same=True)
        except MemoryRuleError:
            return _failed(BAD_TARGET)
        return Outcome(result_id=keep.id)
    if a.action == "merge-many":  # the kept name is the row's entry; `text` holds the others
        ids = list(dict.fromkeys(a.text.split()))
        if not ids or entity_id in ids or len(ids) > MERGE_MANY_MAX:
            return _failed(BAD_TARGET)
        waiting: list[str] = []
        for other_id in ids:
            other = await memory.entity(guild_id, cid, other_id)
            if other is not None and other.merged_into == entity_id:
                continue  # a retried job: this one went in the first time
            if other is None or other.status != CONFIRMED or other.type == PERSON:
                return _failed(NOT_FOUND)
            waiting.append(other_id)
        if waiting:
            try:
                await memory.merge_many(guild_id, cid, entity_id, waiting, source=DM)
            except MemoryRuleError:
                return _failed(BAD_TARGET)
        return Outcome(result_id=entity_id)
    if a.action == "lookalike-merge":  # the kept name is the row's entry
        return await _look_alike_merge(memory, guild_id, cid, entity_id, a)
    if a.action == "remove":
        if not a.flag:
            return _failed(BAD_TARGET)  # (the door asks for the confirmation; so does this)
        await memory.set_entity_status(guild_id, cid, entity_id, REJECTED, source=DM)
        return Outcome()
    if a.action.startswith("link-"):
        return await _link(memory, guild_id, cid, entity_id, a)
    return _failed(FAILED)


async def _link(memory: MemoryStore, guild_id: int, cid: str, entity_id: str, a: Action) -> Outcome:
    """The relationship web's edits (#2323), through the calls the name card makes. The outcome
    names the pair, so the page can draw the edge as it now stands."""
    if a.action == "link-add":
        other = await memory.entity(guild_id, cid, a.target_id)
        if (
            other is None
            or other.status != CONFIRMED
            or other.type == PERSON
            or other.id == entity_id
        ):
            return _failed(BAD_TARGET)
        predicate, detail, swapped = predicate_for_either(a.text)
        # "has a niece" typed on Aldric's card is stored as "Mira kin_of Aldric, niece" (#2457)
        first, second = (other.id, entity_id) if swapped else (entity_id, other.id)
        try:
            added = await memory.add_relation(
                guild_id, cid, first, predicate, second, source=DM, confidence=1.0,
                status=CONFIRMED, detail=detail[:100], secret=a.flag,
                directional=predicate == "kin_of" and bool(detail), by_hand=True,
            )  # fmt: skip
        except MemoryRuleError:
            return _failed(BAD_TARGET)
        # the change it made, for the page's Undo (#4156); none when it was already known
        return Outcome(result_id=entity_id, result_other=other.id, batches=_batch_of(added.batch))
    # the others name one connection, filed under its first name
    link = await memory.relation(guild_id, cid, a.target_id)
    if link is None or entity_id not in (link.subject_id, link.object_id):
        return _failed(NOT_FOUND)
    pair = Outcome(result_id=link.subject_id, result_other=link.object_id)
    if link.status != CONFIRMED:
        # A retried job (the first try did its work, then stopped before it was marked done) finds
        # the connection already ended: that is "done", as for a remove or merge in `apply`.
        if link.status == REJECTED and await _already(memory, guild_id, cid, link, a):
            return pair
        return _failed(NOT_FOUND)
    batch: int | None = None  # the change the edit made, for the page's Undo (#4156)
    try:
        if a.action == "link-secret":
            batch = (await link_edits.set_secret(memory, guild_id, cid, link, a.flag)).batch
        elif a.action == "link-pin":
            # 📌 100% truth (#2447): the card's own call. A retried job finds it already so.
            if (link.id in await memory.pinned_connections(guild_id, cid)) != a.flag:
                batch = (await memory.pin(guild_id, cid, link.id, a.flag, source=DM)).batch
        elif a.action == "link-remove":
            if not a.flag:
                return _failed(BAD_TARGET)
            batch = (await link_edits.remove(memory, guild_id, cid, link)).batch
        elif a.action == "link-words":
            picked = a.text.startswith(FEELING_TEXT)
            if picked and link.predicate != "knows":
                return _failed(BAD_TARGET)  # the picker is for a knows line only (#3606)
            predicate, detail, swapped = predicate_for_either(_words_of(a.text))
            other_end = link.object_id if entity_id == link.subject_id else link.subject_id
            changed = await link_edits.change_words(
                memory,
                guild_id,
                cid,
                link,
                predicate,
                detail,
                first=other_end if swapped else entity_id,
                dm_feeling=picked,  # (the DM's Reputation follows the picked feeling, #3642)
            )  # (a family role is said from the name it belongs to, #2457)
            batch = None if changed is None else changed.batch  # (None: the same words already)
        elif a.action == DIRECTION_ACTION:
            # the page names the two ends as its edge does, by id order (`relationships.edges_from`)
            if a.text not in DIRECTIONS:
                return _failed(BAD_TARGET)
            low, high = sorted((link.subject_id, link.object_id))
            reads_from = None if a.text == "both" else (low if a.text == "a-b" else high)
            # (a retried job finds it already so: the store writes nothing, and there is no batch;
            # a turn is written anew in one batch, so the page's Undo puts the old row back, #4156)
            turned = await memory.set_direction(guild_id, cid, link.id, reads_from, source=DM)
            batch = turned.batch
        elif a.action == RIGHT_ACTION:
            # the DM's ✅ That's right: the call Discord's card makes. A retried job finds the mark
            # already gone, which writes nothing and has no batch.
            batch = (await memory.confirm_assumed(guild_id, cid, link.id, source=DM)).batch
        elif a.action == KNOWS_HOW_ACTION:
            # whether two people have met (#3881, #3882): the DM's word, either way, through the
            # call the Discord edit page's menu makes. `text` is "met", "aware" or "" (clears it).
            # A retried job finds it already so, which changes nothing.
            # (the store also refuses a knows to a place or a thing: only two people meet)
            if link.predicate != "knows" or a.text not in KNOWS_HOWS:
                return _failed(BAD_TARGET)
            if link.knows_how != a.text:  # a retried job finds it already so: nothing to write
                batch = (
                    await memory.set_knows_how(guild_id, cid, link.id, a.text, source=DM)
                ).batch
        elif a.action == LINK_WHO:
            # "Who knows this?" (#2999, never applied until #4159): Discord's call. The website's
            # pick is the whole list (any confirmed character, as its door checks), not Discord's
            # player-characters-only menu, so it replaces every knower.
            # (the same answer again writes nothing: the store only changes what differs, so a
            # retried job is done with no change.) One answer can be two changes (hide it, then
            # who knows it): the page's Undo takes back every one (`link_batches_with`).
            # A job stopped between the two and retried carries only the second.
            who, characters = _who_of(a.text)
            made: list[int] = []
            await link_edits.set_who_knows(
                memory, guild_id, cid, link, who, characters, made=made, listed=_shown_of(a.text)
            )
            return replace(pair, batches=tuple(made))
        else:
            return _failed(FAILED)
    except MemoryRuleError as exc:
        # Showing a connection is refused until its names are shown: say which case it is, so the
        # page can tell the DM what to do first (#3426). The rule itself is unchanged.
        reason = str(exc)
        if _is_cannot_show(exc):
            return _failed(CANNOT_SHOW)
        if reason == NAMES_NOT_SHOWN_WORDS:
            return _failed(NAMES_NOT_SHOWN)
        if reason == unnamed_mod.NAME_BEFORE_SHOWING:
            return _failed(NAME_UNNAMED)
        return _failed(BAD_TARGET)
    return replace(pair, batches=_batch_of(batch))


def _batch_of(batch: int | None) -> tuple[int, ...]:
    """The change batches an outcome carries: the one a write made, or none (nothing changed)."""
    return () if batch is None else (batch,)


async def _answer_which_one(guild_id: int, cid: str, a: Action, w: Waiting) -> Outcome:
    """ "Which one?" for a kept claim (#3463): the very calls the Discord card makes. The pick's
    `text` is `subject:<entry id>` or `object:<entry id>`; `ClaimStore.pick_one` checks the entry is
    one of the choices and that the claim still waits. A retried job finds the pick already made:
    done. Neither is `claim-reject`. Undo is `ClaimStore.unpick`, which holds only while the claim
    still waits with a pick to take back; otherwise `gone`."""
    if a.action == "claim-unpick":
        return Outcome() if await w.claims.unpick(guild_id, cid, a.target_id) else _failed(GONE)
    side, _, entry_id = a.text.partition(":")
    # Only a side still waiting for its pick, and only one of its choices: a second tab sending a
    # different entry for a side already picked must not overwrite the first pick.
    choices = await w.claims.which_one(guild_id, cid, a.target_id)
    if any(c.side == side and entry_id in {o.id for o in c.entries} for c in choices):
        try:
            await w.claims.pick_one(guild_id, cid, a.target_id, side, entry_id)
        except MemoryRuleError:
            return _failed(GONE)
        return Outcome()
    claim = await w.claims.get(guild_id, cid, a.target_id)
    made = (
        claim is not None
        and side in ("subject", "object")
        and bool(entry_id)
        and getattr(claim, f"{side}_picked") == entry_id
    )
    return Outcome() if made else _failed(GONE)


async def _claim_undo(
    memory: MemoryStore, actions: ActionStore, guild_id: int, cid: str, a: Action, w: Waiting
) -> Outcome:
    """Take back one fact of an "All true" / "All secret" press and ask its question again (#3645):
    the change log's Undo and `ClaimStore.reopen_in`, the calls of Discord's "Undo all". Only the
    change this DM's own done `claim-confirm` of that claim made can be undone here. A claim already
    waiting again, or a batch already undone, is done (a retried job); a later change to what the
    fact made is refused with `gone`, the page's "something changed since"."""
    claim = await w.claims.get(guild_id, cid, a.target_id)
    if claim is None:
        return _failed(GONE)
    # Before anything else, so a retried job still succeeds when the answer row it came from has
    # been pruned (rows go after an hour). A reopened claim is hidden from a speaker who stopped
    # being recorded by the waiting list itself (it filters on who is recorded now).
    if claim.status == CLAIM_PROPOSED:
        return Outcome()  # asked again already
    if not (a.text.isascii() and a.text.isdecimal()) or len(a.text) > 12:
        return _failed(GONE)
    batch = int(a.text)
    if not await actions.claim_batch_made(guild_id, cid, a.user_id, a.target_id, batch):
        return _failed(GONE)
    claim_id = a.target_id
    try:
        await memory.undo(
            guild_id,
            cid,
            batch,
            source=DM,
            after=lambda changes: ClaimStore.reopen_in(changes, claim_id),
        )
    except MemoryRuleError as exc:  # (TooLateToUndo is one)
        if str(exc) == ALREADY_UNDONE:
            # undone before, so a retry is done: unless the claim was kept again since (from
            # Discord), when that old batch is not the fact's now
            again = await w.claims.get(guild_id, cid, claim_id)
            return (
                Outcome() if again is not None and again.status == CLAIM_PROPOSED else _failed(GONE)
            )
        return _failed(GONE)
    return Outcome()


async def _answer_waiting(
    memory: MemoryStore, guild_id: int, cid: str, a: Action, w: Waiting
) -> Outcome:
    """The DM's answer to a waiting connection from the website (#2635, #2642): the very calls
    the DM screen's buttons make, so the fact is the DM's own word, consent and the plan are
    checked again, and Undo is the same. A retried job finds the answer already given: done."""
    if a.action in ("claim-pick", "claim-unpick"):
        return await _answer_which_one(guild_id, cid, a, w)
    if a.action in ("claim-confirm", "claim-some", "claim-reject"):
        claim = await w.claims.get(guild_id, cid, a.target_id)
        if claim is None or claim.reason == WAITING_NAME_REASON:
            return _failed(NOT_FOUND)  # (a held claim is in no list the DM sees, #3749)
        if claim.status != CLAIM_PROPOSED:
            if a.action == "claim-some":
                # Already kept for everyone (Discord, another tab): a stale 👥 must not read as
                # "held back and known to exactly these characters".
                return _failed(GONE)
            done = CLAIM_REJECTED if a.action == "claim-reject" else CLAIM_CONFIRMED
            return (
                Outcome(result_id=claim.fact_id or "")
                if claim.status == done
                else _failed(NOT_FOUND)
            )
        if a.action == "claim-reject":
            await w.claims.reject(guild_id, cid, a.target_id)
            return Outcome()
        # 👥 Some know (#4680): the same call as Discord's picker, `confirm(knowers=…)`: the
        # connection is held back and exactly these characters know it, in the claim's own change
        some = a.text.split(",") if a.action == "claim-some" else None
        try:
            confirmed = await w.claims.confirm(
                guild_id,
                cid,
                a.target_id,
                opted_in=await w.opted_in(),
                secret=a.flag,
                knowers=some,
            )
        except MemoryRuleError as exc:
            # the page words these: the question is gone, a new name has to be answered first,
            # someone who said it stopped being recorded, or the rules refuse the fact now
            return _failed(CLAIM_REFUSALS.get(str(exc), CANNOT_WRITE))
        # The change batch rides on the answer, for the page's one Undo of a press (#3645).
        return Outcome(
            result_id=confirmed.claim.fact_id or "",
            result_other="" if confirmed.batch is None else str(confirmed.batch),
            # a held-back connection the table stated is shown now, for the page to say (#3963 c)
            # and one kept hidden beside a hidden one, for the page to point at 👁️ Reveal (#3979)
            # and one said while the party was split: "scene" (the group there knows it) or
            # "split" (which group can't be told), #2996
            counts=_answer_counts(confirmed),
        )
    if a.action in READING_ACTIONS:
        return await _answer_reading(memory, guild_id, cid, a, w)
    if a.action in NAMES_FIXED_ACTIONS:
        # The recap page's Undo of a name-pass fix: done with how many lines went back (0 when the
        # fix was undone already, which meets the wish); `gone` when nothing could be put back
        # and the fix is not recorded as undone (nothing was changed).
        if w.name_pass_undo is None:
            return _failed(FAILED)
        if not (a.target_id.isascii() and a.target_id.isdecimal()) or len(a.target_id) > 12:
            return _failed(NOT_FOUND)
        lines = await w.name_pass_undo(cid, int(a.target_id))
        return _failed(GONE) if lines is None else Outcome(result_other=str(lines))
    if a.action in ("backstory-which-pick", "backstory-which-neither"):
        # "Which Mara?" (#2652): the call the Discord card makes. `text` is the entry picked (one
        # of the question's candidates, which `pick_which` checks again); Neither drops it.
        if a.action == "backstory-which-neither":
            # (the same call as Discord's: a pronoun's question keeps its connection, #2790)
            question = next(
                (q for q in await w.backstory.which_waiting(guild_id, cid) if q.id == a.target_id),
                None,
            )
            if question is None:
                return _failed(GONE)
            result = await none_of_these(memory, w.backstory, guild_id, cid, question)
            if result == COULD_NOT_KEEP:
                return _failed(CANNOT_WRITE)
        else:
            result = await w.backstory.pick_which(guild_id, cid, a.target_id, a.text)
        if result == FULL:
            return _failed(QUEUE_FULL)
        return Outcome() if result == DONE else _failed(GONE)
    onto = await memory.ontology(guild_id, cid)
    symmetric = {k for k, p in onto.predicates.items() if p.symmetric}
    # both ends are read again here: only a connection whose two names are confirmed is kept
    waiting = await w.backstory.waiting(guild_id, cid, symmetric=symmetric)
    ready = [x for x in waiting if x.ready]
    if a.action == "backstory-keep-all":
        shown = set(a.text.split())  # the ids the page showed: no other is kept
        todo = [x for x in ready if x.id in shown]
        if not todo:
            return _failed(NOT_FOUND)
        done_all = await keep.keep_all(
            memory, w.backstory, guild_id, cid, todo, progress=_progress_for(w, a)
        )
        return Outcome(result_id=str(len(done_all.kept))) if done_all.kept else _failed(BAD_TARGET)
    if a.action == "backstory-skip":
        # Skipping writes no fact, so a link may be dismissed while it still waits on a name that
        # is not confirmed yet; only keeping needs both ends confirmed.
        skipped = next((x for x in waiting if x.id == a.target_id), None)
        if skipped is None:
            return _failed(NOT_FOUND)
        await w.backstory.remove(guild_id, cid, skipped)
        return Outcome()
    link = next((x for x in ready if x.id == a.target_id), None)
    if link is None:
        return _failed(NOT_FOUND)
    # (a hand-over is kept here too, #4122: the website's list says what keeping it ends, as the
    # Discord card does, and the same keep ends the previous holder's part; a hidden keep never
    # ends what the players see. Keep all still leaves it out: only its own press keeps it.)
    ticked: list[str] = []
    if a.action == "backstory-keep-some":
        # 👥 Some know (#4680 part 2): the call Discord's picker makes. Never for a secret name or a
        # hand-over (the card refuses both: nothing is written), and every ticked id must be a
        # player character of this campaign now: a stale pick writes nothing rather than leave
        # the connection known to nobody.
        ticked = list(dict.fromkeys(a.text.split(",")))
        if link.secret or link.hand_over or not ticked or len(ticked) > 25:
            return _failed(BAD_TARGET)
        for character in ticked:
            found = await memory.entity(guild_id, cid, character)
            if (
                found is None
                or found.type != "character"
                or found.role != "player_character"
                or found.status != "confirmed"
                or found.merged_into is not None
            ):
                return _failed(BAD_TARGET)
    one = replace(link, secret=link.secret or a.flag)  # "keep as secret" only ever hides more
    # (the DM's own "Keep as secret" press hides an existing public twin; a spelling does not)
    kept = await keep.keep_all(
        memory, w.backstory, guild_id, cid, [one], keep_flags=not a.flag, one_press=True,
        players="show" if a.action == "backstory-keep-show" else "",
        knowers=ticked,
    )  # fmt: skip
    return Outcome(result_id="1") if kept.kept else _failed(BAD_TARGET)


def _progress_for(w: Waiting, a: Action) -> Callable[[int, int], Awaitable[None]] | None:
    """The count for this Keep all, or None when the host keeps none. A failed write is logged
    (the count only) and skipped: the connections are kept all the same."""
    report = w.progress
    if report is None:
        return None

    async def tell(done: int, total: int) -> None:
        try:
            await report(a, done, total)
        except Exception as exc:
            log.info("A Keep all count could not be written: %s", type(exc).__name__)

    return tell


def reading_error(why: str) -> str:
    """The word the page gets for a reading that could not be kept: `placing`'s and the claims'
    own refusals have words; any other the rules give is "cannot write"."""
    return CLAIM_REFUSALS.get(why) or READING_REFUSALS.get(why) or CANNOT_WRITE


async def _answer_reading(
    memory: MemoryStore, guild_id: int, cid: str, a: Action, w: Waiting
) -> Outcome:
    """What people believe, from the website (#2412 slice 3b): the calls the DM screen would make.
    `reading-place` keeps the reading as a belief (never a fact; `ClaimStore.place`, which asks who
    is recorded again inside its write), `reading-no` turns it down, `reading-undo` puts a keep
    back through the store's Undo (a retry finds it already undone, which is done too). A retried
    `reading-place` finds the claim kept already and answers done with the batch of the keep it
    made (so the page still has its Undo); a keep made from Discord has none."""
    if a.action == "reading-undo":
        if w.kept_reading is None or w.spent is None:
            return _failed(FAILED)
        kept = await w.kept_reading(cid, a.user_id, a.target_id)
        if kept is None:
            return _failed(GONE)
        claim_id, batch = kept

        async def reopen(c: Changes) -> None:
            """The question comes back, unless someone whose words it holds has stopped being
            recorded since: then it goes, as `place` would have deleted it. Who is recorded is read
            here, inside the undo, so a stop between the press and the write is seen."""
            row = await c.get(CLAIMS, claim_id)
            if row is None:
                return
            recorded = set(await w.opted_in())
            if not row["speaker_ids"] or not set(row["speaker_ids"]) <= recorded:
                await c.delete(CLAIMS, claim_id)
                await evidence.placeholder_claims(c.conn, guild_id, [claim_id])
                return
            await ClaimStore.reopen_in(c, claim_id)

        try:
            await memory.undo(guild_id, cid, batch, after=reopen)
        except MemoryRuleError as exc:
            if str(exc) != ALREADY_UNDONE:
                return _failed(GONE)  # something changed the same connection since: not undone
            # it was undone already (a retry after a crash, or from Discord): the wish is met
        await w.spent(cid, a.target_id)
        return Outcome(result_id=claim_id)
    claim = await w.claims.get(guild_id, cid, a.target_id)
    if claim is None or claim.reading is None:
        return _failed(NOT_FOUND)
    if claim.status != CLAIM_PROPOSED:
        done = CLAIM_CONFIRMED if a.action == "reading-place" else CLAIM_REJECTED
        if claim.status != done:
            return _failed(GONE)
        # A retried Place finds the keep made: it hands back that keep's batch so the page still
        # has its Undo (none when it was undone or kept from Discord).
        earlier = await w.claims.placed_batch(guild_id, cid, a.target_id)
        return Outcome(
            result_id=claim.fact_id or "",
            result_other=str(earlier) if earlier and a.action == "reading-place" else "",
        )
    if a.action == "reading-no":
        await w.claims.reject(guild_id, cid, a.target_id)
        return Outcome()
    try:
        placed = await w.claims.place(guild_id, cid, a.target_id, opted_in=w.opted_in)
    except MemoryRuleError as exc:
        return _failed(reading_error(str(exc)))
    return Outcome(
        result_id=placed.relation_id, result_other=str(placed.batch) if placed.batch else ""
    )


def _words_of(text: str) -> str:
    """The connection words a queued `link-words` text means: a picked feeling is "knows (word)"."""
    return f"knows ({text[len(FEELING_TEXT) :]})" if text.startswith(FEELING_TEXT) else text


async def _already(memory: MemoryStore, guild_id: int, cid: str, link: Relation, a: Action) -> bool:
    """Whether an ended connection is the work of this very action: a removal (it is ended), or
    new words (a live connection with those words stands between the same two names)."""
    if a.action == "link-remove":
        return a.flag
    if a.action != "link-words":
        return False
    predicate, detail, _ = predicate_for_either(_words_of(a.text))
    return any(
        r.status == CONFIRMED
        and r.predicate == predicate
        and r.detail.casefold() == detail.casefold()
        and {r.subject_id, r.object_id} == {link.subject_id, link.object_id}
        for r in await memory.relations(
            guild_id, cid, entity_id=link.subject_id, include_secret=True
        )
    )
