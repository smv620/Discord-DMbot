"""The website's notice for one upload or import (#3879): counts, the label and until when Undo
works, read through its own database door as the website's role. Only a DM of the campaign can read
it; a notice is found by its source, never "the latest"; counts and times only. Real Postgres;
invented names."""

from __future__ import annotations

from typing import Any

from dmbot.upload_notices import UNDO_SECONDS, Notice, UploadNotices
from dmbot.web import upload_notice
from dmbot.web.recaps import RecapError
from dmbot.web.sessions import Session
from tests.test_recap_web import BOB
from tests.test_web_api import THURSDAY
from tests.test_web_name_actions import Acting
from tests.test_web_theatre_script import Script

COUNTS = {"npc": 30, "place": 24, "connections": 48, "other_names": 3}


class UploadNoticeRead(Script):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.notices = UploadNotices(self.db)

    async def add(
        self, source_id: str = "up1", kind: str = "lore", changes: list[int] | None = None
    ) -> Notice:
        return await self.notices.add(
            THURSDAY.id,
            self.one.id,
            "The Sunken Keep",
            COUNTS,
            [1, 2, 3] if changes is None else changes,
            source_kind=kind,
            source_id=source_id,
        )

    async def read(
        self, kind: str = "lore", source_id: str = "up1", who: Session | None = None
    ) -> dict[str, Any]:
        return await upload_notice.read(self.web, who or self.session, self.cref, kind, source_id)

    async def refusal(self, kind: str = "lore", source_id: str = "up1") -> str:
        with self.assertRaises(RecapError) as caught:
            await self.read(kind, source_id)
        return caught.exception.code

    async def test_a_notice_is_found_by_its_source_with_counts_and_the_label(self) -> None:
        made = await self.add()
        got = await self.read()
        self.assertEqual(got["state"], "done")
        self.assertEqual(got["label"], "The Sunken Keep")
        self.assertEqual(
            got["counts"],
            {"names": 54, "byKind": {"npc": 30, "place": 24}, "connections": 48, "revealed": 0},
        )
        self.assertIsNone(got["undone"])
        # seven days from the upload, as the bot's own rule
        self.assertEqual(got["undoUntil"], made.created_at + UNDO_SECONDS)
        self.assertNotIn("changes", got)

    async def test_connections_shown_or_kept_hidden_are_never_counted_as_names(self) -> None:
        """#3963 (c) and #3979: an import's notice keeps these beside the kinds, never as one."""
        await self.notices.add(
            THURSDAY.id, self.one.id, "Session 3",
            {"npc": 1, "shown": 1, "held": 2, "split_group": 1, "split_unknown": 1}, [1],
            source_kind="import", source_id="up1",
        )  # fmt: skip
        got = await self.read("import")
        self.assertEqual((got["counts"]["names"], got["counts"]["byKind"]), (1, {"npc": 1}))

    async def test_the_hidden_things_a_read_made_known_are_a_count_beside_the_names(self) -> None:
        """#4589: a 🌍 Common knowledge read's reveals, as Discord's notice says them."""
        await self.notices.add(
            THURSDAY.id, self.one.id, "The Sunken Keep", {"npc": 2, "revealed": 3}, [1],
            source_kind="lore", source_id="up9",
        )  # fmt: skip
        got = await self.read("lore", "up9")
        self.assertEqual(got["counts"]["revealed"], 3)
        self.assertEqual((got["counts"]["names"], got["counts"]["byKind"]), (2, {"npc": 2}))

    async def test_another_upload_or_kind_is_not_this_notice(self) -> None:
        await self.add("up1", "lore")
        none = await self.read("lore", "up2")
        self.assertEqual(none["state"], "none")
        self.assertEqual(
            none["counts"], {"names": 0, "byKind": {}, "connections": 0, "revealed": 0}
        )
        self.assertIsNone(none["undone"])
        self.assertIsNone(none["undoUntil"])
        self.assertEqual((await self.read("import", "up1"))["state"], "none")

    async def test_a_notice_without_a_source_is_never_guessed_at(self) -> None:
        await self.notices.add(THURSDAY.id, self.one.id, "From Discord", COUNTS, [1])
        self.assertEqual((await self.read())["state"], "none")

    async def test_an_undone_upload_says_so_and_offers_no_undo(self) -> None:
        made = await self.add()
        assert await self.notices.claim_undo(THURSDAY.id, made.id) is not None
        got = await self.read()
        self.assertEqual(got["state"], "done")
        self.assertIsNone(got["undone"])  # (how many it took back is not kept)
        self.assertIsNone(got["undoUntil"])

    async def test_an_upload_that_changed_nothing_offers_no_undo(self) -> None:
        await self.add(changes=[])
        self.assertIsNone((await self.read())["undoUntil"])

    async def test_a_player_and_another_campaigns_dm_are_told_nothing(self) -> None:
        await self.add()
        bob = await self.signed_in(BOB)  # in the server, not a DM of the campaign
        self.assertEqual(await self._refused_as(bob), "not_found")
        with self.assertRaises(RecapError):  # a campaign this person is no DM of
            await upload_notice.read(
                self.web, self.session, f"{THURSDAY.id}.{self.two.id}", "lore", "up1"
            )

    async def _refused_as(self, who: Session) -> str:
        with self.assertRaises(RecapError) as caught:
            await self.read(who=who)
        return caught.exception.code

    async def test_a_bad_kind_or_id_is_not_found(self) -> None:
        self.assertEqual(await self.refusal(kind="other"), "not_found")
        self.assertEqual(await self.refusal(source_id="x y"), "not_found")
        self.assertEqual(await self.refusal(source_id=""), "not_found")

    async def test_the_window_closes_after_seven_days(self) -> None:
        made = await self.add()
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute(
                "UPDATE upload_notices SET created_at = %s WHERE id = %s",
                (made.created_at - UNDO_SECONDS - 5, made.id),
            )
        self.assertIsNone((await self.read())["undoUntil"])

    async def test_the_same_source_id_in_another_campaign_is_not_found(self) -> None:
        await self.notices.add(
            THURSDAY.id, self.two.id, "Other", COUNTS, [1], source_kind="lore", source_id="up1"
        )
        self.assertEqual((await self.read())["state"], "none")


