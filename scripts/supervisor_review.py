#!/usr/bin/env python3
"""Shows Supervisor's review on a pull request as a `supervisor-review` commit status (#1049).

Run by .github/workflows/supervisor-review.yml. The rule is CLAUDE.md's:

- Supervisor's verdict is a PR review whose body starts "Supervisor review: approved" or
  "Supervisor review: changes needed". The latest one counts.
- "approved" counts only for the commit it was given on. If the PR's head has moved only
  because `development` was merged in (the PR's own change is the same, by patch id), the
  approval carries over. Otherwise the check waits for a new review.
- "changes needed" is a failure until a newer review approves.
- A PR that changes only the testing logs needs no review (log-only, CLAUDE.md).
- A PR written by Supervisor itself ("Session: Supervisor." in its body) is never reviewed by
  Supervisor. Outside the docs-only rule it is approved by the owner's label `approved: owner`:
  the status is green only while that label is on the PR, the newest time it was put on was by
  the owner's account (not taken off since), and that was after the head arrived: the earliest
  status on the head commit, or this run if there is none yet (a commit's own date is set by
  whoever made it, so it is only a floor). A push after the label keeps the status pending until
  the label is added again (#1387). A "changes needed" review still wins over the label. Any
  other PR with that label is judged by Supervisor's review alone. The owner's account is the
  sessions' too (one GitHub account), so this guards against mistakes, not against a session.

Who may give the verdict: only the repository owner's account. Anyone can post a review on a
public repository, so a body alone proves nothing. Standard library only; the token is the
default GITHUB_TOKEN and is never printed. Nothing from the pull request is run or put in a
shell: it is read through the API and compared as text.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

CONTEXT = "supervisor-review"
API = "https://api.github.com"
LOG_ONLY = frozenset({"docs/testing-status.log", "docs/testing-history.log"})
OWNER_LABEL = "approved: owner"
SUPERVISOR_PR = re.compile(r"^\s*Session:\s*Supervisor\b", re.IGNORECASE | re.MULTILINE)
PREFIX = re.compile(r"^\s*supervisor review:\s*(approved|changes needed)\b", re.IGNORECASE)
DESCRIPTION_MAX = 140  # GitHub's limit for a status description
COMPARE_FILE_CAP = 300  # GitHub's compare lists at most this many files

SUCCESS, FAILURE, PENDING = "success", "failure", "pending"
Verdict = tuple[str, str]  # (state, description)


def clip(text: str) -> str:
    return text if len(text) <= DESCRIPTION_MAX else text[: DESCRIPTION_MAX - 1] + "…"


def verdict_of(review: Mapping[str, Any]) -> str | None:
    """ "approved", "changes needed", or None if this isn't a Supervisor verdict."""
    match = PREFIX.match(review.get("body") or "")
    return match.group(1).lower() if match else None


def latest_verdict(
    reviews: Iterable[Mapping[str, Any]], allowed: Iterable[str]
) -> Mapping[str, Any] | None:
    """The newest Supervisor review by someone who may give one (dismissed ones don't count)."""
    names = {a.casefold() for a in allowed}
    found: Mapping[str, Any] | None = None
    for review in reviews:
        user = (review.get("user") or {}).get("login", "")
        if review.get("state") in ("DISMISSED", "PENDING") or user.casefold() not in names:
            continue
        if verdict_of(review) is None:
            continue
        if found is None or _order(review) >= _order(found):
            found = review
    return found


def _order(review: Mapping[str, Any]) -> tuple[str, int]:
    return (str(review.get("submitted_at") or ""), int(review.get("id") or 0))


