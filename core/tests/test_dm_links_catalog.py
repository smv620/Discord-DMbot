"""Every private message to a DM goes through the shared link helper (#3663), the way
`test_campaign_move_cascade` lists tables. A file that sends a private message must be on one of
the three lists below; a new sender fails this test until it is added with its reason."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "dmbot"
SENDS = re.compile(
    r"create_dm\(|sidebar_send_dm\(|_dm_message\(|\buser\.send\(|\bmember\.send\(|\.dm_channel"
)

# Reaches the DM through `ask_privately`, `ask_live` or `tell_dms` in bot.py, which add the links.
THROUGH_THE_HELPER = {"bot.py"}
# Not converted yet: each is a later piece of #3663 Part A, and the list shrinks to empty there.
NOT_YET = {
    "dm_screen/game_masters.py",
    "dm_screen/handover.py",
    "dm_screen/site_offers.py",
    "fightbot.py",
    "questions/discord.py",
    "recaps/discord.py",
    "sheet_reread.py",
    "ui/dmbot_commands.py",
    "ui/theatre_button.py",
}
# Not a message about a question to answer, so no links (each with its reason).
OUT_OF_SCOPE = {
    "consent_dm.py": "messages to players (consent), not to the DM (#3663 out of scope)",
    "sidebar/service.py": "the DM's own live questions and DMbot's in-game replies at the table",
}


class EveryPrivateMessageToTheDM(unittest.TestCase):
    def senders(self) -> set[str]:
        found: set[str] = set()
        for path in SRC.rglob("*.py"):
            rel = path.relative_to(SRC).as_posix()
            if rel.startswith("web/") or rel.startswith("migrations/"):
                continue
            if SENDS.search(path.read_text(encoding="utf-8")):
                found.add(rel)
        return found

    def test_every_sender_is_listed(self) -> None:
        listed = THROUGH_THE_HELPER | NOT_YET | set(OUT_OF_SCOPE)
        self.assertEqual(
            sorted(self.senders() - listed),
            [],
            "A file sends a private message but is on no list in this test: send it through "
            "ui/dm_links.py (or list it with a reason).",
        )

    def test_no_list_names_a_file_that_no_longer_sends(self) -> None:
        listed = THROUGH_THE_HELPER | NOT_YET | set(OUT_OF_SCOPE)
        self.assertEqual(sorted(listed - self.senders()), [])

    def test_the_bot_uses_the_helper_where_it_asks_and_tells(self) -> None:
        text = (SRC / "bot.py").read_text(encoding="utf-8")
        self.assertGreaterEqual(text.count("dm_links.for_campaign("), 3)  # ask, ask live, tell
