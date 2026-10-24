"""A backstory's connections are matched to names the way a reader would (#2642): "my brother
Ulric", "Sister Mara" and "old Brannoc, my mentor" are the listed Ulric Stonehand, Mara Ashgrove
and Brannoc, a word that fits two names is a question and never a guess, a failed slice is tried in
halves, the summary adds up, and Keep all keeps every ready one and Undo takes them back. Invented
names and text throughout."""

from __future__ import annotations

import dataclasses
import json
import re
import time
import unittest
from collections import Counter
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, PropertyMock, patch

from dmbot.ai import AIError, Reply
from dmbot.bot import DMBot
from dmbot.dm_screen import links_ready
from dmbot.memory import background as bg
from dmbot.memory import backstory_keep as keep
from dmbot.memory import lore_read as lr
from dmbot.memory.backstory_links import BackstoryLinks, NewLink
from dmbot.memory.end_match import Ends, head, stripped
from dmbot.memory.models import CONFIRMED, NewName
from dmbot.ui import name_background as ui_bg
from dmbot.ui.name_lists import Upload, _ai_busy, _ai_reads
from tests import lore_fake
from tests.test_background_connections import link, name, reply, shown
from tests.test_memory_names import DM, GUILD, NamesTest

TEXT2 = (
    "Mirelle the Pale serves the Order of the Grey Hand. Brannoch Vale was born in the hills; "
    "my brother Brannoch is the brother of Mirelle."
)
FULL = {
    "Ulric Stonehand": "Ulric Stonehand",
    "Mara Ashgrove": "Mara Ashgrove",
    "Brannoc": "Brannoc",
    "Tavin Ashgrove": "Tavin Ashgrove",
    "Mara Vell": "Mara Vell",
    "Greywater Keep": "Greywater Keep",
}


def reader() -> Ends:
    return Ends({k.casefold(): v for k, v in FULL.items()}, FULL.values())


class Matching(unittest.TestCase):
    def test_a_first_name_a_last_name_and_a_title_each_find_the_one_name(self) -> None:
        r = reader()
        for end, want in (
            ("Ulric", "Ulric Stonehand"),
            ("Stonehand", "Ulric Stonehand"),
            ("my brother Ulric", "Ulric Stonehand"),
            ("Old Brannoc", "Brannoc"),
            ("my mentor Brannoc", "Brannoc"),
            ("Sister Tavin", "Tavin Ashgrove"),
            ("Ulric Stonehand", "Ulric Stonehand"),
            ("ULRIC STONEHAND", "Ulric Stonehand"),
        ):
            got = r.match(end)
            self.assertEqual((got.name, got.ambiguous), (want, ()), end)

    def test_a_trailing_description_resolves_like_a_leading_one(self) -> None:
        """#2650: "Brannoc, my mentor" is "my mentor Brannoc"; a comma that is not followed by a
        description is not cut, and a description alone never finds a name."""
        r = reader()
        for end, want in (
            ("Brannoc, my mentor", "Brannoc"),
            ("Brannoc (my mentor)", "Brannoc"),
            ("Ulric, his brother", "Ulric Stonehand"),
            ("old Brannoc, the smith", "Brannoc"),
        ):
            got = r.match(end)
            self.assertEqual((got.name, got.ambiguous), (want, ()), end)
        self.assertEqual(r.match("Mara, my sister").ambiguous, ("Mara Ashgrove", "Mara Vell"))
        self.assertIsNone(r.match(", my mentor").name)
        self.assertEqual(head("Ulfgar, Lord of Frost"), "Ulfgar")
        self.assertEqual(head("Frost, Ulfgar"), "Frost, Ulfgar")  # not a description

    def test_a_word_that_fits_two_names_is_a_question_not_a_guess(self) -> None:
        got = reader().match("Sister Mara")
        self.assertIsNone(got.name)
        self.assertEqual(got.ambiguous, ("Mara Ashgrove", "Mara Vell"))
        self.assertEqual(reader().match("Ashgrove").ambiguous, ("Mara Ashgrove", "Tavin Ashgrove"))

    def test_what_fits_nothing_or_only_a_title_matches_nothing(self) -> None:
        for end in ("Zorgul", "my brother", "the old", "of", "Keep Greywa"):
            got = reader().match(end)
            self.assertEqual((got.name, got.ambiguous), (None, ()), end)

    def test_titles_come_off_the_front_only_and_never_empty_a_name(self) -> None:
        self.assertEqual(stripped("my brother Ulric"), ["ulric"])
        self.assertEqual(stripped("Old Man Brannoc"), ["man", "brannoc"])
        self.assertEqual(stripped("Sister"), ["sister"])

    def test_a_secret_spelling_is_matched_only_exactly_never_by_a_word_of_it(self) -> None:
        # the secret spelling is in `exact` (as keep_connections builds it) but not in the names
        r = Ends({"the black hand": "Brannoc", "brannoc": "Brannoc"}, ["Brannoc"])
        self.assertEqual(r.match("The Black Hand").name, "Brannoc")
        self.assertEqual(r.match("Hand").name, None)  # a word of it finds nothing