def patch_id(files: Sequence[Mapping[str, Any]]) -> str | None:
    """A fingerprint of what a pull request changes, ignoring where in the file it sits: the
    names of the files (and a renamed file's old name) and each added or removed line
    (whitespace squeezed), not the context around them or the line numbers. A merge of
    `development` into the branch leaves it as it was. None when it can't be told: GitHub
    left a patch out (a binary file, a mode change, a file too big to compare) or listed too
    many files to be the whole list. A missing newline at the end of a file is not seen."""
    if len(files) >= COMPARE_FILE_CAP:
        return None
    digest = hashlib.sha256()
    for item in sorted(files, key=lambda f: str(f.get("filename"))):
        patch = item.get("patch")
        if patch is None and item.get("status") != "renamed":
            return None
        names = f"{item.get('filename')}\0{item.get('status')}\0{item.get('previous_filename')}"
        digest.update(names.encode() + b"\0")
        for line in (patch or "").splitlines():
            if line[:1] in ("+", "-"):  # the patch has no file headers: a "--" line is a change
                digest.update(" ".join(line.split()).encode() + b"\n")
    return digest.hexdigest()


def is_supervisors(body: str | None) -> bool:
    """The PR says Supervisor wrote it (its "Session: Supervisor." line)."""
    return bool(SUPERVISOR_PR.search(body or ""))


def _when(text: object) -> datetime | None:
    """An ISO time from GitHub as an aware time, or None if it can't be read (then nothing is
    approved by it)."""
    try:
        value = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return None
    return value if value.tzinfo is not None else None


def owner_approved(
    labels: Iterable[str],
    events: Iterable[Mapping[str, Any]],
    head_arrived: datetime,
    allowed: Iterable[str],
) -> bool:
    """The owner's label is on the PR now, and its newest `labeled` event (from the first event
    to the last, an `unlabeled` clearing it) was made by the owner's account after the head
    arrived. A label put back by anyone else does not count, whoever put it on before."""
    if OWNER_LABEL not in set(labels):
        return False
    names = {a.casefold() for a in allowed}
    current: tuple[str, datetime | None] | None = None
    for e in events:
        if (e.get("label") or {}).get("name") != OWNER_LABEL:
            continue
        if e.get("event") == "labeled":
            who = ((e.get("actor") or {}).get("login") or "").casefold()
            current = (who, _when(e.get("created_at")))
        elif e.get("event") == "unlabeled":
            current = None
    if current is None or current[0] not in names or current[1] is None:
        return False
    return current[1] > head_arrived


def decide(
    *,
    head: str,
    changed_files: Sequence[str],
    review: Mapping[str, Any] | None,
    same_change_as_reviewed: Callable[[str], bool],
    owner_approved_supervisor_pr: bool = False,
) -> Verdict:
    """The check for one pull request. `same_change_as_reviewed(sha)`: whether the PR's own
    change at `head` is the same as at `sha` (asked only when the approval is for an older
    commit)."""
    if changed_files and all(name in LOG_ONLY for name in changed_files):
        return SUCCESS, "Log-only, no review needed (CLAUDE.md)"
    if owner_approved_supervisor_pr and (review is None or verdict_of(review) != "changes needed"):
        return SUCCESS, "Approved by the owner (a Supervisor PR)"
    if review is None:
        return PENDING, "Waiting for Supervisor's review"
    said = verdict_of(review)
    if said == "changes needed":
        return FAILURE, "Supervisor asked for changes"
    if said != "approved":  # not reachable through latest_verdict; kept for safety
        return PENDING, "Waiting for Supervisor's review"
    reviewed = str(review.get("commit_id") or "")
    if reviewed == head:
        return SUCCESS, "Supervisor approved this commit"
    if reviewed and same_change_as_reviewed(reviewed):
        return SUCCESS, clip(f"Approval carried over from {reviewed[:7]}: same change, refreshed")
    return PENDING, clip(f"Approved {reviewed[:7] or 'an older commit'}; waiting for a new review")


# ---- GitHub ---------------------------------------------------------------------------


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """GitHub's API answers directly; a redirect would carry the token somewhere else."""

    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


