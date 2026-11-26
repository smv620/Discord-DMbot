"""Posting a graph improvement report to the owner's private repository (#3742): the part with no
database. A stored report (`graph_reviews`, #3741) becomes one GitHub Discussion in the category
"Graph improvements", only for a campaign that shares its story to improve DMbot, and only when the
rules below hold *now*: they are checked at posting time, not when the report was made.

**What is never posted** (Supervisor's ruling on #3742 and #3741; CLAUDE.md):
- a report with a quote from anyone who has since stopped being recorded, or opted out of improving
  DMbot: the **whole report is held** back (a half-report would mislead), and stays on the server;
- a quote of a lore passage or of an entry's description, never (Supervisor's ruling on #3742): an
  upload confirms the right to use its text, not who wrote it, and a description can hold words from
  the table, an upload or a picture with nothing recording which. The words say "a passage from the
  campaign's story (words held back)" and give the entry's id. Only a line from someone who still
  agrees, the fix itself and its structure post. A shared adventure or rulebook is never quoted;
- a Discord id, a mention, an email address or anything shaped like a key: they are cut out of every
  word that goes (`scrub`), and counted. Only the campaign's internal id names the campaign.
Every word that goes is Markdown-escaped (`escape`), so a name or a quote can't format the post or
ping anyone. With no token or repository set, nothing is posted.

The report is built here from the stored `verdicts` (the structured form of the report GraphEngineer
writes in the same statement as the Markdown), never from the Markdown itself, so what is left out
is left out for good. Plain HTTPS with aiohttp through an injected transport: tests use a fake. The
token is only ever in an `Authorization` header and is never logged. Logs carry counts only.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable, Collection, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

log = logging.getLogger(__name__)

CATEGORY = "Graph improvements"  # the discussion category the owner made
CATEGORY_SLUG = "graph-improvements"
TITLE_MAX = 200
BODY_MAX = 60_000  # GitHub's limit is 65,536 characters
LABEL_MAX = 100
OLD_BUILD = "an older build"
GENERIC_LORE = "a passage from the campaign's story (words held back)"
# The DM's own writing: the only lore that is quoted (`models.UPLOAD_OWN_WRITING`). An upload of
# any other or unknown kind is read as "unknown": the safe side.
MARKER = "dmbot-report"

# ---- what is cut out of every word that goes ---------------------------------------------------

_MENTION = re.compile(r"<[@#][!&]?\d{5,}>|@everyone|@here", re.I)
_DISCORD_ID = re.compile(r"(?<!\d)\d{17,20}(?!\d)")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
# Anything shaped like a key or a token: the usual prefixes, or a long run of key characters.
_KEY = re.compile(
    r"\b(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}"
    r"|xox[abp]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{16}|[A-Za-z0-9_-]{23,28}\.[A-Za-z0-9_-]{6,7}\."
    r"[A-Za-z0-9_-]{27,}|[A-Za-z0-9_-]{32,})\b"
)
REMOVED = "[removed]"


def scrub(text: str, counts: dict[str, int] | None = None) -> str:
    """The words with every id, mention, email and key-shaped string cut out. `counts`, when given,
    says how many of each kind were cut (never what they were)."""
    out = text
    for kind, pattern in (
        ("mention", _MENTION),
        ("email", _EMAIL),
        ("key", _KEY),
        ("id", _DISCORD_ID),
    ):
        out, n = pattern.subn(REMOVED, out)
        if n and counts is not None:
            counts[kind] = counts.get(kind, 0) + n
    return out


# Zero-width and bidirectional control characters: a quote can't hide or reverse text with them.
_CONTROLS = re.compile("[\u200b-\u200f\u202a-\u202e\u2060-\u2064\u2066-\u2069\ufeff]")
_SPECIAL = re.compile(r"([\\`*_{}\[\]()<>#+\-.!|~$&:])")


def escape(text: str) -> str:
    """Markdown-safe: every character that can format is escaped, a line break is a space, and an
    `@` can't become a mention (a zero-width space follows it)."""
    flat = " ".join(_CONTROLS.sub("", text).split())
    return _SPECIAL.sub(r"\\\1", flat).replace("@", "@​")


def clean(text: str, counts: dict[str, int] | None = None, limit: int | None = None) -> str:
    """`scrub` first, then `escape`, then cut at `limit`."""
    out = escape(scrub(text, counts))
    return out if limit is None or len(out) <= limit else out[: limit - 1].rstrip() + "…"


