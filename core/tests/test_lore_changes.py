"""A lore read that finds a different connection between two names the campaign already connects
(#3021): the same kind the same way round is "already known", a kind that cannot hold beside the
old one is offered as a change (the old one is ended, never deleted), a kind that can is offered
as new, and the reply always says what became of the connections. Invented names and text."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, patch

from dmbot.memory import backstory_keep as keep
from dmbot.memory import link_edits
from dmbot.memory.models import CONFIRMED, MemoryRuleError, NewName
from dmbot.ui import name_background as ui_bg
from dmbot.ui.name_lists import Upload, _ai_busy, _ai_reads
from tests import lore_fake
from tests.test_background_connections import link, name, shown
from tests.test_memory_names import DM, GUILD, NamesTest

TEXT = "Belleros and Cerric were close once. Now Belleros is the sworn enemy of Cerric."
NAMES = [
    name("Belleros", "person", "Belleros and Cerric"),
    name("Cerric", "person", "Cerric were close"),
]


def reply(how: str) -> dict[str, Any]:
    return {
        "names": NAMES,
        "connections": [link("Belleros", how, "Cerric", "Belleros is the sworn enemy of Cerric")],
    }


class TheChange(NamesTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        _ai_busy.clear()
        _ai_reads.clear()
        self.bot.plan_gate = AsyncMock(return_value=None)  # type: ignore[method-assign]
        self.bot.campaigns.record_confirmation = AsyncMock()  # type: ignore[method-assign]
        from dmbot.memory.backstory_links import BackstoryLinks

        self.store = BackstoryLinks(self.db)
        self.bot.backstory_links = self.store
        made = await self.memory.add_names(
            GUILD, self.campaign.id,
            [NewName("Belleros", "npc", CONFIRMED), NewName("Cerric", "npc", CONFIRMED)],
            source="dm",
        )  # fmt: skip
        self.bel, self.cer = made.value
        assert self.bel and self.cer
        self.old = await self.memory.add_relation(
            GUILD, self.campaign.id, self.bel, "ally_of", self.cer, source="dm", confidence=1.0,
            status=CONFIRMED,
        )  # fmt: skip

    async def asyncTearDown(self) -> None:
        _ai_busy.clear()
        _ai_reads.clear()
        await super().asyncTearDown()

    async def read(self, how: str) -> Any:
        ai = lore_fake.lore_ai(json.dumps(reply(how)))
        self.bot.background_ai = ai  # type: ignore[assignment]
        self.bot.background_read_ai = ai  # type: ignore[assignment]
        it = self.it()
        await ui_bg.offer(it, self.campaign.id, Upload(TEXT, "your background"))
        press = self.it()
        await it.response.sent[0][1]["view"]._read(press)
        return press

    async def relations(self) -> list[Any]:
        return await self.memory.relations(GUILD, self.campaign.id, include_secret=True)

    async def card(self) -> Any:
        it = self.it(DM)
        self.assertTrue(await ui_bg.offer_ready(it, self.campaign.id))
        return it.followup.send.await_args.kwargs["view"]

    async def test_a_kind_that_cannot_hold_beside_the_old_one_is_offered_as_a_change(self) -> None:
        press = await self.read("enemy")
        said = shown(press)
        self.assertNotIn("Nothing was added", said)
        self.assertIn("1 change ready to check", said)
        labels = [
            getattr(b, "label", "")
            for b in press.edit_original_response.await_args.kwargs["view"].children
        ]
        self.assertIn("📜 Check the connections", labels)
        view = await self.card()
        self.assertIn("update it?", view.text())
        self.assertIn("Now: Belleros ally of Cerric", view.text())
        self.assertIn("Proposed: Belleros **knows (hostile)** Cerric", view.text())
        # nothing has changed until the DM says yes
        (only,) = await self.relations()
        self.assertIsNone(only.to_session_at)
        await next(b for b in view.children if b.label == "✅ Update it").callback(self.it())
        by_kind = {r.predicate: r for r in await self.relations()}
        self.assertIsNotNone(by_kind["ally_of"].to_session_at)  # ended, not deleted
        self.assertIsNone(by_kind["knows"].to_session_at)  # live (enemy is knows, hostile, #3175)
        self.assertEqual(by_kind["knows"].detail, "hostile")
        self.assertEqual(await self.store.waiting(GUILD, self.campaign.id), [])

    async def test_a_kind_that_can_hold_beside_the_old_one_is_offered_as_new(self) -> None:
        press = await self.read("works for")
        said = shown(press)
        self.assertIn("1 ready to check", said)
        self.assertNotIn("change", said)
        view = await self.card()
        self.assertIn("add it?", view.text())
        await next(b for b in view.children if b.label == "✅ Add it").callback(self.it())
        live = [r.predicate for r in await self.relations() if r.to_session_at is None]
        self.assertCountEqual(live, ["ally_of", "serves"])

    async def test_the_same_kind_is_already_known_and_a_rerun_queues_nothing(self) -> None:
        said = shown(await self.read("friend"))  # (the connection the campaign already holds)
        self.assertIn("1 already known", said)
        self.assertEqual(await self.store.waiting(GUILD, self.campaign.id), [])
        await self.read("enemy")
        again = shown(await self.read("enemy"))
        self.assertIn("1 already known", again)  # (waiting from the first run)
        self.assertEqual(len(await self.store.waiting(GUILD, self.campaign.id)), 1)

    async def test_keep_all_asks_before_ending_a_public_one_and_undo_takes_it_back(self) -> None:
        # (#4524, ruling E: the kept one waits to be said at the table, so ending the public one
        # would take it from the players by itself: "Did this change?" is asked instead)
        await self.read("enemy")
        waiting = await self.store.waiting(GUILD, self.campaign.id)
        done = await keep.keep_all(self.memory, self.store, GUILD, self.campaign.id, waiting)
        by_kind = {r.predicate: r for r in await self.relations()}
        self.assertIsNone(by_kind["ally_of"].to_session_at)
        (q,) = await self.memory.secret_questions_waiting(GUILD, self.campaign.id)
        self.assertEqual((q.kind, q.item_id), ("replace", by_kind["ally_of"].id))
        self.assertEqual(
            await keep.undo_all(self.memory, self.store, GUILD, self.campaign.id, done), 1
        )
        live = [r.predicate for r in await self.relations() if r.to_session_at is None]
        self.assertEqual(live, ["ally_of"])
        self.assertEqual(await self.memory.secret_questions_waiting(GUILD, self.campaign.id), [])

    async def test_a_refused_change_leaves_the_old_connection_as_it_was(self) -> None:
        await self.read("enemy")
        view = await self.card()
        with patch.object(self.memory, "add_relation", AsyncMock(side_effect=MemoryRuleError("x"))):
            await next(b for b in view.children if b.label == "✅ Update it").callback(self.it())
        (only,) = await self.relations()
        self.assertEqual((only.predicate, only.to_session_at), ("ally_of", None))
        self.assertEqual(len(await self.store.waiting(GUILD, self.campaign.id)), 1)

    async def test_a_changed_feeling_on_a_held_knows_is_a_change_and_the_same_is_not(self) -> None:
        # (#3175) the connection now held is "knows (hostile)"; the text first says the same, then
        # the opposite
        await self.read("enemy")
        view = await self.card()
        await next(b for b in view.children if b.label == "✅ Update it").callback(self.it())
        again = shown(await self.read("enemy"))
        self.assertIn("1 already known", again)  # (same feeling: nothing to ask)
        changed = shown(await self.read("friend"))
        self.assertIn("1 change ready to check", changed)  # (the opposite feeling is a change)
        view = await self.card()
        self.assertIn("Now: Belleros knows (hostile) Cerric", view.text())
        self.assertIn("Proposed: Belleros knows **(friendly)** Cerric", view.text())

    async def test_after_a_change_the_old_kind_can_be_offered_back(self) -> None:
        await self.read("enemy")
        view = await self.card()
        await next(b for b in view.children if b.label == "✅ Update it").callback(self.it())
        said = shown(await self.read("friend"))  # (the ended friendship is not "already known")
        self.assertIn("1 change ready to check", said)


class TheCardWords(NamesTest):
    """The connection card's Players line, toggle and bold words (#4429), without a database."""

    def test_only_the_changed_words_are_bold(self) -> None:
        bold = ui_bg.bold_what_changed
        self.assertEqual(bold("A knows B", "A knows (friendly) B"), "A knows **(friendly)** B")
        self.assertEqual(bold("A knows B", "A ally of B"), "A **ally of** B")
        self.assertEqual(bold("A knows B", "A knows B"), "A knows B")

    def test_the_four_lines_of_a_change(self) -> None:
        def line(now_hidden: bool, choice: bool | None) -> ui_bg.PlayersView:
            return ui_bg.players_view(
                change=True, now_hidden=now_hidden, now_secret=now_hidden, waits=False,
                name_secret=False, choice=choice,
            )  # fmt: skip

        self.assertIn("will still see it after the update", line(False, None).line)
        self.assertIn("still will be after the update", line(True, None).line)  # stays hidden
        self.assertTrue(line(True, None).hidden_after)
        self.assertIn("After the update it will be hidden", line(False, True).line)
        self.assertTrue(line(False, True).secret)
        flipped = line(True, False)
        self.assertIn("After the update they will see it", flipped.line)
        self.assertEqual((flipped.secret, flipped.reveal), (False, True))

    def test_a_secret_name_has_no_toggle_and_stays_hidden(self) -> None:
        view = ui_bg.players_view(
            change=False, now_hidden=False, now_secret=False, waits=False, name_secret=True,
            choice=False,
        )  # fmt: skip
        self.assertEqual((view.label, view.secret), ("", True))
        self.assertEqual(view.line, ui_bg.SECRET_NAME_LINE)

    def test_a_new_card_says_whether_it_waits_for_the_table(self) -> None:
        waits = ui_bg.players_view(
            change=False, now_hidden=False, now_secret=False, waits=True, name_secret=False,
            choice=None,
        )  # fmt: skip
        self.assertIn("once it comes up at the table", waits.line)
        hidden = ui_bg.players_view(
            change=False, now_hidden=False, now_secret=False, waits=True, name_secret=False,
            choice=True,
        )  # fmt: skip
        self.assertEqual((hidden.line, hidden.secret), ("🤫 Hidden from players.", True))


