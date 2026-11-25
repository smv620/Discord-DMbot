"""0635_link_source_kind

What kind of upload a kept connection's lore quote came from, and a graph report's verdicts as
structure (#3742 slice A).
"""

from __future__ import annotations

# Supervisor's ruling on #3742 (2026-11-25): the graph-improvement poster renders each report at
# post time, so the consent and source rules can be checked then; finished Markdown can't be
# checked. Two columns:
#
# - `memory_link_sources.upload_kind`: whether the upload a kept connection came from is the DM's
#   own writing (the 📜 lore or backstory read, confirmed as `own_writing`). Every writer today is
#   that read, or the waiting row it queued (`MemoryStore.record_link_source` and the backstory
#   keep). Rows from before are 'unknown': the poster then writes "a passage from the campaign's
#   lore", never the quote, which is the safe side. A shared adventure writes no link source.
# - `graph_reviews.verdicts`: the report's missed fixes as JSON, written by the review run in the
#   same INSERT as `report`, from the same objects (`graph_review.verdicts_json`). NULL for a report
#   from before, which is never posted (Supervisor, #3742 (c)). It holds the same quotes as `report`
#   and goes with the row: "Stop recording me" deletes every report that quotes the person. Its
#   size is bounded like `report` (60,000 characters): the run keeps it to the same shown fixes,
#   and the bound keeps any later writer from storing more.
SQL = r"""
    ALTER TABLE memory_link_sources ADD COLUMN upload_kind TEXT NOT NULL DEFAULT 'unknown'
        CONSTRAINT memory_link_sources_upload_kind_check
        CHECK (upload_kind IN ('own_writing', 'unknown'));
    ALTER TABLE graph_reviews ADD COLUMN verdicts JSONB
        CONSTRAINT graph_reviews_verdicts_check
        CHECK (verdicts IS NULL
               OR (jsonb_typeof(verdicts) = 'object' AND octet_length(verdicts::text) <= 200000));
"""

ISOLATED_TABLES: tuple[str, ...] = ()

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = ()
