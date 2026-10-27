"""The recap page's names to check (#2113): the list from the website's door, an answer queued by
another, and the bot applying each one through the memory paths Discord's name check uses. Invented
names throughout."""

from __future__ import annotations

import asyncio
import logging
import time
from types import SimpleNamespace
from typing import Any, cast

from dmbot import bot as bot_module
from dmbot.memory.models import CONFIRMED, PROPOSED, REJECTED
from dmbot.memory.store import MemoryStore
from dmbot.recaps import name_answers as na
from dmbot.web import name_checks as web
from dmbot.web import sessions
from dmbot.web.recaps import RecapError
from tests.test_recap_web import BOB, NOW, Base
from tests.test_web_api import ALICE, THURSDAY

GUILD = THURSDAY.id
AT = 100  # the recap's session time (the Base's one recap)


class WithChecks(Base):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.memory = MemoryStore(self.db)
        self.answers = na.AnswerStore(self.db)

    async def suggest(self, name: str, *, campaign: str | None = None, link: bool = True) -> str:
        cid = campaign or self.one.id
        written = await self.memory.add_entity(
            GUILD, cid, type="concept", name=name, source="scan", description="Used in a recap"
        )
        if link:
            await self.answers.link(GUILD, cid, AT, [written.value.id])
        return written.value.id

    async def known(self, name: str, kind: str = "place", *, secret: bool = False) -> str:
        written = await self.memory.add_entity(
            GUILD, self.one.id, type=kind, name=name, source="dm", status=CONFIRMED
        )
        if secret:
            await self.memory.add_alias(
                GUILD, self.one.id, written.value.id, name, kind="full", source="dm", secret=True
            )
        return written.value.id

    async def code(self, call: Any) -> str:
        with self.assertRaises(RecapError) as err:
            await call
        return err.exception.code

    async def listed(self) -> dict[str, Any]:
        return await web.read_checks(self.web, self.session, self.ref)

    async def ask(self, entity: str, **body: Any) -> dict[str, Any]:
        return await web.answer(self.web, self.session, self.ref, entity, body)

    async def apply_next(self, dms: Any = None) -> str:
        """The bot's loop for one answer: claim, apply, finish. Returns the reason word."""
        answer = await self.answers.claim_next(GUILD)
        assert answer is not None
        why = await na.apply(self.memory, GUILD, dms if dms is not None else {ALICE.id}, answer)
        await self.answers.finish(GUILD, answer, error=why)
        return why


class TheOldPronounRule(WithChecks):
    """The recap page and Discord's lists agree on the same stored rows (#2358): a pronoun the
    database marks as made before the gate is hidden in both, unless the DM wrote it."""

    async def mark_old(self, *ids: str) -> None:
        async with self.db.guild(GUILD) as conn:
            await conn.execute(
                "UPDATE memory_entities SET made_before_name_gate = true WHERE id = ANY(%s)",
                (list(ids),),
            )

    async def test_both_places_hide_the_same_old_pronouns(self) -> None:
        old = await self.suggest("We")
        new = await self.suggest("They")
        await self.mark_old(old)
        page = [n["name"] for n in (await self.listed())["names"]]
        discord = [
            e.name for e in await self.memory.entities(GUILD, self.one.id, statuses=[PROPOSED])
        ]
        self.assertEqual((page, discord), (["They"], ["They"]))
        self.assertNotIn("old", (await self.listed())["names"][0])
        self.assertNotEqual(old, new)

    async def test_the_dms_own_entry_is_shown_in_both_places(self) -> None:
        written = await self.memory.add_entity(
            GUILD, self.one.id, type="concept", name="We", source="dm", description=""
        )
        await self.answers.link(GUILD, self.one.id, AT, [written.value.id])
        await self.mark_old(written.value.id)
        page = [n["name"] for n in (await self.listed())["names"]]
        discord = [
            e.name for e in await self.memory.entities(GUILD, self.one.id, statuses=[PROPOSED])
        ]
        self.assertEqual((page, discord), (["We"], ["We"]))