BACKSTORY = (
    "I grew up in Greywater Keep. My brother Ulric taught me to fight, and Sister Mara healed "
    "me. Old Brannoc, my mentor, trained me. I met Tavin when we were young."
)
LISTED = [
    bg.Found("Ulric Stonehand", "npc", (), (), ""),
    bg.Found("Mara Ashgrove", "npc", (), (), ""),
    bg.Found("Mara Vell", "npc", (), (), ""),
    bg.Found("Brannoc", "npc", (), (), ""),
    bg.Found("Tavin Ashgrove", "npc", (), (), ""),
    bg.Found("Greywater Keep", "place", (), (), ""),
    bg.Found("Aldric", "npc", (), (), ""),
]


def c(a: str, how: str, b: str) -> bg.Connection:
    return bg.Connection(a, how, b, "q")


class Keeping(unittest.TestCase):
    def test_ten_relationships_by_first_name_title_and_possessive_give_ten_links_and_a_question(
        self,
    ) -> None:
        ends = [
            c("Ulric", "kin_of", "Brannoc"),  # first name
            c("my brother Ulric", "ally_of", "Tavin"),  # possessive and a first name
            c("Old Brannoc", "knows", "Greywater Keep"),  # a title
            c("Stonehand", "knows", "Tavin Ashgrove"),  # a last name
            c("my mentor Brannoc", "knows", "Aldric"),
            c("Sister Tavin", "ally_of", "Aldric"),
            c("Ulric Stonehand", "enemy_of", "Aldric"),  # whole
            c("Brannoc", "ally_of", "Aldric"),
            c("Greywater Keep", "located_in", "Aldric"),
            c("Tavin", "kin_of", "Aldric"),
            c("Sister Mara", "knows", "Ulric"),  # which Mara?
        ]
        lost: Counter[str] = Counter()
        questions: list[str] = []
        kept = bg.keep_connections([ends], LISTED, {}, dropped=lost, questions=questions)
        self.assertEqual(len(kept), 10)
        self.assertEqual(lost["which_one"], 1)
        self.assertEqual(questions, ["Sister Mara"])
        self.assertEqual(lost["name_not_found"], 0)
        names_ = {(x.a, x.b) for x in kept}
        self.assertIn(("Ulric Stonehand", "Brannoc"), names_)
        self.assertIn(("Ulric Stonehand", "Tavin Ashgrove"), names_)  # "Tavin" is one name

    def test_a_secret_spelling_with_a_title_in_front_stays_secret(self) -> None:
        """#2591: "Lady Nightshade" is the secret spelling "Nightshade" of Vell Ashgrove."""
        listed = [
            bg.Found("Vell Ashgrove", "npc", (), ("Nightshade",), ""),
            bg.Found("Thieves Guild", "faction", (), (), ""),
        ]
        for end in ("Lady Nightshade", "the Nightshade", "Nightshade"):
            kept = bg.keep_connections([[c(end, "member_of", "Thieves Guild")]], listed, {})
            self.assertEqual([(x.a, x.secret) for x in kept], [("Vell Ashgrove", True)], end)
        plain = bg.keep_connections(
            [[c("Lady Vell", "member_of", "Thieves Guild")]], listed, {}
        )  # the public name by a word: not secret
        self.assertEqual([(x.a, x.secret) for x in plain], [("Vell Ashgrove", False)])

    def test_the_connections_past_the_cap_are_all_counted(self) -> None:
        lost: Counter[str] = Counter()
        many = [c("Ulric", "knows", f"Place {i}") for i in range(5)]
        listed = [*LISTED, *(bg.Found(f"Place {i}", "place", (), (), "") for i in range(5))]
        kept = bg.keep_connections([many], listed, {}, cap=2, dropped=lost)
        self.assertEqual((len(kept), lost["over_cap"]), (2, 3))

    def test_the_same_pair_said_two_ways_is_one_link(self) -> None:
        lost: Counter[str] = Counter()
        kept = bg.keep_connections(
            [[c("Ulric", "kin_of", "Brannoc"), c("my brother Ulric", "kin_of", "Old Brannoc")]],
            LISTED, {}, dropped=lost,
        )  # fmt: skip
        self.assertEqual(len(kept), 1)
        self.assertEqual(lost["same_pair"], 1)

    def test_an_end_that_is_no_name_is_still_not_found(self) -> None:
        lost: Counter[str] = Counter()
        kept = bg.keep_connections([[c("Zorgul", "knows", "Brannoc")]], LISTED, {}, dropped=lost)
        self.assertEqual((kept, lost["name_not_found"]), ([], 1))

    def test_the_campaigns_own_names_match_by_a_word_too(self) -> None:
        kept = bg.keep_connections(
            [[c("Ulric", "kin_of", "Pellan")]], LISTED, {"pellan voss": "Pellan Voss"}
        )
        self.assertEqual([(x.a, x.b) for x in kept], [("Ulric Stonehand", "Pellan Voss")])

    def test_a_listed_full_name_is_accepted_when_the_quote_says_my_brother_ulric(self) -> None:
        answer = json.dumps({"connections": [{
            "a": "Ulric Stonehand", "how": "family", "b": "Brannoc",
            "quote": "My brother Ulric taught me; Old Brannoc, my mentor",
        }]})  # fmt: skip
        text = "Ulric Stonehand and Brannoc. My brother Ulric taught me; Old Brannoc, my mentor"
        got = bg.read_connections(answer, text)
        self.assertEqual([(x.a, x.b) for x in got], [("Ulric Stonehand", "Brannoc")])
        # but a name that is nowhere in the text, or a quote with neither end, is still dropped
        bad = json.dumps({"connections": [{
            "a": "Ulric Stonehand", "how": "family", "b": "Brannoc", "quote": "It rained.",
        }]})  # fmt: skip
        lost: Counter[str] = Counter()
        self.assertEqual(bg.read_connections(bad, text + " It rained.", dropped=lost), [])
        self.assertEqual(lost["not_in_text"], 1)


