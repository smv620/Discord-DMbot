"""dev1's after-deploy check (#2625, scripts/check-public-paths): its list of public paths is the
examples in public_paths.py, and it says NOT OK for a public path that gets the empty 404 and for
a closed path that gets anything else. A stand-in curl replaces the network."""

from __future__ import annotations

import os
import re
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

from dmbot.web import public_paths as pp

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check-public-paths"


def listed() -> list[str]:
    text = SCRIPT.read_text(encoding="utf-8")
    block = text.split("# PUBLIC-PATHS", 1)[1].split("# END-PUBLIC-PATHS", 1)[0]
    return re.findall(r"^\s+(/\S*)$", block, re.M)


class TheCheck(unittest.TestCase):
    def test_its_list_is_the_examples_in_the_apps_list(self) -> None:
        wanted = sorted(e for p in pp.PUBLIC for e in p.examples)
        self.assertEqual(sorted(listed()), wanted)

    def run_with(self, answers: dict[str, tuple[int, int]]) -> subprocess.CompletedProcess[str]:
        """A stand-in curl answering (status, body size) by the path asked for."""
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp) / "curl"
            table = "\n".join(f'  "{p}") code={c}; size={s};;' for p, (c, s) in answers.items())
            fake.write_text(
                "#!/usr/bin/env bash\n"
                'out=""; url=""\n'
                'while [ $# -gt 0 ]; do case "$1" in -o) out=$2; shift;; http*) url=$1;; esac; '
                "shift; done\n"
                "path=${url#https://api.example.test}; code=404; size=0\n"
                f'case "$path" in\n{table}\nesac\n'
                'head -c "$size" /dev/zero | tr "\\0" x > "$out"\n'
                'printf "%s" "$code"\n'
            )
            fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
            return subprocess.run(
                [str(SCRIPT), "https://api.example.test"],
                capture_output=True,
                text=True,
                env={**os.environ, "DMBOT_CURL": str(fake)},
            )

    def test_all_good_exits_zero(self) -> None:
        answers = {p: (401, 20) for p in listed()}
        got = self.run_with(answers)
        self.assertEqual(got.returncode, 0, got.stdout)
        self.assertIn("stays closed", got.stdout)

    def test_a_public_path_closed_by_cloudflare_is_not_ok(self) -> None:
        answers = {p: (401, 20) for p in listed() if p != "/recap-audio/ref/voice"}
        got = self.run_with(answers)
        self.assertEqual(got.returncode, 1)
        self.assertIn("NOT OK  /recap-audio/ref/voice", got.stdout)

    def test_a_closed_path_that_answers_is_not_ok(self) -> None:
        answers = {p: (401, 20) for p in listed()}
        answers["/health"] = (200, 15)
        got = self.run_with(answers)
        self.assertEqual(got.returncode, 1)
        self.assertIn("NOT OK  /health", got.stdout)
