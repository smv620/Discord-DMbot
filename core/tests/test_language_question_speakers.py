"""A language question heard in a session carries who said it (#4745; Supervisor's ruling on
#4744): it is written only while the session and its speakers are still there, it goes with their
"delete my data" and with the session's transcript, and a moved campaign keeps only who is recorded
in the new server; a lore upload's question and the DM's own "never" stay (the "never" without the
ids). Invented names. Needs Postgres."""

from __future__ import annotations

from typing import Any

from dmbot.memory import trait_offers as to
from dmbot.memory.claims import ClaimStore
from dmbot.memory.models import CONFIRMED, LANGUAGE, NewName, Trait
from dmbot.memory.traits_read import Spoken
from dmbot.transcript.models import Line
from dmbot.transcript.store import TranscriptStore
from dmbot.ui import name_background as nb
from tests.agreed import agree
from tests.test_memory_names import GUILD, NamesTest

GIANT = Trait(LANGUAGE, "Giant", "", None, False)
ELVISH = Trait(LANGUAGE, "Elvish", "", None, False)
SPEAKER, OTHER, SILENT = 4001, 4002, 4003


class HeardQuestions(NamesTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        await agree(self.db, GUILD, SPEAKER, OTHER, SILENT)
        self.store = to.TraitOfferStore(self.db, lambda: 1_000)
        self.transcripts = TranscriptStore(self.db)
        self.sid = await self.transcripts.open_session(GUILD, self.campaign.id, 1_700_000_000)
        await self.transcripts.add_lines(
            GUILD, self.sid,
            [Line(1_000, SPEAKER, "Brewner answers in Giant.", "x"),
             Line(2_000, OTHER, "I listen.", "x")],
        )  # fmt: skip
        made = await self.memory.add_names(
            GUILD, self.campaign.id, [NewName("Brewner", "npc", CONFIRMED)], source="dm"
        )
        (brewner,) = made.value
        assert brewner is not None
        self.brewner: str = brewner

    def heard(self, trait: Trait = GIANT, *who: int) -> to.Offer:
        return to.Offer(self.brewner, trait, "", "s", speaker_ids=who or (SPEAKER,),
                        session_id=self.sid)  # fmt: skip

    async def rows(self) -> list[Any]:
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                "SELECT name, state, speaker_ids, session_id FROM memory_trait_offers"
                " WHERE guild_id = %s ORDER BY name",
                (GUILD,),
            )
            return [
                (r["name"], r["state"], list(r["speaker_ids"]), r["session_id"])
                for r in await cur.fetchall()
            ]

    async def test_a_heard_question_carries_its_speakers_and_session(self) -> None:
        lore = to.Offer(self.brewner, ELVISH, "Brewner speaks Elvish", "Notes")
        await self.store.offer(GUILD, self.campaign.id, [self.heard(GIANT, OTHER, SPEAKER), lore])
        self.assertEqual(
            await self.rows(),
            [("Elvish", "waiting", [], None), ("Giant", "waiting", [SPEAKER, OTHER], self.sid)],
        )

    async def test_the_session_path_through_ask_languages_carries_them(self) -> None:
        asked, _ = await nb.ask_languages(
            self.memory, self.campaign, [Spoken("Brewner", "Giant", speakers=(SPEAKER,))],
            "the session of 1 Jan", None, self.sid,
        )  # fmt: skip
        self.assertEqual(asked, 1)
        self.assertEqual(await self.rows(), [("Giant", "waiting", [SPEAKER], self.sid)])

    async def test_nothing_is_written_for_someone_gone_since_the_read(self) -> None:
        # (the reader's consent check was an AI call ago: a stop, a deletion or someone with no
        # lines in the session is caught at the write, under the session's lock)
        async with self.db.guild(GUILD) as conn:
            await conn.execute(
                "DELETE FROM consent WHERE guild_id = %s AND user_id = %s", (GUILD, OTHER)
            )
        await self.store.offer(GUILD, self.campaign.id, [self.heard(GIANT, OTHER)])
        await self.store.offer(GUILD, self.campaign.id, [self.heard(ELVISH, SILENT)])
        gone = to.Offer(self.brewner, ELVISH, "", "s", speaker_ids=(SPEAKER,), session_id="nope")
        await self.store.offer(GUILD, self.campaign.id, [gone])
        self.assertEqual(await self.rows(), [])

    async def test_the_speakers_forget_takes_the_waiting_question_and_nothing_else(self) -> None:
        lore = to.Offer(self.brewner, ELVISH, "", "Notes")
        await self.store.offer(GUILD, self.campaign.id, [self.heard(), lore])
        async with self.db.guild(GUILD) as conn:
            self.assertEqual(await to.forget_speaker(conn, GUILD, OTHER), 0)
            self.assertEqual(await to.forget_speaker(conn, GUILD, SPEAKER, "x" * 32), 0)
            self.assertEqual(await to.forget_speaker(conn, GUILD, SPEAKER, self.campaign.id), 1)
        self.assertEqual(await self.rows(), [("Elvish", "waiting", [], None)])

    async def test_the_dms_never_stays_without_the_ids(self) -> None:
        heard = self.heard(GIANT, SPEAKER, OTHER)
        await self.store.offer(GUILD, self.campaign.id, [heard])
        await self.store.never(GUILD, self.campaign.id, heard)
        async with self.db.guild(GUILD) as conn:
            self.assertEqual(await to.forget_speaker(conn, GUILD, SPEAKER), 0)
            self.assertEqual(await to.forget_session(conn, GUILD, self.sid), 0)
        self.assertEqual(await self.rows(), [("Giant", "never", [OTHER], None)])

    async def test_the_sessions_transcript_takes_its_waiting_questions(self) -> None:
        other = await self.transcripts.open_session(GUILD, self.campaign.id, 1_700_100_000)
        await self.transcripts.add_lines(GUILD, other, [Line(1_000, SPEAKER, "Elvish.", "x")])
        two = to.Offer(self.brewner, ELVISH, "", "s", speaker_ids=(SPEAKER,), session_id=other)
        await self.store.offer(GUILD, self.campaign.id, [self.heard(), two])
        async with self.db.guild(GUILD) as conn:
            self.assertEqual(await to.forget_session(conn, GUILD, self.sid), 1)
        self.assertEqual(await self.rows(), [("Elvish", "waiting", [SPEAKER], other)])

    async def test_delete_my_transcripts_reaches_it(self) -> None:
        await self.store.offer(GUILD, self.campaign.id, [self.heard()])
        await self.transcripts.delete_person(GUILD, SPEAKER)
        self.assertEqual(await self.rows(), [])

    async def test_stop_recording_me_reaches_it(self) -> None:
        await self.store.offer(GUILD, self.campaign.id, [self.heard()])
        await ClaimStore(self.memory).forget_speaker(GUILD, SPEAKER)
        self.assertEqual(await self.rows(), [])

    async def test_a_moved_campaign_keeps_only_who_is_recorded_there(self) -> None:
        both = self.heard(GIANT, SPEAKER, OTHER)
        await self.store.offer(GUILD, self.campaign.id, [both, self.heard(ELVISH, SPEAKER)])
        await self.store.never(GUILD, self.campaign.id, both)
        async with self.db.guild(GUILD) as conn:
            await to.keep_recorded(conn, GUILD, self.campaign.id, [OTHER])
        self.assertEqual(await self.rows(), [("Giant", "never", [OTHER], self.sid)])
