"""Warn when .env is missing settings that .env.example has (#31)."""

import os
import tempfile
import unittest
from pathlib import Path

from dmbot import env_check

EXAMPLE = """# Copy to .env and fill in.
DISCORD_TOKEN=
# ---- Transcription ----
TRANSCRIBER=whisper-local
  WHISPER_MODEL = small
# OLD_SETTING=commented out
export SHARD_COUNT=1
DISCORD_TOKEN=
"""


class EnvCheckTests(unittest.TestCase):
    def test_example_names_skip_comments_and_repeats(self) -> None:
        self.assertEqual(
            env_check.example_names(EXAMPLE),
            ["DISCORD_TOKEN", "TRANSCRIBER", "WHISPER_MODEL", "SHARD_COUNT"],
        )

    def test_blank_counts_as_set(self) -> None:
        names = ["DISCORD_TOKEN", "TRANSCRIBER", "WHISPER_MODEL"]
        env = {"DISCORD_TOKEN": "", "WHISPER_MODEL": "small"}
        self.assertEqual(env_check.missing_settings(names, env), ["TRANSCRIBER"])

    def test_warning_names_settings_but_never_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env.example"
            path.write_text(EXAMPLE, encoding="utf-8")
            env = {"DISCORD_TOKEN": "secret-token-123", "SHARD_COUNT": "1"}
            text = env_check.check(env, path)
        assert text is not None
        self.assertIn("TRANSCRIBER, WHISPER_MODEL", text)
        self.assertIn("missing 2 settings", text)
        self.assertIn(".env.example", text)  # tells them what to do next
        self.assertNotIn("secret-token-123", text)
        self.assertNotIn("DISCORD_TOKEN", text)  # it's set, so not listed

    def test_one_setting_is_singular(self) -> None:
        self.assertIn("missing 1 setting that", env_check.warning(["TRANSCRIBER"]))

    def test_nothing_missing_is_quiet(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env.example"
            path.write_text("A=\nB=2\n", encoding="utf-8")
            self.assertIsNone(env_check.check({"A": "", "B": "3"}, path))

    def test_unreadable_example_never_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(env_check.check({}, Path(tmp) / "missing.env.example"))
            # Docker makes a folder when a mounted file is missing on the host.
            self.assertIsNone(env_check.check({}, Path(tmp)))

    def test_finds_the_example_in_a_parent_folder(self) -> None:
        here = Path.cwd()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            (root / ".env.example").write_text("A=\n", encoding="utf-8")
            (root / "core").mkdir()
            os.chdir(root / "core")
            try:
                self.assertEqual(env_check.find_example(), root / ".env.example")
            finally:
                os.chdir(here)

    def test_repo_example_parses(self) -> None:
        root = Path(__file__).resolve().parents[2]
        names = env_check.example_names((root / ".env.example").read_text(encoding="utf-8"))
        self.assertIn("DISCORD_TOKEN", names)
        self.assertIn("TRANSCRIBER", names)
        self.assertTrue(all(name.isupper() for name in names))
