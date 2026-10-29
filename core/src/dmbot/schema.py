"""The database schema, as one ordered list of migrations.

Rules:
- Never edit a migration that has shipped; add a new one.
- Every table holding a Discord server's data has a `guild_id BIGINT NOT NULL` column and
  the `guild_isolation` row-level-security policy (see `_isolate`). Child tables reference
  their parent with `(id, guild_id)`, so a row can't point at another server's data.
- Discord IDs are BIGINT. Timestamps are Unix seconds (BIGINT), matching the app.
- Tables holding a *person's* data (the website's accounts, #435) have a `user_id` and
  the `user_isolation` policy (see `_isolate_user`) instead; list them in
  USER_ISOLATED_TABLES. `installs` is listed there too, though it is keyed by server:
  it's visible to its server and to the person who installed DMbot (see its policies).
- The `dmbot.*` settings that open these doors (server, person, install server, plan
  writer, cleanup) are set by dmbot.db.Database, plus two one-statement switches inside
  a transaction it opened: dmbot.web.me (server by server for /me, then the person) and
  dmbot.entitlements.read (the person, for one read). The database enforces each door's
  limits, but which code may open which door is a Python boundary: only dmbot.web opens
  Database.plan_writer() (tests/test_entitlements_writer.py checks).
- Exception: a *routing* table may skip row-level security if it holds nothing but
  Discord server IDs (no names, settings, or campaign data), because a process has to
  find its servers before it can open a per-server transaction. List it in
  ROUTING_TABLES with a comment saying why.
"""

from __future__ import annotations

from typing import Any

from dmbot import migrations
from dmbot.migrations._shared import (
    OFFER_INSERT_COLUMNS as OFFER_INSERT_COLUMNS,
)

Migration = tuple[str, str]

# The website API's own database role (#498). Its policies only ever narrow what it sees;
# they never apply to the bot (role `dmbot`). The role itself is made by
# whoever runs Postgres (deploy/postgres-init/02-dmbot-web.sh, or the README for an
# existing server), because DMbot's own role can't create roles.
WEB_ROLE = "dmbot_web"

