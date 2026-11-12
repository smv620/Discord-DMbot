"""0450_dm_players

A DM's remembered players, kept on the DM's account across campaigns and servers (#4226; the
owner, 2026-11-12: a DM is likely to have the same players in new campaigns, so even if the
campaign is deleted the list is tied to the DM's account and not to a campaign).
"""

from __future__ import annotations

# The table is a deliberate, narrow exception to "nothing crosses between servers" (docs/PLAN.md,
# Isolation): a DM's own list of the players they have played with. **No campaign id, no character,
# no story, no server id**; only who (two Discord ids), the player's name as last seen, two times
# and a count of campaigns. Only that DM reads their list, the player reads (and may clear) which
# DMs remember them, and each reader is held to that by a function below, never by a table grant.
# Written by a trigger on `memory_entities`, so every path that links a player to a character is
# covered at once (the `plays` link, the wizard's step 3, the Names page, an import's speaker
# choice, the website's doors): when a confirmed player character gets a player, every DM of that
# campaign gets or refreshes a row. `campaigns` goes up only when this player had no other
# confirmed character in that campaign yet, so it counts campaigns, not characters. It stays when
# a character is unlinked or a campaign is deleted (the owner's decision), and "Stop recording me"
# leaves it alone: that stops recording, not the DM's memory that they played together.  Row-level
# security: forced, with no policy open except while one of the functions below runs (its own
# `dmbot.owner_sync` gate, as 0431 does). Each gate says which rows: the writers any, the DM's
# readers and forget their own, the player's their own.
_GATES: tuple[tuple[str, tuple[str, ...], str], ...] = (
    # (gate, commands, the rows it opens)
    ("dm_players_note", ("SELECT", "INSERT", "UPDATE"), "true"),
    ("dm_players_dm", ("SELECT", "DELETE"), "dm_user_id = dmbot_current_user()"),
    ("dm_players_player", ("SELECT", "DELETE"), "player_user_id = dmbot_current_user()"),
    (
        "dm_players_forget",
        ("SELECT", "DELETE"),
        "(dm_user_id = dmbot_current_user() OR player_user_id = dmbot_current_user())",
    ),
)


def _policies() -> str:
    out = ["    DO $do$\n    BEGIN\n"]
    for gate, commands, rows in _GATES:
        for command in commands:
            name = f"{gate}_{command.lower()}"
            check = f"(dmbot_owner_sync() = ''{gate}'' AND {rows})"
            if command == "INSERT":
                clause = f"WITH CHECK {check}"
            elif command == "UPDATE":
                clause = f"USING {check} WITH CHECK {check}"
            else:
                clause = f"USING {check}"
            out.append(
                f"        EXECUTE format('CREATE POLICY {name} ON dm_players FOR {command}"
                f" TO %I {clause}', current_user);\n"
            )
    out.append("    END\n    $do$;\n")
    return "".join(out)


def _gated(gate: str, body: str) -> str:
    """The body of a function that opens one gate while it runs and puts the old value back."""
    return f"""
        PERFORM set_config('dmbot.owner_sync', '{gate}', true);
{body}
        PERFORM set_config('dmbot.owner_sync', before, true);"""


_DM_ONLY = (
    "        IF dmbot_current_user() IS NULL THEN\n            RETURN {nothing};\n        END IF;\n"
)


def _function(
    signature: str,
    returns: str,
    gate: str,
    body: str,
    *,
    declare: str = "",
    guard: str = "",
    result: str = "",
) -> str:
    """One definer function: its guard first, then its body with the gate open, then its answer.
    The old gate value is put back on success (an error rolls the setting back)."""
    after = f"\n        RETURN {result};" if result else ""
    return f"""
    CREATE FUNCTION {signature} RETURNS {returns}
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        before TEXT := COALESCE(current_setting('dmbot.owner_sync', true), '');
{declare}
    BEGIN
{guard}{_gated(gate, body)}{after}
    END
    $fn$;"""


