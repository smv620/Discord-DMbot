"""0573_outbox

The outbox (#3843; docs/PLAN.md, "A worker container, and a smaller bot.py"): what a worker wants
said in Discord, written as a row that the bot delivers, because only the bot holds the Discord
connection.
"""

from __future__ import annotations

from dmbot.migrations._shared import _isolate

# A row holds a kind word (`recap_ready`, ...), the ids the bot needs to build the message itself
# and counts: never a transcript line, a name or a document's words (a test plants text in a
# payload and checks the writer refuses it). The bot builds the words when it delivers, so a row
# made by a worker that cannot read a secret still gives the DM the same message. The row is made
# in the same transaction as the work that made it (`outbox.add` takes the caller's connection), so
# it exists exactly when the result does. The insert notifies the bot with the server's id only
# (notifications skip row-level security), and the bot polls every 30 s as a backstop. `sending`
# with an old `claimed_at` is a delivery whose bot died: it is claimed again, never twice at once.
# A row is swept after a few days. Isolated by server; the foreign key moves with the campaign.
SQL = """
    CREATE TABLE outbox (
        guild_id    BIGINT NOT NULL,
        campaign_id TEXT NOT NULL,
        id          BIGSERIAL,
        kind        TEXT NOT NULL CHECK (kind ~ '^[a-z_]{1,40}$'),
        payload     JSONB NOT NULL DEFAULT '{}'::jsonb,
        created_at  BIGINT NOT NULL,
        state       TEXT NOT NULL DEFAULT 'waiting'
                    CHECK (state IN ('waiting', 'sending', 'sent', 'failed')),
        tries       INTEGER NOT NULL DEFAULT 0,
        claimed_at  BIGINT,
        error       TEXT CHECK (error IS NULL OR length(error) <= 40),
        PRIMARY KEY (guild_id, id),
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE ON UPDATE CASCADE
    );
    CREATE INDEX outbox_due ON outbox (guild_id, state, created_at);
    CREATE FUNCTION dmbot_outbox_notify() RETURNS TRIGGER
        LANGUAGE plpgsql
        AS $fn$
        BEGIN
            PERFORM pg_notify('dmbot_outbox', NEW.guild_id::text);
            RETURN NEW;
        END;
        $fn$;
    CREATE TRIGGER outbox_notify AFTER INSERT ON outbox
        FOR EACH ROW EXECUTE FUNCTION dmbot_outbox_notify();
    """ + _isolate("outbox")

ISOLATED_TABLES: tuple[str, ...] = ("outbox",)
