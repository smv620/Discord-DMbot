"""The website's continuity heads-ups (#3695 half 1, migration 0606): only a DM of the campaign
reads them; the door works each heads-up's words out of the bot's pointers (`continuity_hits`) at
read time, as Discord's card does, and leaves out a heads-up whose claim was answered since, whose
speaker is no longer recorded, or another campaign's; the earlier quote is withheld while anyone who
said it stopped, the line itself still returned. The ask and the answer go on the queue in the
shapes the bot's applier takes. Postgres. Invented names."""

from __future__ import annotations

from typing import Any

from dmbot.consent import TERMS_VERSION
from dmbot.memory import web_actions as wa
from dmbot.memory.continuity_hits import ContinuityHits, Hit
from dmbot.memory.models import DEAD, DESTROYED
from dmbot.web import continuity, relationships
from dmbot.web.recaps import RecapError
from tests.test_recap_web import BOB
from tests.test_web_api import THURSDAY
from tests.test_web_relationships import Web

SAID = 4242  # a speaker (a Discord-shaped number made up for the test)
OTHER = 4343
EARLY = 100  # the session the earlier things were said in
LATE = 500  # the session being checked
KEYS = {
    "id", "kind", "level", "name", "now", "earlier", "earlierWords", "ended", "earlierSession",
    "quote", "pinned", "learner", "listed", "choice",
}  # fmt: skip