class Asking(unittest.TestCase):
    def test_the_reading_is_told_to_write_the_listed_full_names(self) -> None:
        text = lr.read_instructions()
        self.assertIn("full names you listed", text)
        self.assertIn("never as 'my brother'", text)


class Retrying(unittest.IsolatedAsyncioTestCase):
    async def test_a_slice_that_fails_is_tried_in_halves_before_a_link_is_lost(self) -> None:
        names = [f"- {n} | person" for n in ("Ulric Stonehand", "Brannoc")]
        links = ['- Ulric Stonehand — is related to — Brannoc | quote: "Ulric and Brannoc"']
        text = "Ulric Stonehand is here. Ulric and Brannoc"
        seen: list[tuple[int, int]] = []

        async def complete(system: str, body: str, *, max_tokens: int, schema: Any = None) -> Reply:
            n = len(re.findall(r"^- ", body.split("RELATIONSHIPS")[0], flags=re.M))
            k = (
                len(re.findall(r"^- ", body.split("RELATIONSHIPS")[1], flags=re.M))
                if "RELATIONSHIPS" in body
                else 0
            )
            seen.append((n, k))
            if n + k > 2:  # the whole slice fails once; halves are small enough
                raise AIError("busy")
            rows = [
                {
                    "name": x[2:].split(" |")[0],
                    "kind": "person",
                    "also": [],
                    "secret": [],
                    "quote": "",
                }
                for x in names
                if x in body
            ]
            conns = (
                [{"a": "Ulric Stonehand", "b": "Brannoc", "how": "family", "words": "",
                  "quote": "Ulric and Brannoc"}]
                if "RELATIONSHIPS" in body else []
            )  # fmt: skip
            return Reply(json.dumps({"names": rows, "connections": conns}), False)

        ai = SimpleNamespace(complete=AsyncMock(side_effect=complete), model="claude-sonnet-5-5")
        stats = lr.Stats()
        import contextlib

        found, joined, cut = await lr._shape(
            ai, text, names, links, {}, contextlib.nullcontext(), stats
        )
        self.assertEqual(len(joined), 1)  # the link was not lost to the failure
        self.assertEqual(stats.dropped["cut_before_check"], 0)
        self.assertFalse(cut)
        self.assertTrue(found)