# The website's role is held to its session's servers by these RESTRICTIVE policies
# (ANDed with each table's own), made `TO dmbot_web` so the bot's role never runs them
# and any role that is a member of dmbot_web is held too. Database.migrate (re)makes them
# with the grants, whenever the role exists. What they guard against: the API's code
# setting a server it shouldn't (a bug), not a taken-over API process, which can write
# its own session row.
WEB_ROLE_POLICIES = """
    DROP POLICY IF EXISTS web_session_servers ON campaigns;
    CREATE POLICY web_session_servers ON campaigns AS RESTRICTIVE TO dmbot_web
        USING (guild_id = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
        WITH CHECK (guild_id = ANY ((SELECT dmbot_web_guilds())::BIGINT[]));
    DROP POLICY IF EXISTS web_session_servers ON campaign_dms;
    CREATE POLICY web_session_servers ON campaign_dms AS RESTRICTIVE TO dmbot_web
        USING (guild_id = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
        WITH CHECK (guild_id = ANY ((SELECT dmbot_web_guilds())::BIGINT[]));
    -- Reading: the session's servers, or the person's own installs (the account page lists
    -- them even after a server leaves their list). Writing: only the one install server,
    -- only one they manage, and only naming themselves.
    DROP POLICY IF EXISTS web_session_servers ON installs;
    CREATE POLICY web_session_servers ON installs AS RESTRICTIVE TO dmbot_web
        USING (guild_id = ANY ((SELECT dmbot_web_guilds())::BIGINT[])
               OR installed_by_user_id = dmbot_current_user())
        WITH CHECK (guild_id = dmbot_install_guild()
                    AND guild_id = ANY ((SELECT dmbot_web_managed_guilds())::BIGINT[])
                    AND installed_by_user_id = dmbot_current_user());
    -- Accepting a hand-over (#614, #437 decision 7): the website may make the signed-in
    -- person a campaign's owner, and add them as one of its DMs, only while that campaign
    -- has an open offer to them that is under 7 days old. Checked here, not just in code.
    -- (The store's campaign lock, SELECT ... FOR UPDATE, passes this UPDATE rule too.)
    DROP POLICY IF EXISTS web_accept_handover ON campaigns;
    CREATE POLICY web_accept_handover ON campaigns AS RESTRICTIVE FOR UPDATE TO dmbot_web
        USING (EXISTS (SELECT 1 FROM campaign_handover_offers o
                       WHERE o.guild_id = campaigns.guild_id AND o.campaign_id = campaigns.id
                         AND o.to_user_id = dmbot_current_user() AND o.status = 'open'
                         AND o.created_at + 7 * 86400 > dmbot_now()))
        WITH CHECK (owner_user_id = dmbot_current_user());
    DROP POLICY IF EXISTS web_accept_handover ON campaign_dms;
    CREATE POLICY web_accept_handover ON campaign_dms AS RESTRICTIVE FOR INSERT TO dmbot_web
        WITH CHECK (user_id = dmbot_current_user()
                    AND EXISTS (SELECT 1 FROM campaign_handover_offers o
                                WHERE o.guild_id = campaign_dms.guild_id
                                  AND o.campaign_id = campaign_dms.campaign_id
                                  AND o.to_user_id = dmbot_current_user() AND o.status = 'open'
                                  AND o.created_at + 7 * 86400 > dmbot_now()));
    -- The person's own consent, in the session's servers (#1150: the data export; #1151: where
    -- DMbot may record them). It may read and delete (stop) its own row, never add or change one.
    DROP POLICY IF EXISTS web_own_rows ON consent;
    CREATE POLICY web_own_rows ON consent AS RESTRICTIVE TO dmbot_web
        USING (user_id = dmbot_current_user()
               AND guild_id = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
        WITH CHECK (user_id = dmbot_current_user()
                    AND guild_id = ANY ((SELECT dmbot_web_guilds())::BIGINT[]));
    -- Hand-over offers (#614): only ones the signed-in person sent or was sent, in the
    -- session's servers. The store's own checks (owner, recipient, 7 days) come on top.
    DROP POLICY IF EXISTS web_session_servers ON campaign_handover_offers;
    CREATE POLICY web_session_servers ON campaign_handover_offers AS RESTRICTIVE TO dmbot_web
        USING (guild_id = ANY ((SELECT dmbot_web_guilds())::BIGINT[])
               AND dmbot_current_user() IN (from_user_id, to_user_id))
        WITH CHECK (guild_id = ANY ((SELECT dmbot_web_guilds())::BIGINT[])
                    AND dmbot_current_user() IN (from_user_id, to_user_id));
    """

# The person's own character sheets (#1150), applied once the table has its player column (an
# older schema, as in a test of a backfill, doesn't).
WEB_ROLE_SHEET_POLICY = """
    DROP POLICY IF EXISTS web_own_rows ON character_sheets;
    CREATE POLICY web_own_rows ON character_sheets AS RESTRICTIVE TO dmbot_web
        USING (player_id = dmbot_current_user()
               AND guild_id = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
        WITH CHECK (player_id = dmbot_current_user()
                    AND guild_id = ANY ((SELECT dmbot_web_guilds())::BIGINT[]));
"""

# The DM's recaps on the website (#1382): the signed-in person's own campaigns' recaps only, in
# their session's servers. The campaign's DM list is read under the same rule, so being a DM is
# what opens a row. Applied once the recap table exists.
WEB_ROLE_RECAP_POLICY = """
    DROP POLICY IF EXISTS web_dm_recaps ON recap_jobs;
    CREATE POLICY web_dm_recaps ON recap_jobs AS RESTRICTIVE TO dmbot_web
        USING (guild_id = ANY ((SELECT dmbot_web_guilds())::BIGINT[])
               AND EXISTS (SELECT 1 FROM campaign_dms d
                           WHERE d.guild_id = recap_jobs.guild_id
                             AND d.campaign_id = recap_jobs.campaign_id
                             AND d.user_id = dmbot_current_user()))
        WITH CHECK (guild_id = ANY ((SELECT dmbot_web_guilds())::BIGINT[])
                    AND EXISTS (SELECT 1 FROM campaign_dms d
                                WHERE d.guild_id = recap_jobs.guild_id
                                  AND d.campaign_id = recap_jobs.campaign_id
                                  AND d.user_id = dmbot_current_user()));
"""

