"""What made a waiting name a name, on the website's name card (#5144, migration 0616): how it was
heard, its other spellings, DMbot's guess and the one line that made it a name (never for a speaker
who no longer agrees), in Discord's words and order. Invented names throughout."""

from __future__ import annotations

import unittest
from typing import Any

from dmbot.consent import TERMS_VERSION
from dmbot.memory import name_words
from dmbot.memory.name_found import NameFound
from dmbot.recaps import name_answers as na
from dmbot.transcript.models import Line, line_ref
from dmbot.transcript.store import TranscriptStore
from dmbot.web import name_checks as web_checks
from tests.test_web_api import ALICE
from tests.test_web_name_checks import AT, GUILD, WithChecks


class TheFoundCard(WithChecks):
    async def line_for(self, speaker: int, text: str) -> str:
        sid = await TranscriptStore(self.db).open_session(GUILD, self.one.id, AT)
        line = Line(4000, speaker, text, text)
        await TranscriptStore(self.db).add_lines(GUILD, sid, [line])
        return line_ref(sid, line.started_ms, speaker)

    async def agree(
        self,
        *users: int,
        leave: tuple[int, ...] = (),
        terms: int = TERMS_VERSION,
        outside: str | None = None,
    ) -> None:
        async with self.db.guild(GUILD) as conn:
            for user in users:
                await conn.execute(
                    "INSERT INTO consent (guild_id, user_id, granted_at, terms_version, outside_to)"
                    " VALUES (%s, %s, 0, %s, %s) ON CONFLICT DO NOTHING",
                    (GUILD, user, terms, outside),
                )
            for user in leave:
                await conn.execute(
                    "DELETE FROM consent WHERE guild_id = %s AND user_id = %s", (GUILD, user)
                )

    async def card(self, *, outside: str | None = None) -> dict[str, Any]:
        got = await web_checks.read_checks(self.web, self.session, self.ref, outside_engine=outside)
        (card,) = got["names"]
        return dict(card)

    async def one_line(self) -> str:
        harrow = await self.suggest("Harrow")
        ref = await self.line_for(ALICE.id, "We rode to Harrow at dawn")
        await self.keep(harrow, "introduced", ref, times=2)
        return harrow

    async def keep(
        self, entity: str, how: str, ref: str | None, *, times: int, typed: int = 0
    ) -> None:
        await NameFound(self.db).keep(
            GUILD, self.one.id, entity, how, ref, times=times, typed=typed
        )

    async def test_a_found_name_carries_how_it_was_heard_and_the_line_that_made_it(self) -> None:
        harrow = await self.suggest("Harrow")
        await self.memory.add_alias(
            GUILD, self.one.id, harrow, "Haro", kind="misheard", source="scan"
        )
        ref = await self.line_for(ALICE.id, "We rode to Harrow at dawn")
        await self.keep(harrow, "introduced", ref, times=5)
        await self.agree(ALICE.id)
        (card,) = (await self.listed())["names"]
        found = card["found"]
        self.assertEqual((found["how"], found["times"], found["typed"]), ("introduced", 5, 0))
        self.assertEqual(found["also"], ["Haro"])
        self.assertEqual(found["heard"], "Heard 5 times, sometimes as Haro.")
        self.assertEqual(found["heard"], name_words.heard_line(5, 0, ["Haro"]))
        self.assertIsNone(found["guess"])  # (a concept has no kind to guess)
        self.assertEqual(found["line"]["text"], "We rode to Harrow at dawn")
        self.assertIn("speaker", found["line"])

    async def test_a_speaker_who_no_longer_agrees_leaves_the_line_out_and_nothing_in_its_place(
        self,
    ) -> None:
        harrow = await self.suggest("Harrow")
        ref = await self.line_for(ALICE.id, "We rode to Harrow at dawn")
        await self.keep(harrow, "typed", ref, times=2, typed=1)
        await self.agree(ALICE.id, leave=(ALICE.id,))
        (card,) = (await self.listed())["names"]
        self.assertIsNone(card["found"]["line"])
        self.assertEqual(card["found"]["heard"], "Typed once in chat, and heard once.")
        self.assertNotIn("We rode", repr(card))

    async def test_a_name_with_no_found_row_is_todays_card_and_the_sure_ones_come_first(
        self,
    ) -> None:
        plain = await self.suggest("Aaron")
        scanned = await self.suggest("Bree")
        sure = await self.suggest("Cael")
        await self.keep(scanned, "scan", None, times=9)
        await self.keep(sure, "introduced", None, times=1)
        names = (await self.listed())["names"]
        self.assertEqual([n["name"] for n in names], ["Cael", "Bree", "Aaron"])
        self.assertNotIn("found", names[2])
        self.assertEqual(names[2]["id"], plain)

    async def test_the_guess_is_the_kind_it_was_given_when_it_was_read(self) -> None:
        place = await self.memory.add_entity(
            GUILD, self.one.id, type="place", name="Thornwick", source="scan", description="x"
        )
        await self.answers.link(GUILD, self.one.id, AT, [place.value.id])
        await self.keep(place.value.id, "place", None, times=3)
        (card,) = (await self.listed())["names"]
        self.assertEqual(card["found"]["guess"], "place")

    async def test_the_line_follows_the_bots_consent_rule_not_any_consent_row(self) -> None:
        """Current wording, and for the outside company while one hears the voices
        (`consent._select`, as the card's `first_places` reads it)."""
        await self.one_line()
        await self.agree(ALICE.id, terms=TERMS_VERSION - 1)  # (agreed to older wording only)
        self.assertIsNone((await self.card())["found"]["line"])

    async def test_an_outside_company_that_is_not_the_one_agreed_to_shows_no_words(self) -> None:
        await self.one_line()
        await self.agree(ALICE.id, outside="company-a")
        self.assertIsNone((await self.card(outside="company-b"))["found"]["line"])
        shown = (await self.card(outside="company-a"))["found"]["line"]
        self.assertEqual(shown["text"], "We rode to Harrow at dawn")

    async def test_a_speaker_who_only_confirmed_an_import_or_who_refused_shows_no_words(
        self,
    ) -> None:
        """The card reads `consent.consenting` for this line, so an import confirmation alone does
        not show it, and a refusal on record never does: the two places agree."""
        await self.one_line()
        async with self.db.guild(GUILD) as conn:
            await conn.execute(
                "INSERT INTO import_consents (guild_id, campaign_id, id, import_id, user_id, basis,"
                " confirmed_by, confirmed_at, terms_version) VALUES (%s, %s, %s, 'i1', %s,"
                " 'uploader_confirmed', %s, 0, %s)",
                (GUILD, self.one.id, "a" * 32, ALICE.id, ALICE.id, TERMS_VERSION),
            )
        self.assertIsNone((await self.card())["found"]["line"])
        async with self.db.guild(GUILD) as conn:
            await conn.execute(
                "INSERT INTO consent_refusals (guild_id, user_id, kind, refused_at)"
                " VALUES (%s, %s, 'declined', 0)",
                (GUILD, ALICE.id),
            )
        self.assertIsNone((await self.card())["found"]["line"])


