"""Who still tells the party by a character's role (#4537, party knowledge slice 2a; design
`docs/design/party_knowledge.md` rule 13.1; Supervisor's ruling on #4537).

"The party" is one function, `memory/party_record.py` (`current_members`, `members_at`): no reader
lists player characters by role to mean the party, because a character who has left is still a
player character but no longer in it. A role is still the right test for THAT an entry is a player
character (a sheet, "played by", consent), so a file may mention it; but each one here is checked
for whether it means the party, and either moves to the function or is marked as identity, in the
reader pass (§14 slice 5). A new file that tests the role fails here until someone decides which it
is and adds it. The list may only shrink: a line whose file no longer mentions the role fails too.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "dmbot"
ROLE = re.compile(r"\bPLAYER_CHARACTER\b|'player_character'|\"player_character\"")
# (the role's own definition and the one function are not readers)
NOT_READERS = {
    "memory/models.py",
    "memory/party_record.py",
    # (#4537 2b) the role there asks "is this still a player character", beside the open stretch
    # that says "in the party": a waiting join question about one made an NPC is not sent
    "memory/join_questions.py",
    # (#4537 6a) a death asks "is this a player character" and then `party_record.current_members`
    # for "in the party": the role is the character's kind, the party is the one function's
    "memory/states.py",
    # (#5055 A) a tool's reason for a waiting entry: "this entry is a player character waiting for
    # who plays it", the character's kind, never who is in the party
    "devtools/why_missed.py",
}

# Files that test the role today, to be checked one by one (slice 5). Alphabetical. (#4537 3a:
# the pattern now also catches "player_character" in double quotes, which found 18 more.)
FILES: tuple[str, ...] = (
    "devtools/graph_key_check.py",
    "devtools/sample_run/characters.py",
    "devtools/sample_run/decide.py",
    "members.py",
    "memory/auto_accept.py",
    "memory/background.py",
    "memory/backstory_keep.py",
    "memory/backup.py",
    "memory/book_links.py",
    "memory/claims.py",
    "memory/importance_rank.py",
    "memory/importance_read.py",
    "memory/importance_step.py",
    "memory/kinds.py",
    "memory/knowers.py",
    "memory/lore_read.py",
    "memory/name_list.py",
    "memory/name_words.py",  # the guess rule moved here from ui/names.py (#5144)
    "memory/ontology.py",
    "memory/party.py",
    "memory/place_words.py",
    "memory/private_scene_store.py",
    "memory/pronouns.py",
    "memory/recall.py",
    "memory/reveal_proposals.py",
    "memory/revealed_aloud.py",
    "memory/roles_backed.py",
    "memory/store.py",
    "memory/story_chain_store.py",
    "memory/voice.py",
    "memory/web_actions.py",
    "memory/whereabouts.py",
    "memory/who_ranking.py",
    "recaps/inputs.py",
    "recaps/name_answers.py",
    "timebot/stamp.py",
    "timebot/store.py",
    "ui/companion.py",
    "ui/name_background.py",
    "ui/name_lists.py",
    "ui/name_questions.py",
    "ui/names.py",
    "web/all_names.py",
    "web/graph_cleanup.py",
    "web/name_found.py",  # the name card's guess leaves a player character out (#5218)
    "web/relationships.py",
    "wizard_finish.py",
)


class TheReaders(unittest.TestCase):
    def found(self) -> set[str]:
        return {
            str(p.relative_to(SRC))
            for p in SRC.rglob("*.py")
            if "migrations" not in p.parts
            and str(p.relative_to(SRC)) not in NOT_READERS
            and ROLE.search(p.read_text(encoding="utf-8"))
        }

    def test_no_new_file_tells_the_party_by_role(self) -> None:
        self.assertEqual(self.found() - set(FILES), set(), "add it here, or ask party_record")

    def test_the_list_only_shrinks(self) -> None:
        self.assertEqual(set(FILES) - self.found(), set(), "remove the line: it moved")

    def test_the_list_is_sorted(self) -> None:
        self.assertEqual(list(FILES), sorted(FILES))
