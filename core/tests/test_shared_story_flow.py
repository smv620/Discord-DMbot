"""Shared story, start to finish (#1743): one test module that runs the real path with only
production entry points and no direct inserts into story tables, so a mistake between the slices
(like reading `memory_mentions` where production writes `memory_heard`) cannot hide behind each
slice's own set-up. An invented book (never real text); the AI is canned. Needs the test database.

The steps: a DM turns on Guide me and says they have the right; gives the book; Read it now builds
the plan; a session records which names were heard; the DM links two of them to the book; the DM
marks the person the book needs alive as dead and Watch the story tells it once; Guide me (the
button and the sidebar's) guesses the chapter from what was heard and shows the open clash, in
the same words; a backup keeps the adventure list and nothing of the plan, and a restore gives no
button until a DM confirms; another campaign, in this server and in another, sees none of it."""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any
from unittest import mock
from unittest.mock import AsyncMock

from dmbot.bot import DMBot
from dmbot.campaigns.models import STORY_GUIDE
from dmbot.campaigns.store import decode_backup, encode_backup
from dmbot.memory import book_links as links
from dmbot.memory import guide
from dmbot.memory import plan_reader as reader
from dmbot.memory import story_plan as sp
from dmbot.memory.adventure import AdventureSection, AdventureStore
from dmbot.memory.models import Heard
from dmbot.memory.story_watch import StoryWatch
from dmbot.ui import guide as guide_ui
from tests import speed
from tests import test_story_plan as plans
from tests.test_adventure import press
from tests.test_memory_store import DM, GUILD_A, GUILD_B, ConfirmedMemoryTest

SESSION = 1_700_100_000
SPEAKER = 11


