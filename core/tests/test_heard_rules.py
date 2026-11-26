"""The heard spellings of a name the DM kept become "change to" rules (#5133 slice 3c-2b): the
guard (`memory/heard_rules.py`, `scan.ordinary_in`), the store's part of it in `confirm_entity`,
and the reply's words. Invented names."""

from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import AsyncMock, patch

from dmbot.memory import heard_rules as hr
from dmbot.memory.models import DM as DM_SOURCE
from dmbot.memory.models import FIX, PROPOSED
from dmbot.memory.name_found import Found
from dmbot.memory.scan import ordinary_in
from dmbot.transcript.models import Line, line_ref
from dmbot.transcript.store import TranscriptStore
from dmbot.ui import names as ui
from tests.test_memory_names import DM, GUILD, MANAGER, PLAYER, NamesTest
from tests.test_memory_store import GUILD_A, MemoryTest

START = 1_700_000_000

LINES = [
    "a dwarf called hroth gar steps out of the smoke",
    "Rothgar raises his axe",
    "we hope he is friendly",
    "the old gar fish swims by",
]


class TheGuard(unittest.TestCase):
    def test_a_misheard_spelling_said_in_lower_case_is_not_ordinary_by_its_own_sightings(
        self,
    ) -> None:
        self.assertFalse(ordinary_in("hroth gar", LINES[:2], leaving_out=["hroth gar"]))
        self.assertTrue(ordinary_in("hroth gar", LINES[:2]))  # (counted, it would be)

    def test_a_word_said_in_lower_case_elsewhere_is_ordinary(self) -> None:
        self.assertTrue(hr.ordinary("Hope", LINES))
        self.assertTrue(ordinary_in("Gar", LINES, leaving_out=["hroth gar"]))

    def test_common_words_and_not_names_are_ordinary(self) -> None:
        self.assertTrue(hr.ordinary("the", []))
        self.assertTrue(hr.ordinary("Monday", []))

    def test_one_or_two_letters_is_too_short(self) -> None:
        self.assertTrue(hr.too_short("Al"))
        self.assertTrue(hr.too_short("K."))
        self.assertFalse(hr.too_short("Kal"))

    def test_only_the_spellings_that_pass_in_their_order_never_the_name_itself(self) -> None:
        got = hr.may_be_rules(
            "Hrothgar", ["Rothgar", "hroth gar", "Hope", "Al", "hrothgar", "Rothgar"], LINES
        )
        self.assertEqual(got, ["Rothgar", "hroth gar"])


class OnlyCapitals(unittest.TestCase):
    """Supervisor's ruling on #5133: a one-word spelling that differs from the name only by
    capitals makes no rule (an NPC called "Hope", heard as "hope", must never rewrite "I hope")."""

    def test_hope_heard_only_as_hope_makes_no_rule(self) -> None:
        self.assertTrue(hr.only_capitals("Hope", "hope"))
        self.assertEqual(hr.may_be_rules("Hope", ["hope"], ["she said hope is gone"]), [])

    def test_hrothgar_makes_none_while_hroth_gar_and_rothgar_do(self) -> None:
        got = hr.may_be_rules("Hrothgar", ["hrothgar", "hroth gar", "Rothgar"], LINES)
        self.assertEqual(got, ["hroth gar", "Rothgar"])
        self.assertFalse(hr.only_capitals("Hrothgar", "hroth gar"))


class TheReply(unittest.TestCase):
    def test_kept_and_what_now_reads_as_it(self) -> None:
        self.assertEqual(
            ui.rules_note("Hrothgar", "npc", ["Rothgar", "hroth gar"], "fixed"),
            "✅ Kept **Hrothgar** as an NPC. *Rothgar* and *hroth gar* now read as Hrothgar, "
            "in this session's transcript and from now on.",
        )
        self.assertEqual(
            ui.rules_note("Hrothgar", "npc", ["Rothgar"], "later"),
            "✅ Kept **Hrothgar** as an NPC. *Rothgar* now reads as Hrothgar, from now on.",
        )
        self.assertEqual(
            ui.rules_note("Hrothgar", "npc", [], "fixed"), "✅ Kept **Hrothgar** as an NPC."
        )

    def test_the_wait_and_the_failure(self) -> None:
        self.assertEqual(
            ui.rules_note("Hrothgar", "npc", ["Rothgar"], "fixing"),
            "✅ Kept **Hrothgar** as an NPC, and DMbot is fixing this session's transcript.",
        )
        self.assertEqual(
            ui.rules_note("Hrothgar", "npc", ["Rothgar"], "failed"),
            "Kept **Hrothgar**, but this session's transcript couldn't be fixed. "
            "Press 🔁 Try again.",
        )

    def test_not_a_kind_fits_a_phone(self) -> None:
        self.assertEqual(ui.not_kind_label("place"), "✏️ Not a place")
        self.assertLessEqual(len(ui.not_kind_label("faction")), ui.REVIEW_LABEL_MAX)