class TheWords(unittest.TestCase):
    """`name_words` is what both cards share: the same words and order."""

    def test_the_heard_line_is_discords(self) -> None:
        also = ["Rothgar", "hroth gar", "Rothgard", "Rotgar"]
        self.assertEqual(
            name_words.heard_line(5, 0, also),
            "Heard 5 times, sometimes as Rothgar, hroth gar or Rothgard and 1 more.",
        )
        self.assertEqual(name_words.heard_line(1, 0, []), "Heard once.")
        self.assertEqual(
            name_words.heard_line(3, 1, ["Rothgar"], mark=lambda a: f"*{a}*"),
            "Typed once in chat, and heard 2 times, sometimes as *Rothgar*.",
        )
        self.assertEqual(
            name_words.heard_line(2, 0, [], written=True), "Found 2 times in your text."
        )


class FakeConsent:
    """Who counts as recorded: only the people given (the bot's `Consent.recorded_in`)."""

    def __init__(self, *people: int) -> None:
        self.people = frozenset(people)

    async def recorded_in(self, guild_id: int, campaign_id: str) -> frozenset[int]:
        return self.people


class TheYes(TheFoundCard):
    """The yes on a name with a found row (#5144 slice B): the other spellings heard become rules,
    this session's transcript follows, one Undo takes it all back, and the poll says so."""

    async def start(self) -> tuple[str, str]:
        # (a table with a player character: the gate holds a new entry for a party that exists, and
        # an empty party sees only common knowledge, so the lines would be "later")
        await self.memory.add_entity(
            GUILD, self.one.id, type="character", name="Mira", source="dm",
            role="player_character", status="confirmed",
        )  # fmt: skip
        harrow = await self.suggest("Hrothgar Hall")
        await self.memory.add_alias(
            GUILD, self.one.id, harrow, "Rothgar Hall", kind="misheard", source="scan"
        )
        sid = await TranscriptStore(self.db).open_session(GUILD, self.one.id, AT)
        line = Line(4000, ALICE.id, "We rode to Rothgar Hall at dawn", "We rode to Rothgar Hall")
        await TranscriptStore(self.db).add_lines(GUILD, sid, [line])
        await self.keep(harrow, "introduced", line_ref(sid, line.started_ms, ALICE.id), times=3)
        return harrow, sid

    async def yes(self, harrow: str) -> tuple[str, na.Kept]:
        await self.ask(harrow, action="confirm", kind="place")
        answer = await self.answers.claim_next(GUILD)
        assert answer is not None
        kept = na.Kept()
        tools = na.HeardTools(TranscriptStore(self.db), FakeConsent(ALICE.id))
        why = await na.apply(self.memory, GUILD, {ALICE.id}, answer, kept, self.answers, tools)
        await self.answers.finish(GUILD, answer, error=why, kept=kept if kept.batch else None)
        return why, kept

    async def test_a_yes_makes_rules_fixes_the_session_and_says_so(self) -> None:
        harrow, sid = await self.start()
        why, kept = await self.yes(harrow)
        self.assertEqual(why, "")
        assert kept.batch is not None
        parsed = na.read_yes_note(kept.note)
        assert parsed is not None
        self.assertEqual(parsed[0:3], ("fixed", "place", "Hrothgar Hall"))
        self.assertEqual(parsed[3], ["Rothgar Hall"])
        read = await web_checks.read_answer(self.web, self.session, self.ref, harrow)
        self.assertEqual(read["state"], "done")
        self.assertEqual(read["batch"], kept.batch)
        self.assertEqual(read["fixed"], "fixed")
        self.assertEqual(read["rules"], ["Rothgar Hall"])
        self.assertIn("now reads as Hrothgar Hall, in this session's transcript", read["note"])
        lines = await TranscriptStore(self.db).lines(GUILD, sid)
        self.assertIn("Hrothgar Hall", lines[0].text)

    async def test_the_undo_puts_the_name_the_lines_and_what_made_it_a_name_back(self) -> None:
        harrow, sid = await self.start()
        _why, kept = await self.yes(harrow)
        await self.ask(harrow, action="undo", batch=kept.batch)
        answer = await self.answers.claim_next(GUILD)
        assert answer is not None
        tools = na.HeardTools(TranscriptStore(self.db), FakeConsent(ALICE.id))
        why = await na.apply(self.memory, GUILD, {ALICE.id}, answer, None, self.answers, tools)
        await self.answers.finish(GUILD, answer, error=why)
        self.assertEqual(why, "")
        entity = await self.memory.entity(GUILD, self.one.id, harrow)
        assert entity is not None
        self.assertEqual(entity.status, "proposed")
        lines = await TranscriptStore(self.db).lines(GUILD, sid)
        self.assertIn("Rothgar Hall", lines[0].text)
        found = (await self.memory.name_found(GUILD, self.one.id, [harrow])).get(harrow)
        assert found is not None
        self.assertEqual((found.how, found.times), ("introduced", 3))
        (card,) = (await self.listed())["names"]
        self.assertEqual(card["found"]["how"], "introduced")

    async def test_a_name_with_no_found_row_keeps_todays_yes(self) -> None:
        plain = await self.suggest("Aaron")
        await self.ask(plain, action="confirm", kind="place")
        answer = await self.answers.claim_next(GUILD)
        assert answer is not None
        kept = na.Kept()
        tools = na.HeardTools(TranscriptStore(self.db), FakeConsent(ALICE.id))
        why = await na.apply(self.memory, GUILD, {ALICE.id}, answer, kept, self.answers, tools)
        self.assertEqual((why, kept.batch), ("", None))