class TheList(WithChecks):
    async def test_the_recaps_waiting_suggestions_are_listed_with_the_known_names(self) -> None:
        mira = await self.suggest("Mirelle")
        await self.known("Oakmere")
        got = await self.listed()
        self.assertEqual(
            got["names"],
            [{"id": mira, "name": "Mirelle", "note": "Used in a recap", "answering": False,
              "error": "", "longerNames": []}],
        )  # fmt: skip
        self.assertEqual([k["name"] for k in got["known"]], ["Oakmere"])
        self.assertNotIn("player_character", [k["key"] for k in got["kinds"]])
        self.assertIn("place", [k["key"] for k in got["kinds"]])

    async def test_a_name_answered_in_discord_or_here_leaves_the_list(self) -> None:
        one, two = await self.suggest("Mirelle"), await self.suggest("Vale Keep")
        await self.memory.set_entity_status(GUILD, self.one.id, one, REJECTED, source="dm")
        self.assertEqual([n["id"] for n in (await self.listed())["names"]], [two])

    async def test_a_secret_name_is_never_listed_nor_offered(self) -> None:
        secret = await self.suggest("Vecnor")
        await self.memory.add_alias(
            GUILD, self.one.id, secret, "Vecnor", kind="full", source="scan", secret=True
        )
        await self.known("The Hidden Keep", secret=True)
        await self.known("Oakmere")
        got = await self.listed()
        self.assertEqual(got["names"], [])
        self.assertEqual([k["name"] for k in got["known"]], ["Oakmere"])
        self.assertEqual(
            await self.code(self.ask(secret, action="not_a_name")), "not_found"
        )  # and it cannot be answered

    async def test_a_known_name_with_any_secret_spelling_is_not_offered(self) -> None:
        keep = await self.known("Oakmere")
        hidden = await self.known("Brother Aldric")
        await self.memory.add_alias(
            GUILD, self.one.id, hidden, "The Lich", kind="full", source="dm", secret=True
        )  # (a secret spelling that is not its main name)
        got = await self.listed()
        self.assertEqual([k["id"] for k in got["known"]], [keep])
        mira = await self.suggest("Mirelle")
        self.assertEqual(await self.code(self.ask(mira, action="same", known=hidden)), "bad_target")

    async def test_the_known_names_and_the_name_ids_are_held_to_the_campaign_and_the_recap(
        self,
    ) -> None:
        mine = await self.suggest("Mirelle")
        theirs = await self.memory.add_entity(
            GUILD, self.two.id, type="concept", name="Zorgul", source="scan"
        )
        their_known = await self.memory.add_entity(
            GUILD, self.two.id, type="place", name="Stormhold", source="dm", status=CONFIRMED
        )
        await self.answers.link(GUILD, self.two.id, AT, [theirs.value.id])
        self.assertEqual((await self.listed())["known"], [])  # another campaign's names: none
        # another campaign's confirmed name cannot be a merge target here
        self.assertEqual(
            await self.code(self.ask(mine, action="same", known=their_known.value.id)),
            "bad_target",
        )
        # another campaign's suggestion cannot be answered through this one
        self.assertEqual(
            await self.code(self.ask(theirs.value.id, action="not_a_name")), "not_found"
        )
        # a suggestion linked only to another recap of this campaign is not this recap's
        elsewhere = await self.suggest("Elsewhere", link=False)
        await self.answers.link(GUILD, self.one.id, 999, [elsewhere])
        self.assertEqual(await self.code(self.ask(elsewhere, action="not_a_name")), "not_found")
        entity = await self.memory.entity(GUILD, self.two.id, theirs.value.id)
        assert entity is not None and entity.status == PROPOSED  # nothing of theirs was touched

    async def test_another_recap_or_campaign_shows_none_of_it(self) -> None:
        await self.suggest("Mirelle")
        theirs = await self.memory.add_entity(
            GUILD, self.two.id, type="concept", name="Zorgul", source="scan"
        )
        await self.answers.link(GUILD, self.two.id, AT, [theirs.value.id])
        got = await web.read_checks(self.web, self.session, self.ref)
        self.assertEqual([n["name"] for n in got["names"]], ["Mirelle"])
        await self.answers.link(
            GUILD, self.one.id, 999, [theirs.value.id]
        )  # (not in this campaign)
        other_recap = await web.read_checks(self.web, self.session, self.ref)
        self.assertEqual([n["name"] for n in other_recap["names"]], ["Mirelle"])

    async def test_a_person_who_is_not_a_dm_of_the_campaign_is_told_nothing(self) -> None:
        mira = await self.suggest("Mirelle")
        token = await sessions.sign_in(self.web, cast(Any, BOB), [THURSDAY], now=NOW, days=30)
        bob = await sessions.find(self.web, token, now=NOW)
        assert bob is not None
        self.assertEqual(await self.code(web.read_checks(self.web, bob, self.ref)), "not_found")
        self.assertEqual(
            await self.code(web.answer(self.web, bob, self.ref, mira, {"action": "not_a_name"})),
            "not_found",
        )
        self.assertEqual(await self.answers.claim_next(GUILD), None)


