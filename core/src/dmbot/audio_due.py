"""When a recap's narration may start (#2827): one text for the worker's pick, its lease and the
campaign move.
"""

AUDIO_MAX_TRIES = 2  # starts: the first, and one more an hour later
# A recap whose narration is waiting to be made and may start (placeholders: the tries limit, now).
# One text for the worker's pick (`RecapStore.audio_due`), its lease (`audio_lease`) and the
# campaign move (campaigns/moves.py), which must refuse exactly while the worker could start.
AUDIO_DUE_WHERE = (
    "state = 'ready' AND audio = 'queued' AND audio_tries < %s"
    " AND status IN ('draft', 'posting', 'posted') AND audio_next_at <= %s"
)
