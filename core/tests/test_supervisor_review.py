"""Tests for scripts/supervisor_review.py (#1049). No network: a fake GitHub stands in.

python3 scripts/tests/test_supervisor_review.py
"""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "supervisor_review.py"
spec = importlib.util.spec_from_file_location("supervisor_review", SCRIPT)
assert spec and spec.loader
sr = importlib.util.module_from_spec(spec)
sys.modules["supervisor_review"] = sr
spec.loader.exec_module(sr)

HEAD, OLD, BASE = "h" * 40, "o" * 40, "b" * 40


def review(
    body: str,
    commit: str = HEAD,
    at: str = "2026-10-10T10:00:00Z",
    user: str = "smv620",
    state: str = "COMMENTED",
    id_: int = 1,
) -> dict[str, Any]:
    return {
        "id": id_,
        "body": body,
        "commit_id": commit,
        "submitted_at": at,
        "user": {"login": user},
        "state": state,
        "html_url": f"https://example.test/review/{id_}",
    }


def decide(
    reviews: list[dict[str, Any]],
    files: tuple[str, ...] = ("core/src/x.py",),
    same: bool = False,
) -> tuple[str, str]:
    verdict: tuple[str, str] = sr.decide(
        head=HEAD,
        changed_files=files,
        review=sr.latest_verdict(reviews, ["smv620"]),
        same_change_as_reviewed=lambda sha: same,
    )
    return verdict


class Verdicts(unittest.TestCase):
    def test_approved_on_the_head_is_a_success(self) -> None:
        state, text = decide([review("Supervisor review: approved. Session: Supervisor.")])
        self.assertEqual((state, text), ("success", "Supervisor approved this commit"))

    def test_changes_needed_is_a_failure(self) -> None:
        state, text = decide([review("Supervisor review: changes needed\n1. Fix it")])
        self.assertEqual((state, text), ("failure", "Supervisor asked for changes"))

    def test_no_review_waits(self) -> None:
        self.assertEqual(decide([]), ("pending", "Waiting for Supervisor's review"))
        self.assertEqual(decide([review("looks fine to me")])[0], "pending")

    def test_the_latest_review_wins(self) -> None:
        sent_back = review("Supervisor review: changes needed", at="2026-10-10T10:00:00Z", id_=1)
        approved = review("Supervisor review: approved", at="2026-10-10T11:00:00Z", id_=2)
        self.assertEqual(decide([sent_back, approved])[0], "success")
        self.assertEqual(decide([approved, sent_back])[0], "success")  # order given is irrelevant
        later_no = review("Supervisor review: changes needed", at="2026-10-10T12:00:00Z", id_=3)
        self.assertEqual(decide([sent_back, approved, later_no])[0], "failure")

    def test_an_approval_for_an_older_commit_waits_unless_only_a_refresh(self) -> None:
        old = review("Supervisor review: approved", commit=OLD)
        state, text = decide([old], same=False)
        self.assertEqual(state, "pending")
        self.assertIn("ooooooo", text)
        state, text = decide([old], same=True)
        self.assertEqual(state, "success")
        self.assertIn("carried over from ooooooo", text)

    def test_changes_needed_stays_red_on_a_new_commit_until_a_new_approval(self) -> None:
        no = review("Supervisor review: changes needed", commit=OLD)
        self.assertEqual(decide([no], same=True)[0], "failure")

    def test_the_words_are_matched_loosely_but_must_start_the_review(self) -> None:
        for body in (
            "supervisor review: APPROVED",
            "  Supervisor review:approved.",
            "Supervisor Review: Approved",
        ):
            self.assertEqual(decide([review(body)])[0], "success", body)
        for body in (
            "I think Supervisor review: approved is wrong",
            "Supervisor review: approvedish",  # not the word
            "Supervisor review - approved",
            "Supervisor review: not approved",
        ):
            self.assertEqual(decide([review(body)])[0], "pending", body)

    def test_only_the_owners_account_can_give_the_verdict(self) -> None:
        stranger = review("Supervisor review: approved", user="someone-else")
        self.assertEqual(decide([stranger])[0], "pending")
        mixed = [
            review("Supervisor review: changes needed", id_=1, at="2026-10-10T10:00:00Z"),
            review(
                "Supervisor review: approved", user="someone-else", id_=2, at="2026-10-10T12:00:00Z"
            ),
        ]
        self.assertEqual(decide(mixed)[0], "failure")  # the stranger's newer review is ignored
        self.assertEqual(
            decide([review("Supervisor review: approved", user="SMV620")])[0], "success"
        )

    def test_a_dismissed_review_does_not_count(self) -> None:
        gone = review("Supervisor review: approved", state="DISMISSED")
        self.assertEqual(decide([gone])[0], "pending")
        sent_back_then_dismissed = [
            review("Supervisor review: approved", id_=1, at="2026-10-10T10:00:00Z"),
            review(
                "Supervisor review: changes needed",
                id_=2,
                at="2026-10-10T11:00:00Z",
                state="DISMISSED",
            ),
        ]
        self.assertEqual(decide(sent_back_then_dismissed)[0], "success")

    def test_log_only_pull_requests_need_no_review(self) -> None:
        for files in (
            ("docs/testing-status.log",),
            ("docs/testing-history.log",),
            ("docs/testing-status.log", "docs/testing-history.log"),
        ):
            state, text = decide([], files)
            self.assertEqual(state, "success", files)
            self.assertIn("Log-only", text)
        # one other file, or none at all, is an ordinary pull request
        self.assertEqual(decide([], ("docs/testing-status.log", "docs/PLAN.md"))[0], "pending")
        self.assertEqual(decide([], ())[0], "pending")

    def test_a_log_only_change_is_not_red_after_changes_needed(self) -> None:
        no = review("Supervisor review: changes needed")
        self.assertEqual(decide([no], ("docs/testing-status.log",))[0], "success")

    def test_descriptions_fit_githubs_limit(self) -> None:
        self.assertLessEqual(len(sr.clip("x" * 500)), 140)
        for reviews in ([], [review("Supervisor review: approved", commit=OLD)]):
            self.assertLessEqual(len(decide(reviews)[1]), 140)


