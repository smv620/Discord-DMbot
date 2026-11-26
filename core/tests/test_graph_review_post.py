"""Posting a graph improvement report (#3742): the report built from its stored verdicts under the
posting rules (a stopped speaker holds the whole report, an unknown or shared lore upload is never
quoted, ids and keys are cut out, every word is escaped), and the GitHub Discussions client with a
fake transport. No database. Invented names; the "ids" and "keys" here are made up."""

from __future__ import annotations

import unittest
from typing import Any

from dmbot.memory import graph_review_post as gp

ALICE, BOB = 7001, 7002
REPORT_ID = "r" * 32


def verdict(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "fix": 1,
        "kind": "link",
        "words": "Mara serves Tavin",
        "relation_id": "a" * 32,
        "entries": [
            {"id": "b" * 32, "kind": "character", "label": "Mara", "made_by": "c" * 40},
            {"id": "d" * 32, "kind": "character", "label": "Tavin", "made_by": None},
        ],
        "quote": "Mara has served Tavin since the fire",
        "sources": ["line"],
        "speaker_ids": [ALICE],
        "upload_kinds": [],
    }
    base.update(over)
    return base


def stored(
    *verdicts: dict[str, Any], speakers: tuple[int, ...] = (ALICE,), **over: Any
) -> gp.Stored:
    args: dict[str, Any] = {
        "id": REPORT_ID,
        "campaign_id": "c" * 32,
        "created_at": 1_800_000_000,
        "review_commit": "e" * 40,
        "checked": 3,
        "missed": len(verdicts),
        "speaker_ids": speakers,
        "verdicts": {"items": list(verdicts), "more_not_shown": 0},
    }
    args.update(over)
    return gp.Stored(**args)


def render(
    report: gp.Stored, *, allowed: tuple[int, ...] = (ALICE, BOB), sharing: bool = True
) -> Any:
    return gp.render(report, allowed_speakers=allowed, sharing=sharing)


class TheRules(unittest.TestCase):
    def test_a_report_is_the_campaigns_internal_id_and_its_fixes(self) -> None:
        post = render(stored(verdict()))
        assert isinstance(post, gp.Post)
        self.assertEqual(
            post.title, "Graph improvements, campaign " + "c" * 32 + ", 2027-01-15 08:00 UTC"
        )
        self.assertIn(f"<!-- dmbot-report:{REPORT_ID} -->", post.body)
        self.assertIn('"Mara has served Tavin since the fire"', post.body.replace("\\", ""))
        self.assertIn("made by build an older build", post.body)  # an entry made by no known build
        self.assertIn("c" * 40, post.body)  # a commit hash is no key: it goes as it is
        self.assertIn("e" * 40, post.body)

    def test_a_speaker_who_stopped_holds_the_whole_report(self) -> None:
        held = render(
            stored(verdict(), verdict(fix=2, speaker_ids=[BOB]), speakers=(ALICE, BOB)),
            allowed=(ALICE,),
        )
        self.assertEqual(held, gp.Held(gp.SPEAKER_STOPPED))

    def test_a_campaign_that_stopped_sharing_posts_nothing(self) -> None:
        self.assertEqual(render(stored(verdict()), sharing=False), gp.Held(gp.NOT_SHARING))

    def test_a_report_with_no_stored_verdicts_is_never_posted(self) -> None:
        self.assertEqual(render(stored(verdicts=None)), gp.Held(gp.NO_VERDICTS))

    def test_lore_is_quoted_only_from_the_dms_own_writing(self) -> None:
        for kinds, quoted in (
            (["own_writing"], True),
            (["own_writing", "unknown"], False),  # one unknown source is enough to say nothing
            (["unknown"], False),
            ([], False),
        ):
            with self.subTest(kinds=kinds):
                post = render(
                    stored(
                        verdict(
                            sources=["lore"],
                            speaker_ids=[],
                            upload_kinds=kinds,
                            quote="the silver key is under the step",
                        )
                    )
                )
                assert isinstance(post, gp.Post)
                self.assertEqual("silver key" in post.body, quoted)
                self.assertEqual(gp.GENERIC_LORE in post.body, not quoted)

    def test_a_quote_from_a_line_and_a_lore_passage_needs_both_to_pass(self) -> None:
        mixed = verdict(sources=["line", "lore"], speaker_ids=[ALICE], upload_kinds=["unknown"])
        post = render(stored(mixed))
        assert isinstance(post, gp.Post)
        self.assertNotIn("served Tavin", post.body)

    def test_a_description_is_never_quoted_its_words_may_be_anyones(self) -> None:
        for sources in (["description"], ["description", "line"]):
            post = render(
                stored(verdict(sources=sources, speaker_ids=[ALICE], quote="Mara, a spy"))
            )
            assert isinstance(post, gp.Post)
            self.assertNotIn("Mara, a spy", post.body)
            self.assertIn(gp.GENERIC_LORE, post.body)

    def test_ids_mentions_emails_and_keys_are_cut_out_and_counted_never_named(self) -> None:
        dirty = (
            "ask <@123456789012345678> or 123456789012345678 or mara@example.com "
            "with sk-abcdefghijklmnopqrstuvwxyz0123456789 and @everyone"
        )
        post = render(stored(verdict(quote=dirty, words=dirty)))
        assert isinstance(post, gp.Post)
        for leaked in ("123456789012345678", "example.com", "sk-abcdef", "@everyone"):
            self.assertNotIn(leaked, post.body)
        self.assertEqual(
            {k: v > 0 for k, v in post.cut.items()},
            {"mention": True, "email": True, "key": True, "id": True},
        )

    def test_the_words_cannot_format_the_post_or_ping_anyone(self) -> None:
        post = render(stored(verdict(quote="**bold** [link](http://x) @someone `code` # head")))
        assert isinstance(post, gp.Post)
        self.assertNotIn("**bold**", post.body)
        self.assertNotIn("](http", post.body)
        self.assertNotIn("@someone", post.body)  # (a zero-width space follows an @)

    def test_a_huge_report_is_cut_with_a_note_and_stays_inside_the_limit(self) -> None:
        many = [verdict(fix=n, quote="x" * 300, words="w" * 300) for n in range(1, 400)]
        post = render(stored(*many))
        assert isinstance(post, gp.Post)
        self.assertLessEqual(len(post.body), gp.BODY_MAX)
        self.assertIn("More not shown", post.body)

    def test_stored_more_not_shown_is_said(self) -> None:
        post = render(stored(verdict(), verdicts={"items": [verdict()], "more_not_shown": 4}))
        assert isinstance(post, gp.Post)
        self.assertIn("4 more not shown", post.body)

    def test_an_entry_id_with_odd_characters_never_breaks_the_code_span(self) -> None:
        self.assertEqual(gp.escape_code("ab`c\nd"), "abcd")


