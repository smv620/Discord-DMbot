"""A story thread's clues on the website (#4625, migration 0533): a database door for a DM of the
campaign and nobody else, a "found" that is the website's own reading of who-knows rows and is held
against Discord's `thread_clues(…, Viewer.the_dm())` case by case (Supervisor's ruling and
GraphEngineer's list on #4625: every difference is written down and none is the unsafe way round),
and four queue actions (add, false lead, take off, Undo) that the bot applies through the same store
calls as Discord's 🔎 Clues screen, each one change with one Undo. Invented names. Needs
Postgres."""

from __future__ import annotations

from typing import Any

from dmbot.memory import gate
from dmbot.memory import thread_clues as tc
from dmbot.memory import web_actions as wa
from dmbot.memory.models import CONFIRMED, DM, OPEN
from dmbot.web import relationships, thread_clues
from dmbot.web.recaps import RecapError, _scoped
from tests.test_recap_web import BOB
from tests.test_web_api import ALICE, THURSDAY
from tests.test_web_name_actions import Acting

PLAYER = 9


class Clues(Acting):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        made = await self.memory.new_thread(
            self.g, self.one.id, "Who killed the Speaker?", "mystery", OPEN, source=DM
        )
        self.thread = made.value.id
        self.pc = await self.entity("Tavin", "character", role="player_character", played_by=PLAYER)

    async def entity(self, name: str, kind: str, **kw: Any) -> str:
        made = await self.memory.add_entity(
            self.g, self.one.id, name=name, type=kind, source=DM, status=CONFIRMED, **kw
        )
        return str(made.value.id)

    async def clue(self, name: str, *, hidden: bool = True, lead: bool = False) -> str:
        """A clue tied to the thread by the DM; hidden from the players unless said."""
        clue = await self.entity(name, "clue", hidden_from_players=hidden)
        await self.memory.tie_clue(
            self.g, self.one.id, self.thread, clue_id=clue, false_lead=lead, source=DM
        )
        return clue

    async def learn(self, clue: str, who: str | None = None) -> None:
        await self.memory.learn_statement(
            self.g, self.one.id, gate.ENTRY, clue, who or self.pc, source=DM
        )

    async def door(self, who: Any = None) -> dict[str, Any]:
        return await thread_clues.read(self.web, who or self.session, self.cref, self.thread)

    async def stored(self) -> list[tc.ThreadClue]:
        return await self.memory.thread_clues(
            self.g, self.one.id, self.thread, gate.Viewer.the_dm()
        )

    async def both(self) -> dict[str, tuple[bool, bool]]:
        """name → (the door's found, Discord's found)."""
        door = {c["name"]: c["found"] for c in (await self.door())["clues"]}
        store = {c.name: c.found for c in await self.stored()}
        self.assertEqual(sorted(door), sorted(store))  # (the same clues, in the same order)
        return {name: (door[name], store[name]) for name in door}

    async def queue_one(self, action: str, text: str, who: Any = None) -> Any:
        session = who or self.session
        async with self.web.user(session.user_id, session=session.id_hash) as conn:
            await _scoped(conn, self.g)
            cur = await conn.execute(
                "SELECT dmbot_web_name_action_add(%s, %s, %s, %s, %s, %s, %s, %s) AS r",
                (self.g, self.one.id, None, action, "", "", text, False),
            )
            found = await cur.fetchone()
        return found["r"] if found else None

    async def act(self, action: str, text: str) -> wa.Outcome:
        got = await self.queue_one(action, text)
        self.assertEqual(got["status"], "ok", got)
        claimed = await self.actions.claim_next(self.g)
        assert claimed is not None
        outcome = await wa.apply(self.memory, self.g, {ALICE.id}, claimed, actions=self.actions)
        await self.actions.finish(
            self.g, claimed, error=outcome.error, result_batches=outcome.batches
        )
        return outcome


class TheDoor(Clues):
    async def test_the_clues_come_a_to_z_with_the_count_and_the_false_lead_mark(
        self,
    ) -> None:
        glove = await self.clue("A bloody glove", lead=True)
        await self.clue("A torn letter")
        await self.learn(glove)
        got = await self.door()
        self.assertEqual(
            [(c["name"], c["falseLead"], c["found"]) for c in got["clues"]],
            [("A bloody glove", True, True), ("A torn letter", False, False)],
        )
        self.assertEqual(got["count"], "1 of 2 clues found (1 is a false lead). Not yet found: 1.")
        self.assertEqual((await self.door())["count"], tc.found_text(await self.stored()))

    async def test_no_clue_says_nothing_and_a_player_or_unknown_thread_is_refused(
        self,
    ) -> None:
        self.assertEqual(await self.door(), {"count": "", "clues": []})
        bob = await self.signed(BOB, THURSDAY)
        with self.assertRaises(RecapError) as refused:
            await self.door(bob)
        self.assertEqual(refused.exception.code, "not_allowed")
        with self.assertRaises(RecapError) as missing:
            await thread_clues.read(self.web, self.session, self.cref, "f" * 32)
        self.assertEqual(missing.exception.code, "not_found")


