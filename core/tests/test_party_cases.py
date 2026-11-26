"""The sample's party stage (#4537 slice 6c; Supervisor's rulings T and U): the invented party
cases (a lore, a session and a key), the reader's rules on them line by line, and the run's party
lines (questions by kind, stretches closed, and the goodbye questions scored against the key).
Counts only, never a name. Invented names."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from dmbot.devtools.sample_run import counts
from dmbot.memory.goodbye_reading import Goodbye, check_quote, names_who, whose
from dmbot.memory.join_questions import LEAVE
from tests.test_party_leave import DM_ID, GUILD, Party

CASES = Path(__file__).parent / "fixtures" / "story_graph"
KEY = CASES / "party_cases_key.json"
SPEAKERS = {"DM": 1, "player_ist": 11, "player_jac": 12, "player_eri": 13, "player_owe": 14}
PLAYS = {"Cerric": 11, "Belleros": 12, "Okthandom": 13, "Claude": 14}


def said() -> list[tuple[int, str]]:
    rows = (CASES / "party_cases_session.txt").read_text(encoding="utf-8").splitlines()
    out = []
    for row in rows:
        who, text = row.split("] ", 1)[1].split(": ", 1)
        out.append((SPEAKERS[who], text))
    return out


class TheCases(unittest.TestCase):
    # (each line with the quote and name a careful reader would give, and whether a question must
    # come of it: the key's two goodbyes, and every false one refused by a rule of its own)
    READ = (
        (2, "Belleros", "Belleros goes to the market", False),
        (3, "Cerric", "Cerric stays behind", False),
        (4, "Okthandom", "Should Okthandom leave the party", False),
        (5, "Okthandom", "Okthandom left the party", False),  # (Cerric's player: ruling T)
        (6, "Hilda Brask", "says goodbye to the guards", False),
        (7, "Okthandom", "Okthandom will leave the party", False),
        (8, "Claude", "Claude says goodbye to everyone", True),
        (9, "Belleros", "I say goodbye to the party", True),
    )

    def test_the_key_is_what_the_reader_keeps(self) -> None:
        lines = said()
        names = {k.casefold(): {k} for k in PLAYS}
        asked: list[str] = []
        for n, who, quote, _ in self.READ:
            speaker, text = lines[n - 1]
            if check_quote(quote, text) or not names_who(who, text):
                continue
            got, _dropped = whose(
                [Goodbye(who, (str(n),), (speaker,))],
                names,
                set(PLAYS),
                PLAYS,
                {SPEAKERS["DM"]},
            )
            asked.extend(e for e, _ in got)
        self.assertEqual(sorted(asked), ["Belleros", "Claude"])
        key = counts.load_party_key(KEY)
        self.assertEqual(
            sorted(n.split()[0] for n in key["leave"]),
            sorted(who for _, who, _, ask in self.READ if ask),
        )

    def test_the_key_is_well_formed_and_names_only_the_samples_four(self) -> None:
        key = json.loads(KEY.read_text(encoding="utf-8"))
        lore = (CASES / "party_cases_lore.txt").read_text(encoding="utf-8")
        for name in (*key["leave"], *key["must_not_leave"]):
            self.assertIn(name, lore)
        self.assertFalse(set(key["leave"]) & set(key["must_not_leave"]))


class Lines(unittest.TestCase):
    def test_the_party_line(self) -> None:
        self.assertEqual(
            counts.party_line({"leave": 2, "join": 1}, 0),
            "Party: questions join 1, back 0, leave 2, died 0; stretches closed 0",
        )

    def test_the_key_line_counts_found_missed_false_and_extra(self) -> None:
        key = {"leave": ["Claude Ambere", "Belleros Stormgrace"], "must_not_leave": ["Cerric"]}
        self.assertEqual(
            counts.party_key_line(["claude ambere", "Cerric", "Someone"], key),
            "Party key: goodbye questions 1 of 2 found, 1 missed, 1 false, 1 extra",
        )

    def test_a_key_without_a_leave_list_is_refused(self) -> None:
        bad = CASES / "party_cases_lore.txt"  # (not JSON at all)
        with self.assertRaises(ValueError):
            counts.load_party_key(bad)


class Reading(Party):
    async def test_it_reads_the_questions_and_closed_stretches_and_names_nobody(self) -> None:
        sid = await self.played(9_000, 9_500)
        async with self.db.guild(GUILD) as conn:
            await conn.execute(
                "INSERT INTO memory_join_questions (guild_id, campaign_id, id, entity_id,"
                " created_at, kind, speaker_ids, session_id)"
                " VALUES (%s, %s, %s, %s, 1, %s, %s, %s)",
                (GUILD, self.c, "a" * 32, self.vell, LEAVE, [DM_ID], sid),
            )
        await self.memory.leave_party(GUILD, self.c, self.tuck, "left")
        key = {"leave": ["Vell"], "must_not_leave": ["Tuck"]}
        got = await counts.read_party(self.db, GUILD, self.c, key)
        self.assertEqual(
            got,
            [
                "Party: questions join 0, back 0, leave 1, died 0; stretches closed 1",
                "Party key: goodbye questions 1 of 1 found, 0 missed, 0 false, 0 extra",
            ],
        )
        self.assertNotIn("Vell", "\n".join(got))
        self.assertEqual(len(await counts.read_party(self.db, GUILD, self.c)), 1)
