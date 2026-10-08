"""The website's "Say hello" forms (#665): feedback and questions, posted as GitHub
Discussions in DMbot's repository.

Privacy (#665): the public post holds the message and the date, nothing else. How to
reach the sender is stored only in the `feedback` table, which the bot and the website
can add to but never read. The repository is public, so nothing from the live database
goes into a post, and logs carry the discussion number only.

Abuse (#665): one post per address per 10 minutes, 2,000 characters at most, and a
Cloudflare Turnstile check when its key is set.
"""

from __future__ import annotations

import datetime
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal, Protocol

import aiohttp

from dmbot.db import Database

Kind = Literal["feedback", "question"]
KINDS: tuple[Kind, ...] = ("feedback", "question")
# The Discussion categories, made by hand in the repository's settings: GitHub's API
# can't create categories.
CATEGORIES: dict[Kind, str] = {"feedback": "Feedback", "question": "Questions"}
TITLES: dict[Kind, str] = {
    "feedback": "Feedback from the website",
    "question": "A question from the website",
}
MAX_MESSAGE = 2000
MAX_CONTACT = 200
WAIT_SECONDS = 10 * 60
GITHUB_GRAPHQL = "https://api.github.com/graphql"
TURNSTILE_VERIFY = "https://challenges.cloudflare.com/turnstile/v0/siteverify"


class FeedbackError(RuntimeError):
    """GitHub or Turnstile refused or didn't answer. The message holds no user text."""


def discussion_body(message: str, day: datetime.date) -> str:
    """The public post: the date, then the message exactly as typed, inside a code block.
    The block stops Markdown from running: no @mentions pinging people, no links or
    images loading, no #numbers linking issues. Its fence is longer than any run of
    backticks in the message, so the message can't close it early."""
    longest = max((len(run) for run in re.findall(r"`+", message)), default=0)
    fence = "`" * max(3, longest + 1)
    return f"Sent from the website on {day.isoformat()}.\n\n{fence}text\n{message}\n{fence}\n"


class Discussions(Protocol):
    async def create(self, kind: Kind, title: str, body: str) -> int:
        """Post a new discussion in the kind's category; returns its number."""
        ...


class HumanCheck(Protocol):
    async def verify(self, token: str, address: str) -> bool:
        """Whether Turnstile says this form was sent by a person."""
        ...


class GitHubDiscussions:
    """GitHub's GraphQL API (Discussions have no REST endpoint to create one). The token
    needs only the Discussions write permission on this one repository."""

    def __init__(self, token: str, repo: str, *, url: str = GITHUB_GRAPHQL) -> None:
        self._auth = {"Authorization": f"Bearer {token}"}
        self._owner, _, self._name = repo.partition("/")
        self._url = url
        self._http: aiohttp.ClientSession | None = None
        # The repository's and categories' ids, looked up once.
        self._ids: tuple[str, dict[str, str]] | None = None

    async def close(self) -> None:
        if self._http is not None:
            await self._http.close()
            self._http = None

    async def _query(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        if self._http is None:
            self._http = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10))
        try:
            async with self._http.post(
                self._url, json={"query": query, "variables": variables}, headers=self._auth
            ) as response:
                if response.status >= 400:
                    raise FeedbackError(f"GitHub answered {response.status}")
                raw = await response.json()
        except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
            raise FeedbackError(f"Couldn't reach GitHub ({type(exc).__name__})") from exc
        # GraphQL reports errors with a 200; their text can echo the input, so it's left out.
        if not isinstance(raw, dict) or raw.get("errors") or not isinstance(raw.get("data"), dict):
            raise FeedbackError("GitHub refused the request")
        data: dict[str, Any] = raw["data"]
        return data

    async def _lookup(self) -> tuple[str, dict[str, str]]:
        if self._ids is None:
            data = await self._query(
                "query($owner: String!, $name: String!) { repository(owner: $owner,"
                " name: $name) { id discussionCategories(first: 25) { nodes { id name } } } }",
                {"owner": self._owner, "name": self._name},
            )
            repo = data.get("repository") or {}
            nodes = (repo.get("discussionCategories") or {}).get("nodes") or []
            self._ids = (str(repo.get("id", "")), {str(n["name"]): str(n["id"]) for n in nodes})
        return self._ids

    async def create(self, kind: Kind, title: str, body: str) -> int:
        repo_id, categories = await self._lookup()
        category = categories.get(CATEGORIES[kind])
        if not repo_id or category is None:
            self._ids = None  # look again next time, once the category is made
            raise FeedbackError(f"The repository has no '{CATEGORIES[kind]}' discussion category")
        data = await self._query(
            "mutation($repo: ID!, $category: ID!, $title: String!, $body: String!) {"
            " createDiscussion(input: {repositoryId: $repo, categoryId: $category,"
            " title: $title, body: $body}) { discussion { number } } }",
            {"repo": repo_id, "category": category, "title": title, "body": body},
        )
        try:
            return int(data["createDiscussion"]["discussion"]["number"])
        except (KeyError, TypeError, ValueError) as exc:
            raise FeedbackError("GitHub's answer had no discussion number") from exc