class Flow(ConfirmedMemoryTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.campaigns.register_section(AdventureSection())
        self.campaigns.register_section(sp.SpendSection())
        self.store = AdventureStore(self.db, clock=lambda: self.now)
        self.plan = sp.PlanStore(self.db, clock=lambda: self.now)
        self.watch = StoryWatch(self.db, clock=lambda: self.now)
        self.paid = True  # the campaign owner's plan
        self.posts: list[tuple[int, str]] = []
        patcher = mock.patch.object(sp, "BATCH_CHUNKS", 1)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.busy = speed.slowdown()  # how slow this machine is before the flow (#2490)
        self.started = time.perf_counter()

    # ---- the bot's side, as little of it as the flow needs ----

    async def plan_allows(self, *args: Any, **kw: Any) -> bool:
        return self.paid

    async def post(self, channel_id: int, text: str, view: Any = None, **kw: Any) -> bool:
        self.posts.append((channel_id, text))
        return True

    def bot(self) -> Any:
        """Stands in for DMBot with the parts the story features use (their own code runs)."""
        host: Any = SimpleNamespace(
            memory=self.memory,
            campaigns=self.campaigns,
            adventure=self.store,
            plan=self.plan,
            watch=self.watch,
            lookup=None,
            plan_allows=self.plan_allows,
            post=self.post,
            transcripts=None,
            clocks=None,
            tables={},
        )
        host.guide_button_allowed = lambda g, c: DMBot.guide_button_allowed(host, g, c)
        return host

    def pressing(self, user: int = DM, guild: int = GUILD_A) -> Any:
        it = press(user, guild)
        it.client = self.bot()
        it.type = None

        async def followup(content: str = "", **kw: Any) -> None:
            it.sent.update(content=content, **kw)

        it.followup.send = followup
        return it

    # ---- the steps, each through its production entry point ----

    async def turn_on_and_give_the_book(
        self, guild: int = GUILD_A, campaign: str | None = None
    ) -> Any:
        cid = campaign or self.c
        await self.campaigns.set_story_mode(guild, cid, STORY_GUIDE, confirmed_by=DM)
        return await self.store.add(guild, cid, DM, "keep.txt", plans.BOOK)

    async def read_it_now(self, doc: Any, guild: int = GUILD_A, campaign: str | None = None) -> Any:
        cid = campaign or self.c
        state = await self.plan.begin(guild, cid, DM, doc.id)
        return await reader.run(
            plans.Canned(plans.FIRST, plans.SECOND, plans.THIRD),
            self.plan,
            guild_id=guild,
            campaign_id=cid,
            document_id=doc.id,
            start_chunk=state.next_chunk,
            model=lambda: plans.MODEL,
            pause=AsyncMock(),
        )

    async def name_it_at_the_table(self) -> tuple[str, str]:
        """The DM keeps two names the book also has, and a session hears them."""
        varn = (
            await self.memory.add_entity(
                GUILD_A,
                self.c,
                type="character",
                role="npc",
                name="Captain Varn",
                source="dm",
                status="confirmed",
            )
        ).value.id
        keep = (
            await self.memory.add_entity(
                GUILD_A, self.c, type="place", name="The old keep", source="dm", status="confirmed"
            )
        ).value.id
        kept = await self.memory.add_session_heard(
            GUILD_A, self.c, SESSION, [Heard(varn, SPEAKER, 3), Heard(keep, SPEAKER, 2)]
        )
        self.assertEqual(kept, 2)
        return varn, keep

    async def link_them(self) -> None:
        pairs = await links.suggestions(self.memory, GUILD_A, self.c)
        self.assertEqual({p.plan_name for p in pairs}, {"Captain Varn", "The old keep"})
        done = await links.decide(
            self.memory,
            GUILD_A,
            self.c,
            [(p.plan_id, p.entity_id, links.SAME) for p in pairs],
            DM,
        )
        self.assertEqual(done.value, 2)

    async def rows_in(self, table: str, guild: int, campaign: str | None = None) -> int:
        async with self.db.guild(guild) as conn:
            cur = await conn.execute(
                f"SELECT count(*) AS n FROM {table} WHERE campaign_id = %s", (campaign or self.c,)
            )
            row = await cur.fetchone()
        assert row is not None
        return int(row["n"])

    # ---- the flow ----

    async def test_the_whole_shared_story_flow(self) -> None:
        # 1-2. A DM on a paid plan turns it on, says they have the right, gives the book.
        doc = await self.turn_on_and_give_the_book()
        self.assertEqual(doc.chunks, 3)
        # 3. Read it now: chapters, places, a person, and "needs … alive".
        report = await self.read_it_now(doc)
        self.assertEqual((report.stopped, report.nodes, report.links), ("", 6, 2))
        # 4-5. A session hears two names; the DM links them to the book.
        varn, _ = await self.name_it_at_the_table()
        await self.link_them()
        # 6. The person the chapter needs alive is dead: Watch the story says it once, with chapter.
        await self.memory.set_condition(GUILD_A, self.c, varn, "alive", source="dm")
        self.now += 10
        await self.memory.set_condition(GUILD_A, self.c, varn, "dead", source="dm")
        bot = self.bot()
        await DMBot.watch_story(bot, GUILD_A, self.c, 4242)
        (told,) = self.posts
        self.assertEqual(told[0], 4242)  # advice goes to the DM's channel only
        self.assertIn("Chapter 2: The Cellar", told[1])
        self.assertIn("needs **Captain Varn** alive", told[1])
        self.assertIn("is dead", told[1])
        await DMBot.watch_story(bot, GUILD_A, self.c, 4242)
        self.assertEqual(len(self.posts), 1)  # not again
        # 7. Guide me, by button and by the sidebar: the right chapter, what is ahead, the clash.
        pressed = self.pressing()
        await guide_ui.GuideButton(self.c).callback(pressed)
        text = pressed.sent["content"]
        self.assertIn("Probably **Chapter 2: The Cellar** (heard: The old keep)", text)
        self.assertIn("The book's next chapter: **Chapter 3: The Circle**", text)
        self.assertIn("needs **Captain Varn** alive", text)  # the open clash
        table: Any = SimpleNamespace(guild_id=GUILD_A, campaign_id=self.c)
        spoken = await DMBot.sidebar_guide(self.bot(), table, DM)
        self.assertEqual(spoken, text)  # the same words
        # "I know": nothing more is said, and Guide me no longer lists it.
        (clash,) = (await self.watch.open_for(GUILD_A, self.c, frozenset(
            [n for n in await self._chapter_ids()]
        )))  # fmt: skip
        self.assertTrue(await self.watch.know(GUILD_A, self.c, clash.id))
        await DMBot.watch_story(bot, GUILD_A, self.c, 4242)
        self.assertEqual(len(self.posts), 1)
        after = self.pressing()
        await guide_ui.GuideButton(self.c).callback(after)
        self.assertNotIn("needs **Captain Varn** alive", after.sent["content"])
        # 8. A backup keeps the adventure list and nothing of the plan; a restore has no button.
        raw = encode_backup(await self.campaigns.export(GUILD_A, self.c))
        restored = await self.campaigns.import_backup(GUILD_B, decode_backup(raw), DM)
        self.assertEqual(await self.rows_in("adventure_documents", GUILD_B, restored.id), 1)
        for table_name in (
            "plan_nodes",
            "plan_links",
            "plan_divergences",
            "plan_position",
            "plan_same_as",
        ):
            self.assertEqual(await self.rows_in(table_name, GUILD_B, restored.id), 0, table_name)
        theirs = await self.campaigns.get(GUILD_B, restored.id)
        assert theirs is not None
        self.assertFalse(await DMBot.guide_button_allowed(self.bot(), GUILD_B, theirs))
        # The spend ledger does travel (amounts only, so a restore can't give a file fresh money).
        self.assertEqual(await self.rows_in("plan_spend", GUILD_B, restored.id), 1)
        text = raw if isinstance(raw, str) else raw.decode("utf8", "ignore")
        for name in ("plan_nodes", "plan_links", "plan_same_as", "plan_divergences"):
            self.assertNotIn(name, text)
        refused = self.pressing(guild=GUILD_B)
        await guide_ui.GuideButton(restored.id).callback(refused)
        self.assertIn(guide_ui.NOT_GUIDE, refused.sent["content"])
        self.assertNotIn("Chapter 2", refused.sent["content"])
        # A DM who confirms again gets the button back (a restore is not locked out for good).
        await self.campaigns.set_story_mode(GUILD_B, restored.id, STORY_GUIDE, confirmed_by=DM)
        self.assertTrue(await DMBot.guide_button_allowed(self.bot(), GUILD_B, theirs))
        again = self.pressing(guild=GUILD_B)
        await guide_ui.GuideButton(restored.id).callback(again)
        self.assertIn(guide_ui.NOT_READ, again.sent["content"])  # no plan came with the backup
        # 9. Another campaign in this server, and one in another server, see none of it.
        other = (await self.campaigns.create(GUILD_A, "Other table", DM)).id
        await self.turn_on_and_give_the_book(GUILD_A, other)
        away = (await self.campaigns.create(GUILD_B, "Elsewhere", DM)).id
        await self.turn_on_and_give_the_book(GUILD_B, away)
        for guild, campaign in ((GUILD_A, other), (GUILD_B, away)):
            self.assertEqual((await self.watch.check(guild, campaign)).shown, ())
            self.assertEqual(await self.rows_in("plan_nodes", guild, campaign), 0)
            nothing = self.pressing(guild=guild)
            await guide_ui.GuideButton(campaign).callback(nothing)
            self.assertIn(guide_ui.NOT_READ, nothing.sent["content"])
            self.assertNotIn("Captain Varn", nothing.sent["content"])
        self.assertEqual(len(self.posts), 1)
        took = time.perf_counter() - self.started
        print(f"\nShared story flow: {took:.1f} s")
        # The limit follows how busy the machine was before and is after, like the other timing
        # tests (#2490): a flow that really is slow misses it on a quiet machine too.
        self.assertLess(took, speed.allowed(10.0, self.busy))

    async def _chapter_ids(self) -> list[str]:
        nodes, _ = await guide.read(
            self.memory, GUILD_A, self.c, (await self.store.documents(GUILD_A, self.c))[0].id
        )
        return [n.id for n in nodes]