# Applied only once the offers table has all four bot-only columns (an older schema, as in a test
# of a backfill, doesn't, and a rule can't name a column that isn't there).
WEB_ROLE_OFFER_POLICY = """
    -- Creating an offer (#728, #690 follow-up; #2532). The website's role may INSERT only the
    -- columns in OFFER_INSERT_COLUMNS, never the whole table, and this rule stays on top: an offer
    -- from the website is the signed-in owner's own, by email, still open, and not claimed or
    -- announced. Otherwise the site could write itself an offer on someone else's campaign.
    DROP POLICY IF EXISTS web_new_offer ON campaign_handover_offers;
    CREATE POLICY web_new_offer ON campaign_handover_offers AS RESTRICTIVE FOR INSERT TO dmbot_web
        WITH CHECK (via_email AND token_hash IS NOT NULL AND to_user_id = 0
                    -- an offer from the website is only ever by email (#2532): a Discord offer
                    -- would be sent to a person as buttons, which PLAN retires; it is born
                    -- delivered, because the bot has no Discord message to send for it
                    AND delivered_at = created_at
                    AND created_at BETWEEN dmbot_now() - 300 AND dmbot_now() + 300
                    AND claimed_at IS NULL AND message_id IS NULL
                    AND end_told_at IS NULL AND status = 'open' AND decided_at IS NULL
                    AND from_user_id = dmbot_current_user()
                    -- only the campaign's own owner can offer it: without this, anyone in the
                    -- server could write themselves an offer and then accept it
                    AND EXISTS (SELECT 1 FROM campaigns c
                                WHERE c.guild_id = campaign_handover_offers.guild_id
                                  AND c.id = campaign_handover_offers.campaign_id
                                  AND c.owner_user_id = campaign_handover_offers.from_user_id));
"""

# What the website's role may touch at all: its own tables, and only reads of the two
# server tables /me needs. Everything else (consent, transcripts, memory...) is refused
# outright. Applied by Database.migrate whenever the role exists, so a new table is never
# opened to it by accident: it has to be added here.
# The functions the website's role may call (name, signature), granted in Database.migrate.
WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = migrations.WEB_ROLE_FUNCTIONS

