"""The DM's "📖 What I looked at" button (#1957): the list is what the answer was built from,
for the DM only, and each line opens its own card. A fake AI and fake interactions."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import discord

from dmbot.rules.house import HouseRule
from dmbot.rules.index import srd
from dmbot.sidebar import context, looked
from dmbot.sidebar.service import SidebarService
from dmbot.ui import looked_at
from tests.test_sidebar_service import (
    DM,
    GUILD,
    FakeAnswer,
    FakeAnswerer,
    FakeHost,
    dm_message,
    make_table,
)

INDEX = srd()
OWNER, OTHER = 7, 8


def house(number: int = 3) -> HouseRule:
    return HouseRule(
        number=number, campaign_id="c1", rule="A shield takes one reaction.",
        supersedes=None, scenario=None, session_id=None,
        created_by=OWNER, created_at=1, updated_at=1,
    )  # fmt: skip


def listed(
    question: str, target: str = "2024", fallback: str = "2014"
) -> tuple[looked.Looked, ...]:
    ctx = context.build(
        question, target=target, fallback=fallback, index=INDEX, house_rules=[], lookup=None,
        scene="",
    )  # fmt: skip
    return looked.of(ctx, INDEX)


class TheList(unittest.TestCase):
    def test_it_is_what_the_question_found_for_three_golden_questions(self) -> None:
        cases = {
            "how much AC does shield give": ["Shield (spell)", "Shield (armor)"],
            "how do I disarm the hidden pit": ["Hidden Pit (trap)"],
            "what does a bag of holding do": ["Bag of Holding (item: Wondrous Item, uncommon)"],
        }
        for question, want in cases.items():
            got = listed(question)
            ctx_hits = context.mentioned_rules(question, INDEX, "2024", "2014")
            self.assertEqual([i.label for i in got], want, question)
            self.assertEqual(len(got), len(ctx_hits), question)  # one line for each hit

    def test_each_line_says_where_it_comes_from_and_the_dms_chapter_says_dm_only(self) -> None:
        shield, armor = listed("how much AC does shield give")
        self.assertRegex(shield.place, r"^SRD 5\.2\.1, p\. \d+$")
        self.assertRegex(armor.place, r"^SRD 5\.2\.1, p\. \d+$")
        (pit,) = listed("how do I disarm the hidden pit")
        self.assertEqual(pit.place, "DM only")
        self.assertEqual(
            looked.text_of([pit]), f"{looked.HEADING}\nHidden Pit (trap) · DM only\n{looked.HINT}"
        )

    def test_the_value_opens_the_right_twin(self) -> None:
        shield, armor = listed("how much AC does shield give")
        self.assertEqual((shield.value, armor.value), ("Shield", "Shield (armor)"))

    def test_an_entry_only_the_older_rules_have_is_tagged(self) -> None:
        # (Plate is no longer one: the 2024 Plate Armor is found by "plate" since #1937.)
        (abacus,) = listed("how much does an abacus cost")
        self.assertIn("Legacy 2014", abacus.place)

    def test_house_rules_come_first_labelled_and_have_no_card(self) -> None:
        ctx = context.build(
            "how much AC does shield give", target="2024", fallback="2014", index=INDEX,
            house_rules=[house(3)], lookup=None, scene="",
        )  # fmt: skip
        items = looked.of(ctx, INDEX)
        self.assertEqual(items[0], looked.Looked("House rule 3", "your table"))
        self.assertEqual(items[0].value, "")
        self.assertEqual(items[1].label, "Shield (spell)")

    def test_nothing_looked_up_says_so_in_plain_words(self) -> None:
        self.assertEqual(listed("the dwarf bartender"), ())
        self.assertEqual(looked.text_of(()), "I didn't look anything up for this one.")

    def test_the_registry_keeps_the_latest_and_drops_the_oldest(self) -> None:
        registry = looked.Registry(keep=2)
        kept = [looked.Kept(OWNER, GUILD, "c1", ()) for _ in range(3)]
        keys = [registry.remember(k) for k in kept]
        self.assertIsNone(registry.get(keys[0]))
        self.assertEqual(registry.get(keys[2]), kept[2])
        self.assertEqual(len(set(keys)), 3)


def press(user: int, client: Any) -> Any:
    sent: dict[str, Any] = {}

    async def send_message(text: str = "", **kw: Any) -> None:
        sent.update(text=text, **kw)

    it = SimpleNamespace(user=SimpleNamespace(id=user), client=client, guild_id=None)
    it.response = SimpleNamespace(send_message=send_message)
    it.sent = sent
    return it


class TheButtonLabels(unittest.TestCase):
    def test_a_button_says_the_name_alone_cut_to_a_phone_and_the_kind_only_for_twins(self) -> None:
        (bag,) = listed("what does a bag of holding do")
        self.assertEqual(looked.button_labels([bag]), ["Bag of Holding"])  # not the long label
        long = looked.Looked("A" * 40 + " (item: Wondrous Item)", "x", "v")
        self.assertEqual(looked.button_labels([long]), ["A" * 24 + "…"])
        twins = listed("how much AC does shield give")
        self.assertEqual(looked.button_labels(twins), ["Shield (spell)", "Shield (armor)"])

    def test_at_most_five_lines_get_a_button_and_the_text_keeps_them_all(self) -> None:
        items = [looked.Looked(f"Thing {n} (rule)", "x", f"Thing {n}") for n in range(8)]
        self.assertEqual(len(items), 8)
        self.assertEqual(looked.text_of(items).count("\n"), 9)  # heading + 8 lines + hint


class TheButtons(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.service = SidebarService(FakeHost())
        self.client = SimpleNamespace(sidebar=self.service)
        items = (
            *listed("how much AC does shield give"),
            looked.Looked("House rule 3", "your table"),
        )
        self.key = self.service.looked.remember(looked.Kept(OWNER, GUILD, "c1", items))

    async def test_the_dm_who_asked_gets_the_list_privately_with_a_button_for_each_card(
        self,
    ) -> None:
        it = press(OWNER, self.client)
        await looked_at.LookedButton(self.key).callback(it)
        self.assertTrue(it.sent["ephemeral"])
        self.assertIn("Shield (spell) · SRD 5.2.1, p.", it.sent["text"])
        self.assertIn("House rule 3 · your table", it.sent["text"])
        labels = [c.item.label for c in it.sent["view"].children]
        self.assertEqual(labels, ["Shield (spell)", "Shield (armor)"])  # twins say their kind
        self.assertIn(looked.HINT, it.sent["text"])

    async def test_nobody_else_gets_it_and_a_forgotten_list_is_said_plainly(self) -> None:
        it = press(OTHER, self.client)
        await looked_at.LookedButton(self.key).callback(it)
        self.assertEqual(it.sent["text"], looked.NOT_YOURS)
        again = press(OWNER, self.client)
        await looked_at.LookedButton("0" * 12).callback(again)
        self.assertEqual(again.sent["text"], looked.GONE)

    async def test_a_pressed_line_opens_the_card_of_the_right_twin(self) -> None:
        it = press(OWNER, self.client)
        with patch("dmbot.ui.rule_lookup.look_up", new=AsyncMock()) as look_up:
            await looked_at.OpenCard(self.key, 1).callback(it)
        look_up.assert_awaited_once()
        assert look_up.await_args is not None
        args, kwargs = look_up.await_args
        self.assertEqual((args[1], args[2]), ("c1", "Shield (armor)"))
        self.assertEqual((kwargs["guild_id"], kwargs["paced"]), (GUILD, False))

    async def test_a_line_pressed_by_someone_else_or_for_a_house_rule_opens_nothing(self) -> None:
        with patch("dmbot.ui.rule_lookup.look_up", new=AsyncMock()) as look_up:
            other = press(OTHER, self.client)
            await looked_at.OpenCard(self.key, 0).callback(other)
            rule = press(OWNER, self.client)
            await looked_at.OpenCard(self.key, 2).callback(rule)  # the house rule has no value
        look_up.assert_not_awaited()
        self.assertEqual(other.sent["text"], looked.NOT_YOURS)
        self.assertEqual(rule.sent["text"], looked.NO_CARD)

    async def test_the_buttons_come_back_from_their_ids_after_a_restart(self) -> None:
        for template, custom_id in (
            (looked_at.LookedButton.__discord_ui_compiled_template__, f"dmbot:looked:{self.key}"),
            (
                looked_at.OpenCard.__discord_ui_compiled_template__,
                f"dmbot:looked:open:{self.key}:2",
            ),
        ):
            found = template.fullmatch(custom_id)
            assert found is not None, custom_id
            self.assertEqual(found["key"], self.key)
        # the list's own button must not be taken for a card's (their ids start alike)
        self.assertIsNone(
            looked_at.LookedButton.__discord_ui_compiled_template__.fullmatch(
                f"dmbot:looked:open:{self.key}:2"
            )
        )


class TheDelivery(unittest.IsolatedAsyncioTestCase):
    """Only the DM's private rules answer carries the button; nothing for the table."""

    def setUp(self) -> None:
        self.host = FakeHost(tables={GUILD: make_table()}, agreed={(GUILD, DM)})
        self.answerer = FakeAnswerer(
            FakeAnswer(),
        )
        self.service = SidebarService(self.host)
        self.service.answerer = self.answerer

    async def test_a_typed_answer_has_the_button_and_the_list_is_kept_for_it(self) -> None:
        self.answerer.answer_to_give = FakeAnswer()
        self.answerer.answer_to_give.looked_at = tuple(listed("how much AC does shield give"))  # type: ignore[attr-defined]
        message = dm_message(content="how much AC does shield give")
        await self.service.on_dm_message(message)
        (call,) = message.channel.send.await_args_list
        view = call.kwargs["view"]
        (button,) = view.children
        self.assertEqual(button.item.label, "What I looked at")
        self.assertEqual(str(button.item.emoji), "📖")
        key = button.item.custom_id.removeprefix("dmbot:looked:")
        kept = self.service.looked.get(key)
        assert kept is not None
        self.assertEqual((kept.owner_id, kept.guild_id, kept.campaign_id), (DM, GUILD, "c1"))
        self.assertEqual([i.label for i in kept.items], ["Shield (spell)", "Shield (armor)"])

    async def test_a_refusal_and_an_off_topic_reply_have_no_button(self) -> None:
        for answer in (FakeAnswer(refused=True), FakeAnswer(in_game=False)):
            self.answerer.answer_to_give = answer
            message = dm_message(content="how much AC does shield give")
            await self.service.on_dm_message(message)
            self.assertNotIn("view", message.channel.send.await_args_list[-1].kwargs)

    async def test_a_spoken_question_answered_by_message_has_the_button_too(self) -> None:
        said = "Hold on, I need to find if you need line of sight for fireball."
        self.assertTrue(self.service.ask_at_table(self.host.tables[GUILD], DM, said))
        for _ in range(50):
            await __import__("asyncio").sleep(0)
        self.assertEqual(len(self.host.views), 1)
        self.assertIsInstance(self.host.views[0], discord.ui.View)

    async def test_a_table_question_has_no_button(self) -> None:
        self.host.tables[GUILD].dmbchat_channel_id = 555
        self.host.agreed.add((GUILD, 8))
        self.assertTrue(self.service.ask_in_chat(self.host.tables[GUILD], 8, "how does cover work"))
        for _ in range(50):
            await __import__("asyncio").sleep(0)
        self.assertEqual(self.host.views, [])  # nothing private was sent with a view
        self.assertTrue(self.host.chat)  # the answer went to the chat, as text only


class TheEngine(unittest.IsolatedAsyncioTestCase):
    async def test_the_answer_carries_the_list_it_was_built_from(self) -> None:
        from tests.sidebar_brevity_cases import reply
        from tests.test_sidebar_answer import FakeAI, campaign
        from tests.test_table_questions import engine

        ai = FakeAI([reply("Shield gives +5 AC.", "SRD 5.2.1 p. 161")])
        answer = await engine(ai).answer(campaign(), "how much AC does shield give", asker_id=1)
        self.assertEqual([i.label for i in answer.looked_at], ["Shield (spell)", "Shield (armor)"])
        self.assertEqual(answer.looked_at, listed("how much AC does shield give"))
