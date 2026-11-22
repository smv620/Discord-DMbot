"""A World read reveals what it states plainly (#4524 slice 1b, Supervisor's ruling C): a hidden
entry it names word for word, a hidden connection it states itself, and a secret other name only
where the text gives it on that same person's own line. A smart or spoiler read reveals nothing;
the upload's ↩️ Undo takes it back; the notice gives a count only. Invented names and text."""

from __future__ import annotations

import json
import time
import unittest
from typing import Any
from unittest.mock import AsyncMock

from dmbot.memory import lore_read, world_reveal
from dmbot.memory.backstory_links import BackstoryLinks
from dmbot.memory.models import CONFIRMED, DM
from dmbot.ui import name_background as ui_bg
from dmbot.ui import upload_notice as un
from dmbot.ui.name_lists import Upload
from dmbot.upload_notices import UploadNotices
from tests import lore_fake
from tests.test_memory_names import DM as THE_DM
from tests.test_memory_names import GUILD, NamesTest

TEXT = "Ash Morrow served Master Orlen in the old days."
NAMES = [
    {"name": "Ash Morrow", "kind": "person", "also": [], "secret": [], "quote": "Ash Morrow"},
    {"name": "Master Orlen", "kind": "person", "also": [], "secret": [], "quote": "Master Orlen"},
]
SERVED = {"a": "Ash Morrow", "how": "works for", "b": "Master Orlen",
          "quote": "Ash Morrow served Master Orlen"}  # fmt: skip


def plain(change: dict[str, Any]) -> dict[str, Any]:
    """A question's change without the run that asked it."""
    return {k: v for k, v in change.items() if k != "run"}


class Pure(unittest.TestCase):
    def test_a_name_counts_only_where_the_text_holds_it_word_for_word(self) -> None:
        text = "The Ashen Crown met Cerric Vanya\u2019elen in Bryn-Shander. There was no hope."
        said = lore_read._says_word_for_word
        self.assertTrue(said(text, "Cerric Vanya'elen"))
        self.assertFalse(said(text, "Cerric Vane"))
        self.assertFalse(said(text, "Ash"))  # (not part of "Ashen")
        self.assertFalse(said(text, "Hope"))  # (a name with capitals needs them)
        self.assertTrue(said("The hooded stranger left.", "the hooded stranger"))

    def test_an_inferred_line_or_one_through_a_secret_name_reaches_the_check_as_such(self) -> None:
        """`keep_connections` hands the check the kept connection: whether it was inferred or
        found through a secret spelling rides along (the reveal refuses both)."""
        from dmbot.memory import background as bg

        seen: list[bg.Connection] = []

        def skip(c: bg.Connection) -> bool:
            seen.append(c)
            return True

        found = [bg.Found(n, "npc", (), (), n) for n in ("Ash Morrow", "Master Orlen")]
        guess = bg.Connection("Ash Morrow", "serves", "Master Orlen", "q", inferred="implied")
        bg.keep_connections([[guess]], found, {}, skip=skip)
        self.assertEqual(seen[-1].inferred, "implied")
        hooded = [bg.Found("Ash Morrow", "npc", (), ("the hooded stranger",), "Ash Morrow"),
                  found[1]]  # fmt: skip
        secretly = bg.Connection("the hooded stranger", "serves", "Master Orlen", "q")
        bg.keep_connections([[secretly]], hooded, {}, skip=skip)
        self.assertTrue(seen[-1].secret)

    def test_the_private_names_line_shows_ten_then_counts(self) -> None:
        from dmbot.ui import secret_questions as card

        self.assertEqual(card.revealed_text([]), "")
        one = card.revealed_text(["**Ash Morrow**"])
        self.assertIn("Only you see this.", one)
        self.assertIn(
            "can now see this in DMbot, and their characters know it:\n• **Ash Morrow**", one
        )
        self.assertIn("takes back the whole upload (within 7 days)", one)
        many = card.revealed_text([f"**N{i}**" for i in range(12)])
        self.assertIn("• **N9**\n…and 2 more.", many)
        self.assertNotIn("**N10**", many)

    def test_ten_long_names_still_fit_one_message(self) -> None:
        """Discord's 2,000 characters: the list is cut to fit with the notice above it."""
        from dmbot.ui import secret_questions as card

        name = card._cut("N" * 100, card.NAME_CUT)
        lines = [f"**{name}** works for **{name}**" for _ in range(10)]
        notice = un.notice_text("L" * 80, {"revealed": 10, "npc": 9, "place": 9}, 9)
        private = card.revealed_text(lines)
        self.assertLessEqual(len(f"**{'C' * 80}**\n{notice}{private}"), 2000)
        self.assertIn("more.", private)  # (what didn't fit is counted)

    def test_the_notice_counts_and_never_names(self) -> None:
        text = un.notice_text("the city guide, 3 Nov", {"revealed": 3}, 0)
        self.assertIn("🌍 **3 hidden things** are now known to everyone.", text)
        self.assertIn("Added **nothing new**", text)  # (a reveal is never counted as a name)
        one = un.notice_text("the city guide, 3 Nov", {"revealed": 1}, 0)
        self.assertIn("**1 hidden thing** is now known", one)
        from dmbot.web import upload_notice as web_notice

        self.assertIn("revealed", web_notice._NOT_KINDS)  # (nor on the website)