WEB_ROLE_GRANTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("SELECT", ("schema_migrations",)),
    # The person's own consent and character sheets, for the data export (#1150); the
    # restrictive web_own_rows policies hold it to their own rows in their servers. The right
    # to stop (DELETE) on consent is the website's Stop recording me (#1151): the same
    # policies hold it to the person's own row; it can never add or change a yes.
    ("SELECT", ("character_sheets",)),
    # A DM's recaps (#1382): read, edit the text, Post, Try again and Don't post. Never the
    # speakers, the call count, the original draft or the grade, which only the bot writes.
    ("SELECT", ("recap_jobs",)),
    (
        "UPDATE (summary, housekeeping, status, tries, state)",
        ("recap_jobs",),
    ),
    ("SELECT, DELETE", ("consent",)),
    # Accepting a hand-over (#614) makes the person the owner and one of the DMs; the
    # store locks the campaign row first (FOR UPDATE needs an UPDATE right), and answering
    # changes only an offer's status.
    ("SELECT, UPDATE (owner_user_id)", ("campaigns",)),
    ("SELECT, INSERT", ("campaign_dms",)),
    ("SELECT, UPDATE (status, decided_at)", ("campaign_handover_offers",)),
    (f"INSERT ({', '.join(OFFER_INSERT_COLUMNS)})", ("campaign_handover_offers",)),  # #2532
    ("SELECT, INSERT, UPDATE", ("installs", "entitlements")),
    ("SELECT, INSERT", ("payment_events", "try_it_used")),
    # The unsubscribe link (#1229) may be written, never read: the list stays the bot's. The
    # website sends its own account emails through dmbot_claim_email (WEB_ROLE_FUNCTIONS), and
    # has no grant on sent_emails.
    ("INSERT", ("email_unsubscribed",)),
    ("INSERT, DELETE", ("feedback",)),
    # Support tickets (#1340, #1373): added by the signed-in person; read back by them only as a
    # list of their own (`read_own`: when, subject, status), never the message, which has no
    # column right at all. The admin page reads the message through dmbot_support_message (a
    # door), and closes with the status UPDATE; the sweep and account deletion delete.
    ("INSERT, DELETE", ("support_tickets",)),
    (
        "SELECT (id, created_at, user_id, plan, subject, source, status, ai_next_at, ai_failed_at,"
        " escalated_reason, handed_over_reason, handed_over_at)",
        ("support_tickets",),
    ),
    (
        "UPDATE (status, ai_next_at, ai_failed_at, escalated_reason, handed_over_reason,"
        " handed_over_at)",
        ("support_tickets",),
    ),
    # The thread (#1431): replies are added and listed (not their words) only while the admin
    # door is open; the words come back through dmbot_support_thread.
    ("INSERT", ("support_replies",)),
    ("SELECT (id, ticket_id, who, created_at)", ("support_replies",)),
    # The weekly support email's once-only mark, through the admin door only (#1514).
    ("SELECT, INSERT, UPDATE", ("support_week_mail",)),
    # The IDs of the emails sent for a ticket, to thread the next one (#2192): admin door only.
    ("SELECT, INSERT", ("support_mail_ids",)),
    ("SELECT, INSERT, UPDATE, DELETE", ("web_users", "web_sessions")),
    # The improvement opt-out (#1609): the person's own row, through the website's switch.
    ("SELECT, INSERT, DELETE", ("improvement_opt_outs",)),
    # Emailed sign-in links (#1231): made and used by the website, never read by anyone else.
    # (Only `used_at` can be changed: a holder of a link can spend it, never revive or move it.)
    ("SELECT, INSERT, DELETE", ("email_sign_in_links",)),
    ("UPDATE (used_at)", ("email_sign_in_links",)),
    # A waiting change of the account's email (#2349): made, replaced, read, confirmed (deleted).
    ("SELECT, INSERT, UPDATE, DELETE", ("email_changes",)),
    # Free access (#771): the admin page gives, changes and revokes grants (through
    # Database.grant_writer()); account deletion deletes the person's own.
    ("SELECT, INSERT, UPDATE, DELETE", ("access_grants",)),
    ("SELECT, INSERT", ("access_log",)),
)

# Consent given under older wording never counts for an import: the store door refuses a caller
# that passes less (the SQL says 6; a test checks the two agree and are not above TERMS_VERSION).
IMPORT_TERMS_FLOOR = 6