class Counting(unittest.TestCase):
    def test_the_note_adds_up_and_names_the_question(self) -> None:
        note = ui_bg._found_note(
            34, 33, ready=30, dropped={"which_one": 1}, questions=["Sister Mara"]
        )
        self.assertIn("Found 34 connections: 30 ready to check, 3 wait for names", note)
        self.assertIn("1 left out (Which Sister Mara?)", note)

    def test_every_reason_is_counted_and_the_parts_add_up_to_what_was_found(self) -> None:
        dropped = {
            "same_pair": 2, "already_have": 1, "already_waiting": 1,  # already known: 4
            "name_not_found": 3, "which_one": 1, "not_in_text": 2,  # left out: 6
            "cut_before_check": 2, "over_cap": 3,  # not read: 5, and 2 more the model skipped
        }  # fmt: skip
        found, ready, waits = 30, 7, 4  # 7 + 4 + 4 + 6 + 5 + 2 = 28, 2 missing: not read too
        note = ui_bg._found_note(found, ready + waits, ready=ready, dropped=dropped)
        self.assertIn("Found 30 connections: 7 ready to check, 4 wait for names", note)
        self.assertIn("4 already known", note)
        self.assertIn("6 left out", note)
        self.assertIn("9 not read in time", note)  # 2 cut + 3 over the cap + 4 never shaped
        numbers = [int(n) for n in re.findall(r"(\d+) (?:ready|wait|already|left|not read)", note)]
        self.assertEqual(sum(numbers), found)

    def test_nothing_is_said_when_nothing_was_lost(self) -> None:
        self.assertEqual(ui_bg._found_note(3, 3), "")