class TheDMsCopy(unittest.IsolatedAsyncioTestCase):
    async def test_the_names_go_to_each_dms_private_copy_and_never_the_channel(self) -> None:
        from types import SimpleNamespace

        from dmbot.bot import DMBot
        from dmbot.upload_notices import Notice

        channel: list[str] = []
        private: list[str] = []

        async def post(_channel: int, text: str, _view: Any) -> Any:
            channel.append(text)
            return None

        async def dm(_user: int, text: str, _view: Any) -> None:
            private.append(text)

        bot = SimpleNamespace(
            settings=SimpleNamespace(site_url=""), upload_notices=object(),
            post_message=post, sidebar_send_dm=dm,
        )  # fmt: skip
        campaign = SimpleNamespace(
            guild_id=1, name="Frozen Wastes", dm_bot_channel_id=5, dm_accounts={2, 3}
        )
        notice = Notice(1, "c1", "n1", "the city guide, 3 Nov", 1, {"revealed": 1})
        names = "\n🌍 _Only you see this:_ now known to everyone: **Ash Morrow**."
        await DMBot.post_upload_notice(bot, campaign, notice, 0, private=names)  # type: ignore[arg-type]
        self.assertTrue(channel and all("Ash Morrow" not in t for t in channel))
        self.assertEqual(len(private), 2)
        self.assertTrue(all("Ash Morrow" in t for t in private))

    async def announce_as(self, user: int) -> str:
        """The presser's own card after `announce`, for a campaign whose DM is 2."""
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        from dmbot.upload_notices import Notice

        shown: list[str] = []

        async def edit(**kw: Any) -> None:
            shown.append(str(kw.get("content", "")))

        it = MagicMock()
        it.user = SimpleNamespace(id=user)
        it.edit_original_response = edit
        bot = SimpleNamespace(
            post_upload_notice=AsyncMock(return_value=None), settings=SimpleNamespace(site_url="")
        )
        campaign = SimpleNamespace(id="c1", guild_id=1, dm_user_ids={2})
        done = SimpleNamespace(
            notice=Notice(1, "c1", "n1", "the city guide, 3 Nov", 1, {"revealed": 1}),
            questions_left=0, trait_questions=0, thread_suggestions=0, secret_questions=0,
            knowledge_note="", revealed_lines=("**Ash Morrow**",),
            pictures_unread=0, unread_pages=(), dropped={},
            pictures_kept=0, pictures_not_kept=0, pictures_undo=False,
            maps_asked=0, maps_over=0, maps_unsent=False,
        )  # fmt: skip
        await ui_bg.announce(it, bot, campaign, done)  # type: ignore[arg-type]
        return shown[-1]

    async def test_a_dm_who_pressed_sees_the_names_on_their_card(self) -> None:
        self.assertIn("**Ash Morrow**", await self.announce_as(2))

    async def test_a_manager_who_is_not_a_dm_sees_counts_only(self) -> None:
        card = await self.announce_as(99)
        self.assertNotIn("Ash Morrow", card)
        self.assertIn("1 hidden thing", card)


