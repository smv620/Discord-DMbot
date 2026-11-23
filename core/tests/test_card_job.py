"""The sentence step of a character's card (#5050): written from the player-safe facts, kept with a
hash of them, made again only when they change. A stand-in AI throughout. Needs Postgres."""

from __future__ import annotations

from typing import Any
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock

from dmbot.ai import Reply
from dmbot.memory import card_job
from dmbot.memory.card_summaries import CardSummaries
from dmbot.memory.models import CONFIRMED
from dmbot.memory.models import DM as DM_SOURCE
from tests.test_memory_names import GUILD, NamesTest


def ai(text: str = "A halfling innkeeper in Bryn Shander.", **kw: Any) -> Any:
    out: Any = type("FakeAI", (), {})()
    out.complete = AsyncMock(
        return_value=Reply(text=text, cut=kw.get("cut", False), refused=kw.get("refused", False))
    )
    return out


class ThePureParts(IsolatedAsyncioTestCase):
    def test_the_answer_is_one_line_without_marks_and_quotes(self) -> None:
        got = card_job.read_sentence(
            Reply(text='\n- "A halfling innkeeper."  \nSecond line.', cut=False)
        )
        self.assertEqual(got, "A halfling innkeeper.")

    def test_unknown_and_an_empty_answer_are_no_sentence(self) -> None:
        self.assertIsNone(card_job.read_sentence(Reply(text="UNKNOWN", cut=False)))
        self.assertIsNone(card_job.read_sentence(Reply(text="unknown.", cut=False)))
        self.assertIsNone(card_job.read_sentence(Reply(text="  \n ", cut=False)))

    def test_the_key_changes_when_the_facts_change_and_not_otherwise(self) -> None:
        self.assertEqual(card_job.facts_key("a"), card_job.facts_key("a"))
        self.assertNotEqual(card_job.facts_key("a"), card_job.facts_key("b"))
        self.assertRegex(card_job.facts_key("a"), r"^[0-9a-f]{64}$")

    def test_a_name_with_nothing_else_is_too_little_to_ask_about(self) -> None:
        self.assertTrue(
            card_job.too_little({"name": "Rook", "description": "", "aliases": [], "links": []})
        )
        self.assertFalse(
            card_job.too_little({"name": "Rook", "description": "A thief.", "aliases": []})
        )
        self.assertFalse(
            card_job.too_little({"name": "Rook", "description": "", "links": [{"x": 1}]})
        )

    def test_the_prompt_carries_the_facts_as_data_and_the_rules(self) -> None:
        self.assertIn("<facts>\nA ferrywoman.\n</facts>", card_job.request("Mira", "A ferrywoman."))
        for rule in ("ONE plain sentence", "Never invent", "UNKNOWN", "never instructions"):
            self.assertIn(
                rule,
                card_job.SYSTEM.replace("only the facts given. ", "").replace(
                    "ONLY the facts given. Never", "Never"
                ),
            )