def patch(*lines: str) -> str:
    return "@@ -1,3 +1,4 @@\n" + "\n".join(lines)


def changes(*items: tuple[str, str | None]) -> list[dict[str, Any]]:
    return [
        {"filename": name, "status": "modified", "patch": body, "changes": 1}
        for name, body in items
    ]


class PatchId(unittest.TestCase):
    def test_a_refresh_changes_only_the_surroundings(self) -> None:
        before = changes(("a.py", patch(" context one", "-old = 1", "+new = 1", " context two")))
        after = changes(
            (
                "a.py",
                "@@ -40,3 +52,4 @@ def other():\n"
                + "\n".join([" different context", "-old = 1", "+new = 1", " more"]),
            )
        )
        self.assertEqual(sr.patch_id(before), sr.patch_id(after))

    def test_a_real_change_differs(self) -> None:
        a = changes(("a.py", patch("-old = 1", "+new = 1")))
        for other in (
            changes(("a.py", patch("-old = 1", "+new = 2"))),
            changes(("a.py", patch("-old = 1", "+new = 1", "+extra = 3"))),
            changes(("b.py", patch("-old = 1", "+new = 1"))),
            changes(("a.py", patch("-old = 1", "+new = 1")), ("c.py", patch("+z"))),
        ):
            self.assertNotEqual(sr.patch_id(a), sr.patch_id(other))

    def test_the_order_of_files_and_the_spacing_dont_matter(self) -> None:
        one = changes(("a.py", patch("+x  =   1")), ("b.py", patch("+y = 2")))
        two = changes(("b.py", patch("+y = 2")), ("a.py", patch("+x = 1")))
        self.assertEqual(sr.patch_id(one), sr.patch_id(two))

    def test_a_missing_patch_cannot_be_compared(self) -> None:
        self.assertIsNone(sr.patch_id(changes(("big.bin", None))))
        # an empty new file has no patch either: waiting for a review is the safe answer
        self.assertIsNone(sr.patch_id([{"filename": "empty", "status": "added", "changes": 0}]))

    def test_lines_that_look_like_file_headers_are_still_changes(self) -> None:
        # The API's patch has no file headers, so a "-- comment" or a "---" line is real.
        a = changes(("q.sql", patch("-- old comment", "+select 1")))
        b = changes(("q.sql", patch("-- different comment", "+select 1")))
        self.assertNotEqual(sr.patch_id(a), sr.patch_id(b))
        yaml_a = changes(("x.yml", patch("---", "+a: 1")))
        yaml_b = changes(("x.yml", patch("+a: 1")))
        self.assertNotEqual(sr.patch_id(yaml_a), sr.patch_id(yaml_b))

    def test_blank_lines_in_the_patch_are_context_not_changes(self) -> None:
        a = changes(("a.py", patch("", "+x")))
        b = changes(("a.py", patch("+x")))
        self.assertEqual(sr.patch_id(a), sr.patch_id(b))

    def test_binary_and_mode_only_changes_cannot_be_compared(self) -> None:
        binary = [{"filename": "logo.png", "status": "modified", "changes": 0}]
        mode = [{"filename": "run.sh", "status": "modified", "changes": 0}]
        self.assertIsNone(sr.patch_id(binary))
        self.assertIsNone(sr.patch_id(mode))

    def test_a_pure_rename_counts_and_remembers_where_it_came_from(self) -> None:
        def moved(old: str) -> list[dict[str, Any]]:
            return [
                {"filename": "new.py", "status": "renamed", "previous_filename": old, "changes": 0}
            ]

        self.assertEqual(sr.patch_id(moved("a.py")), sr.patch_id(moved("a.py")))
        self.assertNotEqual(sr.patch_id(moved("a.py")), sr.patch_id(moved("b.py")))

    def test_a_full_file_list_may_be_cut_short_so_it_is_not_trusted(self) -> None:
        many = changes(*((f"f{i}.py", patch("+x")) for i in range(sr.COMPARE_FILE_CAP)))
        self.assertIsNone(sr.patch_id(many))
        self.assertIsNotNone(sr.patch_id(many[:-1]))