class TheRead(NamesTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.campaign = await self.campaigns.set_ask_before_adding(GUILD, self.campaign.id, False)
        self.bot.plan_gate = AsyncMock(return_value=None)  # type: ignore[method-assign]
        self.bot.backstory_links = BackstoryLinks(self.db)
        self.bot.upload_notices = UploadNotices(self.db)
        self.bot.lookup = None
        self.ash = await self.npc("Ash Morrow")
        self.orlen = await self.npc("Master Orlen")

    async def npc(self, name: str) -> str:
        made = await self.memory.add_entity(
            GUILD, self.campaign.id, name=name, type="character", role="npc",
            status=CONFIRMED, source=DM,
        )  # fmt: skip
        return str(made.value.id)

    async def hide(self, entity_id: str) -> None:
        await self.memory.set_hidden_from_players(
            GUILD, self.campaign.id, entity_id, True, source=DM, dm_chose_secret=True
        )

    async def secret_tie(self) -> str:
        made = await self.memory.add_relation(
            GUILD, self.campaign.id, self.ash, "serves", self.orlen, source=DM,
            confidence=1.0, status=CONFIRMED, secret=True, dm_chose_secret=True,
        )  # fmt: skip
        relation = made.value[0] if isinstance(made.value, tuple) else made.value
        return str(relation.id)

    async def read(
        self,
        choice: str,
        names: Any = NAMES,
        links: Any = (SERVED,),
        text: str = TEXT,
        label: str = "the city guide",
    ) -> Any:
        answer = {"names": list(names), "connections": list(links)}
        ai = lore_fake.lore_ai(json.dumps(answer))
        self.bot.background_ai = self.bot.background_read_ai = ai  # type: ignore[assignment]
        return await ui_bg.read_background(
            self.bot, self.campaign, Upload(text, label, True),
            truth=False, speaker=None, known_by="", started=time.monotonic(), choice=choice,
        )  # fmt: skip

    async def hidden(self, entity_id: str) -> bool:
        e = await self.memory.entity(GUILD, self.campaign.id, entity_id)
        assert e is not None
        return e.hidden_from_players

    async def tie_secret(self, relation_id: str) -> bool:
        rows = await self.memory.relations(GUILD, self.campaign.id, include_secret=True)
        return next(r for r in rows if r.id == relation_id).secret

    async def test_a_world_read_shows_a_hidden_name_it_gives_and_undo_takes_it_back(self) -> None:
        await self.hide(self.ash)
        done = await self.read("world")
        self.assertFalse(await self.hidden(self.ash))
        common = await self.memory.common_knowledge(GUILD, self.campaign.id, "entry", [self.ash])
        self.assertEqual(common, {self.ash})
        self.assertEqual(done.notice.counts.get("revealed"), 1)
        await un.undo_upload_result(self.bot, GUILD, self.campaign.id, done.notice.id, THE_DM)
        self.assertTrue(await self.hidden(self.ash))
        common = await self.memory.common_knowledge(GUILD, self.campaign.id, "entry", [self.ash])
        self.assertEqual(common, set())

    async def test_a_world_read_keeps_the_names_of_what_it_revealed_for_the_dms(self) -> None:
        await self.hide(self.ash)
        tie = await self.secret_tie()
        done = await self.read("world")
        self.assertIn("**Ash Morrow**", done.revealed_lines)
        ties = [line for line in done.revealed_lines if line != "**Ash Morrow**"]
        self.assertTrue(any("Master Orlen" in line and "Ash Morrow" in line for line in ties))
        self.assertFalse(await self.tie_secret(tie))

    async def test_the_notice_keeps_the_ids_of_what_was_revealed_never_words(self) -> None:
        """#4661: the website lists them from these, resolved when a DM reads."""
        await self.hide(self.ash)
        tie = await self.secret_tie()
        done = await self.read("world")
        notices = self.bot.upload_notices
        assert notices is not None
        async with notices._db.guild(GUILD) as conn:
            cur = await conn.execute(
                "SELECT revealed_entry_ids, revealed_link_ids FROM upload_notices WHERE id = %s",
                (done.notice.id,),
            )
            row = await cur.fetchone()
        assert row is not None
        kept = {"entries": row["revealed_entry_ids"], "links": row["revealed_link_ids"]}
        self.assertIn(self.ash, kept["entries"])
        self.assertIn(tie, kept["links"])
        self.assertNotIn("Ash", str(kept))  # (ids only: a name the DM hides later is not kept here)

    async def test_undo_hides_a_revealed_connection_again(self) -> None:
        tie = await self.secret_tie()
        done = await self.read("world")
        self.assertFalse(await self.tie_secret(tie))
        await un.undo_upload_result(self.bot, GUILD, self.campaign.id, done.notice.id, THE_DM)
        self.assertTrue(await self.tie_secret(tie))

    async def test_with_no_notice_to_undo_it_nothing_stays_revealed(self) -> None:
        await self.hide(self.ash)
        self.bot.upload_notices = None
        await self.read("world")
        self.assertTrue(await self.hidden(self.ash))

    async def test_a_campaign_that_asks_first_gets_no_reveal(self) -> None:
        await self.hide(self.ash)
        self.campaign = await self.campaigns.set_ask_before_adding(GUILD, self.campaign.id, True)
        await self.read("world")
        self.assertTrue(await self.hidden(self.ash))

    async def test_a_player_character_is_never_shown_by_a_read(self) -> None:
        made = await self.memory.add_entity(
            GUILD, self.campaign.id, name="Wren Vell", type="character",
            role="player_character", played_by=1002, status=CONFIRMED, source=DM,
        )  # fmt: skip
        got = await world_reveal.reveal(
            self.memory, GUILD, self.campaign.id, entries=[made.value.id]
        )
        self.assertEqual(got.total, 0)

    async def test_a_connection_whose_kept_words_the_text_does_not_say_stays_secret(self) -> None:
        made = await self.memory.add_relation(
            GUILD, self.campaign.id, self.ash, "serves", self.orlen, source=DM, confidence=1.0,
            status=CONFIRMED, secret=True, dm_chose_secret=True, detail="by an old blood pact",
        )  # fmt: skip
        relation = made.value[0] if isinstance(made.value, tuple) else made.value
        await self.read("world")
        self.assertTrue(await self.tie_secret(str(relation.id)))

    async def test_a_smart_or_spoiler_read_reveals_nothing(self) -> None:
        await self.hide(self.ash)
        tie = await self.secret_tie()
        for choice in ("smart", "spoilers"):
            with self.subTest(choice):
                await self.read(choice)
                self.assertTrue(await self.hidden(self.ash))
                self.assertTrue(await self.tie_secret(tie))

    async def test_a_world_read_reveals_a_connection_it_states(self) -> None:
        tie = await self.secret_tie()
        done = await self.read("world")
        self.assertFalse(await self.tie_secret(tie))
        self.assertEqual(done.notice.counts.get("revealed"), 1)

    async def test_a_connection_written_through_a_secret_name_stays_secret(self) -> None:
        await self.memory.add_alias(
            GUILD, self.campaign.id, self.ash, "the hooded stranger", kind="title", secret=True,
            status=CONFIRMED, source=DM, dm_chose_secret=True,
        )  # fmt: skip
        tie = await self.secret_tie()
        hooded = {"a": "the hooded stranger", "how": "works for", "b": "Master Orlen",
                  "quote": "the hooded stranger served Master Orlen"}  # fmt: skip
        await self.read("world", links=(hooded,),
                        text="The hooded stranger served Master Orlen.")  # fmt: skip
        self.assertTrue(await self.tie_secret(tie))

    async def test_a_secret_name_on_its_own_persons_line_is_revealed_but_not_alone(self) -> None:
        made = await self.memory.add_alias(
            GUILD, self.campaign.id, self.ash, "Tamsin Vell", kind="old", secret=True,
            status=CONFIRMED, source=DM, dm_chose_secret=True,
        )  # fmt: skip
        alias = made.value.id

        async def secret() -> bool:
            names = await self.memory.aliases(
                GUILD, self.campaign.id, entity_id=self.ash, include_secret=True
            )
            return next(a for a in names if a.id == alias).secret

        alone = [{"name": "Tamsin Vell", "kind": "person", "also": [], "secret": [],
                  "quote": "Tamsin Vell"}]  # fmt: skip
        await self.read("world", names=alone, links=(), text="Tamsin Vell fled north.")
        self.assertTrue(await secret())  # (the same name could be someone else's)
        own = [{"name": "Ash Morrow", "kind": "person", "also": ["Tamsin Vell"], "secret": [],
                "quote": "Ash Morrow, born Tamsin Vell"}]  # fmt: skip
        await self.read("world", names=own, links=(), text="Ash Morrow, born Tamsin Vell, fled.")
        self.assertFalse(await secret())

    async def test_a_name_the_text_does_not_hold_is_never_revealed(self) -> None:
        await self.hide(self.ash)
        await self.read("world", links=(), text="Master Orlen kept the old ways.")
        self.assertTrue(await self.hidden(self.ash))

    # ---- #4590: asked instead of revealed ----------------------------------------------------

    async def waiting(self) -> list[Any]:
        return await self.memory.secret_questions_waiting(GUILD, self.campaign.id)

    async def test_a_campaign_that_asks_first_is_asked_and_the_answer_reveals_with_one_undo(
        self,
    ) -> None:
        await self.hide(self.ash)
        self.campaign = await self.campaigns.set_ask_before_adding(GUILD, self.campaign.id, True)
        done = await self.read("world", links=())
        (q,) = await self.waiting()
        self.assertEqual(
            (q.kind, q.item_id, plain(q.change)), ("reveal", self.ash, {"common": True})
        )
        self.assertTrue(q.change.get("run"))  # (the upload that asked, for "Same for all")
        self.assertEqual(done.secret_questions, 1)  # (counted on its note)
        made = await world_reveal.answer(self.memory, GUILD, self.campaign.id, [q.id])
        assert made is not None
        self.assertFalse(await self.hidden(self.ash))
        common = await self.memory.common_knowledge(GUILD, self.campaign.id, "entry", [self.ash])
        self.assertEqual(common, {self.ash})
        self.assertIsNone(await world_reveal.answer(self.memory, GUILD, self.campaign.id, [q.id]))
        for batch in reversed(made.batches):
            await self.memory.undo(GUILD, self.campaign.id, batch)
        self.assertTrue(await self.hidden(self.ash))
        self.assertEqual([x.id for x in await self.waiting()], [q.id])  # (back to waiting)

    async def test_with_no_notice_to_undo_it_a_connection_is_asked_about(self) -> None:
        tie = await self.secret_tie()
        self.bot.upload_notices = None
        await self.read("world")
        self.assertTrue(await self.tie_secret(tie))
        self.assertIn(tie, [q.item_id for q in await self.waiting()])

    async def test_a_lone_secret_name_asks_whether_it_is_the_same_person(self) -> None:
        await self.hide(self.ash)
        made = await self.memory.add_alias(
            GUILD, self.campaign.id, self.ash, "Tamsin Vell", kind="old", secret=True,
            status=CONFIRMED, source=DM, dm_chose_secret=True,
        )  # fmt: skip
        alone = [{"name": "Tamsin Vell", "kind": "person", "also": [], "secret": [],
                  "quote": "Tamsin Vell"}]  # fmt: skip
        await self.read("world", names=alone, links=(), text="Tamsin Vell fled north.")
        (q,) = await self.waiting()
        self.assertEqual(
            (q.item_id, plain(q.change)), (made.value.id, {"common": True, "lone": True})
        )
        self.assertTrue(await self.hidden(self.ash))  # (nothing shown before the DM's answer)
        await world_reveal.answer(self.memory, GUILD, self.campaign.id, [q.id])
        names = await self.memory.aliases(
            GUILD, self.campaign.id, entity_id=self.ash, include_secret=True
        )
        self.assertFalse(next(a for a in names if a.id == made.value.id).secret)
        self.assertFalse(await self.hidden(self.ash))  # (the same person: shown with it)

    async def test_keep_it_secret_is_never_asked_again(self) -> None:
        await self.hide(self.ash)
        self.campaign = await self.campaigns.set_ask_before_adding(GUILD, self.campaign.id, True)
        await self.read("world", links=())
        (q,) = await self.waiting()
        await self.memory.answer_secret_question(GUILD, self.campaign.id, q.id, True, source=DM)
        await self.read("world", links=())
        self.assertEqual(await self.waiting(), [])
        self.assertTrue(await self.hidden(self.ash))

    async def two_uploads(self) -> tuple[str, Any, Any, list[str]]:
        """The city guide names Ash and Orlen, the harbour notes Wren, all three hidden, in a
        campaign that asks first: a card on the city guide's first question, and what it said."""
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        from dmbot.ui import secret_questions as card

        wren = await self.npc("Wren Hale")
        for entry in (self.ash, self.orlen, wren):
            await self.hide(entry)
        self.campaign = await self.campaigns.set_ask_before_adding(GUILD, self.campaign.id, True)
        await self.read("world", links=(), label="the city guide")
        wren_line = [{"name": "Wren Hale", "kind": "person", "also": [], "secret": [],
                      "quote": "Wren Hale"}]  # fmt: skip
        await self.read(
            "world", names=wren_line, links=(), text="Wren Hale walked.", label="the city guide"
        )  # fmt: skip (the same name, the same day)
        waiting = await self.waiting()
        first = next(q for q in waiting if q.item_id == self.ash)
        it = MagicMock()
        it.user = SimpleNamespace(id=THE_DM, guild_permissions=SimpleNamespace(manage_guild=False))
        it.guild = SimpleNamespace(id=GUILD)
        it.client = SimpleNamespace(memory=self.memory, campaigns=self.campaigns)
        it.response.is_done = lambda: False
        it.response.defer = AsyncMock()
        sent: list[str] = []

        async def reply(text: str = "", **kw: Any) -> None:
            sent.append(str(kw.get("content", text)))

        it.response.edit_message = it.followup.send = it.edit_original_response = reply
        view = card.SecretQuestions(self.campaign.id, GUILD)
        shown = await card.describe(self.memory, GUILD, self.campaign.id, first)
        view.current = card.Shown(first, shown.item, shown.what, from_upload=2)
        return wren, view, it, sent

    async def test_let_them_all_know_reveals_only_that_uploads_questions(self) -> None:
        from dmbot.ui import secret_questions as card

        wren, view, it, sent = await self.two_uploads()
        self.assertIn(card.same_for_all(2), card.card_text(view.current, 3))
        self.assertIn(card.keep_all(2), card.card_text(view.current, 3))
        await view._all(it)
        self.assertFalse(await self.hidden(self.ash))
        self.assertFalse(await self.hidden(self.orlen))
        self.assertTrue(await self.hidden(wren))  # (another upload's: untouched)
        self.assertEqual([q.item_id for q in await self.waiting()], [wren])
        self.assertIn("can see all 2 from this upload now", sent[-1])
        await view._undo(it)
        self.assertTrue(await self.hidden(self.ash))
        self.assertTrue(await self.hidden(self.orlen))
        self.assertEqual(len(await self.waiting()), 3)

    async def test_a_reveal_that_cannot_be_shown_yet_waits_again(self) -> None:
        await self.hide(self.orlen)
        tie = await self.secret_tie()
        self.campaign = await self.campaigns.set_ask_before_adding(GUILD, self.campaign.id, True)
        await self.read("world")
        q = next(x for x in await self.waiting() if x.item_id == tie)
        made = await world_reveal.answer(self.memory, GUILD, self.campaign.id, [q.id])
        assert made is not None
        self.assertEqual((made.answered, made.refused), (0, 1))  # (Orlen is still hidden)
        self.assertTrue(await self.tie_secret(tie))
        self.assertIn(q.id, [x.id for x in await self.waiting()])

    async def test_a_world_question_is_never_answered_as_an_old_reveal(self) -> None:
        from dmbot.memory.models import MemoryRuleError

        await self.hide(self.ash)
        self.campaign = await self.campaigns.set_ask_before_adding(GUILD, self.campaign.id, True)
        await self.read("world", links=())
        (q,) = await self.waiting()
        with self.assertRaises(MemoryRuleError):
            await self.memory.answer_secret_question(
                GUILD, self.campaign.id, q.id, False, source=DM
            )
        self.assertEqual([x.id for x in await self.waiting()], [q.id])

    async def test_a_notice_that_fails_takes_the_reveals_back_and_asks(self) -> None:
        from unittest.mock import patch

        await self.hide(self.ash)
        with patch("dmbot.ui.upload_notice.add_sure", AsyncMock(return_value=None)):
            await self.read("world", links=())
        self.assertTrue(await self.hidden(self.ash))
        self.assertEqual([q.item_id for q in await self.waiting()], [self.ash])

    async def test_a_secret_name_two_people_share_is_not_asked_as_the_same_person(self) -> None:
        for entry in (self.ash, self.orlen):
            await self.memory.add_alias(
                GUILD, self.campaign.id, entry, "the Grey Hand", kind="title", secret=True,
                status=CONFIRMED, source=DM, dm_chose_secret=True,
            )  # fmt: skip
        alone = [{"name": "the Grey Hand", "kind": "person", "also": [], "secret": [],
                  "quote": "the Grey Hand"}]  # fmt: skip
        await self.read("world", names=alone, links=(), text="The Grey Hand struck at dawn.")
        self.assertEqual(await self.waiting(), [])

    async def test_keep_it_secret_holds_against_a_later_reveal_by_itself(self) -> None:
        """Supervisor's ruling 2 on #4605: a reveal by itself never overrides the DM's answer."""
        await self.hide(self.ash)
        tie = await self.secret_tie()
        self.campaign = await self.campaigns.set_ask_before_adding(GUILD, self.campaign.id, True)
        await self.read("world")
        asked = await self.waiting()
        self.assertEqual({q.item_id for q in asked}, {self.ash, tie})
        for q in asked:
            await self.memory.answer_secret_question(GUILD, self.campaign.id, q.id, True, source=DM)
        self.campaign = await self.campaigns.set_ask_before_adding(GUILD, self.campaign.id, False)
        done = await self.read("world")
        self.assertTrue(await self.hidden(self.ash))
        self.assertTrue(await self.tie_secret(tie))
        self.assertFalse(done.notice.counts.get("revealed"))
        self.assertEqual(await self.waiting(), [])  # (and never asked again)
        await self.memory.set_hidden_from_players(
            GUILD, self.campaign.id, self.ash, False, source=DM, dm_chose_secret=False
        )  # (the DM can still show it themselves: what "Who knows this?" does for an entry)
        self.assertFalse(await self.hidden(self.ash))

    async def test_a_reveal_by_itself_answers_a_waiting_connection_question(self) -> None:
        tie = await self.secret_tie()
        self.campaign = await self.campaigns.set_ask_before_adding(GUILD, self.campaign.id, True)
        await self.read("world")
        self.assertIn(tie, [q.item_id for q in await self.waiting()])
        self.campaign = await self.campaigns.set_ask_before_adding(GUILD, self.campaign.id, False)
        await self.read("world")
        self.assertFalse(await self.tie_secret(tie))
        self.assertNotIn(tie, [q.item_id for q in await self.waiting()])

    async def test_if_answering_the_questions_fails_the_reveal_keeps_its_undo(self) -> None:
        from unittest.mock import patch

        await self.hide(self.ash)
        broken = AsyncMock(side_effect=RuntimeError("down"))
        with patch.object(self.memory, "answer_waiting_world_questions", broken):
            done = await self.read("world", links=())
        self.assertFalse(await self.hidden(self.ash))
        await un.undo_upload_result(self.bot, GUILD, self.campaign.id, done.notice.id, THE_DM)
        self.assertTrue(await self.hidden(self.ash))

    async def test_a_question_still_waiting_does_not_block(self) -> None:
        """Only "Keep it secret" holds: one still waiting does not stop a later reveal."""
        await self.hide(self.ash)
        self.campaign = await self.campaigns.set_ask_before_adding(GUILD, self.campaign.id, True)
        await self.read("world", links=())
        self.campaign = await self.campaigns.set_ask_before_adding(GUILD, self.campaign.id, False)
        done = await self.read("world", links=())
        self.assertFalse(await self.hidden(self.ash))
        # (ruling (b) on #4606: the read answers it, so the card stops offering it, and the
        # upload's Undo asks it again)
        self.assertEqual(await self.waiting(), [])
        await un.undo_upload_result(self.bot, GUILD, self.campaign.id, done.notice.id, THE_DM)
        self.assertTrue(await self.hidden(self.ash))
        self.assertEqual([q.item_id for q in await self.waiting()], [self.ash])

    async def test_keep_all_secret_keeps_only_that_uploads_questions(self) -> None:
        wren, view, it, sent = await self.two_uploads()
        await view._keep_all(it)
        self.assertIn("Kept all 2 secret", sent[-1])
        self.assertEqual([q.item_id for q in await self.waiting()], [wren])
        for entry in (self.ash, self.orlen, wren):
            self.assertTrue(await self.hidden(entry))


if __name__ == "__main__":
    unittest.main()
