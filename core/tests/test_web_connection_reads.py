"""A connection said in the DM's own words, on the website (#2940 slice C), run as the website's
own database role: the four DM-only doors, the bot reading and writing through the same calls as
Discord, and what is never kept. The AI is a fake; names are invented."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import httpx

from dmbot.ai import Reply
from dmbot.memory import web_connection_reads as bot
from dmbot.memory.lookup import CampaignLookup
from dmbot.memory.models import CONFIRMED
from dmbot.memory.ontology import FALLBACK_PREDICATE
from dmbot.web import connection_reads as web
from dmbot.web import sessions
from dmbot.web.app import create_app
from dmbot.web.payments import FakeProvider
from dmbot.web.recaps import RecapError
from tests.test_recap_web import BOB, HEADERS, NOW
from tests.test_web_api import ALICE, FakeDiscord, settings
from tests.test_web_name_checks import GUILD, WithChecks

WORDS = "Mara owes Tavin the favour, not the other way round. Only Tavin knows."


def answer_of(*lines: dict[str, Any], who: str = "everyone", names: tuple[str, ...] = ()) -> Reply:
    body = {"connections": list(lines), "who": {"kind": who, "names": list(names)}}
    return Reply(json.dumps(body), cut=False)


def owes() -> dict[str, Any]:
    return {
        "a": "Mara", "how": "other", "words": "owes", "b": "Tavin",
        "quote": "Mara owes Tavin the favour", "inferred": "", "sure": False, "from": "",
        "change": "",
    }  # fmt: skip


class Hosted:
    """The running bot, as far as the job needs it."""

    def __init__(self, test: Connecting, *replies: Reply, plan: bool = True) -> None:
        self.test = test
        self.memory = test.memory
        self.replies = list(replies)
        self.complete = AsyncMock(side_effect=lambda *a, **k: self.replies.pop(0))
        self.connection_said_ai = SimpleNamespace(complete=self.complete)
        self.plan = plan

    async def get_campaign(self, guild_id: int, campaign_id: str) -> Any:
        return await self.test.campaign_store.get(guild_id, campaign_id)

    async def plan_allows(self, action: str, guild_id: int, campaign: Any, **_: Any) -> bool:
        return self.plan

    async def names_lookup(self, guild_id: int, campaign_id: str) -> Any:
        return CampaignLookup.build(await self.memory.lookup_data(guild_id, campaign_id))


class Connecting(WithChecks):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        from dmbot.campaigns import CampaignStore

        self.campaign_store = CampaignStore(self.db)
        self.reads = bot.ActionStore(self.db)
        self.cref = f"{GUILD}.{self.one.id}"
        self.mara = await self.known("Mara", "character")
        self.tavin = await self.known("Tavin", "character")

    async def say(self, text: str = WORDS, **kw: Any) -> str:
        return await web.request(
            self.web, self.session, self.cref,
            {"a": self.mara, "b": self.tavin, "text": text, **kw},
        )  # fmt: skip

    async def code(self, coro: Any) -> str:
        with self.assertRaises(RecapError) as caught:
            await coro
        return str(caught.exception.code)

    async def rows(self) -> list[dict[str, Any]]:
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                "SELECT id, job, state, error, words, proposals, left_out, batches"
                " FROM web_connection_reads ORDER BY seq"
            )
            return [dict(r) for r in await cur.fetchall()]

    async def work(self, host: Hosted) -> bool:
        item = await self.reads.claim_next(GUILD)
        assert item is not None
        return await bot.run_one(host, self.reads, GUILD, item)  # type: ignore[arg-type]

    async def links(self) -> list[Any]:
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                "SELECT subject_id, object_id, predicate, detail, status FROM memory_relations"
                " WHERE campaign_id = %s ORDER BY created_at, id",
                (self.one.id,),
            )
            return list(await cur.fetchall())

    async def bob(self) -> Any:
        token = await sessions.sign_in(
            self.web, cast(Any, BOB), [await self._guild()], now=NOW, days=30
        )
        found = await sessions.find(self.web, token, now=NOW)
        assert found is not None
        return found

    async def _guild(self) -> Any:
        from tests.test_web_api import THURSDAY

        return THURSDAY

    # ---- the doors ------------------------------------------------------------------------

    async def test_a_dm_queues_a_read_and_it_waits_with_the_words_only_in_the_row(self) -> None:
        rid = await self.say()
        [row] = await self.rows()
        self.assertEqual(
            (row["id"], row["job"], row["state"], row["words"]), (rid, "read", "waiting", WORDS)
        )
        got = await web.status(self.web, self.session, self.cref, rid, moment=NOW)
        self.assertEqual((got["stage"], got["job"]), ("waiting", "read"))
        self.assertNotIn("Tavin the favour", repr(got))

    async def test_the_refusals_are_plain_codes(self) -> None:
        self.assertEqual(await self.code(self.say("   ")), "bad_text")
        self.assertEqual(
            await self.code(
                web.request(
                    self.web, self.session, self.cref,
                    {"a": self.mara, "b": self.mara, "text": WORDS},
                )
            ),
            "bad_choice",
        )  # fmt: skip
        await self.say()
        self.assertEqual(await self.code(self.say()), "busy")  # one read at a time
        other = await self.known("Brin", "character")
        old = (
            await self.memory.add_relation(
                GUILD, self.one.id, other, "knows", self.tavin, source="dm", confidence=1.0,
                status=CONFIRMED,
            )
        ).value[0]  # fmt: skip
        self.assertEqual(await self.code(self.say(edit=old.id)), "bad_choice")  # not the pair's

    async def test_a_player_and_a_stranger_get_nothing(self) -> None:
        rid = await self.say()
        bob = await self.bob()
        self.assertEqual(await self.code(web.status(self.web, bob, self.cref, rid)), "not_found")
        self.assertEqual(
            await self.code(
                web.request(
                    self.web, bob, self.cref, {"a": self.mara, "b": self.tavin, "text": "x"}
                )
            ),
            "not_found",
        )
        self.assertEqual(
            await self.code(
                web.use(self.web, bob, self.cref, rid, {"picks": [1], "who": "everyone"})
            ),
            "not_found",
        )
        self.assertEqual(await self.code(web.undo(self.web, bob, self.cref, rid)), "not_found")

    async def test_ten_reads_an_hour_and_one_the_ai_never_answered_is_not_counted(self) -> None:
        for _ in range(10):
            await self.say()
            await self.sql(
                "UPDATE web_connection_reads SET state = 'done', words = '' WHERE state = 'waiting'"
            )
        self.assertEqual(await self.code(self.say()), "hour_full")
        await self.sql("UPDATE web_connection_reads SET state = 'failed', error = 'unreadable'")
        await self.say()  # none counted any more

    async def sql(self, statement: str, *args: Any) -> None:
        async with self.db.guild(GUILD) as conn:
            await conn.execute(statement, args)

    # ---- the bot: read, use, undo ---------------------------------------------------------

    async def test_the_bot_reads_the_words_and_the_page_gets_plain_sentences(self) -> None:
        rid = await self.say()
        host = Hosted(self, answer_of(owes(), who="some", names=("Tavin",)))
        await self.work(host)
        [row] = await self.rows()
        self.assertEqual((row["state"], row["words"]), ("ready", ""))  # the words are gone
        got = await web.status(self.web, self.session, self.cref, rid, moment=NOW)
        self.assertEqual(got["stage"], "done")
        [one] = got["proposals"]
        self.assertEqual(one["sentence"], "Mara “owes” Tavin")
        self.assertEqual(
            (got["whoKnows"], [c["name"] for c in got["characters"]]), ("some", ["Tavin"])
        )
        self.assertEqual(await self.links(), [])  # nothing is written by reading

    async def test_use_this_writes_one_change_and_undo_takes_it_back_whole(self) -> None:
        old = (
            await self.memory.add_relation(
                GUILD, self.one.id, self.tavin, FALLBACK_PREDICATE, self.mara, source="dm",
                confidence=1.0, status=CONFIRMED, detail="owes",
            )
        ).value[0]  # fmt: skip
        rid = await self.say(edit=old.id)
        await self.work(Hosted(self, answer_of(owes(), who="dm")))
        await web.use(self.web, self.session, self.cref, rid, {"picks": [1], "who": "dm"})
        await self.work(Hosted(self))
        [row] = await self.rows()
        self.assertEqual((row["job"], row["state"]), ("use", "done"))
        live = [r for r in await self.links() if r["status"] == CONFIRMED]
        self.assertEqual(
            [(r["subject_id"], r["object_id"]) for r in live], [(self.mara, self.tavin)]
        )
        got = await web.status(self.web, self.session, self.cref, rid, moment=NOW)
        self.assertTrue(got["canUndo"])
        await web.undo(self.web, self.session, self.cref, rid)
        await self.work(Hosted(self))
        back = [r for r in await self.links() if r["status"] == CONFIRMED]
        self.assertEqual(
            [(r["subject_id"], r["object_id"]) for r in back], [(self.tavin, self.mara)]
        )
        self.assertEqual((await self.rows())[0]["state"], "undone")

    async def test_a_connection_changed_since_the_read_is_not_replaced(self) -> None:
        old = (
            await self.memory.add_relation(
                GUILD, self.one.id, self.tavin, FALLBACK_PREDICATE, self.mara, source="dm",
                confidence=1.0, status=CONFIRMED, detail="owes",
            )
        ).value[0]  # fmt: skip
        rid = await self.say(edit=old.id)
        await self.work(Hosted(self, answer_of(owes())))
        await self.memory.update_relation(
            GUILD, self.one.id, old.id, source="dm", secret=True, dm_chose_secret=True
        )  # the DM hid it meanwhile
        await web.use(self.web, self.session, self.cref, rid, {"picks": [1], "who": "everyone"})
        await self.work(Hosted(self))
        [row] = await self.rows()
        self.assertEqual((row["state"], row["error"]), ("failed", "changed"))
        self.assertEqual(len([r for r in await self.links() if r["status"] == CONFIRMED]), 1)

    async def test_a_read_the_ai_could_not_answer_fails_plainly_and_keeps_no_words(self) -> None:
        await self.say()
        junk = Reply("not json", cut=False)
        await self.work(Hosted(self, junk, junk))
        [row] = await self.rows()
        self.assertEqual((row["state"], row["error"], row["words"]), ("failed", "unreadable", ""))

    async def test_a_plan_without_the_ai_and_a_dm_who_left_are_refused(self) -> None:
        await self.say()
        await self.work(Hosted(self, plan=False))
        self.assertEqual((await self.rows())[0]["error"], "not_on_plan")
        await self.sql("UPDATE web_connection_reads SET state = 'done', words = '' WHERE true")
        await self.say()
        await self.sql("DELETE FROM campaign_dms WHERE campaign_id = %s", self.one.id)
        await self.work(Hosted(self, answer_of(owes())))
        self.assertEqual((await self.rows())[1]["error"], "not_yours")

    async def test_use_needs_a_ready_read_and_a_real_choice(self) -> None:
        rid = await self.say()
        self.assertEqual(
            await self.code(
                web.use(self.web, self.session, self.cref, rid, {"picks": [1], "who": "dm"})
            ),
            "not_ready",
        )
        await self.work(Hosted(self, answer_of(owes())))
        for body in (
            {"picks": [2], "who": "dm"},
            {"picks": [], "who": "dm"},
            {"picks": [1], "who": "keep"},  # (nothing to keep: it corrects nothing)
            {"picks": [1], "who": "some"},  # (some, but no one named)
            {"picks": [1], "who": "sometimes"},
        ):
            with self.subTest(body=body):
                self.assertEqual(
                    await self.code(web.use(self.web, self.session, self.cref, rid, body)),
                    "bad_choice",
                )

    async def test_a_stale_claim_is_taken_again_and_the_old_worker_writes_nothing(self) -> None:
        await self.say()
        first = await self.reads.claim_next(GUILD)
        assert first is not None
        self.assertIsNone(await self.reads.claim_next(GUILD))  # held: nobody else may take it
        await self.sql("UPDATE web_connection_reads SET claimed_at = 1")  # the worker went quiet
        again = await self.reads.claim_next(GUILD)
        assert again is not None and again.id == first.id
        late = await self.reads.finish(GUILD, first, bot.Outcome(proposals=[]))
        self.assertFalse(late)  # the old worker's answer is refused
        self.assertEqual((await self.rows())[0]["state"], "working")

    async def test_an_undo_that_could_not_be_done_can_be_pressed_again(self) -> None:
        rid = await self.say()
        await self.work(Hosted(self, answer_of(owes())))
        await web.use(self.web, self.session, self.cref, rid, {"picks": [1], "who": "everyone"})
        await self.work(Hosted(self))
        await web.undo(self.web, self.session, self.cref, rid)
        down = Hosted(self)
        down.memory = cast(Any, None)  # the bot has no memory just now
        await self.work(down)
        got = await web.status(self.web, self.session, self.cref, rid, moment=NOW)
        self.assertEqual((got["canUndo"], got["undoError"]), (True, "not_available"))  # still there
        await web.undo(self.web, self.session, self.cref, rid)  # and it can be asked again
        await self.work(Hosted(self))
        self.assertEqual((await self.rows())[0]["state"], "undone")

    async def test_only_the_characters_the_read_named_can_be_picked_to_know(self) -> None:
        rid = await self.say()
        await self.work(Hosted(self, answer_of(owes(), who="some", names=("Tavin",))))
        stranger = await self.known("Brin", "character")
        self.assertEqual(
            await self.code(
                web.use(
                    self.web, self.session, self.cref, rid,
                    {"picks": [1], "who": "some", "characters": [stranger]},
                )
            ),
            "bad_choice",
        )  # fmt: skip
        await web.use(
            self.web, self.session, self.cref, rid,
            {"picks": [1], "who": "some", "characters": [self.tavin]},
        )  # fmt: skip

    async def test_a_dm_who_left_can_no_longer_read_a_ready_job(self) -> None:
        rid = await self.say()
        await self.work(Hosted(self, answer_of(owes())))
        await self.sql("DELETE FROM campaign_dms WHERE campaign_id = %s", self.one.id)
        self.assertEqual(
            await self.code(web.status(self.web, self.session, self.cref, rid)), "not_found"
        )

    async def test_the_sweep_removes_a_days_old_job_with_its_words(self) -> None:
        await self.say()
        await self.sql("UPDATE web_connection_reads SET created_at = created_at - 90000")
        self.assertEqual(await self.reads.sweep(GUILD), 1)
        self.assertEqual(await self.rows(), [])

    # ---- the routes -----------------------------------------------------------------------

    async def test_the_routes_answer_404_until_the_switch_is_on(self) -> None:
        for on in (False, True):
            app = create_app(
                settings(connection_said_on=on),
                self.web,
                FakeDiscord(),
                payments=FakeProvider(b"h" * 32, "https://x.example"),
                clock=lambda: NOW,
            )
            token = await sessions.sign_in(
                self.web, cast(Any, ALICE), [await self._guild()], now=NOW, days=30
            )
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="https://web-api.internal.test"
            ) as client:
                client.cookies.set("__Host-dmbot_session", token, domain="web-api.internal.test")
                got = await client.post(
                    f"/me/campaigns/{self.cref}/connections/read",
                    headers=HEADERS,
                    json={"a": self.mara, "b": self.tavin, "text": WORDS},
                )
            self.assertEqual(got.status_code, 202 if on else 404, got.text)
            if on:
                self.assertIn("answer", got.json())
