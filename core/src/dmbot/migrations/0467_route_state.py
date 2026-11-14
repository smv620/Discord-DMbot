"""0467_route_state

A route's state (#4173 slice 6d): known, travelled or planned, on `route_to` connections only.
"""

from __future__ import annotations

# Supervisor's yes to the routes proposal on #4173 (2026-11-11, point 4): a column, not a word in
# the detail, because the detail holds the route's means and the state changes over time. '' is
# known (the way exists); 'travelled' is a journey the party made, in the session its
# `from_session_at` gives, from the subject to the object; 'planned' is the table's intent and
# never counts as where anyone is. Every other kind of connection keeps ''. No row changes (the
# default is ''), so no row-level security needs opening. Carried in the change log, merges and
# backups.
SQL = r"""
    ALTER TABLE memory_relations
        ADD COLUMN route_state TEXT NOT NULL DEFAULT ''
            CONSTRAINT memory_relations_route_state_check
                CHECK (route_state IN ('', 'travelled', 'planned'));
    ALTER TABLE memory_relations ADD CONSTRAINT memory_relations_route_state_kind
        CHECK (route_state = '' OR predicate = 'route_to');
"""