class TheDoors(WithChecks):
    async def test_the_website_role_has_no_right_on_the_tables_only_the_doors(self) -> None:
        from psycopg import errors

        mira = await self.suggest("Mirelle")
        await self.ask(mira, action="not_a_name")
        for table in ("name_answers", "recap_name_checks"):
            with self.subTest(table=table), self.assertRaises(errors.InsufficientPrivilege):
                async with self.web.user(ALICE.id, session=self.session.id_hash) as conn:
                    await conn.execute(f"SELECT 1 FROM {table}")

    async def test_the_doors_are_for_a_dm_of_the_campaign_in_a_server_on_the_sessions_list(
        self,
    ) -> None:
        mira = await self.suggest("Mirelle")
        async with self.web.user(ALICE.id, session=self.session.id_hash) as conn:
            await conn.execute("SELECT set_config('dmbot.guild_id', %s, true)", (str(GUILD),))
            for guild, campaign in ((GUILD, self.two.id), (999, self.one.id)):  # not hers
                cur = await conn.execute(
                    "SELECT dmbot_recap_names_read(%s, %s, %s) AS r", (guild, campaign, AT)
                )
                self.assertIsNone((await cur.fetchone() or {})["r"])
                cur = await conn.execute(
                    "SELECT dmbot_name_answer_add(%s, %s, %s, %s, 'not_a_name', '', '', '') AS r",
                    (guild, campaign, AT, mira),
                )
                self.assertIsNone((await cur.fetchone() or {})["r"])


class TheDoorsFloor(WithChecks):
    async def test_the_database_refuses_control_characters_and_odd_lengths_by_itself(self) -> None:
        mira = await self.suggest("Mirelle")
        async with self.web.user(ALICE.id, session=self.session.id_hash) as conn:
            await conn.execute("SELECT set_config('dmbot.guild_id', %s, true)", (str(GUILD),))
            for action, text in (
                ("explain", "bad\x01note"),
                ("explain", "x" * 301),
                ("respell", "x" * 81),
                ("respell", ""),
                ("not_a_name", "text"),
            ):
                cur = await conn.execute(
                    "SELECT dmbot_name_answer_add(%s, %s, %s, %s, %s, '', '', %s) AS r",
                    (GUILD, self.one.id, AT, mira, action, text),
                )
                got = (await cur.fetchone() or {})["r"]
                self.assertEqual(got["status"], "bad_text", (action, text[:10]))


