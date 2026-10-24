"""A "said as" spelling for the recap narrator (#1419): set and cleared as the DM, undoable,
never on a secret name, carried in backups, read into the narrator's names, and the check."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

from psycopg import errors as pg_errors

from dmbot.bot import DMBot
from dmbot.campaigns.store import decode_backup, encode_backup
from dmbot.memory.lookup import LookupCache
from dmbot.memory.models import DM as DM_SOURCE
from dmbot.memory.models import MemoryRuleError
from dmbot.recaps.audio import names as nm
from tests.test_memory_store import DM, GUILD_A, GUILD_B, MemoryTest  # DM: a user id


class SaidAs(MemoryTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.kazeth = await self.add("Ka'zeth")

    async def said(self) -> str | None:
        return await self.memory.said_as(GUILD_A, self.c, self.kazeth)

    async def test_the_dm_sets_changes_and_clears_it(self) -> None:
        self.assertIsNone(await self.said())
        written = await self.memory.set_said_as(
            GUILD_A, self.c, self.kazeth, "  Kah-zeth ", source=DM_SOURCE
        )
        self.assertEqual(written.value.said_as, "Kah-zeth")  # spaces tidied
        self.assertEqual(await self.said(), "Kah-zeth")
        await self.memory.set_said_as(GUILD_A, self.c, self.kazeth, "Kaz-eth", source=DM_SOURCE)
        self.assertEqual(await self.said(), "Kaz-eth")
        await self.memory.set_said_as(GUILD_A, self.c, self.kazeth, "", source=DM_SOURCE)
        self.assertIsNone(await self.said())

    async def test_it_can_be_undone_like_any_other_edit(self) -> None:
        await self.memory.set_said_as(GUILD_A, self.c, self.kazeth, "Kah-zeth", source=DM_SOURCE)
        second = await self.memory.set_said_as(
            GUILD_A, self.c, self.kazeth, "Kaz-eth", source=DM_SOURCE
        )
        assert second.batch is not None
        await self.memory.undo(GUILD_A, self.c, second.batch, source=DM_SOURCE)
        self.assertEqual(await self.said(), "Kah-zeth")

    async def test_only_the_dm_and_only_a_sensible_spelling(self) -> None:
        for source in ("cleaner", "entitybot", "undo"):
            with self.assertRaises(MemoryRuleError):
                await self.memory.set_said_as(GUILD_A, self.c, self.kazeth, "x", source=source)
        for bad in ("x" * 81, "bell\x07"):
            with self.assertRaises(MemoryRuleError):
                await self.memory.set_said_as(GUILD_A, self.c, self.kazeth, bad, source=DM_SOURCE)
        with self.assertRaises(MemoryRuleError):
            await self.memory.set_said_as(GUILD_A, self.c, "0" * 32, "x", source=DM_SOURCE)
        self.assertIsNone(await self.said())

    async def test_it_stays_in_its_own_campaign_and_server(self) -> None:
        other = (await self.campaigns.create(GUILD_A, "Other", DM)).id
        twin = await self.add("Ka'zeth", campaign=other)
        await self.memory.set_said_as(GUILD_A, self.c, self.kazeth, "Kah-zeth", source=DM_SOURCE)
        self.assertIsNone(await self.memory.said_as(GUILD_A, other, twin))
        with self.assertRaises(MemoryRuleError):  # the campaign isn't in that server
            await self.memory.said_as(GUILD_B, self.c, self.kazeth)

    async def test_making_a_name_secret_clears_its_spelling_and_the_database_refuses_the_pair(
        self,
    ) -> None:
        alias = await self.memory.add_alias(
            GUILD_A, self.c, self.kazeth, "the Grey Voice", kind="title", source=DM_SOURCE
        )
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "UPDATE memory_aliases SET said_as = 'Grey' WHERE id = %s", (alias.value.id,)
            )
        await self.memory.update_alias(
            GUILD_A, self.c, alias.value.id, source=DM_SOURCE, secret=True
        )
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute(
                "SELECT said_as FROM memory_aliases WHERE id = %s", (alias.value.id,)
            )
            row = await cur.fetchone()
        assert row is not None
        self.assertIsNone(row["said_as"])
        with self.assertRaises(pg_errors.CheckViolation):  # even if code forgot
            async with self.db.guild(GUILD_A) as conn:
                await conn.execute(
                    "UPDATE memory_aliases SET said_as = 'Grey' WHERE id = %s", (alias.value.id,)
                )

    async def test_saying_a_known_name_again_as_secret_clears_its_spelling(self) -> None:
        alias = await self.memory.add_alias(
            GUILD_A, self.c, self.kazeth, "Zeth", kind="short", source=DM_SOURCE
        )
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "UPDATE memory_aliases SET said_as = 'Zet' WHERE id = %s", (alias.value.id,)
            )
        again = await self.memory.add_alias(
            GUILD_A, self.c, self.kazeth, "Zeth", kind="short", source=DM_SOURCE, secret=True
        )
        self.assertTrue(again.value.secret)
        self.assertIsNone(again.value.said_as)

    async def test_it_goes_in_a_backup_and_an_older_backup_without_it_still_loads(self) -> None:
        await self.memory.set_said_as(GUILD_A, self.c, self.kazeth, "Kah-zeth", source=DM_SOURCE)
        backup = await self.campaigns.export(GUILD_A, self.c)
        restored = await self.campaigns.import_backup(
            GUILD_B, decode_backup(encode_backup(backup)), DM
        )
        entity = (await self.memory.entities(GUILD_B, restored.id))[0]
        self.assertEqual(await self.memory.said_as(GUILD_B, restored.id, entity.id), "Kah-zeth")
        # A file from before the column has no `said_as` on its names.
        old = await self.campaigns.export(GUILD_A, self.c)
        for row in old["sections"]["memory"]:
            if isinstance(row, dict) and row.get("table") == "alias":
                row.pop("said_as", None)
        older = await self.campaigns.import_backup(GUILD_B, decode_backup(encode_backup(old)), DM)
        entity = (await self.memory.entities(GUILD_B, older.id))[0]
        self.assertIsNone(await self.memory.said_as(GUILD_B, older.id, entity.id))

    async def test_a_backup_that_gives_a_secret_name_a_spelling_is_refused(self) -> None:
        from dmbot.campaigns.models import CampaignError

        await self.memory.add_alias(
            GUILD_A,
            self.c,
            self.kazeth,
            "the Grey Voice",
            kind="title",
            source=DM_SOURCE,
            secret=True,
        )
        backup = await self.campaigns.export(GUILD_A, self.c)
        for row in backup["sections"]["memory"]:
            if isinstance(row, dict) and row.get("table") == "alias" and row["secret"]:
                row["said_as"] = "Grey"
        with self.assertRaises(CampaignError):
            await self.campaigns.import_backup(GUILD_B, decode_backup(encode_backup(backup)), DM)


class ReachingTheNarrator(MemoryTest):
    async def inputs(self) -> Any:
        cache = LookupCache(self.memory)
        bot = SimpleNamespace(
            lookup=cache,
            _recanted=AsyncMock(return_value=()),
            _secret_ties=AsyncMock(return_value=()),
        )
        job = SimpleNamespace()
        return await DMBot.recap_audio_inputs(bot, GUILD_A, self.c, job)  # type: ignore[arg-type]

    async def test_a_spelling_reaches_the_dictionary_rules_and_a_secret_name_never_does(
        self,
    ) -> None:
        kazeth = await self.add("Ka'zeth", status="confirmed")
        await self.memory.set_said_as(GUILD_A, self.c, kazeth, "Kah-zeth", source=DM_SOURCE)
        vael = await self.add("Vaelithor", status="confirmed")
        await self.memory.add_alias(
            GUILD_A, self.c, vael, "the Grey Voice", kind="title", source=DM_SOURCE, secret=True,
            status="confirmed",
        )  # fmt: skip
        got = await self.inputs()
        rules = nm.rules(got.names)
        self.assertEqual([(r.string_to_replace, r.alias) for r in rules], [("Ka'zeth", "Kah-zeth")])
        self.assertIn("the Grey Voice", got.secret_names)
        self.assertNotIn("Grey Voice", str([r.as_request() for r in rules]))