# ---- the report, from its stored verdicts -------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Stored:
    """One stored report, as the poster reads it."""

    id: str
    campaign_id: str
    created_at: int
    review_commit: str | None
    checked: int
    missed: int
    speaker_ids: tuple[int, ...]
    # The stored structure: `{"items": [...], "more_not_shown": N}` (`graph_review.verdicts_json`),
    # or None for a report from before verdicts were kept (never posted).
    verdicts: Mapping[str, Any] | None


@dataclass(frozen=True, slots=True)
class Post:
    title: str
    body: str
    cut: Mapping[str, int] = field(default_factory=dict)  # what was cut out, by kind: counts only


@dataclass(frozen=True, slots=True)
class Held:
    """A report that must not go (yet): why, in a fixed word. Counts only in the log."""

    why: str


NO_VERDICTS = "no_verdicts"  # a report from before verdicts were stored: never posted
SPEAKER_STOPPED = "speaker_stopped"
NOT_SHARING = "not_sharing"
TOO_BIG = "too_big"
BAD_VERDICTS = "bad_verdicts"  # stored in a shape that can't be read: held, never posted


def stamp(created_at: int) -> str:
    return datetime.fromtimestamp(created_at, UTC).strftime("%Y-%m-%d %H:%M UTC")


def title_of(report: Stored) -> str:
    """ "Graph improvements, campaign <internal id>, <timestamp>": the campaign's own id, never its
    name."""
    return f"Graph improvements, campaign {report.campaign_id}, {stamp(report.created_at)}"[
        :TITLE_MAX
    ]


def quotable(verdict: Mapping[str, Any], allowed_speakers: Collection[int]) -> bool:
    """May this quote's words go? Every source it came from must pass: a line only from people who
    still agree, and a lore passage or a description never (Supervisor's ruling on #3742). An
    upload confirms the right to use its text, not who wrote it (GraphEngineer's correction to
    slice A), and a description can hold words from the table ("Met in play: ..."), an upload or
    a picture with nothing recording which. The structure (the fix, the entries and their ids)
    always posts."""
    sources = [str(s) for s in verdict.get("sources", [])]
    if not sources:
        return False
    for source in sources:
        if source == "line":
            speakers = verdict.get("speaker_ids") or []
            if not speakers or not all(int(s) in allowed_speakers for s in speakers):
                return False
        else:  # lore, a description, or any source this rule does not know: the words are held
            return False
    return True


def render(report: Stored, *, allowed_speakers: Collection[int], sharing: bool) -> Post | Held:
    """The Discussion for one report, or why it can't go. `allowed_speakers`: the people whose lines
    may still be quoted (recording consent given and not opted out of improving DMbot), read when
    this is called. `sharing`: the campaign's "share to improve DMbot" is still on."""
    if not sharing:
        return Held(NOT_SHARING)
    if report.verdicts is None:
        return Held(NO_VERDICTS)
    if any(int(s) not in allowed_speakers for s in report.speaker_ids):
        return Held(SPEAKER_STOPPED)  # the whole report: a part of it would mislead
    cut: dict[str, int] = {}
    checked_by = build_of(report.review_commit, cut) if report.review_commit else "unknown"
    lines = [
        f"<!-- {MARKER}:{report.id} -->",
        "# DMbot graph improvements",
        "",
        f"- Campaign: `{report.campaign_id}`",
        f"- Made: {stamp(report.created_at)}, when the DM fixed the story graph",
        f"- Checked by build: {checked_by}",
        f"- Fixes checked: {report.checked}; DMbot missed: {report.missed}",
        "",
    ]
    for item in report.verdicts.get("items", []):
        number = int(item.get("fix", 0))
        section = [f"## Fix {number}: {clean(str(item.get('words', '')), cut, 3 * LABEL_MAX)}", ""]
        if item.get("relation_id"):
            section.append(f"- Connection: `{escape_code(str(item['relation_id']))}`")
        for entry in item.get("entries", []):
            kind = clean(str(entry["kind"]), cut, 30)
            label = clean(str(entry["label"]), cut, LABEL_MAX)
            build = build_of(entry.get("made_by"), cut)
            code = escape_code(str(entry["id"]))
            section.append(f"- Entry `{code}` ({kind}): {label}, made by build {build}")
        if quotable(item, allowed_speakers):
            where = ", ".join(clean(str(s), cut, 20) for s in item.get("sources", [])) or "unknown"
            section.append(f'- In the story ({where}): "{clean(str(item.get("quote", "")), cut)}"')
        else:
            ids = ", ".join(f"`{escape_code(str(e['id']))}`" for e in item.get("entries", []))
            section.append(
                f"- In the story: {GENERIC_LORE} (entries {ids})"
                if ids
                else f"- In the story: {GENERIC_LORE}"
            )
        section.append("")
        if sum(len(x) + 1 for x in lines + section) > BODY_MAX:
            lines.append("More not shown: the report reached its size limit.")
            break
        lines += section
    more = int(report.verdicts.get("more_not_shown", 0) or 0)
    if more:
        lines.append(f"{more} more not shown: the report reached its size limit.")
    body = "\n".join(lines).rstrip() + "\n"
    if len(body) > BODY_MAX:
        return Held(TOO_BIG)
    return Post(title_of(report), body, dict(cut))