class TheAnswers(WithChecks):
    async def test_each_answer_is_queued_and_the_name_shows_it_is_on_its_way(self) -> None:
        mira = await self.suggest("Mirelle")
        oak = await self.known("Oakmere")
        for entity, body in (
            (mira, {"action": "confirm", "kind": "npc"}),
            (mira, {"action": "same", "known": oak}),
        ):
            got = await self.ask(entity, **body)
            self.assertRegex(got["answerId"], r"^[0-9a-f]{32}$")
            name = (await self.listed())["names"][0]
            self.assertTrue(name["answering"])
            await self.apply_next(dms=set())  # (refused: the sender is no DM, clearing the row)
            await self.answers.sweep(GUILD)

    async def test_the_words_and_the_choices_are_checked(self) -> None:
        mira = await self.suggest("Mirelle")
        oak = await self.known("Oakmere")
        bad: list[tuple[dict[str, Any], str]] = [
            ({"action": "dance"}, "bad_action"),
            ({}, "bad_action"),
            ({"action": "confirm"}, "bad_text"),
            ({"action": "confirm", "kind": "Not A Kind!"}, "bad_text"),
            ({"action": "same"}, "bad_target"),
            ({"action": "same", "known": "zz"}, "bad_target"),
            ({"action": "same", "known": "0" * 32}, "bad_target"),
            ({"action": "respell", "text": ""}, "bad_text"),
            ({"action": "respell", "text": "x" * 81}, "bad_text"),
            ({"action": "explain", "text": "x" * 301}, "bad_text"),
            ({"action": "explain", "text": "ok\x00"}, "bad_text"),
            ({"action": "respell", "text": "Mi\x00rel"}, "bad_text"),
            ({"action": "respell", "text": "Mi\x7frel"}, "bad_text"),
            ({"action": "confirm", "kind": "pla\x00ce"}, "bad_text"),
            ({"action": "confirm", "kind": "player_character"}, "bad_text"),
            ({"action": "confirm", "kind": "not_a_kind"}, "bad_text"),
            ({"action": "explain", "text": 7}, "bad_text"),
        ]
        for body, code in bad:
            with self.subTest(body=body):
                self.assertEqual(await self.code(self.ask(mira, **body)), code)
        self.assertEqual(
            await self.code(web.answer(self.web, self.session, self.ref, mira, "nope")),
            "bad_action",
        )
        self.assertEqual(await self.answers.claim_next(GUILD), None)  # nothing was queued
        await self.ask(mira, action="same", known=oak)  # a good one still goes

    async def test_a_second_answer_for_a_name_is_busy_until_the_first_is_done(self) -> None:
        mira = await self.suggest("Mirelle")
        await self.ask(mira, action="explain", text="The innkeeper's sister")
        self.assertEqual(await self.code(self.ask(mira, action="not_a_name")), "busy")
        self.assertEqual(await self.apply_next(), "")
        await self.ask(mira, action="not_a_name")  # the first is done: this one goes

    async def test_a_name_that_is_not_this_recaps_cannot_be_answered(self) -> None:
        stray = await self.suggest("Strayfolk", link=False)
        self.assertEqual(await self.code(self.ask(stray, action="not_a_name")), "not_found")
        self.assertEqual(await self.code(self.ask("0" * 32, action="not_a_name")), "not_found")
        self.assertEqual(await self.code(self.ask("short", action="not_a_name")), "not_found")

    async def test_a_dm_may_have_thirty_waiting_and_no_more(self) -> None:
        names = [await self.suggest(f"Placename{n}") for n in range(31)]
        for entity in names[:30]:
            await self.ask(entity, action="explain", text="note")
        self.assertEqual(
            await self.code(self.ask(names[30], action="explain", text="n")), "too_soon"
        )


