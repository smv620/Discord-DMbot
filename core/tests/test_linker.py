"""The linker (#1364): a name in a claim that DMbot does not know yet becomes a *proposed* entry
for the DM instead of costing the claim. The matching rules need no database; the rest runs
against Postgres with no AI anywhere."""

from __future__ import annotations

import unittest
from typing import Any

from dmbot.memory import link
from dmbot.memory.claims import (
    NAME_FIRST,
    NAME_REJECTED_REASON,
    SECRET_NAME_REASON,
    ClaimStore,
    Extracted,
    Proposed,
)
from dmbot.memory.models import CONFIRMED, REJECTED, MemoryRuleError
from dmbot.memory.models import DM as DM_SOURCE
from dmbot.memory.store import PRONOUNS_KEPT_OUT_FROM
from tests.test_memory_store import DM, GUILD_A, GUILD_B, ConfirmedMemoryTest

EVERYONE = {DM, 8}
THE_DM, PLAYER = DM, 8  # DM is the user id the memory tests use for the campaign's DM


# Far enough apart that the linker never takes one for another spelling of the next.
STRANGERS = [
    "Aldric",
    "Brenna",
    "Cormac",
    "Delia",
    "Evander",
    "Fenwick",
    "Gwendolyn",
    "Hadrian",
    "Isolde",
    "Jasper",
    "Kestrel",
    "Lysander",
    "Marigold",
    "Nestor",
    "Ophelia",
    "Percival",
    "Quentin",
    "Rosalind",
    "Sylvester",
    "Thaddeus",
    "Ursula",
    "Valentin",
    "Winifred",
    "Xanthe",
    "Yorick",
]


def said(subject: str, relationship: str, obj: str, *lines: str) -> Extracted:
    return Extracted(subject, relationship, obj, "", lines or ("1",), "dm_said")


class Matching(unittest.TestCase):
    def test_short_names_are_never_matched_loosely(self) -> None:
        self.assertEqual(link.allowed_distance("bryn"), 0)
        self.assertIsNone(link.alike("bran", {"bryn": "a"}))  # two people
        self.assertEqual(link.allowed_distance("bryns"), 1)
        self.assertEqual(link.allowed_distance("bryn shander"), 1)

    def test_two_things_that_differ_by_a_letter_or_a_number_are_not_one_name(self) -> None:
        for a, b in (
            ("mira stone", "kira stone"),  # two people
            ("room 12", "room 13"),
            ("goblin cave a", "goblin cave b"),
            ("bryn shander", "bryn"),
        ):
            self.assertFalse(link.same_name(a, b), (a, b))
        self.assertTrue(link.same_name("oskar vance", "oskar vence"))
        self.assertTrue(link.same_name("greywater keep", "greywatar keep"))

    def test_the_distance_counts_single_letter_changes(self) -> None:
        self.assertTrue(link.within("oskar vane", "oskar vana", 1))
        self.assertTrue(link.within("oskar vane", "oskar van", 1))  # a letter dropped
        self.assertFalse(link.within("oskar vane", "oskar vaine", 0))
        self.assertFalse(link.within("oskar vane", "oskar vana", 0))
        self.assertFalse(link.within("oskar vane", "oskar vain", 1))  # two letters
        self.assertFalse(link.within("abc", "abcdefg", 2))  # too far apart in length at once

    def test_one_near_entry_is_a_match_and_two_are_not(self) -> None:
        self.assertEqual(link.alike("oskar vance", {"oskar vence": "a", "mira": "b"}), "a")
        self.assertIsNone(link.alike("oskar vance", {"oskar vence": "a", "oskar vanca": "b"}))
        # Two names of one entry are still one entry.
        self.assertEqual(link.alike("oskar vance", {"oskar vence": "a", "oskar vancy": "a"}), "a")
        self.assertIsNone(link.alike("oskar vance", {"mira": "b"}))

    def test_the_most_said_names_come_first_and_the_rest_are_counted(self) -> None:
        said_keys = ["a", "b", "b", "c", "c", "c", "d"]
        self.assertEqual(link.most_mentioned(said_keys, 2), (["c", "b"], 2))
        self.assertEqual(link.most_mentioned(["x", "y"], 5), (["x", "y"], 0))

    def test_a_name_said_two_ways_is_one_group(self) -> None:
        groups = link.spellings(["oskar vance", "mira", "oskar vence", "kira"])
        self.assertEqual(groups, {"oskar vance": ["oskar vence"], "mira": [], "kira": []})