_ID_ONLY = re.compile(r"[^0-9a-zA-Z_-]")
_COMMIT = re.compile(r"[0-9a-f]{40}")


def build_of(text: str | None, counts: dict[str, int]) -> str:
    """A build: a commit hash goes as it is (it is what the developers need, and it is no secret:
    it is not run through the key-shaped scrub); anything else is cleaned like any other word."""
    if not text:
        return OLD_BUILD
    return text if _COMMIT.fullmatch(text) else clean(text, counts, 60)


def escape_code(text: str) -> str:
    """An id inside backticks: only its own characters (never a backtick or a line break)."""
    return _ID_ONLY.sub("", text)[:64]


# ---- GitHub: Discussions are GraphQL only -------------------------------------------------------

Transport = Callable[[str, Mapping[str, Any]], Awaitable[Mapping[str, Any]]]

_REPO = (
    "query($owner: String!, $name: String!) { repository(owner: $owner, name: $name) {"
    " id discussionCategories(first: 50) { nodes { id name slug } } } }"
)
_RECENT = (
    "query($owner: String!, $name: String!, $category: ID!) {"
    " repository(owner: $owner, name: $name) {"
    " discussions(first: 50, categoryId: $category, orderBy: {field: CREATED_AT, direction: DESC})"
    " { nodes { number body } } } }"
)
_CREATE = (
    "mutation($repo: ID!, $category: ID!, $title: String!, $body: String!) {"
    " createDiscussion(input: {repositoryId: $repo, categoryId: $category, title: $title,"
    " body: $body}) { discussion { number } } }"
)


class PostError(Exception):
    """A post that did not happen, for a fixed reason (no word of the report in it)."""


@dataclass(slots=True)
class GitHub:
    """The one repository's discussions. `transport(query, variables)` answers GitHub's `data` or
    raises; it holds the token, which nothing here sees."""

    repo: str  # "owner/name"
    transport: Transport
    _ids: tuple[str, str] | None = field(default=None, init=False)

    @property
    def parts(self) -> tuple[str, str]:
        owner, _, name = self.repo.partition("/")
        if not owner or not name or "/" in name:
            raise PostError("bad_repository")
        return owner, name

    async def ids(self) -> tuple[str, str]:
        """The repository's id and the category's id, looked up once."""
        if self._ids is not None:
            return self._ids
        owner, name = self.parts
        data = await self.transport(_REPO, {"owner": owner, "name": name})
        repo = (data.get("repository") or {}) if isinstance(data, Mapping) else {}
        nodes = (repo.get("discussionCategories") or {}).get("nodes") or []
        category = next(
            (n for n in nodes if n.get("slug") == CATEGORY_SLUG or n.get("name") == CATEGORY), None
        )
        if not repo.get("id") or category is None:
            raise PostError("no_category")
        self._ids = (str(repo["id"]), str(category["id"]))
        return self._ids

    async def already(self, report_id: str) -> int | None:
        """The number of the discussion already posted for this report (its marker is in the body),
        among the latest 50 of the category: a post that went out but was never marked."""
        owner, name = self.parts
        _repo, category = await self.ids()
        data = await self.transport(_RECENT, {"owner": owner, "name": name, "category": category})
        nodes = ((data.get("repository") or {}).get("discussions") or {}).get("nodes") or []
        marker = f"<!-- {MARKER}:{report_id} -->"
        found = next((n for n in nodes if marker in str(n.get("body", ""))), None)
        return None if found is None else int(found["number"])

    async def create(self, post: Post) -> int:
        repo, category = await self.ids()
        data = await self.transport(
            _CREATE, {"repo": repo, "category": category, "title": post.title, "body": post.body}
        )
        made = ((data.get("createDiscussion") or {}).get("discussion") or {}).get("number")
        if not isinstance(made, int):
            raise PostError("not_created")
        return made