class TheParity(Clues):
    """Discord's "found" against the door's, one case at a time."""

    async def test_equal_a_player_characters_own_row_and_no_row_at_all(self) -> None:
        known = await self.clue("Known")
        await self.clue("Unknown")
        await self.learn(known)
        self.assertEqual(await self.both(), {"Known": (True, True), "Unknown": (False, False)})

    async def test_equal_a_common_row_on_a_shown_clue_found_and_on_a_hidden_clue_not(self) -> None:
        shown = await self.clue("Shown", hidden=False)
        hidden = await self.clue("Hidden", hidden=True)
        await self.memory.add_common_knowledge(
            self.g, self.one.id, gate.ENTRY, [shown, hidden], source=DM
        )
        got = await self.both()
        self.assertEqual(got["Shown"], (True, True))
        self.assertEqual(got["Hidden"], (False, False))  # (never found where it is not shown)

    async def test_equal_a_row_held_by_the_party_is_never_found(self) -> None:
        clue = await self.clue("Party's")
        await self.sql(
            "INSERT INTO memory_perspectives (guild_id, campaign_id, id, kind, entity_id,"
            " created_at) VALUES (%s, %s, %s, 'party', NULL, 1) ON CONFLICT DO NOTHING",
            self.g, self.one.id, "c" * 32,
        )  # fmt: skip
        await self.sql(
            "INSERT INTO memory_knowers (guild_id, campaign_id, id, perspective_id,"
            " about_entity_id, created_at) SELECT %s, %s, %s, p.id, %s, 1"
            " FROM memory_perspectives p WHERE p.guild_id = %s AND p.campaign_id = %s"
            " AND p.kind = 'party'",
            self.g, self.one.id, "d" * 32, clue, self.g, self.one.id,
        )  # fmt: skip
        self.assertEqual((await self.both())["Party's"], (False, False))

    async def test_equal_a_former_player_characters_row_is_not_found(self) -> None:
        clue = await self.clue("Left behind")
        old = await self.entity("Rook", "character", role="player_character", played_by=PLAYER + 1)
        await self.learn(clue, old)
        await self.sql(
            "UPDATE memory_entities SET role = NULL WHERE guild_id = %s AND id = %s", self.g, old
        )
        self.assertEqual((await self.both())["Left behind"], (False, False))

    async def test_equal_a_clue_that_is_not_kept_is_not_listed_by_either(self) -> None:
        gone = await self.clue("Gone")
        await self.sql(
            "UPDATE memory_entities SET status = 'rejected' WHERE guild_id = %s AND id = %s",
            self.g, gone,
        )  # fmt: skip
        self.assertEqual(await self.both(), {})

    async def test_a_connection_a_character_knows_is_an_expected_difference(
        self,
    ) -> None:
        """GraphEngineer's case 5: Discord says found (the connection a character knows names the
        clue), the website says "not found yet": the safe way round (§9b item 4)."""
        clue = await self.clue("The ledger")
        vault = await self.entity("The vault", "place")
        tie = await self.memory.add_relation(
            self.g, self.one.id, clue, "located_in", vault, source=DM, confidence=1.0,
            status=CONFIRMED,
        )  # fmt: skip
        await self.memory.learn_statement(
            self.g, self.one.id, gate.CONNECTION, tie.value[0].id, self.pc, source=DM
        )
        door_found, store_found = (await self.both())["The ledger"]
        self.assertFalse(door_found)  # (never the unsafe way: found where Discord says not)
        self.assertTrue(store_found)

    async def test_another_name_a_character_knows_is_an_expected_difference(self) -> None:
        """GraphEngineer's case 6: a clue known by another name."""
        clue = await self.clue("The seal")
        alias = await self.memory.add_alias(
            self.g, self.one.id, clue, "The wolf stamp", kind="nickname", source=DM,
            status=CONFIRMED,
        )  # fmt: skip
        await self.memory.learn_statement(
            self.g, self.one.id, gate.ALIAS, alias.value.id, self.pc, source=DM
        )
        door_found, store_found = (await self.both())["The seal"]
        self.assertFalse(door_found)
        self.assertTrue(store_found)

    async def test_the_door_never_says_found_where_discord_says_not_found(self) -> None:
        a = await self.clue("A")
        await self.clue("B")
        await self.learn(a)
        for door_found, store_found in (await self.both()).values():
            self.assertTrue(store_found or not door_found)