class DmScreenText(unittest.TestCase):
    def test_the_post_says_why_some_of_what_was_said_is_not_asked_yet(self) -> None:
        from dmbot.ui.names import held_back_text

        text = held_back_text(["Mother Ilse", "Oskar Vance"])
        self.assertIn("2 new names to check", text)
        self.assertIn("**Mother Ilse**", text)
        self.assertIn("held back until you check them", text)
        self.assertNotIn("claim", text.lower())  # plain words: "what was said"


class Linking(ConfirmedMemoryTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.claims = ClaimStore(self.memory)
        self.bram = await self.add("Bram", type="character")
        self.town = await self.add("Veldmoor", type="place")

    async def propose(
        self,
        *found: Extracted,
        campaign: str | None = None,
        guild: int = GUILD_A,
        speakers: dict[str, int | None] | None = None,
        **kw: Any,
    ) -> Proposed:
        return await self.claims.propose(
            guild,
            campaign or self.c,
            list(found),
            speakers or {"1": PLAYER, "2": THE_DM},
            opted_in=EVERYONE,
            prompt_version="p1",
            model="m1",
            session_started_at=500,
            dms=kw.pop("dms", {THE_DM}),
            **kw,
        )

    async def entries(self, campaign: str | None = None, guild: int = GUILD_A) -> dict[str, Any]:
        found = await self.memory.entities(guild, campaign or self.c, statuses=("proposed",))
        return {e.name: e for e in found}

    async def counts(self) -> dict[str, int]:
        return await self.claims.counts(GUILD_A, self.c, opted_in=EVERYONE)

    # ---- a pronoun is not a name (#2358) ------------------------------------------------------

    async def test_a_pronoun_in_a_claim_is_never_proposed_as_a_name(self) -> None:
        got = await self.propose(said("Bram", "knows", "He"), said("Bram", "knows", "I'll"))
        self.assertEqual((got.named, got.new), (0, ()))
        self.assertNotIn("He", await self.entries())

    async def test_a_cited_line_that_names_the_word_lets_it_through(self) -> None:
        self.memory._clock = lambda: PRONOUNS_KEPT_OUT_FROM + 1  # the old-suggestion filter is off
        got = await self.propose(
            said("Bram", "knows", "We", "1"), **{"said": {"1": "My name is We"}}
        )
        self.assertEqual(got.new, ("We",))
        self.assertIn("We", await self.entries())

    async def test_a_waiting_claim_for_a_pronoun_is_dropped_when_it_is_read_again(self) -> None:
        kept = await self.propose(
            said("Bram", "knows", "We", "1"), new_names=0, **{"said": {"1": "My name is We"}}
        )
        self.assertEqual((kept.named, kept.deferred), (0, 1))  # kept waiting, no name made
        again = await self.propose(said("Bram", "knows", "Veldmoor"))  # nothing cites a cue now
        self.assertEqual(again.named, 0)
        self.assertNotIn("We", await self.entries())

    async def test_suggestions_made_before_the_filter_are_not_offered_again(self) -> None:
        self.memory._clock = lambda: PRONOUNS_KEPT_OUT_FROM - 100
        for name in ("We", "I'll"):
            await self.memory.add_entity(
                GUILD_A, self.c, type="concept", name=name, source="extractor"
            )
        await self.memory.add_entity(
            GUILD_A, self.c, type="character", name="Oskar", source="extractor"
        )
        self.assertEqual(sorted(await self.entries()), ["Oskar"])
        self.memory._clock = lambda: PRONOUNS_KEPT_OUT_FROM + 100
        await self.memory.add_entity(GUILD_A, self.c, type="concept", name="Us", source="extractor")
        self.assertEqual(sorted(await self.entries()), ["Oskar", "Us"])  # new ones were gated

    # ---- a new name ------------------------------------------------------------------------

    async def test_an_exact_name_makes_nothing_new(self) -> None:
        got = await self.propose(said("Bram", "knows", "Veldmoor"))
        self.assertEqual((got.waiting, got.named, got.new), (1, 0, ()))
        self.assertEqual(await self.entries(), {})

    async def test_a_new_name_is_proposed_for_the_dm_and_its_claim_waits_for_it(self) -> None:
        got = await self.propose(said("Bram", "knows", "Mother Ilse"))
        self.assertEqual(
            (got.waiting, got.dropped, got.named, got.new), (1, 0, 1, ("Mother Ilse",))
        )
        ilse = (await self.entries())["Mother Ilse"]
        self.assertEqual(
            (ilse.status, ilse.type, ilse.source), ("proposed", "concept", "extractor")
        )
        # Not asked yet: the DM has to say who Mother Ilse is first.
        self.assertEqual(await self.claims.waiting(GUILD_A, self.c, opted_in=EVERYONE), [])
        self.assertEqual(await self.counts(), {"names": 1})
        # Nothing was confirmed on the DM's behalf.
        self.assertEqual(await self.memory.relations(GUILD_A, self.c), [])

    async def test_once_the_dm_says_yes_the_claim_is_asked_and_can_be_confirmed(self) -> None:
        await self.propose(said("Bram", "knows", "Mother Ilse"))
        ilse = (await self.entries())["Mother Ilse"]
        await self.memory.set_entity_type(GUILD_A, self.c, ilse.id, "character", source=DM_SOURCE)
        await self.memory.set_entity_status(GUILD_A, self.c, ilse.id, CONFIRMED, source=DM_SOURCE)
        (ask,) = await self.claims.waiting(GUILD_A, self.c, opted_in=EVERYONE)
        self.assertEqual(await self.counts(), {"proposed": 1})
        done = await self.claims.confirm(GUILD_A, self.c, ask.id, opted_in={PLAYER})
        self.assertEqual(done.claim.status, "confirmed")
        (fact,) = await self.memory.relations(GUILD_A, self.c)
        self.assertEqual((fact.status, fact.source), ("confirmed", "dm"))

    async def test_a_claim_cannot_be_confirmed_while_its_name_is_still_waiting(self) -> None:
        await self.propose(said("Bram", "knows", "Mother Ilse"))
        (row,) = [c for c in await self._all() if c.object == "Mother Ilse"]
        with self.assertRaises(MemoryRuleError) as raised:
            await self.claims.confirm(GUILD_A, self.c, row.id, opted_in={PLAYER})
        self.assertEqual(str(raised.exception), NAME_FIRST)

    async def _all(self) -> list[Any]:
        async with self.memory._read(GUILD_A, self.c) as scope:
            from dmbot.memory._changes import CLAIMS
            from dmbot.memory.claims import claim_of

            return [claim_of(r) for r in await scope.select(CLAIMS, "")]

    async def test_the_kind_comes_from_a_relationship_that_fixes_it(self) -> None:
        await self.propose(said("Bram", "located in", "Greywater Keep"))
        keep = (await self.entries())["Greywater Keep"]
        self.assertEqual(keep.type, "place")

    async def test_a_name_in_a_state_change_gets_the_kind_that_can_be_in_it(self) -> None:
        await self.propose(said("Old Garrick", "is", "dead"))
        self.assertEqual((await self.entries())["Old Garrick"].type, "character")

    # ---- near names ------------------------------------------------------------------------

    async def test_a_name_near_a_known_one_is_still_the_dms_to_decide(self) -> None:
        shander = await self.add("Bryn Shander", type="character")
        got = await self.propose(said("Bram", "knows", "Bryn Shandar"))
        self.assertEqual((got.named, got.joined), (1, 0))  # a proposal, not a merge
        self.assertEqual((await self.entries())["Bryn Shandar"].status, "proposed")
        known = await self.memory.entity(GUILD_A, self.c, shander)
        assert known is not None
        self.assertEqual(known.status, "confirmed")  # and the known one is untouched
        self.assertEqual(
            [a.text for a in await self.memory.aliases(GUILD_A, self.c, entity_id=shander)],
            ["Bryn Shander"],
        )

    async def test_two_near_known_names_make_a_plain_new_proposal(self) -> None:
        await self.add("Oskar Vence", type="character")
        await self.add("Oskar Vanca", type="character")
        got = await self.propose(said("Bram", "knows", "Oskar Vance"))
        self.assertEqual(got.named, 1)

    async def test_a_second_spelling_joins_the_name_already_waiting(self) -> None:
        await self.propose(said("Bram", "knows", "Oskar Vance"))
        got = await self.propose(said("Bram", "owes", "Oskar Vence"))
        self.assertEqual((got.named, got.joined, got.waiting), (0, 1, 1))
        found = await self.entries()
        self.assertEqual(list(found), ["Oskar Vance"])  # still one question for the DM
        spelt = [
            a.text
            for a in await self.memory.aliases(GUILD_A, self.c, entity_id=found["Oskar Vance"].id)
        ]
        self.assertEqual(sorted(spelt), ["Oskar Vance", "Oskar Vence"])

    async def test_two_people_whose_names_differ_by_a_letter_stay_two(self) -> None:
        got = await self.propose(
            said("Bram", "knows", "Mira Stone"), said("Bram", "knows", "Kira Stone")
        )
        self.assertEqual((got.named, got.waiting), (2, 2))

    async def test_a_state_change_about_a_new_name_waits_on_the_name_too(self) -> None:
        got = await self.propose(said("Old Garrick", "is", "dead"))
        self.assertEqual((got.named, got.waiting, got.new), (1, 1, ("Old Garrick",)))
        self.assertEqual(await self.counts(), {"names": 1})

    async def test_claims_waiting_on_names_never_hide_the_ones_that_can_be_answered(self) -> None:
        stranger = [said("Bram", "knows", name) for name in STRANGERS[:6]]
        await self.propose(*stranger)
        await self.propose(said("Bram", "knows", "Veldmoor"))
        ask = await self.claims.waiting(GUILD_A, self.c, opted_in=EVERYONE, limit=1)
        self.assertEqual([c.object for c in ask], ["Veldmoor"])

    async def test_a_name_said_two_ways_in_one_session_is_one_proposal(self) -> None:
        got = await self.propose(
            said("Bram", "knows", "Oskar Vance"), said("Bram", "owes", "Oskar Vence")
        )
        self.assertEqual((got.named, got.waiting), (1, 2))
        self.assertEqual(len(await self.entries()), 1)

    # ---- turned down -----------------------------------------------------------------------

    async def test_a_name_the_dm_turns_down_drops_its_claims_and_is_never_proposed_again(
        self,
    ) -> None:
        await self.propose(said("Bram", "knows", "Nobody Special"))
        nobody = (await self.entries())["Nobody Special"]
        await self.memory.set_entity_status(GUILD_A, self.c, nobody.id, REJECTED, source=DM_SOURCE)
        self.assertEqual(await self.claims.waiting(GUILD_A, self.c, opted_in=EVERYONE), [])
        self.assertEqual(await self.counts(), {"dropped": 1})
        again = await self.propose(said("Bram", "owes", "Nobody Special"))
        self.assertEqual((again.named, again.waiting, again.dropped), (0, 0, 1))
        self.assertEqual(dict(again.reasons), {NAME_REJECTED_REASON: 1})
        self.assertEqual(await self.entries(), {})

    async def test_people_at_the_table_are_not_story_names(self) -> None:
        got = await self.propose(said("Bram", "knows", "Mia"), skip={"mia"})
        self.assertEqual((got.named, got.waiting, got.dropped), (0, 0, 1))

    # ---- the limit -------------------------------------------------------------------------

    async def test_only_the_most_said_new_names_go_to_the_dm_and_the_rest_wait(self) -> None:
        found = [said("Bram", "knows", name) for name in STRANGERS]
        found += [said("Bram", "owes", name) for name in STRANGERS[5:]]
        got = await self.propose(*found, new_names=20)
        self.assertEqual((got.named, got.deferred), (20, 5))
        names = set(await self.entries())
        # The five said once are the ones left for next time.
        self.assertEqual(names, set(STRANGERS[5:]))
        self.assertEqual(got.waiting, 40)  # every claim of the twenty is kept

    async def test_names_past_the_limit_are_kept_and_proposed_first_next_session(self) -> None:
        first = [said("Bram", "knows", name) for name in STRANGERS[:25]]
        first += [said("Bram", "owes", name) for name in STRANGERS[5:25]]  # the last 20 said twice
        got = await self.propose(*first, new_names=20)
        self.assertEqual((got.named, got.deferred), (20, 5))  # the five said once are kept waiting
        self.assertEqual(set(await self.entries()), set(STRANGERS[5:25]))
        self.assertEqual(await self.counts(), {"names": 45})  # all waiting on a name
        # Not asked, and not lost: waiting claims stay hidden until their name is proposed.
        self.assertEqual(await self.claims.waiting(GUILD_A, self.c, opted_in=EVERYONE), [])
        # Next session says three other new names; the five left over are proposed first.
        later = await self.propose(
            *[said("Bram", "knows", n) for n in ("Wilhelmina", "Zephyrine", "Bartholomew")],
            new_names=5,
        )
        self.assertEqual((later.named, later.deferred), (5, 3))
        self.assertEqual(set(later.new), set(STRANGERS[:5]))
        self.assertEqual(set(await self.entries()), set(STRANGERS[:25]))
        # The five claims were waiting already; they are still waiting, now on a proposed name.
        self.assertEqual((await self.counts())["names"], 45 + 3)

    async def test_a_kept_claim_is_asked_once_its_name_is_confirmed(self) -> None:
        names = STRANGERS[:2]
        await self.propose(*[said("Bram", "knows", n) for n in names], new_names=1)
        await self.propose(said("Bram", "owes", "Wilhelmina"), new_names=1)  # proposes the other
        both = await self.entries()
        self.assertEqual(len(both), 2)
        for entity in both.values():
            await self.memory.set_entity_type(
                GUILD_A, self.c, entity.id, "character", source=DM_SOURCE
            )
            await self.memory.set_entity_status(
                GUILD_A, self.c, entity.id, CONFIRMED, source=DM_SOURCE
            )
        asked = await self.claims.waiting(GUILD_A, self.c, opted_in=EVERYONE)
        self.assertEqual(sorted(c.object for c in asked), sorted(names))

    # ---- secret names, isolation -----------------------------------------------------------

    async def test_a_secret_name_is_matched_only_for_the_dms_own_lines(self) -> None:
        await self.memory.add_alias(
            GUILD_A, self.c, self.bram, "The Hollow King", kind="nickname", source=DM_SOURCE,
            status=CONFIRMED, secret=True,
        )  # fmt: skip
        by_player = await self.propose(
            said("The Hollow King", "knows", "Veldmoor", "1"), speakers={"1": PLAYER}
        )
        self.assertEqual((by_player.waiting, by_player.named), (0, 0))
        self.assertEqual(dict(by_player.reasons), {SECRET_NAME_REASON: 1})
        by_dm = await self.propose(
            said("The Hollow King", "knows", "Veldmoor", "2"), speakers={"2": THE_DM}
        )
        self.assertEqual((by_dm.waiting, by_dm.dropped), (1, 0))
        self.assertEqual(await self.entries(), {})  # and no second entry for the secret name

    async def test_the_same_name_in_another_campaign_never_links(self) -> None:
        other = await self.campaigns.create(GUILD_A, "The Other One", DM)
        await self.add("Mother Ilse", type="character", campaign=other.id)
        await self.propose(said("Bram", "knows", "Mother Ilse"))
        mine = await self.entries()
        self.assertEqual(list(mine), ["Mother Ilse"])  # new here, whatever the other holds
        theirs = await self.memory.entities(GUILD_A, other.id)
        self.assertEqual([(e.name, e.status) for e in theirs], [("Mother Ilse", "confirmed")])
        self.assertEqual(await self.entries(campaign=other.id), {})

    async def test_the_same_name_in_another_server_never_links(self) -> None:
        await self.propose(said("Bram", "knows", "Mother Ilse"))
        # Another server has no such campaign: nothing there is read or written.
        with self.assertRaises(MemoryRuleError):
            await self.propose(said("Bram", "knows", "Mother Ilse"), guild=GUILD_B)


if __name__ == "__main__":
    unittest.main()