SQL = (
    r"""
    CREATE TABLE dm_players (
        dm_user_id      BIGINT NOT NULL CHECK (dm_user_id > 0),
        player_user_id  BIGINT NOT NULL CHECK (player_user_id > 0),
        last_name       TEXT NOT NULL DEFAULT '' CHECK (char_length(last_name) <= 100),
        first_played_at BIGINT NOT NULL CHECK (first_played_at > 0),
        last_played_at  BIGINT NOT NULL,
        campaigns       INTEGER NOT NULL DEFAULT 1 CHECK (campaigns >= 1),
        PRIMARY KEY (dm_user_id, player_user_id),
        CHECK (dm_user_id <> player_user_id),
        CHECK (last_played_at >= first_played_at)
    );
    CREATE INDEX dm_players_by_player ON dm_players (player_user_id);
    ALTER TABLE dm_players ENABLE ROW LEVEL SECURITY;
    ALTER TABLE dm_players FORCE ROW LEVEL SECURITY;
"""
    + _policies()
    + _function(
        "dmbot_dm_players_trigger()",
        "trigger",
        "dm_players_note",
        r"""
        INSERT INTO dm_players (dm_user_id, player_user_id, last_name, first_played_at,
                                last_played_at, campaigns)
            SELECT m.user_id, NEW.played_by, left(COALESCE(d.display_name, ''), 100),
                   now_s, now_s, 1
            FROM campaign_dms m
            LEFT JOIN member_directory d
                ON d.guild_id = NEW.guild_id AND d.user_id = NEW.played_by
            WHERE m.guild_id = NEW.guild_id AND m.campaign_id = NEW.campaign_id
              AND m.user_id > 0 AND m.user_id <> NEW.played_by
            ON CONFLICT (dm_user_id, player_user_id) DO UPDATE SET
                last_played_at = GREATEST(dm_players.last_played_at, EXCLUDED.last_played_at),
                last_name = CASE WHEN EXCLUDED.last_name <> '' THEN EXCLUDED.last_name
                                 ELSE dm_players.last_name END,
                campaigns = dm_players.campaigns + CASE WHEN another THEN 0 ELSE 1 END;""",
        result="NEW",  # (returned after the gate is put back, or it would stay open)
        declare=r"""
        now_s BIGINT := floor(extract(epoch FROM clock_timestamp()))::BIGINT;
        another BOOLEAN;""",
        guard=r"""
        IF NEW.played_by IS NULL OR NEW.played_by <= 0 OR NEW.status <> 'confirmed'
           OR NOT (COALESCE(NEW.type = 'player_character', false)
                   OR COALESCE(NEW.role = 'player_character', false))
           OR (TG_OP = 'UPDATE' AND OLD.played_by IS NOT DISTINCT FROM NEW.played_by
               AND OLD.status IS NOT DISTINCT FROM NEW.status
               AND OLD.type IS NOT DISTINCT FROM NEW.type
               AND OLD.role IS NOT DISTINCT FROM NEW.role) THEN
            RETURN NEW;
        END IF;
        -- (a NULL role must not make the test NULL: DMbot's own 'person' rows have none)
        -- (this player already played a confirmed character in this campaign: not a new campaign)
        another := EXISTS (SELECT 1 FROM memory_entities e
                           WHERE e.guild_id = NEW.guild_id AND e.campaign_id = NEW.campaign_id
                             AND e.played_by = NEW.played_by AND e.id <> NEW.id
                             AND e.status = 'confirmed'
                             AND (e.type = 'player_character'
                                  OR COALESCE(e.role = 'player_character', false)));
""",
    )
    + r"""
    CREATE TRIGGER memory_entities_dm_players
        AFTER INSERT OR UPDATE OF played_by, status, type, role ON memory_entities
        FOR EACH ROW EXECUTE FUNCTION dmbot_dm_players_trigger();

    -- The campaigns that already exist (CLAUDE.md, #704: a migration that changes rows opens
    -- row-level security for itself, for every table it reads and the one it writes).
    CREATE POLICY migrate_backfill ON memory_entities FOR SELECT USING (true);
    CREATE POLICY migrate_backfill ON campaign_dms FOR SELECT USING (true);
    CREATE POLICY migrate_backfill ON member_directory FOR SELECT USING (true);
    CREATE POLICY migrate_backfill ON dm_players FOR ALL USING (true) WITH CHECK (true);
    INSERT INTO dm_players (dm_user_id, player_user_id, last_name, first_played_at,
                            last_played_at, campaigns)
        SELECT m.user_id, e.played_by, left(COALESCE(max(d.display_name), ''), 100),
               min(e.created_at), max(e.created_at), count(DISTINCT (e.guild_id, e.campaign_id))
        FROM memory_entities e
        JOIN campaign_dms m ON m.guild_id = e.guild_id AND m.campaign_id = e.campaign_id
        LEFT JOIN member_directory d ON d.guild_id = e.guild_id AND d.user_id = e.played_by
        WHERE e.played_by > 0 AND e.status = 'confirmed' AND e.created_at > 0
          AND (e.type = 'player_character' OR e.role = 'player_character')
          AND m.user_id > 0 AND m.user_id <> e.played_by
        GROUP BY m.user_id, e.played_by
        ON CONFLICT (dm_user_id, player_user_id) DO NOTHING;
    DROP POLICY migrate_backfill ON dm_players;
    DROP POLICY migrate_backfill ON member_directory;
    DROP POLICY migrate_backfill ON campaign_dms;
    DROP POLICY migrate_backfill ON memory_entities;
"""
    # The picker mark: has the signed-in DM played with this person (any campaign, any server)?
    # Only the DM's own row is read; WebDev's person-picker doors call it and still list only the
    # members of the asked server, so a name is never shown for someone who is not there.
    + _function(
        "dmbot_dm_remembers(p BIGINT)",
        "BOOLEAN",
        "dm_players_dm",
        r"""
        found := EXISTS (SELECT 1 FROM dm_players r
                         WHERE r.dm_user_id = dmbot_current_user() AND r.player_user_id = p);""",
        declare="        found BOOLEAN := false;",
        guard=_DM_ONLY.format(nothing="false"),
        result="found",
    )
    # The DM's own list, for their account page ("Forget this player").
    + _function(
        "dmbot_web_dm_players_list()",
        "JSONB",
        "dm_players_dm",
        r"""
        found := COALESCE((SELECT jsonb_agg(jsonb_build_object(
                'player', r.player_user_id::TEXT, 'name', r.last_name,
                'since', r.first_played_at, 'last', r.last_played_at, 'campaigns', r.campaigns)
                ORDER BY r.last_played_at DESC, r.player_user_id)
            FROM dm_players r WHERE r.dm_user_id = dmbot_current_user()), '[]'::jsonb);""",
        declare="        found JSONB;",
        guard=_DM_ONLY.format(nothing="NULL"),
        result="found",
    )
    + _function(
        "dmbot_web_dm_players_forget(p BIGINT)",
        "JSONB",
        "dm_players_dm",
        r"""
        DELETE FROM dm_players r WHERE r.dm_user_id = dmbot_current_user()
                                   AND r.player_user_id = p;
        GET DIAGNOSTICS n = ROW_COUNT;""",
        declare="        n INTEGER;",
        guard=_DM_ONLY.format(nothing="NULL"),
        result="jsonb_build_object('status', 'ok', 'removed', n)",
    )
    # The player's side: which DMs remember them, and clearing it (one DM, or all with 0).
    + _function(
        "dmbot_web_players_dms()",
        "JSONB",
        "dm_players_player",
        r"""
        found := COALESCE((SELECT jsonb_agg(jsonb_build_object(
                'dm', r.dm_user_id::TEXT,
                'name', COALESCE(NULLIF(u.display_name, ''), ''),
                'since', r.first_played_at, 'campaigns', r.campaigns)
                ORDER BY r.first_played_at, r.dm_user_id)
            FROM dm_players r LEFT JOIN web_users u ON u.user_id = r.dm_user_id
            WHERE r.player_user_id = dmbot_current_user()), '[]'::jsonb);""",
        declare="        found JSONB;",
        guard=_DM_ONLY.format(nothing="NULL"),
        result="found",
    )
    + _function(
        "dmbot_web_players_clear(d BIGINT)",
        "JSONB",
        "dm_players_player",
        r"""
        DELETE FROM dm_players r WHERE r.player_user_id = dmbot_current_user()
                                   AND (d = 0 OR r.dm_user_id = d);
        GET DIAGNOSTICS n = ROW_COUNT;""",
        declare="        n INTEGER;",
        guard=_DM_ONLY.format(nothing="NULL"),
        result="jsonb_build_object('status', 'ok', 'removed', n)",
    )
    # The bot refreshes the name it has for a player in every DM's row (the name sweep, #4230).
    + _function(
        "dmbot_dm_players_name(p BIGINT, nm TEXT)",
        "VOID",
        "dm_players_note",
        r"""
        UPDATE dm_players r SET last_name = left(nm, 100)
            WHERE r.player_user_id = p AND r.last_name <> left(nm, 100);""",
    )
    # Account deletion (#4141 pattern): rows naming the person, in any DM's list, and their own
    # list.
    + _function(
        "dmbot_forget_dm_players()",
        "VOID",
        "dm_players_forget",
        r"""
        DELETE FROM dm_players r WHERE r.dm_user_id = dmbot_current_user()
                                   OR r.player_user_id = dmbot_current_user();""",
        guard=_DM_ONLY.format(nothing=""),
    )
)