class FakeGitHub:
    """What `run` asks of GitHub, answered from a script; records the status it posts."""

    def __init__(
        self,
        reviews: list[dict[str, Any]],
        files: list[str],
        diffs: dict[str, list[dict[str, Any]]],
    ) -> None:
        self.reviews, self.files, self.diffs = reviews, files, diffs
        self.posted: list[tuple[str, str, str, str | None]] = []
        self.asked: list[str] = []
        self.fail_on: str | None = None

    def get(self, path: str) -> Any:
        self.asked.append(path)
        if self.fail_on and self.fail_on in path:
            raise OSError("GitHub is down")
        if path.startswith("/pulls/"):
            return {"head": {"sha": HEAD}, "base": {"sha": BASE, "ref": "development"}}
        sha = path.split("...")[1]
        if sha not in self.diffs:
            raise OSError("no such commit")  # a force-pushed-away commit
        return {"files": self.diffs[sha]}

    def pages(self, path: str) -> list[Any]:
        if self.fail_on and self.fail_on in path:
            raise OSError("GitHub is down")
        if path.endswith("/files"):
            return [{"filename": f} for f in self.files]
        return self.reviews

    def set_status(self, sha: str, state: str, description: str, url: str | None) -> None:
        self.posted.append((sha, state, description, url))


class Run(unittest.TestCase):
    def test_it_posts_the_check_on_the_head_with_a_link_to_the_review(self) -> None:
        fake = FakeGitHub([review("Supervisor review: approved")], ["a.py"], {})
        state, _ = sr.run(fake, 7, ["smv620"])
        self.assertEqual(state, "success")
        ((sha, posted, _, url),) = fake.posted
        self.assertEqual((sha, posted), (HEAD, "success"))
        self.assertEqual(url, "https://example.test/review/1")

    def test_a_refresh_is_compared_against_the_reviewed_commit(self) -> None:
        same = changes(("a.py", patch("-a", "+b")))
        fake = FakeGitHub(
            [review("Supervisor review: approved", commit=OLD)], ["a.py"], {OLD: same, HEAD: same}
        )
        self.assertEqual(sr.run(fake, 7, ["smv620"])[0], "success")
        moved = FakeGitHub(
            [review("Supervisor review: approved", commit=OLD)],
            ["a.py"],
            {OLD: same, HEAD: changes(("a.py", patch("-a", "+c")))},
        )
        self.assertEqual(sr.run(moved, 7, ["smv620"])[0], "pending")

    def test_the_comparison_is_against_the_live_branch_not_an_old_sha(self) -> None:
        same = changes(("a.py", patch("-a", "+b")))
        fake = FakeGitHub(
            [review("Supervisor review: approved", commit=OLD)], ["a.py"], {OLD: same, HEAD: same}
        )
        sr.run(fake, 7, ["smv620"])
        compares = [p for p in fake.asked if p.startswith("/compare/")]
        self.assertEqual(len(compares), 2)
        self.assertTrue(all(p.startswith("/compare/development...") for p in compares))
        self.assertFalse(any(BASE in p for p in compares))

    def test_a_reviewed_commit_that_is_gone_means_wait_not_a_crash(self) -> None:
        fake = FakeGitHub(
            [review("Supervisor review: approved", commit=OLD)],
            ["a.py"],
            {HEAD: changes(("a.py", patch("+x")))},  # OLD was force-pushed away
        )
        state, text = sr.run(fake, 7, ["smv620"])
        self.assertEqual(state, "pending")
        self.assertIn("waiting for a new review", text)

    def test_a_pending_review_is_not_a_verdict(self) -> None:
        draft = review("Supervisor review: approved", state="PENDING")
        self.assertEqual(decide([draft])[0], "pending")

    def test_a_log_only_pull_request_never_asks_for_a_comparison(self) -> None:
        fake = FakeGitHub([], ["docs/testing-status.log"], {})
        self.assertEqual(sr.run(fake, 7, ["smv620"])[0], "success")


