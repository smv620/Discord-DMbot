"""The stalled count in one SQL function (#3696; Supervisor's ruling (a)): `dmbot_thread_quiet`
gives, for each thread, when it was last touched and how many known sessions started since,
exactly as the Python reference (`threads.last_touch`, `threads.sessions_since`) works it out from
the same rows, so the bot's after-session line and the website's Threads panel can't disagree.
Invented names. Needs Postgres."""

from __future__ import annotations

import secrets
from typing import Any

from dmbot.memory import states
from dmbot.memory import threads as th
from dmbot.memory.models import GOING, OPEN, Heard
from tests.test_memory_store import DM, GUILD_A, GUILD_B
from tests.test_threads import S1, S2, S3, S4, Threads

PREDICATES = ("in_thread", "advances", "resolves", "promised_by", "promised_to")


class QuietInSql(Threads):
    async def quiet(self, ids: list[str], guild: int = GUILD_A, campaign: str = "") -> Any:
        async with self.db.guild(guild) as conn:
            cur = await conn.execute(
                "SELECT entity_id, touched_at, quiet FROM dmbot_thread_quiet(%s, %s, %s)",
                (guild, campaign or self.c, ids),
            )
            return {r["entity_id"]: (r["touched_at"], r["quiet"]) for r in await cur.fetchall()}

    async def reference(self, thread_id: str) -> tuple[int, int]:
        """The Python reference, from the raw rows, as `store._threads` read it before 0575."""
        ids = (GUILD_A, self.c)
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute(
                "SELECT created_at FROM memory_entities WHERE guild_id = %s AND campaign_id = %s"
                " AND id = %s",
                (*ids, thread_id),
            )
            row = await cur.fetchone()
            assert row is not None
            created = int(row["created_at"])
            cur = await conn.execute(
                "SELECT * FROM memory_states WHERE guild_id = %s AND campaign_id = %s"
                " AND entity_id = %s",
                (*ids, thread_id),
            )
            history = [states.state_of(r) for r in await cur.fetchall()]
            # (as the old `_threads` did: the facts read in SQL, the confirmed subjects in Python,
            # so a mistake in the function's join isn't copied here)
            cur = await conn.execute(
                "SELECT subject_id, from_session_at, created_at FROM memory_relations"
                " WHERE guild_id = %s AND campaign_id = %s AND object_id = %s"
                " AND status = 'confirmed' AND to_session_at IS NULL AND predicate = ANY(%s)",
                (*ids, thread_id, list(PREDICATES)),
            )
            raw = await cur.fetchall()
            cur = await conn.execute(
                "SELECT id FROM memory_entities WHERE guild_id = %s AND campaign_id = %s"
                " AND status = 'confirmed'",
                ids,
            )
            confirmed = {r["id"] for r in await cur.fetchall()}
            facts = [f for f in raw if f["subject_id"] in confirmed]
            cur = await conn.execute(
                "SELECT entity_id, session_started_at FROM memory_heard WHERE guild_id = %s"
                " AND campaign_id = %s",
                ids,
            )
            heard = await cur.fetchall()
        touch = th.last_touch(
            created,
            history,
            [f["from_session_at"] for f in facts],
            [f["created_at"] for f in facts],
            [h["session_started_at"] for h in heard if h["entity_id"] == thread_id],
        )
        known = {int(h["session_started_at"]) for h in heard}
        return touch, th.sessions_since(touch, known)

    async def test_the_sql_count_is_the_python_reference(self) -> None:
        other = (await self.start("The Lost Bell", "mystery", OPEN)).id
        quiet_one = (await self.start("Nothing yet", "hook", OPEN)).id
        await self.memory.set_condition(
            GUILD_A, self.c, self.heir, GOING, source="dm", from_session_at=S1
        )
        await self.memory.involve(
            GUILD_A, self.c, self.heir, self.mira, "quest giver", source="dm", from_session_at=S2
        )
        await self.memory.link_event(
            GUILD_A, self.c, other, self.ford, ends_it=False, source="dm", from_session_at=S1
        )
        for s, who in ((S2, self.mira), (S3, other), (S4, self.mira)):
            await self.heard(s, who)
        got = await self.quiet([self.heir, other, quiet_one])
        for thread in (self.heir, other, quiet_one):
            with self.subTest(thread):
                self.assertEqual(got[thread], await self.reference(thread))
        self.assertEqual(got[self.heir][1], 2)  # (S3 and S4 since Mira joined it in S2)
        self.assertEqual(got[other][1], 1)  # (its name heard in S3: only S4 since)
        # (the store's read model uses the function: the same numbers)
        listed = await self.memory.threads(GUILD_A, self.c, stalled_after=3)
        found = {t.thread.id: t for t in listed}
        self.assertEqual(
            (found[self.heir].last_touch, found[self.heir].quiet_sessions), got[self.heir]
        )

    async def test_a_session_where_nobody_was_heard_and_an_ended_fact_do_not_count(self) -> None:
        await self.memory.set_condition(
            GUILD_A, self.c, self.heir, GOING, source="dm", from_session_at=S1
        )
        made = await self.memory.involve(
            GUILD_A, self.c, self.heir, self.mira, "victim", source="dm", from_session_at=S3
        )
        await self.heard(S4, self.mira)
        async with self.db.guild(GUILD_A) as conn:  # (the Story Graph ended that part)
            await conn.execute(
                "UPDATE memory_relations SET to_session_at = %s WHERE guild_id = %s"
                " AND campaign_id = %s AND id = %s",
                (S4, GUILD_A, self.c, made.value.id),
            )
        got = await self.quiet([self.heir])
        self.assertEqual(got[self.heir], await self.reference(self.heir))
        self.assertEqual(got[self.heir][1], 1)  # (only S4 is known; the ended part is no touch)

    async def test_what_never_touches_a_thread(self) -> None:
        # (a fact from a name not yet confirmed, a fact not confirmed itself, and a `led_to` link
        # in a chain: none moves the thread; a state with no session counts by when it was saved)
        await self.memory.set_condition(
            GUILD_A, self.c, self.heir, GOING, source="dm", from_session_at=S1
        )
        before = await self.quiet([self.heir])
        maybe = await self.add("A stranger", type="character", role="npc", status="proposed")
        nxt = (await self.start("The Heir's Return", "quest", OPEN)).id
        async with self.db.guild(GUILD_A) as conn:
            for subject, predicate, status in (
                (maybe, "in_thread", "confirmed"),
                (self.mira, "advances", "proposed"),
                (nxt, "led_to", "confirmed"),
            ):
                await conn.execute(
                    "INSERT INTO memory_relations (guild_id, campaign_id, id, subject_id,"
                    " predicate, object_id, detail, source, confidence, status, mention_ids,"
                    " secret, created_at, from_session_at) VALUES (%s, %s, %s, %s, %s, %s, '',"
                    " 'dm', 1, %s, '{}', false, %s, %s)",
                    (GUILD_A, self.c, secrets.token_hex(16), subject, predicate, self.heir,
                     status, S4, S4),
                )  # fmt: skip
        for s in (S2, S3):
            await self.heard(s, self.mira)
        got = await self.quiet([self.heir])
        self.assertEqual(got[self.heir][0], before[self.heir][0])  # (touched as before)
        self.assertEqual(got[self.heir], await self.reference(self.heir))
        self.assertEqual(got[self.heir][1], 2)

    async def test_only_that_campaigns_sessions_and_never_another_servers(self) -> None:
        self.assertEqual(await self.quiet([]), {})
        self.assertEqual(await self.quiet(["0" * 32]), {})
        await self.memory.set_condition(
            GUILD_A, self.c, self.heir, GOING, source="dm", from_session_at=S1
        )
        await self.heard(S2, self.mira)
        mine = await self.quiet([self.heir])
        # (another campaign in the same server holds later sessions: they are not this one's)
        other = (await self.campaigns.create(GUILD_A, "Elsewhere", DM)).id
        await self.memory.add_session_heard(GUILD_A, other, S3, [Heard(self.mira, 7, 1)])
        await self.memory.add_session_heard(GUILD_A, other, S4, [Heard(self.mira, 7, 1)])
        self.assertEqual(await self.quiet([self.heir]), mine)
        self.assertEqual(await self.quiet([self.heir], campaign=other), {})
        # (another server's call on this server's connection sees nothing: row-level security)
        self.assertEqual(await self.quiet([self.heir], guild=GUILD_B, campaign=self.c), {})