class TheCardToggle(TheChange):
    async def test_the_toggle_flips_only_the_players_line_and_saves_on_update(self) -> None:
        await self.read("enemy")
        view = await self.card()
        before = view.text()
        self.assertIn("will still see it after the update", before)
        await next(b for b in view.children if b.label == ui_bg.HIDE_PLAYERS).callback(self.it())
        after = view.text()
        self.assertIn("After the update it will be hidden", after)
        (old,) = await self.relations()  # nothing saved by the toggle
        self.assertFalse(old.secret)
        await next(b for b in view.children if b.label == "✅ Update it").callback(self.it())
        by_kind = {r.predicate: r for r in await self.relations()}
        self.assertTrue(by_kind["knows"].secret)

    async def test_a_hidden_connection_stays_hidden_by_default_on_update(self) -> None:
        await link_edits.set_secret(self.memory, GUILD, self.campaign.id, self.old.value[0], True)
        await self.read("enemy")
        view = await self.card()
        self.assertIn("still will be after the update", view.text())
        await next(b for b in view.children if b.label == "✅ Update it").callback(self.it())
        knows = next(r for r in await self.relations() if r.predicate == "knows")
        self.assertTrue(knows.secret or knows.unrevealed)

    async def test_the_websites_keep_follows_the_players_choice(self) -> None:
        # (#4432) the same three answers as the card: as it is now, hide, or players see it
        for players, hidden in (("", True), ("show", False), ("hide", True)):
            with self.subTest(players=players):
                await self.asyncTearDown()
                await self.asyncSetUp()
                await link_edits.set_secret(
                    self.memory, GUILD, self.campaign.id, self.old.value[0], True
                )
                await self.read("enemy")
                (one,) = await self.store.waiting(GUILD, self.campaign.id)
                done = await keep.keep_all(
                    self.memory, self.store, GUILD, self.campaign.id, [one],
                    one_press=True, players=players,
                )  # fmt: skip
                self.assertEqual(len(done.kept), 1)
                knows = next(r for r in await self.relations() if r.predicate == "knows")
                self.assertEqual(knows.secret or knows.unrevealed, hidden)

    async def test_an_unknown_players_word_is_refused(self) -> None:
        await self.read("enemy")
        (one,) = await self.store.waiting(GUILD, self.campaign.id)
        with self.assertRaises(ValueError):
            await keep.keep_all(
                self.memory, self.store, GUILD, self.campaign.id, [one],
                one_press=True, players="maybe",
            )  # fmt: skip

    async def test_a_hide_beats_an_identical_visible_twin(self) -> None:
        # (#4432) the hide choice is the DM's own word: an existing public twin is hidden too
        await self.read("enemy")
        view = await self.card()
        await next(b for b in view.children if b.label == "✅ Update it").callback(self.it())
        await self.read("friend")
        await self.read("enemy")  # the same kind again, now an update of the same pair
        waiting = await self.store.waiting(GUILD, self.campaign.id)
        if waiting:
            await keep.keep_all(
                self.memory, self.store, GUILD, self.campaign.id, waiting[:1],
                one_press=True, players="hide",
            )  # fmt: skip
        live = [r for r in await self.relations() if r.to_session_at is None]
        self.assertTrue(all(r.secret or r.unrevealed for r in live if r.predicate == "knows"))