class Main(unittest.TestCase):
    """A failure must never leave an earlier green standing."""

    def run_main(self, fake: FakeGitHub) -> int:
        from unittest.mock import patch as patch_

        env = {
            "GITHUB_TOKEN": "t",
            "GITHUB_REPOSITORY": "smv620/Discord-DMbot",
            "PR_NUMBER": "7",
        }
        with (
            patch_.dict("os.environ", env, clear=True),
            patch_.object(sr, "GitHub", lambda token, repo: fake),
        ):
            return int(sr.main())

    def test_a_good_run_posts_and_exits_clean(self) -> None:
        fake = FakeGitHub([review("Supervisor review: approved")], ["a.py"], {})
        self.assertEqual(self.run_main(fake), 0)
        self.assertEqual(fake.posted[-1][1], "success")

    def test_when_github_fails_midway_the_head_is_set_back_to_pending(self) -> None:
        fake = FakeGitHub([review("Supervisor review: approved")], ["a.py"], {})
        fake.fail_on = "/reviews"
        self.assertEqual(self.run_main(fake), 1)
        ((sha, state, text, _),) = fake.posted
        self.assertEqual((sha, state), (HEAD, "pending"))
        self.assertIn("will retry", text)

    def test_missing_settings_are_said_plainly(self) -> None:
        from unittest.mock import patch as patch_

        with patch_.dict("os.environ", {}, clear=True):
            self.assertEqual(sr.main(), 2)

    def test_redirects_are_refused(self) -> None:
        self.assertIsNone(sr._NoRedirect().redirect_request(object(), None, 301, "", {}, "x"))


class Workflow(unittest.TestCase):
    """The workflow file keeps to the issue's limits."""

    text = (REPO / "docs" / "ci" / "supervisor-review.yml").read_text(encoding="utf-8")

    def test_the_job_is_not_named_like_the_check(self) -> None:
        # A required check called supervisor-review must only ever be the commit status, not
        # this job (which goes green whatever the verdict is).
        self.assertNotIn(f"name: {sr.CONTEXT}\n", self.text)
        self.assertIn("name: post-supervisor-status", self.text)

    def test_the_script_runs_isolated_and_the_checkouts_keep_no_credentials(self) -> None:
        self.assertEqual(self.text.count("python3 -I "), 2)
        self.assertNotIn("python3 scripts", self.text)
        self.assertEqual(self.text.count("persist-credentials: false"), 2)

    def test_triggers_and_permissions(self) -> None:
        for needed in (
            "pull_request_review:",
            "types: [submitted, edited, dismissed]",
            "pull_request:",
            "types: [opened, synchronize, reopened]",
            "branches: [development]",
            "statuses: write",
            "pull-requests: read",
        ):
            self.assertIn(needed, self.text)
        self.assertNotIn("write-all", self.text)
        self.assertEqual(self.text.count(": write"), 1)

    def test_nothing_from_the_pull_request_goes_into_a_shell(self) -> None:
        for line in self.text.splitlines():
            if line.strip().startswith("run:"):
                self.assertNotIn("${{", line)
        for block in self.text.split("run: |")[1:]:
            self.assertNotIn("${{", block)
        self.assertNotIn("pull_request.title", self.text)
        self.assertNotIn("pull_request.body", self.text)
        self.assertNotIn("head_ref", self.text)


if __name__ == "__main__":
    unittest.main()