class Turnstile:
    """Cloudflare Turnstile's server-side check of the form's token."""

    def __init__(self, secret: str, *, url: str = TURNSTILE_VERIFY) -> None:
        self._secret = secret
        self._url = url
        self._http: aiohttp.ClientSession | None = None

    async def close(self) -> None:
        if self._http is not None:
            await self._http.close()
            self._http = None

    async def verify(self, token: str, address: str) -> bool:
        if not token:
            return False
        if self._http is None:
            self._http = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10))
        try:
            async with self._http.post(
                self._url,
                data={"secret": self._secret, "response": token, "remoteip": address},
            ) as response:
                if response.status >= 400:
                    raise FeedbackError(f"Turnstile answered {response.status}")
                raw = await response.json()
        except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
            raise FeedbackError(f"Couldn't reach Turnstile ({type(exc).__name__})") from exc
        return isinstance(raw, dict) and raw.get("success") is True


class RateLimit:
    """One post per address per 10 minutes, kept in memory: the API runs as one process,
    and a restart forgetting the last 10 minutes does no harm. Addresses are never
    stored in the database or logged."""

    def __init__(self, seconds: int = WAIT_SECONDS, clock: Callable[[], float] = time.monotonic):
        self._seconds = seconds
        self._clock = clock
        self._last: dict[str, float] = {}

    def take(self, address: str) -> bool:
        """Claim this address's turn; False if it posted (or is posting) too recently."""
        now = self._clock()
        # Forget addresses whose wait is over, so the table can't grow without end.
        self._last = {a: t for a, t in self._last.items() if now - t < self._seconds}
        if address in self._last:
            return False
        self._last[address] = now
        return True

    def give_back(self, address: str) -> None:
        """The post failed: let the person try again straight away."""
        self._last.pop(address, None)


@dataclass(frozen=True)
class Message:
    kind: Kind
    text: str
    contact: str | None


def read_message(raw: object) -> Message | str:
    """The form's fields, checked; or the error code to answer with."""
    if not isinstance(raw, dict):
        return "bad_request"
    kind = raw.get("kind")
    text = raw.get("message")
    contact = raw.get("contact")
    if kind not in KINDS or not isinstance(text, str):
        return "bad_request"
    if contact is not None and not isinstance(contact, str):
        return "bad_request"
    text = text.strip()
    if not text:
        return "empty"
    if len(text) > MAX_MESSAGE:
        return "too_long"
    contact = (contact or "").strip() or None
    if contact is not None and len(contact) > MAX_CONTACT:
        return "contact_too_long"
    return Message(kind=kind, text=text, contact=contact)


async def post(db: Database, discussions: Discussions, message: Message, *, now: int) -> int:
    """Post the message publicly, then keep it with its contact. Returns the discussion's
    number. GitHub first: if it fails, nothing is stored and the person can simply send
    again, without leaving a duplicate behind."""
    day = datetime.datetime.fromtimestamp(now, tz=datetime.UTC).date()
    number = await discussions.create(
        message.kind, TITLES[message.kind], discussion_body(message.text, day)
    )
    # No server or person is set: the table's policies allow adding a row, nothing else.
    async with db.unscoped() as conn:
        await conn.execute(
            "INSERT INTO feedback (kind, message, contact, discussion, created_at)"
            " VALUES (%s, %s, %s, %s, %s)",
            (message.kind, message.text, message.contact, number, now),
        )
    return number
