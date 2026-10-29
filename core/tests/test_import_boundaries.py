"""The feature-boundary check (#2130): a new forbidden import is what makes it fail, shown on a
throwaway package so the proof does not depend on the real code. The repo's own contracts are
run by the core CI job (`lint-imports`), not repeated here in every shard."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

RUN = "from importlinter.cli import lint_imports; raise SystemExit(lint_imports())"

CONFIG = """[importlinter]
root_package = toy

[importlinter:contract:nobot]
name = Features never import the bot
type = forbidden
source_modules = toy.feature
forbidden_modules = toy.bot
allow_indirect_imports = True
"""


def lint(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", RUN, *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )


class ImportBoundaries(unittest.TestCase):
    def test_a_new_forbidden_import_fails_the_check(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "toy" / "feature").mkdir(parents=True)
            (root / "toy" / "__init__.py").write_text("")
            (root / "toy" / "bot.py").write_text("")
            (root / "toy" / "feature" / "__init__.py").write_text("")
            (root / "toy" / "feature" / "a.py").write_text("")
            (root / ".importlinter").write_text(CONFIG)
            self.assertEqual(lint(root).returncode, 0)  # clean first
            (root / "toy" / "feature" / "a.py").write_text("from toy import bot\n")
            broken = lint(root)
        self.assertNotEqual(broken.returncode, 0)
        self.assertIn("toy.feature.a -> toy.bot", broken.stdout)