class TheBot(WithChecks):
    async def test_it_is_right_confirms_it_as_that_kind_and_it_leaves_discords_list_too(
        self,
    ) -> None:
        mira = await self.suggest("Mirelle")
        await self.ask(mira, action="confirm", kind="place")
        self.assertEqual(await self.apply_next(), "")
        entity = await self.memory.entity(GUILD, self.one.id, mira)
        assert entity is not None
        self.assertEqual((entity.status, entity.type), (CONFIRMED, "place"))
        waiting = await self.memory.entities(GUILD, self.one.id, statuses=(PROPOSED,))
        self.assertEqual([e.id for e in waiting], [])  # Discord's list has no more of it
        self.assertEqual((await self.listed())["names"], [])  # and the page's neither

    async def test_a_players_character_is_asked_in_discord_not_here(self) -> None:
        mira = await self.suggest("Mirelle")
        # the page does not offer it, and the web layer refuses it...
        self.assertEqual(
            await self.code(self.ask(mira, action="confirm", kind="player_character")), "bad_text"
        )
        # ...and a request that got past (written by hand) is refused by the bot, with a reason
        async with self.db.guild(GUILD) as conn:
            await conn.execute(
                "INSERT INTO name_answers (guild_id, campaign_id, id, entity_id, user_id, action,"
                " kind, created_at) VALUES (%s, %s, %s, %s, %s, 'confirm', 'player_character', %s)",
                (GUILD, self.one.id, "a" * 32, mira, ALICE.id, int(time.time())),
            )
        self.assertEqual(await self.apply_next(), na.BAD_KIND)
        entity = await self.memory.entity(GUILD, self.one.id, mira)
        assert entity is not None and entity.status == PROPOSED
        listed = (await self.listed())["names"][0]
        self.assertEqual((listed["answering"], listed["error"]), (False, na.BAD_KIND))
        await self.ask(mira, action="confirm", kind="place")  # a new answer replaces the failure

    async def test_its_actually_a_known_name_joins_them_and_can_be_undone(self) -> None:
        mira = await self.suggest("Mirelle")
        oak = await self.known("Oakmere")
        await self.ask(mira, action="same", known=oak)
        self.assertEqual(await self.apply_next(), "")
        keep = await self.memory.entity(GUILD, self.one.id, oak)
        assert keep is not None
        aliases = await self.memory.aliases(GUILD, self.one.id, entity_id=oak)
        self.assertIn("mirelle", [a.key for a in aliases if a.status == CONFIRMED])
        self.assertEqual((await self.listed())["names"], [])

    async def test_a_known_name_the_page_would_not_offer_is_refused_at_the_door_and_the_bot(
        self,
    ) -> None:
        mira = await self.suggest("Mirelle")
        waiting = await self.suggest("Vale Keep")  # not confirmed
        hidden = await self.known("The Hidden Keep", secret=True)  # a secret name
        for target in (waiting, hidden):
            self.assertEqual(
                await self.code(self.ask(mira, action="same", known=target)), "bad_target"
            )
        # and a request that got past the door (written by hand) is refused by the bot as well
        for target in (waiting, hidden):
            async with self.db.guild(GUILD) as conn:
                await conn.execute(
                    "INSERT INTO name_answers (guild_id, campaign_id, id, entity_id, user_id,"
                    " action, target_id, created_at) VALUES (%s, %s, %s, %s, %s, 'same', %s, %s)",
                    (GUILD, self.one.id, target[:32], mira, ALICE.id, target, int(time.time())),
                )
            self.assertEqual(await self._apply_row(), na.NOT_JOINED)
            async with self.db.guild(GUILD) as conn:
                await conn.execute("DELETE FROM name_answers")
        entity = await self.memory.entity(GUILD, self.one.id, mira)
        assert entity is not None and entity.status == PROPOSED  # nothing was merged

    async def _apply_row(self) -> str:
        answer = await self.answers.claim_next(GUILD)
        assert answer is not None
        why = await na.apply(self.memory, GUILD, {ALICE.id}, answer)
        await self.answers.finish(GUILD, answer, error=why)
        return why

    async def test_a_name_made_secret_after_it_was_queued_is_not_answered(self) -> None:
        mira = await self.suggest("Mirelle")
        await self.ask(mira, action="explain", text="the innkeeper's sister")
        await self.memory.add_alias(
            GUILD, self.one.id, mira, "Mirelle", kind="full", source="scan", secret=True
        )
        self.assertEqual(await self.apply_next(), na.GONE)

    async def test_a_fixed_spelling_renames_the_suggestion_and_it_stays_to_answer(self) -> None:
        mira = await self.suggest("Mirel")
        await self.ask(mira, action="respell", text="Mirelle")
        self.assertEqual(await self.apply_next(), "")
        entity = await self.memory.entity(GUILD, self.one.id, mira)
        assert entity is not None
        self.assertEqual((entity.name, entity.status), ("Mirelle", PROPOSED))
        self.assertEqual([n["name"] for n in (await self.listed())["names"]], ["Mirelle"])

    async def test_not_a_name_rejects_it(self) -> None:
        mira = await self.suggest("Mirelle")
        await self.ask(mira, action="not_a_name")
        self.assertEqual(await self.apply_next(), "")
        entity = await self.memory.entity(GUILD, self.one.id, mira)
        assert entity is not None and entity.status == REJECTED

    async def test_a_note_is_kept_with_the_name_up_to_300_letters(self) -> None:
        mira = await self.suggest("Mirelle")
        note = "The innkeeper's sister, " + "who left for the coast. " * 11
        self.assertLessEqual(len(note), 300)
        await self.ask(mira, action="explain", text=note)
        self.assertEqual(await self.apply_next(), "")
        entity = await self.memory.entity(GUILD, self.one.id, mira)
        assert entity is not None
        self.assertEqual((entity.description, entity.status), (note.strip(), PROPOSED))
        self.assertEqual((await self.listed())["names"][0]["note"], note.strip())

    async def test_a_sender_who_is_no_longer_a_dm_changes_nothing(self) -> None:
        mira = await self.suggest("Mirelle")
        await self.ask(mira, action="not_a_name")
        self.assertEqual(await self.apply_next(dms={BOB.id}), na.NOT_ALLOWED)
        entity = await self.memory.entity(GUILD, self.one.id, mira)
        assert entity is not None and entity.status == PROPOSED

    async def test_a_name_answered_in_discord_meanwhile_is_gone_not_changed(self) -> None:
        mira = await self.suggest("Mirelle")
        await self.ask(mira, action="confirm", kind="place")
        await self.memory.set_entity_status(GUILD, self.one.id, mira, REJECTED, source="dm")
        self.assertEqual(await self.apply_next(), na.GONE)

    async def test_a_stalled_claim_is_taken_back_and_only_the_holder_can_finish(self) -> None:
        mira = await self.suggest("Mirelle")
        await self.ask(mira, action="not_a_name")
        first = await self.answers.claim_next(GUILD)
        assert first is not None
        self.assertIsNone(await self.answers.claim_next(GUILD))  # in hand: not given twice
        later = na.AnswerStore(self.db, clock=lambda: 10**10)
        second = await later.claim_next(GUILD)
        assert second is not None and second.id == first.id
        self.assertFalse(await self.answers.finish(GUILD, first))  # the old holder changes nothing
        self.assertTrue(await later.finish(GUILD, second))

    async def test_old_answers_are_swept(self) -> None:
        mira = await self.suggest("Mirelle")
        await self.ask(mira, action="explain", text="note")
        self.assertEqual(await self.answers.sweep(GUILD), 0)
        later = na.AnswerStore(self.db, clock=lambda: 10**10)
        self.assertEqual(await later.sweep(GUILD), 1)


