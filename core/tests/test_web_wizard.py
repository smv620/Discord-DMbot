"""The campaign start-up wizard's draft on the website (#3955, migration 0409). Run as the website's
own database role: only the DM who started a draft can read or change it, in the server it was
started in; the answers keep to their whitelist and shapes; documents are counted and never echoed;
the right-to-use confirmations and the finish queue follow the design (docs/WIZARD_DESIGN.md). Real
Postgres; invented names."""

from __future__ import annotations

from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from dmbot.web import sessions, wizard
from dmbot.web.discord import DiscordUser
from dmbot.web.recaps import RecapError
from tests.test_recap_web import BOB, NOW, Base
from tests.test_web_api import THURSDAY

SECRET_TEXT = "Mirelle Vane hides the red door."


class Wizard(Base):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        token = await sessions.sign_in(
            self.web,
            DiscordUser(id=BOB.id, name=BOB.name, email=BOB.email),
            [THURSDAY],
            now=NOW,
            days=30,
        )
        found = await sessions.find(self.web, token, now=NOW)
        assert found is not None
        self.bob = found
        self.g = THURSDAY.id

    async def call(self, fn: Any, *args: Any, who: Any = None, **kw: Any) -> Any:
        """A wrapper call as Alice (or `who`) in the test server."""
        return await fn(self.web, who or self.session, self.g, *args, **kw)

    async def refused(self, fn: Any, *args: Any, who: Any = None, **kw: Any) -> str:
        with self.assertRaises(RecapError) as caught:
            await self.call(fn, *args, who=who, **kw)
        return str(caught.exception.code)

    async def draft(self, name: str = "Frostmaiden", who: Any = None) -> None:
        await self.call(wizard.start, name, who=who)

    async def doc(self, slot: str = "lore", name: str = "a.txt", **kw: Any) -> str:
        found: str = await self.call(
            wizard.add_document, slot=slot, name=name, source=kw.pop("source", "upload"), **kw
        )
        return found

    async def rows(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        async with self.db.guild(self.g) as conn:
            cur = await conn.execute(sql, args)
            return list(await cur.fetchall())


class TheDraft(Wizard):
    async def test_there_is_no_draft_until_one_is_started_and_it_keeps_its_name(self) -> None:
        self.assertIsNone(await self.call(wizard.get))
        await self.draft()
        got = await self.call(wizard.get)
        self.assertEqual((got["step"], got["answers"]), (1, {"name": "Frostmaiden"}))
        self.assertGreater(got["expiresAt"], got["now"] + 6 * 86400)
        await self.draft("Other")  # (kept, not replaced)
        self.assertEqual((await self.call(wizard.get))["answers"]["name"], "Frostmaiden")

    async def test_another_dm_and_another_server_see_nothing(self) -> None:
        await self.draft()
        self.assertIsNone(await self.call(wizard.get, who=self.bob))  # another DM
        with self.assertRaises(RecapError):
            await wizard.get(self.web, self.session, 999)  # a server not in the session's list
        await self.draft("Bob's", who=self.bob)
        self.assertEqual((await self.call(wizard.get))["answers"]["name"], "Frostmaiden")

    async def test_a_draft_past_its_week_is_gone(self) -> None:
        await self.draft()
        await self.rows("UPDATE wizard_drafts SET expires_at = 1 RETURNING 1")
        self.assertIsNone(await self.call(wizard.get))
        self.assertEqual(await self.refused(wizard.update, 2, {}), "no_draft")
        await self.draft("Fresh")  # (a new start sweeps the old one)
        self.assertEqual((await self.call(wizard.get))["answers"]["name"], "Fresh")

    async def test_the_website_role_cannot_read_the_tables_directly(self) -> None:
        await self.draft()
        for table in ("wizard_drafts", "wizard_documents", "wizard_finishes"):
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                async with self.web.user(self.session.user_id, session=self.session.id_hash) as c:
                    await c.execute("SELECT set_config('dmbot.guild_id', %s, true)", (str(self.g),))
                    await c.execute(f"SELECT * FROM {table}")

    async def test_an_expired_draft_is_removed_with_its_documents_not_only_hidden(self) -> None:
        await self.draft()
        await self.doc(text=SECRET_TEXT)
        await self.rows("UPDATE wizard_drafts SET expires_at = 1 RETURNING 1")
        self.assertIsNone(await self.call(wizard.get))
        counts = await self.rows(
            "SELECT (SELECT count(*) FROM wizard_drafts) AS d,"
            " (SELECT count(*) FROM wizard_documents) AS x"
        )
        self.assertEqual((counts[0]["d"], counts[0]["x"]), (0, 0))

    async def test_an_expired_draft_takes_no_more_documents_answers_or_finish(self) -> None:
        await self.draft()
        await self.rows("UPDATE wizard_drafts SET expires_at = 1 RETURNING 1")
        for call in (wizard.skip, wizard.confirm_rights):
            arg = 3 if call is wizard.skip else "shared"
            self.assertEqual(await self.refused(call, arg), "no_draft")
        self.assertEqual(await self.refused(wizard.finish), "no_draft")
        self.assertEqual(
            await self.refused(wizard.add_document, slot="lore", name="a", source="link"),
            "no_draft",
        )

    async def test_a_nul_character_or_a_wrong_type_is_a_word_not_a_crash(self) -> None:
        await self.draft()
        self.assertEqual(await self.refused(wizard.update, 2, {"name": "a\x00b"}), "bad_name")
        self.assertEqual(await self.refused(wizard.update, "2", {}), "bad_patch")
        add = wizard.add_document
        for bad in (
            {"name": "a\x00"},
            {"name": "   "},
            {"pages": True},
            {"words": "3"},
            {"text": "a\x00"},
        ):
            args = {"slot": "lore", "name": "a", "source": "upload", **bad}
            self.assertEqual(await self.refused(add, **args), "bad_document")

    async def test_discard_removes_the_draft_and_its_documents(self) -> None:
        await self.draft()
        await self.doc(text=SECRET_TEXT, words=5)
        await self.call(wizard.discard)
        self.assertIsNone(await self.call(wizard.get))
        self.assertEqual((await self.rows("SELECT count(*) AS n FROM wizard_documents"))[0]["n"], 0)


class TheAccount(Wizard):
    async def test_a_person_with_drafts_in_two_servers_loses_both(self) -> None:
        from dmbot import accounts
        from tests.test_web_api import ALICE

        await self.draft()
        await self.rows(
            "INSERT INTO wizard_drafts (guild_id, user_id, created_at, updated_at, expires_at)"
            " VALUES (%s, %s, 1, 1, 99999999999) RETURNING 1",
            self.g,
            ALICE.id + 1,
        )  # (someone else's, same server)
        async with self.db.guild(222) as conn:
            await conn.execute(
                "INSERT INTO wizard_drafts (guild_id, user_id, created_at, updated_at, expires_at)"
                " VALUES (222, %s, 1, 1, 99999999999)",
                (ALICE.id,),
            )
        await accounts.delete_person(self.db, ALICE.id)
        [mine] = await self.rows(
            "SELECT count(*) AS n FROM wizard_drafts WHERE user_id = %s", ALICE.id
        )
        self.assertEqual(mine["n"], 0)
        async with self.db.guild(222) as conn:
            cur = await conn.execute("SELECT count(*) AS n FROM wizard_drafts")
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual(row["n"], 0)

    async def test_deleting_the_account_deletes_the_draft_its_documents_and_runs(self) -> None:
        from dmbot import accounts
        from tests.test_web_api import ALICE

        await self.draft()
        await self.doc(text=SECRET_TEXT)
        await self.call(wizard.confirm_rights, "shared")
        await self.call(wizard.finish)
        await self.draft("Bob's", who=self.bob)
        await accounts.delete_person(self.db, ALICE.id)
        counts = await self.rows(
            "SELECT (SELECT count(*) FROM wizard_drafts) AS d,"
            " (SELECT count(*) FROM wizard_documents) AS x,"
            " (SELECT count(*) FROM wizard_finishes) AS f"
        )
        self.assertEqual((counts[0]["d"], counts[0]["x"], counts[0]["f"]), (1, 0, 0))  # Bob's stays


class TheAnswers(Wizard):
    async def test_a_step_saves_its_answers_and_moves_on(self) -> None:
        await self.draft()
        await self.call(
            wizard.update, 6,
            {"target": "2024", "fallback": "2014", "supplements": ["tasha", "xanathar"],
             "data_use": True},
        )  # fmt: skip
        got = await self.call(wizard.get)
        self.assertEqual(got["step"], 6)
        self.assertEqual(got["answers"]["supplements"], ["xanathar", "tasha"])  # (book order)
        self.assertEqual(got["answers"]["name"], "Frostmaiden")  # (merged, not replaced)

    async def test_bad_answers_are_refused_by_name(self) -> None:
        await self.draft()
        bad: list[tuple[dict[str, Any], str]] = [
            ({"invite": "https://example.com/not-dndbeyond"}, "bad_invite"),
            ({"target": "3000"}, "bad_target"),
            ({"target": "2024", "fallback": "2024"}, "bad_fallback"),
            ({"supplements": ["necronomicon"]}, "bad_supplements"),
            ({"characters": [{"full": "x" * 101}]}, "bad_characters"),
            ({"name": "x" * 101}, "bad_name"),
            ({"data_use": "yes"}, "bad_data_use"),
            ({"password": "hunter2"}, "bad_key"),
        ]
        for patch, code in bad:
            self.assertEqual(await self.refused(wizard.update, 2, patch), code)

    async def test_the_door_keeps_the_key_list_even_if_the_wrapper_is_skipped(self) -> None:
        await self.draft()
        async with self.web.user(self.session.user_id, session=self.session.id_hash) as conn:
            await conn.execute("SELECT set_config('dmbot.guild_id', %s, true)", (str(self.g),))
            cur = await conn.execute(
                "SELECT dmbot_web_wizard_set(%s, 2, %s) AS found", (self.g, Jsonb({"cookie": "x"}))
            )
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual(row["found"]["status"], "bad_key")

    async def test_skipping_a_step_is_remembered_once_and_moves_on(self) -> None:
        await self.draft()
        await self.call(wizard.skip, 4)
        await self.call(wizard.skip, 4)
        got = await self.call(wizard.get)
        self.assertEqual((got["step"], got["answers"]["skipped"]), (5, [4]))


class TheDocuments(Wizard):
    async def test_a_document_is_counted_and_its_text_is_never_given_back(self) -> None:
        await self.draft()
        doc = await self.doc("secrets", "secrets.txt", text=SECRET_TEXT, pages=1, words=6)
        got = await self.call(wizard.get)
        [shown] = got["documents"]
        self.assertEqual(
            (shown["id"], shown["slot"], shown["name"]), (doc, "secrets", "secrets.txt")
        )
        self.assertEqual((shown["pages"], shown["words"], shown["status"]), (1, 6, "ok"))
        self.assertNotIn("Mirelle", str(got))
        await self.call(wizard.remove_document, doc)
        self.assertEqual((await self.call(wizard.get))["documents"], [])

    async def test_limits_and_shapes_are_kept(self) -> None:
        await self.draft()
        add = wizard.add_document
        big = await self.refused(add, slot="lore", name="a", source="upload", text="x" * 262_145)
        self.assertEqual(big, "too_big")
        self.assertEqual(
            await self.refused(add, slot="songs", name="a", source="upload"), "bad_document"
        )
        for n in range(20):
            await self.doc(name=f"{n}.txt", source="link")
        self.assertEqual(await self.refused(add, slot="lore", name="21", source="link"), "too_many")

    async def test_another_dm_cannot_remove_a_document(self) -> None:
        await self.draft()
        doc = await self.doc()
        await self.draft("Bob's", who=self.bob)
        self.assertEqual(await self.refused(wizard.remove_document, doc, who=self.bob), "not_found")
        self.assertEqual(len((await self.call(wizard.get))["documents"]), 1)


class TheFinish(Wizard):
    async def test_a_draft_without_a_name_is_not_finished(self) -> None:
        await self.draft("")
        self.assertEqual(await self.refused(wizard.finish), "needs_name")

    async def test_documents_need_the_right_to_use_confirmation_first(self) -> None:
        await self.draft()
        await self.doc()
        self.assertEqual(await self.refused(wizard.finish), "needs_rights")
        await self.call(wizard.confirm_rights, "shared")
        self.assertEqual(len(await self.call(wizard.finish)), 32)

    async def test_a_ruleset_needs_its_own_confirmation(self) -> None:
        await self.draft()
        await self.doc("ruleset", "r.txt")
        await self.call(wizard.confirm_rights, "shared")
        self.assertEqual(await self.refused(wizard.finish), "needs_rights")
        await self.call(wizard.confirm_rights, "ruleset")
        await self.call(wizard.finish)

    async def test_a_second_press_gets_the_running_run_and_the_state_is_readable(self) -> None:
        await self.draft()
        first = await self.call(wizard.finish)
        self.assertEqual(await self.call(wizard.finish), first)
        state = await self.call(wizard.finish_state)
        self.assertEqual(
            (state["id"], state["state"], state["stage"]), (first, "waiting", "waiting")
        )
        self.assertIsNone(await self.call(wizard.finish_state, who=self.bob))  # not Bob's

    async def test_a_run_that_has_not_moved_for_half_an_hour_is_lost_and_can_be_pressed_again(
        self,
    ) -> None:
        await self.draft()
        first = await self.call(wizard.finish)
        await self.rows(
            "UPDATE wizard_finishes SET created_at = created_at - 3600,"
            " moved_at = moved_at - 3600 RETURNING 1"
        )
        second = await self.call(wizard.finish)
        self.assertNotEqual(first, second)
        [row] = await self.rows("SELECT state, error FROM wizard_finishes WHERE id = %s", first)
        self.assertEqual((row["state"], row["error"]), ("failed", "lost"))
