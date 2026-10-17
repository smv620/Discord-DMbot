"""Reading one published D&D Beyond homebrew page (#1634; docs/PLAN.md, "Homebrew (part 2)").
Pure: no network, no database. What it reads is handed in as text.

**The risk is accepted (owner, 2026-10-16).** D&D Beyond's terms limit automated access. The owner
reads them as aimed at automated scraping, which this is not: ONE page, pulled when ONE user asks
for it, at the address that user pasted. Never a crawl, never a list of pages, never a link
followed from the page, no sign-in, no cookies. Revisit if D&D Beyond objects or blocks it.

**Which sites.** Only the sites listed in `IMPORT_SITES`, starting with dndbeyond.com (and its
`www.`, which `site_urls` folds into it). Adding a site is a decision for the owner, recorded in
the PLAN first.

D&D Beyond answers an unpublished homebrew page with "page not found", so that is what "not
public" looks like from here. The reading is a plain heuristic over the page's own words (its
title, then the text of its main part); the parser's tests use pages written by hand for them,
never a real page's text (CLAUDE.md, the IP rule).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urlsplit

from dmbot.rules.homebrew import NAME_MAX, TEXT_MAX
from dmbot.rules.site_urls import normalise_url

IMPORT_SITES = ("dndbeyond.com",)  # PLAN, "Homebrew (part 2)": approved sites, one at a time
NOT_SUPPORTED = (
    "DMbot can only add homebrew from D&D Beyond for now. Paste a link to a published homebrew "
    "page there."
)
NOT_PUBLIC = (
    "That page isn't public. Publish your homebrew on D&D Beyond first, then paste the link again."
)
NO_ANSWER = "D&D Beyond didn't answer. Try again in a minute."
NO_WORDS = (
    "DMbot found no homebrew text on that page. Check that the link is for a homebrew page, "
    "then try again."
)

# The first part of the address says what the page is (PLAN): anything else is "other".
_KINDS = {
    "magic-items": "item",
    "spells": "spell",
    "monsters": "monster",
    "feats": "feat",
    "species": "species",
    "races": "species",  # the site's older path for species
    "subclasses": "subclass",
    "backgrounds": "background",
}
_SKIPPED = frozenset(
    {"script", "style", "nav", "header", "footer", "aside", "form", "button", "noscript", "svg"}
)
_BLOCKS = frozenset(
    {"p", "div", "li", "br", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article", "ul"}
)
# The site's own not-found page: its title *starts* with this ("Potion of 404" is a real title).
_NOT_FOUND = re.compile(r"^\s*(?:page not found|404(?: error)?\b|not found\b)", re.IGNORECASE)
MAX_HTML = 300_000  # characters read: a real homebrew page is a small fraction of this
_STRAY_LT = re.compile(r"<(?![a-zA-Z/!?])")  # a "<" that starts no tag (HTMLParser is slow on many)
_SITE_TAIL = re.compile(r"\s+[-|–—]\s+(?:[^-|–—]*\s+)?D&D Beyond\s*$", re.IGNORECASE)


def allowed(domain: str) -> bool:
    """Is this a site DMbot adds homebrew from? (`domain` as `site_urls.normalise_domain`.)"""
    return domain in IMPORT_SITES


def check_address(text: str) -> tuple[str, str] | None:
    """(the address in its one form, its site) if it is an https address on an allowed site;
    None for anything else (the caller says `NOT_SUPPORTED`). The one form is `www.` + the site,
    the path in lower case and no query: however a page is written, it is one address, so a kept
    page is found again and is never fetched twice."""
    found = normalise_url(text)
    if found is None or not allowed(found[1]):
        return None
    path = urlsplit(found[0]).path.lower()
    return f"https://www.{found[1]}{path}", found[1]


def kind_for(url: str) -> str:
    """The kind of entry a page is, from the first part of its address's path."""
    first = urlsplit(url).path.strip("/").split("/", 1)[0].lower()
    return _KINDS.get(first, "other")


@dataclass(frozen=True, slots=True)
class Page:
    """What was read from a page: its name, its words (cut to the limit), and whether they were."""

    name: str
    text: str
    truncated: bool


class _Reader(HTMLParser):
    """Collects the page's title, its main part's text and its meta title, as plain text."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.h1: list[str] = []
        self.title: list[str] = []
        self.meta_title = ""
        self.lines: list[list[str]] = [[]]  # blocks of text pieces, joined once at the end
        self._skip = 0
        self._in_h1 = False
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "meta":
            d = {k: (v or "") for k, v in attrs}
            if d.get("property") == "og:title" and not self.meta_title:
                self.meta_title = d.get("content", "")
            return
        if tag == "title":
            self._in_title = True
        if tag in _SKIPPED:
            self._skip += 1
            return
        if tag == "h1":
            self._in_h1 = True
        if tag in _BLOCKS:
            self.lines.append([])

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        if tag in _SKIPPED and self._skip:
            self._skip -= 1
            return
        if tag == "h1":
            self._in_h1 = False
        if tag in _BLOCKS:
            self.lines.append([])

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title.append(data)
            return
        if self._skip:
            return
        if self._in_h1:
            self.h1.append(data)
        self.lines[-1].append(data)


def parse_page(html: str) -> Page | None:
    """The page's name and words, or None when no words can be found. The name is its first
    heading (else its title, without the site's tail); the words are every block of text outside
    the page's menus, buttons and footers, minus the name, one block to a line. Only the first
    `MAX_HTML` characters are read, and this does CPU work: call it off the event loop."""
    reader = _Reader()
    try:
        reader.feed(_STRAY_LT.sub("&lt;", html[:MAX_HTML]))
        reader.close()
    except Exception:  # an untrusted page: whatever it holds, this never raises
        return None
    name = " ".join("".join(reader.h1).split())
    if not name:
        raw = " ".join("".join(reader.title).split()) or " ".join(reader.meta_title.split())
        name = _SITE_TAIL.sub("", raw).strip()
    name = name[:NAME_MAX].strip()
    blocks = [" ".join("".join(line).split()) for line in reader.lines]
    seen: set[str] = set()
    kept: list[str] = []
    for block in blocks:
        if not block or block == name or block in seen:
            continue
        seen.add(block)
        kept.append(block)
    text = "\n".join(kept)
    if not name or not text:
        return None
    truncated = len(text) > TEXT_MAX
    if truncated:
        cut = text[:TEXT_MAX]
        space = cut.rfind(" ")
        text = (cut[:space] if space > TEXT_MAX // 2 else cut).rstrip() + "…"
        text = text[:TEXT_MAX]
    return Page(name, text, truncated)


def is_not_found(status: int, html: str) -> bool:
    """D&D Beyond's answer for a page that is not public (or not there): a 404, or a page whose
    own title says it was not found."""
    if status in (404, 410):
        return True
    page_title = re.search(r"<title[^>]*>(.*?)</title>", html[:20000], re.IGNORECASE | re.DOTALL)
    return bool(page_title and _NOT_FOUND.search(page_title.group(1)))
