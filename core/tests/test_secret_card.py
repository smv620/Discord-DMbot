"""🤫 Secrets to check (#4524 slice 2): the card that answers the questions DMbot asks instead of
taking knowledge away from the players by itself. Its words for each kind, that the answers do
what they say (with ↩️ Undo after a change), that only a DM may answer, the board's count and
button, and the counts-only lines for an upload and a session. Postgres for the store. Invented
names."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from dmbot.memory import secret_questions as sq
from dmbot.memory.models import CONFIRMED, DM
from dmbot.questions import board, counts, kinds
from dmbot.questions.counts import Counted, Sources
from dmbot.ui import secret_questions as card
from tests.test_memory_store import DM as DM_USER
from tests.test_memory_store import GUILD_A, MemoryTest

PLAYER = 4242
STRANGER = 99


def question(kind: str = sq.NARROW, table: str = "memory_relations", **change: Any) -> sq.Question:
    return sq.Question("q1", kind, table, "i1", change or {"secret": True}, "Tavin's tale, 3 Nov", 1)


class TheWords(unittest.TestCase):
    def test_a_narrow_question_says_what_make_it_secret_does(self) -> None:
        tie = card.Shown(question(), "**Bob** is an ally of **Ilsa**", "connection")
        text = card.card_text(tie, 2)
        self.assertIn("🤫 **Secrets to check** (2 waiting)", text)
        self.assertIn(
            "**Bob** is an ally of **Ilsa** sounds like a secret, but your players already know it.",
            text,
        )
        self.assertIn('From "Tavin\'s tale, 3 Nov".', text)
        self.assertIn("🤫 Make it secret: your players stop seeing this connection.", text)
        self.assertIn("👁️ Keep it known: nothing changes, and DMbot won't ask this again.", text)

    def test_a_knowers_change_names_the_characters_who_would_stop_knowing(self) -> None:
        q = question(knowers_off=["e1", "e2"])
        text = card.card_text(card.Shown(q, "**Bob**", "name", knowers=("Ilsa", "Wren")), 1)
        self.assertIn("**Ilsa** and **Wren** stop knowing it.", text)
        self.assertNotIn("waiting", text.splitlines()[0])  # (one question: no count)

    def test_an_other_name_says_whose_it_is(self) -> None:
        q = question(table="memory_aliases")
        text = card.card_text(card.Shown(q, "**Bobby**", "other name", owner="**Bob**"), 1)
        self.assertIn("**Bobby**, another name of **Bob**, sounds like a secret", text)

    def test_replace_and_reveal_have_their_own_answers(self) -> None:
        old = card.Shown(question(sq.REPLACE, end=True, by="r2"), "**A** serves **B**",
                         "connection", new="**A** serves **C**")  # fmt: skip
        text = card.card_text(old, 1)
        self.assertIn("Did this change? Your players know **A** serves **B**.", text)
        self.assertIn("Your lore now says **A** serves **C**.", text)
        self.assertEqual(card._buttons(sq.REPLACE), (card.KEEP_BOTH, card.END_OLD))
        name = card.Shown(question(sq.REVEAL, "memory_aliases", secret=False), "**Tamsin**",
                          "other name", owner="**Ash**")  # fmt: skip
        text = card.card_text(name, 1)
        self.assertIn("knew the name **Tamsin**, but it is also a secret name of **Ash**", text)
        self.assertEqual(card._buttons(sq.REVEAL), (card.KEEP_SECRET, card.LET_KNOW))

    def test_the_count_line_counts_and_never_names(self) -> None:
        self.assertEqual(card.count_line(0), "")
        self.assertIn("🤫 **1 thing your players know** sounded like a secret", card.count_line(1))
        self.assertIn("**3 things your players know** sounded like secrets", card.count_line(3))
        self.assertIn("❓ Questions for you", card.count_line(3))

    def test_the_board_has_the_kind_and_its_button(self) -> None:
        self.assertEqual(kinds.LABELS[kinds.SECRETS], "Secrets to check")
        found = {kinds.SECRETS: Counted(2)}
        self.assertIn("• Secrets to check: **2**", board.text("Frozen Wastes", found))
        view = board.view(GUILD_A, "a" * 32, found)
        ids = [getattr(item, "custom_id", "") for item in view.children]
        self.assertIn(f"dmbot:secrets:ask:{'a' * 32}", ids)


class TheCard(MemoryTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.hero = await self.add(
            "Ilsa", type="character", role="player_character", played_by=PLAYER,
            status=CONFIRMED,
        )  # fmt: skip
        self.bob = await self.add("Bob", status=CONFIRMED)
        self.tie = (await self.relate(self.bob, "ally_of", self.hero, status=CONFIRMED)).value[0]
        with sq.evidence("Tavin's tale, 3 Nov") as self.asked:
            await self.memory.update_relation(GUILD_A, self.c, self.tie.id, secret=True, source=DM)

    def interaction(self, user: int = DM_USER) -> Any:
        sent: list[str] = []

        async def reply(text: str = "", **kw: Any) -> None:
            sent.append(str(kw.get("content", text)))

        it = MagicMock()
        it.user = SimpleNamespace(id=user, guild_permissions=SimpleNamespace(manage_guild=False))
        it.guild = SimpleNamespace(id=GUILD_A)
        it.client = SimpleNamespace(memory=self.memory, campaigns=self.campaigns)
        it.response.is_done = lambda: False
        it.response.defer = AsyncMock()
        it.response.send_message = reply
        it.response.edit_message = reply
        it.followup.send = reply
        it.edit_original_response = reply
        it.sent = sent
        return it

    async def secret(self) -> bool:
        tie = await self.memory.relation(GUILD_A, self.c, self.tie.id)
        assert tie is not None
        return tie.secret

    async def test_the_run_counts_the_questions_it_asked(self) -> None:
        self.assertEqual(len(self.asked), 1)
        (q,) = await self.memory.secret_questions_waiting(GUILD_A, self.c)
        self.assertEqual(q.label, "Tavin's tale, 3 Nov")

    async def test_the_card_names_the_connection(self) -> None:
        view = card.SecretQuestions(self.c, GUILD_A)
        text = await view._load(self.interaction())
        assert text is not None
        self.assertIn("is a friend or ally of", text)  # (a two-way tie: either way round)
        self.assertIn("sounds like a secret, but your players already know it.", text)
        labels = [getattr(b, "label", "") for b in view.children]
        self.assertEqual(labels, [card.KEEP_KNOWN, card.MAKE_SECRET])

    async def test_make_it_secret_then_undo_puts_the_question_back(self) -> None:
        view = card.SecretQuestions(self.c, GUILD_A)
        it = self.interaction()
        await view._load(it)
        await view._change(it)
        self.assertTrue(await self.secret())
        self.assertIn("is a secret now", it.sent[-1])
        self.assertIn(card.NOTHING_LEFT, it.sent[-1])
        self.assertEqual([getattr(b, "label", "") for b in view.children], [card.UNDO])
        await view._undo(it)
        self.assertFalse(await self.secret())
        self.assertIn(card.UNDONE, it.sent[-1])
        self.assertEqual(await self.memory.secret_questions_count(GUILD_A, self.c), 1)

    async def test_keep_it_known_changes_nothing_and_is_not_asked_again(self) -> None:
        view = card.SecretQuestions(self.c, GUILD_A)
        it = self.interaction()
        await view._load(it)
        await view._stay(it)
        self.assertFalse(await self.secret())
        self.assertIn("Kept known", it.sent[-1])
        self.assertEqual(await self.memory.secret_questions_count(GUILD_A, self.c), 0)
        await self.memory.update_relation(GUILD_A, self.c, self.tie.id, secret=True, source=DM)
        self.assertEqual(await self.memory.secret_questions_count(GUILD_A, self.c), 0)

    async def test_only_a_dm_answers(self) -> None:
        view = card.SecretQuestions(self.c, GUILD_A)
        await view._load(self.interaction())
        await view._change(self.interaction(STRANGER))
        self.assertFalse(await self.secret())
        self.assertEqual(await self.memory.secret_questions_count(GUILD_A, self.c), 1)

    async def test_an_answer_from_another_screen_first_is_said_so(self) -> None:
        view = card.SecretQuestions(self.c, GUILD_A)
        it = self.interaction()
        await view._load(it)
        (q,) = await self.memory.secret_questions_waiting(GUILD_A, self.c)
        await self.memory.answer_secret_question(GUILD_A, self.c, q.id, False, source=DM)
        await view._change(it)
        self.assertFalse(await self.secret())
        self.assertIn(card.GONE_NOW, it.sent[-1])

    async def test_a_reveal_question_lets_them_know(self) -> None:
        made = await self.memory.add_alias(
            GUILD_A, self.c, self.bob, "Old Bob", kind="old", secret=True, status=CONFIRMED,
            source=DM, dm_chose_secret=True,
        )  # fmt: skip
        async with self.memory._write(GUILD_A, self.c, DM) as w:
            await sq.ask_reveal(w, made.value.id)
        (q,) = [x for x in await self.memory.secret_questions_waiting(GUILD_A, self.c)
                if x.kind == sq.REVEAL]  # fmt: skip
        shown = await card.describe(self.memory, GUILD_A, self.c, q)
        self.assertEqual((shown.item, shown.owner), ("**Old Bob**", "**Bob**"))
        view = card.SecretQuestions(self.c, GUILD_A)
        view.current = shown
        await view._change(self.interaction())
        names = await self.memory.aliases(GUILD_A, self.c, entity_id=self.bob, include_secret=True)
        self.assertFalse(next(a for a in names if a.id == made.value.id).secret)

    async def test_the_count_is_the_cards(self) -> None:
        got = await counts._COUNTERS[kinds.SECRETS](
            Sources(db=self.db, memory=self.memory), GUILD_A, self.c
        )
        self.assertEqual(got.n, 1)


if __name__ == "__main__":
    unittest.main()