SHARED_RECAP_SESSION = """
    -- A shared recap says which session it was and how long it ran (#2418), the same two facts the
    -- DM's page shows. The public door reads them from the transcripts' own sessions, so the
    -- number is the one the transcripts show. It needs one more read-only policy, tied (like the
    -- others in RECAP_SHARE) to the one recap's server and campaign the function itself found;
    -- only the number and the length leave the function, never a line or a speaker.
    DO $do$
    BEGIN
        EXECUTE format(
            'CREATE POLICY recap_share_read ON transcript_sessions FOR SELECT TO %I
                USING (guild_id = dmbot_recap_share_guild()
                       AND campaign_id = dmbot_recap_share_campaign())',
            current_user);
    END
    $do$;

    CREATE OR REPLACE FUNCTION dmbot_recap_shared(tok TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        job recap_jobs%ROWTYPE;
        sess_number BIGINT;
        sess_seconds BIGINT;
        out JSONB;
    BEGIN
        IF tok IS NULL OR tok !~ '^[A-Za-z0-9_-]{43}$' THEN
            RETURN NULL;
        END IF;
        PERFORM set_config('dmbot.recap_share_tok', tok, true);
        SELECT * INTO job FROM recap_jobs WHERE share_token = tok AND status = 'posted';
        IF NOT FOUND THEN
            PERFORM set_config('dmbot.recap_share_tok', '', true);
            RETURN NULL;
        END IF;
        PERFORM set_config('dmbot.recap_share_guild', job.guild_id::TEXT, true);
        PERFORM set_config('dmbot.recap_share_campaign', job.campaign_id, true);
        -- Which session it was and how long it ran, as the transcripts page numbers and times it
        -- (`dmbot_web_transcript_sessions`: Session N in order of start among the campaign's saved
        -- sessions, those whose transcripts were deleted by retention included). Only these two
        -- numbers leave: nothing of the transcript itself.
        SELECT x.number, x.seconds INTO sess_number, sess_seconds FROM (
            SELECT t.started_at, row_number() OVER (ORDER BY t.started_at, t.id) AS number,
                   CASE WHEN t.ended_at IS NULL THEN NULL
                        ELSE GREATEST(t.ended_at - t.started_at, 0) END AS seconds
            FROM transcript_sessions t
            WHERE t.guild_id = job.guild_id AND t.campaign_id = job.campaign_id
              AND (t.line_count > 0 OR t.transcripts_deleted_at IS NOT NULL)) x
        WHERE x.started_at = job.session_at ORDER BY x.number LIMIT 1;
        out := jsonb_build_object(
            'title', job.title, 'summary', job.summary, 'postedAt', job.decided_at,
            'guild', job.guild_id::TEXT, 'campaign', job.campaign_id, 'at', job.session_at,
            'audio', job.audio, 'audioHash', job.audio_text_hash, 'audioChoice', job.audio_choice,
            'sessionNumber', sess_number, 'sessionSeconds', sess_seconds,
            'audioOn', COALESCE((SELECT k.recaps_audio FROM campaigns k
                                 WHERE k.guild_id = job.guild_id AND k.id = job.campaign_id),
                                false),
            'secret', COALESCE((
                SELECT jsonb_agg(DISTINCT a.text)
                FROM memory_aliases a JOIN memory_entities e
                  ON e.guild_id = a.guild_id AND e.campaign_id = a.campaign_id
                 AND e.id = a.entity_id
                WHERE a.guild_id = job.guild_id AND a.campaign_id = job.campaign_id
                  AND (a.secret OR e.audience_limited OR e.hidden_from_players)), '[]'::jsonb),
            'allowed', COALESCE((
                SELECT jsonb_agg(DISTINCT a.text)
                FROM memory_aliases a JOIN memory_entities e
                  ON e.guild_id = a.guild_id AND e.campaign_id = a.campaign_id
                 AND e.id = a.entity_id
                WHERE a.guild_id = job.guild_id AND a.campaign_id = job.campaign_id
                  AND NOT a.secret AND NOT e.audience_limited AND NOT e.hidden_from_players
                  AND a.status = 'confirmed' AND e.status = 'confirmed'
                  AND e.type <> 'person'), '[]'::jsonb));
        PERFORM set_config('dmbot.recap_share_tok', '', true);
        PERFORM set_config('dmbot.recap_share_guild', '', true);
        PERFORM set_config('dmbot.recap_share_campaign', '', true);
        RETURN out;
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_recap_shared(TEXT) SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
"""


MIGRATIONS: tuple[Migration, ...] = migrations.MIGRATIONS

# Tables that must have row-level security. A test checks every table in the schema
# except these is listed here, so a new table can't forget its policy.
ISOLATED_TABLES: tuple[str, ...] = migrations.ISOLATED_TABLES

# A person's own rows (the website, #435): row-level security on `user_id`, set by
# Database.user(). Sessions can also be found by their cookie hash (Database.session()),
# and installs are visible to their server and to the person who installed DMbot.
USER_ISOLATED_TABLES: tuple[str, ...] = migrations.USER_ISOLATED_TABLES

# Add-only: no policy allows reading a row; the team reads them as the database's
# administrator (#665).
WRITE_ONLY_TABLES = ("feedback",)

# Hold only server IDs (see the rules at the top of this file).
ROUTING_TABLES: tuple[str, ...] = migrations.ROUTING_TABLES

UNSCOPED_TABLES = (
    "schema_migrations",
    "ontology_predicate_versions",
    "sent_emails",
    "email_unsubscribed",
    "trial_extensions",
    "move_codes",
    "account_moves",
    "recap_audio_strays",
    "move_code_asks",
    # One copy of each published homebrew page, whoever imported it (#1633): public content with
    # no campaign scope, the second exception to campaign isolation. No grant for the website.
    "homebrew_pages",
    *ROUTING_TABLES,
)


def __getattr__(name: str) -> Any:
    """The names the old schema.py defined, now in the migration files: tests and code that
    import them from here keep working."""
    return migrations.moved(name)