class RevealedList(Acting):
    """#4661: the DMs see which hidden things a 🌍 upload made known to everyone, in words, read
    from the ids the notice kept; a thing hidden again since is never listed."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.notices = UploadNotices(self.db)

    async def read(
        self, kind: str = "lore", source_id: str = "up1", who: Session | None = None
    ) -> dict[str, Any]:
        return await upload_notice.read(self.web, who or self.session, self.cref, kind, source_id)

    async def plain_notice(self) -> Notice:
        return await self.notices.add(
            THURSDAY.id, self.one.id, "The Sunken Keep", COUNTS, [1, 2, 3],
            source_kind="lore", source_id="up1",
        )  # fmt: skip

    async def entry(self, name: str, kind: str = "character", **kw: Any) -> str:
        made = await self.memory.add_entity(
            THURSDAY.id, self.one.id, name=name, type=kind, source="dm", status="confirmed", **kw
        )
        return str(made.value.id)

    async def notice_with(self, **ids: list[str]) -> None:
        total = sum(len(v) for v in ids.values())
        await self.notices.add(
            THURSDAY.id, self.one.id, "The Sunken Keep", {"npc": 2, "revealed": total}, [1],
            source_kind="lore", source_id="up9", revealed_ids=ids,
        )  # fmt: skip

    async def test_a_dm_sees_what_was_revealed_in_words(self) -> None:
        ash = await self.entry("Ash Morrow")
        orlen = await self.entry("Master Orlen")
        link = await self.memory.add_relation(
            THURSDAY.id,
            self.one.id,
            ash,
            "member_of",
            orlen,
            source="dm",
            confidence=1.0,
            status="confirmed",
        )
        await self.notice_with(entries=[ash], links=[link.value[0].id])
        got = await self.read("lore", "up9")
        self.assertEqual(got["revealedLines"][0], "Ash Morrow")
        self.assertEqual(got["revealedLines"][1], "Ash Morrow is a member of Master Orlen")
        self.assertEqual(got["revealedMore"], 0)

    async def test_a_thing_hidden_again_since_is_not_listed(self) -> None:
        ash = await self.entry("Ash Morrow")
        bram = await self.entry("Bram Tull")
        await self.notice_with(entries=[ash, bram])
        await self.memory.set_hidden_from_players(
            THURSDAY.id, self.one.id, bram, True, source="dm", dm_chose_secret=True
        )
        got = await self.read("lore", "up9")
        self.assertEqual(got["revealedLines"], ["Ash Morrow"])

    async def test_an_entry_whose_own_name_turned_secret_is_not_listed_nor_its_ties(self) -> None:
        """The gate shows an entry by its main name only while that name is not a secret one."""
        ash = await self.entry("Ash Morrow")
        orlen = await self.entry("Master Orlen")
        link = await self.memory.add_relation(
            THURSDAY.id, self.one.id, ash, "member_of", orlen, source="dm", confidence=1.0,
            status="confirmed",
        )  # fmt: skip
        await self.notice_with(entries=[ash, orlen], links=[link.value[0].id])
        async with self.db.guild(THURSDAY.id) as conn:  # (the DM made Ash's own name secret)
            await conn.execute(
                "UPDATE memory_aliases SET secret = true WHERE entity_id = %s AND text = %s",
                (ash, "Ash Morrow"),
            )
        got = await self.read("lore", "up9")
        self.assertEqual(got["revealedLines"], ["Master Orlen"])

    async def test_a_connection_that_came_through_a_secret_other_name_is_not_listed(self) -> None:
        ash = await self.entry("Ash Morrow")
        orlen = await self.entry("Master Orlen")
        link = await self.memory.add_relation(
            THURSDAY.id, self.one.id, ash, "member_of", orlen, source="dm", confidence=1.0,
            status="confirmed",
        )  # fmt: skip
        await self.notice_with(links=[link.value[0].id])
        self.assertEqual(len((await self.read("lore", "up9"))["revealedLines"]), 1)
        alias = await self.memory.add_alias(
            THURSDAY.id, self.one.id, ash, "The Grey Hand", kind="nickname", source="dm",
            secret=True,
        )  # fmt: skip
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute(
                "UPDATE memory_relations SET via_alias_id = %s WHERE id = %s",
                (alias.value.id, link.value[0].id),
            )
        self.assertNotIn("revealedLines", await self.read("lore", "up9"))

    async def test_only_ten_are_listed_and_the_rest_are_counted(self) -> None:
        names = [await self.entry(f"Person {n:02d}") for n in range(12)]
        await self.notice_with(entries=names)
        got = await self.read("lore", "up9")
        self.assertEqual((len(got["revealedLines"]), got["revealedMore"]), (10, 2))

    async def test_no_list_when_nothing_was_revealed_or_the_upload_has_none_stored(self) -> None:
        await self.plain_notice()
        got = await self.read()
        self.assertNotIn("revealedLines", got)
        await self.notice_with()  # (a count of 0: nothing to list)
        self.assertNotIn("revealedLines", await self.read("lore", "up9"))

    async def test_a_stranger_or_a_non_dm_gets_none(self) -> None:
        ash = await self.entry("Ash Morrow")
        await self.notice_with(entries=[ash])
        bob = await self.signed(BOB, THURSDAY)  # DMs Two, not One
        with self.assertRaises(RecapError) as caught:
            await self.read("lore", "up9", bob)
        self.assertEqual(caught.exception.code, "not_found")

    async def test_the_ids_kept_are_capped(self) -> None:
        await self.notice_with(entries=[f"{n:032x}" for n in range(80)])
        async with self.db.guild(THURSDAY.id) as conn:
            cur = await conn.execute(
                "SELECT jsonb_array_length(revealed_ids -> 'entries') AS n FROM upload_notices"
                " WHERE source_id = 'up9'"
            )
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual(row["n"], 50)