class TheChangedWords(WithChecks):
    """✏️ Change the words on the page (#2593, #2570): the very call Discord's form makes."""

    async def aliases(self, entity: str) -> dict[str, str]:
        found = await self.memory.aliases(GUILD, self.one.id, entity_id=entity)
        return {a.text: a.kind for a in found}

    async def test_the_longer_names_the_recap_found_are_listed_with_the_card(self) -> None:
        written = await self.memory.add_entity(
            GUILD, self.one.id, type="concept", name="Harrow", source="scan", description=""
        )
        await self.answers.link(
            GUILD, self.one.id, AT, [written.value.id], {written.value.id: ["a", "b", "c", "d"]}
        )
        (card,) = (await self.listed())["names"]
        self.assertEqual(card["longerNames"], ["a", "b", "c"])  # at most three
        await self.answers.link(
            GUILD, self.one.id, AT, [written.value.id], {}
        )  # not read this time (a failed read): what was stored stays
        self.assertEqual((await self.listed())["names"][0]["longerNames"], ["a", "b", "c"])
        await self.answers.link(
            GUILD, self.one.id, AT, [written.value.id], {written.value.id: []}
        )  # read, and none found: brought up to date
        self.assertEqual((await self.listed())["names"][0]["longerNames"], [])

    async def test_the_words_change_in_one_change_and_the_card_stays(self) -> None:
        harrow = await self.suggest("Harrow")
        await self.ask(harrow, action="change_words", words="Harrow's Keep", heard="Harrow's car")
        self.assertTrue((await self.listed())["names"][0]["answering"])
        self.assertEqual(await self.apply_next(), "")
        (card,) = (await self.listed())["names"]
        self.assertEqual(
            (card["id"], card["name"], card["answering"]), (harrow, "Harrow's Keep", False)
        )
        entity = await self.memory.entity(GUILD, self.one.id, harrow)
        assert entity is not None
        self.assertEqual(
            (entity.name, entity.status), ("Harrow's Keep", PROPOSED)
        )  # still to answer
        names = await self.aliases(harrow)
        self.assertEqual(names.get("Harrow's car"), "old")  # how it was heard: the Cleaner fixes it
        self.assertNotIn("Harrow", names)  # the short word is part of the new name: not kept

    async def test_a_name_that_is_not_part_of_the_new_one_is_kept_as_an_old_spelling(self) -> None:
        mira = await self.suggest("Mirele")
        await self.ask(mira, action="change_words", words="Mirelle")
        self.assertEqual(await self.apply_next(), "")
        self.assertEqual((await self.aliases(mira)).get("Mirele"), "old")

    async def test_no_words_or_too_many_are_refused_before_anything_is_queued(self) -> None:
        mira = await self.suggest("Mirelle")
        for body, code in (
            ({"action": "change_words", "words": ""}, "empty"),
            ({"action": "change_words"}, "empty"),
            ({"action": "change_words", "words": "  "}, "empty"),
            ({"action": "change_words", "words": "x" * 101}, "too_long"),
            ({"action": "change_words", "words": "Ok", "heard": "y" * 101}, "too_long"),
            ({"action": "change_words", "words": "Mi\x00"}, "bad_text"),
        ):
            with self.subTest(body=body):
                self.assertEqual(await self.code(self.ask(mira, **body)), code)
        self.assertIsNone(await self.answers.claim_next(GUILD))

    async def test_a_name_already_known_is_refused_by_the_bot(self) -> None:
        await self.known("Oakmere")
        mira = await self.suggest("Oak")
        await self.ask(mira, action="change_words", words="oakmere")
        self.assertEqual(await self.apply_next(), na.ALREADY_KNOWN)
        (card,) = (await self.listed())["names"]
        self.assertEqual((card["name"], card["error"]), ("Oak", "already_known"))

    async def test_another_entrys_secret_name_is_refused_without_saying_whose(self) -> None:
        await self.known("Vael", "place", secret=True)
        mira = await self.suggest("Vae")
        await self.ask(mira, action="change_words", words="Vael")
        self.assertEqual(await self.apply_next(), na.SECRET_CLASH)
        (card,) = (await self.listed())["names"]
        self.assertEqual(card["error"], "secret_clash")
        self.assertNotIn("Vael", str(await self.listed()))  # (the secret name is not listed at all)

    async def test_a_player_gets_not_found_and_a_changed_dm_is_refused_by_the_bot(self) -> None:
        mira = await self.suggest("Mirelle")
        bob = await sessions.sign_in(self.web, BOB, [THURSDAY], now=NOW, days=30)
        player = await sessions.find(self.web, bob, now=NOW)
        assert player is not None
        with self.assertRaises(RecapError) as err:
            await web.answer(
                self.web, player, self.ref, mira, {"action": "change_words", "words": "Mirelle X"}
            )
        self.assertEqual(err.exception.code, "not_found")
        await self.ask(mira, action="change_words", words="Mirelle X")
        self.assertEqual(await self.apply_next(dms=set()), na.NOT_ALLOWED)  # no longer a DM
        entity = await self.memory.entity(GUILD, self.one.id, mira)
        assert entity is not None
        self.assertEqual(entity.name, "Mirelle")

    async def test_another_campaigns_name_cannot_be_changed_from_this_recap(self) -> None:
        other = (await self.campaigns.create(GUILD, "Elsewhere", ALICE.id)).id
        stray = await self.suggest("Strayfolk", campaign=other)
        self.assertEqual(
            await self.code(self.ask(stray, action="change_words", words="Strayfolk Hall")),
            "not_found",
        )

    async def test_it_is_one_change_with_one_undo(self) -> None:
        harrow = await self.suggest("Harrow")
        await self.ask(harrow, action="change_words", words="Harrow's Keep", heard="Harrow's keep")
        self.assertEqual(await self.apply_next(), "")
        async with self.db.guild(GUILD) as conn:  # the rename is the last change made
            cur = await conn.execute(
                "SELECT max(batch) AS b FROM memory_changes WHERE campaign_id = %s", (self.one.id,)
            )
            row = await cur.fetchone()
        assert row is not None
        await self.memory.undo(GUILD, self.one.id, int(row["b"]), source="dm")
        entity = await self.memory.entity(GUILD, self.one.id, harrow)
        assert entity is not None
        self.assertEqual(entity.name, "Harrow")


