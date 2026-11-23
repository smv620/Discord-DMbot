"""0599_names_cut

How many names a cap left out of a session import or a lore upload (#5055 A, with #5056 point 2).
"""

from __future__ import annotations

# Each reader that offers new names stops at a cap: the name scan of a session at 10
# (`scan.MAX_SUGGESTIONS`), the story reader's linker at 20 a session (`claims.NEW_NAME_LIMIT`) and
# a lore read at 200 a document (`background.MAX_NAMES`). Until now what a cap left out was not
# counted anywhere, so a name lost to a cap looked the same as a name never heard (Supervisor's
# ruling on #5055: count every cap's cut, no rule change). The DM is told in the import's or the
# upload's notice; the count is kept here too, so the sample run can read it from the database
# after the notice is gone. A count only, never a name. On an imported session the two are kept
# apart: `scan_cut`, the name scan's names past its 10 (set by the scan, so a retried scan counts
# once), and `story_held`, the story reader's new names past its 20, which wait for a later session
# rather than being lost (so the DM is not told; the sample run is). Defaulted columns change no
# rows, so no row-level-security policy is needed.
SQL = """
    ALTER TABLE session_imports
        ADD COLUMN scan_cut INT NOT NULL DEFAULT 0 CHECK (scan_cut >= 0),
        ADD COLUMN story_held INT NOT NULL DEFAULT 0 CHECK (story_held >= 0);
    ALTER TABLE lore_uploads
        ADD COLUMN names_cut INT NOT NULL DEFAULT 0 CHECK (names_cut >= 0);
    """