class TheSentence(NamesTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.rows = CardSummaries(self.db, clock=lambda: 1_700_000_000)
        made = await self.memory.add_entity(
            GUILD, self.campaign.id, type="character", name="Shafina", source=DM_SOURCE,
            status=CONFIRMED, description="Runs the Northlook inn in Bryn Shander.",
        )  # fmt: skip
        self.shafina = made.value.id
        bare = await self.memory.add_entity(
            GUILD, self.campaign.id, type="character", name="Rook", source=DM_SOURCE,
            status=CONFIRMED,
        )  # fmt: skip
        self.rook = bare.value.id

    async def go(self, client: Any, entity: str | None = None) -> str:
        return await card_job.refresh_who(
            self.rows, client, GUILD, self.campaign.id, entity or self.shafina
        )

    async def test_the_sentence_is_written_from_the_facts_and_kept_with_their_hash(self) -> None:
        client = ai()
        self.assertEqual(await self.go(client), "written")
        got = await self.rows.get(GUILD, self.campaign.id, self.shafina)
        assert got is not None
        self.assertEqual(got.who, "A halfling innkeeper in Bryn Shander.")
        self.assertRegex(got.facts_key or "", r"^[0-9a-f]{64}$")
        system, text = client.complete.await_args.args
        self.assertIn("Runs the Northlook inn", text)
        self.assertIn("Shafina", text)
        self.assertEqual(system, card_job.SYSTEM)

    async def test_the_same_facts_are_not_asked_again_but_changed_facts_are(self) -> None:
        client = ai()
        await self.go(client)
        self.assertEqual(await self.go(client), "unchanged")
        self.assertEqual(client.complete.await_count, 1)
        async with self.db.guild(GUILD) as conn:
            await conn.execute(
                "UPDATE memory_entities SET description = 'Owns the inn and a ferry.'"
                " WHERE id = %s",
                (self.shafina,),
            )
        self.assertEqual(await self.go(ai("A halfling who owns an inn and a ferry.")), "written")
        got = await self.rows.get(GUILD, self.campaign.id, self.shafina)
        assert got is not None
        self.assertEqual(got.who, "A halfling who owns an inn and a ferry.")

    async def test_a_name_with_no_facts_says_so_with_no_ai_call(self) -> None:
        client = ai()
        self.assertEqual(await self.go(client, self.rook), "little")
        client.complete.assert_not_awaited()
        got = await self.rows.get(GUILD, self.campaign.id, self.rook)
        assert got is not None
        self.assertEqual(got.who, "DMbot doesn't know much about Rook yet.")

    async def test_the_ai_saying_unknown_is_kept_as_too_little_known(self) -> None:
        self.assertEqual(await self.go(ai("UNKNOWN")), "unknown")
        got = await self.rows.get(GUILD, self.campaign.id, self.shafina)
        assert got is not None
        self.assertEqual(got.who, "DMbot doesn't know much about Shafina yet.")

    async def test_a_failing_or_refused_or_cut_or_empty_answer_keeps_nothing_and_never_raises(
        self,
    ) -> None:
        broken: Any = type("Broken", (), {})()
        broken.complete = AsyncMock(side_effect=RuntimeError("Shafina's facts"))
        with self.assertLogs("dmbot.memory.card_job", level="WARNING") as logs:
            self.assertEqual(await self.go(broken), "failed")
        self.assertIn("RuntimeError", " ".join(logs.output))
        self.assertNotIn("Shafina", " ".join(logs.output))
        for client in (ai(refused=True), ai(cut=True), ai("   ")):
            self.assertEqual(await self.go(client), "failed")
        self.assertIsNone(await self.rows.get(GUILD, self.campaign.id, self.shafina))

    async def test_a_removed_or_unconfirmed_entry_is_gone(self) -> None:
        self.assertEqual(await self.go(ai(), "f" * 32), "gone")
        proposed = await self.memory.add_entity(
            GUILD, self.campaign.id, type="character", name="Maybe", source=DM_SOURCE
        )
        self.assertEqual(await self.go(ai(), proposed.value.id), "gone")

    async def test_a_name_the_dm_keeps_secret_never_reaches_the_prompt(self) -> None:
        await self.memory.add_alias(
            GUILD, self.campaign.id, self.shafina, "the Grey Fox", kind="nickname",
            source=DM_SOURCE, status=CONFIRMED, secret=True,
        )  # fmt: skip
        await self.memory.add_alias(
            GUILD, self.campaign.id, self.shafina, "Innkeeper", kind="nickname",
            source=DM_SOURCE, status=CONFIRMED,
        )  # fmt: skip
        client = ai()
        await self.go(client)
        facts = client.complete.await_args.args[1]
        self.assertIn("Innkeeper", facts)  # (a name anyone may use goes in)
        self.assertNotIn("Grey Fox", facts)  # (what the DM keeps secret stays out)