class GitHub:
    def __init__(self, token: str, repo: str) -> None:
        self._token, self._repo = token, repo
        self._opener = urllib.request.build_opener(_NoRedirect)

    def _request(self, method: str, path: str, body: Mapping[str, Any] | None = None) -> Any:
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(
            API + path,
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {self._token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "Content-Type": "application/json",
            },
        )
        with self._opener.open(request, timeout=30) as response:
            raw = response.read()
        return json.loads(raw) if raw else None

    def pages(self, path: str) -> list[Any]:
        out: list[Any] = []
        page = 1
        while True:
            sep = "&" if "?" in path else "?"
            items = self._request("GET", f"/repos/{self._repo}{path}{sep}per_page=100&page={page}")
            out.extend(items)
            if len(items) < 100:
                return out
            page += 1

    def get(self, path: str) -> Any:
        return self._request("GET", f"/repos/{self._repo}{path}")

    def set_status(self, sha: str, state: str, description: str, url: str | None) -> None:
        body: dict[str, Any] = {
            "state": state,
            "context": CONTEXT,
            "description": clip(description),
        }
        if url:
            body["target_url"] = url
        self._request("POST", f"/repos/{self._repo}/statuses/{sha}", body)


def now() -> datetime:
    return datetime.now(UTC)


def head_arrived(github: GitHub, head: str, moment: datetime) -> datetime | None:
    """When the head commit reached the PR, by a time its pusher can't choose: the earliest
    status on it, or `moment` (this run) when it has none yet. Never earlier than the commit's
    own date. None if a time can't be read (nothing is approved then)."""
    statuses = github.pages(f"/commits/{head}/statuses")
    times = [t for t in (_when((s or {}).get("created_at")) for s in statuses) if t is not None]
    made = _when(github.get(f"/commits/{head}")["commit"]["committer"]["date"])
    if made is None:
        return None
    return max(min(times) if times else moment, made)


def run(github: GitHub, number: int, allowed: Iterable[str]) -> Verdict:
    """Work out the check for a pull request and post it on its head commit."""
    pull = github.get(f"/pulls/{number}")
    head = pull["head"]["sha"]
    tip = urllib.parse.quote(pull["base"]["ref"], safe="")  # the live branch, not an old sha
    names = [f["filename"] for f in github.pages(f"/pulls/{number}/files")]
    review = latest_verdict(github.pages(f"/pulls/{number}/reviews"), allowed)
    approved = False
    if is_supervisors(pull.get("body")):  # only then are the label and its history read
        labels = [(item or {}).get("name", "") for item in pull.get("labels") or []]
        if OWNER_LABEL in labels:
            events = github.pages(f"/issues/{number}/events")
            arrived = head_arrived(github, head, now())
            approved = arrived is not None and owner_approved(labels, events, arrived, allowed)

    def same_change(sha: str) -> bool:
        try:
            old = patch_id(github.get(f"/compare/{tip}...{sha}").get("files", []))
            new = patch_id(github.get(f"/compare/{tip}...{head}").get("files", []))
        except OSError:  # the old commit is gone (a rebase), or GitHub failed: not the same
            return False
        return old is not None and old == new

    state, description = decide(
        head=head,
        changed_files=names,
        review=review,
        same_change_as_reviewed=same_change,
        owner_approved_supervisor_pr=approved,
    )
    github.set_status(head, state, description, review.get("html_url") if review else None)
    return state, description


def main() -> int:
    token = os.environ.get("GITHUB_TOKEN", "")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    number = os.environ.get("PR_NUMBER", "")
    owner = os.environ.get("REVIEWER_LOGINS") or repo.split("/")[0]
    if not (token and repo and number.isdigit()):
        print(
            "supervisor-review: needs GITHUB_TOKEN, GITHUB_REPOSITORY and PR_NUMBER",
            file=sys.stderr,
        )
        return 2
    github = GitHub(token, repo)
    try:
        state, description = run(github, int(number), owner.split(","))
    except Exception as exc:  # whatever went wrong, an old green must not stand
        print(f"supervisor-review: could not check ({type(exc).__name__})", file=sys.stderr)
        try:
            head = github.get(f"/pulls/{int(number)}")["head"]["sha"]
            github.set_status(
                head, PENDING, "Could not check Supervisor's review; will retry", None
            )
        except Exception as again:
            print(
                f"supervisor-review: and could not say so ({type(again).__name__})", file=sys.stderr
            )
        return 1
    print(f"supervisor-review: {state}: {description}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
