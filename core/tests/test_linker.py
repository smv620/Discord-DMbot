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
        self.assertEqual(link.allowed_distance("bryn shander"), 2)

    def test_the_distance_counts_single_letter_changes(self) -> None:
        self.assertTrue(link.within("oskar vane", "oskar vana", 1))
        self.assertTrue(link.within("oskar vane", "oskar van", 1))  # a letter dropped
        self.assertFalse(link.within("oskar vane", "oskar vaine", 0))
        self.assertFalse(link.within("oskar vane", "oskar vana", 0))
        self.assertFalse(link.within("oskar vane", "oskar vain", 1))  # two letters
        self.assertFalse(link.within("abc", "abcdefg", 2))  # too far apart in length at once

    def test_one_near_entry_is_a_match_and_two_are_not(self) -> None:
        self.assertEqual(link.alike("oskar vane", {"oskar vain": "a", "mira": "b"}), "a")
        self.assertIsNone(link.alike("oskar vane", {"oskar vain": "a", "oskar vana": "b"}))
        # Two names of one entry are still one entry.
        self.assertEqual(link.alike("oskar vane", {"oskar vain": "a", "oskar vana": "a"}), "a")
        self.assertIsNone(link.alike("oskar vane", {"mira": "b"}))

    def test_the_most_said_names_come_first_and_the_rest_are_counted(self) -> None:
        said_keys = ["a", "b", "b", "c", "c", "c", "d"]
        self.assertEqual(link.most_mentioned(said_keys, 2), (["c", "b"], 2))
        self.assertEqual(link.most_mentioned(["x", "y"], 5), (["x", "y"], 0))

    def test_a_name_said_two_ways_is_one_group(self) -> None:
        groups = link.spellings(["oskar vane", "mira", "oskar vain"])
        self.assertEqual(groups, {"oskar vane": ["oskar vain"], "mira": []})


class DmScreenText(unittest.TestCase):
    def test_the_post_says_why_some_of_what_was_said_is_not_asked_yet(self) -> None:
        from dmbot.ui.names import held_back_text

        text = held_back_text(["Mother Ilse", "Oskar Vane"])
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

    async def test_a_name_in_a_state_change_is_unsure(self) -> None:
        await self.propose(said("Old Garrick", "is", "dead"))
        self.assertEqual((await self.entries())["Old Garrick"].type, "concept")

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
        await self.add("Oskar Vain", type="character")
        await self.add("Oskar Vana", type="character")
        got = await self.propose(said("Bram", "knows", "Oskar Vane"))
        self.assertEqual(got.named, 1)

    async def test_a_second_spelling_joins_the_name_already_waiting(self) -> None:
        await self.propose(said("Bram", "knows", "Oskar Vane"))
        got = await self.propose(said("Bram", "owes", "Oskar Vain"))
        self.assertEqual((got.named, got.joined, got.waiting), (0, 1, 1))
        found = await self.entries()
        self.assertEqual(list(found), ["Oskar Vane"])  # still one question for the DM
        spelt = [
            a.text
            for a in await self.memory.aliases(GUILD_A, self.c, entity_id=found["Oskar Vane"].id)
        ]
        self.assertEqual(sorted(spelt), ["Oskar Vain", "Oskar Vane"])

    async def test_a_name_said_two_ways_in_one_session_is_one_proposal(self) -> None:
        got = await self.propose(
            said("Bram", "knows", "Oskar Vane"), said("Bram", "owes", "Oskar Vain")
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

    async def test_the_names_left_over_are_not_remembered_as_dropped(self) -> None:
        found = [said("Bram", "knows", name) for name in STRANGERS[:3]]
        await self.propose(*found, new_names=1)
        later = await self.propose(*found, new_names=5)
        self.assertEqual(later.named, 2)  # next session they are proposed

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