class FakeGitHub:
    """GitHub's GraphQL, answering from a small state: one repository with the category."""

    def __init__(self, *, category: bool = True, posted: list[str] | None = None) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.category = category
        self.discussions: list[dict[str, Any]] = [
            {"number": 5 + i, "body": b} for i, b in enumerate(posted or [])
        ]

    async def __call__(self, query: str, variables: Any) -> dict[str, Any]:
        self.calls.append((query.split("(")[0].strip(), dict(variables)))
        if "createDiscussion" in query:
            number = 100 + len(self.discussions)
            self.discussions.insert(0, {"number": number, "body": variables["body"]})
            return {"createDiscussion": {"discussion": {"number": number}}}
        if "discussions(" in query:
            return {"repository": {"discussions": {"nodes": self.discussions}}}
        nodes = [{"id": "CAT", "name": "Graph improvements", "slug": "graph-improvements"}]
        return {
            "repository": {
                "id": "REPO",
                "discussionCategories": {"nodes": nodes if self.category else []},
            }
        }


class TheScrubbing(unittest.TestCase):
    def test_a_discord_bot_token_is_cut_out(self) -> None:
        # built in pieces so no scanner mistakes this made-up shape for a real token
        token = ".".join(["MTIzNDU2Nzg5MDEyMzQ1Njc4", "GabcDE", "abcdefghijklmnopqrstuvwxyz0"])
        got = render(stored(verdict(quote=f"the key is {token} ok")))
        text = got.body if not isinstance(got, gp.Held) else ""
        self.assertNotIn(token, text)

    def test_hidden_direction_and_zero_width_characters_are_removed(self) -> None:
        self.assertEqual(gp.escape("a\u202eb\u200bc"), "abc")


class TheClient(unittest.IsolatedAsyncioTestCase):
    async def test_it_looks_up_the_ids_once_and_posts_one_discussion(self) -> None:
        fake = FakeGitHub()
        hub = gp.GitHub("smv620/dmbot-improvements", fake)
        post = render(stored(verdict()))
        assert isinstance(post, gp.Post)
        self.assertEqual(await hub.create(post), 100)
        await hub.create(post)
        lookups = [c for c in fake.calls if c[0].startswith("query") and "discussions" not in c[0]]
        self.assertEqual(len(lookups), 1)  # the ids are looked up once
        sent = [v for q, v in fake.calls if q.startswith("mutation")]
        self.assertEqual((sent[0]["repo"], sent[0]["category"]), ("REPO", "CAT"))

    async def test_a_report_already_posted_is_found_by_its_marker(self) -> None:
        post = render(stored(verdict()))
        assert isinstance(post, gp.Post)
        fake = FakeGitHub(posted=["# some other report", post.body])
        hub = gp.GitHub("smv620/dmbot-improvements", fake)
        self.assertEqual(await hub.already(REPORT_ID), 6)
        self.assertIsNone(await hub.already("z" * 32))

    async def test_a_missing_category_or_a_bad_repository_name_is_a_fixed_error(self) -> None:
        with self.assertRaises(gp.PostError) as caught:
            await gp.GitHub("smv620/dmbot-improvements", FakeGitHub(category=False)).ids()
        self.assertEqual(str(caught.exception), "no_category")
        with self.assertRaises(gp.PostError) as bad:
            await gp.GitHub("nonsense", FakeGitHub()).ids()
        self.assertEqual(str(bad.exception), "bad_repository")

    async def test_the_token_is_not_something_the_client_ever_holds(self) -> None:
        hub = gp.GitHub("a/b", FakeGitHub())
        self.assertNotIn("token", repr(hub).lower())
