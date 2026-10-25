"""The story graph's health numbers for the server log (docs/STORY_MEMORY.md, "Throughout"; #1699):
is a campaign's memory staying tidy? One log line per campaign after each session, counts only.

Never shown to a player or a DM, and never a name or any text: only numbers, under the campaign
the logs already carry (`log_context`). Every count is made by the database in one read scoped to
one campaign (a handful of indexed subselects), so it costs milliseconds and no row is pulled into
Python. A failed read costs the line, never the rest of the after-session work (the caller).

What each number means:
- entities: live entities (confirmed + proposed); confirmed and proposed on their own;
- duplicates: pairs of confirmed entities of one kind (not people) that share a name or an alias
  key and are not joined by a live `same_as` link;
- orphans: confirmed entities (not people) with no live relation that were not heard in the last
  three sessions;
- unused_terms: the campaign's own relationship terms (never the core ones) that no live relation
  uses;
- flags_this_session: continuity and rule-check flags raised since the session began, of any
  status (one raised and resolved in the session still counts);
- asked_dm: proposed claims waiting for the DM from this session, plus the "Did they mean…?" name
  questions the session closed (answered, expired or ended; the caller adds those, which live in
  memory while the session runs). Speaker questions are not counted.
- evidence, evidence_placeholders, evidence_dangling, evidence_foreign (#2662): how many evidence
  links the campaign has, how many lost their line or claim, how many point at nothing and how
  many at another campaign's thing (the last two should stay 0).
"""

from __future__ import annotations

from dataclasses import dataclass

from dmbot.memory.store import MemoryStore

RECENT_SESSIONS = 3  # "heard lately" for an orphan

_LIVE = "r.status <> 'rejected' AND r.to_session_at IS NULL"

COUNTS = f"""
SELECT
  (SELECT count(*) FROM memory_entities WHERE guild_id = %(g)s AND campaign_id = %(c)s
     AND status = 'confirmed') AS confirmed,
  (SELECT count(*) FROM memory_entities WHERE guild_id = %(g)s AND campaign_id = %(c)s
     AND status = 'proposed') AS proposed,
  (SELECT count(*) FROM (
     SELECT DISTINCT a.entity_id AS x, b.entity_id AS y
       FROM memory_aliases a
       JOIN memory_aliases b ON b.guild_id = a.guild_id AND b.campaign_id = a.campaign_id
         AND b.key = a.key AND a.entity_id < b.entity_id
       JOIN memory_entities ea ON ea.guild_id = a.guild_id AND ea.campaign_id = a.campaign_id
         AND ea.id = a.entity_id AND ea.status = 'confirmed' AND ea.type <> 'person'
       JOIN memory_entities eb ON eb.guild_id = b.guild_id AND eb.campaign_id = b.campaign_id
         AND eb.id = b.entity_id AND eb.status = 'confirmed' AND eb.type = ea.type
      WHERE a.guild_id = %(g)s AND a.campaign_id = %(c)s
        AND a.status = 'confirmed' AND b.status = 'confirmed'
        AND NOT EXISTS (
          SELECT 1 FROM memory_relations r
           WHERE r.guild_id = %(g)s AND r.campaign_id = %(c)s AND r.predicate = 'same_as'
             AND {_LIVE}
             AND ((r.subject_id = a.entity_id AND r.object_id = b.entity_id)
               OR (r.subject_id = b.entity_id AND r.object_id = a.entity_id)))
   ) pairs) AS duplicates,
  (SELECT count(*) FROM memory_entities e
    WHERE e.guild_id = %(g)s AND e.campaign_id = %(c)s
      AND e.status = 'confirmed' AND e.type <> 'person'
      AND NOT EXISTS (
        SELECT 1 FROM memory_relations r
         WHERE r.guild_id = e.guild_id AND r.campaign_id = e.campaign_id AND {_LIVE}
           AND (r.subject_id = e.id OR r.object_id = e.id))
      AND NOT EXISTS (
        SELECT 1 FROM memory_heard h
         WHERE h.guild_id = e.guild_id AND h.campaign_id = e.campaign_id AND h.entity_id = e.id
           AND h.session_started_at >= (
             SELECT min(s) FROM (
               SELECT DISTINCT session_started_at AS s FROM memory_heard
                WHERE guild_id = %(g)s AND campaign_id = %(c)s
                ORDER BY s DESC LIMIT %(recent)s) lately))
  ) AS orphans,
  (SELECT count(*) FROM memory_predicates p
    WHERE p.guild_id = %(g)s AND p.campaign_id = %(c)s AND p.status = 'active'
      AND NOT EXISTS (
        SELECT 1 FROM memory_relations r
         WHERE r.guild_id = p.guild_id AND r.campaign_id = p.campaign_id
           AND r.predicate = p.key AND {_LIVE})
  ) AS unused_terms,
  (SELECT count(*) FROM memory_flags WHERE guild_id = %(g)s AND campaign_id = %(c)s
     AND created_at >= %(since)s) AS flags,
  (SELECT count(*) FROM memory_claims WHERE guild_id = %(g)s AND campaign_id = %(c)s
     AND status = 'proposed' AND session_started_at >= %(since)s) AS claims
"""


@dataclass(frozen=True, slots=True)
class Health:
    entities: int
    confirmed: int
    proposed: int
    duplicates: int
    orphans: int
    unused_terms: int
    flags_this_session: int
    asked_dm: int
    # the evidence links (#2662): all of them, the placeholders, and the ones that point at
    # nothing or at another campaign's thing (both should stay 0)
    evidence: int = 0
    evidence_placeholders: int = 0
    evidence_dangling: int = 0
    evidence_foreign: int = 0

    def line(self) -> str:
        """Counts only: no name, no text."""
        return (
            f"memory health: entities={self.entities} confirmed={self.confirmed} "
            f"proposed={self.proposed} duplicates={self.duplicates} orphans={self.orphans} "
            f"unused_terms={self.unused_terms} flags_this_session={self.flags_this_session} "
            f"asked_dm={self.asked_dm} evidence={self.evidence}"
            f" evidence_placeholders={self.evidence_placeholders}"
            f" evidence_dangling={self.evidence_dangling} evidence_foreign={self.evidence_foreign}"
        )


async def read(
    memory: MemoryStore,
    guild_id: int,
    campaign_id: str,
    *,
    since: int,
    names_asked: int = 0,
) -> Health:
    """The numbers for one campaign. `since`: when the session just ended began (seconds);
    `names_asked`: the "Did they mean…?" questions put to the DM in it."""
    # The store runs `COUNTS` (the SQL stays here, with the definitions) in its own
    # campaign-scoped read.
    row = await memory.health_counts(guild_id, campaign_id, since=since, recent=RECENT_SESSIONS)
    confirmed, proposed = int(row["confirmed"]), int(row["proposed"])
    links = await memory.evidence_audit(guild_id, campaign_id)
    return Health(
        entities=confirmed + proposed,
        confirmed=confirmed,
        proposed=proposed,
        duplicates=int(row["duplicates"]),
        orphans=int(row["orphans"]),
        unused_terms=int(row["unused_terms"]),
        flags_this_session=int(row["flags"]),
        asked_dm=int(row["claims"]) + max(0, names_asked),
        evidence=links.links,
        evidence_placeholders=links.placeholders,
        evidence_dangling=links.dangling,
        evidence_foreign=links.foreign,
    )