class TheStore(MemoryTest):
    """`confirm_entity(heard=..., may_rule=...)`: one change, so one Undo."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.e = await self.add("Hrothgar", type="concept", source="scan", status=PROPOSED)
        for spelling in ("Rothgar", "hroth gar", "Hope"):
            await self.memory.add_alias(
                GUILD_A, self.c, self.e, spelling, kind="misheard", source="scan"
            )

    async def keep(self, may: list[str]) -> tuple[str, ...]:
        written = await self.memory.confirm_entity(
            GUILD_A,
            self.c,
            self.e,
            "npc",
            source=DM_SOURCE,
            heard=["Rothgar", "hroth gar", "Hope"],
            may_rule=may,
        )
        self.batch = written.batch
        return written.rules

    async def names_of(self) -> set[str]:
        return {a.text for a in await self.memory.aliases(GUILD_A, self.c, entity_id=self.e)}

    async def rules(self) -> dict[str, tuple[str, str | None]]:
        return {
            c.heard: (c.action, c.entity_id) for c in await self.memory.corrections(GUILD_A, self.c)
        }

    async def test_the_allowed_spellings_become_rules_and_none_stays_a_name(self) -> None:
        made = await self.keep(["Rothgar", "hroth gar"])
        self.assertEqual(sorted(made), ["Rothgar", "hroth gar"])
        self.assertEqual(await self.names_of(), {"Hrothgar"})  # ("Hope" is dropped, not kept)
        self.assertEqual(await self.rules(), {"Rothgar": (FIX, self.e), "hroth gar": (FIX, self.e)})

    async def test_another_entry_s_name_is_never_a_rule(self) -> None:
        await self.add("Rothgar")  # (a known name)
        made = await self.keep(["Rothgar", "hroth gar"])
        self.assertEqual(made, ("hroth gar",))

    async def test_words_with_a_rule_already_keep_it(self) -> None:
        await self.memory.add_correction(
            GUILD_A, self.c, "hroth gar", action="keep", source=DM_SOURCE
        )
        made = await self.keep(["Rothgar", "hroth gar"])
        self.assertEqual(made, ("Rothgar",))
        self.assertEqual((await self.rules())["hroth gar"], ("keep", None))

    async def test_one_undo_takes_back_the_yes_and_the_rules(self) -> None:
        await self.keep(["Rothgar", "hroth gar"])
        assert self.batch is not None
        await self.memory.undo(GUILD_A, self.c, self.batch)
        entity = await self.memory.entity(GUILD_A, self.c, self.e)
        assert entity is not None
        self.assertEqual(entity.status, PROPOSED)
        self.assertEqual(await self.rules(), {})
        self.assertEqual(await self.names_of(), {"Hrothgar", "Rothgar", "hroth gar", "Hope"})

    async def test_a_short_form_or_a_dm_s_nickname_stays_one_of_its_names(self) -> None:
        await self.memory.add_alias(GUILD_A, self.c, self.e, "Hroth", kind="short", source="scan")
        await self.memory.add_alias(
            GUILD_A, self.c, self.e, "Old Axe", kind="nickname", source="dm"
        )
        written = await self.memory.confirm_entity(
            GUILD_A,
            self.c,
            self.e,
            "npc",
            source=DM_SOURCE,
            heard=["Rothgar", "Hroth", "Old Axe"],
            may_rule=["Rothgar", "Hroth", "Old Axe"],
        )
        self.assertEqual(written.rules, ("Rothgar",))
        self.assertTrue({"Hroth", "Old Axe"} <= await self.names_of())
        self.assertEqual(set(await self.rules()), {"Rothgar"})

    async def test_hope_heard_only_as_hope_keeps_the_name_and_makes_no_rule(self) -> None:
        hope = await self.add("Hope", type="concept", source="scan", status=PROPOSED)
        written = await self.memory.confirm_entity(
            GUILD_A, self.c, hope, "npc", source=DM_SOURCE, heard=["hope"], may_rule=["hope"]
        )
        self.assertEqual(written.rules, ())
        self.assertEqual(written.value.name, "Hope")
        self.assertEqual(await self.rules(), {})

    async def test_without_heard_every_spelling_is_kept_as_today(self) -> None:
        written = await self.memory.confirm_entity(GUILD_A, self.c, self.e, "npc", source=DM_SOURCE)
        self.assertEqual(written.rules, ())
        self.assertEqual(await self.names_of(), {"Hrothgar", "Rothgar", "hroth gar", "Hope"})


class TheCard(NamesTest):
    """The new card's yes, end to end (#5133 slice 3c-2b): the rules, the session's cleaned lines,
    the reply, ↩️ Undo and ✏️ Not a …. Invented names and lines."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.store = TranscriptStore(self.db)
        self.bot.transcripts = self.store
        await self.consent.grant(GUILD, DM)
        await self.consent.grant(GUILD, PLAYER)
        self.sid = await self.store.open_session(GUILD, self.campaign.id, START)
        said = [
            Line((START + 1) * 1000, PLAYER, "Rothgar raises his axe", "Rothgar raises his axe"),
            Line((START + 2) * 1000, DM, "a dwarf called Hrothgar Hall", "a dwarf Hrothgar Hall"),
        ]
        await self.store.add_lines(GUILD, self.sid, said)
        written = await self.memory.add_entity(
            GUILD, self.campaign.id, type="place", name="Hrothgar Hall", source="scan",
            description="Heard 2 times",
        )  # fmt: skip
        self.e = written.value.id
        await self.memory.add_alias(
            GUILD, self.campaign.id, self.e, "Rothgar", kind="misheard", source="scan"
        )
        await self.memory.keep_name_found(
            GUILD,
            self.campaign.id,
            self.e,
            Found("scan", line_ref(self.sid, (START + 1) * 1000, PLAYER), 0, 2),
        )

    def said(self, it: Any) -> str:
        return "\n".join(t for t, _ in [*it.response.sent, *it.response.edited])

    async def cleaned(self) -> list[str]:
        return [x.text for x in await self.store.lines(GUILD, self.sid)]

    async def yes(self) -> tuple[ui.SuggestionReview, Any]:
        first = self.it()
        await ui.start_review(first, self.campaign.id)
        view = first.response.sent[0][1]["view"]
        it = self.it()
        await view._yes(it)
        return view, it

    async def test_yes_makes_the_rule_and_fixes_this_session_s_transcript(self) -> None:
        view, it = await self.yes()
        self.assertIn(
            "✅ Kept **Hrothgar Hall** as a place. *Rothgar* now reads as Hrothgar Hall, "
            "in this session's transcript and from now on.",
            self.said(it),
        )
        self.assertIn("Hrothgar Hall raises his axe", await self.cleaned())
        rules = {c.heard: c.action for c in await self.memory.corrections(GUILD, self.campaign.id)}
        self.assertEqual(rules, {"Rothgar": FIX})
        labels = [getattr(b, "label", "") for b in view.children]
        self.assertIn("↩️ Undo", labels)
        self.assertIn("✏️ Not a place", labels)

    async def test_undo_puts_back_the_name_the_rule_and_the_lines(self) -> None:
        view, _ = await self.yes()
        it = self.it()
        await view._undo_kept(it)
        entity = await self.memory.entity(GUILD, self.campaign.id, self.e)
        assert entity is not None
        self.assertEqual(entity.status, PROPOSED)
        self.assertEqual(await self.memory.corrections(GUILD, self.campaign.id), [])
        self.assertIn("Rothgar raises his axe", await self.cleaned())
        self.assertIn("↩️ Undone. **Hrothgar Hall** is waiting again.", self.said(it))
        found = await self.memory.name_found(GUILD, self.campaign.id, [self.e])
        self.assertIn(self.e, found)  # (what made it a name, back with it)
        self.assertEqual(view.queue[0], self.e)

    async def test_not_a_place_changes_what_it_is(self) -> None:
        view, _ = await self.yes()
        it = self.it()
        await view._not_kind(it)
        view.kind._values = ["faction"]  # (shown as "a group")
        it = self.it()
        await view._kind_changed(it)
        entity = await self.memory.entity(GUILD, self.campaign.id, self.e)
        assert entity is not None
        self.assertEqual(entity.type, "faction")
        self.assertIn("✏️ **Hrothgar Hall** is now a group.", self.said(it))

    async def test_the_buttons_are_the_dm_s(self) -> None:
        view, _ = await self.yes()
        it = self.it(MANAGER)
        await view._undo_kept(it)
        entity = await self.memory.entity(GUILD, self.campaign.id, self.e)
        assert entity is not None
        self.assertEqual(entity.status, "confirmed")

    async def labels(self, view: Any) -> list[str]:
        return [str(getattr(b, "label", "") or "") for b in view.children]

    async def test_the_next_card_carries_the_undo_until_it_is_answered(self) -> None:
        await self.memory.add_entity(
            GUILD, self.campaign.id, type="concept", name="Vorna", source="scan",
            description="Heard 3 times",
        )  # fmt: skip
        first = self.it()
        await ui.start_review(first, self.campaign.id)
        view = first.response.sent[0][1]["view"]
        if view.queue[0] != self.e:  # (the surest first: Hrothgar Hall has its row)
            self.fail("Hrothgar Hall should be asked first")
        await view._yes(self.it())
        self.assertIn("↩️ Undo", await self.labels(view))  # (on Vorna's card)
        await view._later(self.it())
        self.assertIsNone(view.kept)

    async def test_not_a_kind_never_offers_a_dm_only_kind(self) -> None:
        view, _ = await self.yes()
        await view._not_kind(self.it())
        offered = {o.value for o in view.kind.options}
        self.assertNotIn("mystery", offered)
        self.assertNotIn("quest", offered)
        self.assertIn("faction", offered)

    async def test_buttons_on_the_end_run_its_steps_once(self) -> None:
        view, _ = await self.yes()
        with patch("dmbot.ui.name_background.offer_ready", AsyncMock()) as offer:
            view.ended = False
            await view._finish(self.it(), None)
            await view._try_again(self.it())
        self.assertEqual(offer.await_count, 1)

    async def test_a_failed_fix_says_so_and_try_again_fixes_it(self) -> None:
        real = self.store.respell_names
        self.store.respell_names = AsyncMock(side_effect=RuntimeError("down"))  # type: ignore[method-assign]
        view, it = await self.yes()
        self.assertIn(
            "Kept **Hrothgar Hall**, but this session's transcript couldn't be fixed. "
            "Press 🔁 Try again.",
            self.said(it),
        )
        self.assertIn("🔁 Try again", await self.labels(view))
        self.store.respell_names = real  # type: ignore[method-assign]
        it = self.it()
        await view._try_again(it)
        self.assertIn("Hrothgar Hall raises his axe", await self.cleaned())
        self.assertIn("in this session's transcript and from now on", self.said(it))

    async def test_an_entry_the_table_does_not_hold_keeps_its_transcript(self) -> None:
        async def nobody(*_a: Any, **_k: Any) -> frozenset[str]:
            return frozenset()

        self.memory.entries_held = nobody  # type: ignore[method-assign]
        _view, it = await self.yes()
        self.assertIn("*Rothgar* now reads as Hrothgar Hall, from now on.", self.said(it))
        self.assertIn("Rothgar raises his axe", await self.cleaned())

    async def test_no_line_left_from_people_who_agree_keeps_today_s_yes(self) -> None:
        await self.consent.revoke(GUILD, PLAYER)
        await self.consent.revoke(GUILD, DM)
        _view, it = await self.yes()
        self.assertEqual(await self.memory.corrections(GUILD, self.campaign.id), [])
        self.assertIn("✅ Kept **Hrothgar Hall** (also **Rothgar**) as a place.", self.said(it))