class TheChanges(Clues):
    async def test_add_makes_a_hidden_clue_and_undo_takes_it_off(self) -> None:
        done = await self.act("clue-add", f"{self.thread} A torn letter")
        self.assertFalse(done.error)
        (batch,) = done.batches
        (clue,) = (await self.door())["clues"]
        self.assertEqual(
            (clue["name"], clue["falseLead"], clue["found"]), ("A torn letter", False, False)
        )
        undone = await self.act("clue-undo", str(batch))
        self.assertFalse(undone.error)
        self.assertEqual((await self.door())["clues"], [])

    async def test_add_by_a_name_the_campaign_has_ties_that_clue_never_a_twin(self) -> None:
        have = await self.entity("A torn letter", "clue", hidden_from_players=True)
        await self.act("clue-add", f"{self.thread} a torn LETTER")
        (clue,) = (await self.door())["clues"]
        self.assertEqual(clue["id"], have)

    async def test_a_clue_tied_already_is_a_bad_target(self) -> None:
        await self.clue("A torn letter")
        done = await self.act("clue-add", f"{self.thread} A torn letter")
        self.assertEqual(done.error, wa.BAD_TARGET)

    async def test_false_lead_on_and_off_and_take_off_each_with_an_undo(self) -> None:
        clue = await self.clue("A torn letter")
        marked = await self.act("clue-word", f"{self.thread} {clue} false")
        self.assertFalse(marked.error)
        self.assertTrue((await self.door())["clues"][0]["falseLead"])
        (batch,) = marked.batches
        await self.act("clue-undo", str(batch))
        self.assertFalse((await self.door())["clues"][0]["falseLead"])
        await self.act("clue-word", f"{self.thread} {clue} false")
        await self.act("clue-word", f"{self.thread} {clue} true")
        self.assertFalse((await self.door())["clues"][0]["falseLead"])
        off = await self.act("clue-off", f"{self.thread} {clue}")
        (took,) = off.batches
        self.assertEqual((await self.door())["clues"], [])
        await self.act("clue-undo", str(took))
        self.assertEqual(len((await self.door())["clues"]), 1)

    async def test_a_clue_no_longer_on_the_thread_is_gone(self) -> None:
        clue = await self.clue("A torn letter")
        await self.act("clue-off", f"{self.thread} {clue}")
        self.assertEqual((await self.act("clue-off", f"{self.thread} {clue}")).error, wa.GONE)
        self.assertEqual(
            (await self.act("clue-word", f"{self.thread} {clue} false")).error, wa.GONE
        )

    async def test_an_undo_of_a_change_that_is_not_the_dms_own_clue_answer_is_gone(self) -> None:
        other = await self.memory.add_alias(
            self.g, self.one.id, self.thread, "Speaker's end", kind="nickname", source=DM,
            status=CONFIRMED,
        )  # fmt: skip
        self.assertIsNotNone(other.batch)
        self.assertEqual((await self.act("clue-undo", str(other.batch))).error, wa.GONE)

    async def test_the_door_refuses_a_bad_shape_and_a_player_and_polls(
        self,
    ) -> None:
        def refused(got: Any) -> bool:
            return got is None or got["status"] != "ok"

        ids = "a" * 32
        for action, text in (
            ("clue-add", ids), ("clue-add", f"{ids} "), ("clue-word", f"{ids} {ids} maybe"),
            ("clue-off", ids), ("clue-undo", "12x"),
        ):  # fmt: skip
            self.assertTrue(refused(await self.queue_one(action, text)), (action, text))
        bob = await self.signed(BOB, THURSDAY)
        self.assertTrue(refused(await self.queue_one("clue-off", f"{ids} {ids}", who=bob)))
        queued = await thread_clues.change(
            self.web, self.session, self.cref, self.thread, {"action": "add", "name": "A key"}
        )
        claimed = await self.actions.claim_next(self.g)
        assert claimed is not None
        outcome = await wa.apply(self.memory, self.g, {ALICE.id}, claimed, actions=self.actions)
        await self.actions.finish(
            self.g, claimed, error=outcome.error, result_batches=outcome.batches
        )
        got = await relationships.read_answer(self.web, self.session, self.cref, queued)
        self.assertEqual(got["state"], "done")
        self.assertIsInstance(got["batch"], int)

    async def test_the_page_queues_only_a_known_change(self) -> None:
        clue = "b" * 32
        for body in (
            {}, {"action": "nope"}, {"action": "add"}, {"action": "add", "name": "x" * 101},
            {"action": "word", "clue": clue}, {"action": "word", "clue": "x", "falseLead": True},
            {"action": "off"}, {"action": "undo", "batch": "x"},
        ):  # fmt: skip
            with self.assertRaises(RecapError) as bad:
                await thread_clues.change(self.web, self.session, self.cref, self.thread, body)
            self.assertEqual(bad.exception.code, "bad_action", body)
