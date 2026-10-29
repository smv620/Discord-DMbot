"""0222_npc_or_pc

Every character is an NPC or a PC: a god is an NPC (#2562).
"""

from __future__ import annotations

NPC_OR_PC = """
    -- Every character is an NPC or a PC (owner, 2026-10-23, #2562): the role "god" goes. A god
    -- becomes an NPC and keeps the word "god" in its description (unless it already says so),
    -- on the entry and in the change log's snapshots, so Undo across this still puts back an
    -- NPC. Nothing is deleted. The statements read and write memory_entities and memory_changes,
    -- which have forced row-level security and are migrated unscoped, so each is opened for this
    -- one migration only (CLAUDE.md, "A migration that changes rows must open row-level security
    -- for itself").
    CREATE POLICY migrate_backfill ON memory_entities FOR ALL USING (true) WITH CHECK (true);
    CREATE POLICY migrate_backfill ON memory_changes FOR ALL USING (true) WITH CHECK (true);
    UPDATE memory_entities SET
        role = 'npc',
        description = CASE
            WHEN description ~* '\\m(god|goddess|deity)\\M' THEN description
            ELSE btrim(left(description, 300 - 7) || ' A god.') END
        WHERE role = 'god';
    UPDATE memory_changes SET
        before = CASE WHEN before->>'role' = 'god' THEN jsonb_set(
            jsonb_set(before, '{role}', '"npc"'),
            '{description}', to_jsonb(CASE
                WHEN coalesce(before->>'description', '') ~* '\\m(god|goddess|deity)\\M'
                THEN coalesce(before->>'description', '')
                ELSE btrim(left(coalesce(before->>'description', ''), 300 - 7) || ' A god.') END))
            ELSE before END,
        after = CASE WHEN after->>'role' = 'god' THEN jsonb_set(
            jsonb_set(after, '{role}', '"npc"'),
            '{description}', to_jsonb(CASE
                WHEN coalesce(after->>'description', '') ~* '\\m(god|goddess|deity)\\M'
                THEN coalesce(after->>'description', '')
                ELSE btrim(left(coalesce(after->>'description', ''), 300 - 7) || ' A god.') END))
            ELSE after END
        WHERE table_name = 'memory_entities'
          AND (before->>'role' = 'god' OR after->>'role' = 'god');
    DROP POLICY migrate_backfill ON memory_changes;
    DROP POLICY migrate_backfill ON memory_entities;
    DO $do$
    DECLARE c TEXT;
    BEGIN
        FOR c IN SELECT conname FROM pg_constraint
                 WHERE conrelid = 'memory_entities'::regclass AND contype = 'c'
                   AND pg_get_constraintdef(oid) LIKE '%god%' LOOP
            EXECUTE format('ALTER TABLE memory_entities DROP CONSTRAINT %I', c);
        END LOOP;
    END
    $do$;
    ALTER TABLE memory_entities ADD CONSTRAINT memory_entities_role_check
        CHECK (role IS NULL OR role IN ('player_character', 'npc'));
"""

SQL = NPC_OR_PC