class TheLoop(WithChecks):
    """The bot's loop and the link it writes when it queues a recap's names."""

    def bot(self) -> Any:
        guild = SimpleNamespace(id=GUILD)
        stale: list[tuple[int, str]] = []
        bot = SimpleNamespace(
            guilds=[guild],
            name_answers=self.answers,
            memory=self.memory,
            campaigns=self.campaigns,
            lookup=SimpleNamespace(mark_stale=lambda g, c: stale.append((g, c))),
            stale=stale,
        )
        bot.run_name_answers = bot_module.DMBot.run_name_answers.__get__(bot)
        bot._link_recap_names = bot_module.DMBot._link_recap_names.__get__(bot)
        bot._longer_names = bot_module.DMBot._longer_names.__get__(bot)
        return bot

    async def test_the_loop_applies_the_answer_and_refreshes_the_lists(self) -> None:
        bot = self.bot()
        mira = await self.suggest("Mirelle")
        await self.ask(mira, action="not_a_name")
        await bot.run_name_answers()
        entity = await self.memory.entity(GUILD, self.one.id, mira)
        assert entity is not None and entity.status == REJECTED
        self.assertEqual(bot.stale, [(GUILD, self.one.id)])

    async def test_a_sender_who_stopped_being_a_dm_is_refused_by_the_loop(self) -> None:
        bot = self.bot()
        mira = await self.suggest("Mirelle")
        await self.ask(mira, action="not_a_name")
        async with self.db.guild(GUILD) as conn:
            await conn.execute("DELETE FROM campaign_dms WHERE user_id = %s", (ALICE.id,))
        await bot.run_name_answers()
        entity = await self.memory.entity(GUILD, self.one.id, mira)
        assert entity is not None and entity.status == PROPOSED
        self.assertEqual(bot.stale, [])

    async def test_the_loop_logs_outcomes_never_the_dms_words(self) -> None:
        bot = self.bot()
        mira = await self.suggest("Mirelle")
        word = "Zanzibarquill"
        await self.ask(mira, action="explain", text=f"{word} is her sister")
        with self.assertLogs(level=logging.INFO) as logs:
            await bot.run_name_answers()
            logging.getLogger("probe").info("probe")  # (assertLogs needs one record)
        self.assertNotIn(word, "\n".join(logs.output))
        bot.memory = SimpleNamespace(entity=self._boom)
        await self.ask(mira, action="not_a_name")
        with self.assertLogs(level=logging.INFO) as failed:
            await bot.run_name_answers()
        self.assertNotIn("down", "\n".join(failed.output))  # the type only, not the message

    async def test_a_failed_answer_is_marked_for_the_page_and_the_loop_goes_on(self) -> None:
        bot = self.bot()
        bot.memory = SimpleNamespace(entity=self._boom)
        mira = await self.suggest("Mirelle")
        await self.ask(mira, action="not_a_name")
        await bot.run_name_answers()
        self.assertEqual((await self.listed())["names"][0]["error"], na.FAILED)

    @staticmethod
    async def _boom(*args: Any) -> None:
        raise RuntimeError("down")

    async def test_the_recaps_names_are_linked_by_their_spelling_to_waiting_suggestions(
        self,
    ) -> None:
        bot = self.bot()
        mira = await self.suggest("Mirelle", link=False)
        await self.suggest("Other Name", link=False)
        job = SimpleNamespace(session_at=AT, to_confirm=("mirelle", "Not Suggested"))
        await bot._link_recap_names(GUILD, self.one.id, job)
        await bot._link_recap_names(GUILD, self.one.id, job)  # a repeat changes nothing
        self.assertEqual([n["id"] for n in (await self.listed())["names"]], [mira])

    async def test_the_cards_quotes_give_the_pages_longer_names(self) -> None:
        from unittest.mock import AsyncMock, patch

        from dmbot.ui import name_places

        bot = self.bot()
        bot.transcripts = object()
        bot.consent = SimpleNamespace(consenting=AsyncMock(return_value={ALICE.id}))
        bot.memory = SimpleNamespace(
            entities=self.memory.entities, mentions=AsyncMock(return_value=[("ref", 1)])
        )
        harrow = await self.suggest("Harrow", link=False)
        job = SimpleNamespace(session_at=AT, to_confirm=("Harrow",))
        quotes = ["> We rode to **Harrow**'s Keep and Harrow's Keep again."]
        with patch.object(
            name_places, "first_places", AsyncMock(return_value=name_places.Places(quotes))
        ):
            await bot._link_recap_names(GUILD, self.one.id, job)
        (card,) = (await self.listed())["names"]
        self.assertEqual((card["id"], card["longerNames"]), (harrow, ["Harrow's Keep"]))

    async def test_a_name_that_is_removed_takes_its_answers_and_links_with_it(self) -> None:
        mira = await self.suggest("Mirelle")
        await self.ask(mira, action="not_a_name")
        async with self.db.guild(GUILD) as conn:
            await conn.execute("DELETE FROM memory_entities WHERE id = %s", (mira,))
        self.assertIsNone(await self.answers.claim_next(GUILD))
        self.assertEqual((await self.listed())["names"], [])
        await asyncio.sleep(0)
