"""The look-alike names script (#534) says what its scoring key counts."""

import re
import unittest
from pathlib import Path

from dmbot.devtools.replay.names import load_known
from dmbot.memory.lookup import CampaignLookup, LookupData
from dmbot.memory.models import lookup_key
from dmbot.memory.name_list import parse
from dmbot.ui.list_matches import plan

SCRIPTS = Path(__file__).resolve().parents[2] / "docs" / "test-scripts"
SCRIPT = SCRIPTS / "names-stress.md"
NAMES = SCRIPTS / "names-stress-names.txt"
NEW = ("Cedric", "Rothgard", "Ravenmoor", "Zephyra", "Valzaren", "Isolda")
WORDS = ("vain", "hail", "wren", "night", "read", "quill on")


def lines() -> dict[int, str]:
    """The numbered lines of the "## Lines" section (the setup steps are numbered too)."""
    text = SCRIPT.read_text(encoding="utf-8")
    section = text[text.index("## Lines") : text.index("## Names")]
    return {int(n): line for n, line in re.findall(r"^(\d+)\. (.+)$", section, re.M)}


def said(name: str, numbers: range) -> int:
    text = " ".join(lines()[n] for n in numbers)
    return len(re.findall(rf"\b{re.escape(name)}\b", text))


class NamesStressTests(unittest.TestCase):
    def test_the_lines_run_1_to_42(self) -> None:
        self.assertEqual(sorted(lines()), list(range(1, 43)))

    def test_the_campaign_knows_all_but_the_new_names(self) -> None:
        known = load_known(NAMES)
        for name in NEW:
            self.assertNotIn(lookup_key(name), known.keys, name)
        for name in ("Mara", "Marin", "Vane", "Knight", "Cerric", "Ysolde", "Bell"):
            self.assertIn(lookup_key(name), known.keys, name)

    def test_the_scoring_keys_counts(self) -> None:
        pairs_a: tuple[str, ...] = (
            *("Mara", "Marin", "Kael", "Kaelen", "Orrin", "Orrick", "Tamsin", "Tamsa"),
            *("Gorrak", "Gorran", "Ilvaris", "Ilvara", "Saelith", "Saelin", "Nyxara", "Nyxa"),
        )
        self.assertEqual(sum(said(n, range(1, 18)) for n in pairs_a), 33)  # Mara 3 times
        names_b = ("Vane", "Hale", "Wren", "Knight", "Reed", "Quillon")
        self.assertEqual(sum(said(n, range(18, 30)) for n in names_b), 12)
        self.assertEqual(sum(said(w, range(18, 30)) for w in WORDS), 12)
        pairs_c = (*NEW, "Cerric", "Hrothgar", "Dravenmoor", "Zephyrine", "Vhalzimar", "Ysolde")
        self.assertEqual(sum(said(n, range(30, 42)) for n in pairs_c), 24)

    def test_every_new_name_is_said_so_the_scan_can_find_it(self) -> None:
        # The scan can't find a name said once, only at the start of a sentence, or also
        # in small letters.
        text = " ".join(lines().values())
        for name in NEW:
            self.assertEqual(len(re.findall(rf"\b{name}\b", text)), 2, name)
            self.assertTrue(re.search(rf"[a-z,] {name}\b", text), name)
            self.assertIsNone(re.search(rf"\b{name.lower()}\b", text), name)

    def test_add_many_on_a_new_campaign_takes_every_name_and_asks_nothing(self) -> None:
        parsed = parse(NAMES.read_text(encoding="utf-8"), secrets=False)
        self.assertEqual(parsed.refused, [])
        empty = CampaignLookup.build(LookupData(1, (), (), (), ()))
        result = plan(list(parsed.lines), empty, secrets=False)
        self.assertEqual(len(result.new), 38)
        self.assertEqual((result.near, result.kinds, result.look), ([], [], 0))
