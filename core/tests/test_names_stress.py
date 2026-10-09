"""The look-alike names script (#534) says what its scoring key counts."""

import re
import unittest
from pathlib import Path

from dmbot.devtools.replay.names import clean_lines, load_known
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


class CleanerOnTheScriptTests(unittest.TestCase):
    """What the real Cleaner does to the script's own lines, and to the lines as the
    speech-to-text is likeliest to write them (#573)."""

    def clean(self, *heard: str) -> list[str]:
        return clean_lines(load_known(NAMES), [(i * 5.0, text) for i, text in enumerate(heard)])

    def test_the_script_as_said_is_left_alone(self) -> None:
        said_lines = [lines()[n] for n in range(1, 43)]
        self.assertEqual(self.clean(*said_lines), said_lines)

    def test_isolde_next_to_ysolde_stays_another_person(self) -> None:
        heard = [
            "The healer Ysolde has a student called Isolde.",
            "Today Isolde heals the wounded, and Ysolde rests.",
        ]
        self.assertEqual(self.clean(*heard), heard)  # was: Isolde written as Ysolde

    def test_rothgar_next_to_hrothgar_stays_another_person(self) -> None:
        heard = [
            "The blacksmith Hrothgar has a rival named Rothgar.",  # line 32, Hrothgar first
            "Every market day, Rothgar sells cheaper shields than Hrothgar.",
        ]
        self.assertEqual(self.clean(*heard), heard)  # was: Hrothgar sells... than Hrothgar

    def test_a_lone_rothgar_is_noted_not_silent(self) -> None:
        from dmbot.transcript.cleaner import Vocabulary, clean

        known = load_known(NAMES)
        hrothgar = next(iter(known.lookup.by_key["hrothgar"])).entity_id
        result = clean(
            known.lookup,
            "I think Rothgar sells shields.",
            vocabulary=Vocabulary(),
            scene={hrothgar},  # Hrothgar was said a moment ago, in another line
        )
        self.assertEqual([(f.heard, f.written, f.sure) for f in result.fixes],
                         [("Rothgar", "Hrothgar", False)])  # fmt: skip
        self.assertEqual(result.text, "I think Hrothgar sells shields.")  # noted, with Undo

    def test_a_real_mishearing_is_still_fixed(self) -> None:
        self.assertEqual(
            self.clean("Then Belle Ross casts a spell.", "We meet Kazeth at dawn."),
            ["Then Belleros casts a spell.", "We meet Ka'zeth at dawn."],
        )


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
        # The twin saves them the same way (#574): every name confirmed.
        twin = load_known(NAMES)
        self.assertEqual({e.status for e in twin.lookup.entities.values()}, {"confirmed"})
