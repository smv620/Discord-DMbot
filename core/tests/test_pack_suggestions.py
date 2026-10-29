"""Suggest to <pack> (#1537): a following campaign sends one of its own homebrew entries to the
pack's publisher. Needs Postgres, like the other store tests. The point of most of these is what
does NOT cross: the publisher's side never holds who sent a suggestion, and nothing from a book
is ever sent."""

from __future__ import annotations

from dmbot.campaigns import CampaignStore
from dmbot.rules import rule_sets
from dmbot.rules.homebrew import FileEntry, Homebrew, HomebrewStore
from dmbot.rules.house import HouseRuleError
from dmbot.rules.pack_suggestions import (
    ALREADY_IN_PACK,
    BOOK_NAME,
    FROM_A_BOOK,
    PICK_A_PACK,
    PackSuggestionStore,
)
from dmbot.rules.rule_sets import FollowedSetsSection, RuleSetError
from tests.pg import DatabaseTest

GUILD_A, GUILD_B, GUILD_C = 1001, 2002, 3003
DM_A, DM_B, DM_C, PLAYER = 11, 22, 55, 33


class Base(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.campaigns = CampaignStore(self.db, clock=lambda: 1_700_000_000)
        self.campaigns.register_section(FollowedSetsSection())
        self.brew = HomebrewStore(self.db, clock=lambda: 1_700_000_000)
        self.packs = PackSuggestionStore(self.db, clock=lambda: 1_700_000_000)
        self.pub = (await self.campaigns.create(GUILD_A, "Frostmaiden", DM_A)).id
        self.far = (await self.campaigns.create(GUILD_B, "Far Away", DM_B)).id
        await self.brew.add(
            GUILD_A, self.pub, DM_A, kind="monster", name="Hollow King", text="A crowned skeleton."
        )
        await self.packs.publish_pack(
            GUILD_A, self.pub, DM_A, "Frost Pack", await self.brew.list(GUILD_A, self.pub)
        )
        self.code = await self.packs.make_pack_code(GUILD_A, self.pub, DM_A)
        offer = await self.packs.by_code(GUILD_B, DM_B, self.code)
        self.follow = await self.packs.follow_pack(GUILD_B, self.far, DM_B, offer)

    async def mine(
        self, name: str, text: str = "Words.", *, kind: str = "item", summary: str | None = None
    ) -> Homebrew:
        return await self.brew.add(
            GUILD_B, self.far, DM_B, kind=kind, name=name, text=text, summary=summary
        )

    async def send(
        self, entry: Homebrew, user: int = DM_B, set_id: str | None = None
    ) -> tuple[str, int, str, str]:
        return await self.packs.suggest_entry(
            GUILD_B, self.far, user, entry.id, entry.version, set_id
        )


class Sending(Base):
    async def test_a_suggestion_lands_on_the_publisher_and_names_nobody(self) -> None:
        entry = await self.mine("Ember Lamp", "Glows warmly.", summary="A warm lamp")
        sid, guild, campaign, pack = await self.send(entry)
        self.assertEqual((guild, campaign, pack), (GUILD_A, self.pub, "Frost Pack"))
        got = await self.packs.pack_suggestion(GUILD_A, self.pub, sid)
        assert got is not None
        self.assertEqual((got.name, got.kind, got.text, got.known, got.status),
                         ("Ember Lamp", "item", "Glows warmly.", True, "open"))  # fmt: skip
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute("SELECT * FROM pack_suggestions")
            rows = await cur.fetchall()
            values = list(rows[0].values())
            for secret in (GUILD_B, self.far, DM_B, "Far Away"):
                self.assertNotIn(secret, values)
            self.assertFalse(any(isinstance(v, str) and "Far Away" in v for v in values))
            cur = await conn.execute("SELECT count(*) AS n FROM pack_suggestions_sent")
            row = await cur.fetchone()
            assert row is not None
            self.assertEqual(row["n"], 0)  # the follower's own record is not on this side

    async def test_an_entry_from_a_book_is_never_sent(self) -> None:
        await self.brew.load_file(
            GUILD_B, self.far, DM_B, [FileEntry("feat", "Booked", "Book words.")], from_book=True
        )
        booked = await self.brew.find(GUILD_B, self.far, "Booked")
        assert booked is not None
        with self.assertRaises(RuleSetError) as err:
            await self.send(booked)
        self.assertEqual(str(err.exception), FROM_A_BOOK)

    async def test_a_player_cannot_suggest_and_only_a_following_campaign_can(self) -> None:
        entry = await self.mine("Ember Lamp")
        with self.assertRaises(HouseRuleError):
            await self.send(entry, user=PLAYER)
        other = (await self.campaigns.create(GUILD_C, "Elsewhere", DM_C)).id
        lamp = await self.brew.add(GUILD_C, other, DM_C, kind="item", name="Lamp", text="x")
        with self.assertRaises(RuleSetError) as err:
            await self.packs.suggest_entry(GUILD_C, other, DM_C, lamp.id, lamp.version)
        self.assertEqual(str(err.exception), rule_sets.NOT_FOLLOWING)

    async def test_what_was_confirmed_is_what_goes(self) -> None:
        entry = await self.mine("Ember Lamp")
        await self.brew.edit(GUILD_B, self.far, DM_B, entry.id, kind="item", name="Ember Lamp",
                             text="Changed since.")  # fmt: skip
        with self.assertRaises(RuleSetError):
            await self.send(entry)  # the DM was shown the old version

    async def test_one_open_suggestion_for_a_name_and_a_limit_per_campaign(self) -> None:
        entry = await self.mine("Ember Lamp")
        await self.send(entry)
        with self.assertRaises(RuleSetError):
            await self.send(entry)  # already sent: the answer comes at the next session
        for n in range(rule_sets.SUGGEST_FOLLOWER_MAX - 1):
            await self.send(await self.mine(f"Item {n}"))
        with self.assertRaises(RuleSetError):
            await self.send(await self.mine("One too many"))

    async def test_a_pack_with_a_waiting_suggestion_for_that_name_refuses_a_second_table(
        self,
    ) -> None:
        far2 = (await self.campaigns.create(GUILD_C, "Second", DM_C)).id
        offer = await self.packs.by_code(GUILD_C, DM_C, self.code)
        await self.packs.follow_pack(GUILD_C, far2, DM_C, offer)
        mine = await self.brew.add(GUILD_C, far2, DM_C, kind="item", name="EMBER LAMP", text="x")
        first = await self.mine("Ember Lamp")
        await self.send(first)
        with self.assertRaises(RuleSetError):
            await self.packs.suggest_entry(GUILD_C, far2, DM_C, mine.id, mine.version)

    async def test_with_two_packs_the_dm_picks_one(self) -> None:
        other = (await self.campaigns.create(GUILD_A, "Other", DM_A)).id
        await self.brew.add(GUILD_A, other, DM_A, kind="item", name="Rope", text="x")
        await self.packs.publish_pack(GUILD_A, other, DM_A, "Rope Pack",
                                      await self.brew.list(GUILD_A, other))  # fmt: skip
        code = await self.packs.make_pack_code(GUILD_A, other, DM_A)
        other_follow = await self.packs.follow_pack(
            GUILD_B, self.far, DM_B, await self.packs.by_code(GUILD_B, DM_B, code)
        )
        entry = await self.mine("Ember Lamp")
        with self.assertRaises(RuleSetError) as err:
            await self.send(entry)
        self.assertEqual(str(err.exception), PICK_A_PACK)
        sid, _, campaign, pack = await self.send(entry, set_id=self.follow.set_id)
        self.assertEqual((campaign, pack), (self.pub, "Frost Pack"))
        self.assertIsNotNone(await self.packs.pack_suggestion(GUILD_A, self.pub, sid))
        # one open suggestion is per entry *per pack*: the same entry may go to the other too
        _, _, other_campaign, other_pack = await self.send(entry, set_id=other_follow.set_id)
        self.assertEqual((other_campaign, other_pack), (other, "Rope Pack"))
        with self.assertRaises(RuleSetError):  # but not twice to the same one
            await self.send(entry, set_id=self.follow.set_id)

    async def test_a_pack_that_stopped_sharing_takes_none(self) -> None:
        entry = await self.mine("Ember Lamp")
        await self.packs.stop_sharing_pack(GUILD_A, self.pub, DM_A)
        with self.assertRaises(RuleSetError):
            await self.send(entry)


class Answering(Base):
    async def sent(self, name: str = "Ember Lamp") -> str:
        sid, *_ = await self.send(await self.mine(name, "Glows warmly."))
        return sid

    async def test_accept_adds_a_new_entry_and_makes_the_next_version(self) -> None:
        sid = await self.sent()
        pack, version = await self.packs.accept_pack_suggestion(
            GUILD_A, self.pub, DM_A, sid, plan_ok=True
        )
        self.assertEqual((pack, version), ("Frost Pack", 2))
        own = {e.name: e.text for e in await self.brew.list(GUILD_A, self.pub)}
        self.assertEqual(own["Ember Lamp"], "Glows warmly.")  # in the publisher's own homebrew
        content = await self.packs.pack_content(GUILD_A, self.pub)
        assert content is not None
        self.assertEqual({e["name"] for e in content["entries"]}, {"Hollow King", "Ember Lamp"})
        answered = await self.packs.pack_suggestion(GUILD_A, self.pub, sid)
        assert answered is not None
        self.assertEqual(answered.status, "accepted")
        with self.assertRaises(RuleSetError):  # answered once only
            await self.packs.accept_pack_suggestion(GUILD_A, self.pub, DM_A, sid, plan_ok=True)

    async def test_accept_replaces_the_publishers_own_entry_of_that_name(self) -> None:
        sid = await self.sent("hollow king")
        await self.packs.accept_pack_suggestion(GUILD_A, self.pub, DM_A, sid, plan_ok=True)
        own = await self.brew.list(GUILD_A, self.pub)
        self.assertEqual([(e.name, e.text) for e in own], [("hollow king", "Glows warmly.")])
        self.assertEqual(own[0].version, 2)

    async def test_an_entry_of_that_name_from_a_book_is_never_replaced(self) -> None:
        sid = await self.sent("Booked")
        await self.brew.load_file(
            GUILD_A, self.pub, DM_A, [FileEntry("feat", "Booked", "Book words.")], from_book=True
        )
        with self.assertRaises(RuleSetError) as err:
            await self.packs.accept_pack_suggestion(GUILD_A, self.pub, DM_A, sid, plan_ok=True)
        self.assertEqual(str(err.exception), BOOK_NAME)
        booked = await self.brew.find(GUILD_A, self.pub, "Booked")
        assert booked is not None
        self.assertEqual(booked.text, "Book words.")

    async def test_the_same_words_already_in_the_pack_closes_it(self) -> None:
        same = await self.mine("Hollow King", "A crowned skeleton.", kind="monster")
        before = await self.brew.find(GUILD_A, self.pub, "Hollow King")
        assert before is not None
        sid, *_ = await self.send(same)
        with self.assertRaises(RuleSetError) as err:
            await self.packs.accept_pack_suggestion(GUILD_A, self.pub, DM_A, sid, plan_ok=True)
        self.assertEqual(str(err.exception), ALREADY_IN_PACK)
        closed = await self.packs.pack_suggestion(GUILD_A, self.pub, sid)
        assert closed is not None
        self.assertEqual(closed.status, "present")
        after = await self.brew.find(GUILD_A, self.pub, "Hollow King")
        assert after is not None
        self.assertEqual(after.version, before.version)  # nothing changed, so no version bump
        self.assertEqual((await self.packs.published_pack(GUILD_A, self.pub)).version, 1)  # type: ignore[union-attr]

    async def test_no_thanks_changes_nothing(self) -> None:
        sid = await self.sent()
        self.assertTrue(await self.packs.decline_pack_suggestion(GUILD_A, self.pub, DM_A, sid))
        self.assertFalse(await self.packs.decline_pack_suggestion(GUILD_A, self.pub, DM_A, sid))
        self.assertEqual((await self.packs.published_pack(GUILD_A, self.pub)).version, 1)  # type: ignore[union-attr]
        with self.assertRaises(RuleSetError):
            await self.packs.accept_pack_suggestion(GUILD_A, self.pub, DM_A, sid, plan_ok=True)

    async def test_only_the_publishers_dms_answer_and_the_plan_must_be_paid(self) -> None:
        sid = await self.sent()
        with self.assertRaises(HouseRuleError):
            await self.packs.accept_pack_suggestion(GUILD_A, self.pub, PLAYER, sid, plan_ok=True)
        with self.assertRaises(HouseRuleError):
            await self.packs.decline_pack_suggestion(GUILD_A, self.pub, PLAYER, sid)
        with self.assertRaises(RuleSetError) as err:
            await self.packs.accept_pack_suggestion(GUILD_A, self.pub, DM_A, sid, plan_ok=False)
        self.assertEqual(str(err.exception), rule_sets.NOT_PAID)
        # The follower's own server holds no such row to answer.
        self.assertIsNone(await self.packs.pack_suggestion(GUILD_B, self.far, sid))
        with self.assertRaises(RuleSetError):
            await self.packs.accept_pack_suggestion(GUILD_B, self.far, DM_B, sid, plan_ok=True)

    async def test_the_sender_hears_the_outcome_once(self) -> None:
        accepted = await self.sent("Ember Lamp")
        declined = await self.sent("Rope")
        waiting = await self.sent("Torch")
        await self.packs.accept_pack_suggestion(GUILD_A, self.pub, DM_A, accepted, plan_ok=True)
        await self.packs.decline_pack_suggestion(GUILD_A, self.pub, DM_A, declined)
        answers = {a.name: a for a in await self.packs.pack_sent_answers(GUILD_B, self.far)}
        self.assertEqual(
            {n: a.status for n, a in answers.items()},
            {"Ember Lamp": "accepted", "Rope": "declined", "Torch": "open"},
        )
        self.assertEqual(answers["Ember Lamp"].version, 2)
        told = [a.id for a in answers.values() if a.status != "open"]
        await self.packs.mark_pack_told(GUILD_B, self.far, told)
        left = await self.packs.pack_sent_answers(GUILD_B, self.far)
        self.assertEqual([a.id for a in left], [waiting])  # told once; the waiting one stays

    async def test_a_pack_that_stopped_sharing_is_told_as_gone(self) -> None:
        await self.sent()
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute("DELETE FROM rule_sets WHERE kind = 'homebrew'")
        (gone,) = await self.packs.pack_sent_answers(GUILD_B, self.far)
        self.assertEqual(gone.status, "gone")

    async def test_the_answer_door_reaches_only_the_ids_it_is_given(self) -> None:
        await self.sent()
        async with self.db.guild(GUILD_B) as conn:
            cur = await conn.execute("SELECT * FROM dmbot_pack_suggestion_answers(%s)", ("0" * 32,))
            self.assertEqual(await cur.fetchall(), [])
            cur = await conn.execute("SELECT count(*) AS n FROM pack_suggestions")
            row = await cur.fetchone()
            assert row is not None
            self.assertEqual(row["n"], 0)  # the follower's server can't read the publisher's rows

    async def test_notes_that_never_reached_the_screen_are_found_and_marked(self) -> None:
        sid = await self.sent()
        waiting = await self.packs.unnoted_pack_suggestions(GUILD_A, self.pub)
        self.assertEqual([(s.id, pack) for s, pack in waiting], [(sid, "Frost Pack")])
        await self.packs.mark_pack_noted(GUILD_A, self.pub, sid)
        self.assertEqual(await self.packs.unnoted_pack_suggestions(GUILD_A, self.pub), [])
