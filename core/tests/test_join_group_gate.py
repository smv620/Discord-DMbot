"""The website's `join_group` asks the gate, not the flag (#4811, party knowledge slice 5,
`docs/design/party_knowledge.md` §9): a group the table holds is joined; one the table doesn't (the
DM hid it, or a newcomer hasn't been told) is refused with the fixed word and nothing is written.
Invented names; needs Postgres."""

from __future__ import annotations

import time
import uuid
from typing import Any

from dmbot.memory.models import CONFIRMED, PLAYER_CHARACTER
from dmbot.recaps import name_answers as na
from tests.test_web_api import ALICE
from tests.test_web_name_checks import GUILD, WithChecks


class JoiningAGroup(WithChecks):
    """Vell and Tuck play; the faction is made while they are the whole party."""

    async def entity(self, name: str, kind: str, **more: Any) -> str:
        made = await self.memory.add_entity(
            GUILD, self.one.id, type=kind, name=name, source="dm", status=CONFIRMED, **more
        )
        return str(made.value.id)

    async def pc(self, name: str, player: int) -> str:
        return await self.entity(name, "character", role=PLAYER_CHARACTER, played_by=player)

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.memory.party_join = True  # (a newcomer is asked about, not copied everything, #4537)
        self.vell = await self.pc("Vell", 801)
        self.tuck = await self.pc("Tuck", 802)
        self.group = await self.entity("The Ash Wolves", "faction")
        async with self.db.guild(GUILD) as conn:  # (a session has been played: joining is asked)
            sid = await self.sessions_open()
            await conn.execute(
                "UPDATE transcript_sessions SET line_count = 3 WHERE id = %s", (sid,)
            )

    async def sessions_open(self) -> str:
        from dmbot.transcript.store import TranscriptStore

        sid = await TranscriptStore(self.db).open_session(GUILD, self.one.id, 5_000)
        await TranscriptStore(self.db).end_session(GUILD, sid, 6_000)
        return sid

    async def waiting(self) -> str:
        made = await self.memory.add_entity(
            GUILD, self.one.id, type="character", name="Tavin and Mira", source="scan",
            description="Heard 2 times",
        )  # fmt: skip
        return str(made.value.id)

    async def knowers(self) -> int:
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                "SELECT count(*) AS n FROM memory_knowers WHERE guild_id = %s", (GUILD,)
            )
            row = await cur.fetchone()
        return int(row["n"]) if row else 0

    async def join(self, listed: str) -> str:
        async with self.db.guild(GUILD) as conn:
            await conn.execute(
                "INSERT INTO name_answers (guild_id, campaign_id, id, entity_id, user_id, action,"
                " target_id, text, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    GUILD, self.one.id, uuid.uuid4().hex, listed, ALICE.id, "join_group",
                    self.group, "", int(time.time()),
                ),
            )  # fmt: skip
        answer = await self.answers.claim_next(GUILD)
        assert answer is not None
        kept = na.Kept()
        why = await na.apply(self.memory, GUILD, {ALICE.id}, answer, kept, self.answers)
        await self.answers.finish(GUILD, answer, error=why, kept=None)
        return why

    async def test_a_group_the_table_holds_is_joined(self) -> None:
        self.assertEqual(await self.join(await self.waiting()), "")

    async def test_a_group_the_newcomer_hasnt_been_told_is_refused(self) -> None:
        await self.pc("Cara", 803)  # (joins after the group was made: not told it yet)
        self.assertEqual(await self.join(await self.waiting()), na.NOT_FOUND_WORD)

    async def test_a_group_the_dm_hid_is_refused(self) -> None:
        await self.memory.set_hidden_from_players(
            GUILD, self.one.id, self.group, True, source="dm", dm_chose_secret=True
        )
        self.assertEqual(await self.join(await self.waiting()), na.NOT_FOUND_WORD)

    async def test_refusing_writes_no_row(self) -> None:
        await self.pc("Cara", 803)
        listed = await self.waiting()
        before = await self.knowers()
        self.assertEqual(await self.join(listed), na.NOT_FOUND_WORD)
        self.assertEqual(await self.knowers(), before)
