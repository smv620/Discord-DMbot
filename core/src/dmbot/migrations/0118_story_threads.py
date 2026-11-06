"""0118_story_threads

A thread's states beside the conditions (#1662).
"""

from __future__ import annotations

STORY_THREADS = """
    -- Story threads (#1662; docs/STORY_MEMORY.md, section 2): a thread's state is kept in
    -- memory_states like a condition, so the CHECK that lists the allowed values learns the five
    -- thread states. The kinds (thread, quest, mystery, hook, goal) and the connections
    -- (in_thread, advances, resolves) are core, in code. No row changes.
    ALTER TABLE memory_states DROP CONSTRAINT memory_states_condition_check;
    ALTER TABLE memory_states ADD CONSTRAINT memory_states_condition_check CHECK (condition IN
        ('alive', 'dead', 'missing', 'captured', 'transformed', 'unknown',
         'intact', 'destroyed', 'lost',
         'hinted', 'open', 'active', 'resolved', 'abandoned'));
"""

SQL = STORY_THREADS