class ContinuityOnTheWeb(Web):
    bob_has_a_hero = False

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.hits = ContinuityHits(self.db)
        self.actions = wa.ActionStore(self.db)
        self.n = 0
        for user in (SAID, OTHER):
            await self.agree(user)
        self.bram = await self.add("Bram", "character")
        self.kesh = await self.add("Kesh", "character")
        self.bow = await self.add("Heartseeker", "item")
        await self.began(50)
        await self.began(EARLY)  # (Session 2: the transcripts' own numbering, by when it began)
        await self.began(LATE)

    async def began(self, started_at: int) -> None:
        self.n += 1
        await self.sql(
            "INSERT INTO transcript_sessions (id, guild_id, campaign_id, started_at, line_count)"
            " VALUES (%s, %s, %s, %s, 3)",
            f"{self.n:032x}", self.g, self.one.id, started_at,
        )  # fmt: skip

    async def agree(self, user: int, terms: int | None = None) -> None:
        await self.sql(
            "INSERT INTO consent (guild_id, user_id, granted_at, terms_version)"
            " VALUES (%s, %s, 0, %s) ON CONFLICT (guild_id, user_id)"
            " DO UPDATE SET terms_version = EXCLUDED.terms_version",
            self.g, user, TERMS_VERSION if terms is None else terms,
        )  # fmt: skip

    async def stop(self, user: int) -> None:
        await self.sql("DELETE FROM consent WHERE guild_id = %s AND user_id = %s", self.g, user)

    async def claim(
        self,
        subject: str,
        relationship: str,
        obj: str,
        *,
        speakers: tuple[int, ...] = (SAID,),
        status: str = "proposed",
        fact: str | None = None,
        session: int = LATE,
        details: str = "",
        campaign: str | None = None,
        cid: str | None = None,
    ) -> str:
        """A claim row as the extractor leaves it (the words and who said them, no line)."""
        self.n += 1
        cid = cid or f"{self.n + 1000:032x}"
        await self.sql(
            "INSERT INTO memory_claims (guild_id, campaign_id, id, subject, relationship, object,"
            " details, how, lines, speaker_ids, source, prompt_version, model, status,"
            " session_started_at, created_at, fact_id) VALUES (%s, %s, %s, %s, %s, %s, %s,"
            " 'dm_said', %s, %s, 'x', 'p', 'm', %s, %s, %s, %s)",
            self.g, campaign or self.one.id, cid, subject, relationship, obj, details,
            ["1"], list(speakers), status, session, self.n, fact,
        )  # fmt: skip
        return cid

    async def earlier_fact(self, speakers: tuple[int, ...] = (OTHER,)) -> tuple[str, str]:
        """ "Bram carries Heartseeker", confirmed in Session 2 and said by `speakers`."""
        fact = await self.link("carries", self.bram, self.bow, f"carries{self.n}")
        said = await self.claim(
            "Bram", "carries", "Heartseeker", speakers=speakers, status="confirmed", fact=fact,
            session=EARLY,
        )  # fmt: skip
        return fact, said

    def up(self, claim: str, entity: str | None = None, **kw: Any) -> Hit:
        return Hit(
            claim, kw.get("kind", "held_twice"), kw.get("level", "probable"),
            entity or self.kesh, kw.get("earlier"), kw.get("session"), kw.get("learner"),
            kw.get("pinned", False),
        )  # fmt: skip

    async def hold(self, *hits: Hit, campaign: str | None = None) -> None:
        await self.hits.replace(self.g, campaign or self.one.id, list(hits))

    async def read(self, who: Any = None, ref: str | None = None) -> dict[str, Any]:
        return await continuity.read(
            self.web, who or self.session, ref or self.cref, outside_engine=None
        )

    async def turned_away(self, who: Any, ref: str | None = None) -> str:
        with self.assertRaises(RecapError) as err:
            await self.read(who, ref)
        return str(err.exception.code)

    async def taken(self) -> list[Any]:
        found = []
        while (a := await self.actions.claim_next(self.g)) is not None:
            found.append(a)
            await self.actions.finish(self.g, a)
        return found

    # The door

    async def test_a_dm_reads_the_hit_worked_out_in_words_not_sentences(self) -> None:
        fact, _said = await self.earlier_fact()
        mine = await self.claim("Kesh", "carries", "Heartseeker", details="in the cellar")
        await self.hold(self.up(mine, earlier=fact, session=EARLY))
        got = await self.read()
        self.assertEqual(got["total"], 1)
        (hit,) = got["hits"]
        self.assertEqual(set(hit), KEYS)
        self.assertEqual(
            hit,
            {
                "id": mine, "kind": "held_twice", "level": "probable", "name": "Kesh",
                "now": {
                    "subject": "Kesh", "relationship": "carries", "object": "Heartseeker",
                    "details": "in the cellar",
                },
                "earlier": {
                    "subject": "Bram", "predicate": "carries", "detail": "",
                    "object": "Heartseeker", "label": None,
                },
                "earlierWords": "Bram carries Heartseeker",
                "ended": None, "earlierSession": 2, "quote": "Bram carries Heartseeker",
                "pinned": False, "learner": None, "listed": None, "choice": "story_changed",
            },
        )  # fmt: skip

    async def test_a_pinned_hit_keeps_its_pin_and_a_campaign_s_own_kind_its_label(self) -> None:
        await self.sql(
            "INSERT INTO memory_predicates (guild_id, campaign_id, key, label, description,"
            " examples, reason, subject_types, object_types, is_symmetric, conflicts_with, status,"
            " created_at) VALUES (%s, %s, 'guards', 'keeps watch over', 'd', '{}', 'r', '{}', '{}',"
            " false, '{}', 'active', 1)",
            self.g, self.one.id,
        )  # fmt: skip
        fact = await self.link("guards", self.bram, self.bow, "guards")
        mine = await self.claim("Kesh", "guards", "Heartseeker")
        await self.hold(self.up(mine, earlier=fact, kind="contradiction", pinned=True))
        (hit,) = (await self.read())["hits"]
        self.assertTrue(hit["pinned"])
        self.assertEqual(hit["earlier"]["label"], "keeps watch over")
        self.assertTrue(hit["earlierWords"].startswith("Bram keeps watch over "))
        self.assertIsNone(hit["quote"])  # (no claim said the earlier fact: nothing to quote)

    async def test_a_player_a_stranger_and_another_campaigns_dm_get_not_allowed(self) -> None:
        mine = await self.claim("Kesh", "carries", "Heartseeker")
        await self.hold(self.up(mine))
        for who in (self.player, await self.signed(BOB, THURSDAY)):
            self.assertEqual(await self.turned_away(who), "not_allowed")
            for call in (
                continuity.ask(self.web, who, self.cref),
                continuity.answer(self.web, who, self.cref, {"claim": mine, "choice": "not"}),
            ):
                with self.assertRaises(RecapError) as err:
                    await call
                self.assertEqual(err.exception.code, "not_allowed")
        self.assertEqual(
            await self.turned_away(self.session, f"{self.g}.{self.two.id}"), "not_allowed"
        )
        self.assertEqual(await self.taken(), [])

    async def test_a_hit_whose_own_claim_has_a_speaker_who_stopped_is_not_returned(self) -> None:
        both = await self.claim("Kesh", "carries", "Heartseeker", speakers=(SAID, OTHER))
        alone = await self.claim("Kesh", "owns", "Heartseeker", speakers=(SAID,))
        await self.hold(self.up(both), self.up(alone, kind="contradiction"))
        self.assertEqual((await self.read())["total"], 2)
        await self.stop(OTHER)
        got = await self.read()
        self.assertEqual((got["total"], [h["id"] for h in got["hits"]]), (1, [alone]))
        await self.agree(OTHER, terms=TERMS_VERSION + 1)  # (agreed to older words: not recorded)
        self.assertEqual((await self.read())["total"], 1)

    async def test_the_earlier_words_and_quote_are_held_back_when_its_speaker_stopped(self) -> None:
        fact, _said = await self.earlier_fact(speakers=(OTHER,))
        mine = await self.claim("Kesh", "carries", "Heartseeker", speakers=(SAID,))
        await self.hold(self.up(mine, earlier=fact, session=EARLY))
        self.assertEqual((await self.read())["hits"][0]["quote"], "Bram carries Heartseeker")
        await self.stop(OTHER)
        (hit,) = (await self.read())["hits"]  # (the heads-up is still there, in DMbot's own words)
        self.assertIsNone(hit["quote"])
        self.assertEqual(hit["earlier"]["subject"], "Bram")
        self.assertEqual(hit["earlierSession"], 2)

    async def test_a_hit_answered_since_the_ask_is_not_returned(self) -> None:
        one = await self.claim("Kesh", "carries", "Heartseeker")
        two = await self.claim("Kesh", "owns", "Heartseeker")
        three = await self.claim("Kesh", "serves", "Heartseeker")
        await self.hold(self.up(one), self.up(two), self.up(three))
        await self.sql("UPDATE memory_claims SET status = 'confirmed' WHERE id = %s", one)
        await self.sql("UPDATE memory_claims SET status = 'rejected' WHERE id = %s", two)
        got = await self.read()
        self.assertEqual((got["total"], [h["id"] for h in got["hits"]]), (1, [three]))
        await self.sql("DELETE FROM memory_claims WHERE id = %s", three)  # (wiped: gone as well)
        self.assertEqual((await self.read())["hits"], [])

    async def test_another_campaigns_rows_never_leak(self) -> None:
        mine = await self.claim("Kesh", "carries", "Heartseeker")
        theirs = await self.claim("Zed", "carries", "Axe", campaign=self.two.id)
        await self.hold(self.up(mine))
        await self.hold(self.up(theirs, self.kesh), campaign=self.two.id)
        self.assertEqual([h["id"] for h in (await self.read())["hits"]], [mine])
        got = await self.read(await self.signed(BOB, THURSDAY), f"{self.g}.{self.two.id}")
        self.assertEqual([h["id"] for h in got["hits"]], [theirs])
        # a hit pointing at another campaign's claim finds no claim of this one
        await self.hold(self.up(theirs))
        self.assertEqual((await self.read())["hits"], [])

    async def test_the_tables_order_is_kept_and_the_page_is_bounded_with_the_total(self) -> None:
        ids = [await self.claim("Kesh", "carries", f"Thing {n:02d}") for n in range(53)]
        order = list(reversed(ids))  # (not the claims' own order: only the table's counts)
        await self.hold(*(self.up(i, kind=f"k{n}") for n, i in enumerate(order)))
        kept = await self.hits.listed(self.g, self.one.id)
        got = await self.read()
        self.assertEqual(got["total"], 53)
        self.assertEqual(len(got["hits"]), 50)
        self.assertEqual([h["id"] for h in got["hits"]], [h.claim_id for h in kept][:50])
        self.assertEqual([h["id"] for h in got["hits"]], order[:50])

    async def test_no_line_number_speaker_or_session_id_is_returned(self) -> None:
        fact, _said = await self.earlier_fact()
        mine = await self.claim("Kesh", "carries", "Heartseeker")
        await self.hold(self.up(mine, earlier=fact, session=EARLY))
        got = await self.read()
        self.assertEqual(set(got), {"total", "hits"})
        self.assertEqual(set(got["hits"][0]), KEYS)
        text = str(got)
        for word in ("lines", "speaker", str(SAID), str(OTHER), "session_id", f"{1:032x}"):
            self.assertNotIn(word, text)

    async def test_a_death_reads_the_entrys_own_ending_and_picks_the_button(self) -> None:
        ulfgar = await self.add("Ulfgar", "character")
        tower = await self.add("Old Tower", "place")
        await self.memory.set_condition(
            self.g, self.one.id, ulfgar, DEAD, source="dm", from_session_at=EARLY
        )
        await self.memory.set_condition(
            self.g, self.one.id, tower, DESTROYED, source="dm", from_session_at=EARLY
        )
        await self.claim(
            "Ulfgar", "is", "dead", status="confirmed", fact=ulfgar, speakers=(OTHER,),
            session=EARLY,
        )  # fmt: skip
        walks = await self.claim("Ulfgar", "carries", "Heartseeker")
        stands = await self.claim("Old Tower", "is", "standing")
        await self.hold(
            self.up(walks, ulfgar, kind="acts_after_end"),
            self.up(stands, tower, kind="back_from_the_end", level="likely"),
        )
        first, second = (await self.read())["hits"]
        self.assertEqual(
            (first["name"], first["ended"], first["earlier"], first["earlierSession"]),
            ("Ulfgar", "dead", None, 2),
        )
        self.assertEqual((first["quote"], first["choice"]), ("Ulfgar is dead", "alive_after_all"))
        self.assertEqual(
            (second["ended"], second["choice"], second["quote"]), ("destroyed", "still_here", None)
        )
        # Ulfgar is alive again (answered elsewhere): a fresh check would not raise it, so the
        # stale line is left out and not counted
        await self.memory.set_condition(
            self.g, self.one.id, ulfgar, "alive", source="dm", from_session_at=LATE
        )
        got = await self.read()
        self.assertEqual((got["total"], [h["name"] for h in got["hits"]]), (1, ["Old Tower"]))

    async def test_a_secret_clue_names_who_learned_it_and_who_is_on_its_list(self) -> None:
        clue = await self.add("Moon Ledger", "item")
        await self.link("knows", self.bram, clue, "bram-ledger")
        await self.link("knows", self.rook, clue, "rook-ledger")
        mine = await self.claim("Kesh", "mentions", "Moon Ledger")
        await self.hold(
            self.up(mine, clue, kind="knows_a_secret", level="possible", learner=self.kesh)
        )
        (hit,) = (await self.read())["hits"]
        self.assertEqual(
            (hit["name"], hit["learner"], hit["listed"], hit["earlier"], hit["choice"]),
            ("Moon Ledger", "Kesh", ["Bram", "Rook"], None, "they_learned_it"),
        )

    # The queue

    async def test_the_ask_and_the_answer_go_on_the_queue_in_the_shape_the_bot_takes(self) -> None:
        claim = "a" * 32
        await continuity.ask(self.web, self.session, self.cref)
        await continuity.answer(
            self.web, self.session, self.cref, {"claim": claim, "choice": "changed"}
        )
        await continuity.answer(
            self.web, self.session, self.cref, {"claim": claim, "choice": "not"}
        )
        found = await self.taken()
        self.assertEqual(
            [(a.action, a.text) for a in found],
            [
                ("continuity-ask", ""),
                ("continuity-answer", f"{claim} c"),
                ("continuity-answer", f"{claim} n"),
            ],
        )

    async def test_a_malformed_answer_is_refused_before_anything_is_queued(self) -> None:
        good = "a" * 32
        bodies: list[object] = [
            5, None, {}, {"choice": "not"}, {"claim": good}, {"claim": good, "choice": "ignore"},
            {"claim": good, "choice": "c"}, {"claim": "A" * 32, "choice": "not"},
            {"claim": "a" * 31, "choice": "not"}, {"claim": 7, "choice": "not"},
            {"claim": f"{good} c", "choice": "not"},
        ]  # fmt: skip
        for body in bodies:
            with self.assertRaises(RecapError) as err:
                await continuity.answer(self.web, self.session, self.cref, body)
            self.assertEqual(err.exception.code, "bad_action", body)
        self.assertEqual(await self.taken(), [])

    async def test_the_answers_read_back_as_counts_and_codes_never_words(self) -> None:
        asked = await continuity.ask(self.web, self.session, self.cref)
        answered = await continuity.answer(
            self.web, self.session, self.cref, {"claim": "b" * 32, "choice": "changed"}
        )
        first = await self.actions.claim_next(self.g)
        assert first is not None
        await self.actions.finish(self.g, first, result_counts="n:3")
        second = await self.actions.claim_next(self.g)
        assert second is not None
        await self.actions.finish(self.g, second, error=wa.GONE)
        read = await relationships.read_answer(self.web, self.session, self.cref, asked)
        self.assertEqual(read, {"state": "done", "error": "", "count": 3})
        read = await relationships.read_answer(self.web, self.session, self.cref, answered)
        self.assertEqual((read["state"], read["error"], read["count"]), ("failed", "gone", None))