_FUNCTIONS: tuple[str, ...] = (
    "dmbot_dm_players_trigger()",
    "dmbot_dm_remembers(BIGINT)",
    "dmbot_web_dm_players_list()",
    "dmbot_web_dm_players_forget(BIGINT)",
    "dmbot_web_players_dms()",
    "dmbot_web_players_clear(BIGINT)",
    "dmbot_dm_players_name(BIGINT, TEXT)",
    "dmbot_forget_dm_players()",
)
SQL += "\n    DO $do$\n    BEGIN\n"
for _sig in _FUNCTIONS:
    SQL += (
        f"        EXECUTE format('ALTER FUNCTION {_sig} SET search_path = pg_catalog, %I, pg_temp',"
        " current_schema());\n"
    )
SQL += "    END\n    $do$;\n"
for _sig in _FUNCTIONS:
    SQL += f"    REVOKE ALL ON FUNCTION {_sig} FROM PUBLIC;\n"

ISOLATED_TABLES: tuple[str, ...] = ()
USER_ISOLATED_TABLES: tuple[str, ...] = ()

# The website's doors (a DM's list and Forget, a player's list and Clear) and account deletion's
# call.
WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_web_dm_players_list", "dmbot_web_dm_players_list()"),
    ("dmbot_web_dm_players_forget", "dmbot_web_dm_players_forget(BIGINT)"),
    ("dmbot_web_players_dms", "dmbot_web_players_dms()"),
    ("dmbot_web_players_clear", "dmbot_web_players_clear(BIGINT)"),
    ("dmbot_forget_dm_players", "dmbot_forget_dm_players()"),
)