class WhenKept(NamesTest):
    """The whole path with Postgres: matched by a word, then Keep all and Undo."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        _ai_busy.clear()
        _ai_reads.clear()
        self.ai = lore_fake.lore_ai(reply())
        self.bot.background_ai = self.ai  # type: ignore[assignment]
        self.bot.background_read_ai = self.ai  # type: ignore[assignment]
        self.bot.plan_gate = AsyncMock(return_value=None)  # type: ignore[method-assign]
        self.bot.campaigns.record_confirmation = AsyncMock()  # type: ignore[method-assign]
        self.store = BackstoryLinks(self.db)
        self.bot.backstory_links = self.store
        names = [
            name("Mirelle", "person", "Mirelle the Pale serves", ("Mirelle the Pale",)),
            name("Order of the Grey Hand", "group", "the Order of the Grey Hand"),
            name("Brannoch Vale", "person", "Brannoch Vale was born"),
        ]
        # the list has the full name "Brannoch Vale"; the AI wrote the end as the text does
        links = [
            link("Brannoch", "family", "Mirelle", "my brother Brannoch is the brother of Mirelle"),
            link("Mirelle the Pale", "works for", "Order of the Grey Hand",
                 "Mirelle the Pale serves the Order of the Grey Hand"),
        ]  # fmt: skip
        lore_fake.set_answer(self.ai, {"names": names, "connections": links})

    async def asyncTearDown(self) -> None:
        _ai_busy.clear()
        _ai_reads.clear()
        await super().asyncTearDown()

    async def read(self) -> Any:
        it = self.it()
        await ui_bg.offer(it, self.campaign.id, Upload(TEXT2, "your background"))
        press = self.it()
        await it.response.sent[0][1]["view"]._read(press)
        return press

    async def offer(self, user: int = DM) -> tuple[Any, Any]:
        it = self.it(user)
        asked = await ui_bg.offer_ready(it, self.campaign.id)
        sent = it.followup.send.await_args
        return it, (sent.kwargs["view"] if asked and sent is not None else None)

    async def relations(self) -> list[Any]:
        return await self.memory.relations(GUILD, self.campaign.id, include_secret=True)

    async def waiting(self) -> list[Any]:
        return await self.store.waiting(GUILD, self.campaign.id)

    async def confirm_all(self) -> None:
        waiting = await self.memory.entities(GUILD, self.campaign.id, statuses=["proposed"])
        for kind in {e.type for e in waiting}:
            ids = [e.id for e in waiting if e.type == kind]
            await self.memory.confirm_kinds(GUILD, self.campaign.id, ids, kind, source="dm")

    def button(self, view: Any, label: str) -> Any:
        return next(b for b in view.children if b.label == label)

    async def test_a_link_to_a_name_the_text_shortens_is_queued_and_the_summary_adds_up(
        self,
    ) -> None:
        press = await self.read()
        self.assertEqual(len(await self.waiting()), 2)
        text = shown(press)
        self.assertIn("2 connections found too", text)

    async def test_keep_all_keeps_every_ready_connection_and_undo_puts_them_back(self) -> None:
        await self.read()
        await self.confirm_all()
        _, view = await self.offer()
        self.assertEqual(self.button(view, "✅ Keep all 2 from this backstory").disabled, False)
        press = self.it()
        await self.button(view, "✅ Keep all 2 from this backstory").callback(press)
        kept = await self.relations()
        self.assertEqual(len(kept), 2)
        self.assertTrue(all(r.status == CONFIRMED and r.source == "dm" for r in kept))
        self.assertEqual(await self.store.count(GUILD, self.campaign.id), 0)
        self.assertIn("Kept 2 connections", shown(press))
        undo = self.it()
        await self.button(view, "↩️ Undo").callback(undo)
        self.assertEqual(await self.relations(), [])
        self.assertEqual(len(await self.waiting()), 2)  # they wait again
        self.assertIn("Took back 2", shown(undo))

    async def test_a_title_in_front_of_a_secret_spelling_is_kept_secret_end_to_end(self) -> None:
        from dmbot.memory.models import NewName

        await self.memory.add_names(
            GUILD, self.campaign.id,
            [NewName("Vell Ashgrove", "npc", CONFIRMED, secrets=("Nightshade",)),
             NewName("Thieves Guild", "faction", CONFIRMED)],
            source="dm",
        )  # fmt: skip
        found = [
            bg.Found("Vell Ashgrove", "npc", (), ("Nightshade",), ""),
            bg.Found("Thieves Guild", "faction", (), (), ""),
        ]
        links = [[bg.Connection("Lady Nightshade", "member_of", "Thieves Guild", "q")]]
        queued = await ui_bg.queue_links(
            self.bot, self.memory, self.campaign, found, links, set(), keys=set()
        )
        self.assertEqual(queued, 1)
        (waiting,) = await self.waiting()
        self.assertTrue(waiting.secret)
        done = await keep.keep_all(self.memory, self.store, GUILD, self.campaign.id, [waiting])
        self.assertEqual(len(done.kept), 1)
        (rel,) = [r for r in await self.relations() if r.predicate == "member_of"]
        self.assertTrue(rel.secret)  # never a public fact about the public name

    async def test_only_a_dm_can_keep_all(self) -> None:
        await self.read()
        await self.confirm_all()
        _, view = await self.offer()
        stranger = self.it(999)
        await self.button(view, "✅ Keep all 2 from this backstory").callback(stranger)
        self.assertEqual(await self.relations(), [])
        self.assertEqual(len(await self.waiting()), 2)

    async def test_a_secret_one_is_kept_as_secret_never_public(self) -> None:
        await self.read()
        await self.confirm_all()
        waiting = await self.waiting()
        await self.store.remove(GUILD, self.campaign.id, waiting[0])
        from dmbot.memory.backstory_links import NewLink

        first = waiting[0]
        await self.store.add(
            GUILD, self.campaign.id,
            [NewLink(first.subject_id, first.predicate, first.object_id, "q", secret=True)],
        )  # fmt: skip
        done = await keep.keep_all(
            self.memory, self.store, GUILD, self.campaign.id, await self.waiting()
        )
        self.assertEqual(len(done.kept), 2)
        secrets = [r.secret for r in await self.relations()]
        self.assertEqual(sorted(secrets), [False, True])

    async def test_the_reading_labels_what_it_queues_with_the_upload(self) -> None:
        await self.read()
        labels = {w.upload for w in await self.waiting()}
        self.assertEqual(len(labels), 1)
        (label,) = labels
        self.assertTrue(label.startswith("your background, "), label)

    async def test_ready_connections_are_said_once(self) -> None:
        await self.read()
        first = await self.store.take_ready_notices(GUILD, self.campaign.id)
        self.assertEqual(first, {})  # the names are not answered yet
        await self.confirm_all()
        said = await self.store.take_ready_notices(GUILD, self.campaign.id)
        self.assertEqual(sum(said.values()), 2)
        self.assertEqual(await self.store.take_ready_notices(GUILD, self.campaign.id), {})

    async def test_the_bot_posts_the_notice_once_with_the_button(self) -> None:
        await self.read()
        await self.confirm_all()
        campaign = dataclasses.replace(self.campaign, dm_screen_channel_id=555)
        self.bot.campaigns.get = AsyncMock(return_value=campaign)  # type: ignore[method-assign]
        self.bot.post = AsyncMock(return_value=True)  # type: ignore[method-assign]
        guilds = PropertyMock(return_value=[SimpleNamespace(id=GUILD)])
        with (
            patch.object(DMBot, "guilds", guilds),
            patch("dmbot.bot.LINKS_READY_SETTLE_S", 0),
        ):
            await self.bot._links_ready_look(self.campaign.id)
            await self.bot._links_ready_look(self.campaign.id)
        self.bot.post.assert_awaited_once()
        channel, text, view = self.bot.post.await_args.args
        self.assertEqual(channel, 555)
        self.assertIn("2 connections from your background", text)
        self.assertEqual(view.children[0].item.label, links_ready.LABEL)

    async def test_nothing_waiting_posts_nothing(self) -> None:
        campaign = dataclasses.replace(self.campaign, dm_screen_channel_id=555)
        self.bot.campaigns.get = AsyncMock(return_value=campaign)  # type: ignore[method-assign]
        self.bot.post = AsyncMock(return_value=True)  # type: ignore[method-assign]
        with (
            patch.object(DMBot, "guilds", PropertyMock(return_value=[SimpleNamespace(id=GUILD)])),
            patch("dmbot.bot.LINKS_READY_SETTLE_S", 0),
        ):
            await self.bot._links_ready_look(self.campaign.id)
        self.bot.post.assert_not_awaited()

    async def test_keep_all_keeps_only_the_upload_on_screen(self) -> None:
        await self.read()
        await self.confirm_all()
        await self.memory.add_names(
            GUILD,
            self.campaign.id,
            [NewName("Edda", "npc", "confirmed"), NewName("Holt", "npc", "confirmed")],
            source="dm",
        )
        ids = {e.name: e.id for e in await self.memory.entities(GUILD, self.campaign.id)}
        self.store._clock = lambda: time.time() + 100  # later than the reading: shown second
        await self.store.add(
            GUILD,
            self.campaign.id,
            [
                NewLink(
                    ids["Edda"], "kin_of", ids["Holt"], "Edda and Holt", upload="Other file, 2 Jan"
                )
            ],
        )
        _, view = await self.offer()
        mine = next(b for b in view.children if b.label.startswith("✅ Keep all"))
        self.assertEqual(mine.label, "✅ Keep all 2 from this backstory")
        await mine.callback(self.it())
        left = await self.waiting()
        self.assertEqual([w.upload for w in left], ["Other file, 2 Jan"])  # never kept unseen
        self.assertEqual(len(await self.relations()), 2)

    async def test_the_counts_for_the_check_are_read_only(self) -> None:
        await self.read()
        before = await self.store.counts(GUILD, self.campaign.id)
        self.assertEqual(sum(n for *_, n in before), 2)
        self.assertTrue(all(not ready for _, _, ready, _ in before))
        await self.confirm_all()
        after = await self.store.counts(GUILD, self.campaign.id)
        self.assertTrue(all(ready for _, _, ready, _ in after))
        self.assertEqual(len(await self.waiting()), 2)  # nothing was cleaned or moved
